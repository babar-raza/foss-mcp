"""The reusable "identifiers the docs name must resolve" check (TC-521).

A pilot's README (the furnished ``_index.md`` page) names the API an agent is told to use. If a
name the README presents in backticks is absent from the pilot's committed ``api_surface.json``,
an agent that follows the README cannot find it with ``search_symbols`` / ``get_symbol`` - the
65-task blind run found 3 of 5 jmap/nodejs tasks undiscoverable for exactly this reason.

This module is a helper, not a test module (nothing here is collected). The permanent test is
``tests/extraction/test_docs_identifiers_resolve.py``; later re-extraction cards import the
functions below::

    from tests.extraction.docs_identifiers import compare, unresolved

The gaps that exist TODAY are recorded in ``tests/fixtures/readme_identifier_allowlist.json``.
The allowlist is exact in both directions (the stale-allowlist rule): a name the README uses that
the fixture does not hold must be listed, and a listed name that now resolves (or is no longer
named) must be removed. Regenerate the file deterministically, after regenerating a fixture, with::

    python -m tests.extraction.docs_identifiers --write
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ALLOWLIST_PATH = FIXTURES / "readme_identifier_allowlist.json"

#: Every pilot with a committed extraction artifact, sorted.
PILOTS: list[str] = sorted(
    path.parent.name for path in FIXTURES.glob("*/api_surface.json") if path.parent.name != "furnished"
)

# A backtick pair around at most 80 characters on one line. Deliberately no look-around: the content
# of a double-backtick span (``io.BytesIO``) is read as its inner span, which is what the card's
# measurement (2295 unresolved identifiers over the committed fixtures) was taken with.
_SPAN_RE = re.compile(r"`([^`\n]{1,80})`")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*(?:(?:\.|::)[A-Za-z_]\w*)*(?:\(\))?$")
_SEGMENT_SPLIT_RE = re.compile(r"\.|::")
_LOWER_TO_UPPER_RE = re.compile(r"[a-z][A-Z]")
_CAPITALISED_WORDS_RE = re.compile(r"[A-Z][a-z]+[A-Z]")


def is_api_shaped(token: str) -> bool:
    """True when *token* (a backtick span that is already identifier-shaped) reads as API.

    API-shaped: it ends with ``()``, or contains ``.`` or ``::``, or its last segment has a
    lower-to-upper case transition or an underscore, or two capitalised words (``PdfDocument``).
    A bare lower-case word (``pip``, ``toml``) is prose or a tool name, not an identifier to find.
    """
    if token.endswith("()") or "." in token or "::" in token:
        return True
    last = _SEGMENT_SPLIT_RE.split(token.removesuffix("()"))[-1]
    return bool(_LOWER_TO_UPPER_RE.search(last) or "_" in last or _CAPITALISED_WORDS_RE.search(last))


def docs_identifiers(pilot: str) -> set[str]:
    """The last segment of every API-shaped single-backtick span of the pilot's README page.

    Fenced code blocks are dropped; a span counts when it is at most 80 characters on one line and
    is a (possibly dotted or ``::``-qualified) identifier with an optional trailing ``()``.
    """
    page = FIXTURES / "furnished" / pilot / "pages" / "_index.md"
    if not page.is_file():
        return set()
    found: set[str] = set()
    for text in run_extraction._page_texts(page):
        prose = run_extraction._FENCE_RE.sub(" ", text)
        for span in _SPAN_RE.finditer(prose):
            token = span.group(1)
            if _IDENTIFIER_RE.match(token) and is_api_shaped(token):
                found.add(_SEGMENT_SPLIT_RE.split(token.removesuffix("()"))[-1])
    return found


def load_fixture(pilot: str) -> dict[str, Any]:
    """The pilot's committed ``api_surface.json``."""
    loaded = json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def known_names_of(fixture: Mapping[str, Any]) -> set[str]:
    """Every name an extraction artifact holds: type names, the last ``.``/``::``/``/`` segment of
    each ``class_import``, and the name of every method, property and enum member."""
    names: set[str] = set()
    for entry in fixture.get("types") or []:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("name"):
            names.add(str(entry["name"]))
        class_import = entry.get("class_import")
        if class_import:
            names.add(re.split(r"\.|::|/", str(class_import))[-1])
        for key in ("methods", "properties", "enum_members"):
            for member in entry.get(key) or []:
                if isinstance(member, Mapping) and member.get("name"):
                    names.add(str(member["name"]))
    return names


def known_names(pilot: str) -> set[str]:
    """``known_names_of`` the pilot's committed fixture."""
    return known_names_of(load_fixture(pilot))


def unresolved(pilot: str) -> set[str]:
    """The identifiers the pilot's README names that its committed fixture does not hold."""
    return docs_identifiers(pilot) - known_names(pilot)


def load_allowlist() -> dict[str, list[str]]:
    """The committed allowlist, ``{pilot: [identifier, ...]}``."""
    loaded = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    return {str(pilot): [str(name) for name in names] for pilot, names in loaded.items()}


def compare_sets(unresolved_names: Iterable[str], allowed: Iterable[str]) -> tuple[set[str], set[str]]:
    """``(new gaps not on the allowlist, stale allowlist entries)`` - the pure comparison.

    A stale entry is a listed name that resolves now or that the README no longer names.
    """
    current, listed = set(unresolved_names), set(allowed)
    return current - listed, listed - current


def compare(pilot: str) -> tuple[set[str], set[str]]:
    """``compare_sets`` of the pilot's current gaps against its allowlist entry."""
    return compare_sets(unresolved(pilot), load_allowlist().get(pilot, []))


def assert_docs_identifiers_resolve(pilots: Iterable[str] | None = None) -> None:
    """Raise ``AssertionError`` naming every pilot whose gaps differ from the allowlist."""
    allowlist = load_allowlist()
    problems: list[str] = []
    for pilot in sorted(PILOTS if pilots is None else pilots):
        new_gaps, stale = compare_sets(unresolved(pilot), allowlist.get(pilot, []))
        if new_gaps:
            problems.append(
                f"{pilot}: the README names {sorted(new_gaps)} which the fixture does not hold - "
                "fix the extraction, or add the name to the allowlist with a reason"
            )
        if stale:
            problems.append(
                f"{pilot}: {sorted(stale)} are on the allowlist but now resolve or are no longer named - "
                "REMOVE them (python -m tests.extraction.docs_identifiers --write)"
            )
    if problems:
        raise AssertionError(
            "docs-referenced identifiers do not match the allowlist:\n" + "\n".join(problems)
        )


def render_allowlist(pilots: Iterable[str] | None = None) -> str:
    """The allowlist file's exact text: sorted keys, sorted lists, indent 2, trailing newline,
    only pilots with a non-empty set."""
    data = {
        pilot: sorted(names)
        for pilot in sorted(PILOTS if pilots is None else pilots)
        if (names := unresolved(pilot))
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def write_allowlist(path: Path | None = None) -> Path:
    """Write ``render_allowlist()`` to *path* (the committed allowlist by default), UTF-8, LF."""
    target = ALLOWLIST_PATH if path is None else Path(path)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_allowlist())
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--write", action="store_true", help="regenerate the committed allowlist")
    args = parser.parse_args(argv)
    if args.write:
        target = write_allowlist()
        print(f"wrote {target}")
        return 0
    try:
        assert_docs_identifiers_resolve()
    except AssertionError as error:
        print(error)
        return 1
    print("docs-referenced identifiers match the allowlist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
