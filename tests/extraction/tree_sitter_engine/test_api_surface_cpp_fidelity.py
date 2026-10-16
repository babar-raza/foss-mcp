"""TC-524 (REQ-G3-008, finding RST-06): the C++ extraction keeps what a caller needs to compile.

The independent pre-production review diffed the served cells/cpp and pdf/cpp symbol chunks against the
upstream public headers: constructors were missing (96 of 165 classes lost all of theirs), a reference or
pointer parameter lost its `&` or `*`, no method was marked `const` or `static`, `WorksheetCollection&
GetWorksheets()` was served `-> WorksheetCollection`, file-static helpers defined in `.cpp` files were
published as public API, and `/// <summary>` headers produced no description at all.

Every test here is offline and deterministic: it parses a short snippet written into `tmp_path`, or the
committed `tests/fixtures/cpp_sample` header sample, with the real tree-sitter-cpp grammar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "cpp_sample"


def _extract(tmp_path: Path, files: dict[str, str]) -> list[dict[str, Any]]:
    package = tmp_path / "src"
    package.mkdir()
    for relative, source in files.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", package, tmp_path, "widget")
    return types


def _by_name(types: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in types}


def _methods(types: list[dict[str, Any]], class_name: str = "Widget") -> list[dict[str, Any]]:
    return _by_name(types)[class_name]["methods"]


def _named(methods: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [m for m in methods if m["name"] == name]


def _functions(types: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in types if t.get("kind") == "function"]


def _sample_types() -> list[dict[str, Any]]:
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", SAMPLE_DIR, SAMPLE_DIR, "cells")
    return types


# --- constructors, destructors, operators --------------------------------------------------------------


def test_constructors_are_collected_with_their_parameters(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
class Widget {
public:
    Widget();
    Widget(int size, const std::string& label);
    void Run();
};
"""
        },
    )
    methods = _methods(types)
    constructors = [m for m in methods if m["is_constructor"]]
    assert [(m["name"], [p["name"] for p in m["params"]]) for m in constructors] == [
        ("Widget", []),
        ("Widget", ["size", "label"]),
    ]
    assert [m["is_constructor"] for m in _named(methods, "Run")] == [False]


def test_explicit_constructor_is_marked_explicit(tmp_path: Path) -> None:
    types = _extract(tmp_path, {"widget.h": "class Widget {\npublic:\n    explicit Widget(int size);\n};\n"})
    (ctor,) = _methods(types)
    assert ctor["is_constructor"] is True
    assert ctor["modifiers"] == ["explicit"]
    assert ctor["return_type"] == ""


def test_default_argument_is_kept_on_the_parameter(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    void Open(int flags = 0, const char* pw = nullptr);\n};\n"
        },
    )
    (method,) = _methods(types)
    assert method["params"] == [
        {"name": "flags", "type": "int", "default": "0"},
        {"name": "pw", "type": "const char*", "default": "nullptr"},
    ]


def test_reference_parameter_keeps_ampersand_and_const(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    void Save(std::vector<std::uint8_t>& out, "
            "const std::string& path, Widget&& moved);\n};\n"
        },
    )
    (method,) = _methods(types)
    assert [(p["name"], p["type"]) for p in method["params"]] == [
        ("out", "std::vector<std::uint8_t>&"),
        ("path", "const std::string&"),
        ("moved", "Widget&&"),
    ]


def test_pointer_parameter_keeps_star(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    void Fill(char* buffer, const Node *const *nodes, "
            "char*& cursor, int (*callback)(int), int values[]);\n};\n"
        },
    )
    (method,) = _methods(types)
    assert [(p["name"], p["type"]) for p in method["params"]] == [
        ("buffer", "char*"),
        ("nodes", "const Node *const *"),
        ("cursor", "char*&"),
        ("callback", "int (*)(int)"),
        ("values", "int[]"),
    ]


def test_reference_return_type_keeps_the_ampersand(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    Widget& Self();\n    const Widget& View() const;\n"
            "    char* Raw();\n    Widget Copy();\n};\n"
        },
    )
    methods = _methods(types)
    assert {m["name"]: m["return_type"] for m in methods} == {
        "Self": "Widget&",
        "View": "const Widget&",
        "Raw": "char*",
        "Copy": "Widget",
    }


def test_const_and_non_const_overloads_are_both_present_and_distinguishable(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    Items& GetItems();\n    const Items& GetItems() const;\n};\n"
        },
    )
    overloads = _named(_methods(types), "GetItems")
    assert [(m["return_type"], m.get("modifiers", [])) for m in overloads] == [
        ("Items&", []),
        ("const Items&", ["const"]),
    ]


def test_const_method_is_marked_const(tmp_path: Path) -> None:
    types = _extract(
        tmp_path, {"widget.h": "class Widget {\npublic:\n    int Size() const;\n    int Grow();\n};\n"}
    )
    methods = _methods(types)
    assert _named(methods, "Size")[0]["modifiers"] == ["const"]
    assert "modifiers" not in _named(methods, "Grow")[0]


def test_static_method_is_marked_static(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    static Widget Make(int size);\n    Widget Clone() const;\n};\n"
        },
    )
    methods = _methods(types)
    assert _named(methods, "Make")[0]["modifiers"] == ["static"]
    assert _named(methods, "Make")[0]["return_type"] == "Widget"
    assert "static" not in _named(methods, "Clone")[0]["modifiers"]


def test_noexcept_is_recorded(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    void Stop() noexcept;\n    int Peek() const noexcept;\n};\n"
        },
    )
    methods = _methods(types)
    assert _named(methods, "Stop")[0]["modifiers"] == ["noexcept"]
    assert _named(methods, "Peek")[0]["modifiers"] == ["const", "noexcept"]


def test_deleted_and_defaulted_special_members_are_marked(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
class Widget {
public:
    Widget() = default;
    Widget(const Widget&) = delete;
    Widget& operator=(const Widget&) = delete;
    Widget(Widget&&) noexcept = default;
};
"""
        },
    )
    methods = _methods(types)
    assert [(m["name"], m.get("modifiers", [])) for m in methods] == [
        ("Widget", ["defaulted"]),
        ("Widget", ["deleted"]),
        ("operator=", ["deleted"]),
        ("Widget", ["noexcept", "defaulted"]),
    ]
    assert [m["is_constructor"] for m in methods] == [True, True, False, True]


def test_destructor_is_collected_with_tilde_name(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {"widget.h": "class Widget {\npublic:\n    virtual ~Widget();\n    ~Widget() = default;\n};\n"},
    )
    methods = _methods(types)
    assert [m["name"] for m in methods] == ["~Widget", "~Widget"]
    assert [m["is_constructor"] for m in methods] == [False, False]
    assert methods[0]["modifiers"] == ["virtual"]
    assert methods[0]["return_type"] == ""


def test_operator_overloads_are_collected_as_members(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
class Widget {
public:
    bool operator==(const Widget& other) const;
    Widget& operator[](int index);
    Widget operator+(const Widget& other) const;
    explicit operator bool() const;
};
"""
        },
    )
    methods = _methods(types)
    assert [(m["name"], m["return_type"], m.get("modifiers", [])) for m in methods] == [
        ("operator==", "bool", ["const"]),
        ("operator[]", "Widget&", []),
        ("operator+", "Widget", ["const"]),
        ("operator bool", "", ["explicit", "const"]),
    ]
    assert [p["type"] for p in methods[0]["params"]] == ["const Widget&"]


def test_inline_defined_method_keeps_const_and_noexcept(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    int Count() const noexcept { return n_; }\nprivate:\n    int n_ = 0;\n};\n"
        },
    )
    (method,) = _methods(types)
    assert (method["name"], method["return_type"], method["modifiers"]) == (
        "Count",
        "int",
        ["const", "noexcept"],
    )


def test_unnamed_parameter_is_kept_with_an_empty_name(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    Widget(const Widget&) = delete;\n    void Put(int, const char*);\n};\n"
        },
    )
    methods = _methods(types)
    assert methods[0]["params"] == [{"name": "", "type": "const Widget&"}]
    assert methods[1]["params"] == [{"name": "", "type": "int"}, {"name": "", "type": "const char*"}]


def test_void_parameter_list_is_empty(tmp_path: Path) -> None:
    types = _extract(tmp_path, {"widget.h": "class Widget {\npublic:\n    int Count(void) const;\n};\n"})
    (method,) = _methods(types)
    assert method["params"] == []


# --- access ------------------------------------------------------------------------------------------


def test_public_data_member_is_still_a_property(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    std::string name;\n    int count;\nprivate:\n    int secret;\n};\n"
        },
    )
    properties = _by_name(types)["Widget"]["properties"]
    assert [(p["name"], p["type"], p["writable"]) for p in properties] == [
        ("name", "std::string", True),
        ("count", "int", True),
    ]


def test_private_members_are_not_collected(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
class Widget {
    Widget(int hidden);
    void Implicit();
public:
    Widget();
protected:
    void Guarded();
private:
    void Helper();
    ~Widget();
    int count_ = 0;
};
"""
        },
    )
    widget = _by_name(types)["Widget"]
    assert [m["name"] for m in widget["methods"]] == ["Widget"]
    assert widget["methods"][0]["params"] == []
    assert widget["properties"] == []


def test_struct_members_are_public_by_default(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "struct Point {\n    Point(int x, int y);\n    int Norm() const;\n    int x;\nprivate:\n    int hidden;\n};\n"
        },
    )
    point = _by_name(types)["Point"]
    assert [m["name"] for m in point["methods"]] == ["Point", "Norm"]
    assert point["methods"][0]["is_constructor"] is True
    assert [p["name"] for p in point["properties"]] == ["x"]


# --- free functions ----------------------------------------------------------------------------------


def test_free_function_declared_in_public_header_is_published_with_params(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "namespace Lib {\nbool IsLeap(int year);\nstd::string Upper(const std::string& s);\n}\n"
        },
    )
    functions = {f["name"]: f for f in _functions(types)}
    assert set(functions) == {"IsLeap", "Upper"}
    assert [(p["name"], p["type"]) for p in functions["IsLeap"]["params"]] == [("year", "int")]
    assert functions["IsLeap"]["return_type"] == "bool"
    assert functions["Upper"]["params"] == [{"name": "s", "type": "const std::string&"}]
    assert functions["Upper"]["return_type"] == "std::string"
    assert functions["IsLeap"]["file"] == "src/widget.h"


def test_free_function_defined_inline_in_header_is_published(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.hpp": "namespace Lib {\ninline int Clamp(int v, int lo, int hi) { return v < lo ? lo : v; }\n}\n"
        },
    )
    (clamp,) = _functions(types)
    assert clamp["name"] == "Clamp"
    assert [p["name"] for p in clamp["params"]] == ["v", "lo", "hi"]
    assert clamp["return_type"] == "int"
    assert clamp["file"].endswith(".hpp")


def test_function_defined_only_in_cpp_is_not_published(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.cpp": "namespace Lib {\nstd::string Escape(const std::string& s) { return s; }\n"
            "double Elapsed(double d) { return d; }\n}\n"
        },
    )
    assert _functions(types) == []


def test_static_function_in_cpp_is_not_published(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.cpp": "static bool IsDigit(char c) noexcept { return c == '0'; }\nnamespace {\nint Hidden(int v) { return v; }\n}\n"
        },
    )
    assert _functions(types) == []


def test_static_function_in_header_is_not_published(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {"widget.h": "namespace Lib {\nstatic int FileLocal(int v) { return v; }\nint Shared(int v);\n}\n"},
    )
    assert [f["name"] for f in _functions(types)] == ["Shared"]


def test_anonymous_namespace_function_in_header_is_not_published(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "namespace Lib {\nnamespace {\nint Anonymous(int v) { return v; }\n}\n"
            "namespace detail {\nint Detail(int v);\n}\nnamespace internal {\nint Internal(int v);\n}\n"
            "int Public(int v);\n}\n"
        },
    )
    assert [f["name"] for f in _functions(types)] == ["Public"]


def test_header_under_detail_directory_publishes_no_free_function(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "lib/detail/helpers.h": "namespace Lib {\ninline int HiddenHelper(int v) { return v; }\n}\n",
            "lib/internal/more.h": "namespace Lib {\nint MoreHelper(int v);\n}\n",
            "lib/api.h": "namespace Lib {\nint Visible(int v);\n}\n",
        },
    )
    assert [f["name"] for f in _functions(types)] == ["Visible"]


def test_overloaded_free_functions_in_one_header_all_survive(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "namespace Lib {\nstd::string ToString(int value);\nstd::string ToString(double value);\n}\n"
        },
    )
    overloads = _functions(types)
    assert [(f["name"], [p["type"] for p in f["params"]]) for f in overloads] == [
        ("ToString", ["int"]),
        ("ToString", ["double"]),
    ]


def test_free_function_class_import_carries_the_namespace(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {"widget.h": "namespace Aspose::Cells {\nint Count(int v);\n}\nint Global(int v);\n"},
    )
    functions = {f["name"]: f for f in _functions(types)}
    assert functions["Count"]["canonical_namespace"] == "Aspose::Cells"
    assert functions["Count"]["class_import"] == "Aspose::Cells::Count"
    assert "canonical_namespace" not in functions["Global"]
    assert functions["Global"]["class_import"] == "Global"


def test_out_of_line_member_definition_is_still_not_a_free_function(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": "class Widget {\npublic:\n    int Size() const;\n};\n"
            "inline int Widget::Size() const { return 1; }\n"
        },
    )
    assert _functions(types) == []
    assert [m["name"] for m in _methods(types)] == ["Size"]


# --- doc comments --------------------------------------------------------------------------------------


def test_triple_slash_summary_documents_a_member(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
class Widget {
public:
    /// <summary>Saves the widget to a stream. More detail follows.</summary>
    void Save();
    /// Resets the widget.
    void Reset();
    /// <summary>
    /// Counts the parts.
    /// </summary>
    int Count() const;
};
"""
        },
    )
    docs = {m["name"]: m["doc"] for m in _methods(types)}
    assert docs == {
        "Save": "Saves the widget to a stream.",
        "Reset": "Resets the widget.",
        "Count": "Counts the parts.",
    }


def test_triple_slash_summary_documents_the_class(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
/// <summary>
/// Represents a widget. Entry point of the library.
/// </summary>
class Widget {
public:
    Widget();
};
"""
        },
    )
    assert _by_name(types)["Widget"]["doc"] == "Represents a widget."


def test_block_comment_doc_still_documents_a_member(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
/** A widget. */
class Widget {
public:
    /**
     * Saves the widget. Extra words.
     */
    void Save();
};
"""
        },
    )
    assert _by_name(types)["Widget"]["doc"] == "A widget."
    assert _methods(types)[0]["doc"] == "Saves the widget."


def test_blank_line_between_comment_and_declaration_gives_no_doc(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
class Widget {
public:
    /// Detached comment.

    void Save();
};
"""
        },
    )
    assert _methods(types)[0]["doc"] == ""


# --- the committed sample ------------------------------------------------------------------------------


def test_reviewer_cases_on_the_committed_cpp_sample() -> None:
    types = _sample_types()
    workbook = _by_name(types)["Workbook"]
    assert workbook["doc"] == "Represents an Excel workbook."
    methods = workbook["methods"]

    def signature(method: dict[str, Any]) -> tuple[Any, ...]:
        params = [
            (p["name"], p["type"], p.get("default")) if "default" in p else (p["name"], p["type"])
            for p in method["params"]
        ]
        return (method["name"], params, method["return_type"], method.get("modifiers", []))

    assert [signature(m) for m in methods] == [
        ("Workbook", [], "", []),
        ("Workbook", [("path", "std::string_view")], "", ["explicit"]),
        ("Workbook", [("data", "const std::vector<std::uint8_t>&"), ("flags", "int", "0")], "", []),
        ("Workbook", [("", "const Workbook&")], "", ["deleted"]),
        ("operator=", [("", "const Workbook&")], "Workbook&", ["deleted"]),
        ("Workbook", [("", "Workbook&&")], "", ["noexcept", "defaulted"]),
        ("~Workbook", [], "", ["virtual"]),
        (
            "Save",
            [("stream", "std::vector<std::uint8_t>&"), ("format", "SaveFormat")],
            "void",
            ["const"],
        ),
        ("GetWorksheets", [], "WorksheetCollection&", []),
        ("GetWorksheets", [], "const WorksheetCollection&", ["const"]),
        (
            "Open",
            [("path", "const std::string&"), ("password", "const char*", "nullptr")],
            "Workbook",
            ["static", "noexcept"],
        ),
        ("operator==", [("other", "const Workbook&")], "bool", ["const"]),
        ("operator[]", [("index", "int")], "Workbook&", []),
        ("Count", [], "int", ["const", "noexcept"]),
    ]
    assert len(methods) == 14
    assert sum(1 for m in methods if m["is_constructor"]) == 5
    assert sum(1 for m in methods if "static" in m.get("modifiers", [])) == 1
    assert sum(1 for m in methods if "const" in m.get("modifiers", [])) == 4
    assert [(p["name"], p["type"]) for p in workbook["properties"]] == [("name", "std::string")]
    assert not {"Helper", "count_"} & {m["name"] for m in methods}
    assert not {"Helper", "count_"} & {p["name"] for p in workbook["properties"]}

    docs = {(m["name"], len(m["params"])): m["doc"] for m in methods}
    assert docs[("Workbook", 0)] == "Creates an empty workbook."
    assert docs[("Save", 2)] == "Saves the workbook to a stream."

    functions = _functions(types)
    assert sorted((f["name"], [p["type"] for p in f["params"]]) for f in functions) == [
        ("Clamp", ["int", "int", "int"]),
        ("IsLeapYear", ["int"]),
        ("ToString", ["double"]),
        ("ToString", ["int"]),
        ("ToUpper", ["const std::string&"]),
    ]
    by_name = {f["name"]: f for f in functions}
    assert by_name["IsLeapYear"]["doc"] == "Returns true for a leap year."
    assert by_name["IsLeapYear"]["return_type"] == "bool"
    assert by_name["ToUpper"]["return_type"] == "std::string"
    assert by_name["Clamp"]["return_type"] == "int"
    for function in functions:
        assert function["file"].endswith(".h")
        assert function["class_import"] == f"Aspose::Cells::{function['name']}"
        assert function["canonical_namespace"] == "Aspose::Cells"
    names = {t["name"] for t in types}
    assert (
        not {
            "HiddenHelper",
            "FileLocal",
            "AnonymousHelper",
            "GetElapsedMinutes",
            "IsNumericFormatChar",
            "EscapeTextContent",
        }
        & names
    )
    assert sum(1 for f in functions if f["name"] == "IsLeapYear") == 1


# --- guards and other languages ------------------------------------------------------------------------


def test_getter_synthesis_skips_constructors_operators_and_statics() -> None:
    methods = [
        {"name": "Widget", "params": [], "return_type": "Widget", "line": 1, "is_constructor": True},
        {"name": "~Widget", "params": [], "return_type": "int", "line": 2, "is_constructor": False},
        {"name": "operator*", "params": [], "return_type": "int", "line": 3, "is_constructor": False},
        {
            "name": "Instance",
            "params": [],
            "return_type": "Widget",
            "line": 4,
            "is_constructor": False,
            "modifiers": ["static"],
        },
        {"name": "size", "params": [], "return_type": "int", "line": 5, "is_constructor": False},
    ]
    properties, _claims = api_surface._synthesize_cpp_properties(methods, "Widget", "widget.h", "widget")
    assert [p["name"] for p in properties] == ["size"]


def test_subclass_still_does_not_inherit_base_constructors(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {
            "widget.h": """
class Base {
public:
    Base();
    Base(int seed);
    virtual ~Base();
    void Run();
};
class Child : public Base {
public:
    Child();
    void Extra();
};
"""
        },
    )
    base_ctors = [m for m in _methods(types, "Base") if m["is_constructor"]]
    assert len(base_ctors) == 2
    child = _methods(types, "Child")
    assert [(m["name"], m.get("inherited_from")) for m in child] == [
        ("Child", None),
        ("Extra", None),
        ("Run", "Base"),
    ]


def test_csharp_snippet_output_is_unchanged(tmp_path: Path) -> None:
    source = """
namespace Demo {
    public class Greeter {
        public Greeter(string name, int times = 2) { }
        public string Name { get; set; }
        public static int Count(ref int total, params string[] parts) { return 0; }
        public void Say(string text, bool loud) { }
    }
}
"""
    package = tmp_path / "src"
    package.mkdir()
    (package / "Greeter.cs").write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("csharp"), "csharp", package, tmp_path, "demo")
    (greeter,) = types
    # Copied from running the unmodified extractor before TC-524.
    assert [
        (m["name"], [(p["name"], p["type"]) for p in m["params"]], m["return_type"])
        for m in greeter["methods"]
    ] == [
        ("Greeter", [("name", "string"), ("times", "int")], ""),
        ("Count", [("total", "ref int"), ("parts", "params string[]")], "int"),
        ("Say", [("text", "string"), ("loud", "bool")], "void"),
    ]
    assert [(p["name"], p["type"]) for p in greeter["properties"]] == [("Name", "string")]


def test_java_snippet_output_is_unchanged(tmp_path: Path) -> None:
    source = """
package demo;
public class Greeter {
    public Greeter(String name, int times) { }
    public static int count(int[] values, final String label) { return 0; }
    public String getName() { return null; }
    public void say(String text, boolean loud) { }
}
"""
    package = tmp_path / "src"
    package.mkdir()
    (package / "Greeter.java").write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("java"), "java", package, tmp_path, "demo")
    (greeter,) = types
    # Copied from running the unmodified extractor before TC-524.
    assert [
        (m["name"], [(p["name"], p["type"]) for p in m["params"]], m["return_type"])
        for m in greeter["methods"]
    ] == [
        ("Greeter", [("name", "String"), ("times", "int")], ""),
        ("count", [("values", "int[]"), ("label", "String")], "int"),
        ("getName", [], "String"),
        ("say", [("text", "String"), ("loud", "boolean")], "void"),
    ]
    assert [(p["name"], p["type"]) for p in greeter["properties"]] == [("name", "String")]
