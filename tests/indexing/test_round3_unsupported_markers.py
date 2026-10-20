"""G3/TC-559 (REQ-G3-009, rows RD3-060 / RD3-061): how a stub or documented-unsupported symbol is rendered.

The contract is one fixed phrase, ``not implemented in this build``. It ends the line of the member that is
flagged (``  - Render(...)  (not implemented in this build: <reason>)``), leads a ``Limitation:`` line under the
``Kind:`` line for a flagged type, and appears nowhere for a symbol that works. Every expectation is a hand-written
entry in the shape the extraction fixture has; one test per pilot also goes through the committed fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from foss_mcp.indexing import chunk_builder
from foss_mcp.indexing.chunk_builder import NOT_IMPLEMENTED_PHRASE, build_chunks_from_api_surface
from foss_mcp.mcp.tools import search_symbols

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(*types: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_repository": "acme/widget",
        "source_commit": "a" * 40,
        "type_count": len(types),
        "types": list(types),
    }


def _text(*types: dict[str, Any]) -> str:
    chunks = build_chunks_from_api_surface(_fixture(*types), title="widget")
    return "\n".join(chunk.text for chunk in chunks)


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _with(text: str, needle: str) -> list[str]:
    return [line for line in _lines(text) if needle in line]


def _class(name: str = "Scene", **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "kind": "class_declaration",
        "class_import": f"Acme.{name}",
        "methods": [],
        "properties": [],
    }
    entry.update(extra)
    return entry


def test_the_phrase_is_the_fixed_contract_phrase_search_symbols_surfaces() -> None:
    assert NOT_IMPLEMENTED_PHRASE == "not implemented in this build"
    assert search_symbols.NOT_IMPLEMENTED_PHRASE == NOT_IMPLEMENTED_PHRASE


def test_a_stub_method_line_ends_with_the_phrase_and_its_reason() -> None:
    text = _text(
        _class(
            methods=[
                {
                    "name": "Render",
                    "params": [{"name": "camera", "type": "Camera"}],
                    "return_type": "void",
                    "stub": True,
                    "stub_reason": "throws NotImplementedException",
                },
                {"name": "Open", "params": [], "return_type": "Scene"},
            ]
        )
    )
    assert _with(text, "Render(") == [
        "  - Render(camera: Camera) -> void  (not implemented in this build: throws NotImplementedException)"
    ]
    assert _with(text, "Open(") == ["  - Open() -> Scene"]


def test_a_stub_without_a_reason_still_carries_the_bare_phrase() -> None:
    text = _text(_class(methods=[{"name": "Render", "params": [], "return_type": "void", "stub": True}]))
    assert _with(text, "Render(") == ["  - Render() -> void  (not implemented in this build)"]


def test_a_member_that_is_not_flagged_true_renders_exactly_as_before() -> None:
    plain = _class(methods=[{"name": "Render", "params": [], "return_type": "void"}])
    falsy = _class(
        methods=[{"name": "Render", "params": [], "return_type": "void", "stub": False, "stub_reason": "x"}]
    )
    assert _text(plain) == _text(falsy)
    assert NOT_IMPLEMENTED_PHRASE not in _text(plain)


def test_a_stub_property_line_carries_the_phrase_after_its_writability() -> None:
    text = _text(
        _class(
            properties=[
                {
                    "name": "Frames",
                    "type": "int",
                    "writable": False,
                    "stub": True,
                    "stub_reason": "raises NotImplementedError",
                },
                {"name": "Name", "type": "str", "writable": True},
            ]
        )
    )
    assert _with(text, "Frames") == [
        "  - Frames: int (read-only)  (not implemented in this build: raises NotImplementedError)"
    ]
    assert _with(text, "  - Name") == ["  - Name: str (writable)"]


def test_an_enum_value_the_scope_documentation_lists_as_unsupported_carries_the_phrase() -> None:
    text = _text(
        _class(
            "SaveFormat",
            kind="enum_declaration",
            enum_members=[
                {"name": "Pptx", "value": ""},
                {"name": "Pdf", "value": "", "stub": True, "stub_reason": "listed as unsupported"},
                {"name": "Xps", "value": "7", "stub": True},
            ],
        )
    )
    assert _with(text, "  - Pptx") == ["  - Pptx"]
    assert _with(text, "  - Pdf") == ["  - Pdf  (not implemented in this build: listed as unsupported)"]
    assert _with(text, "  - Xps") == ["  - Xps = 7  (not implemented in this build)"]


def test_a_flagged_type_states_its_limitation_on_the_line_after_kind() -> None:
    text = _text(
        _class(
            "PdfFormat",
            stub=True,
            stub_reason="listed as unsupported",
            methods=[{"name": "Save", "params": []}],
        )
    )
    lines = _lines(text)
    kind_at = lines.index("Kind: class_declaration")
    assert lines[kind_at + 1] == "Limitation: not implemented in this build: listed as unsupported"
    assert not _with(text, "  - Save(") or NOT_IMPLEMENTED_PHRASE not in _with(text, "  - Save(")[0]


def test_a_flagged_free_function_says_it_once_on_its_own_signature_line() -> None:
    text = _text(
        {
            "name": "convert",
            "kind": "function",
            "class_import": "pkg.convert",
            "methods": [],
            "properties": [],
            "params": [{"name": "path", "type": "str"}],
            "return_type": "None",
            "stub": True,
            "stub_reason": "raises NotImplementedError",
        }
    )
    assert "Limitation:" not in text
    assert text.count(NOT_IMPLEMENTED_PHRASE) == 1
    assert _with(text, "convert(")[0].endswith("(not implemented in this build: raises NotImplementedError)")


def test_a_stub_inherited_by_a_subclass_keeps_the_phrase_after_the_inherited_from_suffix() -> None:
    text = _text(
        _class(
            "Child",
            methods=[
                {
                    "name": "Render",
                    "params": [],
                    "return_type": "void",
                    "inherited_from": "Acme.Base",
                    "stub": True,
                    "stub_reason": "raises NotImplementedError",
                }
            ],
        )
    )
    assert _with(text, "Render(") == [
        "  - Render() -> void  (inherited from Acme.Base)  (not implemented in this build: raises NotImplementedError)"
    ]


def test_a_reason_is_one_line_and_clipped() -> None:
    long_reason = "throws X: " + "word " * 80 + "\nsecond line"
    text = _text(_class(methods=[{"name": "Go", "params": [], "stub": True, "stub_reason": long_reason}]))
    line = _with(text, "Go(")[0]
    assert "\n" not in line and line.endswith("...)")
    assert (
        len(line) < len("  - Go()  (not implemented in this build: )") + chunk_builder._STUB_REASON_LIMIT + 5
    )


def test_a_re_export_shell_shows_the_limitation_of_the_definition_it_is_given() -> None:
    definition = _class(
        "Texture",
        file="pkg/texture.py",
        class_import="pkg.texture.Texture",
        stub=True,
        stub_reason="documented",
        methods=[{"name": "load", "params": []}],
    )
    shell = _class(
        "Texture", file="pkg/__init__.py", class_import="pkg.Texture", reexport_of="pkg.texture.Texture"
    )
    chunks = build_chunks_from_api_surface(_fixture(definition, shell), title="pkg")
    assert all("Limitation: not implemented in this build" in chunk.text for chunk in chunks)


def test_3d_net_chunk_of_scene_carries_the_phrase_on_every_render_overload() -> None:
    fixture = json.loads((FIXTURES / "3d_net" / "api_surface.json").read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title="3d", max_types=1000)
    scene = next(chunk for chunk in chunks if chunk.text.startswith("FQN: Aspose.ThreeD.Scene\n"))
    renders = _with(scene.text, "  - Render(")
    assert len(renders) == 5
    assert all(NOT_IMPLEMENTED_PHRASE in line and "NotImplementedException" in line for line in renders)
    assert not any(NOT_IMPLEMENTED_PHRASE in line for line in _with(scene.text, "  - Open("))


def test_a_fixture_with_no_stub_key_anywhere_renders_without_the_phrase() -> None:
    fixture = json.loads((FIXTURES / "cells_go" / "api_surface.json").read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title="cells")
    assert chunks and not any(NOT_IMPLEMENTED_PHRASE in chunk.text for chunk in chunks)
