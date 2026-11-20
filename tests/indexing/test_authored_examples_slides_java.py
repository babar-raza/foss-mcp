"""Offline checks of the slides/java authored-example sidecar (TC-754, REQ-G3-014).

``tests/fixtures/furnished/slides_java/authored_examples.yaml`` holds six authored examples
written against the real public API of ``org.aspose.slides.foss`` (Aspose.Slides FOSS for Java) at
the pinned commit: a connector round trip whose start and end connections are read back by shape
name, a hidden-slide flag round trip across a three-slide deck, a paragraph alignment/margin/indent
round trip, a solid shape fill round trip read back as fill type and ARGB value, a slide
removal whose remaining order is confirmed after a reload, and (TC-850) an in-memory slide clone whose copy is
read back as a shape count (the README "Clone a Slide" example prints nothing, so it is no longer published; the
reload at the end only reports the saved slide count, because SLIDES-JAVA-010B documents that addClone's slide is
not registered in the saved file). The real pinned upstream repository
(``aspose-slides-foss/Aspose.Slides-FOSS-for-Java``) ships no dedicated ``examples/``-style sample
directory at the pinned commit (its tree holds only ``src/``, ``tests/test_data/`` and build files),
so every one of these six was authored against the real API surface in
``tests/fixtures/slides_java/api_surface.json`` and the working calls shown in the upstream README.

Each is a complete, self-contained Java program (its own ``public class`` with a ``main`` method
and its own ``import`` lines) rather than a bare statement block, so ``verify_java_example``
compiles and runs it exactly as written with no wrapper and no furnished-page imports added
(``_JAVA_PUBLIC_CLASS_RE`` matches a ``public class`` declaration and uses the candidate's own
source verbatim). Every one was built with ``mvn package -Dmaven.test.skip=true`` against the real
pinned commit, individually compiled with ``javac`` and run with ``java`` by this card's author, and
then passed through the real ``verify_java_example`` before being committed here.

This file proves the sidecar is well-formed, labelled, tied to the pinned commit and uses only
symbols that resolve in the pilot's extraction. Whether the examples compile or run under the real
ingestion verifier is decided later by that verifier, never here: nothing in this file imports the
library, compiles, runs an example, or reaches a network.

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

PILOT = "slides_java"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 6
EXAMPLE_NUMBERS = [1, 2, 3, 4, 5, 6]

_IMPORT_LINE_RE = re.compile(r"(?m)^import\s+([^;]+);")
_LIBRARY_IMPORT_PREFIX = "org.aspose.slides.foss"
_LITERAL_PATH_SAVE_RE = re.compile(r'\.save\(\s*"')
_LITERAL_PATH_PRESENTATION_RE = re.compile(r'new\s+Presentation\(\s*"')


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


def test_the_sidecar_loads_and_yields_exactly_six_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"java"}


def test_the_sidecar_header_matches_the_chart_pin() -> None:
    library = _pilot_library()
    document = _document()
    assert document["schema_version"] == 1
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
    # TC-754: the pinned commit, re-confirmed against infra/helm/foss-mcp/values.yaml (slides/java)
    # and the real upstream repository at this commit when this card was authored.
    assert document["library_commit"] == "620a2614418854b4a18966a361e6907ddc88c7cb"
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
def test_every_example_is_a_complete_program_with_its_own_public_class(number: int) -> None:
    code = _example(number)["code"]
    # verify_java_example only skips wrapping (and page-import/java-packages injection) when the
    # candidate already declares its own public class; every authored example here relies on that
    # path, carrying its own imports, so this is pinned directly rather than merely implied.
    assert re.search(r"public\s+(?:final\s+|abstract\s+)?class\s+\w+", code)
    assert "public static void main(" in code


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_import_line_is_the_standard_library_or_the_pinned_package(number: int) -> None:
    code = _example(number)["code"]
    imports = [match.group(1).strip() for match in _IMPORT_LINE_RE.finditer(code)]
    library_imports = [name for name in imports if name.startswith(_LIBRARY_IMPORT_PREFIX)]
    assert library_imports, f"the example imports nothing from {_LIBRARY_IMPORT_PREFIX}"
    for name in imports:
        assert name.startswith((_LIBRARY_IMPORT_PREFIX, "java.")), f"unexpected import: import {name};"
    # Every bare (non-dotted) symbol the example declares is imported by name from the pinned package.
    imported_simple_names = {name.rsplit(".", 1)[-1] for name in library_imports}
    type_symbols = {symbol for symbol in _example(number)["symbols"] if "." not in symbol}
    for symbol in type_symbols:
        assert symbol in imported_simple_names, f"{symbol} is not imported from {_LIBRARY_IMPORT_PREFIX}"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_no_example_depends_on_a_file_that_does_not_exist(number: int) -> None:
    code = _example(number)["code"]
    # Every example saves to and reloads from an in-memory byte array; none touches the real
    # filesystem, so there is nothing to clean up and nothing that can go stale on disk.
    assert _LITERAL_PATH_SAVE_RE.search(code) is None, "a literal string path is passed to .save("
    assert _LITERAL_PATH_PRESENTATION_RE.search(code) is None, "a literal string path opens a Presentation"
    assert "ByteArrayOutputStream" in code
    assert "ByteArrayInputStream" in code


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_example_prints_the_output_it_asserts_on(number: int) -> None:
    # A compiled-but-silent program is not distinguishable from one that never ran; every example
    # prints the real value(s) it then asserts against, so a verifier run leaves a human-readable
    # trace of what actually happened, not just an exit code.
    code = _example(number)["code"]
    assert "System.out.println(" in code
    assert "throw new AssertionError(" in code
