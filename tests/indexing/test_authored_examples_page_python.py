"""Offline checks of the page/python authored-example sidecar (TC-764, REQ-G3-014).

``tests/fixtures/furnished/page_python/authored_examples.yaml`` holds four minimal, labelled
examples written against the real public API of ``aspose-page-foss`` at the pinned commit. The
pinned repository ships no dedicated ``examples/``-style sample directory upstream (Appendix C of
the foss-mcp unified plan classes it as "C: none"), so this sidecar carries new authored examples
derived from the library's public API surface instead. Every example uses only local, in-memory
PostScript/EPS or XPS construction and a round trip through the temporary directory, with no
environment, process, network or interactive-input access, and none of them touches rasterization
(``to_image``/the Skia backend), since this environment's own verifier image is separately noted as
missing ``libEGL``.

Each example was run against the pinned package by the ingestion verifier before it was committed.
Whether they run is decided again at build time by that same verifier, never here: nothing in this
file imports the library, runs an example, or reaches a network.

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

PILOT = "page_python"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FURNISHED = FIXTURES / "furnished"
SIDECAR = FURNISHED / PILOT / SIDECAR_NAME
PAGE = FURNISHED / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 4
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


def test_the_sidecar_loads_and_yields_exactly_four_authored_candidates() -> None:
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
    library_imports = [module for module in modules if module.startswith("aspose.page")]
    assert library_imports, "the example imports nothing from aspose.page"
    # Every other import is standard library; a third-party package would not resolve in the verifier's project.
    stdlib_modules = {"os", "tempfile"}
    for module in modules:
        top_level = module.split(".", 1)[0]
        assert module.startswith("aspose.page") or top_level in stdlib_modules, f"unexpected import: {module}"


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_carries_unittest_or_sys_path_scaffolding(index: int) -> None:
    code = _example(index)["code"]
    assert "unittest" not in code
    assert "sys.path" not in code
    assert "self.assert" not in code
    assert "pytest" not in code
    assert "tmp_path" not in code


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_depends_on_a_file_that_does_not_exist(index: int) -> None:
    code = _example(index)["code"]
    # A saved file must go to the temporary directory (and be removed) or stay in memory.
    if ".save(" in code:
        assert "tempfile.gettempdir()" in code
        assert "os.remove(" in code


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_example_prints_and_asserts_real_output(index: int) -> None:
    # Every example must observe (print) and check (assert) real output, not merely construct an
    # object and discard it.
    code = _example(index)["code"]
    assert "print(" in code
    assert "assert " in code


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_touches_rasterization(index: int) -> None:
    # This environment's own ingestion verifier image is noted as missing libEGL, which may block
    # anything that touches actual rendering/rasterization (to_image / the Skia backend). None of
    # these examples depend on it: each stays with document/page construction, XMP metadata, print
    # tickets and page reordering instead.
    code = _example(index)["code"]
    assert "to_image" not in code
    assert "skia" not in code.lower()
