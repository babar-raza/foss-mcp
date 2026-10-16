"""TC-520 (REQ-G3-005, REQ-G3-008): the coverage line of every symbol chunk.

``API-Coverage:`` separates types from functions and, when the extraction recorded source files it
could not parse, says how many. A fixture without the new keys renders exactly as before. Fully
offline: synthetic fixtures plus the committed ones, which are only read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing.chunk_builder import (
    PARSE_FAILURE_NOTE_THRESHOLD,
    build_chunks_from_api_surface,
    parse_failures_over_threshold,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
LEGACY_LINE = re.compile(r"^API-Coverage:\s*(\d+)\s+of\s+(\d+)\s+types\b", re.MULTILINE)


def _fixture(types: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types, **extra}


def _texts(fixture: Mapping[str, object]) -> list[str]:
    return [chunk.text for chunk in build_chunks_from_api_surface(fixture, title="t", max_types=10_000)]


def _real(pilot: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / pilot / "api_surface.json").read_text(encoding="utf-8"))


def _last_lines(fixture: Mapping[str, object]) -> set[str]:
    return {text.splitlines()[-1] for text in _texts(fixture)}


def _go_like(extra: dict[str, Any]) -> dict[str, object]:
    types: list[dict[str, object]] = [
        {"name": "Alpha", "kind": "type_spec", "file": "a.go"},
        {"name": "Beta", "kind": "type_spec", "file": "b.go"},
        {"name": "Run", "kind": "function", "file": "a.go", "params": [], "return_type": ""},
    ]
    return _fixture(types, type_count=3, **extra)


def _failures(engine: str, failed: int, total: int) -> dict[str, object]:
    return {"engine": engine, "failed": failed, "total": total, "examples": ["src/p/m.py:2: bad"]}


# ---------------------------------------------------------------- the threshold


@pytest.mark.parametrize(
    ("parse_failures", "expected"),
    [
        ({"engine": "ast", "failed": 11, "total": 100, "examples": []}, True),
        ({"engine": "ast", "failed": 10, "total": 100, "examples": []}, False),
        ({"engine": "tree-sitter", "failed": 20, "total": 100, "examples": []}, False),
        ({"engine": "tree-sitter", "failed": 30, "total": 100, "examples": []}, True),
        ({"engine": "ast", "failed": 0, "total": 0, "examples": []}, False),
        ({"engine": "ast", "failed": "many", "total": 10}, False),
        ({"engine": "ast", "failed": True, "total": 10}, False),
        ({"engine": "ast", "failed": 5}, False),
        ({"engine": "regex", "failed": 90, "total": 100}, False),
        ({"failed": 90, "total": 100}, False),
        ("35 of 45", False),
        (None, False),
    ],
)
def test_threshold_rules(parse_failures: object, expected: bool) -> None:
    assert parse_failures_over_threshold(parse_failures) is expected


def test_threshold_constant_is_the_documented_pair() -> None:
    assert PARSE_FAILURE_NOTE_THRESHOLD == {"ast": 0.10, "tree-sitter": 0.25}


# ---------------------------------------------------------------- the legacy rendering stays


def test_legacy_line_is_unchanged_for_fixtures_without_the_new_keys() -> None:
    fixture = _fixture(
        [
            {"name": "Real", "kind": "function", "file": "real.go", "line": 1},
            {"name": "Helper", "kind": "function", "file": "real_test.go", "line": 2},
            {"name": "NoFile", "kind": "function"},
        ],
        type_count=3,
    )
    assert _last_lines(fixture) == {"API-Coverage: 2 of 3 types"}


def test_committed_pdf_net_fixture_renders_901_of_901_types() -> None:
    # re-extracted by TC-530: "API-Coverage: 300 of 901 types" -> the complete surface
    assert _last_lines(_real("pdf_net")) == {"API-Coverage: 901 of 901 types"}


def test_no_coverage_line_without_a_type_count_or_symbol_counts() -> None:
    assert not any("API-Coverage" in text for text in _texts(_fixture([{"name": "A", "kind": "class"}])))


# ---------------------------------------------------------------- types and functions


def test_types_and_functions_clauses_on_a_go_like_fixture() -> None:
    fixture = _go_like({"symbol_counts": {"types": 259, "functions": 2054}})
    assert _last_lines(fixture) == {"API-Coverage: 2 of 259 types; 1 of 2054 functions"}


def test_functions_clause_is_omitted_when_there_are_no_functions() -> None:
    fixture = _fixture(
        [{"name": "Alpha", "kind": "class_specifier", "file": "a.hpp"}],
        type_count=9,
        symbol_counts={"types": 9, "functions": 0},
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 9 types"}


def test_symbol_counts_without_types_fall_back_to_the_legacy_types_clause() -> None:
    fixture = _go_like({"symbol_counts": {"functions": 7}})
    assert _last_lines(fixture) == {"API-Coverage: 3 of 3 types; 1 of 7 functions"}


# ---------------------------------------------------------------- unparsed source files


def test_parse_failure_clause_states_the_files_over_the_threshold() -> None:
    fixture = _fixture(
        [{"name": "A", "kind": "class", "file": "a.py"}],
        type_count=4,
        symbol_counts={"types": 4, "functions": 0},
        parse_failures=_failures("ast", 35, 45),
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 4 types; 35 of 45 source files could not be parsed"}


def test_no_failure_clause_at_or_below_the_threshold() -> None:
    fixture = _fixture(
        [{"name": "A", "kind": "class", "file": "a.py"}],
        type_count=1,
        symbol_counts={"types": 1, "functions": 0},
        parse_failures=_failures("ast", 10, 100),
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 1 types"}
    noisy = {**fixture, "parse_failures": _failures("tree-sitter", 20, 100)}
    assert _last_lines(noisy) == {"API-Coverage: 1 of 1 types"}


def test_syntax_error_entries_without_parse_failures_say_at_least() -> None:
    fixture = _fixture(
        [{"name": "A", "kind": "class", "file": "a.py"}],
        type_count=1,
        unresolved=[
            "pkg.a:3:syntax-error:src/pkg/a.py:bad",
            "pkg.a:9:syntax-error:src/pkg/a.py:worse",  # the same file twice: one file
            "pkg.b:1:syntax-error:src/pkg/b.py:bad",
            "pkg:1:unresolved-reexport:pkg.a.X",
        ],
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 1 types; at least 2 source files could not be parsed"}


def test_parse_failures_key_wins_over_the_unresolved_fallback() -> None:
    fixture = _fixture(
        [{"name": "A", "kind": "class", "file": "a.py"}],
        type_count=1,
        unresolved=["pkg.a:3:syntax-error:src/pkg/a.py:bad"],
        parse_failures=_failures("ast", 1, 100),
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 1 types"}


def test_every_clause_in_order() -> None:
    fixture = _go_like(
        {"symbol_counts": {"types": 10, "functions": 5}, "parse_failures": _failures("tree-sitter", 40, 100)}
    )
    assert _last_lines(fixture) == {
        "API-Coverage: 2 of 10 types; 1 of 5 functions; 40 of 100 source files could not be parsed"
    }


# ---------------------------------------------------------------- the committed tex_python fixture


def test_committed_tex_python_fixture_states_the_unparsed_files_in_every_chunk() -> None:
    fixture = _real("tex_python")
    texts = _texts(fixture)
    assert len(texts) == 4
    expected = "API-Coverage: 4 of 4 types; at least 2 source files could not be parsed"
    assert all(text.splitlines()[-1] == expected for text in texts)


def test_the_line_still_matches_the_legacy_regex_with_the_types_clause_first() -> None:
    fixtures = [
        _go_like({"symbol_counts": {"types": 259, "functions": 2054}}),
        _real("tex_python"),
        _fixture(
            [{"name": "A", "kind": "class", "file": "a.py"}],
            type_count=4,
            parse_failures=_failures("ast", 35, 45),
        ),
    ]
    for fixture in fixtures:
        for text in _texts(fixture):
            match = LEGACY_LINE.search(text)
            assert match is not None, text
            assert text.splitlines()[-1].startswith(match.group(0))


def test_every_symbol_chunk_carries_the_clause_not_only_the_first() -> None:
    fixture = _fixture(
        [{"name": f"T{index}", "kind": "class", "file": f"t{index}.py"} for index in range(6)],
        type_count=6,
        parse_failures=_failures("ast", 5, 10),
    )
    texts = _texts(fixture)
    assert len(texts) == 6
    assert all(text.splitlines()[-1].endswith("5 of 10 source files could not be parsed") for text in texts)


def test_the_line_stays_the_last_line_before_the_source_commit_is_appended() -> None:
    fixture = _real("tex_python")
    for text in _texts(fixture):
        appended = f"{text}\nSource-Commit: {fixture['source_commit']}"
        assert appended.splitlines()[-2].startswith("API-Coverage: 4 of 4 types")
