"""G3/TC-550: the eleven re-extracted, uncapped fixtures (REQ-G3-008, REQ-G3-005). Offline, deterministic, no skips.

Every pilot of ``PILOTS`` was truncated at 300 types (or a lower proposal) before TC-530/TC-550. Each is now regenerated
at its chart pin by the merged extractor with a cap above its total, so the fixture holds the WHOLE public surface
(kept == total, ``truncated`` false), and the extractor constant, the chart ``maxTypes`` and the compose ``--max-types``
are one number. TC-530 did the same for pdf_net and words_net (``tests/extraction/test_reextracted_dotnet.py``).

The pilots whose upstream repositories answer HTTP 404 (owner item OWNER-15) were removed from the product by TC-551, so
they are not here and cannot be re-extracted. Every number in the tables below is MEASURED on the regenerated fixtures
and is a ratchet: a later extraction may raise it, never lower it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import pytest

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from infra.build_chunks import MAX_CHUNK_TEXT_CHARS
from tests.extraction import docs_identifiers
from tests.extraction import reextraction_checks as rc

PILOTS = [
    "pdf_java",
    "pdf_typescript",
    "pdf_go",
    "pdf_python",
    "slides_python",
    "html_python",
    "3d_python",
    "pdf_cpp",
    "slides_cpp",
    "cells_cpp",
    "words_python",
]

README_TYPES = {
    "3d_python": [
        "Scene",
        "Mesh",
        "Box",
        "Sphere",
        "Cylinder",
        "PbrMaterial",
        "LambertMaterial",
        "PhongMaterial",
        "ObjLoadOptions",
        "GltfSaveOptions",
        "PolygonModifier",
        "TransformBuilder",
        "RenderState",
        "ShaderMaterial",
    ],
    "cells_cpp": [
        "Workbook",
        "Worksheet",
        "Cell",
        "Style",
        "Font",
        "Color",
        "PageSetup",
        "DisplayTextFormatter",
    ],
    # SelectorList, StackOfOpenElements, BrowsingContext, ClassSelector and TypeSelector are README-named but are
    # defined in underscore-prefixed (private) modules of the library, which the engine does not extract.
    "html_python": ["HTMLDocument", "Document", "JSContext", "CSSStyleSheet", "Tokenizer", "TreeBuilder"],
    "pdf_cpp": [
        "Document",
        "Form",
        "Rectangle",
        "Circle",
        "Permissions",
        "TextBoxField",
        "TiffDevice",
        "DocumentInfo",
        "Page",
    ],
    "pdf_go": [
        "Document",
        "Page",
        "Form",
        "Table",
        "TextStyle",
        "AnnotationType",
        "EncryptionOptions",
        "LineAnnotation",
        "PngDevice",
        "TableAbsorber",
        "TextStamp",
        "OcrOptions",
    ],
    "pdf_java": [
        "Document",
        "Page",
        "Rectangle",
        "Color",
        "WidgetAnnotation",
        "RadioButtonField",
        "AnnotationCollection",
        "ButtonField",
        "BmpDevice",
        "AbsorbedTable",
        "PageCollection",
        "PdfFileEditor",
    ],
    "pdf_python": [
        "Document",
        "Page",
        "Field",
        "PdfLoadLimits",
        "PageSize",
        "PdfExtractor",
        "PdfFileEditor",
        "ImageStamp",
        "GoToAction",
        "HtmlSaveOptions",
        "FormFlattener",
        "InvalidPasswordException",
    ],
    "pdf_typescript": ["Document", "Page", "Field", "PageFormat", "StampAnnotation", "MarkupAnnotation"],
    "slides_cpp": [
        "Presentation",
        "Slide",
        "SaveFormat",
        "ShapeCollection",
        "ShapeType",
        "SlideCollection",
        "Connector",
        "PictureFrame",
        "Shape",
    ],
    "slides_python": [
        "Presentation",
        "Shape",
        "SaveFormat",
        "ChartType",
        "AutoShape",
        "ChartData",
        "ChartSeries",
        "ColorFormat",
        "DataLabel",
        "DocumentProperties",
        "BulletFormat",
        "CommentAuthorCollection",
    ],
    # ReaderContext lives in a private module (`docx_reader/ldm_builder/_context.py`); ShapeNode is named by the
    # README but has no declaration anywhere in the repository at the pin.
    "words_python": [
        "Document",
        "SaveFormat",
        "MarkdownSaveOptions",
        "OoxmlSaveOptions",
        "PdfSaveOptions",
        "DocumentReader",
        "Font",
        "Paragraph",
        "ParagraphFormat",
        "Table",
        "Cell",
        "OoxmlCompliance",
        "PdfCompliance",
        "RtfFileReader",
        "TextFileReader",
        "PdfPageMode",
    ],
}

RECALL_FLOOR = {
    "pdf_java": 0.90,
    "pdf_typescript": 0.90,
    "pdf_go": 0.90,
    "pdf_python": 0.90,
    "slides_python": 0.90,
    "html_python": 0.85,
    "3d_python": 0.90,
    "pdf_cpp": 0.90,
    "slides_cpp": 0.89,
    "cells_cpp": 0.87,
    "words_python": 0.90,
}

RECALL_EXCEPTION_REASON = {
    "cells_cpp": (
        "the README's three unresolved identifiers are FetchContent and add_subdirectory (CMake commands it tells "
        "the reader to use) and txt (the file extension of LICENSE.txt): a build tool and a file extension, none an "
        "API symbol of this library"
    ),
    "html_python": (
        "the 35 unresolved identifiers are classes and functions defined in underscore-prefixed (private) modules the "
        "engine does not extract (ActiveFormattingList, AttributeOperator, AttributeSelector, BrowsingContext, "
        "ClassSelector, ComplexSelector, SelectorList, StackOfOpenElements, TypeSelector, WindowEventLoop, "
        "_matches_pseudo_class and the other selector AST and tree-builder internals of `css/_ast.py`, "
        "`tree/_builder.py`, `dom/_browsing_context.py`), CSS property names (break_after, break_before, "
        "break_inside), web-platform names (getComputedStyle, querySelector), the package name aspose_html, a "
        "build keyword (import, toml) and the builtin NotImplementedError: none is a public symbol of the library"
    ),
    "slides_cpp": (
        "the 21 unresolved identifiers are build tooling (FetchContent, cmake, find_dependency, PATCH, "
        "SameMinorVersion, target_include_directories, aspose_slides_foss), file extensions and library suffixes "
        "(h, lib, txt), std::invalid_argument, pugi's xml_node, the members the README's 'not in the public API' "
        "section names because they DO NOT EXIST on purpose (add_chart, add_group_shape, set_hyperlink_click, "
        "background, master_theme, slide_size, sections, get_inherited_xfrm), and PptxExporterFactory, a README "
        "table row with no declaration anywhere in the repository at the pin"
    ),
}

# Measured on the regenerated fixtures (LF text, bytes). floor = measured * 0.9 rounded down to a multiple of 10 KB.
SIZE_FLOOR_BYTES = {
    "pdf_java": 10_127_360,
    "pdf_typescript": 3_512_320,
    "pdf_go": 532_480,
    "pdf_python": 1_392_640,
    "slides_python": 1_832_960,
    "html_python": 9_379_840,
    "3d_python": 1_239_040,
    "pdf_cpp": 2_754_560,
    "slides_cpp": 1_720_320,
    "cells_cpp": 890_880,
    "words_python": 675_840,
}
SIZE_CEILING_BYTES = 25 * 1024 * 1024
TOTAL_CEILING_BYTES = 120 * 1024 * 1024

# methods + properties over every entry, measured, times 0.95 rounded down.
MEMBER_FLOOR = {
    "pdf_java": 23_187,
    "pdf_typescript": 5_598,
    "pdf_go": 1_258,
    "pdf_python": 2_347,
    "slides_python": 3_861,
    "html_python": 25_419,
    "3d_python": 2_893,
    "pdf_cpp": 6_543,
    "slides_cpp": 3_733,
    "cells_cpp": 1_918,
    "words_python": 1_741,
}

# Symbol chunks longer than infra.build_chunks.MAX_CHUNK_TEXT_CHARS (the sanitiser would cut them), measured: none.
OVERSIZE_CHUNK_CEILING = dict.fromkeys(PILOTS, 0)

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_COVERAGE = re.compile(
    r"^API-Coverage: (\d+) of (\d+) types(; (\d+) of (\d+) functions)?(; (\d+) of (\d+) source files could not be parsed)?$"
)
_OLD_TYPESCRIPT_COMMIT = "c672b3918318ccd0c13bc9dc619620cac2674c9e"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _symbol_chunks(key: str) -> list[Any]:
    return build_chunks_from_api_surface(rc.load_fixture(key), title=key, max_types=rc.chart_max_types(key))


def _oversize_count(texts: Iterable[str], limit: int = MAX_CHUNK_TEXT_CHARS) -> int:
    return sum(1 for text in texts if len(text) > limit)


def _member_count(fixture: dict[str, Any]) -> int:
    return sum(
        len(e.get("methods") or []) + len(e.get("properties") or []) for e in fixture.get("types") or []
    )


def _parse_coverage(line: str) -> tuple[int, int, int | None, int | None] | None:
    """``(types kept, types total, functions kept, functions total)`` or None when the line is malformed."""
    match = _COVERAGE.match(line)
    if match is None:
        return None
    functions = (int(match.group(4)), int(match.group(5))) if match.group(3) else (None, None)
    return int(match.group(1)), int(match.group(2)), functions[0], functions[1]


def _coverage_is_complete(line: str) -> bool:
    parsed = _parse_coverage(line)
    if parsed is None:
        return False
    kept, total, f_kept, f_total = parsed
    return kept == total and f_kept == f_total


def _size_problems(key_size: int, floor: int, ceiling: int = SIZE_CEILING_BYTES) -> list[str]:
    problems = []
    if key_size < floor:
        problems.append(f"{key_size} bytes is below the floor {floor}")
    if key_size > ceiling:
        problems.append(f"{key_size} bytes is above the ceiling {ceiling}")
    return problems


# -- generic tests, one case per pilot ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_is_extracted_at_the_chart_pin(key: str) -> None:
    commit = rc.load_fixture(key)["source_commit"]
    assert commit == rc.pinned_commit(key)
    assert _HEX40.match(commit)


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_carries_the_current_extractor_keys(key: str) -> None:
    fixture = rc.load_fixture(key)
    assert fixture.get("extractor_version") == rc.REQUIRED_EXTRACTOR_VERSION
    counts = fixture.get("symbol_counts")
    assert isinstance(counts, dict)
    assert _is_int(counts.get("types")) and _is_int(counts.get("functions"))
    totals = fixture.get("counts")
    assert isinstance(totals, dict)
    assert all(_is_int(totals.get(name)) for name in ("types", "functions", "methods"))
    failures = fixture.get("parse_failures")
    if failures is not None:
        assert failures["engine"] in {"ast", "tree-sitter"}
        assert _is_int(failures["failed"]) and _is_int(failures["total"])
        assert failures["failed"] <= failures["total"]


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_is_canonical_json(key: str) -> None:
    text = rc.fixture_text(key)
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + chr(10)


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_has_no_test_file_entries(key: str) -> None:
    assert rc.test_file_entries(rc.load_fixture(key)) == []


@pytest.mark.parametrize("key", PILOTS)
def test_cap_is_one_number_in_extractor_chart_and_compose(key: str) -> None:
    assert rc.cap_problems(key) == []


@pytest.mark.parametrize("key", PILOTS)
def test_ingestion_slice_keeps_every_extracted_entry(key: str) -> None:
    assert rc.ingestion_slice_problems(key) == []


@pytest.mark.parametrize("key", PILOTS)
def test_surface_is_complete_and_not_truncated(key: str) -> None:
    fixture = rc.load_fixture(key)
    assert fixture["truncated"] is False
    assert fixture["type_count"] == fixture["reduced_type_count"] == len(fixture["types"])
    assert rc.chart_max_types(key) == max(fixture["type_count"], 300)
    counts = fixture["symbol_counts"]
    assert counts["types"] + counts["functions"] == fixture["type_count"]


@pytest.mark.parametrize("key", PILOTS)
def test_symbol_counts_and_counts_agree_with_the_entries(key: str) -> None:
    fixture = rc.load_fixture(key)
    entries = fixture["types"]
    functions = sum(1 for e in entries if e.get("kind") == "function")
    methods = sum(len(e.get("methods") or []) for e in entries)
    assert fixture["symbol_counts"] == {"types": len(entries) - functions, "functions": functions}
    assert fixture["counts"] == {
        "types": len(entries) - functions,
        "functions": functions,
        "methods": methods,
    }


@pytest.mark.parametrize("key", PILOTS)
def test_coverage_line_states_the_complete_surface(key: str) -> None:
    chunks = _symbol_chunks(key)
    assert chunks
    lines: set[str] = set()
    for chunk in chunks:
        found = [line for line in chunk.text.splitlines() if line.startswith("API-Coverage:")]
        assert len(found) == 1, chunk.section_title
        assert chunk.text.splitlines()[-1] == found[0]
        lines.add(found[0])
    assert len(lines) == 1, lines
    (line,) = lines
    assert _parse_coverage(line) is not None, line
    assert _coverage_is_complete(line), line


@pytest.mark.parametrize("key", PILOTS)
def test_readme_named_types_resolve(key: str) -> None:
    assert len(README_TYPES[key]) >= (4 if key == "pdf_typescript" else 5)
    names = rc.type_names(rc.load_fixture(key))
    missing = [name for name in README_TYPES[key] if name not in names]
    assert not missing, missing


@pytest.mark.parametrize("key", PILOTS)
def test_readme_recall_has_a_floor(key: str) -> None:
    recall = rc.readme_recall(key)
    assert recall >= RECALL_FLOOR[key], (recall, sorted(docs_identifiers.unresolved(key)))
    if RECALL_FLOOR[key] < 0.90:
        assert RECALL_EXCEPTION_REASON.get(key, "").strip(), f"{key} needs a stated exception"
    else:
        assert key not in RECALL_EXCEPTION_REASON


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_size_is_between_floor_and_ceiling(key: str) -> None:
    size = len(rc.fixture_text(key).encode("utf-8"))
    assert _size_problems(size, SIZE_FLOOR_BYTES[key]) == []


@pytest.mark.parametrize("key", PILOTS)
def test_member_count_has_a_floor(key: str) -> None:
    assert _member_count(rc.load_fixture(key)) >= MEMBER_FLOOR[key]


@pytest.mark.parametrize("key", PILOTS)
def test_no_symbol_chunk_is_cut_by_the_sanitiser_cap(key: str) -> None:
    assert _oversize_count(chunk.text for chunk in _symbol_chunks(key)) <= OVERSIZE_CHUNK_CEILING[key]


# -- single tests ------------------------------------------------------------------------------------------------------------


def test_pdf_go_indexes_every_exported_package_function() -> None:
    entries = rc.load_fixture("pdf_go")["types"]
    exported = [
        e
        for e in entries
        if e.get("kind") == "function" and not e.get("receiver_type") and e["name"][:1].isupper()
    ]
    assert len(exported) >= 98


def test_pdf_go_indexes_the_open_family_by_name() -> None:
    entries = rc.load_fixture("pdf_go")["types"]
    functions = {e["name"] for e in entries if e.get("kind") == "function" and not e.get("receiver_type")}
    for name in (
        "Open",
        "OpenWithPassword",
        "OpenStream",
        "OpenStreamWithPassword",
        "NewDocument",
        "NewDocumentFromFormat",
        "NewTable",
    ):
        assert name in functions, name


def test_slides_python_contains_presentation_with_members() -> None:
    entries = [e for e in rc.load_fixture("slides_python")["types"] if e.get("name") == "Presentation"]
    assert any(e.get("methods") or e.get("properties") for e in entries)


def test_pdf_python_contains_document_with_members() -> None:
    # re-extracted by TC-550 (card defect): `aspose_pdf.Document` is the package re-export of
    # `aspose_pdf.document.Document`, so the members sit on the definition the shell names via `reexport_of`.
    entries = {e["class_import"]: e for e in rc.load_fixture("pdf_python")["types"] if e.get("class_import")}
    shell = entries["aspose_pdf.Document"]
    definition = entries[shell["reexport_of"]]
    assert len(definition.get("methods") or []) + len(definition.get("properties") or []) >= 20
    security = {m["name"] for m in entries["aspose_pdf.facades.PdfFileSecurity"].get("methods") or []}
    assert {"encrypt_file", "decrypt_file", "change_password"} <= security


def test_words_python_and_3d_python_index_their_readme_classes() -> None:
    for key in ("words_python", "3d_python"):
        names = rc.type_names(rc.load_fixture(key))
        for name in README_TYPES[key]:
            assert name in names, f"{key}: README class {name} is not indexed"


def test_pdf_typescript_stays_at_the_chart_pin_and_not_the_newer_commit() -> None:
    commit = rc.load_fixture("pdf_typescript")["source_commit"]
    assert commit == rc.pinned_commit("pdf_typescript") == "155bfc7a33f0ba23fb4252b6ba201828b02a5b9d"
    assert commit != _OLD_TYPESCRIPT_COMMIT


def test_every_recall_exception_names_what_is_unresolved() -> None:
    for key, reason in RECALL_EXCEPTION_REASON.items():
        assert isinstance(reason, str) and reason.strip(), key
        unresolved = docs_identifiers.unresolved(key)
        assert any(name in reason for name in unresolved), (
            f"{key}: the reason names none of {sorted(unresolved)}"
        )


def test_recall_exceptions_are_real() -> None:
    for key in RECALL_EXCEPTION_REASON:
        assert key in PILOTS, key
        assert rc.readme_recall(key) < 0.90, f"{key} reaches 0.90 now; delete its exception"


def test_no_committed_fixture_is_truncated() -> None:
    truncated = []
    for key in docs_identifiers.PILOTS:
        fixture = rc.load_fixture(key)
        if fixture.get("truncated") is True or fixture.get("reduced_type_count") != fixture.get("type_count"):
            truncated.append(key)
    assert truncated == []


def test_the_eleven_fixtures_together_are_below_the_total_ceiling() -> None:
    total = sum(len(rc.fixture_text(key).encode("utf-8")) for key in PILOTS)
    assert total <= TOTAL_CEILING_BYTES


def test_pilots_are_the_eleven_in_a_fixed_order() -> None:
    assert len(PILOTS) == len(set(PILOTS)) == 11
    # re-pinned by TC-551: "no removed-family key in PILOTS" -> every key of PILOTS is a pilot of the chart
    assert all(
        rc.chart_pilot(key) for key in PILOTS
    )  # chart_pilot raises KeyError for a key the chart does not list
    assert set(PILOTS) | {"pdf_net", "words_net"} <= set(docs_identifiers.PILOTS)


def test_every_table_covers_exactly_the_pilots() -> None:
    for table in (README_TYPES, RECALL_FLOOR, SIZE_FLOOR_BYTES, MEMBER_FLOOR, OVERSIZE_CHUNK_CEILING):
        assert set(table) == set(PILOTS)


def test_cells_cpp_is_below_the_default_cap_and_keeps_300() -> None:
    fixture = rc.load_fixture("cells_cpp")
    assert fixture["type_count"] < 300
    assert (
        rc.chart_max_types("cells_cpp")
        == rc.compose_max_types("cells_cpp")
        == rc.recommended_cap("cells_cpp")
        == 300
    )


# -- helper tests: synthetic -----------------------------------------------------------------------------------------------


def test_coverage_line_parser_accepts_a_complete_line_without_a_functions_clause() -> None:
    assert _parse_coverage("API-Coverage: 901 of 901 types") == (901, 901, None, None)
    assert _coverage_is_complete("API-Coverage: 901 of 901 types")


def test_coverage_line_parser_accepts_a_complete_line_with_a_functions_clause() -> None:
    line = "API-Coverage: 259 of 259 types; 98 of 98 functions"
    assert _parse_coverage(line) == (259, 259, 98, 98)
    assert _coverage_is_complete(line)


def test_coverage_line_parser_accepts_the_parse_failure_clause() -> None:
    line = "API-Coverage: 10 of 10 types; 2 of 2 functions; 3 of 40 source files could not be parsed"
    assert _parse_coverage(line) == (10, 10, 2, 2)


def test_coverage_line_parser_flags_a_partial_line() -> None:
    assert _parse_coverage("API-Coverage: 299 of 300 types") == (299, 300, None, None)
    assert not _coverage_is_complete("API-Coverage: 299 of 300 types")
    assert not _coverage_is_complete("API-Coverage: 10 of 10 types; 1 of 2 functions")


def test_coverage_line_parser_rejects_a_malformed_line() -> None:
    assert _parse_coverage("API-Coverage: many types") is None
    assert not _coverage_is_complete("API-Coverage: many types")


def test_size_check_flags_a_file_above_the_ceiling_and_below_the_floor() -> None:
    assert _size_problems(SIZE_CEILING_BYTES + 1, 10)
    assert _size_problems(9, 10)
    assert _size_problems(10, 10) == []
    assert _size_problems(SIZE_CEILING_BYTES, 10) == []


def test_member_counter_counts_methods_plus_properties() -> None:
    fixture = {
        "types": [
            {"name": "A", "methods": [{"name": "m"}, {"name": "n"}], "properties": [{"name": "p"}]},
            {"name": "B", "methods": [], "properties": None},
            {"name": "C"},
        ]
    }
    assert _member_count(fixture) == 3
    assert _member_count({"types": []}) == 0


def test_oversize_counter_counts_a_24001_character_chunk_and_ignores_a_24000_one() -> None:
    assert _oversize_count(["x" * 24_001]) == 1
    assert _oversize_count(["x" * 24_000]) == 0
    assert _oversize_count(["x" * 24_001, "y" * 24_000, "z"]) == 1
    assert MAX_CHUNK_TEXT_CHARS == 24_000


def test_cap_problems_flags_the_old_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "chart_max_types", lambda key: 4393)
    monkeypatch.setattr(rc, "compose_max_types", lambda key: 4393)
    monkeypatch.setattr(rc, "recommended_cap", lambda key: 1250)
    problems = rc.cap_problems("pdf_typescript")
    assert any("chart maxTypes 4393" in p and "extractor cap 1250" in p for p in problems)


def test_cap_problems_flags_a_chart_still_at_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "chart_max_types", lambda key: 300)
    problems = rc.cap_problems("pdf_java")
    assert any("chart maxTypes 300" in p for p in problems)
    assert rc.ingestion_slice_problems("pdf_java")


def test_cap_problems_is_empty_for_a_consistent_pilot() -> None:
    assert rc.cap_problems("pdf_java") == []
