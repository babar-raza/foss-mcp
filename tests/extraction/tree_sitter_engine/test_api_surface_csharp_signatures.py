"""TC-396: C# return types, ``params`` arrays, parameter modifiers/defaults, member modifiers, indexers.

tree-sitter-c-sharp names a method's return type field ``returns`` (the extractor used to read only
``return_type``/``type`` and gave every C# method ""), parses ``params T[] x`` as loose children of the
parameter list instead of a ``parameter`` node, and has a dedicated ``indexer_declaration`` node.
Every other language's output must stay byte-identical, which the Java and Go tests pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface


def _extract(root: Path, language: str, filename: str, source: str) -> list[dict[str, Any]]:
    package = root / "src" / "Aspose.Widget"
    package.mkdir(parents=True)
    (package / filename).write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser(language), language, package, root, "widget")
    return types


def _cs(tmp_path: Path, body: str, header: str = "public class Widget") -> dict[str, Any]:
    source = f"namespace Aspose.Widget\n{{\n    {header}\n    {{\n{body}\n    }}\n}}\n"
    types = _extract(tmp_path, "csharp", "Widget.cs", source)
    return next(t for t in types if t["name"] == "Widget")


def _method(widget: dict[str, Any], name: str) -> dict[str, Any]:
    return next(m for m in widget["methods"] if m["name"] == name)


def _prop(widget: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in widget["properties"] if p["name"] == name)


# ---------------------------------------------------------------- return types


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("void", "void"),
        ("int", "int"),
        ("string", "string"),
        ("Task<List<int>>", "Task<List<int>>"),
        ("Dictionary<string, List<int>>", "Dictionary<string, List<int>>"),
        ("int?", "int?"),
        ("byte[]", "byte[]"),
        ("int[,]", "int[,]"),
        ("(int a, string b)", "(int a, string b)"),
        ("System.Collections.Generic.List<int>", "System.Collections.Generic.List<int>"),
        ("Aspose.Widget.Document", "Aspose.Widget.Document"),
    ],
)
def test_the_return_type_is_the_source_text_of_the_declared_type(
    tmp_path: Path, declared: str, expected: str
) -> None:
    widget = _cs(tmp_path, f"        public {declared} Get() {{ throw null; }}")
    assert _method(widget, "Get")["return_type"] == expected


def test_a_modified_method_still_reads_its_return_type(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public static async Task<int> Run() { return 1; }")
    assert _method(widget, "Run")["return_type"] == "Task<int>"


def test_a_constructor_keeps_an_empty_return_type(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public Widget(int size) { }")
    ctor = _method(widget, "Widget")
    assert ctor["is_constructor"] is True
    assert ctor["return_type"] == ""
    assert ctor["params"] == [{"name": "size", "type": "int"}]


# ---------------------------------------------------------------- parameters


def test_a_params_array_parameter_is_extracted_with_its_prefix(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public void Merge(string name, params Document[] documents) { }")
    assert _method(widget, "Merge")["params"] == [
        {"name": "name", "type": "string"},
        {"name": "documents", "type": "params Document[]"},
    ]


def test_a_lone_params_parameter_is_extracted(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public int Sum(params int[] values) { return 0; }")
    assert _method(widget, "Sum")["params"] == [{"name": "values", "type": "params int[]"}]


def test_ref_out_and_in_modifiers_are_kept_as_a_type_prefix(tmp_path: Path) -> None:
    widget = _cs(
        tmp_path,
        "        public bool TryGet(ref int a, out string b, in double c, int d) { b = null; return true; }",
    )
    assert _method(widget, "TryGet")["params"] == [
        {"name": "a", "type": "ref int"},
        {"name": "b", "type": "out string"},
        {"name": "c", "type": "in double"},
        {"name": "d", "type": "int"},
    ]


def test_an_extension_method_keeps_its_this_parameter(tmp_path: Path) -> None:
    source = (
        "namespace Aspose.Widget\n{\n    public static class Ext\n    {\n"
        "        public static int Twice(this int value, int times) { return value; }\n    }\n}\n"
    )
    types = _extract(tmp_path, "csharp", "Ext.cs", source)
    ext = next(t for t in types if t["name"] == "Ext")
    assert _method(ext, "Twice")["params"] == [
        {"name": "value", "type": "this int"},
        {"name": "times", "type": "int"},
    ]


def test_a_parameter_default_is_recorded_as_source_text(tmp_path: Path) -> None:
    widget = _cs(
        tmp_path,
        '        public void Save(string path = "out.pdf", int x = 3, bool flag = true, object o = null) { }',
    )
    assert _method(widget, "Save")["params"] == [
        {"name": "path", "type": "string", "default": '"out.pdf"'},
        {"name": "x", "type": "int", "default": "3"},
        {"name": "flag", "type": "bool", "default": "true"},
        {"name": "o", "type": "object", "default": "null"},
    ]


def test_a_parameter_without_a_default_has_no_default_key(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public void Save(string path, int x = 3) { }")
    params = _method(widget, "Save")["params"]
    assert "default" not in params[0]
    assert params[1]["default"] == "3"


def test_a_default_with_a_modifier_keeps_both(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public void Scale(in int factor = 2) { }")
    assert _method(widget, "Scale")["params"] == [{"name": "factor", "type": "in int", "default": "2"}]


# ---------------------------------------------------------------- member modifiers


@pytest.mark.parametrize(
    ("declaration", "name", "expected"),
    [
        ("public static Widget Create() { return null; }", "Create", ["static"]),
        ("public abstract void Draw();", "Draw", ["abstract"]),
        ("public virtual void Update() { }", "Update", ["virtual"]),
        ("public override string ToString() { return null; }", "ToString", ["override"]),
        ("public async Task Load() { }", "Load", ["async"]),
        ("public sealed override void Lock() { }", "Lock", ["sealed", "override"]),
        ("public static async Task<int> Count() { return 1; }", "Count", ["static", "async"]),
        ("static public void Backwards() { }", "Backwards", ["static"]),
        ("public new void Hide() { }", "Hide", ["new"]),
        ("public static extern void Native();", "Native", ["static", "extern"]),
    ],
)
def test_member_modifiers_are_listed_in_source_order(
    tmp_path: Path, declaration: str, name: str, expected: list[str]
) -> None:
    widget = _cs(tmp_path, f"        {declaration}", header="public abstract class Widget")
    assert _method(widget, name)["modifiers"] == expected


def test_a_plain_method_has_no_modifiers_key(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public void Plain() { }")
    assert "modifiers" not in _method(widget, "Plain")
    assert _method(widget, "Plain") == {
        "name": "Plain",
        "params": [],
        "return_type": "void",
        "doc": "",
        "line": 5,
        "is_constructor": False,
        "deprecated": False,
        "deprecated_reason": "",
        "file": "src/Aspose.Widget/Widget.cs",
    }


def test_access_modifiers_are_never_recorded(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public static void A() { }\n        public void B() { }")
    assert _method(widget, "A")["modifiers"] == ["static"]
    assert "modifiers" not in _method(widget, "B")


def test_a_static_constructor_style_constructor_and_a_property_carry_modifiers(tmp_path: Path) -> None:
    widget = _cs(
        tmp_path,
        "        public static Widget Default { get; set; }\n"
        "        public virtual string Title { get; set; }\n"
        "        public int Plain { get; set; }",
    )
    assert _prop(widget, "Default")["modifiers"] == ["static"]
    assert _prop(widget, "Title")["modifiers"] == ["virtual"]
    assert "modifiers" not in _prop(widget, "Plain")


# ---------------------------------------------------------------- indexers


def test_a_read_only_indexer_is_a_non_writable_property(tmp_path: Path) -> None:
    widget = _cs(
        tmp_path,
        "        /// <summary>The cell.</summary>\n        public Cell this[string address] { get { return null; } }",
    )
    indexer = _prop(widget, "this[string]")
    assert indexer["type"] == "Cell"
    assert indexer["writable"] is False
    assert indexer["doc"] == "The cell."
    assert indexer["params"] == [{"name": "address", "type": "string"}]


def test_an_expression_bodied_indexer_is_read_only(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public int this[int i] => i;")
    assert _prop(widget, "this[int]")["writable"] is False


def test_a_read_write_indexer_is_writable(tmp_path: Path) -> None:
    widget = _cs(
        tmp_path,
        "        public string this[int row, string col] { get { return null; } set { } }",
    )
    indexer = _prop(widget, "this[int, string]")
    assert indexer["type"] == "string"
    assert indexer["writable"] is True
    assert indexer["params"] == [{"name": "row", "type": "int"}, {"name": "col", "type": "string"}]


def test_a_non_public_indexer_is_not_extracted(tmp_path: Path) -> None:
    widget = _cs(
        tmp_path,
        "        private int this[int i] { get { return 0; } }\n"
        "        internal int this[string s] { get { return 0; } }\n"
        "        public int Count { get; set; }",
    )
    assert [p["name"] for p in widget["properties"]] == ["Count"]


def test_an_indexer_inside_a_nested_class_belongs_to_the_nested_class(tmp_path: Path) -> None:
    widget = _cs(
        tmp_path,
        "        private class Inner { public int this[int i] { get { return 0; } } }\n"
        "        public int this[long i] { get { return 0; } }",
    )
    assert [p["name"] for p in widget["properties"]] == ["this[long]"]


def test_a_virtual_indexer_records_its_modifiers(tmp_path: Path) -> None:
    widget = _cs(tmp_path, "        public virtual int this[int i] { get { return 0; } }")
    assert _prop(widget, "this[int]")["modifiers"] == ["virtual"]


# ---------------------------------------------------------------- other languages are untouched


def test_java_output_carries_the_varargs_parameter_and_the_static_mark(tmp_path: Path) -> None:
    # TC-557: renamed from test_java_output_is_byte_identical_to_before. The Java output moved on purpose:
    # ``run`` params [a] -> [a, rest: int...] (RD3-040) and no modifiers -> modifiers ["static"] (RD3-042);
    # everything else of the two entries is exactly what it was.
    source = (
        "package a.b;\npublic class Widget {\n"
        "    /** Go. */\n"
        "    public static String run(String a, int... rest) { return a; }\n"
        "    public Widget() {}\n"
        "}\n"
    )
    types = _extract(tmp_path, "java", "Widget.java", source)
    widget = next(t for t in types if t["name"] == "Widget")
    file = "src/Aspose.Widget/Widget.java"
    assert widget["methods"] == [
        {
            "name": "run",
            "params": [
                {"name": "a", "type": "String"},
                {"name": "rest", "type": "int...", "varargs": True},  # TC-557: absent -> int...
            ],
            "return_type": "String",
            "doc": "Go.",
            "line": 4,
            "is_constructor": False,
            "deprecated": False,
            "deprecated_reason": "",
            "file": file,
            "modifiers": ["static"],  # TC-557: absent -> ["static"]
        },
        {
            "name": "Widget",
            "params": [],
            "return_type": "",
            "doc": "",
            "line": 5,
            "is_constructor": True,
            "deprecated": False,
            "deprecated_reason": "",
            "file": file,
        },
    ]
    assert widget["properties"] == []


def test_go_output_is_byte_identical_to_before(tmp_path: Path) -> None:
    source = (
        "package widget\n\n"
        "type Doc struct{}\n\n"
        "// Save saves.\n"
        "func (d *Doc) Save(path, name string, rest ...int) error { return nil }\n"
    )
    types = _extract(tmp_path, "go", "doc.go", source)
    doc = next(t for t in types if t["name"] == "Doc")
    assert doc["methods"] == [
        {
            "name": "Save",
            "doc": "Save saves.",
            "params": [
                {"name": "path", "type": "string"},
                {"name": "name", "type": "string"},
                {"name": "rest", "type": "...int"},
            ],
            "return_type": "error",
            "line": 6,
            "file": "src/Aspose.Widget/doc.go",
            "is_constructor": False,
        }
    ]
    assert doc["properties"] == []
