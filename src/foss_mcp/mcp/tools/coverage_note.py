"""The one place that turns a symbol chunk's ``API-Coverage:`` line into a caveat a caller reads.

A symbol chunk states how much of the library's API the index holds (``foss_mcp.indexing.
chunk_builder``): ``API-Coverage: 300 of 901 types``, optionally followed by ``; 45 of 2054
functions`` and ``; 35 of 45 source files could not be parsed``. ``get_symbol`` (a miss) and
``report_index_freshness`` both state the caveat through ``coverage_note_from_text`` so the two can
never disagree, and the sibling serving cards import the same helper to put it on other misses.

Pure: no I/O, no store access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_API_COVERAGE_LINE = re.compile(r"^API-Coverage:\s*(\d+)\s+of\s+(\d+)\s+types\b(.*)$", re.MULTILINE)
_FUNCTIONS_CLAUSE = re.compile(r"^(\d+)\s+of\s+(\d+)\s+functions$")
_UNPARSED_CLAUSE = re.compile(r"^(?:at least\s+\d+|\d+\s+of\s+\d+)\s+source files could not be parsed$")

_UPSTREAM_TAIL = "a type missing from it may still exist upstream"


@dataclass(frozen=True)
class ApiCoverage:
    """What one ``API-Coverage:`` line states.

    ``indexed``/``total`` are the types; ``functions`` is ``(indexed, total)`` when the line has a
    functions clause; ``unparsed`` is the clause text (``"35 of 45 source files could not be
    parsed"`` or ``"at least 2 source files could not be parsed"``) when the line states failures.
    """

    indexed: int
    total: int
    functions: tuple[int, int] | None = None
    unparsed: str | None = None


def parse_api_coverage(text: str) -> ApiCoverage | None:
    """The FIRST ``API-Coverage:`` line of a chunk text, parsed; ``None`` when there is none."""
    match = _API_COVERAGE_LINE.search(text)
    if match is None:
        return None
    functions: tuple[int, int] | None = None
    unparsed: str | None = None
    for clause in match.group(3).split(";"):
        clause = " ".join(clause.split())
        functions_match = _FUNCTIONS_CLAUSE.match(clause)
        if functions_match is not None:
            functions = (int(functions_match.group(1)), int(functions_match.group(2)))
        elif _UNPARSED_CLAUSE.match(clause):
            unparsed = clause
    return ApiCoverage(int(match.group(1)), int(match.group(2)), functions, unparsed)


def _describe(coverage: ApiCoverage) -> list[str]:
    sentences: list[str] = []
    functions = coverage.functions
    partial_functions = functions if functions is not None and functions[0] < functions[1] else None
    types_partial = coverage.indexed < coverage.total
    if partial_functions is not None:
        held = (
            f"the API index holds {coverage.indexed} of {coverage.total} types and "
            f"{partial_functions[0]} of {partial_functions[1]} functions"
        )
        sentences.append(
            f"{held}; a symbol missing from it may still exist upstream" if types_partial else held
        )
    elif types_partial:
        sentences.append(
            f"the API index holds {coverage.indexed} of {coverage.total} types; {_UPSTREAM_TAIL}"
        )
    if coverage.unparsed is not None:
        sentences.append(
            f"{coverage.unparsed}, so the API index is incomplete and a type missing from it may "
            "still exist upstream (the Extraction notice documentation chunk lists the files)"
        )
    return sentences


def coverage_note_from_text(text: str) -> str | None:
    """The partial-coverage caveat the first ``API-Coverage:`` line of *text* states.

    ``None`` when the line is absent, or the index holds every type and function and no source
    file failed to parse. The sentence for a line that only counts types is the legacy one,
    unchanged: ``the API index holds P of T types; a type missing from it may still exist
    upstream``. Clauses are joined by "; ".
    """
    coverage = parse_api_coverage(text)
    if coverage is None:
        return None
    sentences = _describe(coverage)
    return "; ".join(sentences) if sentences else None
