"""G3/TC-506 (REQ-G3-008): with no usable generation, every generation-backed tool is an error.

Before this card a deployment whose generation was missing or corrupt answered ``isError false`` with
``found: false`` (an agent reads "this API does not exist") or with raw exception text. The six tools that
answer from the published generation now answer ``isError true`` with the code ``unavailable`` and one fixed,
sanitised message. ``report_index_freshness`` (which honestly says "never published"),
``get_product_reference`` and ``list_recent_changes`` do not need a generation and are unchanged.

Offline and deterministic. Dispatch goes through ``server.get_request_handler("tools/call")`` exactly as
``tests/mcp/test_dispatch_offload.py`` does.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.health import GENERATION_UNAVAILABLE_MESSAGE
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import ToolErrorCode, create_server
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES
from foss_mcp.telemetry.usage_recorder import UsageRecorder

sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_stdio  # noqa: E402

REPO_ROOT = Path(__file__).parents[2]
BAKED_PDF_GO = REPO_ROOT / "infra" / "demo-manifests" / "pdf__go__self_extracted"
CONFIG = DeploymentConfig(family="pdf", platform="go")
GUARD_SECONDS = 60

GENERATION_TOOLS = {
    "lookup": {"query": "Document"},
    "search_docs": {"query": "install", "content_type": sorted(CONTENT_TYPES)[0]},
    "search_symbols": {"query": "Document"},
    "get_symbol": {"fqn": "Document"},
    "list_members": {"fqn": "Document"},
    "find_examples": {"query": "Document"},
}


def _call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Dispatch exactly as the transport does: through the server's registered tools/call handler."""
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


def _copy_baked(tmp_path: Path) -> tuple[Path, Path]:
    """A copy of the baked pdf/go scope; returns (manifests root, scope directory)."""
    root = tmp_path / "manifests"
    scope_dir = root / "pdf__go__self_extracted"
    shutil.copytree(BAKED_PDF_GO, scope_dir)
    return root, scope_dir


def _server(root: Path, **kwargs: Any) -> Any:
    return create_server(CONFIG, GenerationManifestStore(root), **kwargs)


def _assert_unavailable(result: CallToolResult, tmp_path: Path) -> None:
    assert result.is_error is True
    assert result.structured_content["code"] == "unavailable"
    assert result.structured_content["message"] == GENERATION_UNAVAILABLE_MESSAGE
    assert result.structured_content["correlation_id"]
    text = result.content[0].text
    assert text == GENERATION_UNAVAILABLE_MESSAGE
    assert str(tmp_path) not in text
    assert str(tmp_path) not in json.dumps(result.structured_content)


@pytest.mark.parametrize("tool", sorted(GENERATION_TOOLS))
def test_each_generation_tool_is_an_error_over_an_empty_store(tool: str, tmp_path: Path) -> None:
    result = _call(_server(tmp_path / "empty"), tool, GENERATION_TOOLS[tool])
    _assert_unavailable(result, tmp_path)
    lowered = result.content[0].text.lower()
    assert '"found"' not in lowered
    assert "found:" not in lowered
    # "this is not a statement that the API ... does not exist" is the one allowed mention, inside
    # the fixed message; nothing else says a symbol or a page is missing.
    assert result.content[0].text.replace(GENERATION_UNAVAILABLE_MESSAGE, "") == ""
    assert "traceback" not in lowered
    assert ":\\" not in result.content[0].text


@pytest.mark.parametrize("tool", ["search_symbols", "lookup"])
def test_a_corrupt_generation_gives_the_same_fixed_message(tool: str, tmp_path: Path) -> None:
    root, scope_dir = _copy_baked(tmp_path)
    next(scope_dir.glob("generation__*.json")).write_bytes(b"\x00\xff\xfe not json at all {{{")
    result = _call(_server(root), tool, GENERATION_TOOLS[tool])
    _assert_unavailable(result, tmp_path)


def test_a_missing_generation_file_gives_the_same_fixed_message(tmp_path: Path) -> None:
    root, scope_dir = _copy_baked(tmp_path)
    next(scope_dir.glob("generation__*.json")).unlink()
    result = _call(_server(root), "get_symbol", GENERATION_TOOLS["get_symbol"])
    _assert_unavailable(result, tmp_path)


def test_no_exception_text_reaches_the_client(tmp_path: Path) -> None:
    root, scope_dir = _copy_baked(tmp_path)
    (scope_dir / "active_pointer.json").write_text(
        json.dumps({"generation_id": "pdf::go::self_extracted::../../etc/passwd"}), encoding="utf-8"
    )
    result = _call(_server(root), "search_docs", GENERATION_TOOLS["search_docs"])
    _assert_unavailable(result, tmp_path)
    serialised = json.dumps(result.structured_content) + result.content[0].text
    for fragment in ("passwd", "etc", "InvalidScopeError", "invalid generation version", "Traceback"):
        assert fragment not in serialised


def test_report_index_freshness_still_answers_without_a_generation(tmp_path: Path) -> None:
    result = _call(_server(tmp_path / "empty"), "report_index_freshness", {})
    assert result.is_error is False
    assert result.structured_content["result"]


def test_get_product_reference_and_list_recent_changes_do_not_need_a_generation(tmp_path: Path) -> None:
    server = _server(tmp_path / "empty")
    reference = _call(server, "get_product_reference", {"section": "license"})
    changes = _call(server, "list_recent_changes", {})
    assert reference.is_error is False
    assert changes.is_error is False
    assert changes.structured_content["result"] == []


def test_a_usable_generation_is_unaffected(tmp_path: Path) -> None:
    root, _ = _copy_baked(tmp_path)
    result = _call(_server(root), "search_symbols", {"query": "Document"})
    assert result.is_error is False
    hits = result.structured_content["result"]
    assert hits
    assert hits[0]["generation_id"].startswith("pdf::go::self_extracted::")
    assert "FQN: Document" in hits[0]["text"]


def test_validation_errors_stay_invalid_argument_and_read_no_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _copy_baked(tmp_path)
    reads: list[str] = []
    original = GenerationManifestStore._cached_generation_document

    def counting(self: GenerationManifestStore, path: Path) -> Any:
        reads.append(path.name)
        return original(self, path)

    monkeypatch.setattr(GenerationManifestStore, "_cached_generation_document", counting)
    server = _server(root)
    result = _call(server, "lookup", {"query": ""})
    too_big = _call(server, "search_symbols", {"query": "Document", "top_k": 2**63})
    for outcome in (result, too_big):
        assert outcome.is_error is True
        assert outcome.structured_content["code"] == "invalid_argument"
    assert reads == []
    # And over an empty store a validation error is still invalid_argument, never unavailable.
    empty = _call(_server(tmp_path / "empty"), "lookup", {"query": ""})
    assert empty.structured_content["code"] == "invalid_argument"


def test_a_warning_with_reason_and_correlation_id_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = UsageRecorder()
    server = _server(tmp_path / "empty", usage_recorder=recorder)
    with caplog.at_level(logging.WARNING, logger="foss_mcp.mcp.server"):
        result = _call(server, "lookup", {"query": "Document"})
    warnings = [
        record
        for record in caplog.records
        if record.name == "foss_mcp.mcp.server" and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "lookup" in message
    assert "no active generation" in message
    assert result.structured_content["correlation_id"] in message
    assert str(tmp_path) not in message
    assert warnings[0].exc_info is None
    assert len(recorder) == 1  # the call is still recorded as a usage event, outcome error


def test_unavailable_is_in_the_closed_code_vocabulary() -> None:
    assert ToolErrorCode.UNAVAILABLE.value == "unavailable"
    assert {member.value for member in ToolErrorCode} == {
        "not_found",
        "invalid_argument",
        "internal",
        "unavailable",
    }


def test_stdio_build_gives_the_same_error_for_an_empty_store(tmp_path: Path) -> None:
    manifests = tmp_path / "stdio-manifests"
    manifests.mkdir()  # an empty manifests directory: no generation and no sidecars
    server = serve_stdio.build_stdio_server(CONFIG, manifests_dir=manifests)
    result = _call(server, "search_symbols", {"query": "Document"})
    _assert_unavailable(result, tmp_path)
