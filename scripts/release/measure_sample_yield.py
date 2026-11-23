#!/usr/bin/env python3
"""Measure how many of an upstream project's own example programs verify (G3/TC-866 / REQ-G3-014, DG-3).

Usage:
    python scripts/release/measure_sample_yield.py measure --pilot cells/python --checkout DIR
        [--samples-dir examples] [--image IMAGE] [--out-json FILE] [--out-md FILE]
        [--exclude NAME=REASON ...] [--repository OWNER/REPO --commit SHA] [--keep-volume]
    python scripts/release/measure_sample_yield.py pin --pilot cells/python

The plan assumed that about 70% of an upstream project's shipped example programs can become verified, published
examples. This tool measures it instead. Given a pilot, a checkout of its pinned upstream commit, a sample
directory and an ingestion image it:

  1. SETUP (network on): in the image, copies the checkout into a docker volume and builds the venv the product's
     own ``prepare_python_library`` builds (``pip install -e`` of the pinned library), the clone step replaced by a
     copy of the checkout so the measured tree is the one the caller pinned;
  2. runs every sample, one fresh container each, with ``--network none``, the volume read-only, through the
     product's OWN ``verify_python_example`` (staging of the repository files the sample names, ``provide_fixtures``,
     the 120 s bounded run, the import-only rejection) - imported, never reimplemented - and the product's OWN
     artifact validators (``example_artifacts``) on any file the run wrote;
  3. classifies each sample exactly once (``CLASSES``) and writes a JSON list and a markdown table, sorted by name.

A sample listed with ``--exclude NAME=REASON`` (its licence or a bundled asset forbids republication) is not run:
it is recorded as ``excluded`` with the reason and reported beside the denominator, never silently dropped.

``yield_decision(verified, total)`` is the DG-3 rule: ``proceed`` when verified/total >= 0.70, else ``revise``.

No credential is ever forwarded to a container, and none is printed. Only the python platform is measured: the
other platforms' run steps (go, rust, typescript, java, dotnet) need their own toolchain set-up and are out of
scope for this slice.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "CLASSES",
    "DECISION_THRESHOLD",
    "SampleResult",
    "classify_outcome",
    "measure_samples",
    "pilot_pin",
    "render_markdown",
    "summarise",
    "yield_decision",
]

_PARENTS = Path(__file__).resolve().parents
REPO_ROOT = _PARENTS[2] if len(_PARENTS) > 2 else _PARENTS[0]  # inside the container the file sits at /tool
VALUES_FILE = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

DECISION_THRESHOLD = 0.70

C_PRODUCED = "verified-ran-produced-output"
C_EXIT0 = "verified-ran-exit0"
C_COMPILE = "rejected-compile"
C_FIXTURE = "rejected-missing-fixture"
C_RUNTIME = "rejected-runtime"
C_NETWORK = "rejected-needs-network-or-credentials"
C_NO_STATEMENTS = "rejected-no-statements"
C_EXCLUDED = "excluded"
CLASSES: tuple[str, ...] = (
    C_PRODUCED,
    C_EXIT0,
    C_COMPILE,
    C_FIXTURE,
    C_RUNTIME,
    C_NETWORK,
    C_NO_STATEMENTS,
    C_EXCLUDED,
)
VERIFIED_CLASSES = frozenset({C_PRODUCED, C_EXIT0})

DEFAULT_IMAGE = "foss-mcp-ingestion:regen-p4b"
DEFAULT_SAMPLE_SUFFIXES = (".py",)
RESULT_MARKER = "@@MEASURE_RESULT@@"
_SAMPLE_CONTAINER_TIMEOUT_SECONDS = 300
_SETUP_TIMEOUT_SECONDS = 1800
_CONTAINER_WORK = "/work"
_CONTAINER_SEED = "/seed"
_CONTAINER_TOOL = "/tool/measure_sample_yield.py"
_CONTAINER_CHECKOUT = "/checkout"
_TEXT_CAP = 600

# Markers in a failed run's text that say the sample needs the network or an account, not that it is broken.
_NETWORK_RE = re.compile(
    r"ConnectionError|ConnectionRefusedError|URLError|Temporary failure in name resolution|"
    r"Name or service not known|getaddrinfo failed|Network is unreachable|Max retries exceeded|"
    r"NewConnectionError|socket\.gaierror|api[_ -]?key|credential|Unauthori[sz]ed|"
    r"KeyError: '[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*'",
    re.IGNORECASE,
)
_SYNTAX_RE = re.compile(r"^\s*(?:IndentationError|TabError|SyntaxError)\b", re.MULTILINE)
_FILE_NOT_FOUND_RE = re.compile(r"FileNotFoundError:.*?['\"]([^'\"]+)['\"]")
_UNITTEST_RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_UNITTEST_RESULT_RE = re.compile(r"^(OK|FAILED)(?: \(([^)]*)\))?\s*$", re.MULTILINE)
# A test that looks for an input file the upstream repository does not ship.
_MISSING_INPUT_RES = (
    re.compile(r"[Ii]nput file (\S+) does not exist"),
    re.compile(r"No (\w+) files found in (\w+) directory"),
)
_NO_STATEMENTS_RE = re.compile(r"rejected: the block has no statements")


def yield_decision(verified: int, total: int) -> str:
    """DG-3: ``proceed`` when ``verified / total >= 0.70``, else ``revise`` (an empty denominator is ``revise``)."""
    if total <= 0:
        return "revise"
    return "proceed" if verified * 100 >= round(DECISION_THRESHOLD * 100) * total else "revise"


@dataclass(frozen=True)
class SampleResult:
    """One sample, classified exactly once."""

    name: str
    classification: str
    reason: str
    seconds: float = 0.0

    @property
    def verified(self) -> bool:
        return self.classification in VERIFIED_CLASSES


def _one_line(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[:limit]


_EXCEPTION_LINE_RE = re.compile(
    r"^(?:[A-Za-z_][\w.]*\.)?[A-Z]\w*(?:Error|Exception|Exit|Failure)\b.*$", re.MULTILINE
)


def unittest_summary(text: str) -> str:
    """``unittest: ran 12, OK (skipped=3)`` from a run's output, ``""`` when it printed no unittest summary.

    Upstream's examples here are ``unittest`` programs: exit 0 says the tests passed, and a summary that says
    every test was skipped is the difference between a verified example and an empty one.
    """
    ran = _UNITTEST_RAN_RE.search(text)
    if ran is None:
        return ""
    result = _UNITTEST_RESULT_RE.search(text)
    verdict = (
        f"{result.group(1)} ({result.group(2)})"
        if result and result.group(2)
        else (result.group(1) if result else "?")
    )
    return f"unittest: ran {ran.group(1)}, {verdict}"


def _missing_input(text: str) -> str:
    """The input a failed test says it could not find (``input/x.xlsx``, ``input/*.xlsx``), else ``""``."""
    # The traceback echoes the test's own source line (``f"Input file {input_path} ..."``): skip placeholders.
    named = next(
        (m for m in _MISSING_INPUT_RES[0].finditer(text) if not re.search(r"[{}$%]", m.group(1))), None
    )
    if named is not None:
        path = named.group(1).replace("\\", "/")
        # The sandbox directory is random; keep the part the sample chose (``input/...``) so the table is stable.
        return path[path.index("input/") :] if "input/" in path else path.rsplit("/", 1)[-1]
    globbed = _MISSING_INPUT_RES[1].search(text)
    if globbed is not None:
        return f"{globbed.group(2)}/*.{globbed.group(1).lower()}"
    return ""


def _first_error_line(outcome: Mapping[str, object]) -> str:
    """The line that says why a run failed: the first ``SomeError: message`` line the run printed (stderr, then
    stdout), else the product's own picker (``error_line``), else the last text line."""
    for stream in (str(outcome.get("stderr") or ""), str(outcome.get("stdout") or "")):
        match = _EXCEPTION_LINE_RE.search(stream)
        if match is not None:
            return _one_line(match.group(0))
    picked = str(outcome.get("error_line") or "")
    if picked:
        return _one_line(picked)
    text = f"{outcome.get('stderr', '')}\n{outcome.get('stdout', '')}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return _one_line(lines[-1]) if lines else f"exit code {outcome.get('exit_code')}"


def classify_outcome(outcome: Mapping[str, object]) -> tuple[str, str]:
    """Map what one sample's run reported to ``(classification, reason)``. Pure: the same dict, the same answer.

    ``outcome`` keys (written by ``run_one_sample`` inside the container): ``compile_error``, ``no_statements``,
    ``verified``, ``fixture_unavailable``, ``missing`` (names the sample asked for and nothing supplied),
    ``exit_code``, ``stdout``, ``stderr``, ``error_line``, ``new_files`` (name -> size of files the run wrote),
    ``invalid_artifacts`` (one line per written file the product's validators reject).
    """
    compile_error = str(outcome.get("compile_error") or "")
    if compile_error:
        return C_COMPILE, _one_line(compile_error)
    if outcome.get("no_statements"):
        return C_NO_STATEMENTS, "only imports, comments or blank lines; nothing was run"
    text = f"{outcome.get('stderr', '')}\n{outcome.get('stdout', '')}"
    if outcome.get("verified"):
        invalid = outcome.get("invalid_artifacts") or []
        if invalid:
            return C_RUNTIME, _one_line("invalid artefact: " + "; ".join(str(item) for item in invalid))
        new_files = outcome.get("new_files") or {}
        printed = str(outcome.get("stdout") or "").strip()
        if printed or (isinstance(new_files, Mapping) and any(size for size in new_files.values())):
            what = (
                "printed output" if printed else "wrote " + ", ".join(sorted(str(name) for name in new_files))
            )
            return C_PRODUCED, _one_line("; ".join(part for part in (what, unittest_summary(text)) if part))
        return C_EXIT0, "; ".join(
            part for part in ("exit 0, no output and no file written", unittest_summary(text)) if part
        )
    # A "name" the verifier extracted from an f-string (``{input_path}``) is a placeholder, not a file.
    missing = [str(item) for item in (outcome.get("missing") or []) if not re.search(r"[{}$%]", str(item))]
    if outcome.get("fixture_unavailable"):
        return C_FIXTURE, _one_line(", ".join(missing) or _missing_input(text) or _first_error_line(outcome))
    if _SYNTAX_RE.search(text):
        return C_COMPILE, _first_error_line(outcome)
    missing_input = _missing_input(text)
    if missing_input:
        return C_FIXTURE, _one_line(missing_input)
    not_found = _FILE_NOT_FOUND_RE.search(text)
    if not_found is not None:
        return C_FIXTURE, _one_line(not_found.group(1))
    if _NETWORK_RE.search(text):
        return C_NETWORK, _first_error_line(outcome)
    return C_RUNTIME, _first_error_line(outcome)


def summarise(results: Sequence[SampleResult]) -> dict[str, object]:
    """Counts per class, the measured yield over the non-excluded samples, and the DG-3 decision."""
    counts = {name: 0 for name in CLASSES}
    for result in results:
        counts[result.classification] += 1
    excluded = counts[C_EXCLUDED]
    total = len(results) - excluded
    verified = counts[C_PRODUCED] + counts[C_EXIT0]
    return {
        "counts": counts,
        "total_samples": len(results),
        "excluded": excluded,
        "denominator": total,
        "verified": verified,
        "yield": (verified / total) if total else 0.0,
        "threshold": DECISION_THRESHOLD,
        "decision": yield_decision(verified, total),
    }


Runner = Callable[[str, str], Mapping[str, object]]


def measure_samples(
    samples: Mapping[str, str],
    runner: Runner,
    *,
    excluded: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> list[SampleResult]:
    """Run ``runner(name, code)`` for every sample and classify it. Output is sorted by name (deterministic).

    ``excluded`` names samples that are not run, with the reason; a sample is classified exactly once, and an
    excluded name that is not among ``samples`` is a caller error.
    """
    excluded = dict(excluded or {})
    unknown = sorted(set(excluded) - set(samples))
    if unknown:
        raise ValueError(f"excluded names that are not samples: {', '.join(unknown)}")
    results: list[SampleResult] = []
    for name in sorted(samples):
        if name in excluded:
            results.append(SampleResult(name, C_EXCLUDED, excluded[name]))
            continue
        started = clock()
        try:
            outcome = runner(name, samples[name])
        except Exception as exc:  # a harness failure is recorded against the sample, never dropped
            elapsed = clock() - started
            results.append(
                SampleResult(name, C_RUNTIME, _one_line(f"harness: {type(exc).__name__}: {exc}"), elapsed)
            )
            continue
        classification, reason = classify_outcome(outcome)
        results.append(SampleResult(name, classification, reason, clock() - started))
    return results


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(results: Sequence[SampleResult], summary: Mapping[str, object]) -> str:
    """The per-sample table and the totals, as markdown."""
    lines = ["| Sample | Classification | Reason | Seconds |", "| --- | --- | --- | ---: |"]
    for result in results:
        lines.append(
            f"| `{_cell(result.name)}` | {result.classification} | {_cell(result.reason)} | {result.seconds:.1f} |"
        )
    counts = summary["counts"]
    assert isinstance(counts, Mapping)
    lines += ["", "| Class | Count |", "| --- | ---: |"]
    lines += [f"| {name} | {counts[name]} |" for name in CLASSES]
    lines += [
        "",
        f"Verified {summary['verified']} of {summary['denominator']} measured samples "
        f"({100 * float(summary['yield']):.1f}%); {summary['excluded']} excluded "
        f"(of {summary['total_samples']} found). Rule: proceed when verified/total >= "
        f"{DECISION_THRESHOLD:.2f}. Decision: **{summary['decision']}**.",
    ]
    return "\n".join(lines) + "\n"


def pilot_pin(values_file: Path, pilot: str) -> tuple[str, str]:
    """``(repository, commit)`` of ``pilot`` (``family/platform``) from the Helm values file."""
    import yaml

    family, _, platform = pilot.partition("/")
    document = yaml.safe_load(values_file.read_text(encoding="utf-8"))

    def walk(node: object) -> list[Mapping[str, object]]:
        found: list[Mapping[str, object]] = []
        if isinstance(node, Mapping):
            if node.get("family") == family and node.get("platform") == platform:
                found.append(node)
            for value in node.values():
                found.extend(walk(value))
        elif isinstance(node, list):
            for value in node:
                found.extend(walk(value))
        return found

    for entry in walk(document):
        library = entry.get("library")
        if isinstance(library, Mapping) and library.get("repository") and library.get("commit"):
            return str(library["repository"]), str(library["commit"])
    raise LookupError(f"no pilot {pilot} with a library pin in {values_file}")


def collect_samples(
    checkout: Path, samples_dir: str, suffixes: Sequence[str] = DEFAULT_SAMPLE_SUFFIXES
) -> dict[str, str]:
    """``{path relative to the sample directory: source}`` for every sample file below it (recursive)."""
    root = checkout / samples_dir
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in suffixes and ".git" not in path.parts:
            found[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return found


# -- inside the container ------------------------------------------------------------------------------------


def _files_below(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _missing_names(output: str) -> list[str]:
    """The names on the verifier's ``[foss-mcp] fixture unavailable: a, b`` line (empty when it wrote none)."""
    marker = "fixture unavailable: "
    for line in reversed(output.splitlines()):
        if marker in line:
            return [part.strip() for part in line.split(marker, 1)[1].split(",") if part.strip()]
    return []


def run_one_sample(name: str, code: str, work: Path) -> dict[str, object]:
    """Verify ONE sample with the product's own verifier, inside the image, and describe what happened."""
    from foss_mcp.indexing import example_artifacts
    from foss_mcp.indexing import example_verifier as ev
    from foss_mcp.indexing.example_candidates import CandidateExample

    started = time.monotonic()
    outcome: dict[str, object] = {"name": name, "compile_error": "", "no_statements": False}
    try:
        ast.parse(code)
    except (SyntaxError, ValueError) as exc:
        outcome["compile_error"] = f"{type(exc).__name__}: {exc}"
        outcome.update({"verified": False, "seconds": round(time.monotonic() - started, 2)})
        return outcome

    captured: dict[str, object] = {}
    original = ev._run_bounded

    def recording(args: list[str], *, cwd: Path, timeout_seconds: float, env=None):  # type: ignore[no-untyped-def]
        before = _files_below(cwd)
        completed = original(args, cwd=cwd, timeout_seconds=timeout_seconds, env=env)
        after = _files_below(cwd)
        captured.update(
            completed=completed,
            new_files={n: s for n, s in after.items() if n not in before or before[n] != s},
            script_dir=cwd,
        )
        return completed

    candidate = CandidateExample(title=name, description="", language="python", code=code)
    scratch = Path(tempfile.mkdtemp(prefix="measure_sample_"))
    ev._run_bounded = recording  # type: ignore[assignment]
    try:
        result = ev.verify_python_example(
            candidate, venv_python=work / "venv" / "bin" / "python", workdir=scratch, repo_dir=work / "repo"
        )
    finally:
        ev._run_bounded = original  # type: ignore[assignment]
    completed = captured.get("completed")
    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    exit_code = getattr(completed, "returncode", None)
    new_files = dict(captured.get("new_files") or {})  # type: ignore[arg-type]
    script_dir = captured.get("script_dir")
    invalid: list[str] = []
    if result.verified and isinstance(script_dir, Path):
        written = {n: (script_dir / n).read_bytes() for n in new_files if (script_dir / n).is_file()}
        invalid = example_artifacts.artifact_problems(written)
    outcome.update(
        verified=bool(result.verified),
        no_statements=completed is None and "no statements of its own" in result.output,
        fixture_unavailable=result.status == "fixture_unavailable",
        missing=_missing_names(result.output),
        exit_code=exit_code,
        stdout=stdout[-_TEXT_CAP * 4 :],
        stderr=stderr[-_TEXT_CAP * 4 :],
        error_line=ev._runtime_error_text(
            ev.RunOutcome(exit_code=exit_code or 0, stdout=stdout, stderr=stderr)
        )
        if not result.verified and completed is not None
        else "",
        new_files=new_files,
        invalid_artifacts=invalid,
        level=result.level,
        seconds=round(time.monotonic() - started, 2),
    )
    shutil.rmtree(scratch, ignore_errors=True)
    return outcome


def setup_environment(repository: str, commit: str, work: Path, checkout: Path) -> dict[str, object]:
    """Build the library venv with the product's own ``prepare_python_library``, the clone replaced by a copy
    of the caller's checkout (already at the pinned commit)."""
    from foss_mcp.indexing import example_verifier as ev

    def copy_checkout(_repository: str, _commit: str, destination: Path) -> None:
        shutil.copytree(checkout, destination, symlinks=True)

    ev._clone_pinned_commit = copy_checkout  # type: ignore[assignment]
    venv_python = ev.prepare_python_library(repository, commit, work)
    version = subprocess.run([str(venv_python), "--version"], capture_output=True, text=True, check=False)
    freeze = subprocess.run(
        [str(venv_python), "-m", "pip", "freeze"], capture_output=True, text=True, check=False
    )
    return {
        "venv_python": str(venv_python),
        "python_version": (version.stdout or version.stderr).strip(),
        "pip_freeze": sorted(line for line in freeze.stdout.splitlines() if line.strip()),
    }


def _inner_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="measure_sample_yield inner")
    sub = parser.add_subparsers(dest="mode", required=True)
    setup = sub.add_parser("setup")
    setup.add_argument("--repository", required=True)
    setup.add_argument("--commit", required=True)
    setup.add_argument("--checkout", default=_CONTAINER_CHECKOUT)
    setup.add_argument("--work", default=_CONTAINER_WORK)
    run = sub.add_parser("run")
    run.add_argument("--name", required=True)
    run.add_argument("--work", default=_CONTAINER_WORK)
    run.add_argument("--seed", default="")
    args = parser.parse_args(list(argv))
    if args.mode == "setup":
        payload = setup_environment(args.repository, args.commit, Path(args.work), Path(args.checkout))
    else:
        if args.seed:
            # A fresh, writable copy of the set-up environment for THIS sample only: a sample that writes under the
            # repository (editable install) must neither fail on a read-only tree nor leave files for the next one.
            shutil.copytree(args.seed, args.work, symlinks=True, dirs_exist_ok=True)
        payload = run_one_sample(args.name, sys.stdin.read(), Path(args.work))
    print(RESULT_MARKER + json.dumps(payload))
    return 0


# -- on the host --------------------------------------------------------------------------------------------


def _docker(
    args: Sequence[str], *, stdin: str | None = None, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _parse_result(stdout: str) -> dict[str, object]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER) :])
    raise RuntimeError("the container wrote no result line")


def _tool_mount() -> list[str]:
    return ["--mount", f"type=bind,source={Path(__file__).resolve()},target={_CONTAINER_TOOL},readonly"]


class DockerRunner:
    """Runs setup and each sample in the ingestion image; one fresh ``--network none`` container per sample."""

    def __init__(self, image: str, volume: str) -> None:
        self.image = image
        self.volume = volume

    def setup(self, checkout: Path, repository: str, commit: str) -> dict[str, object]:
        created = _docker(["volume", "create", self.volume], timeout=60)
        if created.returncode != 0:
            raise RuntimeError(f"docker volume create failed: {created.stderr.strip()}")
        completed = _docker(
            [
                "run", "--rm", "--user", "root", "--entrypoint", "python",
                "-v", f"{self.volume}:{_CONTAINER_WORK}",
                "--mount", f"type=bind,source={checkout},target={_CONTAINER_CHECKOUT},readonly",
                *_tool_mount(),
                self.image, _CONTAINER_TOOL, "setup", "--repository", repository, "--commit", commit,
            ],
            timeout=_SETUP_TIMEOUT_SECONDS,
        )  # fmt: skip
        if completed.returncode != 0:
            raise RuntimeError(f"setup container failed ({completed.returncode}): {completed.stderr[-1500:]}")
        return _parse_result(completed.stdout)

    def __call__(self, name: str, code: str) -> Mapping[str, object]:
        container = f"p4b-yield-{uuid.uuid4().hex[:12]}"
        try:
            completed = _docker(
                [
                    "run", "--rm", "-i", "--name", container, "--network", "none", "--memory", "4g",
                    "--cpus", "2", "--pids-limit", "512", "--entrypoint", "python",
                    "--tmpfs", f"{_CONTAINER_WORK}:rw,exec,uid=10001,gid=10001,size=2g",
                    "-v", f"{self.volume}:{_CONTAINER_SEED}:ro",
                    *_tool_mount(),
                    self.image, _CONTAINER_TOOL, "run", "--name", name, "--seed", _CONTAINER_SEED,
                ],
                stdin=code,
                timeout=_SAMPLE_CONTAINER_TIMEOUT_SECONDS,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            _docker(["kill", container], timeout=60)
            return {
                "verified": False,
                "stderr": f"[foss-mcp] TIMEOUT: container exceeded {_SAMPLE_CONTAINER_TIMEOUT_SECONDS}s",
            }
        if completed.returncode != 0:
            raise RuntimeError(f"container exited {completed.returncode}: {completed.stderr[-400:]}")
        return _parse_result(completed.stdout)

    def remove_volume(self) -> None:
        _docker(["volume", "rm", "-f", self.volume], timeout=120)


def _image_facts(image: str) -> dict[str, object]:
    completed = _docker(["image", "inspect", "--format", "{{json .}}", image], timeout=60)
    if completed.returncode != 0:
        return {"image": image, "error": "docker image inspect failed"}
    data = json.loads(completed.stdout)
    labels = (data.get("Config") or {}).get("Labels") or {}
    return {
        "image": image,
        "image_id": data.get("Id"),
        "created": data.get("Created"),
        "toolchain_labels": {
            k: v for k, v in sorted(labels.items()) if k.startswith("org.foss-mcp.toolchain")
        },
    }


def _parse_exclusions(items: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        name, separator, reason = item.partition("=")
        if not separator or not name or not reason.strip():
            raise SystemExit(f"--exclude wants NAME=REASON, got {item!r}")
        parsed[name] = reason.strip()
    return parsed


def _measure_command(args: argparse.Namespace) -> int:
    repository, commit = (
        (args.repository, args.commit) if args.repository else pilot_pin(VALUES_FILE, args.pilot)
    )
    checkout = Path(args.checkout).resolve()
    samples = collect_samples(checkout, args.samples_dir)
    if not samples:
        print(f"no sample files under {checkout / args.samples_dir}", file=sys.stderr)
        return 2
    runner = DockerRunner(args.image, f"p4b-yield-{uuid.uuid4().hex[:8]}")
    try:
        environment = runner.setup(checkout, repository, commit)
        results = measure_samples(samples, runner, excluded=_parse_exclusions(args.exclude))
    finally:
        if not args.keep_volume:
            runner.remove_volume()
    summary = summarise(results)
    payload = {
        "pilot": args.pilot,
        "repository": repository,
        "commit": commit,
        "samples_dir": args.samples_dir,
        "environment": {**environment, **_image_facts(args.image)},
        "summary": summary,
        "samples": [asdict(result) for result in results],
    }
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown(results, summary)
    if args.out_md:
        Path(args.out_md).write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] in (["setup"], ["run"]):
        return _inner_main(arguments)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    measure = sub.add_parser("measure")
    measure.add_argument("--pilot", required=True)
    measure.add_argument("--checkout", required=True)
    measure.add_argument("--samples-dir", default="examples")
    measure.add_argument("--image", default=DEFAULT_IMAGE)
    measure.add_argument("--out-json")
    measure.add_argument("--out-md")
    measure.add_argument("--exclude", action="append", default=[], metavar="NAME=REASON")
    measure.add_argument("--repository")
    measure.add_argument("--commit")
    measure.add_argument("--keep-volume", action="store_true")
    pin = sub.add_parser("pin")
    pin.add_argument("--pilot", required=True)
    args = parser.parse_args(arguments)
    if args.command == "pin":
        repository, commit = pilot_pin(VALUES_FILE, args.pilot)
        print(f"{repository} {commit}")
        return 0
    return _measure_command(args)


if __name__ == "__main__":
    sys.exit(main())
