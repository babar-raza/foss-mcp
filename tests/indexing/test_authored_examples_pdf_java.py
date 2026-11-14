"""Offline checks of the pdf/java authored-example sidecar (TC-748, REQ-G3-014).

``tests/fixtures/furnished/pdf_java/authored_examples.yaml`` holds six authored examples written
against the real public API of ``org.aspose.pdf`` (Aspose.PDF FOSS for Java) at the pinned commit:
a text-fragment round trip read back with ``TextAbsorber``, an AcroForm ``TextBoxField`` round
trip, a page rendered to a PNG with ``PngDevice``, a ``DocumentInfo`` title/author round trip, a
``Table`` row round trip read back with ``TextAbsorber`` (TC-827 dropped it only to stay inside the
five-example cap, which made the bare query ``table`` a miss; TC-837 raised the cap to six and
restored it) and (TC-827) a ``RadioButtonField`` group round trip: two options, one selected by
value, the group and its selection read back by name after a reload. The upstream
README's own radio-button example omits the ``RadioButtonField`` import and cannot compile as
published, so this one carries the correct ``org.aspose.pdf.forms`` imports. Each is a complete, self-
contained Java program (its own ``public class`` with a ``main`` method and its own ``import``
lines) rather than a bare statement block, so ``verify_java_example`` compiles and runs it exactly
as written with no wrapper and no furnished-page imports added (``_JAVA_PUBLIC_CLASS_RE`` matches
a ``public class`` declaration and uses the candidate's own source verbatim). Every one was built
with ``mvn package -Dmaven.test.skip=true`` against the real pinned commit and individually
compiled with ``javac`` and run with ``java`` by this card's author before being committed here,
confirming the real printed output each assertion claims.

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

PILOT = "pdf_java"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 6
EXAMPLE_NUMBERS = [1, 2, 3, 4, 5, 6]

_IMPORT_LINE_RE = re.compile(r"(?m)^import\s+([^;]+);")
_LIBRARY_IMPORT_PREFIX = "org.aspose.pdf"
_LITERAL_PATH_SAVE_RE = re.compile(r'\.save\(\s*"')
_LITERAL_PATH_DOCUMENT_RE = re.compile(r'new\s+Document\(\s*"')


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
    # TC-748: re-confirmed against config/products/pdf/java.yaml and the real cloned commit's own
    # "release 26.9.0" tag at the time this card was authored.
    assert document["library_commit"] == "736cedaa2922e4952f429364bdb9a4bbb1d97863"
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
    # verify_java_example only skips wrapping (and page-import injection) when the candidate
    # already declares its own public class; every authored example here relies on that path,
    # carrying its own imports, so this is pinned directly rather than merely implied.
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
    # Every bare (non-dotted) symbol the example declares is imported by name, or is itself the
    # library's wildcard-free direct package member used via a dotted symbol only.
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
    assert _LITERAL_PATH_DOCUMENT_RE.search(code) is None, "a literal string path opens a Document"
    # Every example keeps its PDF or raster bytes in memory (a ByteArrayOutputStream, read back
    # with a ByteArrayInputStream for a Document round trip, or read directly for a rendered
    # image); none of them is a Document.save(...) to a literal path, which the two patterns above
    # already rule out.
    assert "ByteArrayOutputStream" in code


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_example_prints_the_output_it_asserts_on(number: int) -> None:
    # A compiled-but-silent program is not distinguishable from one that never ran; every example
    # prints the real value(s) it then asserts against, so a verifier run leaves a human-readable
    # trace of what actually happened, not just an exit code.
    code = _example(number)["code"]
    assert "System.out.println(" in code
    assert "throw new AssertionError(" in code


def _radio_button_examples() -> list[dict]:
    return [example for example in _document()["examples"] if "RadioButtonField" in example["symbols"]]


def test_exactly_one_example_covers_a_radio_button_group_in_a_form() -> None:
    # TC-827 (OWNER-07): the upstream README's "Create a Form with Radio Buttons" uses RadioButtonField
    # without importing it and cannot compile as published, so the pilot serves its own verified
    # example of the same subject. It must be present, and be exactly one example.
    examples = _radio_button_examples()
    assert len(examples) == 1
    example = examples[0]
    words = example["title"].lower().split()
    # The title names the subject in specific words (a generic-word title collides with unrelated
    # queries through title_anchored) and stays within the 12-word limit.
    assert {"radio", "acroform", "group"} <= set(words)
    assert len(words) <= 12
    # TC-839: the bare title word "button" anchors the generic query "add a button to a pdf" (the forgiveness
    # concise table asks for the Widget Annotation README example there and allows zero wrong answers):
    # once this sidecar is baked, that query answered with this example (23 hit / 2 miss / 1 WRONG). The
    # word "radio" alone names the subject, and the query "radio button" still answers through it.
    assert not {"button", "buttons"} & set(words)
    candidate_titles = {candidate.title for candidate in _candidates()}
    assert example["title"] in candidate_titles


def test_the_radio_button_example_imports_the_class_the_readme_example_forgets() -> None:
    code = _radio_button_examples()[0]["code"]
    imports = {match.group(1).strip() for match in _IMPORT_LINE_RE.finditer(code)}
    assert "org.aspose.pdf.forms.RadioButtonField" in imports
    assert "org.aspose.pdf.forms.Form" in imports


def test_the_radio_button_example_builds_a_group_then_checks_the_reloaded_selection() -> None:
    code = _radio_button_examples()[0]["code"]
    assert "new RadioButtonField(page)" in code
    assert code.count(".addOption(") >= 2
    assert ".setValue(" in code
    assert "new Document(new ByteArrayInputStream(" in code
    reload_index = code.index("new Document(new ByteArrayInputStream(")
    after_reload = code[reload_index:]
    # the group is looked up by name on the reloaded document and its selection and option count are
    # asserted there, not on the in-memory object that was built before the save
    assert 'form.get("shippingMethod")' in after_reload
    assert "(RadioButtonField)" in after_reload
    assert ".getValue()" in after_reload
    assert ".getOptions().size()" in after_reload
    assert after_reload.count("throw new AssertionError(") >= 2


# --- TC-837: the Table row example, dropped only for the five-example cap, is restored -------------


def _table_examples() -> list[dict]:
    return [example for example in _document()["examples"] if "Table" in example["symbols"]]


def test_exactly_one_example_builds_a_table_row_and_reads_it_back() -> None:
    examples = _table_examples()
    assert len(examples) == 1, [example["title"] for example in examples]
    example = examples[0]
    words = example["title"].lower().split()
    assert {"two-column", "row"} <= set(words)
    assert len(words) <= 12
    assert example["title"] in {candidate.title for candidate in _candidates()}
    # the Table example and the radio button example are different examples with different titles
    radio = [e for e in _document()["examples"] if "RadioButtonField" in e["symbols"]]
    assert len(radio) == 1
    assert radio[0]["title"] != example["title"]


def test_the_table_example_adds_two_cells_then_checks_both_in_the_reloaded_text() -> None:
    code = _table_examples()[0]["code"]
    assert "import org.aspose.pdf.Table;" in code
    assert "import org.aspose.pdf.Row;" in code
    assert "page.getParagraphs().add(table)" in code
    assert "table.getRows().add()" in code
    assert code.count("row.getCells().add(") == 2
    assert "new Document(new ByteArrayInputStream(" in code
    after_reload = code[code.index("new Document(new ByteArrayInputStream(") :]
    # the cell text is read from the RELOADED document, not from the in-memory table
    assert "new TextAbsorber()" in after_reload
    assert "absorber.getText()" in after_reload
    assert after_reload.count("throw new AssertionError(") >= 1
    assert 'extracted.contains("Quarter")' in after_reload
    assert 'extracted.contains("Revenue")' in after_reload


def test_the_table_example_keeps_its_verified_hash() -> None:
    # verified when first authored (TC-748) and re-run for TC-837 against org.aspose:aspose-pdf-foss at
    # the pinned commit; an edit to the code changes this hash
    code_sha256 = _table_examples()[0]["code_sha256"]
    assert code_sha256 == "bc186a8eea83e5e2268d7065c74c3496b67007bdc20e85cf4caebce9abc37718"
