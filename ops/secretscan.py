"""Secret scanner for commits, pushes and CI (DECISION_LOG 2026-10-11).

The operator will hand an agent-operated repository a GitHub token, an embedding-gateway key and an internal
registry login, and asked that none of them ever be exposed by any agent. This module is the last line: it refuses a
commit or push whose ADDED lines contain a credential, and it never prints one.

Two layers, both on added lines only:

1. High-precision token shapes (GitHub, AWS, Slack, OpenAI-style, private-key blocks, docker config auth, long bearer
   literals). Measured against every tracked file on 2026-10-11: zero false positives.
2. Exact-match detection of the VALUES of the secret-looking variables in the current environment (names matching
   TOKEN/KEY/SECRET/PASSWORD/CREDENTIAL, value at least 16 characters, no whitespace or path separators). This catches a
   key in any format, including one a model pasted into a file or a commit message. The report names the variable, never
   its value.

A line containing the marker `secretscan:allow` is skipped, for test fixtures that need a fake token.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import gatectl as G

ALLOW_MARKER = "secretscan:allow"
ENV_NAME_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSW|CREDENTIAL)", re.IGNORECASE)
MIN_ENV_VALUE = 16

PATTERNS: dict[str, re.Pattern[str]] = {
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
    "github_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "docker_auth": re.compile(r'"auth"\s*:\s*"[A-Za-z0-9+/=]{20,}"'),
    "slack_token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    "bearer_literal": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{30,}"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
}

_SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".whl", ".jar", ".zip", ".gz", ".tar", ".pdf")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str  # a pattern name, or "env:<VARIABLE_NAME>"

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  {self.kind}"


def secret_env_values(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Variable name -> value for environment entries that look like secrets and are safe to match literally."""
    source: Mapping[str, str] = os.environ if env is None else env
    out: dict[str, str] = {}
    for name, value in source.items():
        if not ENV_NAME_RE.search(name) or len(value) < MIN_ENV_VALUE:
            continue
        if re.search(r"\s", value) or "/" in value or "\\" in value:
            continue
        out[name] = value
    return out


def scan_line(line: str, env_secrets: dict[str, str]) -> list[str]:
    """The kinds found in one line (never the values)."""
    if ALLOW_MARKER in line:
        return []
    kinds = [name for name, rx in PATTERNS.items() if rx.search(line)]
    kinds += [f"env:{name}" for name, value in env_secrets.items() if value in line]
    return kinds


def scan_text(
    text: str, path: str, env_secrets: dict[str, str] | None = None, first_line: int = 1
) -> list[Finding]:
    env_secrets = secret_env_values() if env_secrets is None else env_secrets
    out: list[Finding] = []
    for offset, line in enumerate(text.splitlines()):
        for kind in scan_line(line, env_secrets):
            out.append(Finding(path, first_line + offset, kind))
    return out


def added_lines_from_patch(patch: str) -> list[tuple[str, int, str]]:
    """(path, new line number, text) for every ADDED line of a unified diff (`git diff -U0` / `git log -p -U0`)."""
    out: list[tuple[str, int, str]] = []
    path, lineno = "", 0
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = "" if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            if path:
                out.append((path, lineno, raw[1:]))
            lineno += 1
    return out


def scan_patch(patch: str, env_secrets: dict[str, str] | None = None) -> list[Finding]:
    env_secrets = secret_env_values() if env_secrets is None else env_secrets
    found: list[Finding] = []
    for path, lineno, text in added_lines_from_patch(patch):
        if path.lower().endswith(_SKIP_SUFFIXES):
            continue
        for kind in scan_line(text, env_secrets):
            found.append(Finding(path, lineno, kind))
    return found


def _git_patch(*args: str) -> str:
    rc, out, err = G.run(["git", *args])
    if rc != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {(err or out).strip()[:160]}")
    return out


def scan_staged(env_secrets: dict[str, str] | None = None) -> list[Finding]:
    return scan_patch(_git_patch("diff", "--cached", "-U0", "--no-color", "--no-ext-diff"), env_secrets)


def scan_pushed(local_sha: str, env_secrets: dict[str, str] | None = None) -> list[Finding]:
    """Every line ADDED by any commit that would be newly published (reachable from local_sha, not from a remote ref).

    Walks each commit's own patch, so a secret added and then removed again inside the same push is still caught.
    """
    patch = _git_patch(
        "log", local_sha, "--not", "--remotes", "-p", "-U0", "--no-color", "--no-ext-diff", "--format="
    )
    return scan_patch(patch, env_secrets)


def scan_tree(env_secrets: dict[str, str] | None = None) -> list[Finding]:
    env_secrets = secret_env_values() if env_secrets is None else env_secrets
    rc, out, _ = G.run(["git", "ls-files", "-z"])
    if rc != 0:
        raise RuntimeError("git ls-files failed")
    found: list[Finding] = []
    for rel in filter(None, out.split("\0")):
        if rel.lower().endswith(_SKIP_SUFFIXES):
            continue
        p = Path(G.REPO) / rel
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        found.extend(scan_text(text, rel, env_secrets))
    return found


def report(findings: list[Finding]) -> str:
    """Human text for a refusal: where and what kind, never the secret itself."""
    lines = [
        f"SECRET SCAN FAILED - {len(findings)} credential-shaped value(s) in added lines (values are never printed):"
    ]
    lines += [f"  {f}" for f in findings[:40]]
    if len(findings) > 40:
        lines.append(f"  ... and {len(findings) - 40} more")
    lines.append(
        "Remove the value, rotate it if it left this machine, and refer to it by variable name instead."
    )
    return "\n".join(lines)
