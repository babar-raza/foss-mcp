"""Card preflight (Round-3 item SUP-1): catch a card's authoring defects BEFORE a worker is dispatched.

Why: the decision log attributes most rework to supervisor-authored card defects, and four more appeared in two days of
the Round-3 work: `min_tests` above the number of tests the first check collects (TC-554, TC-555), a check that names a test
file that does not exist (TC-567), a check that includes host-sensitive tests (TC-554) and a placeholder negative control.
Each one cost a worker round trip and a 20 to 30 minute review.

Pure functions plus one runner; the pytest collection is injected so the rules are unit-testable without running pytest.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from collections.abc import Callable, Iterable
from pathlib import Path

# pytest options that take a value in a separate token; the value must not be read as a test path.
_VALUE_OPTIONS = {
    "-k",
    "-m",
    "-p",
    "-c",
    "-n",
    "--deselect",
    "--ignore",
    "--ignore-glob",
    "--maxfail",
    "--timeout",
    "--junitxml",
    "--rootdir",
}
PLACEHOLDER_MARKERS = ("to be authored", "negative control to be authored", "PLACEHOLDER")
# How many tests a new test file may be assumed to add before the supervisor must confirm min_tests by hand.
NEW_TEST_SLACK = 60


def split_pytest(command: str) -> tuple[list[str], list[str]] | None:
    """(paths, collect_args) of a `{python} -m pytest ...` check command, or None when it is not a pytest command.

    `collect_args` are the selection options that change which tests are collected (-k, -m, --deselect, --ignore): they are
    passed to `pytest --collect-only` unchanged so the count matches what the check will run.
    """
    try:
        tokens = shlex.split(command.replace("{python}", "python"), posix=True)
    except ValueError:
        return None
    if "pytest" not in tokens:
        return None
    rest = tokens[tokens.index("pytest") + 1 :]
    paths: list[str] = []
    collect_args: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in _VALUE_OPTIONS and i + 1 < len(rest):
            if tok in {"-k", "-m", "--deselect", "--ignore", "--ignore-glob"}:
                collect_args += [tok, rest[i + 1]]
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        paths.append(tok.split("::", 1)[0])
        i += 1
    return paths, collect_args


def _covered(path: str, write_paths: Iterable[str]) -> bool:
    norm = path.replace("\\", "/")
    for w in write_paths:
        w = w.replace("\\", "/")
        if norm == w or fnmatch.fnmatch(norm, w) or (w.endswith("/") and norm.startswith(w)):
            return True
    return False


def preflight(
    card: dict,
    repo: Path,
    collect_count: Callable[[list[str], list[str]], int | None],
) -> tuple[list[str], list[str]]:
    """(problems, warnings) for one card. A problem blocks dispatch; a warning needs the supervisor's eyes.

    `collect_count(existing_paths, collect_args)` returns the number of tests pytest would collect, or None when it cannot tell.
    """
    cid = card.get("id", "?")
    problems: list[str] = []
    warnings: list[str] = []
    write_paths = card.get("write_paths", []) or []
    min_tests = card.get("min_tests", 0)

    nc = card.get("negative_control") or {}
    mutate = nc.get("mutate", "") if isinstance(nc, dict) else ""
    rationale = nc.get("rationale", "") if isinstance(nc, dict) else ""
    if not str(mutate).strip():
        problems.append(f"{cid}: the negative control has no mutate command")
    for marker in PLACEHOLDER_MARKERS:
        if marker.lower() in (str(mutate) + " " + str(rationale)).lower():
            problems.append(
                f"{cid}: the negative control is still the registration placeholder ({marker!r}); author a real falsifier before dispatch"
            )
            break
    if "REGISTERED AS A DRAFT" in str(card.get("purpose", "")):
        problems.append(
            f"{cid}: the purpose still says REGISTERED AS A DRAFT; finalise the card (min_tests, negative control, write_paths) and remove the sentence"
        )

    for n, chk in enumerate(card.get("checks", []) or [], start=1):
        parsed = split_pytest(chk.get("command", ""))
        if parsed is None:
            continue
        paths, collect_args = parsed
        existing: list[str] = []
        new: list[str] = []
        for p in paths:
            if (repo / p).exists():
                existing.append(p)
            elif _covered(p, write_paths):
                new.append(p)
            else:
                problems.append(
                    f"{cid} check {n}: names {p!r}, which does not exist at HEAD and is not in write_paths (a typo, or a file a later card creates)"
                )
        counted = collect_count(existing, collect_args) if existing else 0
        if counted is None:
            warnings.append(
                f"{cid} check {n}: could not count the collected tests; confirm min_tests {min_tests} by hand"
            )
            continue
        if not new and counted < min_tests:
            problems.append(
                f"{cid} check {n}: pytest collects {counted} tests but min_tests is {min_tests} (min_tests applies to EACH check)"
            )
        elif new and min_tests > counted + NEW_TEST_SLACK:
            warnings.append(
                f"{cid} check {n}: {counted} tests exist and the new file(s) {new} must add at least {min_tests - counted} to reach min_tests {min_tests}; confirm the card tells the worker so"
            )
        elif new and counted < min_tests:
            warnings.append(
                f"{cid} check {n}: {counted} existing + new tests must reach min_tests {min_tests}; the card's inputs should state the expected number of new tests"
            )

    for w in write_paths:
        if "*" in w or w.endswith("/"):
            continue
        if (
            not (repo / w).exists()
            and not re.match(r"^(tests|scripts|docs)/", w)
            and not w.endswith((".md", ".json", ".yaml", ".yml"))
        ):
            warnings.append(f"{cid}: write_paths entry {w!r} does not exist at HEAD (a new file, or a typo)")
    return problems, warnings


def venv_collect_count(
    repo: Path, python: str, run: Callable[..., tuple[int, str, str]]
) -> Callable[[list[str], list[str]], int | None]:
    """A `collect_count` that runs `pytest --collect-only -q` with the project's venv python (pytest 9 prints `path: N`)."""

    def count(paths: list[str], collect_args: list[str]) -> int | None:
        rc, out, _ = run(
            [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *paths, *collect_args],
            timeout=900,
        )
        if rc not in (0, 5):
            return None
        total = 0
        for ln in out.splitlines():
            m = re.match(r"^(\S.*?\.py): (\d+)$", ln.strip())
            if m:
                total += int(m.group(2))
            elif "::" in ln and not ln.startswith("ERROR"):
                total += 1
        return total

    return count
