"""TC-532 (Round 3, rows RD3-001 to RD3-009): the eleven committed Python fixtures serve what the upstream says.

Every fact below was read from the upstream source at the pinned commit with ``ast`` (a clone of each
repository at the chart pin, 2026-10-19), never from the fixture: the signatures quoted in the comments are the
source's own text. The ratchet tables are the totals measured on the regenerated fixtures; a later extraction may
raise them, never lower them. Offline: the committed fixtures only.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.extraction import run_extraction
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

PILOTS = (
    "3d_python",
    "barcode_python",
    "cells_python",
    "email_python",
    "html_python",
    "note_python",
    "page_python",
    "pdf_python",
    "slides_python",
    "tex_python",
    "words_python",
)

# Measured on the regenerated fixtures: parameters with a default, keyword-only parameters, and methods marked
# static or classmethod, over every method of every entry. Ratchets.
DEFAULTS_FLOOR = {
    "3d_python": 517,
    "barcode_python": 119,
    "cells_python": 253,
    "email_python": 126,
    "html_python": 3785,
    "note_python": 138,
    "page_python": 227,
    "pdf_python": 2057,
    "slides_python": 16,
    "tex_python": 11,
    "words_python": 252,
}
KEYWORD_ONLY_FLOOR = {
    "3d_python": 0,
    "barcode_python": 20,
    "cells_python": 0,
    "email_python": 27,
    "html_python": 780,
    "note_python": 78,
    "page_python": 0,
    "pdf_python": 988,
    "slides_python": 0,
    "tex_python": 0,
    "words_python": 22,
}
STATIC_OR_CLASSMETHOD_FLOOR = {
    "3d_python": 82,
    "barcode_python": 0,
    "cells_python": 37,
    "email_python": 20,
    "html_python": 10,
    "note_python": 3,
    "page_python": 24,
    "pdf_python": 90,
    "slides_python": 3,
    "tex_python": 0,
    "words_python": 10,
}


@cache
def fixture(pilot: str) -> dict[str, Any]:
    return json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


def entries(pilot: str) -> dict[str, dict[str, Any]]:
    return {entry["class_import"]: entry for entry in fixture(pilot)["types"]}


def entry(pilot: str, class_import: str) -> dict[str, Any]:
    return entries(pilot)[class_import]


def methods(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {method["name"]: method for method in item["methods"]}


def properties(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {prop["name"]: prop for prop in item["properties"]}


def params(method: dict[str, Any]) -> list[tuple[str, str | None, str | None]]:
    return [(p["name"], p.get("default"), p.get("kind")) for p in method["params"]]


def pin(pilot: str) -> str:
    family, platform = pilot.split("_")
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    (found,) = [
        p["library"]["commit"]
        for p in data["ingestion"]["pilots"]
        if p["family"] == family and p["platform"] == platform
    ]
    return str(found)


def all_methods(pilot: str) -> list[dict[str, Any]]:
    return [m for item in fixture(pilot)["types"] for m in item.get("methods") or []]


# --------------------------------------------------------------------------- every fixture


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_fixture_is_the_current_extraction_at_the_chart_pin(pilot: str) -> None:
    data = fixture(pilot)
    assert data["source_commit"] == pin(pilot)
    assert data["extractor_version"] == run_extraction.EXTRACTOR_VERSION == 5
    assert data["language"] == "python"
    assert isinstance(data["symbol_counts"], dict) and isinstance(data["counts"], dict)
    assert data["parse_failures"]["engine"] == "ast"
    assert data["reduced_type_count"] == data["type_count"] == len(data["types"])
    assert data["truncated"] is False


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_counts_agree_with_the_entries(pilot: str) -> None:
    data = fixture(pilot)
    assert data["counts"] == run_extraction.api_counts(data["types"])
    assert data["symbol_counts"] == run_extraction.summarize_symbols(data["types"])


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_class_import_is_unique(pilot: str) -> None:
    imports = [item["class_import"] for item in fixture(pilot)["types"]]
    assert len(imports) == len(set(imports))


@pytest.mark.parametrize("pilot", PILOTS)
def test_defaults_keyword_only_marks_and_decorator_marks_are_served(pilot: str) -> None:
    found = all_methods(pilot)
    functions = [item for item in fixture(pilot)["types"] if item["kind"] == "function"]
    defaults = sum(1 for m in [*found, *functions] for p in m.get("params") or [] if "default" in p)
    keyword_only = sum(1 for m in found for p in m.get("params") or [] if p.get("kind") == "keyword_only")
    marked = sum(1 for m in found if {"static", "classmethod"} & set(m.get("modifiers") or []))
    assert defaults >= DEFAULTS_FLOOR[pilot]
    assert keyword_only >= KEYWORD_ONLY_FLOOR[pilot]
    assert marked >= STATIC_OR_CLASSMETHOD_FLOOR[pilot]


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_enum_has_members_itself_or_through_the_definition_it_re_exports(pilot: str) -> None:
    by_import = entries(pilot)
    bare: list[str] = []
    for item in fixture(pilot)["types"]:
        if item["kind"] != "enum":
            continue
        current, hops = item, 0
        while not current["enum_members"] and current.get("reexport_of") in by_import and hops < 8:
            current, hops = by_import[current["reexport_of"]], hops + 1
        if not current["enum_members"]:
            bare.append(item["class_import"])
    assert bare == []


@pytest.mark.parametrize("pilot", PILOTS)
def test_a_shadowing_exported_name_and_the_class_it_replaced_point_at_each_other(pilot: str) -> None:
    by_import = entries(pilot)
    for item in fixture(pilot)["types"]:
        if item.get("shadows"):
            replaced = by_import[item["shadows"]]
            assert replaced["shadowed_by"] == item["class_import"]
            assert item["shadows"] != item.get("reexport_of")
        if item.get("shadowed_by"):
            assert by_import[item["shadowed_by"]]["shadows"] == item["class_import"]


@pytest.mark.parametrize("pilot", PILOTS)
def test_an_override_is_served_once_with_its_own_signature(pilot: str) -> None:
    for item in fixture(pilot)["types"]:
        own = {m["name"] for m in item.get("methods") or [] if not m.get("inherited_from")}
        inherited = [m["name"] for m in item.get("methods") or [] if m.get("inherited_from")]
        assert not own & set(inherited), item["class_import"]
        assert len(inherited) == len(set(inherited)), item["class_import"]


# --------------------------------------------------------------------------- barcode (RD3-001, RD3-002)


def test_barcode_generate_has_keyword_only_encode_and_render_defaulting_to_none() -> None:
    # upstream: def generate(symbology, data, *, encode=None, render=None) -> Barcode
    for class_import in ("aspose_barcode_foss.api.generate", "aspose_barcode_foss.generate"):
        function = entry("barcode_python", class_import)
        assert function["kind"] == "function"
        assert params(function) == [
            ("symbology", None, None),
            ("data", None, None),
            ("encode", "None", "keyword_only"),
            ("render", "None", "keyword_only"),
        ]


@pytest.mark.parametrize(
    "class_import", ["aspose_barcode_foss.QrEncodeMode", "aspose_barcode_foss.options.QrEncodeMode"]
)
def test_barcode_qr_encode_mode_lists_its_five_members_built_with_auto(class_import: str) -> None:
    members = entry("barcode_python", class_import)["enum_members"]
    assert [m["name"] for m in members] == ["AUTO", "NUMERIC", "ALPHANUMERIC", "BYTE", "KANJI"]
    assert {m["value"] for m in members} == {"auto()"}


def test_barcode_error_correction_levels_and_code128_modes() -> None:
    levels = entry("barcode_python", "aspose_barcode_foss.QrErrorCorrectionLevel")["enum_members"]
    assert [m["name"] for m in levels] == ["L", "M", "Q", "H"]
    modes = entry("barcode_python", "aspose_barcode_foss.Code128EncodeMode")["enum_members"]
    assert len(modes) == 7 and modes[0]["name"] == "AUTO"


def test_barcode_eight_enum_entries_carry_thirty_six_members() -> None:
    # four enums of aspose_barcode_foss/_internal/models/options.py, each exported under two names; every member
    # is built with auto(): 7 + 2 + 5 + 4 = 18 members, listed once per export
    enums = [e for e in fixture("barcode_python")["types"] if e["kind"] == "enum"]
    assert len(enums) == 8
    assert sum(len(e["enum_members"]) for e in enums) == 36
    assert len({(e["name"], m["name"]) for e in enums for m in e["enum_members"]}) == 18


# --------------------------------------------------------------------------- cells, email, note (RD3-001)


def test_cells_workbook_constructor_and_save_default_what_the_source_defaults() -> None:
    # upstream: Workbook.__init__(self, file_path=None, password=None)
    #           Workbook.save(self, file_path, save_format=None, options=None, password=None, encryption_params=None)
    workbook = methods(entry("cells_python", "aspose.cells_foss.workbook.Workbook"))
    assert params(workbook["__init__"]) == [("file_path", "None", None), ("password", "None", None)]
    assert params(workbook["save"]) == [
        ("file_path", None, None),
        ("save_format", "None", None),
        ("options", "None", None),
        ("password", "None", None),
        ("encryption_params", "None", None),
    ]


def test_email_attachment_from_bytes_is_a_classmethod_with_keyword_only_arguments() -> None:
    # upstream: @classmethod def from_bytes(cls, filename, data, *, mime_type=None, content_id=None)
    from_bytes = methods(entry("email_python", "aspose.email_foss.msg.mapi_message.MapiAttachment"))[
        "from_bytes"
    ]
    assert from_bytes["modifiers"] == ["classmethod"]
    assert params(from_bytes) == [
        ("filename", None, None),
        ("data", None, None),
        ("mime_type", "None", "keyword_only"),
        ("content_id", "None", "keyword_only"),
    ]


def test_note_constructors_take_their_properties_by_keyword() -> None:
    # upstream: AttachedFile.__init__(self, *, FileName=None, Bytes=b'', Tags=None)
    init = methods(entry("note_python", "aspose.note.model.AttachedFile"))["__init__"]
    assert params(init) == [
        ("FileName", "None", "keyword_only"),
        ("Bytes", "b''", "keyword_only"),
        ("Tags", "None", "keyword_only"),
    ]


def test_page_image_save_options_is_a_dataclass_whose_constructor_requires_format() -> None:
    # upstream: @dataclass class ImageSaveOptions: format: str; dpi: int = 96; ...
    init = methods(entry("page_python", "aspose.page.ps.output.ImageSaveOptions"))["__init__"]
    assert params(init)[:2] == [("format", None, None), ("dpi", "96", None)]
    assert init["is_constructor"] is True


# --------------------------------------------------------------------------- 3d (RD3-003, RD3-004)


OPTION_CLASSES = [
    f"{name}{kind}Options" for name in ("Fbx", "Obj", "Stl", "Gltf", "Collada") for kind in ("Load", "Save")
]


@pytest.mark.parametrize("name", OPTION_CLASSES)
def test_the_exported_3d_option_name_is_the_empty_placeholder_not_the_working_class(name: str) -> None:
    # upstream: aspose/threed/formats/__init__.py imports the working class from .<format>.<name> and, LATER, the
    # placeholder module .<name> (class <name>: """..."""): the exported name binds the placeholder.
    exported = entry("3d_python", f"aspose.threed.formats.{name}")
    format_package = name.removesuffix("LoadOptions").removesuffix("SaveOptions").lower()
    real = f"aspose.threed.formats.{format_package}.{name}.{name}"
    assert exported["shadows"] == real
    assert exported["reexport_of"] == f"aspose.threed.formats.{name}.{name}"
    assert exported["methods"] == [] and exported["properties"] == []
    working = entry("3d_python", real)
    assert working["shadowed_by"] == f"aspose.threed.formats.{name}"
    assert "__init__" in methods(working)
    assert "shadows" not in working


def test_the_3d_working_load_options_keep_their_constructor_and_properties() -> None:
    fbx = entry("3d_python", "aspose.threed.formats.fbx.FbxLoadOptions.FbxLoadOptions")
    assert params(methods(fbx)["__init__"]) == [("format", "None", None)]
    assert {"keep_builtin_global_settings", "compatible_mode"} <= set(properties(fbx))


def test_3d_transform_builder_rotate_degree_is_the_second_definition() -> None:
    # upstream: TransformBuilder.py defines rotate_degree twice (lines 16 and 19); the second (self, rot, order) wins.
    builder = entry("3d_python", "aspose.threed.utilities.TransformBuilder.TransformBuilder")
    rotate = methods(builder)["rotate_degree"]
    assert [p["name"] for p in rotate["params"]] == ["rot", "order"]
    assert rotate["line"] == 19


def test_3d_geometry_remove_property_is_the_last_of_its_two_definitions() -> None:
    # upstream: Geometry.remove_property(self, property) at line 114, again as (self, property_name: str) at line 117.
    geometry = entry("3d_python", "aspose.threed.entities.Geometry.Geometry")
    remove = [m for m in geometry["methods"] if m["name"] == "remove_property"]
    assert len(remove) == 1
    assert [p["name"] for p in remove[0]["params"]] == ["property_name"]
    find = methods(geometry)["find_property"]
    assert [p["name"] for p in find["params"]] == [
        "property"
    ]  # the override, not A3DObject's (property_name)


def test_3d_matrix4_identity_built_by_property_is_served() -> None:
    matrix = entry("3d_python", "aspose.threed.utilities.Matrix4.Matrix4")
    assert "identity" in properties(matrix) and properties(matrix)["identity"]["writable"] is False
    assert methods(matrix)["get_identity"]["modifiers"] == ["static"]


# --------------------------------------------------------------------------- pdf (RD3-005, RD3-008)


def test_pdf_circle_annotation_inherits_through_the_private_shape_annotation() -> None:
    # upstream: CircleAnnotation(_ShapeAnnotation); _ShapeAnnotation(MarkupAnnotation); MarkupAnnotation(Annotation)
    circle = entry("pdf_python", "aspose_pdf.annotations.CircleAnnotation")
    props = properties(circle)
    assert props["interior_color"]["inherited_from"] == "aspose_pdf.annotations._ShapeAnnotation"
    assert props["opacity"]["inherited_from"] == "aspose_pdf.annotations.MarkupAnnotation"
    assert props["color"]["inherited_from"] == "aspose_pdf.annotations.Annotation"
    assert {"reply", "add_popup", "get_property"} <= set(methods(circle))
    assert len(circle["properties"]) >= 20


def test_pdf_factory_built_properties_are_served_with_their_type() -> None:
    # upstream: TextAnnotation.icon = _name_property("Name", ...) with getter -> str | None and a setter
    text = properties(entry("pdf_python", "aspose_pdf.annotations.TextAnnotation"))
    assert (text["icon"]["type"], text["icon"]["writable"]) == ("str | None", True)
    assert text["is_open"]["type"] == "bool"
    line = properties(entry("pdf_python", "aspose_pdf.annotations.LineAnnotation"))
    assert line["line_endings"]["writable"] is True  # = property(lambda ..., lambda ..., doc=...)
    assert line["start"]["writable"] is False and line["start"]["type"] == "tuple[float, float] | None"


def test_pdf_facades_carry_their_own_methods() -> None:
    security = methods(entry("pdf_python", "aspose_pdf.facades.PdfFileSecurity"))
    for name in ("encrypt_file", "decrypt_file", "change_password"):
        assert name in security and "inherited_from" not in security[name]
    assert "aspose_pdf.facades.PdfFileEditor" in entries("pdf_python")


def test_pdf_annotation_overrides_show_their_own_signature_once() -> None:
    # Annotation.reply(self, contents, *, title=None, rect=None, subtype='Text', properties=None)
    reply = methods(entry("pdf_python", "aspose_pdf.annotations.Annotation"))["reply"]
    assert params(reply)[0] == ("contents", None, None)
    assert [p for p in params(reply) if p[2] == "keyword_only"][0] == ("title", "None", "keyword_only")


# --------------------------------------------------------------------------- html, slides, words, tex


def test_html_tokenizer_state_lists_its_eighty_members_though_it_lives_in_a_private_module() -> None:
    # upstream: class TokenizerState(IntEnum) in tokenizer/_states.py, re-exported by tokenizer/__init__.py
    state = entry("html_python", "aspose_html.tokenizer.TokenizerState")
    assert state["kind"] == "enum" and len(state["enum_members"]) == 80
    assert state["enum_members"][0] == {"name": "DATA", "value": "0"}
    assert all(m["value"].isdigit() for m in state["enum_members"])


def test_slides_geometry_shape_definition_holds_the_members_it_inherits() -> None:
    # upstream: class GeometryShape(Shape, IGeometryShape, ABC): pass  (Shape imported from .Shape)
    geometry = entry("slides_python", "aspose.slides_foss.GeometryShape.GeometryShape")
    props = properties(geometry)
    assert props["fill_format"]["inherited_from"] == "aspose.slides_foss.Shape.Shape"
    assert props["shape_type"]["inherited_from"] == "aspose.slides_foss.IGeometryShape.IGeometryShape"


def test_words_markdown_enums_built_with_auto_keep_their_members_in_source_order() -> None:
    # upstream: md_import/blocks.py, class MarkdownBlockLevel(Enum): DOCUMENT = auto(); BLOCK = auto(); LEAF = ...
    level = entry("words_python", "aspose.words_foss.md_import.blocks.MarkdownBlockLevel")
    assert [(m["name"], m["value"]) for m in level["enum_members"]] == [
        ("DOCUMENT", "auto()"),
        ("BLOCK", "auto()"),
        ("LEAF", "auto()"),
        ("INLINE", "auto()"),
    ]
    block_type = entry("words_python", "aspose.words_foss.md_import.blocks.BlockType")
    assert len(block_type["enum_members"]) == 26


def test_tex_options_is_a_dataclass_with_a_generated_constructor() -> None:
    # upstream: @dataclass class TeXOptions: job_name: str = 'texput'; magnification: int = 1000; ...
    options = entry("tex_python", "aspose_tex.presentation.TeXOptions")
    init = methods(options)["__init__"]
    assert params(init)[:2] == [("job_name", "'texput'", None), ("magnification", "1000", None)]
    assert any(p[1] == "<factory>" for p in params(init))
    assert {"job_name", "magnification"} <= set(properties(options))


def test_tex_reports_the_files_that_do_not_parse() -> None:
    health = fixture("tex_python")["parse_failures"]
    assert (health["engine"], health["failed"], health["total"]) == ("ast", 11, 45)
    assert health["examples"]
    assert fixture("tex_python")["symbol_counts"]["types"] == 21


# --------------------------------------------------------------------------- the chunks


@pytest.mark.parametrize("pilot", ["barcode_python", "3d_python"])
def test_the_chunks_state_the_enum_members_and_the_shadowing(pilot: str) -> None:
    data = fixture(pilot)
    texts: dict[str, str] = {}
    for chunk in build_chunks_from_api_surface(data, title=pilot, max_types=len(data["types"])):
        match = re.search(r"^FQN: (.+)$", chunk.text, re.MULTILINE)
        assert match is not None
        texts[match.group(1)] = chunk.text
    if pilot == "barcode_python":
        lines = texts["aspose_barcode_foss.QrEncodeMode"].splitlines()
        assert "  - KANJI = auto()" in lines and "  - AUTO = auto()" in lines
    else:
        placeholder = texts["aspose.threed.formats.FbxLoadOptions"]
        assert "Shadows: aspose.threed.formats.fbx.FbxLoadOptions.FbxLoadOptions" in placeholder
        assert "empty placeholder at runtime" in placeholder
        assert "Methods:" not in placeholder
        working = texts["aspose.threed.formats.fbx.FbxLoadOptions.FbxLoadOptions"]
        assert "Shadowed-By: aspose.threed.formats.FbxLoadOptions" in working
        assert "  - __init__(format = None)" in working
