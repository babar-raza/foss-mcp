"""G3/TC-558 (REQ-G3-009, REQ-G3-008): the .NET extraction states what the C# language declares.

The oracle of every synthetic test is a hand-written C# source whose answer follows from the C# language rules (the
language specification sections are named where they decide), run through the extractor offline: no dotnet, no SDK, no
network. The real-fixture tests assert the pins the round-3 audit read from the compiled assemblies, on the fixtures
regenerated at the chart pins; the counts were also verified independently of the extractor on the cloned sources (a
tree walk that shares no code with it): 3d/net 67 public operators and conversions, pdf/net 17, 3d/net 54 public
mutable instance fields plus 1 static, pdf/net 81, pdf/net 14 delegates and 7 events, 3d/net 3 delegates.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.extraction.tree_sitter_engine.lang import csharp as cs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _extract(tmp_path: Path, sources: dict[str, str]) -> list[dict[str, Any]]:
    package = tmp_path / "src" / "Lib"
    package.mkdir(parents=True)
    for name, text in sources.items():
        (package / name).write_text(text, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("csharp"), "csharp", package, tmp_path, "lib")
    return types


def _one(tmp_path: Path, source: str) -> list[dict[str, Any]]:
    return _extract(tmp_path, {"A.cs": source})


def _type(types: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(t for t in types if t["name"] == name)


def _method_names(entry: dict[str, Any]) -> list[str]:
    return [m["name"] for m in entry["methods"]]


def _prop(entry: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in entry["properties"] if p["name"] == name)


@functools.cache
def _fixture(key: str) -> dict[str, Any]:
    return json.loads((FIXTURES / key / "api_surface.json").read_text(encoding="utf-8"))


# -- operators and conversions (RD3-050) ---------------------------------------------------------------------------------------

OPERATORS = """
namespace N
{
    public struct V
    {
        public static V operator +(V a, V b) => a;
        public static V operator -(V a) => a;
        public static bool operator ==(V a, V b) => true;
        public static implicit operator int(V v) => 1;
        public static explicit operator V(double d) => default;
        private static V operator *(V a, V b) => a;
        internal static V operator /(V a, V b) => a;
    }
}
"""


def test_a_binary_operator_is_listed_with_its_operand_and_return_types(tmp_path: Path) -> None:
    plus = next(m for m in _type(_one(tmp_path, OPERATORS), "V")["methods"] if m["name"] == "operator +")
    assert [(p["name"], p["type"]) for p in plus["params"]] == [("a", "V"), ("b", "V")]
    assert plus["return_type"] == "V"
    assert "static" in plus["modifiers"]


def test_unary_and_equality_operators_are_listed_once_each(tmp_path: Path) -> None:
    names = _method_names(_type(_one(tmp_path, OPERATORS), "V"))
    assert names.count("operator -") == 1
    assert names.count("operator ==") == 1


def test_implicit_and_explicit_conversions_are_named_by_kind_and_target(tmp_path: Path) -> None:
    v = _type(_one(tmp_path, OPERATORS), "V")
    implicit = next(m for m in v["methods"] if m["name"] == "implicit operator int")
    explicit = next(m for m in v["methods"] if m["name"] == "explicit operator V")
    assert (implicit["params"][0]["type"], implicit["return_type"]) == ("V", "int")
    assert (explicit["params"][0]["type"], explicit["return_type"]) == ("double", "V")


def test_a_non_public_operator_is_not_served(tmp_path: Path) -> None:
    # C# spec: a user-defined operator is public static; a private or internal one is not part of the API.
    names = _method_names(_type(_one(tmp_path, OPERATORS), "V"))
    assert "operator *" not in names and "operator /" not in names


def test_the_operator_switch_off_serves_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_surface, "CSHARP_INDEX_OPERATORS", False)
    names = _method_names(_type(_one(tmp_path, OPERATORS), "V"))
    assert not [n for n in names if "operator" in n]


# -- fields and events (RD3-043, RD3-054) --------------------------------------------------------------------------------------

FIELDS = """
namespace N
{
    public class Settings
    {
        public int Width, Height;
        public static int Counter;
        public readonly string Name = "x";
        public static readonly Settings Default = new Settings();
        public const int Max = 3;
        private int hidden;
        internal int Internal;
        public event System.EventHandler Changed;
        public static event System.Action<int> Progress { add { } remove { } }
        private event System.EventHandler Quiet;
    }
}
"""


def test_public_mutable_fields_are_writable_properties_with_their_type(tmp_path: Path) -> None:
    settings = _type(_one(tmp_path, FIELDS), "Settings")
    for name in ("Width", "Height"):
        prop = _prop(settings, name)
        assert (prop["type"], prop["kind"], prop["writable"]) == ("int", "field", True)


def test_a_static_mutable_field_is_marked_static(tmp_path: Path) -> None:
    counter = _prop(_type(_one(tmp_path, FIELDS), "Settings"), "Counter")
    assert counter["modifiers"] == ["static"] and counter["writable"] is True


def test_readonly_and_const_fields_stay_read_only_and_a_readonly_mark_is_added(tmp_path: Path) -> None:
    settings = _type(_one(tmp_path, FIELDS), "Settings")
    name, default, maximum = _prop(settings, "Name"), _prop(settings, "Default"), _prop(settings, "Max")
    assert name["writable"] is False and name["modifiers"] == ["readonly"]
    assert default["writable"] is False and default["modifiers"] == ["static", "readonly"]
    assert maximum["kind"] == "constant" and maximum["writable"] is False


def test_non_public_fields_and_events_are_not_served(tmp_path: Path) -> None:
    names = {p["name"] for p in _type(_one(tmp_path, FIELDS), "Settings")["properties"]}
    assert not names & {"hidden", "Internal", "Quiet"}


def test_events_are_listed_with_their_delegate_type_and_an_event_mark(tmp_path: Path) -> None:
    settings = _type(_one(tmp_path, FIELDS), "Settings")
    changed, progress = _prop(settings, "Changed"), _prop(settings, "Progress")
    assert (changed["type"], changed["kind"]) == ("System.EventHandler", "event")
    assert changed["modifiers"] == ["event"] and "writable" not in changed
    assert progress["type"] == "System.Action<int>" and progress["modifiers"] == ["event", "static"]


def test_an_interface_event_is_public(tmp_path: Path) -> None:
    source = "namespace N { public interface IWatch { event System.EventHandler Ticked; int Count(); } }"
    assert _prop(_type(_one(tmp_path, source), "IWatch"), "Ticked")["kind"] == "event"


# -- delegates (RD3-054) ----------------------------------------------------------------------------------------------------

DELEGATES = """
namespace N
{
    public delegate int Handler(string text, params int[] more);
    public delegate void Callback<T>(T item) where T : class;
    internal delegate void Hidden();
    public class Host
    {
        public delegate void Nested(int code);
    }
}
"""


def test_a_public_delegate_is_a_type_whose_invoke_has_its_signature(tmp_path: Path) -> None:
    handler = _type(_one(tmp_path, DELEGATES), "Handler")
    assert handler["kind"] == "delegate_declaration"
    (invoke,) = handler["methods"]
    assert invoke["name"] == "Invoke" and invoke["return_type"] == "int"
    assert [(p["name"], p["type"]) for p in invoke["params"]] == [
        ("text", "string"),
        ("more", "params int[]"),
    ]


def test_a_generic_delegate_keeps_its_parameters_and_constraint_and_a_nested_one_is_indexed(
    tmp_path: Path,
) -> None:
    types = _one(tmp_path, DELEGATES)
    callback = _type(types, "Callback")
    assert (callback["generics"], callback["where"]) == ("<T>", "where T : class")
    assert _type(types, "Nested")["kind"] == "delegate_declaration"


def test_an_internal_delegate_is_not_served(tmp_path: Path) -> None:
    assert "Hidden" not in {t["name"] for t in _one(tmp_path, DELEGATES)}


# -- generic parameters (RD3-044) ------------------------------------------------------------------------------------------


def test_a_generic_method_keeps_its_type_parameters_and_constraints(tmp_path: Path) -> None:
    source = """
namespace N
{
    public class Node
    {
        public T GetEntity<T>() where T : Entity => default;
        public void Plain() { }
        public U Map<T, U>(T item) where T : class, new() where U : struct => default;
    }
}
"""
    node = _type(_one(tmp_path, source), "Node")
    get = next(m for m in node["methods"] if m["name"] == "GetEntity")
    assert (get["generics"], get["where"], get["return_type"]) == ("<T>", "where T : Entity", "T")
    mapped = next(m for m in node["methods"] if m["name"] == "Map")
    assert mapped["generics"] == "<T, U>"
    assert mapped["where"] == "where T : class, new() where U : struct"
    assert "generics" not in next(m for m in node["methods"] if m["name"] == "Plain")


def test_a_generic_type_keeps_its_parameters_and_constraints(tmp_path: Path) -> None:
    source = "namespace N { public struct Box<TKey, TValue> where TKey : notnull { } }"
    box = _type(_one(tmp_path, source), "Box")
    assert (box["generics"], box["where"]) == ("<TKey, TValue>", "where TKey : notnull")


# -- using aliases (RD3-051) ------------------------------------------------------------------------------------------------

ALIASED = """
using Image = SkiaSharp.SKBitmap;
using Fmt = System.Drawing.Imaging.ImageFormat;
using Drawing2D = System.Drawing.Drawing2D;

namespace N
{
    public class Canvas : Base<Image>
    {
        public Image Load(Image source, List<Image> more, Fmt format) => source;
        public Drawing2D.Matrix Transform { get; set; }
        public Other.Image Untouched() => null;
    }
}
"""


def test_an_alias_is_resolved_in_every_signature_that_uses_it(tmp_path: Path) -> None:
    canvas = _type(_one(tmp_path, ALIASED), "Canvas")
    load = next(m for m in canvas["methods"] if m["name"] == "Load")
    assert load["return_type"] == "SkiaSharp.SKBitmap"
    assert [p["type"] for p in load["params"]] == [
        "SkiaSharp.SKBitmap",
        "List<SkiaSharp.SKBitmap>",
        "System.Drawing.Imaging.ImageFormat",
    ]
    assert canvas["bases"] == ["Base<SkiaSharp.SKBitmap>"]
    assert _prop(canvas, "Transform")["type"] == "System.Drawing.Drawing2D.Matrix"


def test_a_name_merely_ending_in_the_alias_is_not_replaced(tmp_path: Path) -> None:
    untouched = next(
        m for m in _type(_one(tmp_path, ALIASED), "Canvas")["methods"] if m["name"] == "Untouched"
    )
    assert untouched["return_type"] == "Other.Image"


FRAMEWORK_ALIAS = """
#if NETSTANDARD || NET
using Image = SkiaSharp.SKBitmap;
#else
using Image = System.Drawing.Image;
#endif

namespace N
{
    public class Doc { public void Add(Image image) { } }
}
"""


def test_an_alias_per_target_framework_is_the_target_of_the_packaged_net8_build(tmp_path: Path) -> None:
    add = next(m for m in _type(_one(tmp_path, FRAMEWORK_ALIAS), "Doc")["methods"] if m["name"] == "Add")
    assert add["params"][0]["type"] == "SkiaSharp.SKBitmap"


def test_the_else_branch_is_taken_when_the_condition_is_false_in_the_packaged_build(tmp_path: Path) -> None:
    source = FRAMEWORK_ALIAS.replace("NETSTANDARD || NET", "NET462")
    add = next(m for m in _type(_one(tmp_path, source), "Doc")["methods"] if m["name"] == "Add")
    assert add["params"][0]["type"] == "System.Drawing.Image"


def test_an_alias_whose_branch_cannot_be_decided_is_left_unresolved(tmp_path: Path) -> None:
    source = FRAMEWORK_ALIAS.replace("NETSTANDARD || NET", "SOME_CUSTOM_SYMBOL")
    add = next(m for m in _type(_one(tmp_path, source), "Doc")["methods"] if m["name"] == "Add")
    assert add["params"][0]["type"] == "Image"


# -- Foo and Foo<T> (RD3-052) ------------------------------------------------------------------------------------------------

PAIRS = """
namespace N
{
    public class Mesh { public static Mesh FromMesh(int a) => null; }
    public class Mesh<T> where T : struct { public static Mesh<T> FromMesh(int a) => null; }
    public class Pair<TKey, TValue> { }
    public class Only<T> { }
}
"""


def test_foo_and_foo_of_t_are_two_symbols_with_distinct_fqns(tmp_path: Path) -> None:
    types = _one(tmp_path, PAIRS)
    imports = sorted(t["class_import"] for t in types if t["name"].startswith("Mesh"))
    assert imports == ["N.Mesh", "N.Mesh<T>"]
    generic = next(t for t in types if t["name"] == "Mesh<T>")
    generic_from = next(m for m in generic["methods"] if m["name"] == "FromMesh")
    assert generic_from["return_type"] == "Mesh<T>" and generic["where"] == "where T : struct"
    plain_from = next(
        m for m in next(t for t in types if t["name"] == "Mesh")["methods"] if m["name"] == "FromMesh"
    )
    assert plain_from["return_type"] == "Mesh"


def test_a_generic_type_whose_name_is_unique_keeps_its_plain_name(tmp_path: Path) -> None:
    names = {t["name"] for t in _one(tmp_path, PAIRS)}
    assert {"Pair", "Only"} <= names


def test_foo_of_t_and_foo_of_t_u_are_told_apart_by_their_parameters(tmp_path: Path) -> None:
    source = "namespace N { public class Fn<T> { } public class Fn<T, U> { } }"
    assert sorted(t["name"] for t in _one(tmp_path, source)) == ["Fn<T, U>", "Fn<T>"]


# -- base lists (RD3-053) ----------------------------------------------------------------------------------------------------


def test_a_generic_base_list_is_split_at_top_level_commas_only(tmp_path: Path) -> None:
    source = """
namespace N
{
    public class Meta : IDictionary<string, XmpValue>, IEnumerable<KeyValuePair<string, int>>, IDisposable { }
}
"""
    assert _type(_one(tmp_path, source), "Meta")["bases"] == [
        "IDictionary<string, XmpValue>",
        "IEnumerable<KeyValuePair<string, int>>",
        "IDisposable",
    ]


# -- conditionally public types (RD3-055) -------------------------------------------------------------------------------------

CONDITIONAL = """
namespace N
{
#if CPLUSPLUS
    public
#else
    internal
#endif
    interface IHidden
    {
        int M();
    }

#if !CPLUSPLUS
    public
#else
    internal
#endif
    class Shown
    {
        public int Z() => 1;
    }

#if SOME_SYMBOL
    public
#else
    internal
#endif
    class Undecided { }
}
"""


def test_a_type_declared_public_only_under_a_transpilation_symbol_is_not_public(tmp_path: Path) -> None:
    names = {t["name"] for t in _one(tmp_path, CONDITIONAL)}
    assert "IHidden" not in names


def test_the_branch_the_packaged_build_compiles_decides_in_both_directions(tmp_path: Path) -> None:
    assert "Shown" in {t["name"] for t in _one(tmp_path, CONDITIONAL)}


def test_an_undecidable_condition_is_not_guessed_and_line_numbers_hold() -> None:
    resolved = cs.resolve_conditional_modifiers(CONDITIONAL.encode()).decode()
    assert resolved.count("\n") == CONDITIONAL.count("\n")
    assert "#if SOME_SYMBOL" in resolved and "#if CPLUSPLUS" not in resolved


# -- implicit constructors (RD3-047) -----------------------------------------------------------------------------------------

CONSTRUCTORS = """
namespace N
{
    public class Plain { public void Go() { } }
    public class Declared { public Declared(int a) { } }
    public class PrivateOnly { private PrivateOnly() { } public static PrivateOnly Make() => null; }
    public class StaticCtorOnly { static StaticCtorOnly() { } }
    public static class Statics { public static void Go() { } }
    public abstract class Abstract { public abstract void Go(); }
    public struct Value { public int X; }
    public interface IFace { void Go(); }
    public enum Mode { A }
    public class Primary(int size, string name) { }
}
"""


def test_a_class_without_a_declared_constructor_gets_the_implicit_default_one(tmp_path: Path) -> None:
    plain = _type(_one(tmp_path, CONSTRUCTORS), "Plain")
    first = plain["methods"][0]
    assert (first["name"], first["params"], first["is_constructor"]) == ("Plain", [], True)
    assert first["modifiers"] == ["implicit"]


def test_a_static_constructor_does_not_suppress_the_implicit_one(tmp_path: Path) -> None:
    # C# spec 15.11.5: only an instance constructor declaration removes the default constructor.
    assert _type(_one(tmp_path, CONSTRUCTORS), "StaticCtorOnly")["methods"][0]["modifiers"] == ["implicit"]


@pytest.mark.parametrize("name", ["Declared", "PrivateOnly", "Statics", "Abstract", "Value", "IFace", "Mode"])
def test_no_implicit_constructor_where_the_language_gives_none(tmp_path: Path, name: str) -> None:
    entry = _type(_one(tmp_path, CONSTRUCTORS), name)
    assert not [m for m in entry["methods"] if "implicit" in (m.get("modifiers") or [])]


def test_a_constructor_declared_in_any_part_of_a_partial_class_suppresses_it(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "A.cs": "namespace N { public partial class Doc { public void One() { } } }",
            "B.cs": "namespace N { public partial class Doc { public Doc(int a) { } } }",
        },
    )
    doc = _type(types, "Doc")
    assert not [m for m in doc["methods"] if "implicit" in (m.get("modifiers") or [])]
    assert sorted(m["name"] for m in doc["methods"]) == ["Doc", "One"]


def test_a_class_with_a_primary_constructor_serves_it_not_a_default_one(tmp_path: Path) -> None:
    ctors = [m for m in _type(_one(tmp_path, CONSTRUCTORS), "Primary")["methods"] if m["is_constructor"]]
    assert len(ctors) == 1
    assert [(p["name"], p["type"]) for p in ctors[0]["params"]] == [("size", "int"), ("name", "string")]


# -- records (RD3-050, RD3-054) -----------------------------------------------------------------------------------------------

RECORDS = """
namespace N
{
    public sealed record Header(byte[] Signature, ushort Version, string? Label = null)
    {
        public int Size => 1;
    }
    public readonly record struct Point(int X, int Y);
    public record struct Cell(int Row);
    public record Named(string Id)
    {
        public string Id { get; init; } = Id;
    }
}
"""


def test_a_positional_record_lists_its_components_constructor_and_deconstruct(tmp_path: Path) -> None:
    header = _type(_one(tmp_path, RECORDS), "Header")
    components = [p for p in header["properties"] if p.get("kind") == "record_component"]
    assert [(p["name"], p["type"]) for p in components] == [
        ("Signature", "byte[]"),
        ("Version", "ushort"),
        ("Label", "string?"),
    ]
    ctor = next(m for m in header["methods"] if m["is_constructor"])
    assert ctor["name"] == "Header" and [p["name"] for p in ctor["params"]] == [
        "Signature",
        "Version",
        "Label",
    ]
    assert ctor["params"][2]["default"] == "null"
    deconstruct = next(m for m in header["methods"] if m["name"] == "Deconstruct")
    assert [p["type"] for p in deconstruct["params"]] == ["out byte[]", "out ushort", "out string?"]
    assert deconstruct["return_type"] == "void"
    assert "Size" in {p["name"] for p in header["properties"]}


def test_a_record_has_the_equality_operators_the_compiler_declares(tmp_path: Path) -> None:
    types = _one(tmp_path, RECORDS)
    header_ops = [m for m in _type(types, "Header")["methods"] if m["name"].startswith("operator")]
    assert [(m["name"], m["return_type"], m["params"][0]["type"]) for m in header_ops] == [
        ("operator ==", "bool", "Header?"),
        ("operator !=", "bool", "Header?"),
    ]
    point_ops = [m for m in _type(types, "Point")["methods"] if m["name"].startswith("operator")]
    assert (
        point_ops[0]["params"][0]["type"] == "Point"
    )  # a record struct is a value type: no nullable operand


def test_record_component_properties_are_init_only_unless_the_record_struct_is_mutable(
    tmp_path: Path,
) -> None:
    types = _one(tmp_path, RECORDS)
    assert _prop(_type(types, "Header"), "Version")["modifiers"] == ["init"]
    assert _prop(_type(types, "Point"), "X")["modifiers"] == ["init"]  # readonly record struct
    assert "modifiers" not in _prop(_type(types, "Cell"), "Row")  # C# spec: settable on a plain record struct


def test_a_component_the_body_declares_is_not_listed_twice(tmp_path: Path) -> None:
    named = _type(_one(tmp_path, RECORDS), "Named")
    assert [p["name"] for p in named["properties"]].count("Id") == 1


# -- the regenerated fixtures: the audit's pins ------------------------------------------------------------------------------


def _declared_operators(fixture: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (t["name"], m["name"])
        for t in fixture["types"]
        for m in t["methods"]
        if ("operator " in m["name"])
        and not m.get("inherited_from")
        and "implicit" not in (m.get("modifiers") or [])
    ]


def test_3d_net_serves_its_67_operators_and_conversions_and_vector3_plus() -> None:
    fixture = _fixture("3d_net")
    assert len(_declared_operators(fixture)) == 67
    vector3 = next(t for t in fixture["types"] if t["name"] == "Vector3")
    plus = [m for m in vector3["methods"] if m["name"] == "operator +"]
    assert plus and plus[0]["return_type"] == "Vector3"
    assert [p["type"] for p in plus[0]["params"]] == ["Vector3", "Vector3"]


def test_pdf_net_serves_its_17_operators_and_conversions() -> None:
    assert len(_declared_operators(_fixture("pdf_net"))) == 17


def test_the_record_equality_operators_make_email_and_slides_4_each() -> None:
    for key in ("email_net", "slides_net"):
        operators = [
            m
            for t in _fixture(key)["types"]
            for m in t["methods"]
            if "operator " in m["name"] and not m.get("inherited_from")
        ]
        assert len(operators) >= 4, key


def test_trimesh_and_trimesh_of_t_are_two_symbols_with_distinct_fqns() -> None:
    fixture = _fixture("3d_net")
    imports = sorted(t["class_import"] for t in fixture["types"] if t["name"].startswith("TriMesh"))
    assert imports == ["Aspose.ThreeD.Entities.TriMesh", "Aspose.ThreeD.Entities.TriMesh<T>"]
    generic = next(t for t in fixture["types"] if t["name"] == "TriMesh<T>")
    assert generic["where"] == "where T : struct"


def test_node_get_entity_keeps_its_type_parameter_and_constraint() -> None:
    node = next(t for t in _fixture("3d_net")["types"] if t["class_import"] == "Aspose.ThreeD.Node")
    get = next(m for m in node["methods"] if m["name"] == "GetEntity")
    assert (get["generics"], get["where"]) == ("<T>", "where T : Entity")


def test_pdf_net_serves_the_html_save_options_fields() -> None:
    options = next(
        t for t in _fixture("pdf_net")["types"] if t["class_import"] == "Aspose.Pdf.HtmlSaveOptions"
    )
    fields = [p for p in options["properties"] if p.get("kind") == "field"]
    assert "FontSavingMode" in {p["name"] for p in fields}
    assert len(fields) >= 23


def test_3d_and_pdf_net_serve_their_public_mutable_instance_fields() -> None:
    def instance_fields(key: str) -> int:
        return sum(
            1
            for t in _fixture(key)["types"]
            for p in t["properties"]
            if p.get("kind") == "field"
            and not p.get("inherited_from")
            and "static" not in (p.get("modifiers") or [])
        )

    assert instance_fields("3d_net") == 54
    assert instance_fields("pdf_net") == 81


def test_the_sdi_image_format_alias_is_resolved_in_the_pdf_converter() -> None:
    fixture = _fixture("pdf_net")
    signature_types = [
        text
        for t in fixture["types"]
        for m in t["methods"]
        for text in (m["return_type"], *(p["type"] for p in m["params"]))
    ]
    assert not [text for text in signature_types if "SdiImageFormat" in text]
    converter = next(t for t in fixture["types"] if t["class_import"] == "Aspose.Pdf.Facades.PdfConverter")
    resolved = [
        m
        for m in converter["methods"]
        if any(p["type"] == "System.Drawing.Imaging.ImageFormat" for p in m["params"])
    ]
    assert len(resolved) >= 14


def test_the_words_image_alias_is_the_skia_bitmap_of_the_packaged_build() -> None:
    fixture = _fixture("words_net")
    builder = next(t for t in fixture["types"] if t["class_import"] == "Aspose.Words.DocumentBuilder")
    inserts = [m for m in builder["methods"] if m["name"] == "InsertImage"]
    assert any(m["params"] and m["params"][0]["type"] == "SkiaSharp.SKBitmap" for m in inserts)
    assert not [
        p
        for t in fixture["types"]
        for m in t["methods"]
        for p in m["params"]
        if p["type"] in ("Image", "Image[]")
    ]


def test_generic_base_lists_are_not_cut_at_the_comma_in_pdf_and_words() -> None:
    for key in ("pdf_net", "words_net"):
        for t in _fixture(key)["types"]:
            for base in t["bases"]:
                assert base.count("<") == base.count(">"), (key, t["name"], base)
    metadata = next(t for t in _fixture("pdf_net")["types"] if t["name"] == "Metadata")
    assert "IDictionary<string, XmpValue>" in metadata["bases"]


def test_delegates_and_events_are_indexed() -> None:
    pdf, d3 = _fixture("pdf_net"), _fixture("3d_net")
    assert sum(1 for t in pdf["types"] if t["kind"] == "delegate_declaration") == 14
    assert sum(1 for t in d3["types"] if t["kind"] == "delegate_declaration") == 3
    events = [
        p
        for t in pdf["types"]
        for p in t["properties"]
        if p.get("kind") == "event" and not p.get("inherited_from")
    ]
    assert len(events) == 7


def test_the_three_words_types_declared_under_cplusplus_public_are_not_served() -> None:
    names = {t["name"] for t in _fixture("words_net")["types"]}
    assert not names & {"INodeCollection", "DocumentPositionMovement", "NodeEnumerator"}


@pytest.mark.parametrize(
    ("key", "floor"), [("pdf_net", 137), ("words_net", 120), ("slides_net", 70), ("cells_net", 8)]
)
def test_implicit_default_constructors_are_rendered(key: str, floor: int) -> None:
    implicit = [
        t
        for t in _fixture(key)["types"]
        for m in t["methods"]
        if t["kind"] == "class_declaration"
        and m.get("is_constructor")
        and m.get("modifiers") == ["implicit"]
        and not m.get("params")
    ]
    assert len(implicit) >= floor, key


def test_email_net_records_list_components_constructor_and_deconstruct() -> None:
    fixture = _fixture("email_net")
    header = next(t for t in fixture["types"] if t["name"] == "Header")
    assert len([p for p in header["properties"] if p.get("kind") == "record_component"]) == 18
    assert {"Header", "Deconstruct"} <= {m["name"] for m in header["methods"]}
    entry = next(t for t in fixture["types"] if t["name"] == "DirectoryEntry")
    assert len([p for p in entry["properties"] if p.get("kind") == "record_component"]) == 14
