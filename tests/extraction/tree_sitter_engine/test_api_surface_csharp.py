"""``api_surface.extract_api_surface`` reads a C# package straight, with no facade in front.

Adapted from repository-presenter's ``test_extractor.py`` (which exercises this same engine
through a typed facade this card does not port). These assertions are made against the engine's
own raw dict output instead.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

CSHARP = """
namespace Aspose.Widget
{
    /// <summary>A widget.</summary>
    public class Widget
    {
        public string Name { get; set; }
        public void Save(string path) { }
        private void Hidden() { }
    }

    public enum Mode { Fast, Slow }

    internal class NotPublic { }
}
"""

OVERLOADED = """
namespace P
{
    public class Cell
    {
        public string GetStyle() { return null; }
        public string GetStyle(int index) { return null; }
    }
}
"""


def _package(root: Path, name: str, source: str) -> Path:
    package = root / "src" / "Aspose.Widget"
    package.mkdir(parents=True)
    (package / name).write_text(source, encoding="utf-8")
    return package


def _extract(package: Path, root: Path, family: str = "widget") -> list[dict]:
    types, *_ = api_surface.extract_api_surface(get_parser("csharp"), "csharp", package, root, family)
    return types


def test_a_public_class_carries_its_doc_and_public_members(tmp_path: Path) -> None:
    package = _package(tmp_path, "Widget.cs", CSHARP)
    types = _extract(package, tmp_path)
    widget = next(t for t in types if t["name"] == "Widget")
    assert widget["kind"] == "class_declaration"
    assert widget["doc"] == "A widget."
    assert widget["visibility"] == "public"
    assert [m["name"] for m in widget["methods"]] == ["Save"]
    assert widget["methods"][0]["params"] == [{"name": "path", "type": "string"}]
    assert [p["name"] for p in widget["properties"]] == ["Name"]


def test_a_private_method_is_not_in_the_class_members(tmp_path: Path) -> None:
    package = _package(tmp_path, "Widget.cs", CSHARP)
    types = _extract(package, tmp_path)
    widget = next(t for t in types if t["name"] == "Widget")
    assert "Hidden" not in {m["name"] for m in widget["methods"]}


def test_an_internal_top_level_class_is_absent_from_the_output(tmp_path: Path) -> None:
    package = _package(tmp_path, "Widget.cs", CSHARP)
    types = _extract(package, tmp_path)
    assert "NotPublic" not in {t["name"] for t in types}


def test_an_enum_is_extracted_with_its_members(tmp_path: Path) -> None:
    package = _package(tmp_path, "Widget.cs", CSHARP)
    types = _extract(package, tmp_path)
    mode = next(t for t in types if t["name"] == "Mode")
    assert mode["kind"] == "enum_declaration"
    assert {m["name"] for m in mode["enum_members"]} == {"Fast", "Slow"}


def test_every_type_carries_its_declaring_file_and_line(tmp_path: Path) -> None:
    package = _package(tmp_path, "Widget.cs", CSHARP)
    types = _extract(package, tmp_path)
    widget = next(t for t in types if t["name"] == "Widget")
    assert widget["file"].endswith("Widget.cs")
    assert widget["line"] == 5


def test_overloaded_methods_are_each_reported_as_their_own_entry(tmp_path: Path) -> None:
    """The engine reports both raw declarations; overload de-duplication is a caller's concern."""
    package = _package(tmp_path, "Cell.cs", OVERLOADED)
    types = _extract(package, tmp_path, family="p")
    cell = next(t for t in types if t["name"] == "Cell")
    overloads = [m for m in cell["methods"] if m["name"] == "GetStyle"]
    assert len(overloads) == 2
    assert {tuple(p["name"] for p in m["params"]) for m in overloads} == {(), ("index",)}
