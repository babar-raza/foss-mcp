"""G2/TC-064: pdf/net's real, live first publish, proven end to end through real
docker-compose services - not a test's in-memory store, not code-reading.

The 2026-09-25 audit found, by hand, that the real running ``serving`` container answered
every content tool call with "no published generation for this scope": ``docker-compose.yml``
mounted no volume for ``/data/manifests`` and nothing in production ever invoked the
extraction-to-publish pipeline (``infra/ingest.py``). TC-060 (citation validation), TC-061
(a real ``/readyz``), TC-062 (``build_chunks_from_api_surface``) and TC-063
(``HashingEmbeddingProvider``) built the missing pieces; this file is the one place they are
actually wired together and run for real: ``docker compose up --build`` the new one-shot
``ingest-pdf-net`` service (which really publishes a generation into the real, named
``manifests-pdf-net`` volume via ``infra/build_chunks.py`` + ``infra/ingest.py``), then bring up
``serving`` (which mounts that SAME volume), poll the real ``/readyz`` until ready, then run a
real MCP-over-HTTP session and assert real content comes back.

Mirrors ``tests/e2e/test_container_session.py``'s established real-docker-compose pattern
(compose helpers, an SSE-decoding MCP session, teardown even on failure) - never reinvented.

network: true on this card, for exactly this reason - everything here talks to containers this
file itself builds, starts and tears down.
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
PROJECT_NAME = "foss-mcp-e2e-tc064"
BASE_URL = "http://127.0.0.1:8080"
MCP_PATH = "/mcp"
READYZ_PATH = "/readyz"
# REQ-G2-048 (TC-069) widened this: the ingest-pdf-net image now bakes in a real .NET 8 SDK
# download/install, and the container itself now does a real git clone plus real `dotnet
# build`s (the reference library once, then one per real furnished-content candidate) -
# genuinely slower than the pre-TC-069 type-only publish this budget originally covered.
INGEST_TIMEOUT_SECONDS = 1500
READYZ_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

# TC-011's own real, committed pdf/net fixture (tests/fixtures/pdf_net/api_surface.json): its
# first entry is a real class, class_import "Aspose.Pdf.AFRelationship", kind
# "enum_declaration" - build_chunks_from_api_surface's default max_types=20 includes it. A real
# query response containing this exact string is genuine, falsifiable proof of real content,
# never a guess.
REAL_SYMBOL = "AFRelationship"
REAL_SOURCE_COMMIT = "b7172877651413cff57a8bfe41fb8a8befb2406b"

# REQ-G2-048 (TC-069): the real, compile-verified example this card's real containerized
# ingestion run produces - of the 3 real candidates in tests/fixtures/furnished/pdf_net's
# real furnished page, only "Add a Watermark Annotation" really compiles against the real
# pinned Aspose.PDF-FOSS-for-.NET commit (confirmed by hand today, at the host level, by
# TC-067/TC-068). ``AddWatermarkAnnotation`` is the real, distinctive method call this
# candidate's real code uses - never a guess at what a container might return.
REAL_EXAMPLE_SYMBOL = "AddWatermarkAnnotation"

VALID_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": PROTOCOL_VERSION,
}


def _compose(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT_NAME, "-f", str(COMPOSE_FILE), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _initialize_body(request_id: int = 0) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tc-064-e2e-client", "version": "0.0.1"},
        },
    }


def _sse_json(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line found in response: {text!r}")


def _wait_for_readyz() -> None:
    """Poll the real published ``/readyz`` route (TC-061) until it reports ready - a bounded
    retry, never an unbounded loop, and never a fixed sleep: this is exactly what /readyz was
    built for, and this is its one real (non-unit-test) exercise.
    """
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
    """The real, live first publish: build and run the one-shot ``ingest-pdf-net`` service to
    completion (this IS the real ingestion the 2026-09-25 audit found never ran anywhere), then
    bring up ``serving`` against the SAME named volume it just published into.

    ``docker compose down -v`` first, per the card's own recovery note, so this is idempotent
    against any stale volume state left by a prior run.
    """
    _compose("down", "-v", "--remove-orphans")
    try:
        ingest = _compose(
            "up",
            "--build",
            "--exit-code-from",
            "ingest-pdf-net",
            "ingest-pdf-net",
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        assert ingest.returncode == 0, (ingest.stdout + ingest.stderr)[-4000:]

        up = _compose("up", "-d", "--build", "serving")
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_for_readyz()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP client session against the real running ``serving`` container:
    initialize, capture the session id, acknowledge, then issue whatever the test needs.
    """

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


# ---------------------------------------------------------------------
# The real, live proof.
# ---------------------------------------------------------------------


def test_readyz_is_ready_after_the_real_ingestion_publish(real_generation: None) -> None:
    """The one real (non-unit-test) exercise of TC-061's ``/readyz``: 200, because a real
    active generation now exists for this deployment's own scope - never process reachability.
    """
    response = httpx.get(f"{BASE_URL}{READYZ_PATH}", timeout=5)
    assert response.status_code == 200
    assert response.text == "ready"


def test_report_index_freshness_reports_the_real_fresh_generation(session: _McpSession) -> None:
    """A real generation id, and ``stale=False`` once the real indexed commit (recorded via the
    ``Source-Commit:`` line ``infra/build_chunks.py`` appends to each chunk's text) is compared
    against the real fixture's own real commit sha - never the pre-ingestion answer
    ("never published"/``stale=True``) every prior audit-era container gave.
    """
    body = session.call_tool(
        "report_index_freshness", {"current_source_commit": REAL_SOURCE_COMMIT}
    )
    result = body["result"]["structuredContent"]["result"]
    assert result["indexed_generation_id"] is not None
    assert result["indexed_generation_id"].startswith("pdf::net::self_extracted::")
    assert result["stale"] is False, result["reason"]


def test_search_symbols_returns_real_content_from_the_real_fixture(session: _McpSession) -> None:
    """``search_symbols`` for a real class name from TC-011's real fixture returns real,
    non-empty content - never the "no published generation for this scope" Miss every content
    tool call gave before this card wired ingestion into anything that runs.
    """
    body = session.call_tool("search_symbols", {"query": REAL_SYMBOL})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real matches, got a Miss: {result}"
    assert any(REAL_SYMBOL in match["text"] for match in result), result


def test_lookup_returns_real_content_from_the_real_fixture(session: _McpSession) -> None:
    """``lookup`` (the forgiving entry point) for the same real symbol, through the same real
    running container - a second, independent tool proving the same real generation is served.
    """
    body = session.call_tool("lookup", {"query": REAL_SYMBOL})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real matches, got a Miss: {result}"
    assert any(REAL_SYMBOL in match["text"] for match in result), result


def test_find_examples_returns_the_real_compile_verified_watermark_example(
    session: _McpSession,
) -> None:
    """REQ-G2-048 (TC-069): the concrete answer to the operator's own worked question ("how
    do I add a watermark to a PDF"), proven through the FULL containerized production path -
    not only at the host/test level (TC-068) but through a real ``docker compose up --build``
    of ``ingest-pdf-net`` (real git clone + real ``dotnet build`` INSIDE the container) feeding
    the same real ``serving`` container every other test in this file already queries.

    A task-oriented query ('watermark') takes ``find_examples``'s semantic/lexical fallback
    path (TC-068's own real fixture chunk is deliberately labeled ``FQN: Example: Add a
    Watermark Annotation``, not a real symbol FQN, so no exact-match path would fire here) -
    ranking every real ``Example:``-bearing chunk against the query text and returning the one
    real, compile-verified match.
    """
    body = session.call_tool("find_examples", {"query": "watermark"})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real ExampleMatch(es), got a Miss: {result}"
    assert any(REAL_EXAMPLE_SYMBOL in match["snippet"] for match in result), result
