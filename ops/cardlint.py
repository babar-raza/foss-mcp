"""Authoring-time lint for taskcard checks (redesign D5, DECISION_LOG 2026-10-04).

Four of the defects that cost attempts in this project were visible in the card
text or in the test files a card names, before any worker ran:

  L1  A falsifier built with a backslash or an escaped quote. The shell mangles
      it, so the falsifier does not apply, and a check that never broke is
      charged as the card's failure. (TC-168 attempt 1.)
  L2  A check that reaches the network under `network: false`. The offline gate
      runs with a dead proxy, so such a test errors at setup rather than
      skipping. (TC-168 attempt 1: the cells/cpp pinned-commit fixtures.)
  L3  A test that runs the CI or a hook. Its subprocess can run that same test
      again, so the run recurses. (TC-173: the githooks test ran the full CI.)

Scope and the rule for old cards: lint applies only to cards WITHOUT an accepted
receipt. A card that passed verify has been proven once, so this module must not
retroactively fail it. That keeps what already works from being weakened.

The lint is a heuristic over text, and it can be wrong in both directions. It
returns plain findings for a supervisor to read. It is never a silent gate.
"""

from __future__ import annotations

import re
from pathlib import Path

# Substrings that mean "this test touches the network". Chosen from the real
# failures listed above, and kept specific enough to avoid matching ordinary words.
NETWORK_HINTS = (
    "git clone",
    "git fetch",
    "'clone'",
    "'fetch'",
    "_clone_pinned_commit",
    "urlopen(",
    "requests.get(",
)

# Substrings that mean "this test runs the CI or a hook".
CI_HINTS = (
    # Invocation shapes only. A bare file name also matches assertion strings that search for the
    # hook's output, which is not a run (TC-180).
    "'bash', 'scripts/ci_check.sh'",
    '"bash", "scripts/ci_check.sh"',
    "'gatectl.py', 'gate-exit'",
    "'bash', '.githooks/pre-push'",
)

_TEST_TOKEN = re.compile(r"^tests/\S*$")


def _strip_docstrings(text: str) -> str:
    """The source with its docstrings removed. A test that explains a hook is not a test that runs it."""
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    out = text
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                out = out.replace(first.value.value, "")
    return out


def _check_commands(card: dict) -> list[str]:
    return [str(c.get("command", "")) for c in card.get("checks", []) or []]


def _test_files(card: dict, repo: Path) -> list[Path]:
    """Python files a card's check commands point at, directories expanded."""
    out: list[Path] = []
    for cmd in _check_commands(card):
        for raw in cmd.split():
            tok = raw.strip("'\"")
            if not _TEST_TOKEN.match(tok):
                continue
            p = repo / tok.rstrip("/")
            if p.is_dir():
                out.extend(sorted(p.rglob("*.py")))
            elif p.is_file() and p.suffix == ".py":
                out.append(p)
    # Stable and de-duplicated, so findings are the same on every run.
    return sorted(set(out))


def _mutate_problems(card: dict) -> list[str]:
    neg = card.get("negative_control") or {}
    mutate = str(neg.get("mutate", ""))
    cid = card.get("id", "?")
    out = []
    if "\\" in mutate:
        out.append(
            f"{cid}: negative_control.mutate contains a backslash. The shell can mangle it, "
            "so the falsifier may never apply. Build it with chr() (AGENTS.md, falsifier rule)."
        )
    return out


def lint_card(card: dict, repo: Path, *, accepted: bool) -> list[str]:
    """Findings for one card. Empty when the card is accepted (grandfathered)."""
    if accepted:
        return []
    cid = card.get("id", "?")
    problems = _mutate_problems(card)

    files = _test_files(card, repo)
    offline = not bool(card.get("network", False))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(repo).as_posix()
        if offline:
            for hint in NETWORK_HINTS:
                if hint in text:
                    problems.append(
                        f"{cid}: {rel} reaches the network ({hint!r}) but the card says "
                        "network: false. Declare network: true, or read the pinned fixture "
                        "from the cache (D4)."
                    )
                    break
        code = _strip_docstrings(text)
        for hint in CI_HINTS:
            if hint in code:
                problems.append(
                    f"{cid}: {rel} invokes the CI or a hook ({hint!r}). A test that runs "
                    "the gate can recurse into itself (TC-173)."
                )
                break
    return problems
