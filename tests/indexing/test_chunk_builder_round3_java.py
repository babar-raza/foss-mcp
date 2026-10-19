"""G3/TC-557 (REQ-G3-009, rows RD3-040 to RD3-047): the chunk renderer serves what the Java extraction now records.

The extraction records a Java member's ``throws`` list, its ``modifiers`` (``static``, ``implicit``), the ``generics`` of a
generic method, a varargs parameter typed ``T...``, a public mutable field as a writable property and a record's components.
These tests prove each reaches the served text, and that ``chunk_builder.JAVA_THROWS_RENDERED`` is the one switch for ``throws``.
They are offline: synthetic entries for the renderer's rules, and the committed fixtures of the four Java pilots for the real
signatures (facts read from the pinned upstream sources; see tests/extraction/test_round3_java_fidelity.py).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing import chunk_builder
from foss_mcp.indexing.chunk_builder import _format_type_text, _method_line, _property_line

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@functools.cache
def _fixture(family: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{family}_java" / "api_surface.json").read_text(encoding="utf-8"))


def _text_of(family: str, class_import: str) -> str:
    entry = next(t for t in _fixture(family)["types"] if t["class_import"] == class_import)
    return _format_type_text(entry)


def _lines(text: str) -> list[str]:
    return text.split("\n")


# -- the synthetic rules ---------------------------------------------------------------------------------------------------------


def test_the_throws_clause_follows_the_return_type() -> None:
    method = {
        "name": "save",
        "params": [{"name": "path", "type": "String"}],
        "return_type": "void",
        "throws": ["IOException"],
    }
    assert _method_line(method) == "  - save(path: String) -> void throws IOException"


def test_several_exceptions_are_joined_in_source_order() -> None:
    method = {
        "name": "load",
        "params": [],
        "return_type": "Document",
        "throws": ["IOException", "SAXException"],
    }
    assert _method_line(method) == "  - load() -> Document throws IOException, SAXException"


def test_a_constructor_renders_its_throws_without_a_return_type() -> None:
    constructor = {
        "name": "Reader",
        "params": [{"name": "path", "type": "String"}],
        "return_type": "",
        "is_constructor": True,
        "throws": ["IOException"],
    }
    assert _method_line(constructor) == "  - Reader(path: String) throws IOException"


def test_a_method_without_throws_renders_exactly_as_before() -> None:
    plain = {"name": "size", "params": [], "return_type": "int"}
    assert _method_line(plain) == "  - size() -> int"
    assert _method_line({**plain, "throws": []}) == "  - size() -> int"
    assert _method_line({**plain, "throws": None}) == "  - size() -> int"


def test_the_throws_switch_is_the_one_constant_and_turns_the_clause_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(chunk_builder.__file__).read_text(encoding="utf-8")
    assert source.count("JAVA_THROWS_RENDERED = True") == 1
    method = {"name": "save", "params": [], "return_type": "void", "throws": ["IOException"]}
    monkeypatch.setattr(chunk_builder, "JAVA_THROWS_RENDERED", False)
    assert _method_line(method) == "  - save() -> void"


def test_a_varargs_parameter_renders_its_three_dots() -> None:
    method = {
        "name": "fromPoints",
        "params": [{"name": "points", "type": "Vector3...", "varargs": True}],
        "return_type": "Line",
        "modifiers": ["static"],
    }
    assert _method_line(method) == "  - fromPoints(points: Vector3...) -> Line [static]"


def test_static_generics_and_throws_keep_a_fixed_order_and_the_name_ends_at_the_parenthesis() -> None:
    method = {
        "name": "pick",
        "params": [{"name": "key", "type": "K"}],
        "return_type": "V",
        "throws": ["IOException"],
        "modifiers": ["static"],
        "generics": "<K, V extends List<K>>",
    }
    line = _method_line(method)
    assert line == "  - pick(key: K) -> V throws IOException [static]  [generic <K, V extends List<K>>]"
    assert line.split("(")[0] == "  - pick"


def test_the_implicit_default_constructor_is_marked() -> None:
    constructor = {
        "name": "Options",
        "params": [],
        "return_type": "",
        "is_constructor": True,
        "modifiers": ["implicit"],
    }
    assert _method_line(constructor) == "  - Options() [implicit]"


def test_a_public_mutable_field_renders_as_a_writable_property_and_a_static_one_is_marked() -> None:
    assert _property_line({"name": "x", "type": "double", "kind": "field", "writable": True}) == (
        "  - x: double (writable)"
    )
    assert _property_line(
        {"name": "counter", "type": "int", "kind": "field", "writable": True, "modifiers": ["static"]}
    ) == ("  - counter: int [static] (writable)")
    assert _property_line({"name": "MAX", "type": "int", "writable": False, "modifiers": ["static"]}) == (
        "  - MAX: int [static] (read-only)"
    )


def test_a_record_renders_its_components_canonical_constructor_and_accessors() -> None:
    entry = {
        "name": "Point",
        "kind": "record_declaration",
        "class_import": "p.Point",
        "methods": [
            {
                "name": "Point",
                "params": [{"name": "x", "type": "int"}, {"name": "y", "type": "int"}],
                "return_type": "",
                "is_constructor": True,
                "modifiers": ["implicit"],
            },
            {"name": "x", "params": [], "return_type": "int", "modifiers": ["implicit"]},
        ],
        "properties": [
            {"name": "x", "type": "int", "writable": False, "kind": "record_component"},
            {"name": "y", "type": "int", "writable": False, "kind": "record_component"},
        ],
    }
    lines = _lines(_format_type_text(entry))
    assert "Kind: record_declaration" in lines
    assert "  - Point(x: int, y: int) [implicit]" in lines
    assert "  - x() -> int [implicit]" in lines
    assert "  - x: int (read-only)" in lines
    assert "  - y: int (read-only)" in lines


# -- the real signatures, rendered from the committed fixtures ---------------------------------------------------------------------


def test_pdf_java_document_renders_the_ioexception_its_save_declares() -> None:
    # upstream org/aspose/pdf/Document.java line 1502: ``public void save(String filePath) throws IOException``
    lines = _lines(_text_of("pdf", "org.aspose.pdf.Document"))
    assert "  - save(filePath: String) -> void throws IOException" in lines
    # line 1153: ``public PdfDictionary getCatalog() throws IOException``
    assert "  - getCatalog() -> PdfDictionary throws IOException" in lines


def test_pdf_java_compaction_options_renders_set_pages_as_varargs_and_the_implicit_constructor() -> None:
    # upstream org/aspose/pdf/CompactionOptions.java line 39 (``int... pages``); the class declares no constructor
    lines = _lines(_text_of("pdf", "org.aspose.pdf.CompactionOptions"))
    assert "  - setPages(pages: int...) -> CompactionOptions" in lines
    assert "  - CompactionOptions() [implicit]" in lines


def test_3d_java_line_renders_from_points_as_a_static_varargs_method() -> None:
    # upstream com/aspose/threed/Line.java line 59
    lines = _lines(_text_of("3d", "com.aspose.threed.Line"))
    assert "  - fromPoints(points: Vector3...) -> Line [static]" in lines
    assert not any(line.startswith("  - fromPoints()") for line in lines)


def test_3d_java_vector3_renders_its_public_instance_fields() -> None:
    # upstream com/aspose/threed/Vector3.java lines 6-8
    lines = _lines(_text_of("3d", "com.aspose.threed.Vector3"))
    for name in ("x", "y", "z"):
        assert f"  - {name}: double (writable)" in lines


def test_slides_java_color_marks_from_argb_static_and_font_data_is_a_record() -> None:
    # upstream org/aspose/slides/foss/drawing/Color.java lines 129, 141, 151; FontData.java line 10
    color = _lines(_text_of("slides", "org.aspose.slides.foss.drawing.Color"))
    assert "  - fromArgb(argb: int) -> Color [static]" in color
    font = _lines(_text_of("slides", "org.aspose.slides.foss.FontData"))
    assert "Kind: record_declaration" in font
    assert "  - FontData(fontName: String)" in font
    assert "  - fontName: String (read-only)" in font


def test_cells_java_renders_a_declared_checked_exception() -> None:
    # upstream org/aspose/cells_foss/XlsxWorkbookSerializer.java line 486:
    # ``public static WorkbookModel load(InputStream stream, LoadOptions options, LoadDiagnostics diagnostics) throws IOException``
    lines = _lines(_text_of("cells", "org.aspose.cells_foss.XlsxWorkbookSerializer"))
    assert (
        "  - load(stream: InputStream, options: LoadOptions, diagnostics: LoadDiagnostics) -> WorkbookModel"
        " throws IOException [static]"
    ) in lines
