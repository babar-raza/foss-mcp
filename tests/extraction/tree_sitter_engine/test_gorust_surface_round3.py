"""G3/TC-535 (REQ-G3-009, REQ-G3-008; Round-3 rows RD3-025 to RD3-033): the Go and Rust extraction, from hand-written sources.

Offline and deterministic. Every source below is written to ``tmp_path`` and every expectation follows from the
LANGUAGE RULES (what ``go doc`` / ``rustc`` would say about that source), never from what the extractor happened to
produce: a Go package is imported by the module path plus its directory, a ``const`` group without ``iota`` repeats
the previous spec, the method set of an embedding struct contains the exported methods of its embedded fields at
any depth (shallowest wins, equal depth is ambiguous), a Rust item is importable only through a chain of ``pub mod``
and ``pub use`` from the crate root, and so on.

No test builds anything: nothing here runs ``go``, ``cargo`` or ``rustc``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.extraction.tree_sitter_engine.lang import go as lang_go

# ---------------------------------------------------------------------------------------------------- helpers


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _go(root: Path, files: dict[str, str], module: str | None = "example.com/lib") -> list[dict[str, Any]]:
    if module is not None:
        files = {"go.mod": f"module {module}\n\ngo 1.22\n", **files}
    _write(root, files)
    types, *_ = api_surface.extract_api_surface(get_parser("go"), "go", root, root, "pdf")
    return types


def _rust(root: Path, files: dict[str, str]) -> list[dict[str, Any]]:
    _write(root, {f"src/{name}": text for name, text in files.items()})
    types, *_ = api_surface.extract_api_surface(get_parser("rust"), "rust", root / "src", root, "cells")
    return types


def _by_name(types: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in types}


def _methods(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {method["name"]: method for method in entry.get("methods", [])}


# ------------------------------------------------------------------------------------------------------- Go


def test_a_sub_package_symbol_carries_its_own_import_path(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "lib.go": "package lib\n\nfunc Open() int { return 0 }\n",
            "ai/chat.go": "package ai\n\ntype ChatCopilot struct{}\n\nfunc NewChatCopilot() *ChatCopilot { return nil }\n",
        },
    )
    by_name = _by_name(types)
    assert by_name["NewChatCopilot"]["package"] == "ai"
    assert by_name["NewChatCopilot"]["import_path"] == "example.com/lib/ai"
    assert by_name["ChatCopilot"]["import_path"] == "example.com/lib/ai"
    assert by_name["Open"]["package"] == "lib"
    assert by_name["Open"]["import_path"] == "example.com/lib"


def test_the_switch_off_serves_one_flat_package(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(lang_go, "GO_SUBPACKAGE_IMPORT_PATHS", False)
    types = _go(
        tmp_path,
        {
            "lib.go": "package lib\n\nfunc Open() int { return 0 }\n",
            "ai/chat.go": "package ai\n\nfunc NewChatCopilot() int { return 0 }\n",
        },
    )
    by_name = _by_name(types)
    assert "import_path" not in by_name["NewChatCopilot"]
    assert "package" not in by_name["NewChatCopilot"]
    assert "import_path" not in by_name["Open"]


def test_same_name_in_two_packages_are_two_types_with_their_own_methods(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "lib.go": "package lib\n\ntype Config struct{ Root string }\n\nfunc (c *Config) Load() {}\n",
            "ai/cfg.go": "package ai\n\ntype Config struct{ Model string }\n\nfunc (c *Config) Validate() {}\n",
        },
    )
    configs = [entry for entry in types if entry["name"] == "Config"]
    assert sorted(entry["import_path"] for entry in configs) == ["example.com/lib", "example.com/lib/ai"]
    by_path = {entry["import_path"]: entry for entry in configs}
    assert set(_methods(by_path["example.com/lib"])) == {"Load"}
    assert set(_methods(by_path["example.com/lib/ai"])) == {"Validate"}
    assert [p["name"] for p in by_path["example.com/lib/ai"]["properties"]] == ["Model"]


def test_without_a_go_mod_the_package_name_is_kept_and_no_import_path_is_invented(tmp_path: Path) -> None:
    types = _go(tmp_path, {"lib.go": "package lib\n\nfunc Open() int { return 0 }\n"}, module=None)
    entry = _by_name(types)["Open"]
    assert entry["package"] == "lib"
    assert "import_path" not in entry


def test_a_nested_module_has_its_own_module_path(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "lib.go": "package lib\n\nfunc Open() int { return 0 }\n",
            "extra/go.mod": "module example.com/extra\n\ngo 1.22\n",
            "extra/t/t.go": "package t\n\nfunc Run() {}\n",
        },
    )
    by_name = _by_name(types)
    assert by_name["Open"]["import_path"] == "example.com/lib"
    assert by_name["Run"]["import_path"] == "example.com/extra/t"


def test_the_module_is_found_above_a_package_root_inside_the_repository(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"go.mod": "module example.com/lib\n", "pkg/p.go": "package pkg\n\nfunc Open() int { return 0 }\n"},
    )
    types, *_ = api_surface.extract_api_surface(get_parser("go"), "go", tmp_path / "pkg", tmp_path, "pdf")
    assert _by_name(types)["Open"]["import_path"] == "example.com/lib/pkg"


def test_a_main_package_and_an_internal_directory_are_not_served(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "lib.go": "package lib\n\nfunc Open() int { return 0 }\n",
            "cmd/tool/main.go": "package main\n\nfunc Run() {}\n",
            "internal/secret/s.go": "package secret\n\nfunc Hidden() {}\n",
        },
    )
    assert set(_by_name(types)) == {"Open"}


def test_untyped_constants_with_iota_are_served_with_their_values(tmp_path: Path) -> None:
    types = _go(tmp_path, {"c.go": "package lib\n\nconst (\n\tA = iota\n\tB\n\tC\n)\n"})
    by_name = _by_name(types)
    assert [(n, by_name[n]["kind"], by_name[n]["value"]) for n in ("A", "B", "C")] == [
        ("A", "constant", "0"),
        ("B", "constant", "1"),
        ("C", "constant", "2"),
    ]


def test_constants_and_variables_keep_their_value_or_declared_type(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "v.go": (
                'package lib\n\nimport "errors"\n\n// Version is the library version.\nconst Version = "1.2"\n\n'
                'var (\n\t// ErrBad reports a bad input.\n\tErrBad = errors.New("bad")\n\tLimit int = 5\n\tinternal = 1\n)\n'
            )
        },
    )
    by_name = _by_name(types)
    assert by_name["Version"]["kind"] == "constant"
    assert by_name["Version"]["value"] == '"1.2"'
    assert by_name["Version"]["doc"].startswith("Version is the library version")
    assert by_name["ErrBad"]["kind"] == "variable"
    assert by_name["ErrBad"]["value"] == 'errors.New("bad")'
    assert by_name["Limit"]["type"] == "int"
    assert by_name["Limit"]["value"] == "5"
    assert "internal" not in by_name


def test_a_constant_declared_inside_a_function_is_not_api(tmp_path: Path) -> None:
    types = _go(tmp_path, {"f.go": "package lib\n\nfunc Run() {\n\tconst Local = 1\n\t_ = Local\n}\n"})
    assert set(_by_name(types)) == {"Run"}


def test_methods_of_unexported_types_are_not_served(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "r.go": (
                "package lib\n\ntype reader struct{}\n\nfunc (r *reader) Close() error { return nil }\n"
                "func (r *reader) ReadAt(p []byte, off int64) (int, error) { return 0, nil }\n\n"
                "type Writer struct{}\n\nfunc (w *Writer) Write(p []byte) (int, error) { return 0, nil }\n"
            )
        },
    )
    assert {entry["name"] for entry in types} == {"Writer"}
    assert set(_methods(_by_name(types)["Writer"])) == {"Write"}


def test_a_struct_tag_is_never_the_field_type(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "cell.go": (
                'package lib\n\nimport "encoding/xml"\n\ntype Cell struct {\n'
                '\tXMLName xml.Name `xml:"c"`\n\tRef string `xml:"r,attr"`\n\tA, B int `json:"ab"`\n}\n'
            )
        },
    )
    fields = {prop["name"]: prop["type"] for prop in _by_name(types)["Cell"]["properties"]}
    assert fields == {"XMLName": "xml.Name", "Ref": "string", "A": "int", "B": "int"}


def test_a_function_typed_declaration_renders_its_signature(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "cb.go": (
                "package lib\n\n// RowCallback is called for every row.\n"
                "type RowCallback func(rowIndex int, cells map[string]string) error\n\ntype Row []string\n\ntype Mode int\n"
            )
        },
    )
    by_name = _by_name(types)
    assert by_name["RowCallback"]["underlying"] == "func(rowIndex int, cells map[string]string) error"
    assert by_name["Row"]["underlying"] == "[]string"
    assert "underlying" not in by_name["Mode"]  # `type Mode int` is already in bases


def test_members_promoted_through_an_unexported_embedded_type_are_listed(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "f.go": (
                "package lib\n\ntype base struct{ Name string }\n\nfunc (b *base) Rename(n string) {}\n"
                "func (b *base) Label() string { return b.Name }\n\n"
                'type Field struct{ base }\n\nfunc (f *Field) Label() string { return "own" }\n\n'
                "type Box struct{ *Field }\n"
            )
        },
    )
    by_name = _by_name(types)
    field = _methods(by_name["Field"])
    assert set(field) == {"Label", "Rename"}
    assert "promoted_from" not in field["Label"]  # Field declares Label itself: it hides base.Label
    assert field["Rename"]["promoted_from"] == "base"
    assert [p["name"] for p in by_name["Field"]["properties"]] == ["Name"]
    assert by_name["Field"]["properties"][0]["promoted_from"] == "base"
    box = _methods(by_name["Box"])
    assert set(box) == {"Label", "Rename"}
    assert box["Label"]["promoted_from"] == "Field"  # the shallowest declaration wins
    assert box["Rename"]["promoted_from"] == "base"


def test_a_name_found_twice_at_the_same_depth_is_ambiguous_and_not_promoted(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "a.go": (
                'package lib\n\ntype A struct{}\n\nfunc (A) Name() string { return "a" }\nfunc (A) OnlyA() {}\n\n'
                'type B struct{}\n\nfunc (B) Name() string { return "b" }\n\ntype AB struct {\n\tA\n\tB\n}\n'
            )
        },
    )
    assert set(_methods(_by_name(types)["AB"])) == {"OnlyA"}


def test_interface_embedding_promotes_the_methods_of_the_embedded_interface(tmp_path: Path) -> None:
    types = _go(
        tmp_path,
        {
            "i.go": (
                "package lib\n\ntype reader interface{ Read() int }\n\n"
                "type ReadCloser interface {\n\treader\n\tClose() error\n}\n"
            )
        },
    )
    methods = _methods(_by_name(types)["ReadCloser"])
    assert set(methods) == {"Close", "Read"}
    assert methods["Read"]["promoted_from"] == "reader"


# ----------------------------------------------------------------------------------------------------- Rust


def test_a_method_renders_its_receiver(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "lib.rs": (
                "pub struct W { pub n: i32 }\n\nimpl W {\n"
                "    pub fn new() -> Self { W { n: 0 } }\n"
                "    pub fn save(&mut self, p: &str) -> bool { true }\n"
                "    pub fn len(&self) -> usize { 0 }\n"
                "    pub fn into_n(self) -> i32 { 0 }\n"
                "    pub fn into_m(mut self) -> i32 { 0 }\n"
                "    pub fn name<'a>(&'a self) -> &'a str { \"\" }\n}\n"
            )
        },
    )
    methods = _methods(_by_name(types)["W"])
    assert methods["save"]["receiver"] == "&mut self"
    assert methods["save"]["params"] == [{"name": "p", "type": "&str"}]
    assert methods["len"]["receiver"] == "&self"
    assert methods["into_n"]["receiver"] == "self"
    assert methods["into_m"]["receiver"] == "mut self"
    assert methods["name"]["receiver"] == "&'a self"
    assert "receiver" not in methods["new"]
    assert methods["new"]["modifiers"] == ["static"]  # an associated function, called as W::new()


def test_generic_parameters_with_bounds_and_where_clauses_are_recorded(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "lib.rs": (
                "pub struct Bag<T: Clone> where T: Send { pub items: Vec<T> }\n\nimpl<T: Clone> Bag<T> where T: Send {\n"
                "    pub fn put<P: AsRef<str>>(&mut self, p: P, t: T) where P: Send {}\n}\n"
                "pub fn load<R: std::io::Read>(r: R) -> i32 { 0 }\n"
            )
        },
    )
    by_name = _by_name(types)
    assert by_name["Bag"]["generics"] == "<T: Clone>"
    assert by_name["Bag"]["where"] == "where T: Send"
    put = _methods(by_name["Bag"])["put"]
    assert put["generics"] == "<P: AsRef<str>>"
    assert put["where"] == "where P: Send"
    assert put["receiver"] == "&mut self"
    # a free function of the crate root is importable too, with its own bounds
    assert by_name["load"]["generics"] == "<R: std::io::Read>"


def test_enum_variants_keep_their_payload(tmp_path: Path) -> None:
    types = _rust(tmp_path, {"lib.rs": "pub enum V { A(i32), B { x: u8, y: String }, C = 5, D }\n"})
    members = {m["name"]: m for m in _by_name(types)["V"]["enum_members"]}
    assert members["A"]["payload"] == "(i32)"
    assert members["B"]["payload"] == "{ x: u8, y: String }"
    assert members["C"]["value"] == "5"
    assert "payload" not in members["C"]
    assert "payload" not in members["D"]


def test_a_pub_type_alias_is_indexed_with_its_target(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "lib.rs": "pub struct Inner;\n\npub type S = Inner;\npub type Items<'a> = Vec<&'a Inner>;\ntype Private = Inner;\n"
        },
    )
    by_name = _by_name(types)
    assert by_name["S"]["kind"] == "type_item"
    assert by_name["S"]["alias_of"] == "Inner"
    assert by_name["Items"]["alias_of"] == "Vec<&'a Inner>"
    assert by_name["Items"]["generics"] == "<'a>"
    assert "Private" not in by_name


def test_derived_traits_are_not_bases(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "lib.rs": (
                "use std::fmt::{Display, Formatter, Result};\n\n#[derive(Debug, Clone, PartialEq)]\npub struct P;\n\n"
                "impl Display for P {\n    fn fmt(&self, f: &mut Formatter<'_>) -> Result { Ok(()) }\n}\n\n"
                "pub trait Shape: Sized + Clone { fn area(&self) -> f64; }\n"
            )
        },
    )
    by_name = _by_name(types)
    assert by_name["P"]["derives"] == ["Debug", "Clone", "PartialEq"]
    # the only base of P is a trait it IMPLEMENTS (impl Display for P), none of the three it derives
    assert by_name["P"]["bases"] == ["Display"]
    assert by_name["Shape"]["bases"] == ["Sized", "Clone"]


def test_a_fn_in_a_private_module_that_is_not_re_exported_is_not_served(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "lib.rs": "mod hidden;\nmod shown;\n\npub use shown::Visible;\n",
            "hidden.rs": "pub fn secret() -> i32 { 0 }\n\npub struct Buried;\n",
            "shown.rs": "pub struct Visible;\n\npub struct NotReExported;\n\npub fn helper() {}\n",
        },
    )
    assert set(_by_name(types)) == {"Visible"}


def test_a_pub_mod_chain_and_a_glob_and_a_rename_make_items_importable(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "lib.rs": (
                "pub mod util;\nmod inner;\npub use inner::*;\npub use util::deep::Leaf as Renamed;\n"
            ),
            "util/mod.rs": "pub mod deep;\n\npub fn helper() {}\n",
            "util/deep.rs": "pub struct Leaf;\n\nstruct Private;\n",
            "inner.rs": "pub struct FromGlob;\n\npub(crate) struct CrateOnly;\n",
        },
    )
    by_name = _by_name(types)
    assert set(by_name) == {"helper", "Leaf", "FromGlob"}
    assert by_name["helper"]["class_import"] == "util::helper"
    # the shortest of the paths to Leaf: the rename at the crate root
    assert by_name["Leaf"]["class_import"] == "Renamed"
    assert by_name["FromGlob"]["class_import"] == "FromGlob"


def test_path_attributes_wire_modules_and_a_file_no_mod_names_is_not_served(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "lib.rs": '#[path = "Impl/api.rs"]\nmod api;\n\npub use api::{Wired, Aliased as Other};\n',
            "Impl/api.rs": '#[path = "Wired.rs"]\nmod wired;\npub use wired::Wired;\n\npub struct Aliased;\n',
            "Impl/Wired.rs": "pub struct Wired;\n",
            "Impl/Dead.rs": "pub struct WorkbookModel;\n",
        },
    )
    assert set(_by_name(types)) == {"Wired", "Aliased"}


def test_trait_impl_functions_of_unserved_types_are_not_free_api(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "lib.rs": (
                "use std::fmt;\n\nstruct Private;\n\nimpl fmt::Display for Private {\n"
                "    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { Ok(()) }\n}\n\n"
                "impl PartialEq for Private {\n    fn eq(&self, o: &Self) -> bool { true }\n}\n\n"
                "pub struct Public;\n\nimpl Default for Public {\n    fn default() -> Self { Public }\n}\n"
            )
        },
    )
    by_name = _by_name(types)
    assert set(by_name) == {"Public"}
    default = _methods(by_name["Public"])["default"]
    assert default["modifiers"] == ["static", "impl Default"]


def test_a_crate_without_a_lib_rs_keeps_every_public_item(tmp_path: Path) -> None:
    types = _rust(
        tmp_path,
        {
            "widget.rs": "pub struct Widget { pub n: i32 }\n\nimpl Widget {\n    pub fn size(&self) -> i32 { 0 }\n}\n"
        },
    )
    widget = _by_name(types)["Widget"]
    assert widget["reachable"] is True
    assert _methods(widget)["size"]["receiver"] == "&self"
