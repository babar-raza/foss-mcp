"""G3/TC-559 (REQ-G3-009, rows RD3-060 and RD3-061): symbols the library declares but does not implement.

The expectations come from the rules of each language, written as small upstream-shaped snippets (a body that
only throws, a ``switch`` that refuses an enum value, a scope page that lists identifiers as unsupported), and
from the pinned upstream sources read by an independent reader (``ast`` for the python pilot). They never come
from a previous run of the extractor: a committed fixture is only checked against those facts.
"""

from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import api_surface, python_surface

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY / "tests" / "fixtures"
PHRASE = "not implemented in this build"


def _extract(root: Path, language: str, filename: str, source: str) -> list[dict[str, Any]]:
    package = root / "src" / "Aspose.Widget"
    package.mkdir(parents=True)
    (package / filename).write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser(language), language, package, root, "widget")
    return types


def _type(types: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(entry for entry in types if entry["name"] == name)


def _flags(entry: dict[str, Any]) -> dict[str, bool]:
    return {
        method["name"]: method.get("stub") is True
        for method in entry["methods"]
        if not method.get("is_constructor")
    }


def _reason(entry: dict[str, Any], name: str) -> str:
    return next(method for method in entry["methods"] if method["name"] == name)["stub_reason"]


def _python_entries(root: Path, body: str) -> dict[str, dict[str, Any]]:
    package = root / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mod.py").write_text(body, encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\n', encoding="utf-8")
    types, _unresolved = run_extraction._extract_python_surface(root)
    return {entry["class_import"]: entry for entry in types}


# ------------------------------------------------------------------------------------------- the phrase


def test_the_switch_and_the_constants_exist_with_the_mandated_values() -> None:
    assert api_surface.STUB_MARKERS_ENABLED is True
    source = (REPOSITORY / "src/foss_mcp/extraction/tree_sitter_engine/api_surface.py").read_text(
        encoding="utf-8"
    )
    assert "\nSTUB_MARKERS_ENABLED = True\n" in source
    chunk_source = (REPOSITORY / "src/foss_mcp/indexing/chunk_builder.py").read_text(encoding="utf-8")
    assert f'\nNOT_IMPLEMENTED_PHRASE = "{PHRASE}"\n' in chunk_source


# ------------------------------------------------------------------------------------------------ python


def test_python_a_body_that_only_raises_not_implemented_error_is_a_stub(tmp_path: Path) -> None:
    entries = _python_entries(
        tmp_path,
        "class Renderer:\n"
        "    def render(self, scene):\n"
        "        raise NotImplementedError\n"
        "    def save(self, path):\n"
        '        raise NotImplementedError("no exporter")\n'
        "    def documented(self):\n"
        '        """Renders."""\n'
        "        raise NotImplementedError()\n",
    )
    methods = {method["name"]: method for method in entries["pkg.mod.Renderer"]["methods"]}
    assert all(methods[name]["stub"] is True for name in ("render", "save", "documented"))
    assert methods["render"]["stub_reason"] == "raises NotImplementedError"


def test_python_an_empty_pass_or_ellipsis_body_is_a_stub(tmp_path: Path) -> None:
    entries = _python_entries(
        tmp_path,
        "class Shape:\n"
        "    def area(self):\n"
        "        pass\n"
        "    def volume(self): ...\n"
        "    def documented(self):\n"
        '        """Docs."""\n'
        "        pass\n",
    )
    methods = {method["name"]: method for method in entries["pkg.mod.Shape"]["methods"]}
    assert methods["area"]["stub"] is True and methods["area"]["stub_reason"] == "empty body (pass)"
    assert methods["volume"]["stub"] is True and methods["volume"]["stub_reason"] == "empty body (...)"
    assert methods["documented"]["stub"] is True


def test_python_working_methods_are_never_flagged(tmp_path: Path) -> None:
    entries = _python_entries(
        tmp_path,
        "class Box:\n"
        "    def area(self):\n"
        "        return 1\n"
        "    def check(self):\n"
        "        raise ValueError('bad')\n"
        "    def two(self):\n"
        "        x = 1\n"
        "        raise NotImplementedError\n"
        "    def doc_only(self):\n"
        '        """Only a docstring."""\n'
        "    def guarded(self):\n"
        "        if True:\n"
        "            raise NotImplementedError\n",
    )
    methods = entries["pkg.mod.Box"]["methods"]
    assert methods and not any("stub" in method for method in methods)


def test_python_an_abstract_method_an_overload_and_a_protocol_member_are_not_stubs(tmp_path: Path) -> None:
    entries = _python_entries(
        tmp_path,
        "import abc\n"
        "from typing import Protocol, overload\n"
        "class Base(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def run(self):\n"
        "        raise NotImplementedError\n"
        "    @overload\n"
        "    def get(self, a: int) -> int: ...\n"
        "    @overload\n"
        "    def get(self, a: str) -> str: ...\n"
        "    def get(self, a):\n"
        "        return a\n"
        "class Reader(Protocol):\n"
        "    def read(self) -> bytes: ...\n",
    )
    assert "stub" not in next(m for m in entries["pkg.mod.Base"]["methods"] if m["name"] == "run")
    assert not any("stub" in m for m in entries["pkg.mod.Base"]["methods"] if m["name"] == "get")
    assert not any("stub" in m for m in entries["pkg.mod.Reader"]["methods"])


def test_python_a_constructor_that_does_nothing_is_not_a_stub_but_one_that_raises_is(tmp_path: Path) -> None:
    entries = _python_entries(
        tmp_path,
        "class Plain:\n"
        "    def __init__(self):\n"
        "        pass\n"
        "class Texture:\n"
        "    def __init__(self, path):\n"
        "        raise NotImplementedError\n",
    )
    assert not any("stub" in m for m in entries["pkg.mod.Plain"]["methods"])
    assert entries["pkg.mod.Texture"]["methods"][0]["stub"] is True


def test_python_a_property_getter_and_a_module_function_can_be_stubs(tmp_path: Path) -> None:
    entries = _python_entries(
        tmp_path,
        "class Scene:\n"
        "    @property\n"
        "    def frames(self):\n"
        "        raise NotImplementedError\n"
        "    @property\n"
        "    def name(self):\n"
        "        return 'x'\n"
        "def convert(path):\n"
        "    pass\n"
        "def working(path):\n"
        "    return path\n",
    )
    props = {prop["name"]: prop for prop in entries["pkg.mod.Scene"]["properties"]}
    assert props["frames"]["stub"] is True and "stub" not in props["name"]
    assert entries["pkg.mod.convert"]["stub"] is True and "stub" not in entries["pkg.mod.working"]


def test_python_a_flagged_member_is_inherited_flagged(tmp_path: Path) -> None:
    entries = _python_entries(
        tmp_path,
        "class Base:\n"
        "    def render(self):\n"
        "        raise NotImplementedError\n"
        "class Child(Base):\n"
        "    def other(self):\n"
        "        return 1\n",
    )
    inherited = next(m for m in entries["pkg.mod.Child"]["methods"] if m["name"] == "render")
    assert inherited["inherited_from"] == "pkg.mod.Base" and inherited["stub"] is True


def test_python_stub_reason_is_the_one_python_surface_states() -> None:
    assert python_surface.STUB_RAISES_REASON == "raises NotImplementedError"
    function = ast.parse("def f(self):\n    raise NotImplementedError('x')\n").body[0]
    assert isinstance(function, ast.FunctionDef)
    assert python_surface.stub_reason(function) == python_surface.STUB_RAISES_REASON


# --------------------------------------------------------------------------------------------------- C#


def test_csharp_a_block_body_that_only_throws_not_implemented_is_a_stub(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        "csharp",
        "Scene.cs",
        "namespace Aspose.Widget\n{\n    public class Scene\n    {\n"
        '        public void Render(int camera) { throw new NotImplementedException("rendering"); }\n'
        "        public int Count() { return 3; }\n"
        "        public void Save(string path) { Check(path); throw new NotSupportedException(); }\n"
        "    }\n}\n",
    )
    scene = _type(types, "Scene")
    assert _flags(scene) == {"Render": True, "Count": False, "Save": False}
    assert _reason(scene, "Render") == 'throws NotImplementedException: "rendering"'


def test_csharp_an_expression_body_that_throws_is_a_stub(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        "csharp",
        "Mesh.cs",
        "namespace Aspose.Widget\n{\n    public class Mesh\n    {\n"
        "        public int Evaluate() => throw new NotImplementedException();\n"
        "        public int Area() => 4;\n"
        "    }\n}\n",
    )
    assert _flags(_type(types, "Mesh")) == {"Evaluate": True, "Area": False}


def test_csharp_a_domain_exception_whose_message_says_not_implemented_is_a_stub(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        "csharp",
        "Exporter.cs",
        "namespace Aspose.Widget\n{\n    public class Exporter\n    {\n"
        '        public void Export() { throw new ExportException("FBX export is not implemented in FOSS version"); }\n'
        '        public void Fail() { throw new ArgumentException("bad argument"); }\n'
        "    }\n}\n",
    )
    exporter = _type(types, "Exporter")
    assert _flags(exporter) == {"Export": True, "Fail": False}
    assert _reason(exporter, "Export").startswith("throws ExportException")


def test_csharp_an_abstract_member_and_an_interface_member_are_not_stubs(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        "csharp",
        "Contract.cs",
        "namespace Aspose.Widget\n{\n    public abstract class Contract\n    {\n"
        "        public abstract void Run();\n"
        "    }\n    public interface IContract\n    {\n        void Run();\n    }\n}\n",
    )
    assert not any(m.get("stub") for m in _type(types, "Contract")["methods"])
    assert not any(m.get("stub") for m in _type(types, "IContract")["methods"])


# -------------------------------------------------------------------------------------------------- Java


def test_java_a_body_that_only_throws_unsupported_operation_is_a_stub(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        "java",
        "Exporter.java",
        "package aspose.widget;\npublic class Exporter {\n"
        "    public void save(String path) { throw new UnsupportedOperationException(); }\n"
        "    public void load(String path) { read(path); }\n"
        '    public void other() { throw new UnsupportedOperationException("not supported"); }\n'
        "}\n",
    )
    exporter = _type(types, "Exporter")
    assert _flags(exporter) == {"save": True, "load": False, "other": True}
    assert _reason(exporter, "save") == "throws UnsupportedOperationException"


# --------------------------------------------------------------------------------------------------- C++


def test_cpp_an_inline_body_that_only_throws_logic_error_not_implemented_is_a_stub(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        "cpp",
        "Widget.h",
        "#pragma once\n#include <stdexcept>\nnamespace widget {\nclass Widget {\npublic:\n"
        '    void Draw() { throw std::logic_error("not implemented"); }\n'
        "    int Size() const { return 1; }\n"
        "    void Later();\n"
        "};\n}\n",
    )
    flags = _flags(_type(types, "Widget"))
    assert flags["Draw"] is True and flags["Size"] is False and flags["Later"] is False
    assert _reason(_type(types, "Widget"), "Draw") == 'throws std::logic_error: "not implemented"'


# ------------------------------------------------------------------------------------------- TypeScript


def test_typescript_a_body_that_only_throws_error_not_implemented_is_a_stub(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "widget"}\n', encoding="utf-8")
    package = tmp_path / "src"
    package.mkdir()
    (package / "widget.ts").write_text(
        "export class Widget {\n"
        "    draw(): void { throw new Error('not implemented'); }\n"
        "    size(): number { return 1; }\n"
        "}\n",
        encoding="utf-8",
    )
    types, *_ = api_surface.extract_api_surface(
        get_parser("typescript"), "typescript", package, tmp_path, "widget"
    )
    widget = _type(types, "Widget")
    assert _flags(widget) == {"draw": True, "size": False}
    assert _reason(widget, "draw") == 'throws Error: "not implemented"'


# ---------------------------------------------------------------------------------------- the switch


def test_the_switch_turns_every_language_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(api_surface, "STUB_MARKERS_ENABLED", False)
    types = _extract(
        tmp_path / "cs",
        "csharp",
        "Scene.cs",
        "namespace Aspose.Widget\n{\n    public class Scene\n    {\n"
        "        public void Render() { throw new NotImplementedException(); }\n"
        "    }\n}\n",
    )
    assert not any("stub" in method for method in _type(types, "Scene")["methods"])
    entries = _python_entries(
        tmp_path / "py", "class A:\n    def f(self):\n        raise NotImplementedError\n"
    )
    assert not any("stub" in method for method in entries["pkg.mod.A"]["methods"])
    page = tmp_path / "page.md"
    page.write_text(
        "## Scope and Limitations\n\n- `Scene.Render` throws `NotImplementedException`.\n", encoding="utf-8"
    )
    scene = {"name": "Scene", "kind": "class", "methods": [{"name": "Render"}], "properties": []}
    assert run_extraction.mark_documented_unsupported([scene], page) == []
    assert "stub" not in scene["methods"][0]


# ------------------------------------------------------ the scope documentation (acceptance c, RD3-061)


def _page(tmp_path: Path, markdown: str) -> Path:
    page = tmp_path / "_index.md"
    page.write_text(markdown, encoding="utf-8")
    return page


def _entry(name: str, kind: str = "class", methods: tuple[str, ...] = (), enum: tuple[str, ...] = ()) -> dict:
    return {
        "name": name,
        "kind": kind,
        "class_import": f"lib.{name}",
        "methods": [{"name": method} for method in methods],
        "properties": [],
        "enum_members": [{"name": member, "value": ""} for member in enum],
    }


def _marked(entries: list[dict]) -> set[str]:
    found: set[str] = set()
    for entry in entries:
        if entry.get("stub"):
            found.add(entry["name"])
        for method in entry["methods"]:
            if method.get("stub"):
                found.add(f"{entry['name']}.{method['name']}")
        for member in entry["enum_members"]:
            if member.get("stub"):
                found.add(f"{entry['name']}.{member['name']}")
    return found


def test_a_member_and_a_type_the_scope_page_says_throw_not_implemented_are_marked(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "## Scope and Limitations\n\n"
        "- `Scene.Render` and `RenderFactory` throw `NotImplementedException` in this build.\n"
        "- `Scene.Open` works for every format.\n",
    )
    entries = [_entry("Scene", methods=("Render", "Open")), _entry("RenderFactory"), _entry("Other")]
    run_extraction.mark_documented_unsupported(entries, page)
    assert _marked(entries) == {"Scene.Render", "RenderFactory"}
    assert entries[0]["methods"][0]["stub_reason"] == run_extraction.DOC_UNSUPPORTED_REASON


def test_only_an_exact_identifier_matches_never_a_substring(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "## Limitations\n\n- `Render` is not implemented.\n- `PdfSaveOptions` throw `NotImplementedError`.\n",
    )
    entries = [_entry("Renderer", methods=("RenderAll",)), _entry("PdfSaveOptionsBuilder"), _entry("Render")]
    run_extraction.mark_documented_unsupported(entries, page)
    assert _marked(entries) == {"Render"}


def test_a_name_the_artifact_does_not_define_marks_nothing_and_removes_nothing(tmp_path: Path) -> None:
    page = _page(tmp_path, "## Limitations\n\n- `Ghost.Method` and `Phantom` throw `NotImplementedError`.\n")
    entries = [_entry("Real", methods=("Method",))]
    before = json.dumps(entries)
    assert run_extraction.mark_documented_unsupported(entries, page) == []
    assert json.dumps(entries) == before


def test_the_values_a_save_refuses_are_marked_and_the_ones_it_writes_are_not(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "## Scope and Limitations\n\n"
        "- **Save formats**: `SaveFormat` declares 5 values. `save()` writes two of them - `PPTX` and `PPSX`,\n"
        "  each a different package, and\n"
        "  raises `UnsupportedOperationException` for the other 3 (`PDF`, `XPS`, `TIFF`).\n",
    )
    entries = [_entry("SaveFormat", "enum", enum=("PPTX", "PPSX", "PDF", "XPS", "TIFF"))]
    run_extraction.mark_documented_unsupported(entries, page)
    assert _marked(entries) == {"SaveFormat.PDF", "SaveFormat.XPS", "SaveFormat.TIFF"}


def test_every_other_value_enumeration_matches_an_enum_value_that_differs_only_by_case(
    tmp_path: Path,
) -> None:
    page = _page(
        tmp_path,
        "## Scope and Limitations\n\n"
        "- Every other `SaveFormat` value (`Pdf`, `Html`) throws `std::invalid_argument`:\n",
    )
    entries = [_entry("SaveFormat", "enum", enum=("PPTX", "PDF", "HTML"))]
    run_extraction.mark_documented_unsupported(entries, page)
    assert _marked(entries) == {"SaveFormat.PDF", "SaveFormat.HTML"}


def test_a_refused_column_of_a_table_in_a_scope_section_is_marked(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "### Save formats\n\n`SaveFormat` declares 4 values.\n\n| Written | Refused |\n| `Pptx`, `Ppsx` | `Pdf`, `Xps` |\n",
    )
    entries = [_entry("SaveFormat", "enum", enum=("Pptx", "Ppsx", "Pdf", "Xps"))]
    run_extraction.mark_documented_unsupported(entries, page)
    assert _marked(entries) == {"SaveFormat.Pdf", "SaveFormat.Xps"}


def test_a_statement_that_is_not_in_a_scope_section_marks_nothing(tmp_path: Path) -> None:
    page = _page(
        tmp_path, "## Key Capabilities\n\n- `Scene.Render` throws `NotImplementedException` sometimes.\n"
    )
    entries = [_entry("Scene", methods=("Render",))]
    assert run_extraction.mark_documented_unsupported(entries, page) == []


def test_a_class_named_only_to_qualify_a_member_is_not_marked_and_an_enum_is_never_marked(
    tmp_path: Path,
) -> None:
    page = _page(
        tmp_path,
        "## Limitations\n\n- `Mesh.DoBoolean` and `SaveFormat` throw `NotImplementedException`.\n",
    )
    entries = [_entry("Mesh", methods=("DoBoolean", "Area")), _entry("SaveFormat", "enum", enum=("Pdf",))]
    run_extraction.mark_documented_unsupported(entries, page)
    assert _marked(entries) == {"Mesh.DoBoolean"}


def test_a_csharp_switch_that_throws_not_supported_marks_the_enum_values_it_lists(tmp_path: Path) -> None:
    source = tmp_path / "RW" / "WriterFactory.cs"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class WriterFactory {\n  void Create(SaveFormat f) {\n    switch (f) {\n"
        "      case SaveFormat.Docx:\n        return new DocxWriter();\n"
        "      case SaveFormat.Pdf:\n#if NET\n      case Aspose.Words.SaveFormat.Html:\n#endif\n"
        '        throw new NotSupportedException("FOSS");\n'
        "    }\n  }\n}\n",
        encoding="utf-8",
    )
    entries = [_entry("SaveFormat", "enum", enum=("Docx", "Pdf", "Html"))]
    marked = run_extraction.mark_unsupported_switch_cases(entries, tmp_path)
    assert sorted(marked) == ["SaveFormat.Html", "SaveFormat.Pdf"]
    assert _marked(entries) == {"SaveFormat.Pdf", "SaveFormat.Html"}
    assert "NotSupportedException" in entries[0]["enum_members"][1]["stub_reason"]


# ---------------------------------------------------------------- the regenerated fixtures (real data)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name / "api_surface.json").read_text(encoding="utf-8"))


def _find(fixture: dict[str, Any], class_import: str) -> dict[str, Any]:
    return next(entry for entry in fixture["types"] if entry.get("class_import") == class_import)


def test_3d_net_scene_render_overloads_carry_the_marker() -> None:
    scene = _find(_fixture("3d_net"), "Aspose.ThreeD.Scene")
    renders = [method for method in scene["methods"] if method["name"] == "Render"]
    assert len(renders) == 5 and all(method["stub"] is True for method in renders)
    assert all("NotImplementedException" in method["stub_reason"] for method in renders)
    assert not any(m.get("stub") for m in scene["methods"] if m["name"] in ("Open", "Save"))


def test_3d_net_polygon_modifier_and_pdf_format_carry_the_marker() -> None:
    fixture = _fixture("3d_net")
    modifier = _find(fixture, "Aspose.ThreeD.Entities.PolygonModifier")
    pdf = _find(fixture, "Aspose.ThreeD.Formats.PdfFormat")
    assert modifier["stub"] is True and pdf["stub"] is True
    assert any(m["name"] == "Triangulate" and m["stub"] for m in modifier["methods"])
    assert any(m["name"] == "ExtractScene" and m["stub"] for m in pdf["methods"])


def test_pdf_python_html_and_svg_load_options_are_named_unsupported_by_the_scope_page() -> None:
    fixture = _fixture("pdf_python")
    named = [entry for entry in fixture["types"] if entry["name"] in ("HtmlLoadOptions", "SvgLoadOptions")]
    assert {entry["name"] for entry in named} == {"HtmlLoadOptions", "SvgLoadOptions"}
    assert all(entry["stub"] is True for entry in named)
    page = FIXTURES / "furnished" / "pdf_python" / "pages" / "_index.md"
    assert "`HtmlLoadOptions`" in page.read_text(encoding="utf-8")  # the page itself is the source


def test_words_net_pdf_html_and_epub_save_formats_are_refused_by_the_writer_switch() -> None:
    save_format = _find(_fixture("words_net"), "Aspose.Words.SaveFormat")
    flagged = {member["name"] for member in save_format["enum_members"] if member.get("stub")}
    assert {"Pdf", "Html", "Epub"} <= flagged
    assert not {"Docx", "Markdown", "Text"} & flagged
    assert "NotSupportedException" in next(
        member["stub_reason"] for member in save_format["enum_members"] if member["name"] == "Pdf"
    )


@pytest.mark.parametrize(
    ("fixture", "class_import", "refused", "written"),
    [
        (
            "slides_java",
            "org.aspose.slides.foss.export.SaveFormat",
            {"PDF", "XPS", "TIFF", "HTML", "GIF", "MD"},
            {"PPTX", "PPSX", "POTX"},
        ),
        (
            "slides_net",
            "Aspose.Slides.Foss.Export.SaveFormat",
            {"Pdf", "Xps", "Tiff", "Html", "Gif", "Md"},
            {"Pptx", "Ppsx", "Potx"},
        ),
        # the cpp page names four of the refused values by name ("`Pdf`, `Html`, `Odp`, `Ppt`, image formats, ...")
        (
            "slides_cpp",
            "Aspose::Slides::Foss::SaveFormat",
            {"PDF", "HTML", "ODP", "PPT"},
            {"PPTX", "PPSX", "POTX"},
        ),
    ],
)
def test_slides_save_formats_the_scope_page_lists_as_refused_are_marked(
    fixture: str, class_import: str, refused: set[str], written: set[str]
) -> None:
    save_format = _find(_fixture(fixture), class_import)
    flagged = {member["name"] for member in save_format["enum_members"] if member.get("stub")}
    assert refused <= flagged
    assert not written & flagged


def test_cells_cpp_scope_page_makes_no_statement_so_nothing_is_flagged() -> None:
    """The cells/cpp furnished page lists nothing as unsupported and no body only throws: nothing is invented."""
    fixture = _fixture("cells_cpp")
    assert not any(
        member.get("stub")
        for entry in fixture["types"]
        for key in ("methods", "properties", "enum_members")
        for member in entry.get(key) or []
    )


def test_nothing_is_removed_from_a_regenerated_fixture() -> None:
    """Each regenerated pilot keeps every type its chart cap allows: kept equals total."""
    for name in (
        "3d_python",
        "pdf_python",
        "3d_net",
        "words_net",
        "slides_net",
        "slides_cpp",
        "slides_java",
        "cells_cpp",
    ):
        fixture = _fixture(name)
        assert fixture["reduced_type_count"] == fixture["type_count"], name
        assert fixture["truncated"] is False, name


# --------------------------------------------- 3d/python: the stub count against the upstream source


PYTHON_3D_PIN_FLAGGED_OWN_MEMBERS = (
    333  # own (not inherited) methods and properties of the fixture flagged stub
)


def _independent_stub_lines(package_root: Path) -> dict[tuple[str, int], bool]:
    """``{(file, line): is_stub}`` for every class-level function of the sources, read with ``ast`` and the
    rules of Python only: the body (after a docstring) is ``raise NotImplementedError``, ``pass`` or ``...``;
    an abstract method, an ``@overload`` and a Protocol member are not stubs; neither is a ``pass``/``...``
    ``__init__``."""
    found: dict[tuple[str, int], bool] = {}
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root).as_posix()
        if set(path.relative_to(package_root).parts) & {"tests", "test", "docs", "examples", "build", "dist"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            protocol = any(ast.unparse(base).endswith("Protocol") for base in node.bases)
            for item in node.body:
                if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                body = list(item.body)
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body = body[1:]
                stub = False
                if len(body) == 1:
                    statement = body[0]
                    if isinstance(statement, ast.Raise) and statement.exc is not None:
                        raised = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
                        stub = isinstance(raised, ast.Name) and raised.id == "NotImplementedError"
                    elif item.name != "__init__":
                        stub = isinstance(statement, ast.Pass) or (
                            isinstance(statement, ast.Expr)
                            and isinstance(statement.value, ast.Constant)
                            and statement.value.value is Ellipsis
                        )
                decorators = {ast.unparse(d) for d in item.decorator_list}
                if any("abstract" in d or "overload" in d for d in decorators) or protocol:
                    stub = False
                found[(relative, item.lineno)] = stub
    return found


def test_3d_python_flagged_own_member_count_is_pinned() -> None:
    fixture = _fixture("3d_python")
    own = [
        member
        for entry in fixture["types"]
        for key in ("methods", "properties")
        for member in entry.get(key) or []
        if member.get("stub") and not member.get("inherited_from") and member.get("file")
    ]
    assert len({(member["file"], member["line"]) for member in own}) == PYTHON_3D_PIN_FLAGGED_OWN_MEMBERS
    assert all(
        member["stub_reason"] in ("raises NotImplementedError", "empty body (pass)", "empty body (...)")
        for member in own
    )
    inherited = [
        member
        for entry in fixture["types"]
        for key in ("methods", "properties")
        for member in entry.get(key) or []
        if member.get("stub") and member.get("inherited_from")
    ]
    assert inherited, "a stub inherited from a base is flagged on the subclass too"


def _clone_3d_python(destination: Path) -> Path:
    values = yaml.safe_load((REPOSITORY / "infra/helm/foss-mcp/values.yaml").read_text(encoding="utf-8"))
    pilot = next(p for p in values["ingestion"]["pilots"] if (p["family"], p["platform"]) == ("3d", "python"))
    clone = destination / "clone"
    run_extraction._clone_at_commit(
        run_extraction.repository_url(pilot["library"]["repository"]), clone, pilot["library"]["commit"]
    )
    return clone


@pytest.mark.live
def test_3d_python_the_flagged_members_are_exactly_the_stub_bodies_the_upstream_source_has() -> None:
    """Live (network, ``FOSS_MCP_NETWORK_TESTS=1``): the pinned upstream sources read with ``ast`` independently of
    the extractor. Every member the fixture lists is flagged exactly when its function is a stub."""
    with tempfile.TemporaryDirectory(prefix="tc559-") as directory:
        clone = _clone_3d_python(Path(directory))
        stubs = _independent_stub_lines(clone)
    fixture = _fixture("3d_python")
    listed: dict[tuple[str, int], bool] = {}
    for entry in fixture["types"]:
        for key in ("methods", "properties"):
            for member in entry.get(key) or []:
                if (
                    member.get("inherited_from")
                    or not member.get("file")
                    or (member["file"], member["line"]) not in stubs
                ):
                    continue
                listed[(member["file"], member["line"])] = member.get("stub") is True
    assert len(listed) > 1000
    assert {key for key, flagged in listed.items() if flagged} == {key for key in listed if stubs[key]}
    assert sum(1 for key in listed if stubs[key]) == PYTHON_3D_PIN_FLAGGED_OWN_MEMBERS
