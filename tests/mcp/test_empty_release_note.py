"""G3/TC-542 (REQ-G3-008): an empty release list is explained beside the bare list.

An independent review found that ``list_recent_changes`` answers a bare ``[]`` for 15 of the 40 pilots
(repositories that publish no GitHub release), which an agent cannot tell from a failed fetch or a missing
capability. The list stays a bare list (TC-507 and TC-519 pin that shape); a successful, empty answer
gains ``structuredContent.note`` and a second text block carrying one fixed sentence. A non-empty answer,
an error and every other tool gain nothing.

Offline and deterministic: the through-the-handler scope store is a COPY of a committed scope directory
and the embedding gateway settings are removed from the environment.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.response_envelope import NO_RELEASES_NOTE, NOTE_KEY, empty_result_note
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import TOOL_DESCRIPTIONS, ToolErrorCode, create_server
from foss_mcp.mcp.sidecar_state import RELEASES_FETCH_FAILED_MESSAGE, SIDECAR_FETCH_FAILED

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SERVER_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "server.py"
GUARD_SECONDS = 60
SENTENCE = (
    "This repository has no published GitHub releases, so there is nothing to list. "
    "An empty list is a complete answer, not a fetch failure (a failed fetch is reported as an error). "
    "Use search_docs for changelog or upgrade notes in the documentation."
)
RELEASES = (
    Release(tag_name="v2.0.0", body="Breaking: the foo API moved to bar.", richness="detailed"),
    Release(tag_name="v1.0.0", body="Initial release.", richness="none"),
)
BASE_KEYS = {"result", "scope", "content_origin", "response_budget"}


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


def _server(tmp_path: Path, family: str, platform: str, *, copy_scope: bool = True, **kwargs: Any) -> Any:
    directory = f"{family}__{platform}__self_extracted"
    if copy_scope:
        shutil.copytree(DEMO_MANIFESTS / directory, tmp_path / directory)
    return create_server(
        DeploymentConfig(family=family, platform=platform), GenerationManifestStore(tmp_path), **kwargs
    )


def _call_raw(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


def _call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    result = _call_raw(server, name, arguments)
    assert result.is_error is False, result.content
    assert result.structured_content is not None
    return result


def _release_from(entry: dict[str, Any]) -> Release:
    return Release(
        tag_name=entry["tag_name"],
        body=entry["body"],
        richness=entry["richness"],
        published_at=entry.get("published_at"),
        url=entry.get("url"),
    )


def _sidecar_releases(family: str, platform: str) -> tuple[Release, ...]:
    raw = json.loads(
        (DEMO_MANIFESTS / f"recent_releases_{family}_{platform}.json").read_text(encoding="utf-8")
    )
    return tuple(_release_from(entry) for entry in raw)


def _pilots() -> list[tuple[str, str]]:
    pilots = []
    for path in sorted(DEMO_MANIFESTS.glob("recent_releases_*.json")):
        family, _, platform = path.stem.removeprefix("recent_releases_").partition("_")
        pilots.append((family, platform))
    return pilots


# --- the pure function ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "payload", "expected"),
    [
        ("list_recent_changes", [], SENTENCE),
        ("list_recent_changes", [{"tag_name": "v1"}], None),
        ("list_recent_changes", [{}], None),
        ("list_recent_changes", {}, None),
        ("list_recent_changes", {"result": []}, None),
        ("list_recent_changes", (), None),
        ("list_recent_changes", None, None),
        ("list_members", (), None),
        ("list_members", [], None),
        ("list_members", ["A", "B"], None),
        ("search_symbols", [], None),
        ("search_docs", [], None),
        ("lookup", [], None),
        ("find_examples", [], None),
        ("get_product_reference", {}, None),
        ("report_index_freshness", {}, None),
        ("get_symbol", {"fqn": "X"}, None),
    ],
)
def test_the_note_is_said_only_for_an_empty_list_recent_changes(
    tool: str, payload: Any, expected: str | None
) -> None:
    assert empty_result_note(tool, payload) == expected


def test_the_note_names_search_docs_and_is_under_400_characters() -> None:
    assert NO_RELEASES_NOTE == SENTENCE
    assert NOTE_KEY == "note"
    assert "search_docs" in NO_RELEASES_NOTE
    assert "not a fetch failure" in NO_RELEASES_NOTE
    assert "no published GitHub releases" in NO_RELEASES_NOTE
    assert len(NO_RELEASES_NOTE) < 400


# --- through the real tools/call handler ----------------------------------------------------------


def test_an_empty_list_through_the_real_tools_call_handler_keeps_the_bare_list_and_adds_the_note(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path, "pdf", "python", recent_releases=())
    result = _call(server, "list_recent_changes", {})
    structured = result.structured_content
    assert structured is not None
    assert structured["result"] == []
    assert structured["note"] == SENTENCE
    assert set(structured) == BASE_KEYS | {"note"}
    assert structured["scope"]["family"] == "pdf" and structured["scope"]["platform"] == "python"
    assert structured["content_origin"] == "upstream_repository"
    assert len(result.content) == 2
    assert result.content[0].text == "[]"  # type: ignore[union-attr]
    assert result.content[1].text == SENTENCE  # type: ignore[union-attr]


def test_a_non_empty_answer_has_no_note_and_one_text_block(tmp_path: Path) -> None:
    server = _server(tmp_path, "pdf", "python", recent_releases=RELEASES)
    result = _call(server, "list_recent_changes", {})
    structured = result.structured_content
    assert structured is not None
    assert set(structured) == BASE_KEYS
    assert NOTE_KEY not in structured
    assert [item["tag_name"] for item in structured["result"]] == ["v2.0.0", "v1.0.0"]
    assert len(result.content) == 1


def test_a_limit_that_returns_something_has_no_note(tmp_path: Path) -> None:
    server = _server(tmp_path, "pdf", "python", copy_scope=False, recent_releases=RELEASES)
    result = _call(server, "list_recent_changes", {"limit": 1})
    assert result.structured_content is not None
    assert NOTE_KEY not in result.structured_content
    assert len(result.content) == 1


def test_a_fetch_failed_sidecar_is_still_an_unavailable_error_without_a_note(tmp_path: Path) -> None:
    server = _server(tmp_path, "pdf", "go", copy_scope=False, recent_releases_state=SIDECAR_FETCH_FAILED)
    result = _call_raw(server, "list_recent_changes", {})
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["code"] == ToolErrorCode.UNAVAILABLE.value
    assert result.structured_content["message"] == RELEASES_FETCH_FAILED_MESSAGE
    assert NOTE_KEY not in result.structured_content
    assert len(result.content) == 1
    assert result.content[0].text == RELEASES_FETCH_FAILED_MESSAGE  # type: ignore[union-attr]


OTHER_TOOL_ARGUMENTS: dict[str, dict[str, Any]] = {
    "lookup": {"query": "Document"},
    "search_docs": {"query": "install", "content_type": "getting_started"},
    "search_symbols": {"query": "Document"},
    "get_symbol": {"fqn": "Aspose.Pdf.Document"},
    "list_members": {"fqn": "Aspose.Pdf.Document"},
    "find_examples": {"query": "watermark"},
    "get_product_reference": {"section": "license"},
    "report_index_freshness": {},
}


@pytest.mark.parametrize("tool", sorted(OTHER_TOOL_ARGUMENTS))
def test_other_tools_gain_no_note(tmp_path: Path, tool: str) -> None:
    server = _server(tmp_path, "pdf", "net", recent_releases=())
    result = _call(server, tool, OTHER_TOOL_ARGUMENTS[tool])
    assert result.structured_content is not None
    assert NOTE_KEY not in result.structured_content
    assert set(result.structured_content) == BASE_KEYS
    assert len(result.content) == 1


def test_an_empty_answer_of_another_tool_gains_no_note(tmp_path: Path) -> None:
    server = _server(tmp_path, "pdf", "net", recent_releases=())
    result = _call(server, "search_symbols", {"query": "send a fax via modem"})
    assert result.structured_content is not None
    assert NOTE_KEY not in result.structured_content
    assert len(result.content) == 1


# --- descriptions and wiring ----------------------------------------------------------------------


def test_the_tool_description_mentions_the_note_and_the_nine_stay_distinct() -> None:
    text = TOOL_DESCRIPTIONS["list_recent_changes"]
    assert "An empty list comes with a note that says so." in text
    assert "An empty list means the product has no GitHub releases." in text  # the existing sentences stay
    assert "never an empty list" in text
    assert len(TOOL_DESCRIPTIONS) == 9
    assert len(set(TOOL_DESCRIPTIONS.values())) == 9
    other_tools = [name for name in TOOL_DESCRIPTIONS if name != "list_recent_changes"]
    assert all("comes with a note" not in TOOL_DESCRIPTIONS[name] for name in other_tools)


def _called_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_server_source_calls_empty_result_note_in_call_tool() -> None:
    tree = ast.parse(SERVER_SOURCE.read_text(encoding="utf-8"))
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names
    }
    assert {"empty_result_note", "NOTE_KEY"} <= imported
    create = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "create_server"
    )
    call_tool = next(
        node
        for node in ast.walk(create)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "_call_tool"
    )
    assert "empty_result_note" in _called_names(call_tool)
    uses_note_key = any(isinstance(node, ast.Name) and node.id == "NOTE_KEY" for node in ast.walk(call_tool))
    assert uses_note_key


def test_the_deciding_line_in_the_envelope_appears_once() -> None:
    source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "response_envelope.py").read_text(encoding="utf-8")
    line = 'if tool == "list_recent_changes" and isinstance(payload, list) and not payload:'
    assert source.count(line) == 1


# --- the 40 baked pilots through the real handler -------------------------------------------------


def test_the_baked_pilots_are_forty() -> None:
    assert len(_pilots()) == 40


def test_the_served_note_is_present_exactly_for_the_pilots_whose_sidecar_is_empty(tmp_path: Path) -> None:
    # On main d7b594fd 15 of the 40 sidecars hold [] (3d/net, 3d/python, 3d/typescript, barcode/python,
    # cells/typescript, email/cpp, email/net, email/python, html/python, imaging/net, page/python,
    # pdf/python, pdf/typescript, tex/python, words/python); only >= 1 is required because the
    # side-file refetch of TC-519 turns some of them into tag lists.
    noted: list[tuple[str, str]] = []
    for family, platform in _pilots():
        releases = _sidecar_releases(family, platform)
        server = _server(
            tmp_path / f"{family}_{platform}", family, platform, copy_scope=False, recent_releases=releases
        )
        result = _call(server, "list_recent_changes", {"limit": 50})
        structured = result.structured_content
        assert structured is not None
        if not releases:
            assert structured["result"] == [], (family, platform)
            assert structured["note"] == SENTENCE, (family, platform)
            assert len(result.content) == 2, (family, platform)
            noted.append((family, platform))
        else:
            assert isinstance(structured["result"], list) and structured["result"], (family, platform)
            assert NOTE_KEY not in structured, (family, platform)
            assert len(result.content) == 1, (family, platform)
            assert structured["result"][0]["tag_name"] == releases[0].tag_name, (family, platform)
    assert len(noted) >= 1
