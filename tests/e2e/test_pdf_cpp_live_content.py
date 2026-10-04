"""G2/TC-172: pdf/cpp's real, live first publish, proven end to end through real docker-compose
services - mirrors tests/e2e/test_pdf_net_live_content.py's pattern for pdf/cpp.

``docker compose up`` the one-shot ``ingest-pdf-cpp`` service (which publishes API-surface chunks
built from pdf/cpp's committed fixture into the named ``manifests-pdf-cpp`` volume), bring up
``serving-pdf-cpp`` (which mounts that SAME volume), poll the real ``/readyz`` with a bounded
retry, then run a real MCP-over-HTTP session and assert real, non-empty content comes back.

Publishes API-surface chunks only: pdf/cpp has no compile-verified example path, so no example
assertions are made here.

network: true - this file builds, starts and tears down the containers it talks to. It is skipped
unless FOSS_MCP_LIVE_E2E=1 is set, because Docker is not available in every environment that runs
the offline gate. A skipped run is NOT a passing live run.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import time
from collections.abc import Iterator

import httpx2 as httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FOSS_MCP_LIVE_E2E") != "1",
    reason="live pdf/cpp e2e requires Docker; set FOSS_MCP_LIVE_E2E=1 to run it",
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
CPP_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "pdf_cpp" / "api_surface.json"
PROJECT_NAME = "foss-mcp-e2e-tc172"
BASE_URL = "http://127.0.0.1:8082"
MCP_PATH = "/mcp"
READYZ_PATH = "/readyz"
INGEST_TIMEOUT_SECONDS = 900
READYZ_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

VALID_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": PROTOCOL_VERSION,
}


def _real_class_name() -> str:
    """A real class name read from pdf/cpp's own committed fixture at test time, never a guess.

    The fixture's own ``class_import`` is a C++ qualified name (e.g. ``Aspose::Pdf::Foo``); the
    last segment is the bare symbol the served chunks must contain.
    """
    fixture = json.loads(CPP_FIXTURE.read_text(encoding="utf-8"))
    class_import = fixture["types"][0]["class_import"]
    name = re.split(r"::|\.", class_import)[-1]
    assert name, f"fixture's first type has no usable name: {class_import!r}"
    return name


REAL_SYMBOL = _real_class_name()


def _compose(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Retries on transient Docker daemon teardown races, as test_pdf_net_live_content does."""
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(3):
        result = subprocess.run(
            ["docker", "compose", "-p", PROJECT_NAME, "-f", str(COMPOSE_FILE), *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode == 0 or "Error response from daemon" not in result.stderr:
            return result
        time.sleep(2 * (attempt + 1))
    assert result is not None
    return result


def _initialize_body(request_id: int = 0) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tc-172-e2e-client", "version": "0.0.1"},
        },
    }


def _sse_json(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line found in response: {text!r}")


def _wait_for_readyz() -> None:
    """Bounded poll of the real /readyz route until it reports 200 - never an unbounded loop."""
    deadline = time.monotonic() + READYZ_TIMEOUT_SECONDS
    last_error = "never attempted"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{BASE_URL}{READYZ_PATH}", timeout=5)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"/readyz never returned 200 within {READYZ_TIMEOUT_SECONDS}s: {last_error}")


@pytest.fixture(scope="module")
def real_generation() -> Iterator[None]:
    """Run the one-shot ingest-pdf-cpp to completion, then bring up serving-pdf-cpp on the SAME
    named volume it just published into. ``down -v`` first and always in teardown.
    """
    _compose("down", "-v", "--remove-orphans")
    try:
        ingest = _compose(
            "up",
            "--build",
            "--exit-code-from",
            "ingest-pdf-cpp",
            "ingest-pdf-cpp",
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        assert ingest.returncode == 0, (ingest.stdout + ingest.stderr)[-4000:]

        up = _compose("up", "-d", "--build", "serving-pdf-cpp")
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_for_readyz()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP session against the running serving-pdf-cpp container."""

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

    def call_tool(self, name: str, arguments: dict, *, request_id: int = 1) -> dict:
        response = httpx.post(
            f"{BASE_URL}{MCP_PATH}",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=self.headers,
            timeout=15,
        )
        assert response.status_code == 200, response.text
        return _sse_json(response.text)


@pytest.fixture()
def session(real_generation: None) -> _McpSession:
    return _McpSession()


def test_readyz_is_ready_after_the_real_pdf_cpp_publish(real_generation: None) -> None:
    response = httpx.get(f"{BASE_URL}{READYZ_PATH}", timeout=5)
    assert response.status_code == 200
    assert response.text == "ready"


def test_search_symbols_returns_real_content_from_the_pdf_cpp_fixture(session: _McpSession) -> None:
    """A real class name from pdf/cpp's own fixture returns real, non-empty content - never the
    empty/"no published generation" answer a correctly-wired-but-unpublished container gives.
    """
    body = session.call_tool("search_symbols", {"query": REAL_SYMBOL})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real matches, got a Miss: {result}"
    assert any(REAL_SYMBOL in match["text"] for match in result), result
