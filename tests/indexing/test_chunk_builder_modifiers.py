"""TC-396: symbol chunks render default values and member modifiers without disturbing member-name parsing.

A parameter with a ``default`` renders ``name: type = default``; a member with ``modifiers`` gets a
``[static]`` / ``[static, async]`` suffix after its return/property type and before any
``(inherited from ...)`` / ``(deprecated...)`` suffix. Members without the new keys render exactly
as they did before.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.mcp.tools.search_symbols import _member_name


def _fixture(types: list[dict[str, object]]) -> dict[str, object]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types}


def _lines(fixture: Mapping[str, object]) -> list[str]:
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=10_000)
    return "\n".join(chunk.text for chunk in chunks).splitlines()


def _type(methods: list[dict[str, object]], properties: list[dict[str, object]] | None = None):
    return _fixture([{"name": "Doc", "kind": "class", "methods": methods, "properties": properties or []}])


def test_a_parameter_default_renders_after_the_type() -> None:
    lines = _lines(
        _type(
            [
                {
                    "name": "Save",
                    "params": [{"name": "x", "type": "int", "default": "3"}],
                    "return_type": "void",
                }
            ]
        )
    )
    assert "  - Save(x: int = 3) -> void" in lines


def test_a_string_default_is_rendered_with_its_source_text() -> None:
    method = {"name": "Open", "params": [{"name": "mode", "type": "string", "default": '"rb"'}]}
    assert '  - Open(mode: string = "rb")' in _lines(_type([method]))


def test_a_default_on_an_untyped_parameter_still_renders() -> None:
    method = {"name": "Open", "params": [{"name": "n", "default": "null"}]}
    assert "  - Open(n = null)" in _lines(_type([method]))


def test_an_empty_default_is_not_rendered() -> None:
    method = {"name": "Open", "params": [{"name": "n", "type": "int", "default": ""}]}
    assert "  - Open(n: int)" in _lines(_type([method]))


def test_mixed_defaulted_and_plain_parameters() -> None:
    method = {
        "name": "Cut",
        "params": [
            {"name": "a", "type": "int"},
            {"name": "b", "type": "int", "default": "0"},
            {"name": "docs", "type": "params Document[]"},
        ],
        "return_type": "bool",
    }
    assert "  - Cut(a: int, b: int = 0, docs: params Document[]) -> bool" in _lines(_type([method]))


def test_a_static_method_gets_a_suffix_after_the_return_type() -> None:
    method = {"name": "Create", "params": [], "return_type": "Doc", "modifiers": ["static"]}
    assert "  - Create() -> Doc [static]" in _lines(_type([method]))


def test_several_modifiers_are_comma_separated_in_order() -> None:
    method = {"name": "Run", "params": [], "return_type": "Task", "modifiers": ["static", "async"]}
    assert "  - Run() -> Task [static, async]" in _lines(_type([method]))


def test_a_method_without_a_return_type_still_gets_the_suffix() -> None:
    method = {"name": "Init", "params": [], "modifiers": ["static"]}
    assert "  - Init() [static]" in _lines(_type([method]))


def test_empty_or_missing_modifiers_render_exactly_as_before() -> None:
    methods = [
        {"name": "A", "params": [], "return_type": "int"},
        {"name": "B", "params": [], "return_type": "int", "modifiers": []},
        {"name": "C", "params": [], "return_type": "int", "modifiers": None},
    ]
    lines = _lines(_type(methods))
    assert "  - A() -> int" in lines
    assert "  - B() -> int" in lines
    assert "  - C() -> int" in lines
    assert not any("[" in line for line in lines if line.startswith("  - "))


def test_modifiers_come_before_the_inherited_and_deprecated_suffixes() -> None:
    method = {
        "name": "Old",
        "params": [],
        "return_type": "int",
        "modifiers": ["virtual"],
        "inherited_from": "Base",
        "deprecated": True,
        "deprecated_reason": "gone",
    }
    assert "  - Old() -> int [virtual]  (inherited from Base) (deprecated: gone)" in _lines(_type([method]))


def test_a_deprecated_method_with_modifiers_keeps_the_deprecated_suffix_last() -> None:
    method = {"name": "Old", "params": [], "return_type": "int", "modifiers": ["static"], "deprecated": True}
    assert "  - Old() -> int [static] (deprecated)" in _lines(_type([method]))


def test_a_static_property_gets_the_suffix_after_its_type() -> None:
    prop = {"name": "Default", "type": "Doc", "writable": False, "modifiers": ["static"]}
    assert "  - Default: Doc [static] (read-only)" in _lines(_type([], [prop]))


def test_a_property_with_modifiers_keeps_inherited_and_deprecated_suffixes_after_it() -> None:
    prop = {
        "name": "Id",
        "type": "int",
        "modifiers": ["override"],
        "inherited_from": "Base",
        "deprecated": True,
    }
    assert "  - Id: int [override]  (inherited from Base) (deprecated)" in _lines(_type([], [prop]))


def test_a_property_without_modifiers_is_unchanged() -> None:
    prop = {"name": "Name", "type": "string", "writable": True}
    assert "  - Name: string (writable)" in _lines(_type([], [prop]))


def test_an_indexer_property_renders_with_its_bracketed_name() -> None:
    prop = {
        "name": "this[string]",
        "type": "Cell",
        "writable": False,
        "params": [{"name": "k", "type": "string"}],
    }
    assert "  - this[string]: Cell (read-only)" in _lines(_type([], [prop]))


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (
            {
                "name": "Create",
                "params": [{"name": "x", "type": "int", "default": "3"}],
                "modifiers": ["static"],
            },
            "Create",
        ),
        ({"name": "Run", "params": [], "return_type": "Task", "modifiers": ["static", "async"]}, "Run"),
        ({"name": "Old", "params": [], "modifiers": ["virtual"], "inherited_from": "Base"}, "Old"),
        ({"name": "Plain", "params": [{"name": "a", "type": "int"}], "return_type": "int"}, "Plain"),
    ],
)
def test_search_symbols_member_name_still_reads_the_bare_method_name(
    member: dict[str, object], expected: str
) -> None:
    line = next(line for line in _lines(_type([member])) if line.startswith("  - "))
    assert _member_name(line.strip().removeprefix("- ")) == expected


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        ({"name": "Default", "type": "Doc", "modifiers": ["static"]}, "Default"),
        ({"name": "Id", "type": "int", "modifiers": ["override"], "inherited_from": "Base"}, "Id"),
        ({"name": "this[int, string]", "type": "Cell", "modifiers": ["virtual"]}, "this[int, string]"),
        ({"name": "Plain", "type": "int", "writable": True}, "Plain"),
    ],
)
def test_search_symbols_member_name_still_reads_the_bare_property_name(
    prop: dict[str, object], expected: str
) -> None:
    line = next(line for line in _lines(_type([], [prop])) if line.startswith("  - "))
    assert _member_name(line.strip().removeprefix("- ")) == expected


def test_nothing_is_ever_rendered_before_the_member_name() -> None:
    member = {"name": "Make", "params": [], "return_type": "Doc", "modifiers": ["static", "sealed"]}
    prop = {"name": "P", "type": "int", "modifiers": ["static"]}
    lines = [line for line in _lines(_type([member], [prop])) if line.startswith("  - ")]
    assert lines
    assert all(line.startswith(("  - Make(", "  - P:")) for line in lines)
