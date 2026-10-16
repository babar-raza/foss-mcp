"""TC-525 (REQ-G3-008, REQ-G3-005): Go extraction fidelity.

Offline and deterministic. The committed ``tests/fixtures/go_sample`` package and small trees in
``tmp_path`` go through ``extract_api_surface``: exported constant groups become the Members of
their owning type, methods attach to their (possibly generic) receiver type, a method of an
unexported type is dropped, and an exported method that cannot be attached keeps a receiver mark
instead of passing for a package function.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

GO_SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "go_sample"


def _extract(root: Path) -> list[dict[str, Any]]:
    types, *_ = api_surface.extract_api_surface(get_parser("go"), "go", root, root, "pdf")
    return types


def _tree(tmp_path: Path, source: str) -> list[dict[str, Any]]:
    (tmp_path / "x.go").write_text(source, encoding="utf-8")
    return _extract(tmp_path)


def _by_name(types: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in types}


def _members(entry: dict[str, Any]) -> list[tuple[str, Any]]:
    return [(member["name"], member["value"]) for member in entry.get("enum_members", [])]


def _sample() -> list[dict[str, Any]]:
    return _extract(GO_SAMPLE)


# ------------------------------------------------------------------------------------ constants


def test_const_group_is_folded_into_the_owning_type_members() -> None:
    by_name = _by_name(_sample())
    assert _members(by_name["HAlign"]) == [("HAlignLeft", 0), ("HAlignCenter", 1), ("HAlignRight", 2)]
    assert "HAlignLeft" not in by_name


def test_iota_values_are_positions(tmp_path: Path) -> None:
    types = _tree(
        tmp_path,
        "package p\n\ntype K int\n\nconst (\n\tKZero K = iota\n\tKOne\n\tKTwo\n)\n",
    )
    assert _members(_by_name(types)["K"]) == [("KZero", 0), ("KOne", 1), ("KTwo", 2)]


def test_shifted_iota_values_are_evaluated(tmp_path: Path) -> None:
    types = _tree(
        tmp_path,
        "package p\n\ntype A int\n\nconst (\n\tAOne A = iota + 5\n\tATwo\n)\n\n"
        "type B int\n\nconst (\n\tBOne B = 3 + iota\n\tBTwo\n)\n\n"
        "type C uint\n\nconst (\n\tCOne C = 1 << iota\n\tCTwo\n\tCFour\n)\n",
    )
    by_name = _by_name(types)
    assert _members(by_name["A"]) == [("AOne", 5), ("ATwo", 6)]
    assert _members(by_name["B"]) == [("BOne", 3), ("BTwo", 4)]
    assert _members(by_name["C"]) == [("COne", 1), ("CTwo", 2), ("CFour", 4)]
    assert _members(_by_name(_sample())["Flags"]) == [("FlagA", 1), ("FlagB", 2)]


def test_string_constants_keep_their_literal_text() -> None:
    assert _members(_by_name(_sample())["SaveFormat"]) == [
        ("SaveFormatPDF", '"pdf"'),
        ("SaveFormatHTML", '"html"'),
    ]


def test_implicit_repetition_uses_the_previous_expression_at_its_own_position(tmp_path: Path) -> None:
    types = _tree(
        tmp_path,
        "package p\n\ntype S int\n\nconst (\n\tSFirst S = 10 + iota\n\tSSecond\n\tSThird\n)\n\n"
        'type Q string\n\nconst (\n\tQa Q = "a"\n\tQb\n)\n',
    )
    by_name = _by_name(types)
    assert _members(by_name["S"]) == [("SFirst", 10), ("SSecond", 11), ("SThird", 12)]
    assert _members(by_name["Q"]) == [("Qa", '"a"'), ("Qb", '"a"')]


def test_unexported_constant_is_not_a_member() -> None:
    names = [name for name, _ in _members(_by_name(_sample())["Flags"])]
    assert "flagHidden" not in names


def test_untyped_constant_is_not_attached(tmp_path: Path) -> None:
    types = _tree(tmp_path, "package p\n\ntype T int\n\nconst Max = 100\n\nconst (\n\tLimit = 5\n)\n")
    assert all("enum_members" not in entry for entry in types)
    assert "Max" not in _by_name(types)


def test_constant_of_an_unextracted_type_is_ignored(tmp_path: Path) -> None:
    types = _tree(
        tmp_path,
        "package p\n\ntype hidden int\n\nconst (\n\tHiddenA hidden = iota\n)\n\n"
        "const (\n\tElsewhere Missing = 1\n)\n\ntype Visible struct{}\n",
    )
    assert [entry["name"] for entry in types] == ["Visible"]
    assert "enum_members" not in types[0]


# -------------------------------------------------------------------------------------- methods


def test_method_of_an_exported_receiver_attaches_to_its_type() -> None:
    document = _by_name(_sample())["Document"]
    assert {method["name"] for method in document["methods"]} == {"Save", "Close"}


def test_method_in_a_second_file_attaches_to_the_same_type() -> None:
    document = _by_name(_sample())["Document"]
    close = next(method for method in document["methods"] if method["name"] == "Close")
    assert close["file"].endswith("document_io.go")
    assert "modifiers" not in close


def test_pointer_receiver_method_is_attached_with_its_signature() -> None:
    document = _by_name(_sample())["Document"]
    save = next(method for method in document["methods"] if method["name"] == "Save")
    assert [(p["name"], p["type"]) for p in save["params"]] == [("w", "io.Writer"), ("format", "SaveFormat")]
    assert save["return_type"] == "error"


def test_generic_receiver_method_attaches_to_its_generic_type(tmp_path: Path) -> None:
    pair = _by_name(_sample())["Pair"]
    assert [method["name"] for method in pair["methods"]] == ["Swap"]
    assert [prop["name"] for prop in pair["properties"]] == ["First"]
    types = _tree(
        tmp_path,
        "package p\n\ntype Two[K comparable, V any] struct{}\n\nfunc (t Two[K, V]) Get() {}\n"
        "func (t *Two[K, V]) Put() {}\n",
    )
    assert [method["name"] for method in _by_name(types)["Two"]["methods"]] == ["Get", "Put"]
    assert [entry["name"] for entry in types] == ["Two"]


def test_method_of_an_unexported_receiver_is_dropped() -> None:
    types = _sample()
    assert "Flatten" not in {entry["name"] for entry in types}
    assert all(method["name"] != "Flatten" for entry in types for method in entry["methods"]), (
        "an unexported receiver's method must not be attached anywhere either"
    )


def test_exported_method_of_an_undeclared_receiver_keeps_a_receiver_mark() -> None:
    run = _by_name(_sample())["Run"]
    assert run["kind"] == "function"
    assert run["receiver_type"] == "Missing"
    assert run["modifiers"] == ["receiver *Missing"]


# ---------------------------------------------------------------------------------- functions


def test_exported_package_functions_are_functions_without_a_receiver() -> None:
    functions = {
        entry["name"] for entry in _sample() if entry["kind"] == "function" and not entry.get("receiver_type")
    }
    assert functions == {"Open", "OpenWithPassword", "NewDocument", "NewDocumentFromFormat"}


def test_unexported_function_is_absent() -> None:
    names = {entry["name"] for entry in _sample()}
    assert {"unexported", "pageCount", "helper"}.isdisjoint(names)


def test_unexported_field_is_absent() -> None:
    document = _by_name(_sample())["Document"]
    assert [prop["name"] for prop in document["properties"]] == ["Title"]


def test_e2e_receiver_names_never_appear_as_bare_functions() -> None:
    bare = {
        entry["name"] for entry in _sample() if entry["kind"] == "function" and not entry.get("receiver_type")
    }
    assert bare.isdisjoint({"Save", "Close", "Swap", "Flatten"})
    assert not any(entry["name"] in {"Save", "Close", "Swap", "Flatten"} for entry in _sample())


def test_go_sample_matches_the_expected_entry_set() -> None:
    types = _sample()
    summary = sorted((entry["kind"], entry["name"], entry.get("receiver_type")) for entry in types)
    assert summary == sorted(
        [
            ("type_spec", "HAlign", None),
            ("type_spec", "SaveFormat", None),
            ("type_spec", "Flags", None),
            ("type_spec", "Document", None),
            ("type_spec", "Pair", None),
            ("function", "Open", None),
            ("function", "OpenWithPassword", None),
            ("function", "NewDocument", None),
            ("function", "NewDocumentFromFormat", None),
            ("function", "Run", "Missing"),
        ]
    )
    assert "receiver_type" not in next(entry for entry in types if entry["name"] == "Open")
    assert all("_receiver_text" not in entry for entry in types)


# --------------------------------------------------------------------------- other languages


def test_other_languages_are_unchanged(tmp_path: Path) -> None:
    rust_root = tmp_path / "rust"
    rust_root.mkdir()
    (rust_root / "lib.rs").write_text(
        "pub struct Widget { pub name: String }\n\nimpl Widget {\n    pub fn size(&self) -> u32 { 1 }\n}\n\n"
        "pub fn make() -> Widget { Widget { name: String::new() } }\n",
        encoding="utf-8",
    )
    rust, *_ = api_surface.extract_api_surface(get_parser("rust"), "rust", rust_root, rust_root, "w")
    rust_by_name = _by_name(rust)
    assert rust_by_name["Widget"]["kind"] == "struct_item"
    assert [method["name"] for method in rust_by_name["Widget"]["methods"]] == ["size"]
    assert rust_by_name["make"]["kind"] == "function"
    assert "enum_members" not in rust_by_name["Widget"]
    assert "modifiers" not in rust_by_name["make"]

    java_root = tmp_path / "java"
    java_root.mkdir()
    (java_root / "Widget.java").write_text(
        "package demo;\n\npublic class Widget {\n    public int size() { return 1; }\n}\n",
        encoding="utf-8",
    )
    java, *_ = api_surface.extract_api_surface(get_parser("java"), "java", java_root, java_root, "w")
    java_by_name = _by_name(java)
    assert [method["name"] for method in java_by_name["Widget"]["methods"]] == ["size"]
    assert "enum_members" not in java_by_name["Widget"]
    assert "modifiers" not in java_by_name["Widget"]["methods"][0]
