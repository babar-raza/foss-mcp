"""Offline checks of the pdf/go authored-example sidecar (TC-735, REQ-G3-014).

``tests/fixtures/furnished/pdf_go/authored_examples.yaml`` holds up to five real, individually
hand-verified examples adapted from real programs in the upstream ``_examples/`` directory of
``aspose-pdf-foss/Aspose-PDF-FOSS-for-Go`` at the pilot's pinned commit, written against the real
public API of the library. This file proves they are well-formed, labelled, tied to the chart's
pinned commit, and use only symbols that resolve in the pilot's extraction. Whether they compile
or run is decided later by the real ingestion verifier (``verify_go_example``), never here:
nothing in this file imports the library, runs an example, or reaches a network. Every example was
built and run by hand against a real checkout of the pinned commit before being written here; this
file only re-checks the static shape, not the runtime behaviour.

Every rule and constant is imported from ``foss_mcp.indexing.authored_examples`` rather than
retyped.
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

PILOT = "pdf_go"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 5
EXAMPLE_NUMBERS = [1, 2, 3, 4, 5]

_IMPORT_SPEC_RE = re.compile(r'(?m)^\t(?:(\w+)\s+)?"([^"]+)"')
_SAVE_OR_OPEN_LITERAL_RE = re.compile(r'\b(?:Save|Open|OpenWithPassword)\(\s*"')


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


def _module_path() -> str:
    return _pilot_library()["modulePath"]


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


def test_the_sidecar_loads_and_yields_exactly_five_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"go"}


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
def test_every_example_is_a_complete_program_with_func_main(number: int) -> None:
    code = _example(number)["code"]
    assert "package main" in code
    assert "func main(" in code


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_library_import_line_imports_from_the_pinned_module(number: int) -> None:
    code = _example(number)["code"]
    module_path = _module_path()
    imports = [(match.group(1), match.group(2)) for match in _IMPORT_SPEC_RE.finditer(code)]
    library_imports = [path for _, path in imports if path == module_path]
    assert library_imports, f"the example imports nothing from {module_path}"
    # Every other import is standard library: Go's standard packages have no dot in their
    # first path segment, unlike a third-party module, which would not resolve in the
    # verifier's throwaway project (only the pinned library is ever replaced there).
    for _, path in imports:
        if path == module_path:
            continue
        first_segment = path.split("/", 1)[0]
        assert "." not in first_segment, f"unexpected non-stdlib import: {path!r}"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_file_path_used_is_a_temporary_file_not_a_fixture_on_disk(number: int) -> None:
    code = _example(number)["code"]
    # A standalone example must not depend on a testdata/ fixture or a result_files/ output
    # directory that only exists in the upstream checkout: every Save/Open/OpenWithPassword
    # call on a literal path must go through a path built from the OS temporary directory.
    for match in _SAVE_OR_OPEN_LITERAL_RE.finditer(code):
        raise AssertionError(f"a literal path is passed directly to {match.group(0)!r}")
    if re.search(r"\.Save\(|pdf\.Open\(|pdf\.OpenWithPassword\(", code):
        assert "TempDir()" in code
