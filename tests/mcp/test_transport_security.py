"""``transport_security.reject_request`` - the ONE place both transport-boundary MUSTs
(Origin, MCP-Protocol-Version) are decided, and the fix for TC-124's confirmed,
launch-blocking bug: every real MCP client's very first HTTP request (``initialize``) was
hard-rejected with HTTP 400 before it could ever reach tool dispatch, because this module
called ``negotiate_revision`` unconditionally on the ``MCP-Protocol-Version`` HEADER - a
Streamable HTTP transport-layer convention used only on requests made AFTER a protocol
version has been negotiated - and conflated it with the JSON-RPC BODY's own, genuinely
REQUIRED ``initialize.params.protocolVersion`` field (a completely different MUST, already
validated independently by the mcp SDK's own initialize handler once it parses the body).

The direct unit tests below exercise ``reject_request`` itself (Origin rejection, and the
present-but-invalid-version rejection that was always correct and is untouched by this fix).
The integration test is the REQUIRED real proof this card demands: the real ``mcp`` SDK's own
``mcp.client.streamable_http`` transport, feeding a real ``mcp.ClientSession``, calling a
genuine ``session.initialize()`` against a real running instance of
``infra.serve_http.build_app`` - over ``httpx2.ASGITransport`` (this project's installed,
renamed httpx fork; see the module import below) - with NO ``MCP-Protocol-Version`` header
set, exactly as every real third-party client's first request actually looks. Before this
fix, this exact integration test fails end to end with ``MCPError: Server returned an error
response`` (verified directly while authoring this card); after it, a real ``InitializeResult``
comes back.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.types import ASGIApp

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.transport_security import reject_request

# infra/ is not a package (no __init__.py, matching scripts/ convention) - import its module
# directly from the path, the same way tests/mcp/test_server_wiring.py and
# tests/infra/test_serve_http.py already do it.
sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

ALLOWED_ORIGINS = ["https://example.com"]


# --- Direct unit tests of reject_request itself -----------------------------------------


def test_an_origin_not_in_the_allowed_list_is_rejected() -> None:
    reason = reject_request(
        {"Origin": "https://evil.example", "MCP-Protocol-Version": "2025-06-18"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is not None
    assert "origin" in reason.lower()


def test_an_absent_origin_is_not_rejected_on_its_own() -> None:
    """Non-browser clients legitimately never send an Origin header at all - only a
    PRESENT, disallowed Origin is rejected."""
    reason = reject_request({"MCP-Protocol-Version": "2025-06-18"}, allowed_origins=ALLOWED_ORIGINS)
    assert reason is None


def test_a_missing_protocol_version_header_is_now_allowed_through() -> None:
    """The TC-124 fix itself: a genuine client's first ``initialize`` request never carries
    this header yet (no version has been negotiated), so its absence must not be rejected -
    the request's REQUIRED protocol version lives in the JSON-RPC body instead, which the SDK
    validates on its own once it parses that body."""
    reason = reject_request({"Origin": "https://example.com"}, allowed_origins=ALLOWED_ORIGINS)
    assert reason is None


def test_a_present_but_invalid_protocol_version_is_still_rejected() -> None:
    """Unaffected by this fix: once a client sends the header at all, it must still be a
    value this server can actually parse - protecting the part of the original design that
    was always correct."""
    reason = reject_request(
        {"Origin": "https://example.com", "MCP-Protocol-Version": "not-a-real-version"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is not None
    assert "protocol-version" in reason.lower()


def test_a_present_valid_protocol_version_is_not_rejected() -> None:
    reason = reject_request(
        {"Origin": "https://example.com", "MCP-Protocol-Version": "2025-06-18"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is None


# --- Real-SDK-client integration proof ---------------------------------------------------


@asynccontextmanager
async def _running_lifespan(app: ASGIApp):
    """Drive the real ASGI lifespan protocol (startup/shutdown) around *app* by hand.

    ``httpx2.ASGITransport`` (this project's installed httpx fork - there is no ``httpx``
    package in this venv, only the renamed ``httpx2``; see the module docstring) calls *app*
    directly for HTTP requests but never sends lifespan events on its own. The real
    ``mcp.server.streamable_http_manager`` session manager needs its ``run()`` lifespan task
    group started first (the SDK's own "Task group is not initialized" error otherwise) - the
    exact same real lifespan Starlette's own ``TestClient`` drives for the in-process
    ``TestClient``-based tests elsewhere in this suite, reimplemented here at the raw ASGI
    protocol level because the real SDK client needs an async transport, not a sync one.
    """
    to_app_send, to_app_receive = anyio.create_memory_object_stream(1)
    from_app_send, from_app_receive = anyio.create_memory_object_stream(1)

    async def run_app() -> None:
        await app({"type": "lifespan"}, to_app_receive.receive, from_app_send.send)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_app)
        await to_app_send.send({"type": "lifespan.startup"})
        started = await from_app_receive.receive()
        assert started["type"] == "lifespan.startup.complete", started
        try:
            yield
        finally:
            await to_app_send.send({"type": "lifespan.shutdown"})
            stopped = await from_app_receive.receive()
            assert stopped["type"] == "lifespan.shutdown.complete", stopped
            tg.cancel_scope.cancel()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_a_real_mcp_sdk_client_initializes_with_no_protocol_version_header(
    tmp_path: Path, anyio_backend: str
) -> None:
    """THE required real proof: the real ``mcp`` SDK's own Streamable HTTP client transport
    (``mcp.client.streamable_http.streamable_http_client``), feeding a real
    ``mcp.ClientSession``, completes a genuine ``session.initialize()`` against a real running
    ``infra.serve_http.build_app`` instance - with NO ``MCP-Protocol-Version`` header set at
    all, exactly what every real third-party MCP client's first request looks like. This is
    never a hand-rolled fake client: it is the identical client code a genuine third party
    would run.

    Reverting the fix (this card's own negative control) makes this test fail again with the
    real SDK client's own ``MCPError: Server returned an error response`` - confirmed directly
    while authoring this test.
    """
    del anyio_backend
    store = GenerationManifestStore(tmp_path / "manifests")
    deployment_config = DeploymentConfig(family="pdf", platform="net")
    app = serve_http.build_app(deployment_config, manifest_store=store, allowed_origins=[])

    transport = httpx2.ASGITransport(app=app)
    http_client = httpx2.AsyncClient(transport=transport, base_url="http://testserver")

    async with _running_lifespan(app):
        async with streamable_http_client("http://testserver/mcp", http_client=http_client) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                # No MCP-Protocol-Version header was ever set on http_client - this is the
                # exact scenario the confirmed bug hard-rejected before it could reach the SDK.
                result = await session.initialize()
                assert result.protocol_version
                assert result.server_info.name == "foss-mcp"
