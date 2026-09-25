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
