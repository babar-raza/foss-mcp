"""HTTP serving container entrypoint: the MCP server over Streamable HTTP, on port 8080 at
the SDK's default path (``/mcp``), behind Origin and protocol-version rejection, plus a real
``/healthz`` liveness probe and a real ``/readyz`` readiness probe.

Two transports over ONE dispatch core (``foss_mcp.mcp.server.create_server``), never a second
implementation: this module and ``infra/serve_stdio.py`` both call it; only the transport
differs. The SDK's own DNS-rebinding protection (Host/Origin) is disabled here deliberately -
``foss_mcp.mcp.transport_security.reject_request`` is the ONE place Origin and
protocol-version MUSTs are decided, wrapping the SDK's ASGI app rather than duplicating its
own, separate check.

``/healthz`` and ``/readyz`` are both added via the SDK's own ``custom_starlette_routes`` hook -
INTO the same Starlette app ``streamable_http_app`` builds, never a second, separately-mounted
app - so they share that app's real lifespan (``session_manager.run()``); a second Starlette app
wrapped around it would never start that lifespan and every MCP call would fail with "Task
group is not initialized". ``RejectionMiddleware`` then exempts both paths: each must answer a
plain container healthcheck (``python -c "...urllib..."`` from ``Dockerfile.serving``, which
sends neither an ``Origin`` nor an ``MCP-Protocol-Version`` header) without being rejected as a
malformed MCP request - those MUSTs govern the MCP transport itself, not these probes.

``/healthz`` calls ``foss_mcp.mcp.health.is_alive`` - unconditional liveness, by that
function's own design, so a container orchestrator always has a liveness check distinct from
readiness. ``/readyz`` calls ``foss_mcp.mcp.health.round_trip_check`` against the SAME
``GenerationManifestStore`` instance ``create_server`` was built with: not merely that the
deployment's own active-generation pointer exists, but that the generation it names actually
carries queryable content (the real reference-system defect this replaces: a readiness probe
that reported healthy while serving nothing).
"""

from __future__ import annotations

import os

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Send
from starlette.types import Scope as ASGIScope

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.health import DeploymentGenerationStore, is_alive, round_trip_check
from foss_mcp.mcp.routing import DeploymentConfig, resolve_scope
from foss_mcp.mcp.server import _default_manifest_store, create_server
from foss_mcp.mcp.transport_security import reject_request

DEFAULT_PORT = 8080
HEALTHZ_PATH = "/healthz"
READYZ_PATH = "/readyz"


def allowed_origins_from_env() -> list[str]:
    raw = os.environ.get("FOSS_MCP_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class RejectionMiddleware:
    """Wraps an ASGI app: every HTTP request is checked against
    ``transport_security.reject_request`` before it can reach the wrapped app at all.

    A rejection is a plain HTTP 400 JSON error - never a JSON-RPC envelope - so it can never
    be confused with a tool call that ran and reported no match.

    ``exempt_paths`` (``/healthz`` and ``/readyz``, in practice) never reach ``reject_request``
    at all: the Origin/protocol-version MUSTs are an MCP-transport contract, and a plain
    container healthcheck request is not an MCP request and carries neither header.
    """

    def __init__(
        self, app: ASGIApp, allowed_origins: list[str], exempt_paths: frozenset[str] = frozenset()
    ) -> None:
        self.app = app
        self.allowed_origins = allowed_origins
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])}
        reason = reject_request(headers, allowed_origins=self.allowed_origins)
        if reason is not None:
            response = JSONResponse({"error": "rejected", "reason": reason}, status_code=400)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _healthz_route() -> Route:
    """The ``/healthz`` route: unconditional liveness (``foss_mcp.mcp.health.is_alive``),
    always 200 - matching ``is_alive``'s own documented contract that it never depends on
    external state, so a container orchestrator has a liveness check distinct from readiness
    that a restart can never fix by waiting on content.
    """

    async def healthz(request: Request) -> PlainTextResponse:
        del request  # is_alive() never depends on the request - see module docstring.
        if is_alive():
            return PlainTextResponse("alive", status_code=200)
        return PlainTextResponse("not alive", status_code=503)

    return Route(HEALTHZ_PATH, healthz, methods=["GET"])


def _readyz_route(deployment_config: DeploymentConfig, manifest_store: GenerationManifestStore) -> Route:
    """The ``/readyz`` route: 200 only when the deployment's own scope names a real active
    generation that actually carries queryable content (``foss_mcp.mcp.health.round_trip_check``),
    503 otherwise - never process reachability, and never merely "a generation pointer exists".

    Binds to *manifest_store* directly (the SAME instance ``create_server`` was built with, per
    ``build_app`` below) and to the scope this deployment - never a request - resolves to
    (``foss_mcp.mcp.routing.resolve_scope``), so no per-request work decides which generation
    matters.
    """
    scope = resolve_scope(deployment_config, request=None)
    generation_store = DeploymentGenerationStore.for_scope(
        manifest_store, scope, deployment_config.source_kind
    )

    async def readyz(request: Request) -> PlainTextResponse:
        del request  # nothing about the request influences readiness - see module docstring.
        if round_trip_check(generation_store):
            return PlainTextResponse("ready", status_code=200)
        return PlainTextResponse("not ready", status_code=503)

    return Route(READYZ_PATH, readyz, methods=["GET"])


def build_app(
    deployment_config: DeploymentConfig,
    manifest_store: GenerationManifestStore | None = None,
    allowed_origins: list[str] | None = None,
) -> ASGIApp:
    """The ASGI app this container serves: the same ``create_server`` dispatch core as
    stdio, wrapped in Origin/protocol-version rejection, plus real ``/healthz`` and ``/readyz``
    probes added into the SAME app (so they share the SDK's own session-manager lifespan) and
    exempted from that rejection layer (see module docstring for why).

    ``manifest_store`` and ``allowed_origins`` default to the real deployment's own store
    (``foss_mcp.mcp.server``'s ``/data/manifests``) and the ``FOSS_MCP_ALLOWED_ORIGINS`` env
    var respectively; both are overridable so tests never touch either. ``manifest_store`` is
    resolved ONCE, here, and handed to both ``create_server`` and the readiness route - never
    two separate stores for one running deployment.
    """
    resolved_store = manifest_store if manifest_store is not None else _default_manifest_store()
    server = create_server(deployment_config, resolved_store)
    allowed_origins = allowed_origins_from_env() if allowed_origins is None else allowed_origins
    inner = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        custom_starlette_routes=[_healthz_route(), _readyz_route(deployment_config, resolved_store)],
    )
    return RejectionMiddleware(inner, allowed_origins, exempt_paths=frozenset({HEALTHZ_PATH, READYZ_PATH}))


def main() -> None:
    import uvicorn

    config = DeploymentConfig(
        family=os.environ.get("FOSS_MCP_FAMILY", "pdf"),
        platform=os.environ.get("FOSS_MCP_PLATFORM", "net"),
        source_kind=os.environ.get("FOSS_MCP_SOURCE_KIND", "self_extracted"),
    )
    uvicorn.run(build_app(config), host="0.0.0.0", port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
