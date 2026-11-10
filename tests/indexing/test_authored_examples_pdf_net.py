"""Offline checks of the pdf/net authored-example sidecar (TC-747, REQ-G3-014).

``tests/fixtures/furnished/pdf_net/authored_examples.yaml`` holds five authored examples written
against the real public API of ``Aspose.PDF FOSS for .NET`` at the pinned commit
(``aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET``). The pinned repository ships no ``examples/``- or
``samples/``-style directory of runnable sample programs at this commit - only the library source
(``src/``), its own test suite (``tests/``), and its README's own fenced snippets, most of which
read an ``input.pdf``/``form.pdf`` that does not exist on disk and so cannot verify. All five
examples here are therefore authored directly against the real public API surface
(``tests/fixtures/pdf_net/api_surface.json``), the same way ``email/net``'s and ``words/net``'s own
sibling cards did for part of their own slate when upstream samples ran thin: encryption with a
rejected wrong password, an AcroForm text box field, a nested bookmark outline built with
``OutlineBuilder``, visible/hidden optional-content layers built with ``OptionalContentBuilder``,
and embedded files with metadata. Each example builds a document from scratch with
``Document.Create()``, round-trips it through a plain ``byte[]`` with ``Document.ToArray()`` /
``Document.Open(byte[])`` - the exact pattern this repository's own test suite uses
(``tests/OutlineBuilderTests.cs``, ``tests/OptionalContentBuilderTests.cs``,
``tests/FileSpecificationTests.cs``) - rather than a literal path on disk, so the ingestion
verifier's throwaway working directory never needs a file that does not already exist. This file
proves the five are well-formed, labelled, tied to the pilot's pinned commit and use only symbols
that resolve in the pilot's extraction. Whether each one compiles and runs is decided later by the
ingestion verifier, never here: nothing in this file imports the library, compiles a project, runs
an example, or reaches a network. Every one of the five was built and run for real against the
pinned commit before being written here (not asserted by any test in this file, which stays
offline per the authored-examples contract) - including one (embedded files) whose first drafted
form read the two attachments back by their 1-based numeric index and assumed insertion order;
a real run showed the ``/Names/EmbeddedFiles`` name tree instead orders entries alphabetically by
filename (``report.bin`` before ``summary.txt``, the reverse of the add order), so it was rewritten
to look each attachment up by name instead.

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

PILOT = "pdf_net"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 5
EXAMPLE_NUMBERS = [1, 2, 3, 4, 5]

#  A true using-DIRECTIVE (``using Namespace;``), never a using-DECLARATION (``using var x = ...;``):
#  the token right after ``using`` must be a bare dotted identifier followed directly by ``;``.
_USING_RE = re.compile(r"(?m)^[ \t]*using\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;")


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


def test_the_sidecar_loads_and_yields_exactly_five_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"csharp"}


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


def test_the_example_titles_are_unique_and_each_has_at_least_two_symbols() -> None:
    examples = _document()["examples"]
    titles = [example["title"] for example in examples]
    assert len(set(titles)) == len(titles)
    for example in examples:
        assert len(example["symbols"]) >= 2, example["title"]


def test_the_titles_differ_from_every_readme_candidate_title() -> None:
    readme_titles = _readme_titles()
    assert readme_titles, "the committed page must carry at least one README candidate"
    titles = {example["title"] for example in _document()["examples"]}
    assert not titles & readme_titles


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
def test_every_example_round_trips_through_a_byte_array(number: int) -> None:
    """Every example saves to and reloads from a plain ``byte[]`` (``Document.ToArray()`` /
    ``Document.Open(byte[])``), never a literal path on disk, so the ingestion verifier's
    throwaway working directory never needs a file that does not already exist."""
    code = _example(number)["code"]
    assert "doc.ToArray()" in code
    assert "Document.Open(" in code
    assert not re.search(r"\.Save\(\s*\"", code), "a literal file path must not be saved to disk"
    assert not re.search(r"Document\.Open\(\s*\"", code), "a literal file path must not be loaded from disk"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_example_has_a_real_assertion(number: int) -> None:
    code = _example(number)["code"]
    assert "throw new Exception(" in code


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_library_using_directive_imports_from_the_pilot_namespace(number: int) -> None:
    code = _example(number)["code"]
    usings = [match.group(1).strip() for match in _USING_RE.finditer(code)]
    assert usings, "every example must have at least one using directive"
    assert all(using.startswith("Aspose.Pdf") for using in usings), usings


def test_no_two_examples_share_identical_code() -> None:
    codes = [_example(number)["code"] for number in EXAMPLE_NUMBERS]
    assert len(set(codes)) == len(codes)
