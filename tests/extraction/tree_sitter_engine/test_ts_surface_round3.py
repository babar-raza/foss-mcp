"""TC-533 (Round 3, rows RD3-010 to RD3-013): the TypeScript reader keeps what the source says, nothing more.

Every test writes a small TypeScript package whose answer follows from the language rules (a property is
read-only only with the ``readonly`` modifier, a member of an inline object type is not a member of the
enclosing class, a comma inside ``<...>`` does not separate two base types, a package serves what its entry
point exports) and runs ``run_extraction.extract_from_clone`` over it, offline. No expectation is read from the
extractor's own output.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.extraction.tree_sitter_engine.lang import typescript as ts_lang

MANIFEST = {"platform": "typescript", "family": "x", "repository": "x/y"}


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text('{"name": "t", "main": "src/index.ts"}\n', encoding="utf-8")
    for name, source in files.items():
        target = root / "src" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    ts_lang.clear_cache()
    return root


def _extract_files(tmp_path: Path, files: dict[str, str]) -> list[dict[str, Any]]:
    types, unresolved = run_extraction.extract_from_clone(_write_tree(tmp_path / "tree", files), MANIFEST)
    assert unresolved == []
    return types


def _extract(tmp_path: Path, source: str) -> list[dict[str, Any]]:
    return _extract_files(tmp_path, {"index.ts": source})


def _type(types: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [t for t in types if t["name"] == name]
    assert len(matches) == 1, [t["name"] for t in types]
    return matches[0]


def _names(types: list[dict[str, Any]]) -> set[str]:
    return {t["name"] for t in types}


def _props(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in entry["properties"]}


def _methods(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in entry["methods"]}


# -- read-only only when declared ---------------------------------------------------------------------------------------


def test_the_readonly_switch_is_the_mandated_line_and_is_on() -> None:
    assert api_surface.TS_READONLY_ONLY_WHEN_DECLARED is True
    assert "\nTS_READONLY_ONLY_WHEN_DECLARED = True\n" in inspect.getsource(api_surface)


def test_an_interface_property_is_read_only_only_with_the_readonly_modifier(tmp_path: Path) -> None:
    types = _extract(tmp_path, "export interface I { readonly a: number; b?: string }\n")
    props = _props(_type(types, "I"))
    assert props["a"]["writable"] is False
    assert props["b"]["writable"] is True
    assert props["b"]["modifiers"] == ["optional"]
    assert "modifiers" not in props["a"]


def test_a_class_field_is_read_only_only_with_the_readonly_modifier(tmp_path: Path) -> None:
    types = _extract(
        tmp_path, "export class C { public readonly fixed: number = 1; public free: number = 2; }\n"
    )
    props = _props(_type(types, "C"))
    assert props["fixed"]["writable"] is False
    assert props["free"]["writable"] is True


def test_with_the_switch_off_every_property_is_read_only_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_surface, "TS_READONLY_ONLY_WHEN_DECLARED", False)
    types = _extract(
        tmp_path, "export interface I { readonly a: number; b?: string }\nexport class C { free = 2 }\n"
    )
    assert {name: p["writable"] for name, p in _props(_type(types, "I")).items()} == {"a": False, "b": False}
    assert _props(_type(types, "C"))["free"]["writable"] is False


def test_a_getter_without_a_setter_is_read_only_and_a_pair_is_read_write(tmp_path: Path) -> None:
    source = (
        "export class C {\n"
        "  get only(): number { return 1; }\n"
        "  get both(): string { return ''; }\n"
        "  set both(v: string) {}\n"
        "}\n"
    )
    props = _props(_type(_extract(tmp_path, source), "C"))
    assert props["only"]["access_mode"] == "readonly"
    assert props["both"]["access_mode"] == "readwrite"


# -- members of inline object types are not members of the enclosing type -----------------------------------------------


def test_the_return_type_of_a_private_method_adds_no_property(tmp_path: Path) -> None:
    source = "export class C {\n  v = 1;\n  private f() { return { x: 1 }; }\n  private g(): { y: number } { return { y: 2 }; }\n}\n"
    entry = _type(_extract(tmp_path, source), "C")
    assert list(_props(entry)) == ["v"]


def test_the_return_type_of_a_public_method_adds_no_property(tmp_path: Path) -> None:
    source = "export class C {\n  area(): { w: number; h: number } { return { w: 1, h: 2 }; }\n}\n"
    entry = _type(_extract(tmp_path, source), "C")
    assert entry["properties"] == []
    assert list(_methods(entry)) == ["area"]
    assert _methods(entry)["area"]["return_type"] == "{ w: number; h: number }"


def test_an_inline_options_parameter_adds_no_property(tmp_path: Path) -> None:
    source = (
        "export class W {\n"
        "  async save(path: string, options?: { password?: string; format?: string }): Promise<void> {}\n"
        "}\n"
    )
    entry = _type(_extract(tmp_path, source), "W")
    assert entry["properties"] == []
    assert _methods(entry)["save"]["params"][1]["name"] == "options"


def test_a_property_typed_with_an_inline_object_is_one_property(tmp_path: Path) -> None:
    source = (
        "export interface Chart {\n  title: string;\n  plot?: {\n    x: number;\n    y: number;\n  };\n}\n"
    )
    entry = _type(_extract(tmp_path, source), "Chart")
    assert list(_props(entry)) == ["title", "plot"]


def test_an_object_type_alias_keeps_its_own_members_and_an_intersection_too(tmp_path: Path) -> None:
    source = (
        "export type P = { a: number; b?: string };\n"
        "export type Q = P & { c: boolean };\n"
        "export type R = Readonly<{ hidden: number }>;\n"
    )
    types = _extract(tmp_path, source)
    assert list(_props(_type(types, "P"))) == ["a", "b"]
    assert list(_props(_type(types, "Q"))) == ["c"]
    assert _type(types, "R")["properties"] == []


# -- type aliases, property types, signatures ---------------------------------------------------------------------------


def test_a_type_alias_keeps_its_right_hand_side(tmp_path: Path) -> None:
    types = _extract(tmp_path, "export type T = 'a' | 'b';\n")
    assert _type(types, "T")["alias_of"] == "'a' | 'b'"


def test_no_property_type_carries_a_leading_colon(tmp_path: Path) -> None:
    source = (
        "export interface I { a: string; b?: number[] }\n"
        "export class C { field: Map<string, number> = new Map(); get g(): boolean { return true; } }\n"
    )
    types = _extract(tmp_path, source)
    seen = {p["name"]: p["type"] for name in ("I", "C") for p in _type(types, name)["properties"]}
    assert seen == {"a": "string", "b": "number[]", "field": "Map<string, number>", "g": "boolean"}


def test_static_async_and_default_are_kept(tmp_path: Path) -> None:
    source = (
        "export interface O { x?: number }\n"
        "export class D {\n"
        "  static async load(p: string, opts: O = {}): Promise<D> { return new D(); }\n"
        "}\n"
    )
    load = _methods(_type(_extract(tmp_path, source), "D"))["load"]
    assert load["modifiers"] == ["static", "async"]
    assert load["return_type"] == "Promise<D>"
    assert [(p["name"], p["type"], p.get("default")) for p in load["params"]] == [
        ("p", "string", None),
        ("opts", "O", "{}"),
    ]


def test_optional_and_rest_parameters_are_recorded(tmp_path: Path) -> None:
    source = "export class M {\n  make(first: number, label?: string, ...rest: number[]): void {}\n}\n"
    params = _methods(_type(_extract(tmp_path, source), "M"))["make"]["params"]
    assert [p["name"] for p in params] == ["first", "label", "...rest"]
    assert params[1]["optional"] is True
    assert params[2]["type"] == "number[]"
    assert "optional" not in params[0]


def test_this_and_iterator_returns_keep_their_text(tmp_path: Path) -> None:
    source = (
        "export class B {\n"
        "  add(n: number): this { return this; }\n"
        "  [Symbol.iterator](): Iterator<number> { return [][Symbol.iterator](); }\n"
        "  *values(): IterableIterator<number> { yield 1; }\n"
        "}\n"
    )
    methods = _methods(_type(_extract(tmp_path, source), "B"))
    assert methods["add"]["return_type"] == "this"
    assert methods["[Symbol.iterator]"]["return_type"] == "Iterator<number>"
    assert methods["values"]["return_type"] == "IterableIterator<number>"


def test_a_static_readonly_field_is_a_static_constant(tmp_path: Path) -> None:
    types = _extract(tmp_path, "export class V { static readonly VERSION = '1.0'; static count = 0; }\n")
    props = _props(_type(types, "V"))
    assert props["VERSION"]["modifiers"] == ["static"]
    assert props["VERSION"]["writable"] is False
    assert props["VERSION"]["kind"] == "constant"
    assert props["count"]["modifiers"] == ["static"]
    assert props["count"]["writable"] is True


# -- generic base lists -------------------------------------------------------------------------------------------------


def test_a_generic_base_with_two_arguments_is_one_base(tmp_path: Path) -> None:
    source = "export class Base<A, B> {}\nexport class D extends Base<number, string> {}\n"
    assert _type(_extract(tmp_path, source), "D")["bases"] == ["Base<number, string>"]


def test_an_omit_base_with_a_union_of_keys_is_one_base_and_the_next_base_is_another(tmp_path: Path) -> None:
    source = (
        "export interface A { a: number; x: string; y: string }\n"
        "export interface B { b: number }\n"
        "export interface C extends Omit<A, 'x' | 'y'>, B { c: number }\n"
    )
    assert _type(_extract(tmp_path, source), "C")["bases"] == ["Omit<A, 'x' | 'y'>", "B"]


def test_extends_and_implements_lists_are_all_split_at_top_level_commas_only(tmp_path: Path) -> None:
    source = (
        "export interface I {}\n"
        "export interface J<K, L> {}\n"
        "export class Base<A, B> {}\n"
        "export class D extends Base<number, Map<string, number>> implements I, J<string, number> {}\n"
    )
    assert _type(_extract(tmp_path, source), "D")["bases"] == [
        "Base<number, Map<string, number>>",
        "I",
        "J<string, number>",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A, B", ["A", "B"]),
        ("Base<A, B>", ["Base<A, B>"]),
        ("Omit<A, 'x,y' | 'z'>, B", ["Omit<A, 'x,y' | 'z'>", "B"]),
        ("F<(a: number, b: string) => void, G>, H", ["F<(a: number, b: string) => void, G>", "H"]),
        ("P<{ a: 1; b: 2 }, [x, y]>", ["P<{ a: 1; b: 2 }, [x, y]>"]),
        ("A, // note, with a comma\n B", ["A", "B"]),
        ("A /* x, y */, B", ["A", "B"]),
        ("", []),
    ],
)
def test_the_top_level_comma_split(text: str, expected: list[str]) -> None:
    assert api_surface._ts_split_top_level(text) == expected


# -- a package with an entry point serves exactly its export surface ----------------------------------------------------


def test_a_function_the_entry_point_does_not_export_is_not_served(tmp_path: Path) -> None:
    files = {
        "index.ts": "export { shown } from './mod.js';\n",
        "mod.ts": (
            "export function shown(): number { return 1; }\n"
            "export function hidden(): number { return 2; }\n"
            "function local(): void {}\n"
        ),
    }
    assert _names(_extract_files(tmp_path, files)) == {"shown"}


def test_types_aliases_and_constants_follow_the_same_rule(tmp_path: Path) -> None:
    files = {
        "index.ts": "export { Shown, ShownAlias, SHOWN } from './mod.js';\n",
        "mod.ts": (
            "export interface Shown { a: number }\n"
            "export interface Internal { b: number }\n"
            "export type ShownAlias = 'a' | 'b';\n"
            "export type InternalAlias = 'c';\n"
            "export const SHOWN = 1;\n"
            "export const INTERNAL = 2;\n"
            "export class InternalClass {}\n"
        ),
    }
    assert _names(_extract_files(tmp_path, files)) == {"Shown", "ShownAlias", "SHOWN"}


def test_a_re_export_chain_through_a_star_export_is_followed(tmp_path: Path) -> None:
    files = {
        "index.ts": "export * from './a.js';\n",
        "a.ts": "export { viaChain } from './b.js';\nexport class Direct {}\n",
        "b.ts": "export function viaChain(): void {}\nexport function notChained(): void {}\n",
    }
    assert _names(_extract_files(tmp_path, files)) == {"Direct", "viaChain"}


def test_a_public_class_keeps_the_members_it_inherits_from_an_unexported_base(tmp_path: Path) -> None:
    files = {
        "index.ts": "export { Pub } from './mod.js';\n",
        "mod.ts": "class Base { inherited(): number { return 1; } }\nexport class Pub extends Base { own(): void {} }\n",
    }
    types = _extract_files(tmp_path, files)
    assert _names(types) == {"Pub"}
    pub = _methods(_type(types, "Pub"))
    assert pub["inherited"]["inherited_from"].endswith("Base")
    assert "inherited_from" not in pub["own"]


def test_an_entry_point_that_exports_nothing_leaves_every_symbol_served(tmp_path: Path) -> None:
    files = {
        "index.ts": "import { a } from './mod.js';\nconsole.log(a());\n",
        "mod.ts": "export function a(): number { return 1; }\nexport function b(): number { return 2; }\n",
    }
    assert _names(_extract_files(tmp_path, files)) == {"a", "b"}


def test_the_export_surface_is_what_the_filter_serves(tmp_path: Path) -> None:
    files = {
        "index.ts": "export { one, Two } from './mod.js';\n",
        "mod.ts": "export function one(): void {}\nexport class Two {}\nexport class Three {}\n",
    }
    root = _write_tree(tmp_path / "tree", files)
    exported, _entry = ts_lang.export_surface(root, root / "src")
    assert exported == {"one", "Two"}
    types, _ = run_extraction.extract_from_clone(root, MANIFEST)
    assert _names(types) == exported


# -- accessors with modifiers are properties, not methods ---------------------------------------------------------------


def test_a_static_getter_is_a_static_read_only_property_and_not_a_method(tmp_path: Path) -> None:
    source = (
        "export class Box {\n"
        "  static get infinite(): Box { return new Box(); }\n"
        "  static get zero(): number { return 0; }\n"
        "  static set zero(v: number) {}\n"
        "  get size(): number { return 1; }\n"
        "}\n"
    )
    entry = _type(_extract(tmp_path, source), "Box")
    assert entry["methods"] == []
    props = _props(entry)
    assert props["infinite"]["access_mode"] == "readonly"
    assert props["infinite"]["modifiers"] == ["static"]
    assert props["infinite"]["type"] == "Box"
    assert props["zero"]["access_mode"] == "readwrite"
    assert props["zero"]["modifiers"] == ["static"]
    assert props["size"]["access_mode"] == "readonly"
    assert "modifiers" not in props["size"]


def test_a_getter_with_an_access_or_override_modifier_is_a_property(tmp_path: Path) -> None:
    source = (
        "export class Base { get v(): number { return 1; } }\n"
        "export class D extends Base {\n"
        "  public get name(): string { return ''; }\n"
        "  override get v(): number { return 2; }\n"
        "}\n"
    )
    entry = _type(_extract(tmp_path, source), "D")
    assert entry["methods"] == []
    assert {name: p["access_mode"] for name, p in _props(entry).items()} == {
        "name": "readonly",
        "v": "readonly",
    }


def test_a_method_named_get_is_still_a_method(tmp_path: Path) -> None:
    source = (
        "export class Bag {\n"
        "  get(index: number): number { return index; }\n"
        "  static get(key: string): string { return key; }\n"
        "}\n"
    )
    entry = _type(_extract(tmp_path, source), "Bag")
    assert entry["properties"] == []
    assert [m["name"] for m in entry["methods"]] == ["get", "get"]


# -- a renamed re-export is served under the name the package exports --------------------------------------------------


def test_a_renamed_re_export_is_served_under_its_exported_name(tmp_path: Path) -> None:
    files = {
        "index.ts": "export { describe as describeNotRendered, CONSTRUCTS } from './report.js';\n",
        "report.ts": "export function describe(): string { return ''; }\nexport const CONSTRUCTS = ['a'];\n",
        "other.ts": "export function describe(): number { return 1; }\n",
    }
    types = _extract_files(tmp_path, files)
    assert _names(types) == {"describeNotRendered", "CONSTRUCTS"}
    renamed = _type(types, "describeNotRendered")
    assert renamed["file"] == "src/report.ts"
    assert renamed["return_type"] == "string"


def test_an_original_that_is_also_exported_under_its_own_name_stays(tmp_path: Path) -> None:
    files = {
        "index.ts": "export { Foo, Foo as Bar } from './m.js';\n",
        "m.ts": "export class Foo { run(): void {} }\n",
    }
    types = _extract_files(tmp_path, files)
    assert _names(types) == {"Foo", "Bar"}
    assert [m["name"] for m in _type(types, "Bar")["methods"]] == ["run"]
    assert _type(types, "Bar")["class_import"].endswith(".Bar")
    assert _type(types, "Foo")["class_import"].endswith(".Foo")
