"""G3/TC-559 (REQ-G3-009, rows RD3-060 / RD3-061): what the symbol tools return for a symbol that is not implemented.

``get_symbol`` returns the lines that say so in ``limitations`` and keeps the phrase on the member lines;
``list_members`` returns the same member lines (a flagged type first); ``search_symbols`` surfaces the line as
``limitation`` (TC-562). The chunks are built by the real ``build_chunks_from_api_surface`` from hand-written
entries and from the committed 3d/net fixture, then served through the real tools: no network, no container.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from foss_mcp.indexing.chunk_builder import NOT_IMPLEMENTED_PHRASE, build_chunks_from_api_surface
from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.server import _to_jsonable
from foss_mcp.mcp.tools.get_symbol import NotFound, ResolvedSignature, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.mcp.tools.search_symbols import SymbolMatch, search_symbols

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SCOPE = Scope(family="3d", platform="net")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


class _Store:
    """The two calls the tools make of a generation store, over an in-memory lexical index."""

    def __init__(self, texts: list[str]) -> None:
        chunks = [SimpleNamespace(text=text) for text in texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], "gen-tc559")  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc559"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _store_from(*types: dict[str, Any]) -> _Store:
    fixture = {
        "source_repository": "acme/widget",
        "source_commit": "a" * 40,
        "type_count": len(types),
        "types": list(types),
    }
    return _Store([chunk.text for chunk in build_chunks_from_api_surface(fixture, title="widget")])


def _scene() -> dict[str, Any]:
    return {
        "name": "Scene",
        "kind": "class_declaration",
        "class_import": "Acme.Scene",
        "methods": [
            {
                "name": "Render",
                "params": [{"name": "camera", "type": "Camera"}],
                "return_type": "void",
                "stub": True,
                "stub_reason": "throws NotImplementedException",
            },
            {
                "name": "Render",
                "params": [],
                "return_type": "void",
                "stub": True,
                "stub_reason": "throws NotImplementedException",
            },
            {"name": "Open", "params": [], "return_type": "Scene"},
        ],
        "properties": [
            {
                "name": "Frames",
                "type": "int",
                "writable": False,
                "stub": True,
                "stub_reason": "raises NotImplementedError",
            }
        ],
    }


def _format() -> dict[str, Any]:
    return {
        "name": "PdfFormat",
        "kind": "class_declaration",
        "class_import": "Acme.PdfFormat",
        "stub": True,
        "stub_reason": "listed as unsupported",
        "methods": [{"name": "Extract", "params": [], "return_type": "void"}],
        "properties": [],
    }


def _save_format() -> dict[str, Any]:
    return {
        "name": "SaveFormat",
        "kind": "enum_declaration",
        "class_import": "Acme.SaveFormat",
        "methods": [],
        "properties": [],
        "enum_members": [
            {"name": "Pptx", "value": ""},
            {"name": "Pdf", "value": "", "stub": True, "stub_reason": "listed as unsupported"},
        ],
    }


def _plain() -> dict[str, Any]:
    return {
        "name": "Box",
        "kind": "class_declaration",
        "class_import": "Acme.Box",
        "methods": [{"name": "Area", "params": [], "return_type": "int"}],
        "properties": [],
    }


def test_get_symbol_returns_the_lines_that_say_a_member_is_not_implemented() -> None:
    result = get_symbol(_store_from(_scene(), _plain()), SCOPE, "Acme.Scene")  # type: ignore[arg-type]
    assert isinstance(result, SymbolSignature)
    assert len(result.limitations) == 3
    assert all(NOT_IMPLEMENTED_PHRASE in line for line in result.limitations)
    assert any(line.startswith("Render(") for line in result.limitations)
    assert any(line.startswith("Frames:") for line in result.limitations)
    assert not any(line.startswith("Open(") for line in result.limitations)
    assert [line for line in result.methods if NOT_IMPLEMENTED_PHRASE in line] == [
        line for line in result.limitations if line.startswith("Render(")
    ]


def test_get_symbol_of_a_symbol_that_works_has_no_limitations() -> None:
    result = get_symbol(_store_from(_scene(), _plain()), SCOPE, "Acme.Box")  # type: ignore[arg-type]
    assert isinstance(result, SymbolSignature) and result.limitations == ()
    assert NOT_IMPLEMENTED_PHRASE not in result.raw_text


def test_get_symbol_of_a_flagged_type_returns_the_type_level_line_first() -> None:
    result = get_symbol(_store_from(_format(), _plain()), SCOPE, "Acme.PdfFormat")  # type: ignore[arg-type]
    assert isinstance(result, SymbolSignature)
    assert result.limitations == ("Limitation: not implemented in this build: listed as unsupported",)


def test_get_symbol_of_a_member_name_returns_only_that_members_limitations() -> None:
    result = get_symbol(_store_from(_scene(), _plain()), SCOPE, "Acme.Scene.Render")  # type: ignore[arg-type]
    assert isinstance(result, ResolvedSignature) and result.member == "Render"
    assert len(result.member_lines) == 2 and all(
        NOT_IMPLEMENTED_PHRASE in line for line in result.member_lines
    )
    assert result.limitations == result.member_lines
    working = get_symbol(_store_from(_scene(), _plain()), SCOPE, "Acme.Scene.Open")  # type: ignore[arg-type]
    assert isinstance(working, ResolvedSignature) and working.limitations == ()


def test_an_enum_value_with_the_marker_is_still_found_by_its_own_name() -> None:
    store = _store_from(_save_format())
    resolved = get_symbol(store, SCOPE, "Acme.SaveFormat.Pdf")  # type: ignore[arg-type]
    assert isinstance(resolved, ResolvedSignature) and resolved.member == "Pdf"
    assert len(resolved.member_lines) == 1 and NOT_IMPLEMENTED_PHRASE in resolved.member_lines[0]
    assert resolved.limitations == resolved.member_lines
    missing = get_symbol(store, SCOPE, "Acme.SaveFormat.Pdf2")  # type: ignore[arg-type]
    assert isinstance(missing, NotFound)
    whole = get_symbol(store, SCOPE, "Acme.SaveFormat")  # type: ignore[arg-type]
    assert isinstance(whole, SymbolSignature)
    assert whole.members == ("Pptx", "Pdf  (not implemented in this build: listed as unsupported)")


def test_list_members_returns_the_member_lines_with_the_phrase() -> None:
    lines = list_members(_store_from(_scene(), _plain()), SCOPE, "Acme.Scene")  # type: ignore[arg-type]
    assert not isinstance(lines, NotFound)
    assert [line for line in lines if NOT_IMPLEMENTED_PHRASE in line] == [
        line for line in lines if line.startswith(("Render(", "Frames:"))
    ]
    assert sum(NOT_IMPLEMENTED_PHRASE in line for line in lines) == 3
    one = list_members(_store_from(_scene()), SCOPE, "Acme.Scene.Render")  # type: ignore[arg-type]
    assert (
        not isinstance(one, NotFound)
        and len(one) == 2
        and all(NOT_IMPLEMENTED_PHRASE in line for line in one)
    )


def test_list_members_of_a_flagged_type_starts_with_its_limitation() -> None:
    lines = list_members(_store_from(_format(), _plain()), SCOPE, "Acme.PdfFormat")  # type: ignore[arg-type]
    assert not isinstance(lines, NotFound)
    assert lines[0] == "Limitation: not implemented in this build: listed as unsupported"
    assert lines[1].startswith("Extract(")


def test_list_members_of_a_working_type_is_exactly_its_members() -> None:
    lines = list_members(_store_from(_plain(), _scene()), SCOPE, "Acme.Box")  # type: ignore[arg-type]
    assert lines == ("Area() -> int",)


def test_the_limitations_travel_in_the_json_the_server_returns() -> None:
    result = get_symbol(_store_from(_scene()), SCOPE, "Acme.Scene")  # type: ignore[arg-type]
    payload = _to_jsonable(result)
    assert payload["limitations"] and all(NOT_IMPLEMENTED_PHRASE in line for line in payload["limitations"])
    assert json.loads(json.dumps(payload))["limitations"] == payload["limitations"]


@pytest.fixture(scope="module")
def real_3d_net_store() -> _Store:
    fixture = json.loads((FIXTURES / "3d_net" / "api_surface.json").read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title="3d", max_types=1000)
    return _Store([chunk.text for chunk in chunks])


def test_real_data_search_symbols_surfaces_the_phrase_for_3d_net_scene_render(
    real_3d_net_store: _Store,
) -> None:
    found = search_symbols(real_3d_net_store, SCOPE, "Scene.Render")  # type: ignore[arg-type]
    assert isinstance(found, list) and found
    scene = next(match for match in found if match.text.startswith("FQN: Aspose.ThreeD.Scene\n"))
    assert isinstance(scene, SymbolMatch)
    assert NOT_IMPLEMENTED_PHRASE in scene.limitation
    assert "Render" in scene.limitation and "NotImplementedException" in scene.limitation


def test_real_data_get_symbol_and_list_members_agree_on_3d_net_scene_render(
    real_3d_net_store: _Store,
) -> None:
    exact = get_symbol(real_3d_net_store, SCOPE, "Aspose.ThreeD.Scene")  # type: ignore[arg-type]
    assert isinstance(exact, SymbolSignature)
    assert sum(line.startswith("Render(") for line in exact.limitations) == 5
    lines = list_members(real_3d_net_store, SCOPE, "Aspose.ThreeD.Scene.Render")  # type: ignore[arg-type]
    assert not isinstance(lines, NotFound) and len(lines) == 5
    assert all(NOT_IMPLEMENTED_PHRASE in line for line in lines)
    working = list_members(real_3d_net_store, SCOPE, "Aspose.ThreeD.Scene.Save")  # type: ignore[arg-type]
    assert not isinstance(working, NotFound) and working
    assert not any(NOT_IMPLEMENTED_PHRASE in line for line in working)


def test_real_data_a_type_the_scope_page_names_is_flagged_for_every_tool(real_3d_net_store: _Store) -> None:
    for fqn in ("Aspose.ThreeD.Entities.PolygonModifier", "Aspose.ThreeD.Formats.PdfFormat"):
        result = get_symbol(real_3d_net_store, SCOPE, fqn)  # type: ignore[arg-type]
        assert isinstance(result, SymbolSignature)
        assert result.limitations[0].startswith("Limitation: " + NOT_IMPLEMENTED_PHRASE)
        members = list_members(real_3d_net_store, SCOPE, fqn)  # type: ignore[arg-type]
        assert not isinstance(members, NotFound) and members[0] == result.limitations[0]
