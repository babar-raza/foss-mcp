"""A sample of the non-C# language adapters, proving the engine dispatches by language.

Each of these exercises a different module under ``lang/`` through ``api_surface``, so a
negative-control deletion of any single one breaks a test here (or breaks collection of the
whole package, since ``lang/__init__.py`` imports every adapter eagerly).
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

GO = """
package widget

type Widget struct {
	Name string
}

func NewWidget(name string) *Widget {
	return &Widget{Name: name}
}
"""

RUST = """
pub struct Widget {
    pub name: String,
}

pub fn make_widget(name: &str) -> Widget {
    Widget { name: name.to_string() }
}
"""

# TC-051: reproduces the real bug found against pdf/typescript's
# src/metrics.ts:81 -- a real function's SECOND parameter is typed by an
# INLINE object-type literal (`w: { A: number; E: number }`), whose property
# keys ('A', 'E') were previously fabricated as bogus top-level "function"
# entries. Also includes a genuine interface with a real member (whose
# existing, separate handling this fix must not disturb) and a genuine
# standalone function with no such inline literal, as a control.
TYPESCRIPT = """
export interface Shape {
    area: number;
}

export function withAccents(base: number, w: { A: number; E: number }): void {
}

export function plainFunction(x: number): number {
    return x + 1;
}
"""


def test_typescript_inline_object_type_literal_properties_are_not_fabricated_as_functions(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"name": "widget"}\n', encoding="utf-8")
    package = tmp_path / "src"
    package.mkdir()
    (package / "shapes.ts").write_text(TYPESCRIPT, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("typescript"), "typescript", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # The real function is captured correctly as itself, with no corruption
    # from its parameter's inline object-type literal.
    assert by_name["withAccents"]["kind"] == "function"
    assert [p["name"] for p in by_name["withAccents"]["params"]] == ["base", "w"]

    # The inline object-type literal's own property keys must NOT leak
    # through as bogus top-level "function" entries (the exact TC-051 bug).
    assert "A" not in by_name
    assert "E" not in by_name

    # The genuine standalone function is unaffected.
    assert by_name["plainFunction"]["kind"] == "function"

    # The genuine interface's own member handling (a separate code path,
    # api_surface.py's class/interface member extraction loop) is unaffected
    # by this fix: the interface is still extracted with its real member.
    assert "Shape" in by_name
    assert by_name["Shape"]["kind"] == "interface_declaration"
    prop_names = {p["name"] for p in by_name["Shape"]["properties"]}
    assert "area" in prop_names


def test_go_names_a_top_level_type_type_spec_and_a_function_literally_function(
    tmp_path: Path,
) -> None:
    (tmp_path / "go.mod").write_text("module example.com/widget\n\ngo 1.21\n", encoding="utf-8")
    package = tmp_path / "widget"
    package.mkdir()
    (package / "widget.go").write_text(GO, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("go"), "go", package, tmp_path, "widget")
    by_name = {t["name"]: t["kind"] for t in types}
    assert by_name["Widget"] == "type_spec"
    assert by_name["NewWidget"] == "function"


def test_rust_names_a_top_level_free_function_literally_function(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "widget"\nversion = "0.1.0"\n', encoding="utf-8")
    package = tmp_path / "src"
    package.mkdir()
    (package / "lib.rs").write_text(RUST, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("rust"), "rust", package, tmp_path, "widget")
    by_name = {t["name"]: t["kind"] for t in types}
    assert by_name["Widget"] == "struct_item"
    assert by_name["make_widget"] == "function"


# TC-010b: reproduces two confirmed bugs in tree_helpers.py, both verified
# against the real tree-sitter-cpp grammar via a live parse probe
# (tree_sitter_language_pack.get_parser("cpp") against these exact shapes)
# before being fixed:
#
# (1) is_public() had no branch for a bare C++ free function at namespace/
#     global scope -- it fell through every existing check and was always
#     dropped. `makeWidget` below is such a function, at namespace scope.
#
# (2) _extract_bases() had no branch reading a class's `base_class_clause`
#     child (the real tree-sitter-cpp node for `: public Base1, private
#     Base2`) -- it fell through to the generic Java/C# loop, which does not
#     recognize `base_class_clause` at all but DOES match the class's own
#     `type_identifier` name child, so bases lists were not merely empty but
#     silently populated with the class's own name (confirmed empirically:
#     `_extract_bases()` on `class Widget : public Base1` previously returned
#     ['Widget'], not ['Base1'] and not []).
CPP = """
namespace widget {

int makeWidget(int seed) {
    return seed + 1;
}

class Widget : public OnlyBase {
public:
    Widget();
};

class MixedWidget : public PubBase, protected ProtBase, private PrivBase {
public:
    MixedWidget();
};

}
"""


def test_cpp_free_function_at_namespace_scope_is_extracted_as_function(tmp_path: Path) -> None:
    package = tmp_path / "src"
    package.mkdir()
    (package / "widget.cpp").write_text(CPP, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # The exact TC-010b free-function bug: previously dropped entirely by
    # is_public() falling through every branch to False.
    assert "makeWidget" in by_name
    assert by_name["makeWidget"]["kind"] == "function"


def test_cpp_class_with_single_public_base_has_base_in_bases_list(tmp_path: Path) -> None:
    package = tmp_path / "src"
    package.mkdir()
    (package / "widget.cpp").write_text(CPP, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    assert by_name["Widget"]["bases"] == ["OnlyBase"]


def test_cpp_class_with_multiple_bases_and_mixed_access_specifiers_has_all_bases(tmp_path: Path) -> None:
    package = tmp_path / "src"
    package.mkdir()
    (package / "widget.cpp").write_text(CPP, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # Verified against the real grammar via a live parse probe: public,
    # protected and private bases all appear as base_class_clause children
    # and must all be captured regardless of their access specifier.
    assert by_name["MixedWidget"]["bases"] == ["PubBase", "ProtBase", "PrivBase"]


# TC-058: reproduces a bug found by direct reproduction against the real
# public aspose-pdf-foss/Aspose.PDF-FOSS-for-Cpp repository (commit
# c559fc3dd1c5d3a42c7f12a953918ce8dc59fa07): a real free function
# `constexpr Matrix Identity() noexcept` was extracted with name 'Matrix'
# (its own return type) instead of 'Identity'.
#
# Root cause, confirmed via a live parse probe (tree_sitter_language_pack.
# get_parser("cpp")): a C++ function_definition's direct children include
# the return-type node itself. For a function returning a user-defined type
# by value, that return-type node is a bare 'type_identifier' (e.g.
# 'Widget' below) -- exactly what _node_name()'s generic identifier-child
# fallback loop matches, so it returned the return type instead of ever
# reaching the function_declarator-based lookup that finds the real name
# ('Make', nested inside the function_declarator child alongside it).
CPP_FREE_FUNCTION_BARE_RETURN_TYPE = """
struct Widget {};

Widget Make() {
    return Widget{};
}

bool HasSide(int x) {
    return x > 0;
}
"""


def test_cpp_free_function_returning_bare_user_type_by_value_is_named_correctly(
    tmp_path: Path,
) -> None:
    package = tmp_path / "src"
    package.mkdir()
    (package / "widget.cpp").write_text(CPP_FREE_FUNCTION_BARE_RETURN_TYPE, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # The exact TC-058 bug: a free function returning a bare user-defined
    # type by value must be named after itself ('Make'), not misnamed after
    # its own return type ('Widget').
    assert "Make" in by_name
    assert by_name["Make"]["kind"] == "function"
    assert "Widget" not in by_name or by_name["Widget"]["kind"] != "function"

    # Control: a free function whose return type is NOT a bare identifier
    # (here 'bool', a primitive_type node) never triggered this bug and
    # must remain correctly named after this fix.
    assert "HasSide" in by_name
    assert by_name["HasSide"]["kind"] == "function"


# TC-059: reproduces a bug found by direct reproduction against the real
# public aspose-pdf-foss/Aspose.PDF-FOSS-for-Cpp repository (commit
# c559fc3dd1c5d3a42c7f12a953918ce8dc59fa07): real free functions such as
# `AnnotationFlags::operator|`, `BorderSide::operator|`,
# `Permissions::operator|` and `RichTextFontStyles::operator|` (all returning
# their own enum type by value) were extracted with the WRONG name -- each
# named after its own return type (e.g. 'Flags') instead of its real name
# ('operator|'). TC-058's bare-identifier-return-type fix does not cover this
# case: it is a narrower, distinct gap for operator overloads specifically.
#
# Root cause, confirmed via a live parse probe (tree_sitter_language_pack.
# get_parser("cpp")) against `Flags operator|(Flags a, Flags b)`: the
# function_declarator's declarator-name child has node type 'operator_name'
# (with literal text 'operator|'), a genuinely distinct tree-sitter-cpp node
# type from a plain function's 'identifier' -- not covered by the existing
# _cpp_free_function_name() check before this fix.
CPP_FREE_FUNCTION_OPERATOR_OVERLOAD = """
enum class Flags { A, B };

Flags operator|(Flags a, Flags b) {
    return a;
}
"""


def test_cpp_free_function_operator_overload_is_named_correctly(
    tmp_path: Path,
) -> None:
    package = tmp_path / "src"
    package.mkdir()
    (package / "widget.cpp").write_text(CPP_FREE_FUNCTION_OPERATOR_OVERLOAD, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # The exact TC-059 bug: a free function operator overload must be named
    # after itself ('operator|'), not misnamed after its own return type
    # ('Flags').
    assert "operator|" in by_name
    assert by_name["operator|"]["kind"] == "function"
    assert "Flags" not in by_name or by_name["Flags"]["kind"] != "function"
