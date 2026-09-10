#!/usr/bin/env python
"""gatectl - the deterministic core of the foss-mcp supervised execution loop.

Every verdict in this system is one of this program's exit codes. No verdict is
ever a sentence, and no model ever hand-writes project/state.yaml, a receipt, or
a timestamp.

Design notes that are load-bearing (see the plan's SS2.4):
  * The worker produces a commit. gatectl computes the verdict. One path.
  * A card's checks are run TWICE clean (disagreement == flake == fail), then
    once more with the supervisor-authored negative-control patch applied, where
    they MUST fail. A suite that passes both ways is vacuous and is rejected.
  * Checks run in a throwaway `git worktree` at the target revision with a
    hash-pinned venv and a pinned environment, so only committed content can
    participate in a verdict.
  * project/state.yaml is DERIVED. `validate` asserts committed == rebuild().
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OPS = REPO / "ops"
PLANS = REPO / "plans"
SCHEMAS = REPO / "schemas"
EVIDENCE = REPO / "evidence"
STATE_FILE = REPO / "project" / "state.yaml"
STATUS_JSONL = OPS / "status.jsonl"
INSTRUCTIONS_JSONL = OPS / "instructions.jsonl"
QUESTIONS_JSONL = OPS / "open_questions.jsonl"
HEARTBEAT = OPS / "heartbeat.json"
LOCKFILE = REPO / "requirements.lock"
WORKTREE_ROOT = REPO / ".gatectl-worktrees"

CARD_RE = re.compile(r"^TC-\d{3}[a-z]?$")
GATES = ["G0", "G1", "G2", "G3", "G4", "G5"]

# Paths no taskcard may EVER declare in write_paths. Enforced at authoring time
# by `validate`, so a bad card fails before a worker is ever spawned, and again
# at scope time. This is the mechanical form of "two governance tracks, no
# overlap" - prevention, not post-hoc revert.
GLOBAL_DENY = [
    "plans/**",
    "project/**",
    "ops/**",
    "schemas/*",  # control-plane schemas only; schemas/product/** is card-writable
    "evidence/**",
    ".githooks/**",
    ".claude/**",
    ".gitattributes",
    ".gitignore",
    "requirements.lock",
    "requirements.in",
]

# Pinned verification environment. PYTHONUTF8 is not cosmetic on Windows:
# cp1252-by-default is precisely the family that produced the reference
# system's ASCII-apostrophe-vs-curly-quote debugging failure.
CANONICAL_ENV = {
    "PYTHONHASHSEED": "0",
    "PYTHONUTF8": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONIOENCODING": "utf-8",
    "TZ": "UTC",
    "LC_ALL": "C.UTF-8",
    "SOURCE_DATE_EPOCH": "1700000000",
}

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def now_utc() -> str:
    """The ONLY timestamp source in this system. No model types a timestamp."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes()) if p.exists() else "0" * 64


def run(cmd, cwd=None, env=None, timeout=None, text=True):
    """Run a command, capturing both streams. Never raises on non-zero."""
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd or REPO),
            env=e,
            timeout=timeout,
            capture_output=True,
            text=text,
            shell=isinstance(cmd, str),
        )
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT after {timeout}s"
    except Exception as exc:  # noqa: BLE001 - a launch failure is a real verdict
        return 125, "", f"LAUNCH-FAILED: {exc}"


def git(*args, cwd=None):
    rc, out, err = run(["git", *args], cwd=cwd)
    return rc, out.strip(), err.strip()


def die(msg: str, code: int = EXIT_FAIL):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------
# yaml / json loading
# --------------------------------------------------------------------------
def _yaml():
    import yaml

    class TextTimestampLoader(yaml.SafeLoader):
        """Keep dates/timestamps as text so the schemas constrain their form.

        Without this, PyYAML auto-types an ISO timestamp into a datetime and the
        schema's string pattern can never match it.
        """

    TextTimestampLoader.yaml_implicit_resolvers = {
        k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
        for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    return yaml, TextTimestampLoader


def load_yaml_text(text: str):
    yaml, loader = _yaml()
    return yaml.load(text, Loader=loader)


def load_yaml(p: Path):
    return load_yaml_text(p.read_text(encoding="utf-8"))


def dump_yaml(obj) -> str:
    import yaml

    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, allow_unicode=True)


def load_schema(name: str):
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def schema_errors(schema, instance):
    import jsonschema

    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(x) for x in e.path)}: {e.message}" for e in v.iter_errors(instance)]


# --------------------------------------------------------------------------
# path matching  (Windows-aware: core.ignorecase=true is live in this repo)
# --------------------------------------------------------------------------
def norm_path(p: str) -> str:
    return p.replace("\\", "/").strip().strip("/")


def path_matches(path: str, pattern: str) -> bool:
    """Segment-wise match. `a/b/**` matches inside a/b; `a/b.py` is exact.

    Deliberately NOT prefix matching: `src/reg` must not match
    `src/regression_hack.py`. Casefolded because core.ignorecase=true here.
    """
    p = norm_path(path).casefold()
    q = norm_path(pattern).casefold()
    if q.endswith("/**"):
        base = q[:-3]
        return p == base or p.startswith(base + "/")
    if q.endswith("/*"):
        base = q[:-2]
        return p.startswith(base + "/") and "/" not in p[len(base) + 1 :]
    return p == q


def matches_any(path: str, patterns) -> bool:
    return any(path_matches(path, pat) for pat in patterns)


ILLEGAL_NAME_RE = re.compile(r"(^|/)(con|aux|nul|prn|com[1-9]|lpt[1-9])(\.|/|$)", re.IGNORECASE)


def illegal_path_reasons(p: str):
    """Windows/git hazards that make a declared path unsafe to trust."""
    out = []
    n = norm_path(p)
    if ":" in n:
        out.append("contains ':' (NTFS alternate data streams are invisible to git diff)")
    if ILLEGAL_NAME_RE.search(n):
        out.append("uses a Windows reserved device name")
    for seg in n.split("/"):
        if seg != seg.strip() or seg.endswith("."):
            out.append(f"segment {seg!r} has a trailing space or dot")
    return out


# --------------------------------------------------------------------------
# taskcards
# --------------------------------------------------------------------------
def card_path(card_id: str) -> Path:
    return PLANS / f"{card_id}.yaml"


def load_card_from_worktree(card_id: str):
    p = card_path(card_id)
    if not p.exists():
        die(f"no such card: {p}")
    return load_yaml(p)


def load_card_at_rev(card_id: str, rev: str):
    """Read the card as it existed at the revision it was ISSUED at.

    A worker that edits its own card must not be able to redefine its own
    permissions, so scope/verify never read the working-tree copy.
    """
    rc, out, err = git("show", f"{rev}:plans/{card_id}.yaml")
    if rc != 0:
        die(f"cannot read {card_id} at {rev}: {err}")
    return load_yaml_text(out), sha256_bytes(out.encode("utf-8"))


def all_cards():
    cards = {}
    for p in sorted(PLANS.glob("TC-*.yaml")):
        c = load_yaml(p)
        if isinstance(c, dict) and "id" in c:
            cards[c["id"]] = c
    return cards


# --------------------------------------------------------------------------
# scope
# --------------------------------------------------------------------------


def resolve_rev(rev: str) -> str:
    """Expand any revision to a full 40-hex SHA.

    A supervisor typing an abbreviated SHA on the command line put a 12-char
    value straight into a receipt, which the schema requires to be 40 hex - so
    the receipt failed validation after the work was already verified. Normalise
    at the boundary rather than trusting whatever was typed.
    """
    rc, out, err = git("rev-parse", "--verify", f"{rev}^{{commit}}")
    if rc != 0 or len(out) != 40:
        die(f"cannot resolve revision {rev!r}: {err or out}")
    return out


def commit_subject(rev: str) -> str:
    return git("log", "-1", "--format=%s", rev)[1]


def commit_body(rev: str) -> str:
    return git("log", "-1", "--format=%b", rev)[1]


def changed_paths(base: str, head: str, card_id: str | None = None, gate: str | None = None):
    """Union of paths touched by every commit in base..head that belongs to this card.

    An endpoint diff (`git diff base..head`) hides touch-and-revert: a worker can
    modify ops/gatectl.py in commit 1 and revert it in commit 3, and the endpoint
    diff is clean while the history contains the change. So we walk each commit.
    --no-renames because rename detection prints only the destination, hiding the
    source path entirely.

    Why the card filter: the supervisor commits governance while a card is in
    flight, so base..head legitimately contains commits the worker never made.
    Blaming those on the worker produced a spurious scope violation three times.
    The commit convention already identifies ownership - `(<GATE>/<CARD>)` in the
    subject - so scope is judged on the worker's own commits. Commits that claim
    neither this card nor the supervisor trailer are reported separately rather
    than silently ignored, so an untagged worker commit cannot hide here.
    """
    rc, out, err = git("rev-list", "--reverse", f"{base}..{head}")
    if rc != 0:
        die(f"rev-list failed: {err}")
    revs = [r for r in out.splitlines() if r.strip()]

    unattributed = []
    if card_id:
        tag = f"({gate}/{card_id})" if gate else f"/{card_id})"
        mine, others = [], []
        for r in revs:
            if tag in commit_subject(r):
                mine.append(r)
            elif "Claude Opus 5" in commit_body(r):
                others.append(r)  # supervisor governance, legitimately out of scope
            else:
                unattributed.append(r)
        revs = mine + unattributed
    touched, per_commit = set(), {}
    for r in revs:
        rc, out, _ = git("diff-tree", "--no-commit-id", "--no-renames", "-r", "--name-status", r)
        if rc != 0:
            continue
        paths = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                # every listed path counts, whatever the status letter
                for pth in parts[1:]:
                    if pth.strip():
                        paths.append(norm_path(pth))
        per_commit[r] = paths
        touched.update(paths)
    return revs, sorted(touched), per_commit, unattributed


def risky_modes(base: str, head: str):
    """Symlinks (120000) and gitlinks/submodules (160000) put content outside
    the path check entirely. core.symlinks=false here, so a symlink looks like
    an ordinary text file on disk while being mode 120000 in the index."""
    out_flags = []
    rc, out, _ = git("diff", "--no-renames", "--raw", f"{base}..{head}")
    for line in out.splitlines():
        if not line.startswith(":"):
            continue
        fields = line[1:].split()
        if len(fields) >= 2:
            newmode = fields[1]
            if newmode in ("120000", "160000"):
                kind = "symlink" if newmode == "120000" else "submodule/gitlink"
                out_flags.append(f"{kind} introduced: {line.strip()}")
    return out_flags


def check_scope(card, base: str, head: str):
    """Returns (ok, changed, violations)."""
    write_paths = card.get("write_paths", [])
    revs, changed, _, unattributed = changed_paths(base, head, card.get("id"), card.get("gate"))
    violations = []

    if not revs:
        violations.append(
            f"no commit in {base[:12]}..{head[:12]} is tagged for this card - the commit "
            f"subject must end with ({card.get('gate')}/{card.get('id')})"
        )
    for r in unattributed:
        violations.append(
            f"commit {r[:12]} claims neither this card nor the supervisor: {commit_subject(r)[:70]!r}"
        )

    for p in changed:
        if matches_any(p, GLOBAL_DENY):
            violations.append(f"{p}: touches a globally denied path")
        elif not matches_any(p, write_paths):
            violations.append(f"{p}: outside the card's declared write_paths")

    violations.extend(risky_modes(base, head))

    # history must be a fast-forward from the pre-spawn base: no amend/reset
    rc, _, _ = git("merge-base", "--is-ancestor", base, head)
    if rc != 0:
        violations.append(f"{head[:12]} does not descend from base {base[:12]} (history was rewritten)")

    return (len(violations) == 0), changed, violations


# --------------------------------------------------------------------------
# environment fingerprint
# --------------------------------------------------------------------------
def venv_python(root: Path = REPO) -> Path:
    win = root / ".venv" / "Scripts" / "python.exe"
    return win if win.exists() else root / ".venv" / "bin" / "python"


def fingerprint():
    py = venv_python()
    rc, out, _ = run(
        [str(py), "-c", "import sys,platform;print(sys.version.split()[0]);print(platform.platform())"]
    )
    lines = out.strip().splitlines()
    return {
        "python": lines[0] if lines else "unknown",
        "platform": lines[1] if len(lines) > 1 else platform.platform(),
        "lock_sha256": sha256_file(LOCKFILE),
        "env": dict(CANONICAL_ENV),
    }


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
def ensure_shared_venv() -> Path:
    """A venv that is a pure function of requirements.lock.

    Cached under .gatectl-worktrees/venv-<lockhash> so repeated verifies do not
    pay a fresh install each time. Caching is sound precisely because the key is
    the lock digest: a different lock can never reuse a venv.
    """
    lock_sha = sha256_file(LOCKFILE)
    target = WORKTREE_ROOT / f"venv-{lock_sha[:16]}"
    py = (target / "Scripts" / "python.exe") if os.name == "nt" else (target / "bin" / "python")
    if py.exists():
        return py
    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    rc, _, err = run([sys.executable, "-m", "venv", str(target)], timeout=300)
    if rc != 0:
        die(f"could not create pinned venv: {err}")
    rc, _, err = run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], timeout=300)
    rc, _, err = run(
        [str(py), "-m", "pip", "install", "--quiet", "--require-hashes", "-r", str(LOCKFILE)], timeout=900
    )
    if rc != 0:
        die(f"pinned install failed (--require-hashes): {err[:600]}")
    return py


def make_worktree(rev: str) -> Path:
    """Throwaway checkout so ONLY committed content can reach a verdict.

    Verifying in the live tree lets an untracked conftest.py, a stale
    __pycache__, or a globally pip-installed package silently make checks pass
    in a way that exists nowhere else. That is the most likely false-green.
    """
    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    wt = Path(tempfile.mkdtemp(prefix="wt-", dir=str(WORKTREE_ROOT)))
    shutil.rmtree(wt, ignore_errors=True)
    rc, _, err = git("worktree", "add", "--detach", "--force", str(wt), rev)
    if rc != 0:
        die(f"worktree add failed for {rev}: {err}")
    return wt


def drop_worktree(wt: Path):
    git("worktree", "remove", "--force", str(wt))
    shutil.rmtree(wt, ignore_errors=True)


def parse_junit(xml_path: Path):
    if not xml_path.exists():
        return {}
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return {}
    nodes = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    agg = {"tests": 0, "skipped": 0, "errors": 0, "failures": 0}
    for n in nodes:
        for k in agg:
            agg[k] += int(n.attrib.get(k, 0) or 0)
    return agg


def main(argv=None) -> int:
    """Entry point. The command surface lives in gatecli to keep this file
    focused on primitives (paths, scope, fingerprint, worktrees)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gatecli

    return gatecli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
