"""Offline checks of the cells/rust authored-example sidecar (TC-501, REQ-G3-007).

``tests/fixtures/furnished/cells_rust/authored_examples.yaml`` holds exactly three minimal, labelled
examples written against the real public API of ``aspose_cells_foss_rust`` at the pinned commit. This
file proves they are well-formed, labelled, tied to the chart's pinned commit and use only symbols that
resolve in the pilot's extraction. Whether they compile or run is decided later by the ingestion
verifier, never here: nothing in this file imports the library, runs an example, or reaches a network.

The README's own Rust fragments fail because they use variables and types they never declare
(``sheet``, ``ChartType``); the pilot-specific tests below pin that the authored examples do not.

Every rule and constant is imported from ``foss_mcp.indexing.authored_examples`` rather than retyped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import (
    FORBIDDEN_CODE_PATTERNS,
    LANGUAGE_BY_PLATFORM,
    SIDECAR_NAME,
    load_authored_examples,
    validate_authored_examples,
)
from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    extract_candidate_examples,
    normalised_code_sha256,
)
from foss_mcp.normalization.citation import qualified_symbol_index_from_api_surface

PILOT = "cells_rust"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 3
EXAMPLE_NUMBERS = [1, 2, 3]

_USE_RE = re.compile(r"(?m)^[ \t]*use\s+([^;]*);")
_DECLARES_SHEET_RE = re.compile(r"\blet\s+(?:mut\s+)?sheet\b")
_SHEET_USE_RE = re.compile(r"(?<![A-Za-z0-9_\"'])sheet(?![A-Za-z0-9_\"'])")


def _pilot_library() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    for pilot in values["ingestion"]["pilots"]:
        if f"{pilot['family']}_{pilot['platform']}" == PILOT:
            return pilot["library"]
    raise AssertionError(f"{PILOT} is not a pilot in values.yaml")


def _api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _document() -> dict:
    return yaml.safe_load(SIDECAR.read_text(encoding="utf-8"))


def _example(number: int) -> dict:
    return _document()["examples"][number - 1]


def _crate_module() -> str:
    return _pilot_library()["crateName"].replace("-", "_")


def _candidates() -> tuple[CandidateExample, ...]:
    library = _pilot_library()
    return load_authored_examples(
        SIDECAR,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )


def _readme_titles() -> set[str]:
    page = build_chunks._load_furnished_page(PAGE)
    platform = _pilot_library()["platform"]
    titles = {candidate.title for candidate in extract_candidate_examples(page)}
    titles |= {candidate.title for candidate in extract_candidate_examples(page, platform=platform)}
    return titles


def test_the_sidecar_loads_and_yields_exactly_three_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"rust"}


def test_the_sidecar_header_matches_the_chart_pin() -> None:
    library = _pilot_library()
    document = _document()
    assert document["schema_version"] == 1
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
    assert document["language"] == LANGUAGE_BY_PLATFORM[library["platform"]]
    assert len(document["examples"]) == EXPECTED_EXAMPLES


def test_the_sidecar_has_no_validation_problems() -> None:
    library = _pilot_library()
    problems = validate_authored_examples(
        _document(),
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )
    assert problems == []


def test_the_example_titles_are_unique_and_each_has_at_least_two_symbols() -> None:
    examples = _document()["examples"]
    titles = [example["title"] for example in examples]
    assert len(set(titles)) == len(titles)
    for example in examples:
        assert len(example["symbols"]) >= 2, example["title"]


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_the_code_hash_matches(number: int) -> None:
    example = _example(number)
    assert example["code_sha256"] == normalised_code_sha256(example["code"])


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_the_label_comment_is_the_first_non_blank_line(number: int) -> None:
    code = _example(number)["code"]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first.startswith("//")
    assert "Authored example" in first


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_declared_symbol_resolves_and_appears_in_the_code(number: int) -> None:
    example = _example(number)
    index = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in example["symbols"]:
        assert index.declarations_for(symbol), f"{symbol} has no declaration in the api_surface"
        last_segment = symbol.rsplit(".", 1)[-1]
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(last_segment)}(?![A-Za-z0-9_])", example["code"]), (
            f"{symbol} is not used in the code"
        )


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_no_forbidden_pattern_matches(number: int) -> None:
    code = _example(number)["code"]
    hits = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert hits == []


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_the_title_differs_from_every_readme_candidate_title(number: int) -> None:
    assert _example(number)["title"] not in _readme_titles()


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_example_is_a_complete_program_with_fn_main(number: int) -> None:
    assert "fn main(" in _example(number)["code"]


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_library_use_line_imports_from_the_crate_itself(number: int) -> None:
    code = _example(number)["code"]
    crate = _crate_module()
    uses = [match.group(1).strip() for match in _USE_RE.finditer(code)]
    library_uses = [use for use in uses if use.startswith(crate)]
    assert library_uses, f"the example imports nothing from {crate}"
    # Every other `use` is standard library; a third-party crate would not resolve in the verifier's project.
    for use in uses:
        assert use.startswith((crate, "std::")), f"unexpected import: use {use};"
    # Every library symbol the example declares as its own is imported by name.
    imported = " ".join(library_uses)
    type_symbols = {symbol for symbol in _example(number)["symbols"] if "." not in symbol}
    for symbol in type_symbols:
        assert re.search(rf"\b{re.escape(symbol)}\b", imported), f"{symbol} is not imported from {crate}"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_no_example_has_the_readme_defects_an_undeclared_sheet_or_chart_type(number: int) -> None:
    code = _example(number)["code"]
    assert "ChartType" not in code
    body = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("//"))
    first_use = _SHEET_USE_RE.search(body)
    if first_use is not None:
        declaration = _DECLARES_SHEET_RE.search(body)
        assert declaration is not None, "`sheet` is used but never declared"
        assert declaration.start() < first_use.start(), "`sheet` is used before it is declared"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_no_example_depends_on_a_file_that_does_not_exist(number: int) -> None:
    code = _example(number)["code"]
    # A saved file must go to the temporary directory (and be removed) or stay in memory.
    for match in re.finditer(r"\.(?:save|load_xlsx|open)\(\s*(?:&)?\"", code):
        raise AssertionError(f"a literal relative path is saved or loaded: {match.group(0)!r}")
    if re.search(r"\.save\(|\.load_xlsx\(", code):
        assert "temp_dir()" in code
        assert "remove_file(" in code
