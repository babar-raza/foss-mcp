"""TC-554: a rendered member is ONE line, so a ``Methods:`` / ``Properties:`` block is never ended early.

``get_symbol``'s ``_extract_block`` reads a block as the run of consecutive lines that start with
``  - ``. A parameter type, return type, property type or default the extraction recorded over several
lines (a TypeScript object-literal type with comments) used to be rendered with its raw newlines, so
the block stopped at the first continuation line and every later member was invisible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing.chunk_builder import _method_lines, build_chunks_from_api_surface
from foss_mcp.mcp.tools.get_symbol import _declared_members, _extract_block

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_HEADERS = ("Methods:", "Properties:", "Members:")
_BULLET = "  - "
_MULTILINE_TYPE = "MarkdownFlowOptions & {\n  /** t */\n  title?: string;\n}"
_COLLAPSED_TYPE = "MarkdownFlowOptions & { /** t */ title?: string; }"


def _chunk_text(types: list[dict[str, Any]]) -> str:
    fixture = {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types}
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=10_000)
    return "\n".join(chunk.text for chunk in chunks)


def _klass(methods: list[dict[str, Any]], properties: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"name": "Doc", "kind": "class_declaration", "methods": methods, "properties": properties or []}


def _method(
    name: str, ptype: str = "string", ret: str = "void", default: str | None = None
) -> dict[str, Any]:
    param: dict[str, Any] = {"name": "options", "type": ptype}
    if default is not None:
        param["default"] = default
    return {"name": name, "params": [param], "return_type": ret}


def test_a_parameter_type_with_a_newline_and_a_comment_renders_one_line() -> None:
    lines = _chunk_text([_klass([_method("AddMarkdown", _MULTILINE_TYPE)])]).splitlines()
    # TC-554: one-line member
    assert f"  - AddMarkdown(options: {_COLLAPSED_TYPE}) -> void" in lines


def test_a_member_after_a_multi_line_parameter_type_is_still_in_the_block() -> None:
    text = _chunk_text(
        [_klass([_method("AddMarkdown", _MULTILINE_TYPE), _method("Merge"), _method("Append")])]
    )
    assert len(_extract_block(text, "Methods:")) == 3
    assert set(_declared_members(text)) == {"AddMarkdown", "Merge", "Append"}


def test_a_return_type_with_a_newline_renders_one_line() -> None:
    ret = "Promise<{\n  ok: boolean;\n  reason?: string;\n}>"
    text = _chunk_text([_klass([_method("Save", ret=ret), _method("Close")])])
    assert "  - Save(options: string) -> Promise<{ ok: boolean; reason?: string; }>" in text.splitlines()
    assert len(_extract_block(text, "Methods:")) == 2


def test_a_property_type_with_a_newline_renders_one_line() -> None:
    props = [
        {"name": "style", "type": ": {\n  bold?: boolean;\n  italic?: boolean;\n}", "writable": True},
        {"name": "after", "type": ": number", "writable": True},
    ]
    text = _chunk_text([_klass([], props)])
    assert "  - style: { bold?: boolean; italic?: boolean; } (writable)" in text.splitlines()
    assert len(_extract_block(text, "Properties:")) == 2


def test_a_default_with_a_newline_renders_one_line() -> None:
    text = _chunk_text(
        [_klass([_method("Open", "OpenOptions", default="{\n  mode: 'r',\n  flags: 0\n}"), _method("Z")])]
    )
    assert "  - Open(options: OpenOptions = { mode: 'r', flags: 0 }) -> void" in text.splitlines()
    assert len(_extract_block(text, "Methods:")) == 2


def test_a_default_that_is_only_whitespace_is_no_default() -> None:
    text = _chunk_text([_klass([_method("Open", "OpenOptions", default="\n  \n")])])
    assert "  - Open(options: OpenOptions) -> void" in text.splitlines()


def test_a_tab_is_collapsed_to_one_space() -> None:
    text = _chunk_text([_klass([_method("M", "A\t&\tB")])])
    assert "  - M(options: A & B) -> void" in text.splitlines()


def test_a_carriage_return_line_break_is_collapsed_to_one_space() -> None:
    text = _chunk_text([_klass([_method("M", "A &\r\n  B"), _method("N")])])
    assert "  - M(options: A & B) -> void" in text.splitlines()
    assert len(_extract_block(text, "Methods:")) == 2
    assert "\r" not in text


def test_a_plain_union_type_is_byte_identical() -> None:
    text = _chunk_text([_klass([_method("M", "string | string[]")])])
    assert "  - M(options: string | string[]) -> void" in text.splitlines()


def test_a_python_callable_type_is_byte_identical() -> None:
    text = _chunk_text([_klass([_method("M", "Callable[[int], str]", ret="Callable[[int], str]")])])
    assert "  - M(options: Callable[[int], str]) -> Callable[[int], str]" in text.splitlines()


def test_a_type_with_single_spaces_is_byte_identical() -> None:
    text = _chunk_text([_klass([_method("M", "Map<string, number>", default="new Map()")])])
    assert "  - M(options: Map<string, number> = new Map()) -> void" in text.splitlines()


def test_a_leading_declaration_colon_is_still_removed() -> None:
    props = [{"name": "p", "type": ":   string", "writable": True}]
    assert "  - p: string (writable)" in _chunk_text([_klass([], props)]).splitlines()


def test_a_double_space_inside_a_string_literal_type_is_collapsed_too() -> None:
    # Deliberate: a rendered type is a display line, not a value, so every whitespace run is one space.
    text = _chunk_text([_klass([_method("M", "'a  b' | 'c'")])])
    assert "  - M(options: 'a b' | 'c') -> void" in text.splitlines()


def test_an_empty_type_still_renders_no_type() -> None:
    text = _chunk_text([_klass([{"name": "M", "params": [{"name": "x", "type": "  \n "}]}])])
    assert "  - M(x)" in text.splitlines()


def _load(fixture: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (_FIXTURES / fixture / "api_surface.json").read_text(encoding="utf-8")
    )
    return loaded


_FIXTURE_NAMES = ["pdf_typescript", "cells_typescript", "3d_typescript", "pdf_cpp"]
_CHUNKS: dict[str, list[Any]] = {}


def _chunks(fixture: str) -> list[Any]:
    if fixture not in _CHUNKS:
        data = _load(fixture)
        _CHUNKS[fixture] = build_chunks_from_api_surface(
            data, title=fixture, max_types=int(data["type_count"])
        )
    return _CHUNKS[fixture]


def _block_after(lines: list[str], header: str) -> tuple[list[str], str | None]:
    """The run of lines after *header* up to the next line that does not start with ``  - ``, and that line."""
    run: list[str] = []
    for line in lines[lines.index(header) + 1 :]:
        if not line.startswith(_BULLET):
            return run, line
        run.append(line)
    return run, None


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_every_member_block_of_a_real_fixture_has_no_continuation_line(fixture: str) -> None:
    broken: list[str] = []
    checked = 0
    for chunk in _chunks(fixture):
        lines = chunk.text.splitlines()
        for header in _HEADERS:
            if header not in lines:
                continue
            checked += 1
            _, stopper = _block_after(lines, header)
            if stopper not in (None, ""):
                broken.append(f"{header} stopped at {stopper!r}")
    assert checked > 0
    assert broken == []


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_extract_block_length_equals_the_member_lines_of_every_real_chunk(fixture: str) -> None:
    mismatched: list[str] = []
    for chunk in _chunks(fixture):
        lines = chunk.text.splitlines()
        for header in _HEADERS:
            if header not in lines:
                continue
            start = lines.index(header) + 1
            blank = lines.index("", start) if "" in lines[start:] else len(lines)
            under_header = [line for line in lines[start:blank] if line.startswith(_BULLET)]
            if len(_extract_block(chunk.text, header)) != len(under_header):
                mismatched.append(f"{header}: {chunk.text.splitlines()[0]}")
    assert mismatched == []


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_no_member_line_of_a_real_fixture_carries_a_whitespace_run(fixture: str) -> None:
    offenders = [
        line
        for chunk in _chunks(fixture)
        for header in _HEADERS
        if header in chunk.text.splitlines()
        for line in _block_after(chunk.text.splitlines(), header)[0]
        if "\t" in line or "\r" in line
    ]
    assert offenders == []


def _entry_and_chunk(fixture: str, class_import: str) -> tuple[dict[str, Any], str]:
    entry = next(e for e in _load(fixture)["types"] if e.get("class_import") == class_import)
    chunk = next(c for c in _chunks(fixture) if f"\nFQN: {class_import}\n" in f"\n{c.text}\n")
    return entry, chunk.text


@pytest.mark.parametrize(
    ("class_import", "members"),
    [
        ("document.Document", ("Merge", "Append", "Redact", "RemovePage", "AddMarkdown")),
        ("page.Page", ("AddImage", "AddTable", "ToSvg", "Redact")),
    ],
)
def test_the_central_pdf_typescript_classes_declare_their_late_members(
    class_import: str, members: tuple[str, ...]
) -> None:
    _, text = _entry_and_chunk("pdf_typescript", class_import)
    declared = _declared_members(text)
    assert [name for name in members if name not in declared] == []


@pytest.mark.parametrize("class_import", ["document.Document", "page.Page"])
def test_the_whole_method_list_of_a_central_class_is_in_the_block(class_import: str) -> None:
    entry, text = _entry_and_chunk("pdf_typescript", class_import)
    expected = _method_lines(entry["methods"])
    assert len(expected) > 0
    assert list(_extract_block(text, "Methods:")) == [line[len(_BULLET) :].strip() for line in expected]
    # Only an inherited constructor or a verbatim duplicate may be missing from the rendered list.
    inherited_constructors = sum(
        1 for m in entry["methods"] if m.get("inherited_from") and m.get("is_constructor") is True
    )
    assert len(entry["methods"]) - len(expected) >= inherited_constructors
    assert len(expected) >= len({m["name"] for m in entry["methods"]}) - inherited_constructors


def test_the_multi_line_member_that_ended_the_document_block_is_one_line() -> None:
    entry, text = _entry_and_chunk("pdf_typescript", "document.Document")
    assert any("\n" in str(p.get("type")) for m in entry["methods"] for p in m["params"])
    add_markdown = _declared_members(text)["AddMarkdown"]
    assert len(add_markdown) >= 1
    assert all("\n" not in line for line in add_markdown)
