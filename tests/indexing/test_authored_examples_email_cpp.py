"""Offline checks of the email/cpp authored-example sidecar (TC-737, REQ-G3-014).

``tests/fixtures/furnished/email_cpp/authored_examples.yaml`` holds five minimal, labelled
examples written against the real public API of ``AsposeEmailFoss`` at the pinned commit - the
real upstream repository's own ``examples/`` directory only carries three sample programs, so two
of the five reach into the real public header surface (``mapi_property_collection``,
``cfb_storage``/``cfb_stream``/``cfb_document``/``cfb_reader``/``cfb_writer``) beyond what those
samples show, the same way ``cells/rust``'s own sidecar mixes sample-derived and API-derived
examples. This file proves the five are well-formed, labelled, tied to the chart's pinned commit
and use only symbols that resolve in the pilot's extraction. Whether they compile or run is
decided later by the ingestion verifier, never here: nothing in this file imports the library,
compiles a translation unit, runs an example, or reaches a network.

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
    extract_candidate_examples,
    normalised_code_sha256,
)
from foss_mcp.normalization.citation import qualified_symbol_index_from_api_surface

PILOT = "email_cpp"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 5
EXAMPLE_INDEXES = list(range(EXPECTED_EXAMPLES))

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]', re.MULTILINE)


def _pilot_library() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    for pilot in values["ingestion"]["pilots"]:
        if f"{pilot['family']}_{pilot['platform']}" == PILOT:
            return pilot["library"]
    raise AssertionError(f"{PILOT} is not a pilot in values.yaml")


def _api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _document() -> dict:
    return yaml.safe_load(SIDECAR.read_bytes().decode("utf-8"))


def _load() -> tuple:
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
    library = _pilot_library()
    titles = {candidate.title for candidate in extract_candidate_examples(page)}
    titles |= {
        candidate.title for candidate in extract_candidate_examples(page, platform=library["platform"])
    }
    return titles


def test_the_sidecar_loads_as_five_authored_candidates() -> None:
    candidates = _load()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"cpp"}


def test_the_sidecar_is_tied_to_the_charts_pinned_library() -> None:
    library = _pilot_library()
    document = _document()
    assert document["schema_version"] == 1
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
    assert document["language"] == LANGUAGE_BY_PLATFORM[library["platform"]]
    assert len(document["examples"]) == EXPECTED_EXAMPLES


def test_the_sidecar_has_no_validation_problem() -> None:
    library = _pilot_library()
    problems = validate_authored_examples(
        _document(),
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )
    assert problems == []


def test_a_stale_commit_is_refused_by_the_validator() -> None:
    library = _pilot_library()
    document = _document()
    document["library_commit"] = "0" * 40
    problems = validate_authored_examples(
        document,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )
    assert any("rule 3" in problem for problem in problems), problems


def test_titles_are_unique_and_differ_from_every_readme_title() -> None:
    titles = [example["title"] for example in _document()["examples"]]
    assert len(set(titles)) == EXPECTED_EXAMPLES
    readme_titles = _readme_titles()
    assert readme_titles, "the committed page must carry at least one README candidate"
    assert not set(titles) & readme_titles


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_code_hash_matches(index: int) -> None:
    example = _document()["examples"][index]
    assert example["code_sha256"] == normalised_code_sha256(example["code"])


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_label_comment_is_the_first_non_blank_line(index: int) -> None:
    code = _document()["examples"][index]["code"]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first.startswith("//")
    assert "Authored example" in first


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_declared_symbol_resolves_and_appears_in_the_code(index: int) -> None:
    example = _document()["examples"][index]
    symbols = example["symbols"]
    assert len(symbols) >= 2
    symbol_index = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in symbols:
        assert symbol_index.declarations_for(symbol), f"{symbol} has no declaration in the api_surface"
        last = symbol.rsplit(".", 1)[-1]
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(last)}(?![A-Za-z0-9_])", example["code"]), symbol


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_forbidden_pattern_matches(index: int) -> None:
    code = _document()["examples"][index]["code"]
    matched = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert matched == []


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_program_has_main_and_includes_at_least_one_library_header(index: int) -> None:
    code = _document()["examples"][index]["code"]
    assert "int main(" in code
    includes = _INCLUDE_RE.findall(code)
    assert includes, "an example must include at least one header"
    library_includes = [path for _delimiter, path in includes if path.startswith("aspose/email/foss/")]
    assert library_includes, f"no #include names a header under aspose/email/foss/: {includes}"


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_depends_on_a_file_that_does_not_exist_on_disk(index: int) -> None:
    code = _document()["examples"][index]["code"]
    # Every example works purely in memory (bytes round-tripped through a stringstream); none
    # saves to or loads from a literal relative path on disk.
    assert not re.search(r"\.save\(\s*\"|\.save_to_eml\(\s*\"|from_file\(\s*\"|load_from_eml\(\s*\"", code)
