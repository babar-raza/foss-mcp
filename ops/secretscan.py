"""Secret scanner for commits, pushes and CI (DECISION_LOG 2026-10-11).

The operator will hand an agent-operated repository a GitHub token, an embedding-gateway key and an internal registry login, and
asked that none of them ever be exposed by any agent. This module is the last line: it refuses a commit or push whose ADDED lines,
file names or commit and tag messages contain a credential, and it never prints one.

Two layers:

1. High-precision token shapes (GitHub, AWS, Google, Slack, OpenAI and Anthropic style, private-key blocks, docker config auth, long
   bearer literals). Measured against every tracked file on 2026-10-11: zero false positives.
2. Exact-match detection of the VALUES of the secret-looking variables in the current environment (name tokens such as TOKEN, KEY,
   SECRET, PASSWORD, PASS, AUTH, PAT, CREDENTIAL; value at least 8 characters, no whitespace, not a filesystem path), in the raw form
   and in its base64 and URL-encoded forms. This catches a key in any format, including one pasted into a file or a commit message.
   The report names the variable, never its value.

Hardened after an independent red-team review (2026-10-11): the diff parser is a state machine, so an added line that looks like a
file header cannot blind it; lines are split on "\\n" only, so exotic separators cannot hide a token; merge commits are scanned
(first-parent diff); binary attributes cannot suppress a diff (`--text`); commit and tag messages and file names are scanned; a push
is scanned from the remote's own recorded tip when there is one; the allow marker is honoured only under test-fixture paths.
"""

from __future__ import annotations

import base64
import os
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import gatectl as G

ALLOW_MARKER = "secretscan:allow"
ALLOW_PATH_PREFIXES = ("ops/tests/", "tests/")
SECRET_NAME_TOKENS = frozenset(
    {
        "TOKEN",
        "KEY",
        "SECRET",
        "PASSWORD",
        "PASSWD",
        "PASS",
        "AUTH",
        "PAT",
        "CREDENTIAL",
        "CREDENTIALS",
        "APIKEY",
    }
)
SECRET_NAME_SUFFIXES = ("TOKEN", "KEY", "SECRET", "PASSWORD")
MIN_ENV_VALUE = 8
MAX_TREE_FILE_BYTES = 5 * 1024 * 1024
_TRIVIAL_VALUES = frozenset(
    {"true", "false", "none", "null", "changeme", "password", "default", "enabled", "disabled"}
)
_ZERO_SHA = "0" * 40

PATTERNS: dict[str, re.Pattern[str]] = {
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
    "github_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    "aws_access_key": re.compile(r"\bA(?:KIA|SIA)[0-9A-Z]{16}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "docker_auth": re.compile(r'"auth"\s*:\s*"[A-Za-z0-9+/=]{20,}"'),
    "slack_token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    "bearer_literal": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{30,}"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b"),
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str  # a pattern name, or "env:<VARIABLE_NAME>"

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  {self.kind}"


def _looks_secret_name(name: str) -> bool:
    upper = name.upper()
    tokens = {t for t in re.split(r"[^A-Z0-9]+", upper) if t}
    return bool(tokens & SECRET_NAME_TOKENS) or upper.endswith(SECRET_NAME_SUFFIXES)


def secret_env_values(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Variable name -> value for environment entries that look like secrets and are safe to match literally."""
    source: Mapping[str, str] = os.environ if env is None else env
    out: dict[str, str] = {}
    for name, value in source.items():
        if not _looks_secret_name(name) or len(value) < MIN_ENV_VALUE:
            continue
        if (
            re.search(r"\s", value)
            or "\\" in value
            or value.startswith("/")
            or re.match(r"^[A-Za-z]:", value)
        ):
            continue
        if value.lower() in _TRIVIAL_VALUES or len(set(value)) <= 2:
            continue
        out[name] = value
    return out


def _variants(value: str) -> tuple[str, ...]:
    raw = value.encode("utf-8", "ignore")
    forms = {value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)}
    for enc in (base64.b64encode(raw), base64.urlsafe_b64encode(raw)):
        text = enc.decode("ascii")
        forms.add(text)
        forms.add(text.rstrip("="))
    return tuple(f for f in forms if len(f) >= MIN_ENV_VALUE)


def _marker_allowed(path: str | None) -> bool:
    return bool(path) and path.replace("\\", "/").startswith(ALLOW_PATH_PREFIXES)  # type: ignore[union-attr]


def scan_line(line: str, env_secrets: Mapping[str, str], path: str | None = None) -> list[str]:
    """The kinds found in one line (never the values)."""
    if ALLOW_MARKER in line and _marker_allowed(path):
        return []
    kinds = [name for name, rx in PATTERNS.items() if rx.search(line)]
    kinds += [
        f"env:{name}" for name, value in env_secrets.items() if any(v in line for v in _variants(value))
    ]
    return kinds


def scan_text(
    text: str, path: str, env_secrets: Mapping[str, str] | None = None, first_line: int = 1
) -> list[Finding]:
    env_secrets = secret_env_values() if env_secrets is None else env_secrets
    out: list[Finding] = []
    for offset, line in enumerate(text.split("\n")):
        for kind in scan_line(line, env_secrets, path):
            out.append(Finding(path, first_line + offset, kind))
    return out


def _clean_path(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] == '"':
        token = token[1:-1]
    if token.startswith(("a/", "b/")):
        token = token[2:]
    return token


def parse_patch(patch: str) -> tuple[list[tuple[str, int, str]], list[str]]:
    """(added lines as (path, new line number, text), file paths seen) from a unified diff.

    A small state machine: header lines (`--- `, `+++ `, `index`, ...) are recognised only between a `diff --git` line and the
    first `@@`; after a hunk starts, EVERY line beginning with `+` is added content, including one that looks like `+++ b/x`.
    """
    added: list[tuple[str, int, str]] = []
    paths: list[str] = []
    state, path, lineno = "none", "", 0
    for raw in patch.split("\n"):
        if raw.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            state, path = "header", ""
            m = re.match(r'diff --git (?:"?a/.*?"?) (?:"?b/)?(.*?)"?$', raw)
            if m:
                path = _clean_path(m.group(1))
            if path:
                paths.append(path)
            continue
        if state == "none" and raw.startswith("+++ "):  # a bare unified diff with no `diff --git` line
            state = "header"
        if state == "header":
            if raw.startswith("+++ "):
                target = _clean_path(raw[4:])
                path = "" if target == "/dev/null" else target
                if path and path not in paths:
                    paths.append(path)
            elif raw.startswith("@@"):
                state = "hunk"
                m = re.search(r"\+(\d+)", raw)
                lineno = int(m.group(1)) if m else 0
            continue
        if state == "hunk":
            if raw.startswith("@@"):
                m = re.search(r"\+(\d+)", raw)
                lineno = int(m.group(1)) if m else 0
            elif raw.startswith("+"):
                if path:
                    added.append((path, lineno, raw[1:]))
                lineno += 1
    return added, paths


def added_lines_from_patch(patch: str) -> list[tuple[str, int, str]]:
    return parse_patch(patch)[0]


def scan_patch(patch: str, env_secrets: Mapping[str, str] | None = None) -> list[Finding]:
    env_secrets = secret_env_values() if env_secrets is None else env_secrets
    added, paths = parse_patch(patch)
    found: list[Finding] = []
    for p in paths:  # a credential in a file NAME is still a credential
        for kind in scan_line(p, env_secrets):
            found.append(Finding(p, 0, kind))
    for path, lineno, text in added:
        for kind in scan_line(text, env_secrets, path):
            found.append(Finding(path, lineno, kind))
    return found


def scan_message(text: str, label: str, env_secrets: Mapping[str, str] | None = None) -> list[Finding]:
    """A commit or tag message. The allow marker is never honoured here."""
    env_secrets = secret_env_values() if env_secrets is None else env_secrets
    out: list[Finding] = []
    for i, line in enumerate(text.split("\n"), 1):
        for kind in scan_line(line, env_secrets, None):
            out.append(Finding(label, i, kind))
    return out


_DIFF_FLAGS = ("-U0", "--no-color", "--no-ext-diff", "--text", "--no-textconv", "--no-renames")


def _git_out(*args: str) -> str:
    rc, out, err = G.run(["git", "-c", "core.quotepath=false", *args])
    if rc != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {(err or out).strip()[:160]}")
    return out


def scan_staged(env_secrets: Mapping[str, str] | None = None) -> list[Finding]:
    return scan_patch(_git_out("diff", "--cached", *_DIFF_FLAGS), env_secrets)


def _range_args(local_sha: str, base: str | None, remote: str | None) -> list[str]:
    if base and base != _ZERO_SHA:
        return [f"{base}..{local_sha}"]
    return [local_sha, "--not", f"--remotes={remote}" if remote else "--remotes"]


def scan_pushed(
    local_sha: str,
    env_secrets: Mapping[str, str] | None = None,
    base: str | None = None,
    remote: str | None = None,
) -> list[Finding]:
    """Everything a push would newly publish: every added line and file name of every commit patch (merges by first parent),
    every commit message, and the message of an annotated tag.

    With `base` (the remote's recorded tip, from the pre-push protocol) the range is exactly base..local; otherwise commits
    reachable from a remote-tracking ref of `remote` are excluded. Walks each commit's own patch, so a secret added and then
    removed again inside the same push is still caught.
    """
    env_secrets = secret_env_values() if env_secrets is None else env_secrets
    rng = _range_args(local_sha, base, remote)
    found: list[Finding] = []
    kind_of = G.run(["git", "cat-file", "-t", local_sha])[1].strip()
    if kind_of == "tag":
        found += scan_message(
            _git_out("cat-file", "-p", local_sha), f"tag {local_sha[:12]} message", env_secrets
        )
    patch = _git_out("log", *rng, "-p", *_DIFF_FLAGS, "--diff-merges=first-parent", "--format=")
    found += scan_patch(patch, env_secrets)
    records = _git_out("log", *rng, "--format=%H%x1f%B%x1e")
    for rec in filter(None, records.split("\x1e")):
        sha, _, body = rec.strip("\n").partition("\x1f")
        found += scan_message(body, f"commit {sha[:12]} message", env_secrets)
    return found


def scan_tree(env_secrets: Mapping[str, str] | None = None) -> list[Finding]:
    env_secrets = secret_env_values() if env_secrets is None else env_secrets
    rc, out, _ = G.run(["git", "ls-files", "-z"])
    if rc != 0:
        raise RuntimeError("git ls-files failed")
    found: list[Finding] = []
    for rel in filter(None, out.split("\0")):
        p = Path(G.REPO) / rel
        try:
            if p.stat().st_size > MAX_TREE_FILE_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for kind in scan_line(rel, env_secrets):
            found.append(Finding(rel, 0, kind))
        found.extend(scan_text(text, rel, env_secrets))
    return found


def report(findings: list[Finding]) -> str:
    """Human text for a refusal: where and what kind, never the secret itself."""
    lines = [
        f"SECRET SCAN FAILED - {len(findings)} credential-shaped value(s) found (values are never printed):"
    ]
    lines += [f"  {f}" for f in findings[:40]]
    if len(findings) > 40:
        lines.append(f"  ... and {len(findings) - 40} more")
    lines.append(
        "Remove the value, rotate it if it left this machine, and refer to it by variable name instead."
    )
    return "\n".join(lines)
