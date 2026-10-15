"""G3/TC-507: the dispatch core reports a failed sidecar fetch truthfully.

``list_recent_changes`` answers an ``unavailable`` error (never an empty list) when the releases fetch
failed; ``get_product_reference`` answers a normal NotAvailable result whose reason says the data could not
be fetched. The tool descriptions say what is true. Offline; dispatch as ``tests/mcp/test_dispatch_offload.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import TOOL_DESCRIPTIONS, ToolErrorCode, create_server
from foss_mcp.mcp.sidecar_state import (
    FETCH_FAILED_REASON,
    RELEASES_FETCH_FAILED_MESSAGE,
    SIDECAR_FETCH_FAILED,
    SIDECAR_PRESENT,
)
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs

CONFIG = DeploymentConfig(family="pdf", platform="go")
GUARD_SECONDS = 60
RELEASES = (Release(tag_name="v1.0.0", body="Real notes about the release", richness="detailed"),)
# The same expression tests/mcp/test_tool_surface_truthful.py applies to every description.
QUOTED_IDENTIFIER = re.compile(r"'([A-Za-z_][\w.:]*)'")


def _call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


def _server(tmp_path: Path, **kwargs: Any) -> Any:
    return create_server(CONFIG, GenerationManifestStore(tmp_path / "manifests"), **kwargs)


def test_list_recent_changes_is_an_unavailable_error_when_the_releases_fetch_failed(tmp_path: Path) -> None:
    server = _server(tmp_path, recent_releases_state=SIDECAR_FETCH_FAILED)
    result = _call(server, "list_recent_changes", {})
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["code"] == ToolErrorCode.UNAVAILABLE.value == "unavailable"
    assert result.structured_content["message"] == RELEASES_FETCH_FAILED_MESSAGE
    assert result.structured_content["correlation_id"]
    assert "result" not in result.structured_content
    assert result.content[0].text == RELEASES_FETCH_FAILED_MESSAGE  # type: ignore[union-attr]


def test_list_recent_changes_failure_does_not_depend_on_the_releases_passed(tmp_path: Path) -> None:
    server = _server(tmp_path, recent_releases=RELEASES, recent_releases_state=SIDECAR_FETCH_FAILED)
    result = _call(server, "list_recent_changes", {"limit": 5})
    assert result.is_error is True


def test_list_recent_changes_empty_is_a_normal_empty_result(tmp_path: Path) -> None:
    result = _call(_server(tmp_path), "list_recent_changes", {})
    assert result.is_error is False
    # The response envelope (G3/TC-514) adds a sibling scope beside the unchanged bare list.
    assert result.structured_content["result"] == []
    assert "scope" in result.structured_content
    assert result.content[0].text == "[]"  # type: ignore[union-attr]


def test_list_recent_changes_with_releases_is_unchanged(tmp_path: Path) -> None:
    result = _call(_server(tmp_path, recent_releases=RELEASES), "list_recent_changes", {})
    assert result.is_error is False
    assert result.structured_content is not None
    (entry,) = result.structured_content["result"]
    assert entry["tag_name"] == "v1.0.0"


def test_get_product_reference_of_a_failed_fetch_is_a_normal_not_available_result(tmp_path: Path) -> None:
    inputs = ProductReferenceInputs(platform="go", sidecar_state=SIDECAR_FETCH_FAILED)
    server = _server(tmp_path, product_reference_inputs=inputs, recent_releases_state=SIDECAR_FETCH_FAILED)
    for section in ("install", "compatibility", "license", "support", "contributing", "agent_guidance"):
        result = _call(server, "get_product_reference", {"section": section})
        assert result.is_error is False, section
        assert result.structured_content is not None
        answer = result.structured_content["result"]
        assert answer["section"] == section
        assert answer["reason"] == FETCH_FAILED_REASON
    formats = _call(server, "get_product_reference", {"section": "formats"})
    assert formats.is_error is False
    assert formats.structured_content is not None
    assert "has no data source" in formats.structured_content["result"]["reason"]


def test_the_get_product_reference_description_is_true() -> None:
    text = TOOL_DESCRIPTIONS["get_product_reference"]
    assert "authoritative absence" not in text
    assert "do not retry" not in text
    assert "lookup" in text
    assert "search_docs" in text
    assert "could not be fetched" in text
    assert "transient" in text
    assert "retry later" in text
    assert "is not present in this repository" in text
    assert "currently NotAvailable for every product" not in text


def test_the_list_recent_changes_description_explains_empty_and_failure() -> None:
    text = TOOL_DESCRIPTIONS["list_recent_changes"]
    assert "empty list means the product has no GitHub releases" in text
    assert "could not be fetched" in text
    assert "never an empty list" in text


def test_every_description_is_distinct_and_longer_than_80_characters() -> None:
    assert len(set(TOOL_DESCRIPTIONS.values())) == len(TOOL_DESCRIPTIONS)
    assert all(len(text) > 80 for text in TOOL_DESCRIPTIONS.values())


def test_create_server_default_releases_state_is_present(tmp_path: Path) -> None:
    import inspect

    assert inspect.signature(create_server).parameters["recent_releases_state"].default == SIDECAR_PRESENT
    result = _call(_server(tmp_path, recent_releases=RELEASES), "list_recent_changes", {})
    assert result.is_error is False


@pytest.mark.parametrize("tool", ["get_product_reference", "list_recent_changes"])
def test_the_quoted_identifier_rule_still_holds_for_the_new_descriptions(tool: str) -> None:
    for token in QUOTED_IDENTIFIER.findall(TOOL_DESCRIPTIONS[tool]):
        assert "." not in token and not token[0].isupper(), f"{tool} quotes {token!r}"
