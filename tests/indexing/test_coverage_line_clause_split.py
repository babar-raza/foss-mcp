"""G3/TC-820 (REQ-G3-012, RD2-066 / RD2-178): every ``API-Coverage:`` count names what it counts.

``symbol_counts.types`` folds a library's package-level constants and variables in with its real
types, so the line used to say ``34 of 34 types`` for a Go package with 14 types and 20 constants.
It now says ``14 of 14 types; 20 of 20 constants/variables; 15 of 15 functions`` (a zero part is
omitted, and a bare ``N of N (100%)`` tautology is never written). pdf/cpp's two real type counts
(the README's 244 and this index's 263) are told apart by a project-authored ``known_hazards``
entry in its product manifest, the existing mechanism for text the upstream repository lacks.

Fully offline: synthetic fixtures through the real chunk builder, plus the committed fixtures,
which are only read. Nothing here relies on the committed generations having been regenerated.
"""

from __future__ import annotations

import functools
import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.indexing.chunk_builder import CONSTANT_KINDS, _coverage_line, build_chunks_from_api_surface
from foss_mcp.mcp.tools.coverage_note import ApiCoverage, coverage_note_from_text, parse_api_coverage
from foss_mcp.mcp.tools.get_product_reference import known_hazards_from_manifest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
PILOT_KEYS = sorted(path.parent.name for path in FIXTURES.glob("*/api_surface.json"))
COVERAGE_LINE = re.compile(r"^API-Coverage: (.*)$", re.MULTILINE)
CLAUSE = re.compile(r"^(\d+) of (\d+) (types|constants/variables|functions)$")


def _fixture(types: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types, **extra}


def _go_with_constants(**extra: Any) -> dict[str, object]:
    types: list[dict[str, object]] = [
        {"name": "Alpha", "kind": "type_spec", "file": "a.go"},
        {"name": "Beta", "kind": "type_spec", "file": "b.go"},
        {"name": "MaxItems", "kind": "constant", "file": "a.go"},
        {"name": "MinItems", "kind": "constant", "file": "a.go"},
        {"name": "Default", "kind": "variable", "file": "b.go"},
        {"name": "Run", "kind": "function", "file": "a.go", "params": [], "return_type": ""},
    ]
    return _fixture(types, type_count=6, symbol_counts={"types": 5, "functions": 1}, **extra)


def _lines(fixture: Mapping[str, object], *, max_types: int = 10_000) -> set[str]:
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=max_types)
    return {match.group(1) for chunk in chunks for match in COVERAGE_LINE.finditer(chunk.text)}


def _only(lines: set[str]) -> str:
    assert len(lines) == 1, lines
    return next(iter(lines))


# ------------------------------------------------------------ the clause split, synthetic input


def test_constants_and_variables_are_counted_apart_from_types() -> None:
    assert _only(_lines(_go_with_constants())) == "2 of 2 types; 3 of 3 constants/variables; 1 of 1 functions"


def test_the_split_reaches_every_symbol_chunk_of_the_generation() -> None:
    chunks = build_chunks_from_api_surface(_go_with_constants(), title="t", max_types=10_000)
    symbol_lines = [COVERAGE_LINE.findall(chunk.text) for chunk in chunks]
    assert len(symbol_lines) == 6  # 2 types + 3 constants/variables + 1 function
    assert all(len(found) == 1 for found in symbol_lines)


def test_a_fixture_without_constants_renders_exactly_as_before() -> None:
    fixture = _fixture(
        [
            {"name": "Alpha", "kind": "type_spec", "file": "a.go"},
            {"name": "Run", "kind": "function", "file": "a.go", "params": [], "return_type": ""},
        ],
        type_count=2,
        symbol_counts={"types": 1, "functions": 1},
    )
    assert _only(_lines(fixture)) == "1 of 1 types; 1 of 1 functions"


def test_a_zero_part_is_omitted() -> None:
    no_functions = _go_with_constants()
    no_functions["types"] = [e for e in no_functions["types"] if e["kind"] != "function"]  # type: ignore[attr-defined, union-attr]
    no_functions["symbol_counts"] = {"types": 5, "functions": 0}
    assert _only(_lines(no_functions)) == "2 of 2 types; 3 of 3 constants/variables"


def test_a_library_of_only_constants_states_no_zero_of_zero_types_clause() -> None:
    fixture = _fixture(
        [
            {"name": "A", "kind": "constant", "file": "a.go"},
            {"name": "B", "kind": "variable", "file": "a.go"},
        ],
        type_count=2,
        symbol_counts={"types": 2, "functions": 0},
    )
    line = _only(_lines(fixture))
    assert line == "2 of 2 constants/variables"
    assert "0 of 0" not in line


def test_the_kept_numerators_follow_the_slot_cap_per_kind() -> None:
    # the cap keeps the first 2 entries in fixture order: both types, no constant/variable, no function
    line = _only(_lines(_go_with_constants(), max_types=2))
    assert line == "2 of 2 types; 0 of 3 constants/variables; 0 of 1 functions"
    # the cap keeps the first 3 entries: both types and the first constant
    line = _only(_lines(_go_with_constants(), max_types=3))
    assert line == "2 of 2 types; 1 of 3 constants/variables; 0 of 1 functions"


def test_the_types_clause_never_counts_a_constant_or_variable_kind() -> None:
    fixture = _go_with_constants()
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=10_000)
    kinds = Counter(chunk.text.splitlines()[1] for chunk in chunks)
    assert kinds["Kind: constant"] + kinds["Kind: variable"] == 3
    coverage = parse_api_coverage(f"API-Coverage: {_only(_lines(fixture))}")
    assert coverage is not None
    assert (coverage.indexed, coverage.total) == (2, 2)  # the two type_spec entries only
    assert coverage.constants == (3, 3)


def test_test_source_constants_are_not_counted() -> None:
    fixture = _go_with_constants()
    fixture["types"] = [*fixture["types"], {"name": "T", "kind": "constant", "file": "a_test.go"}]  # type: ignore[misc]
    assert _only(_lines(fixture)) == "2 of 2 types; 3 of 3 constants/variables; 1 of 1 functions"


def test_an_entry_list_that_disagrees_with_symbol_counts_is_not_split_on_a_guess() -> None:
    fixture = _go_with_constants()
    fixture["symbol_counts"] = {"types": 2, "functions": 1}  # fewer than the 3 constants/variables
    assert _only(_lines(fixture)) == "5 of 2 types; 1 of 1 functions"


def test_the_legacy_line_of_a_fixture_without_symbol_counts_is_unchanged() -> None:
    fixture = _go_with_constants()
    del fixture["symbol_counts"]
    assert _only(_lines(fixture)) == "6 of 6 types"


def test_the_line_never_degrades_into_a_percentage_tautology() -> None:
    for line in _lines(_go_with_constants()) | _lines(_go_with_constants(), max_types=2):
        assert "%" not in line
        assert not re.fullmatch(r"\d+ of \d+", line.strip())
        for clause in line.split("; "):
            assert CLAUSE.match(clause), clause


def test_coverage_line_direct_call_with_a_minimal_fixture() -> None:
    entries: list[Mapping[str, object]] = [{"name": "A", "kind": "constant", "file": "a.go"}]
    fixture = {"types": list(entries), "symbol_counts": {"types": 1, "functions": 0}}
    assert _coverage_line(fixture, entries) == "1 of 1 constants/variables"
    # without an entry list there is nothing to split on
    assert _coverage_line({"symbol_counts": {"types": 1}}, entries) == "1 of 1 types"


# ------------------------------------------------------------ the served caveat parses the new clause


def test_parse_reads_the_constants_clause() -> None:
    parsed = parse_api_coverage(
        "FQN: A\nAPI-Coverage: 14 of 14 types; 20 of 20 constants/variables; 15 of 15 functions"
    )
    assert parsed == ApiCoverage(14, 14, (15, 15), None, (20, 20))


def test_parse_reads_a_line_with_only_a_constants_clause() -> None:
    parsed = parse_api_coverage("API-Coverage: 2 of 2 constants/variables")
    assert parsed == ApiCoverage(0, 0, None, None, (2, 2))


def test_a_complete_split_line_states_no_caveat() -> None:
    assert (
        coverage_note_from_text(
            "API-Coverage: 14 of 14 types; 20 of 20 constants/variables; 15 of 15 functions"
        )
        is None
    )


def test_a_partial_constants_clause_is_named_in_the_caveat() -> None:
    note = coverage_note_from_text(
        "API-Coverage: 226 of 259 types; 19 of 26 constants/variables; 55 of 98 functions"
    )
    assert note == (
        "the API index holds 226 of 259 types, 19 of 26 constants/variables and 55 of 98 functions; "
        "a symbol missing from it may still exist upstream"
    )


def test_only_partial_constants_is_a_caveat_that_names_them() -> None:
    note = coverage_note_from_text("API-Coverage: 5 of 5 types; 0 of 8 constants/variables")
    assert note == (
        "the API index holds 5 of 5 types and 0 of 8 constants/variables; "
        "a symbol missing from it may still exist upstream"
    )


def test_the_old_sentences_are_unchanged_when_there_is_no_constants_clause() -> None:
    assert (
        coverage_note_from_text("API-Coverage: 300 of 901 types")
        == "the API index holds 300 of 901 types; a type missing from it may still exist upstream"
    )
    assert coverage_note_from_text("API-Coverage: 5 of 5 types; 3 of 4 functions") == (
        "the API index holds 5 of 5 types and 3 of 4 functions"
    )


# ------------------------------------------------------------ every committed pilot


@functools.cache
def _real(pilot: str) -> dict[str, Any]:
    return json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


@functools.cache
def _served_line(pilot: str) -> str:
    return _only(_lines(_real(pilot), max_types=300))


def _clauses(line: str) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    for clause in line.split("; "):
        match = CLAUSE.match(clause)
        if match is not None:
            found[match.group(3)] = (int(match.group(1)), int(match.group(2)))
    return found


def test_the_fixture_set_is_the_whole_fleet() -> None:
    assert len(PILOT_KEYS) >= 32


@pytest.mark.parametrize("pilot", PILOT_KEYS)
def test_every_pilots_line_names_what_each_count_counts(pilot: str) -> None:
    fixture = _real(pilot)
    line = _served_line(pilot)
    clauses = _clauses(line)
    counts = fixture.get("symbol_counts")
    if not isinstance(counts, Mapping):
        pytest.skip("a fixture without symbol_counts renders the legacy line")
    constants_total = sum(1 for entry in fixture["types"] if entry.get("kind") in CONSTANT_KINDS)
    types_total = counts["types"] - constants_total
    # the types clause counts types only; the constants/variables clause exists exactly when there are some
    assert (clauses["types"][1] if "types" in clauses else 0) == types_total
    assert ("constants/variables" in clauses) is (constants_total > 0)
    if constants_total:
        assert clauses["constants/variables"][1] == constants_total
    # the three parts add up to the fixture's own folded count: nothing lost, nothing counted twice
    folded = sum(total for name, (_, total) in clauses.items() if name != "functions")
    assert folded == counts["types"]
    assert ("functions" in clauses) is (counts.get("functions", 0) > 0)
    # a part that is entirely kept is stated as N of N, never a bare percentage or a lone number
    assert "%" not in line


def test_exactly_the_pilots_with_constants_or_variables_changed_their_wording() -> None:
    changed = {pilot for pilot in PILOT_KEYS if "constants/variables" in _served_line(pilot)}
    assert changed == {"cells_go", "cells_python", "pdf_go", "pdf_typescript"}


def test_cells_go_states_14_types_20_constants_and_15_functions() -> None:
    assert _served_line("cells_go") == "14 of 14 types; 20 of 20 constants/variables; 15 of 15 functions"


# ------------------------------------------------------------ pdf/cpp: the count definition (RD2-178)


def _pdf_cpp_hazards() -> tuple[str, ...]:
    manifest = yaml.safe_load((REPO / "config" / "products" / "pdf" / "cpp.yaml").read_text(encoding="utf-8"))
    return tuple(hazard.text for hazard in known_hazards_from_manifest(manifest))


def test_pdf_cpp_carries_one_count_definition_hazard() -> None:
    texts = [text for text in _pdf_cpp_hazards() if "244" in text and "263" in text]
    assert len(texts) == 1
    text = texts[0]
    assert "README" in text and "developer_guide" in text
    assert "19 free functions" in text and "not types" in text


def test_the_count_definition_numbers_are_the_real_ones() -> None:
    fixture = _real("pdf_cpp")
    kinds = Counter(entry["kind"] for entry in fixture["types"])
    assert kinds["class_specifier"] + kinds["struct_specifier"] + kinds["enum_specifier"] == 263
    assert (kinds["class_specifier"], kinds["struct_specifier"], kinds["enum_specifier"]) == (187, 4, 72)
    assert kinds["function"] == 19
    assert _served_line("pdf_cpp") == "263 of 263 types; 19 of 19 functions"
    # the README's own module tables list 244 distinct names, as the hazard says
    readme = (FIXTURES / "round3_readmes" / "pdf_cpp" / "README.md").read_text(encoding="utf-8")
    api = readme[readme.index("## API Reference") :]
    api = api[: api.index("\n## ", 5)]
    names = set(re.findall(r"^\|\s*`([^`]+)`\s*\|", api, re.MULTILINE)) - {"Class"}
    assert "exposes 244 public types across 5 modules" in api
    assert len(names) == 244


def test_the_count_definition_is_the_last_top_level_key_of_the_manifest() -> None:
    top_level = [
        line.split(":")[0]
        for line in (REPO / "config/products/pdf/cpp.yaml").read_text(encoding="utf-8").splitlines()
        if line and not line[0].isspace() and not line.startswith("#")
    ]
    assert top_level[-1] == "known_hazards"
