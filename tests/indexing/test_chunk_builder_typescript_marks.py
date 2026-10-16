"""TC-523: symbol chunks render TypeScript optional, read-only, static, async, defaulted and aliased members.

An optional member renders ``x: number [optional] (writable)`` - the mark goes after the type, never into
the name, so ``search_symbols`` still takes ``x`` as the member name. An optional parameter renders
``x?: number``; a parameter with a default renders ``opts: OpenOptions = {}``. A type alias prints its
right-hand side on an ``Alias-Of:`` line that ``get_symbol``'s block parsing ignores.
"""

from __future__ import annotations

from typing import Any

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import _extract_block, _parse_signature
from foss_mcp.mcp.tools.search_symbols import _member_name


def _fixture(types: list[dict[str, Any]]) -> dict[str, Any]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types}


def _text(types: list[dict[str, Any]]) -> str:
    chunks = build_chunks_from_api_surface(_fixture(types), title="t", max_types=10_000)
    return "\n".join(chunk.text for chunk in chunks)


def _lines(types: list[dict[str, Any]]) -> list[str]:
    return _text(types).splitlines()


def _interface(
    properties: list[dict[str, Any]], methods: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "name": "Style",
        "kind": "interface_declaration",
        "methods": methods or [],
        "properties": properties,
    }


def _alias(**extra: Any) -> dict[str, Any]:
    return {"name": "ArrowShape", "kind": "type_alias_declaration", "methods": [], "properties": [], **extra}


def test_an_optional_property_renders_the_mark_before_the_writability() -> None:
    prop = {"name": "x", "type": ": number", "writable": True, "modifiers": ["optional"]}
    assert "  - x: number [optional] (writable)" in _lines([_interface([prop])])


def test_a_read_only_property_renders_read_only() -> None:
    prop = {"name": "y", "type": ": string", "writable": False}
    assert "  - y: string (read-only)" in _lines([_interface([prop])])


def test_a_static_async_method_gets_the_suffix() -> None:
    method = {
        "name": "open",
        "params": [{"name": "buf", "type": "Uint8Array"}],
        "return_type": "Promise<Document>",
        "modifiers": ["static", "async"],
    }
    lines = _lines([{"name": "Document", "kind": "class_declaration", "methods": [method], "properties": []}])
    assert "  - open(buf: Uint8Array) -> Promise<Document> [static, async]" in lines


def test_an_optional_interface_method_gets_the_optional_suffix() -> None:
    method = {"name": "m", "params": [], "return_type": "void", "modifiers": ["optional"]}
    assert "  - m() -> void [optional]" in _lines([_interface([], [method])])


def test_a_defaulted_parameter_renders_name_type_equals_default() -> None:
    method = {
        "name": "open",
        "params": [
            {"name": "buf", "type": "Uint8Array"},
            {"name": "opts", "type": "OpenOptions", "default": "{}"},
        ],
        "return_type": "Promise<Document>",
    }
    lines = _lines([{"name": "Document", "kind": "class_declaration", "methods": [method], "properties": []}])
    assert "  - open(buf: Uint8Array, opts: OpenOptions = {}) -> Promise<Document>" in lines


def test_an_optional_parameter_renders_a_question_mark() -> None:
    method = {
        "name": "save",
        "params": [
            {"name": "path", "type": "string", "optional": True},
            {"name": "extra", "type": "", "optional": True},
        ],
        "return_type": "void",
    }
    lines = _lines([{"name": "Document", "kind": "class_declaration", "methods": [method], "properties": []}])
    assert "  - save(path?: string, extra?) -> void" in lines


def test_a_parameter_with_a_default_is_not_also_marked_optional() -> None:
    method = {
        "name": "run",
        "params": [{"name": "n", "type": "number", "default": "1", "optional": True}],
        "return_type": "",
    }
    lines = _lines([{"name": "C", "kind": "class_declaration", "methods": [method], "properties": []}])
    assert "  - run(n: number = 1)" in lines


def test_a_parameter_without_the_new_keys_renders_as_before() -> None:
    method = {
        "name": "scale",
        "params": [{"name": "factor", "type": "float"}, {"name": "raw", "type": ""}],
        "return_type": "void",
    }
    lines = _lines([{"name": "C", "kind": "class_declaration", "methods": [method], "properties": []}])
    assert "  - scale(factor: float, raw) -> void" in lines


def test_the_alias_line_follows_the_kind_line_and_precedes_the_description() -> None:
    lines = _lines([_alias(alias_of="'arrow' | 'rightArrow'", doc="Arrow shapes.")])
    kind = lines.index("Kind: type_alias_declaration")
    assert lines[kind + 1] == "Alias-Of: 'arrow' | 'rightArrow'"
    assert lines[kind + 2] == "Arrow shapes."


def test_the_alias_line_comes_after_the_deprecated_line() -> None:
    lines = _lines([_alias(alias_of="'a' | 'b'", deprecated=True, deprecated_reason="use Other")])
    assert lines.index("Deprecated: use Other") < lines.index("Alias-Of: 'a' | 'b'")


def test_an_entry_without_the_alias_key_renders_no_alias_line() -> None:
    assert not any(line.startswith("Alias-Of:") for line in _lines([_alias()]))
    assert not any(line.startswith("Alias-Of:") for line in _lines([_alias(alias_of="")]))
    assert not any(line.startswith("Alias-Of:") for line in _lines([_interface([])]))


def test_an_alias_text_is_printed_on_one_line() -> None:
    lines = _lines([_alias(alias_of="'a'\n   | 'b'")])
    assert "Alias-Of: 'a' | 'b'" in lines


def test_a_rendered_optional_members_name_still_parses_to_the_bare_name() -> None:
    props = [
        {"name": "x", "type": ": number", "writable": True, "modifiers": ["optional"]},
        {"name": "y", "type": ": string", "writable": False},
    ]
    methods = [
        {
            "name": "m",
            "params": [{"name": "a", "type": "number"}],
            "return_type": "void",
            "modifiers": ["optional"],
        },
        {
            "name": "save",
            "params": [{"name": "path", "type": "string", "optional": True}],
            "return_type": "void",
        },
    ]
    text = _text([_interface(props, methods)])
    assert [_member_name(item) for item in _extract_block(text, "Properties:")] == ["x", "y"]
    assert [_member_name(item) for item in _extract_block(text, "Methods:")] == ["m", "save"]


def test_get_symbol_block_parsing_ignores_the_alias_line() -> None:
    entry = _alias(
        alias_of="'a' | 'b'",
        properties=[{"name": "depth", "type": ": number", "writable": True}],
    )
    with_alias = _text([entry])
    without_alias = _text([{key: value for key, value in entry.items() if key != "alias_of"}])
    assert "Alias-Of: 'a' | 'b'" in with_alias
    parsed = _parse_signature(_scope(), "g", "d", "c", with_alias)
    baseline = _parse_signature(_scope(), "g", "d", "c", without_alias)
    assert parsed.kind == baseline.kind == "type_alias_declaration"
    assert parsed.fqn == baseline.fqn
    assert parsed.properties == baseline.properties == ("depth: number (writable)",)
    assert parsed.methods == baseline.methods == ()
    assert parsed.bases == baseline.bases == ()
    assert parsed.members == baseline.members == ()


def _scope() -> Scope:
    return Scope(family="x", platform="typescript")
