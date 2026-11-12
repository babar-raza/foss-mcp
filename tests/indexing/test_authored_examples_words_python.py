"""Offline checks of the words/python authored-example sidecar (TC-770, REQ-G3-014).

``tests/fixtures/furnished/words_python/authored_examples.yaml`` holds exactly five minimal,
labelled examples written against the real public API of ``aspose-words-foss`` at the pinned
commit, each adapted from a real upstream ``ApiExamples/*.py`` module (loading from a stream,
Markdown save options, OOXML save options, image round-tripping, and cross-format text
consistency) with the ``DocsExamplesBase``/``docs_examples_base`` scaffolding and the
``ApiExamples``-only output directory stripped. This file proves they are well-formed, labelled,
tied to the chart's pinned commit and use only symbols that resolve in the pilot's extraction.
Whether they run is decided later by the ingestion verifier, never here: nothing in this file
imports the library, runs an example, or reaches a network.

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

PILOT = "words_python"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 5
EXAMPLE_INDEXES = list(range(EXPECTED_EXAMPLES))

_IMPORT_RE = re.compile(r"(?m)^[ \t]*(?:import\s+([^\s#]+)|from\s+([^\s#]+)\s+import\b)")


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


def _example(index: int) -> dict:
    return _document()["examples"][index]


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
    assert {candidate.language for candidate in candidates} == {"python"}


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


def test_the_example_titles_are_unique_and_differ_from_every_readme_title() -> None:
    examples = _document()["examples"]
    titles = [example["title"] for example in examples]
    assert len(set(titles)) == len(titles)
    readme_titles = _readme_titles()
    assert readme_titles, "the committed page must carry at least one README candidate"
    assert not set(titles) & readme_titles
    for example in examples:
        assert len(example["symbols"]) >= 2, example["title"]


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_code_hash_matches(index: int) -> None:
    example = _example(index)
    assert example["code_sha256"] == normalised_code_sha256(example["code"])


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_label_comment_is_the_first_non_blank_line(index: int) -> None:
    code = _example(index)["code"]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first.startswith("#")
    assert "Authored example" in first


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_declared_symbol_resolves_and_appears_in_the_code(index: int) -> None:
    example = _example(index)
    index_of_symbols = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in example["symbols"]:
        assert index_of_symbols.declarations_for(symbol), f"{symbol} has no declaration in the api_surface"
        last_segment = symbol.rsplit(".", 1)[-1]
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(last_segment)}(?![A-Za-z0-9_])", example["code"]), (
            f"{symbol} is not used in the code"
        )


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_forbidden_pattern_matches(index: int) -> None:
    code = _example(index)["code"]
    hits = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert hits == []


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_example_imports_from_the_pilots_own_package(index: int) -> None:
    code = _example(index)["code"]
    modules = [module or package for module, package in _IMPORT_RE.findall(code)]
    assert modules, "the example imports nothing"
    library_imports = [module for module in modules if module.startswith("aspose.words_foss")]
    assert library_imports, "the example imports nothing from aspose.words_foss"
    # Every other import is standard library; a third-party package would not resolve in the verifier's project.
    stdlib_modules = {"os", "tempfile", "io"}
    for module in modules:
        top_level = module.split(".", 1)[0]
        assert module.startswith("aspose.words_foss") or top_level in stdlib_modules, (
            f"unexpected import: {module}"
        )


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_carries_docs_examples_base_scaffolding(index: int) -> None:
    # The upstream ApiExamples modules subclass DocsExamplesBase and write their output under
    # ApiExamples/output/; each authored example must be the extracted standalone logic, running
    # against a bare-named upstream fixture staged beside it, not the original harness.
    code = _example(index)["code"]
    assert "DocsExamplesBase" not in code
    assert "ARTIFACTS_DIR" not in code
    assert "MY_DIR" not in code
    assert "sys.path" not in code


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_depends_on_a_file_that_does_not_exist(index: int) -> None:
    code = _example(index)["code"]
    # A saved file must go to the temporary directory (and be removed) or stay in memory.
    if ".save(" in code:
        assert "tempfile.gettempdir()" in code
        assert "os.remove(" in code


def test_every_staged_fixture_file_is_real_in_the_pinned_repository_layout() -> None:
    # Every bare-quoted file name an example opens must be one of the pinned repository's own
    # tests/data/input/ fixtures, which the real ingestion verifier's _stage_named_repository_files
    # copies beside the candidate script by exact file name before running it.
    known_fixtures = {
        "test_full_article.docx",
        "test_full_article.doc",
        "test_full_article.rtf",
        "image_in_header.docx",
    }
    staged_name_re = re.compile(r"""["'](test_full_article\.\w+|image_in_header\.docx)["']""")
    for index in EXAMPLE_INDEXES:
        code = _example(index)["code"]
        for match in staged_name_re.finditer(code):
            assert match.group(1) in known_fixtures, match.group(1)
