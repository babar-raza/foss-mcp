"""TC-534 (REQ-G3-008, REQ-G3-009, findings RD3-020 to RD3-023): the C++ extraction keeps what a caller needs.

The round-3 review diffed the served C++ pilots against the upstream headers and found four defect classes:
`static const` / `static constexpr` data members lost their qualifiers and were served writable (RD3-020),
public nested types were missing though public signatures reference them (RD3-021), implementation types were
served as API (RD3-022) and member-level fidelity had never been gated (RD3-023).

Every expectation here follows from the C++ language rules applied to a short HAND-WRITTEN header, never from
the extractor's own output. Offline and deterministic: each test parses a snippet written into `tmp_path` with
the real tree-sitter-cpp grammar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

API_SURFACE_SOURCE = Path(api_surface.__file__)


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


def _properties(types: list[dict[str, Any]], type_name: str) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in _by_name(types)[type_name]["properties"]}


def _methods(types: list[dict[str, Any]], type_name: str) -> list[dict[str, Any]]:
    return _by_name(types)[type_name]["methods"]


# --- RD3-020: static const / constexpr data members keep their qualifiers and are read-only -------------------

COLOR_HEADER = """
struct Color {
    static const Color Red;
    static constexpr int N = 3;
    int r = 0;
};
"""


def test_the_mandated_switch_is_one_line_and_on() -> None:
    assert api_surface.CPP_KEEP_STATIC_CONST_QUALIFIERS is True
    source = API_SURFACE_SOURCE.read_text(encoding="utf-8")
    assert source.count("CPP_KEEP_STATIC_CONST_QUALIFIERS = True") == 1


def test_static_const_and_constexpr_members_are_read_only_with_their_qualifiers(tmp_path: Path) -> None:
    props = _properties(_extract(tmp_path, {"color.h": COLOR_HEADER}), "Color")
    assert props["Red"]["modifiers"] == ["static", "const"]
    assert props["Red"]["writable"] is False
    assert props["Red"]["type"] == "Color"
    assert props["N"]["modifiers"] == ["static", "constexpr", "const"]  # constexpr is const
    assert props["N"]["writable"] is False
    assert props["N"]["type"] == "int"


def test_an_ordinary_member_stays_writable_and_unqualified(tmp_path: Path) -> None:
    props = _properties(_extract(tmp_path, {"color.h": COLOR_HEADER}), "Color")
    assert props["r"]["writable"] is True
    assert "modifiers" not in props["r"]
    assert props["r"]["type"] == "int"


def test_a_const_member_is_const_and_read_only(tmp_path: Path) -> None:
    types = _extract(
        tmp_path, {"limit.h": "class Limit {\npublic:\n    const int max = 5;\n    int cur = 0;\n};\n"}
    )
    props = _properties(types, "Limit")
    assert props["max"]["modifiers"] == ["const"]
    assert props["max"]["writable"] is False
    assert props["max"]["type"] == "int"
    assert props["cur"]["writable"] is True


def test_a_static_member_that_is_not_const_is_static_and_writable(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        {"counter.h": "struct Counter {\n    static int live;\n    static const int zero = 0;\n};\n"},
    )
    props = _properties(types, "Counter")
    assert props["live"]["modifiers"] == ["static"]
    assert props["live"]["writable"] is True
    assert props["zero"]["modifiers"] == ["static", "const"]
    assert props["zero"]["writable"] is False


def test_a_pointer_to_const_is_writable_but_a_const_pointer_is_not(tmp_path: Path) -> None:
    header = """
struct Names {
    const char* name;
    char* const tag;
    static constexpr const char* Key = "k";
};
"""
    props = _properties(_extract(tmp_path, {"names.h": header}), "Names")
    assert props["name"]["type"] == "const char*"
    assert props["name"]["writable"] is True  # the pointer can be re-seated; only the pointee is const
    assert "modifiers" not in props["name"]
    assert props["tag"]["type"] == "char* const"
    assert props["tag"]["modifiers"] == ["const"]
    assert props["tag"]["writable"] is False
    assert props["Key"]["type"] == "const char*"
    assert props["Key"]["modifiers"] == ["static", "constexpr", "const"]  # constexpr makes the pointer const
    assert props["Key"]["writable"] is False


def test_pointer_reference_array_and_multiple_declarator_members_are_all_served(tmp_path: Path) -> None:
    header = """
struct Mixed {
    int* ptr;
    Mixed& self;
    int grid[3], one, *two;
    unsigned flag : 1;
};
"""
    props = _properties(_extract(tmp_path, {"mixed.h": header}), "Mixed")
    assert {name: p["type"] for name, p in props.items()} == {
        "ptr": "int*",
        "self": "Mixed&",
        "grid": "int[3]",
        "one": "int",
        "two": "int*",
        "flag": "unsigned",
    }


def test_private_and_protected_members_are_not_served(tmp_path: Path) -> None:
    header = """
class Safe {
public:
    static const int Open = 1;
private:
    static const int Hidden = 2;
    int secret;
protected:
    static constexpr int Shared = 3;
};
"""
    assert set(_properties(_extract(tmp_path, {"safe.h": header}), "Safe")) == {"Open"}


def test_switching_the_qualifiers_off_serves_the_members_writable_and_unqualified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_surface, "CPP_KEEP_STATIC_CONST_QUALIFIERS", False)
    props = _properties(_extract(tmp_path, {"color.h": COLOR_HEADER}), "Color")
    for name in ("Red", "N", "r"):
        assert props[name]["writable"] is True, name
        assert "modifiers" not in props[name], name


# --- RD3-021: public nested types are types of their own ------------------------------------------------------


def test_a_public_nested_struct_is_served_as_outer_inner(tmp_path: Path) -> None:
    types = _extract(tmp_path, {"a.h": "class A {\npublic:\n    struct Params { int x = 0; };\n};\n"})
    names = set(_by_name(types))
    assert {"A", "A::Params"} <= names
    params = _by_name(types)["A::Params"]
    assert params["kind"] == "struct_specifier"
    assert [p["name"] for p in params["properties"]] == ["x"]
    assert params["class_import"] == "A::Params"


def test_a_nested_type_carries_the_namespace_not_the_enclosing_class(tmp_path: Path) -> None:
    header = "namespace lib {\nclass A {\npublic:\n    class Inner { public: Inner(); };\n};\n}\n"
    inner = _by_name(_extract(tmp_path, {"a.h": header}))["A::Inner"]
    assert inner["canonical_namespace"] == "lib"
    assert inner["class_import"] == "lib::A::Inner"


def test_a_nested_enum_and_a_doubly_nested_type_are_served(tmp_path: Path) -> None:
    header = """
class A {
public:
    enum class Kind { First, Second };
    struct B {
        struct C { int v; };
    };
};
"""
    by_name = _by_name(_extract(tmp_path, {"a.h": header}))
    assert {"A::Kind", "A::B", "A::B::C"} <= set(by_name)
    assert by_name["A::Kind"]["kind"] == "enum_specifier"
    assert [m["name"] for m in by_name["A::Kind"]["enum_members"]] == ["First", "Second"]


def test_only_public_nested_types_are_served(tmp_path: Path) -> None:
    header = """
class A {
    struct ByDefaultPrivate {};
public:
    struct Shown {};
private:
    struct Hidden {};
protected:
    struct Guarded {};
};
struct S {
    struct ByDefaultPublic {};
private:
    struct Closed {};
};
"""
    names = set(_by_name(_extract(tmp_path, {"a.h": header})))
    assert {"A::Shown", "S::ByDefaultPublic"} <= names
    assert names.isdisjoint({"A::ByDefaultPrivate", "A::Hidden", "A::Guarded", "S::Closed"})


def test_a_nested_forward_declaration_is_not_a_type_but_its_out_of_class_definition_is(
    tmp_path: Path,
) -> None:
    header = """
class A {
public:
    class Iterator;
    Iterator begin();
};
class A::Iterator {
public:
    Iterator();
};
"""
    types = _extract(tmp_path, {"a.h": header})
    assert [t["name"] for t in types].count("A::Iterator") == 1
    assert "A::Iterator" in _by_name(types)


def test_a_nested_type_referenced_by_a_public_signature_is_served_with_its_members(tmp_path: Path) -> None:
    header = """
class Editor {
public:
    class Options {
    public:
        Options() noexcept = default;
        explicit Options(int level, bool strict = false);
        bool operator==(const Options& other) const;
        int level() const;
    };
    void apply(const Options& options);
    Options defaults() const;
};
"""
    types = _extract(tmp_path, {"editor.h": header})
    assert "Editor::Options" in _by_name(types)
    methods = _methods(types, "Editor::Options")
    constructors = [m for m in methods if m["is_constructor"]]
    assert len(constructors) == 2  # the nested type's constructors are constructors of Options
    assert [p["default"] for m in constructors for p in m["params"] if "default" in p] == ["false"]
    assert {m["name"] for m in methods} == {"Options", "operator==", "level"}
    assert [m["name"] for m in _methods(types, "Editor")] == ["apply", "defaults"]


def test_iterator_aliases_of_a_public_class_are_served_as_types(tmp_path: Path) -> None:
    header = """
class Items {
public:
    using iterator = std::vector<int>::iterator;
    typedef std::vector<int>::const_iterator const_iterator;
    using value_type = int;
    iterator begin();
};
"""
    by_name = _by_name(_extract(tmp_path, {"items.h": header}))
    assert by_name["Items::iterator"]["kind"] == "alias_declaration"
    assert by_name["Items::iterator"]["alias_of"] == "std::vector<int>::iterator"
    assert by_name["Items::const_iterator"]["kind"] == "type_definition"
    assert by_name["Items::const_iterator"]["alias_of"] == "std::vector<int>::const_iterator"
    assert "Items::value_type" not in by_name  # only iterator aliases are types of the collection


def test_a_nested_type_of_a_private_section_alias_is_not_served(tmp_path: Path) -> None:
    header = "class Items {\nprivate:\n    using iterator = int*;\npublic:\n    int size();\n};\n"
    assert "Items::iterator" not in _by_name(_extract(tmp_path, {"items.h": header}))


# --- RD3-022: implementation types are not API ---------------------------------------------------------------

PUBLIC_CLASS = "class Api { public: int run(); };\n"


def test_a_class_in_a_namespace_named_internal_detail_is_not_served(tmp_path: Path) -> None:
    header = (
        "namespace lib {\n"
        "class Shown { public: int a(); };\n"
        "namespace Internal { class Upper { public: int b(); }; }\n"
        "namespace internal { class Lower { public: int c(); }; }\n"
        "namespace detail { struct Helper { int d; }; }\n"
        "namespace InternalValidation { class Checker { public: int e(); }; }\n"
        "}\n"
    )
    names = set(_by_name(_extract(tmp_path, {"lib.h": header})))
    assert {"Shown", "Checker"} <= names  # InternalValidation is a different name
    assert names.isdisjoint({"Upper", "Lower", "Helper"})


def test_a_class_in_a_nested_internal_namespace_is_not_served(tmp_path: Path) -> None:
    header = "namespace lib::Internal::opc {\nclass Part { public: int a(); };\n}\nnamespace lib { class Open { public: int b(); }; }\n"
    names = set(_by_name(_extract(tmp_path, {"lib.h": header})))
    assert "Open" in names
    assert "Part" not in names


def test_a_class_in_an_anonymous_namespace_is_not_served(tmp_path: Path) -> None:
    header = "namespace { class Local { public: int a(); }; }\n" + PUBLIC_CLASS
    names = set(_by_name(_extract(tmp_path, {"lib.h": header})))
    assert names == {"Api"}


def test_a_header_under_an_internal_directory_is_not_served(tmp_path: Path) -> None:
    files = {
        "api.h": PUBLIC_CLASS,
        "internal/parser.h": "class Parser { public: int parse(); };\nint helper(int x);\n",
        "_internal/opc/part.h": "class Part { public: int id(); };\n",
        "detail/impl.h": "class Impl { public: int go(); };\n",
        "internalvalidation/checker.h": "class Checker { public: int check(); };\n",
    }
    names = set(_by_name(_extract(tmp_path, files)))
    assert {"Api", "Checker"} <= names  # `internalvalidation` is not `internal`
    assert names.isdisjoint({"Parser", "helper", "Part", "Impl"})


def test_a_type_declared_only_in_a_source_file_is_not_served(tmp_path: Path) -> None:
    files = {
        "api.h": PUBLIC_CLASS,
        "api.cpp": "class Scratch { public: int run(); };\nstruct Tally { int n; };\nint Api::run() { return 1; }\n",
    }
    types = _extract(tmp_path, files)
    names = set(_by_name(types))
    assert "Api" in names
    assert names.isdisjoint({"Scratch", "Tally"})
    assert not [t for t in types if t["file"].endswith(".cpp")]


def test_a_type_with_an_internal_suffix_is_not_served(tmp_path: Path) -> None:
    header = "class widget_internal { public: int run(); };\nclass widget { public: int run(); };\n"
    assert set(_by_name(_extract(tmp_path, {"widget.h": header}))) == {"widget"}


def test_a_free_function_in_an_internal_namespace_or_directory_is_not_served(tmp_path: Path) -> None:
    files = {
        "api.h": "namespace lib {\nint shown(int x);\nnamespace Internal { int hidden(int x); }\n}\n",
        "_internal/util.h": "int buried(int x);\n",
    }
    names = {t["name"] for t in _extract(tmp_path, files) if t["kind"] == "function"}
    assert names == {"shown"}


# --- RD3-023: member-level fidelity: constructors, operators, overloads, default arguments, qualifiers --------


MEMBERS_HEADER = """
class A {
public:
    A(int x = 3);
    explicit A(const A& other);
    virtual ~A();
    bool operator==(const A& other) const;
    A& operator+=(int step);
    void f(int a);
    void f(int a, double scale = 1.5);
    static int count();
    virtual void draw() const = 0;
    int size() const noexcept override;
};
"""


def test_constructors_operators_and_overloads_all_appear(tmp_path: Path) -> None:
    methods = _methods(_extract(tmp_path, {"a.h": MEMBERS_HEADER}), "A")
    assert [m["name"] for m in methods if m["is_constructor"]] == ["A", "A"]
    assert [m["name"] for m in methods if m["name"].startswith("operator")] == ["operator==", "operator+="]
    overloads = [m for m in methods if m["name"] == "f"]
    assert len(overloads) == 2
    assert [len(m["params"]) for m in overloads] == [1, 2]


def test_default_arguments_are_kept_on_constructors_and_overloads(tmp_path: Path) -> None:
    methods = _methods(_extract(tmp_path, {"a.h": MEMBERS_HEADER}), "A")
    ctor = next(m for m in methods if m["is_constructor"] and m["params"][0]["name"] == "x")
    assert ctor["params"] == [{"name": "x", "type": "int", "default": "3"}]
    scale = next(m for m in methods if m["name"] == "f" and len(m["params"]) == 2)
    assert scale["params"][1] == {"name": "scale", "type": "double", "default": "1.5"}


def test_const_static_virtual_pure_and_override_qualifiers_are_kept(tmp_path: Path) -> None:
    methods = {
        m["name"]: m
        for m in _methods(_extract(tmp_path, {"a.h": MEMBERS_HEADER}), "A")
        if not m["is_constructor"]
    }
    assert "static" in methods["count"]["modifiers"]
    assert {"virtual", "const", "pure"} <= set(methods["draw"]["modifiers"])
    assert {"const", "noexcept", "override"} <= set(methods["size"]["modifiers"])
    assert "const" in methods["operator=="]["modifiers"]


def test_member_fidelity_holds_inside_a_nested_type_too(tmp_path: Path) -> None:
    header = """
class Outer {
public:
    struct Inner {
        Inner(int x = 3);
        explicit Inner(const Inner& other);
        bool operator==(const Inner& other) const;
        void f(int a);
        void f(int a, double scale = 1.5);
    };
};
"""
    methods = _methods(_extract(tmp_path, {"outer.h": header}), "Outer::Inner")
    assert sum(1 for m in methods if m["is_constructor"]) == 2
    assert sum(1 for m in methods if m["name"] == "f") == 2
    assert any(m["name"] == "operator==" for m in methods)
