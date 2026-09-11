"""The gate's whole point (TC-020): a REAL MCP client completing a full session against the
ACTUALLY RUNNING pdf/net serving container - docker-compose up, a real published port, a real
socket. ``tests/mcp/test_server_wiring.py`` already proves the same contract in-process
(``TestClient``, no socket); this file is the one place that in-process proof is checked
against reality, and the one place a broken published port is guaranteed to be noticed.

network: true on this card, for exactly this reason - everything here talks to a container
this file itself brings up and tears down.

BASE_URL is hardcoded to the port docker-compose.yml currently publishes (8080), deliberately
never read back out of that file: a harness that re-derives the port from the (possibly
mutated) compose file would silently follow a broken publish and never notice a thing - the
single most likely way an E2E suite like this one degrades into theatre.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
PROJECT_NAME = "foss-mcp-e2e"
BASE_URL = "http://127.0.0.1:8080"
MCP_PATH = "/mcp"
STARTUP_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

# No Origin header on a legitimate request: docker-compose.yml sets no
# FOSS_MCP_ALLOWED_ORIGINS, so the container's allow-list is empty and ANY present Origin
# would be rejected - exactly like a real non-browser MCP client, which never sends one.
VALID_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": PROTOCOL_VERSION,
}

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

# One valid, minimal argument set per tool - enough to prove each is reachable end-to-end
# through the real container. An honest Miss/NotAvailable against this freshly-built,
# unpopulated deployment is still a successful, non-error tool result (tests/mcp/tools/**
# already proves real content against real data, in-process).
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


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT_NAME, "-f", str(COMPOSE_FILE), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _initialize_body(request_id: int = 0) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tc-020-e2e-client", "version": "0.0.1"},
        },
    }


def _sse_json(text: str) -> dict:
    """The streamable-HTTP transport answers a POST with one SSE `data:` line - decode it."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line found in response: {text!r}")


def _wait_until_ready() -> None:
    """Poll the real published port with a real initialize request until the container
    answers - or fail with whatever the last real error actually was, never a bare timeout."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error = "never attempted"
    while time.monotonic() < deadline:
        try:
            response = httpx.post(
                f"{BASE_URL}{MCP_PATH}", json=_initialize_body(), headers=VALID_HEADERS, timeout=5
            )
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"container never became ready on {BASE_URL}: {last_error}")


@pytest.fixture(scope="module")
def running_container() -> Iterator[None]:
    up = _compose("up", "-d", "--build")
    try:
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_until_ready()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP client session against the real running container: initialize,
    capture the session id, acknowledge, then issue whatever the test needs."""

    def __init__(self) -> None:
        response = httpx.post(
            f"{BASE_URL}{MCP_PATH}", json=_initialize_body(), headers=VALID_HEADERS, timeout=10
        )
        assert response.status_code == 200, response.text
        self.session_id = response.headers["mcp-session-id"]
        self.headers = dict(VALID_HEADERS, **{"mcp-session-id": self.session_id})
        httpx.post(
            f"{BASE_URL}{MCP_PATH}",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self.headers,
            timeout=10,
        )

    def request(self, method: str, params: dict, *, request_id: int = 1) -> httpx.Response:
        return httpx.post(
            f"{BASE_URL}{MCP_PATH}",
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            headers=self.headers,
            timeout=15,
        )


@pytest.fixture()
def session(running_container: None) -> _McpSession:
    return _McpSession()


# ---------------------------------------------------------------------
# Discovery and per-tool calls, against the real running container.
# ---------------------------------------------------------------------


def test_tools_list_returns_all_nine_tools_with_schemas(session: _McpSession) -> None:
    response = session.request("tools/list", {})
    assert response.status_code == 200
    body = _sse_json(response.text)
    tools = body["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == EXPECTED_TOOLS
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_each_tool_is_callable_against_the_real_container(session: _McpSession, tool_name: str) -> None:
    response = session.request("tools/call", {"name": tool_name, "arguments": VALID_ARGUMENTS[tool_name]})
    assert response.status_code == 200
    body = _sse_json(response.text)
    assert "result" in body, body
    assert body["result"]["isError"] is False


# ---------------------------------------------------------------------
# Protocol-level rejection, at the real published port.
# ---------------------------------------------------------------------


def test_an_invalid_origin_is_rejected(running_container: None) -> None:
    response = httpx.post(
        f"{BASE_URL}{MCP_PATH}",
        json=_initialize_body(),
        headers=dict(VALID_HEADERS, Origin="https://evil.example"),
        timeout=10,
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "rejected"
    assert "origin" in body["reason"].lower()


def test_a_missing_protocol_version_is_rejected(running_container: None) -> None:
    headers = {key: value for key, value in VALID_HEADERS.items() if key != "MCP-Protocol-Version"}
    response = httpx.post(f"{BASE_URL}{MCP_PATH}", json=_initialize_body(), headers=headers, timeout=10)
    assert response.status_code == 400
    assert "protocol-version" in response.json()["reason"].lower()


def test_an_invalid_protocol_version_is_rejected(running_container: None) -> None:
    response = httpx.post(
        f"{BASE_URL}{MCP_PATH}",
        json=_initialize_body(),
        headers=dict(VALID_HEADERS, **{"MCP-Protocol-Version": "not-a-real-version"}),
        timeout=10,
    )
    assert response.status_code == 400
    assert "protocol-version" in response.json()["reason"].lower()


def test_protocol_errors_stay_distinguishable_from_tool_domain_errors(session: _McpSession) -> None:
    """A protocol-level rejection is a plain HTTP 400 JSON body that never reaches JSON-RPC
    dispatch; a tool-domain error (an unknown tool name, here) is a completely normal 200
    JSON-RPC envelope with ``isError: true``. The two must never collapse into one shape."""
    rejected = httpx.post(
        f"{BASE_URL}{MCP_PATH}",
        json=_initialize_body(),
        headers=dict(VALID_HEADERS, Origin="https://evil.example"),
        timeout=10,
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "rejected"

    tool_error = session.request("tools/call", {"name": "not_a_real_tool", "arguments": {}})
    assert tool_error.status_code == 200
    body = _sse_json(tool_error.text)
    assert "result" in body and "error" not in body
    assert body["result"]["isError"] is True
