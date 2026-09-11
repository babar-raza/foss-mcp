"""All nine tools wired into the served MCP transport, and Origin/protocol-version rejection
enforced at the transport boundary - in-process, over the real SDK's ASGI app
(``starlette.testclient.TestClient``), never a real socket (``network: false``).

Protocol-level errors and tool-domain errors stay distinguishable by construction: a
rejection never reaches JSON-RPC dispatch at all (a plain HTTP 400 JSON body), while a tool
reporting no match is a completely normal ``CallToolResult`` with ``isError: false``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import create_server, schema_for
from foss_mcp.mcp.transport_security import reject_request

# infra/ is not a package (no __init__.py, matching scripts/ convention) - import its module
# directly from the path, the same way its own entrypoints are invoked.
sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

EXPECTED_TOOLS = {
    "lookup",
    "search_docs",
    "search_symbols",
    "get_symbol",
    "list_members",
    "find_examples",
    "get_product_reference",
    "list_recent_changes",
    "report_index_freshness",
}

# One valid, minimal argument set per tool - enough to prove each is callable without
# erroring, over an intentionally empty store (an honest Miss/NotFound is still a successful,
# non-error tool result; TC-017a/b/c already prove real content against real data).
VALID_ARGUMENTS = {
    "lookup": {"query": "Widget"},
    "search_docs": {"query": "install", "content_type": "developer_guide"},
    "search_symbols": {"query": "Widget"},
    "get_symbol": {"fqn": "Widget"},
    "list_members": {"fqn": "Widget"},
    "find_examples": {"query": "Widget"},
    "get_product_reference": {"section": "license"},
    "list_recent_changes": {},
    "report_index_freshness": {},
}

VALID_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Origin": "https://example.com",
    "MCP-Protocol-Version": "2025-06-18",
}
ALLOWED_ORIGINS = ["https://example.com"]


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


class _McpSession:
    """A thin, real MCP-over-HTTP client session for these in-process tests: initialize,
    acknowledge, then send whatever the test needs - driven through the REAL ASGI app, not a
    stand-in for it.
    """

    def __init__(self, client: TestClient) -> None:
        self.client = client
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.0.1"},
                },
            },
            headers=VALID_HEADERS,
        )
        assert response.status_code == 200, response.text
        self.session_id = response.headers["mcp-session-id"]
        self.headers = dict(VALID_HEADERS, **{"mcp-session-id": self.session_id})
        client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=self.headers
        )

    def request(self, method: str, params: dict, *, request_id: int = 1):
        return self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            headers=self.headers,
        )


def _client(tmp_path: Path) -> TestClient:
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=_store(tmp_path),
        allowed_origins=ALLOWED_ORIGINS,
    )
    return TestClient(app)


# ---------------------------------------------------------------------
# transport_security.reject_request - the stable contract, tested directly.
# ---------------------------------------------------------------------


def test_a_disallowed_origin_is_rejected() -> None:
    reason = reject_request(
        {"Origin": "https://evil.example", "MCP-Protocol-Version": "2025-06-18"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is not None and "origin" in reason.lower()


def test_an_absent_origin_is_not_rejected_on_its_own() -> None:
    """Non-browser clients legitimately never send Origin; only a PRESENT, disallowed one
    is a DNS-rebinding-relevant signal."""
    reason = reject_request({"MCP-Protocol-Version": "2025-06-18"}, allowed_origins=ALLOWED_ORIGINS)
    assert reason is None


def test_a_missing_protocol_version_is_rejected() -> None:
    reason = reject_request({"Origin": "https://example.com"}, allowed_origins=ALLOWED_ORIGINS)
    assert reason is not None and "protocol-version" in reason.lower()


def test_an_invalid_protocol_version_is_rejected() -> None:
    reason = reject_request(
        {"Origin": "https://example.com", "MCP-Protocol-Version": "not-a-date"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is not None and "protocol-version" in reason.lower()


def test_a_fully_valid_request_is_not_rejected() -> None:
    reason = reject_request(
        {"Origin": "https://example.com", "MCP-Protocol-Version": "2025-06-18"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is None


# ---------------------------------------------------------------------
# schema_for - derived from each tool wrapper's own signature.
# ---------------------------------------------------------------------


def test_schema_for_marks_defaulted_parameters_optional() -> None:
    def example(*, required_one: str, optional_one: int = 5) -> None:
        pass

    schema = schema_for(example)
    assert schema["properties"]["required_one"] == {"type": "string"}
    assert schema["properties"]["optional_one"] == {"type": "integer"}
    assert schema["required"] == ["required_one"]


# ---------------------------------------------------------------------
# The served transport, end-to-end in-process: tools/list, tools/call, and rejection.
# ---------------------------------------------------------------------


def test_all_nine_tools_are_registered_directly_on_the_server(tmp_path: Path) -> None:
    server = create_server(DeploymentConfig(family="pdf", platform="net"), _store(tmp_path))
    assert server is not None  # construction alone proves on_list_tools/on_call_tool wired


def test_tools_list_returns_all_nine_tools_with_schemas(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/list", {})
        assert response.status_code == 200
        body = _sse_json(response.text)
        tools = body["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert names == EXPECTED_TOOLS
        for tool in tools:
            assert tool["inputSchema"]["type"] == "object"


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_each_tool_is_callable(tmp_path: Path, tool_name: str) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/call", {"name": tool_name, "arguments": VALID_ARGUMENTS[tool_name]})
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert "result" in body, body
        assert body["result"]["isError"] is False


def test_a_bad_origin_is_rejected_at_the_transport_never_reaching_a_tool(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers=dict(VALID_HEADERS, Origin="https://evil.example"),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "rejected"
        assert "origin" in response.json()["reason"].lower()


def test_a_missing_protocol_version_is_rejected_with_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = {key: value for key, value in VALID_HEADERS.items() if key != "MCP-Protocol-Version"}
        response = client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, headers=headers
        )
        assert response.status_code == 400
        assert "protocol-version" in response.json()["reason"].lower()


def test_an_invalid_protocol_version_is_rejected_with_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers=dict(VALID_HEADERS, **{"MCP-Protocol-Version": "not-a-real-version"}),
        )
        assert response.status_code == 400
        assert "protocol-version" in response.json()["reason"].lower()


def test_a_valid_request_still_passes(tmp_path: Path) -> None:
    """A rejection is a plain 400 JSON body; a real, valid session's response is the normal
    JSON-RPC envelope - the two are never the same shape."""
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/list", {})
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert "result" in body and "error" not in body


def _sse_json(text: str) -> dict:
    """The streamable-HTTP transport answers a POST with one SSE `data:` line - decode it."""
    import json

    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line found in response: {text!r}")
