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
