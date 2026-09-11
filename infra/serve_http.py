"""HTTP serving container entrypoint: the MCP server over Streamable HTTP, on port 8080 at
the SDK's default path (``/mcp``), behind Origin and protocol-version rejection.

Two transports over ONE dispatch core (``foss_mcp.mcp.server.create_server``), never a second
implementation: this module and ``infra/serve_stdio.py`` both call it; only the transport
differs. The SDK's own DNS-rebinding protection (Host/Origin) is disabled here deliberately -
``foss_mcp.mcp.transport_security.reject_request`` is the ONE place Origin and
protocol-version MUSTs are decided, wrapping the SDK's ASGI app rather than duplicating its
own, separate check.
"""

from __future__ import annotations

import os

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Send
from starlette.types import Scope as ASGIScope

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import create_server
from foss_mcp.mcp.transport_security import reject_request

DEFAULT_PORT = 8080


def allowed_origins_from_env() -> list[str]:
    raw = os.environ.get("FOSS_MCP_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class RejectionMiddleware:
    """Wraps an ASGI app: every HTTP request is checked against
    ``transport_security.reject_request`` before it can reach the wrapped app at all.

    A rejection is a plain HTTP 400 JSON error - never a JSON-RPC envelope - so it can never
    be confused with a tool call that ran and reported no match.
    """

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])}
        reason = reject_request(headers, allowed_origins=self.allowed_origins)
        if reason is not None:
            response = JSONResponse({"error": "rejected", "reason": reason}, status_code=400)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_app(
    deployment_config: DeploymentConfig,
    manifest_store: GenerationManifestStore | None = None,
    allowed_origins: list[str] | None = None,
) -> ASGIApp:
    """The ASGI app this container serves: the same ``create_server`` dispatch core as
    stdio, wrapped in Origin/protocol-version rejection.

    ``manifest_store`` and ``allowed_origins`` default to the real deployment's own store
    (``foss_mcp.mcp.server``'s ``/data/manifests``) and the ``FOSS_MCP_ALLOWED_ORIGINS`` env
    var respectively; both are overridable so tests never touch either.
    """
    server = create_server(deployment_config, manifest_store)
    allowed_origins = allowed_origins_from_env() if allowed_origins is None else allowed_origins
    inner = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    return RejectionMiddleware(inner, allowed_origins)


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
