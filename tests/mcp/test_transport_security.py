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
from foss_mcp.mcp.transport_security import SUPPORTED_PROTOCOL_REVISIONS, reject_request

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


def test_each_declared_supported_revision_passes_through_unchanged() -> None:
    """Exact-match case, unaffected by the fallback_applied fix: every revision this project
    currently declares in ``SUPPORTED_PROTOCOL_REVISIONS`` must still be allowed through."""
    for revision in SUPPORTED_PROTOCOL_REVISIONS:
        reason = reject_request(
            {"Origin": "https://example.com", "MCP-Protocol-Version": revision},
            allowed_origins=ALLOWED_ORIGINS,
        )
        assert reason is None, f"{revision!r} is a declared supported revision and must pass"


def test_every_sdk_handshake_revision_passes_through_unchanged() -> None:
    """The TC-250 fix: SUPPORTED_PROTOCOL_REVISIONS now tracks the installed MCP SDK's own
    ``mcp_types.version.HANDSHAKE_PROTOCOL_VERSIONS`` directly - the exact set its private,
    unoverridable ``initialize`` handler can negotiate and already answer to a client - so EVERY
    member of that set, "2025-11-25" included (the exact revision TC-246's own now-removed test
    wrongly hard-coded as something to reject), must pass ``reject_request`` cleanly. This is the
    inverse of TC-246's premise, proven wrong by the live pod lockout this card fixes."""
    assert "2025-11-25" in SUPPORTED_PROTOCOL_REVISIONS

    for revision in SUPPORTED_PROTOCOL_REVISIONS:
        reason = reject_request(
            {"Origin": "https://example.com", "MCP-Protocol-Version": revision},
            allowed_origins=ALLOWED_ORIGINS,
        )
        assert reason is None, f"{revision!r} is now a declared supported revision and must pass"


def test_a_revision_genuinely_outside_the_sdk_handshake_set_is_still_rejected() -> None:
    """The fallback-rejection mechanism itself stays proven: a well-formed revision no SDK has
    ever released is still rejected, naming it unsupported - only the accepted-set bug (which
    revisions count as "supported" in the first place) is fixed by this card, not the rejection
    mechanism for a revision that is genuinely outside that set."""
    never_released_revision = "2099-01-01"
    assert never_released_revision not in SUPPORTED_PROTOCOL_REVISIONS

    reason = reject_request(
        {"Origin": "https://example.com", "MCP-Protocol-Version": never_released_revision},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is not None
    assert never_released_revision in reason
    assert "not" in reason.lower() and "support" in reason.lower()

    # Distinct from the invalid-format rejection reason - a separate, unrelated case.
    invalid_format_reason = reject_request(
        {"Origin": "https://example.com", "MCP-Protocol-Version": "not-a-real-version"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason != invalid_format_reason
    assert "invalid" not in reason.lower()


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


@pytest.mark.anyio
async def test_a_real_mcp_sdk_client_completes_a_second_call_on_the_same_session(
    tmp_path: Path, anyio_backend: str
) -> None:
    """THE decisive regression test TC-250 exists to add: ``initialize()`` alone, proven by the
    test above, was never the gap - the TC-246 lockout only ever showed up on the request AFTER
    it, because the real SDK client transport caches the server's own negotiated
    ``protocolVersion`` from the ``initialize`` result and echoes it back in the
    ``MCP-Protocol-Version`` header on every subsequent request (confirmed directly in the
    installed ``mcp.client.streamable_http`` transport's own ``_protocol_version_header``
    handling). A real client that only ever calls ``initialize()`` can never exercise that
    header at all, which is exactly how TC-246's own regression shipped unnoticed.

    This test drives a genuine ``session.initialize()`` followed by a genuine
    ``session.call_tool(...)`` - a real SECOND request on the SAME session, using the installed
    SDK client's own normal behavior, never a hand-rolled header or a fake client. Against the
    pre-TC-250 code (``SUPPORTED_PROTOCOL_REVISIONS`` missing "2025-11-25", which is what the
    installed SDK's own handshake negotiates for a client requesting the latest handshake
    version), this test fails on the ``call_tool`` step with the real SDK client's own
    ``McpError: Server returned an error response`` - confirmed directly while authoring this
    test, by temporarily reverting the ``transport_security.py`` fix and re-running it. After the
    fix, both calls succeed on the same session.
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
                init_result = await session.initialize()
                assert init_result.protocol_version

                # The real second request: the SDK client transport now echoes the exact
                # negotiated protocol version from `init_result` in the MCP-Protocol-Version
                # header on its own, with no test-side header manipulation whatsoever - this is
                # the genuine, unmodified behavior of every spec-compliant client, including the
                # official SDK client, and it is the request TC-246's own fix rejected.
                call_result = await session.call_tool("report_index_freshness", {})
                assert not call_result.is_error
                assert call_result.content
