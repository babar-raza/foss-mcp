"""G3/TC-552: nothing shipped names the removed pilot family (REQ-G3-005, REQ-G3-008).

The operator removed one whole product family from the product on 2026-10-16 (its eight upstream repositories no longer
exist). TC-551 removed the pilots, their data and every roster; TC-552 removed the last mentions in source comments and
in tests that used the name as a synthetic one. This module is the permanent guard: it scans every file git tracks,
case-insensitively, and allows the name only in the immutable governance history and in the single removal sentence of
docs/RELEASE.md. A later card that reintroduces the name anywhere else fails here, naming ``path:line``.

Offline and deterministic: it reads files that git already tracks. A checkout without git FAILS the first test, it does
not skip. The name is built from two halves on purpose, so that this file and tests/deploy/test_pilot_roster.py never
contain the string they forbid.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NEEDLE = "j" + "map"

# Paths (prefix, or exact file) where the name is history and may stay. Adding an entry later needs a card.
HISTORY_ALLOWANCE: tuple[str, ...] = (
    "plans/",  # the taskcards, including every card that built or removed the family
    "evidence/",  # acceptance receipts and checks output of those cards
    "ops/",  # the dispatch and instruction log
    "project/",  # the derived state of every card
    "docs/DECISION_LOG.md",  # the append-only record of decisions, including the removal itself
    "docs/REVIEW_LEDGER.md",  # generated from the finding texts of build_review_ledger.py, byte for byte
    "scripts/release/build_review_ledger.py",  # the source of those finding texts
)

# The one document that may name the family, on exactly one line: the sentence that records the removal.
REMOVAL_DOCUMENT = "docs/RELEASE.md"
REMOVAL_LINE_MARKER = "upstream GitHub repositories no longer exist"

MAX_REPORTED = 60
BINARY_SNIFF_BYTES = 8192

Reader = Callable[[str], bytes]


def is_history(path: str) -> bool:
    return any(
        path == entry or (entry.endswith("/") and path.startswith(entry)) for entry in HISTORY_ALLOWANCE
    )


def scan(paths: Iterable[str], read: Reader) -> list[str]:
    """Return ``path:line`` entries (``path`` alone for a file name) that name the family outside the allowance."""
    problems: list[str] = []
    for path in sorted(paths):
        if is_history(path):
            continue
        if NEEDLE in path.lower():
            problems.append(f"{path} (the file name)")
        data = read(path)
        if b"\x00" in data[:BINARY_SNIFF_BYTES]:
            continue
        lines = data.decode("utf-8", errors="replace").splitlines()
        hits = [number for number, line in enumerate(lines, start=1) if NEEDLE in line.lower()]
        if path == REMOVAL_DOCUMENT:
            hits = [n for n in hits if REMOVAL_LINE_MARKER not in lines[n - 1]] + [
                n for n in hits if REMOVAL_LINE_MARKER in lines[n - 1]
            ][1:]
        problems.extend(f"{path}:{number}" for number in hits)
    return problems


def tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return [p for p in result.stdout.decode("utf-8").split("\0") if p]


def read_tracked(path: str) -> bytes:
    target = REPO_ROOT / path
    # A tracked file deleted in the working tree (or a submodule or symlink to nowhere) has no content to scan.
    return target.read_bytes() if target.is_file() else b""


def test_the_scan_covers_the_whole_tracked_tree() -> None:
    paths = tracked_paths()
    assert len(paths) > 1000, f"git ls-files returned only {len(paths)} paths"
    assert "README.md" in paths
    assert "infra/helm/foss-mcp/values.yaml" in paths
    assert any(is_history(p) for p in paths), "no history path is tracked: the allowance would be vacuous"


def test_no_tracked_file_outside_history_names_the_removed_family() -> None:
    problems = scan(tracked_paths(), read_tracked)
    shown = problems[:MAX_REPORTED]
    more = f" (+{len(problems) - MAX_REPORTED} more)" if len(problems) > MAX_REPORTED else ""
    assert not problems, (
        "the removed family is named outside the history allowance:\n  " + "\n  ".join(shown) + more
    )


def test_the_release_document_names_it_in_exactly_one_line() -> None:
    lines = (REPO_ROOT / REMOVAL_DOCUMENT).read_text(encoding="utf-8").splitlines()
    named = [line for line in lines if NEEDLE in line.lower()]
    assert len(named) == 1, (
        f"{REMOVAL_DOCUMENT} names the removed family on {len(named)} lines, expected exactly 1"
    )
    assert REMOVAL_LINE_MARKER in named[0]


def test_the_scan_flags_a_name_in_a_line_and_in_a_file_name_and_skips_history() -> None:
    files = {
        "src/a.py": b"fine\nthe " + NEEDLE.upper().encode() + b" client\n",
        f"docs/{NEEDLE}-notes.md": b"clean\n",
        "plans/TC-001.yaml": NEEDLE.encode() + b"\n",
        "evidence/x/receipt.json": NEEDLE.encode(),
        "docs/DECISION_LOG.md": NEEDLE.encode(),
        "assets/logo.bin": b"\x00" + NEEDLE.encode(),
        "src/clean.py": b"nothing here\n",
    }
    problems = scan(files, files.__getitem__)
    assert problems == [f"docs/{NEEDLE}-notes.md (the file name)", "src/a.py:2"]


def test_the_scan_tolerates_exactly_one_removal_line_and_flags_a_second() -> None:
    removal = f"The eight {NEEDLE} pilots were removed because their {REMOVAL_LINE_MARKER}.\n".encode()
    other = f"a {NEEDLE} mention elsewhere\n".encode()
    one = {REMOVAL_DOCUMENT: b"intro\n" + removal}
    assert scan(one, one.__getitem__) == []
    two = {REMOVAL_DOCUMENT: b"intro\n" + removal + b"middle\n" + removal}
    assert scan(two, two.__getitem__) == [f"{REMOVAL_DOCUMENT}:4"]
    unrelated = {REMOVAL_DOCUMENT: removal + other}
    assert scan(unrelated, unrelated.__getitem__) == [f"{REMOVAL_DOCUMENT}:2"]
