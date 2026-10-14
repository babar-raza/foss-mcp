"""Offline checks of the jmap/python authored-example sidecar (TC-464, REQ-G3-007).

``tests/fixtures/furnished/jmap_python/authored_examples.yaml`` holds three minimal, fully offline
examples written against the real public API of ``aspose_jmap_foss`` at the pinned commit. This file
proves they are well-formed, labelled and use only symbols that resolve in the pilot's extraction.
Whether they compile or run against the library is decided later by the ingestion verifier, never
here: nothing in this file imports the library, runs an example, or reaches a network.

Every rule and constant is imported from ``foss_mcp.indexing.authored_examples`` rather than retyped.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import (
    FORBIDDEN_CODE_PATTERNS,
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

PILOT = "jmap_python"
LIBRARY_PACKAGE = "aspose_jmap_foss"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"


def _chart_library() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    for pilot in values["ingestion"]["pilots"]:
        if f"{pilot['family']}_{pilot['platform']}" == PILOT:
            return pilot["library"]
    raise AssertionError(f"{PILOT} is not a pilot in {CHART_VALUES}")


def _api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _document() -> dict:
    return yaml.safe_load(SIDECAR.read_text(encoding="utf-8"))


def _candidates() -> tuple[CandidateExample, ...]:
    library = _chart_library()
    return load_authored_examples(
        SIDECAR,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )


def _readme_titles() -> set[str]:
    page = build_chunks._load_furnished_page(PAGE)
    platform = _chart_library()["platform"]
    titles = {candidate.title for candidate in extract_candidate_examples(page)}
    titles |= {candidate.title for candidate in extract_candidate_examples(page, platform=platform)}
    return titles


EXAMPLE_NUMBERS = [1, 2, 3]


def _example(number: int) -> dict:
    return _document()["examples"][number - 1]


def test_the_sidecar_loads_and_yields_exactly_three_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == 3
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"python"}


def test_the_sidecar_header_matches_the_chart_pin() -> None:
    library = _chart_library()
    document = _document()
    assert document["schema_version"] == 1
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
    assert document["language"] == "python"
    assert len(document["examples"]) == 3


def test_the_sidecar_has_no_validation_problems() -> None:
    library = _chart_library()
    problems = validate_authored_examples(
        _document(),
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )
    assert problems == []


def test_the_example_titles_are_unique_and_at_least_two_symbols_each() -> None:
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
    assert first.startswith("#")
    assert "Authored example" in first


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_declared_symbol_resolves_and_appears_in_the_code(number: int) -> None:
    example = _example(number)
    index = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in example["symbols"]:
        assert index.declarations_for(symbol), f"{symbol} has no declaration in the api_surface"
        last_segment = symbol.rsplit(".", 1)[-1]
        assert last_segment in example["code"], f"{symbol} is not used in the code"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_no_forbidden_pattern_matches(number: int) -> None:
    code = _example(number)["code"]
    hits = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert hits == []


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_the_title_differs_from_every_readme_candidate_title(number: int) -> None:
    assert _example(number)["title"] not in _readme_titles()


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_the_code_compiles_as_python(number: int) -> None:
    example = _example(number)
    compile(example["code"], example["title"], "exec")


def _imported_from_library(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == LIBRARY_PACKAGE or node.module.startswith(LIBRARY_PACKAGE + "."):
                names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_example_imports_from_the_library_itself(number: int) -> None:
    tree = ast.parse(_example(number)["code"])
    imported = _imported_from_library(tree)
    assert imported, "the example imports nothing from aspose_jmap_foss"
    symbols = {symbol.split(".")[0] for symbol in _example(number)["symbols"]}
    assert symbols <= imported, f"symbols not imported from the library: {sorted(symbols - imported)}"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_a_client_is_always_given_a_transport_subclass_defined_in_the_same_code(number: int) -> None:
    tree = ast.parse(_example(number)["code"])
    constructs_client = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "JmapClient"
        for node in ast.walk(tree)
    )
    if not constructs_client:
        return
    transport_classes = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "JmapTransport" for base in node.bases)
    }
    assert transport_classes, "JmapClient is constructed but no JmapTransport subclass is defined"
    passed = {
        keyword.value.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "JmapClientOptions"
        for keyword in node.keywords
        if keyword.arg == "transport"
        and isinstance(keyword.value, ast.Call)
        and isinstance(keyword.value.func, ast.Name)
    }
    assert passed & transport_classes, "the subclass is not passed as the transport option"


def test_at_least_one_example_exercises_the_custom_transport_path() -> None:
    codes = [example["code"] for example in _document()["examples"]]
    assert any("JmapClient(" in code and "transport=" in code for code in codes)
