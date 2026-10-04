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

import json
import logging
import os
import sys
from datetime import datetime, timezone

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
from foss_mcp.telemetry.usage_recorder import UsageRecorder

DEFAULT_PORT = 8080
HEALTHZ_PATH = "/healthz"
READYZ_PATH = "/readyz"
METRICS_PATH = "/metrics"

LOG_LEVEL_ENV = "FOSS_MCP_LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"
SHUTDOWN_GRACE_ENV = "FOSS_MCP_SHUTDOWN_GRACE_SECONDS"
# Inside the 30-second terminationGracePeriodSeconds default of the Helm chart (TC-177), so
# uvicorn finishes in-flight tool calls before the kubelet sends SIGKILL.
DEFAULT_SHUTDOWN_GRACE_SECONDS = 25

# Attributes every LogRecord carries. Anything else on a record came from ``extra=`` and is
# emitted as a top-level JSON field.
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}
_JSON_HANDLER_MARK = "_foss_mcp_json_stdout"


class JsonLogFormatter(logging.Formatter):
    """One JSON object per record: ``ts``, ``level``, ``logger``, ``message``, then every
    ``extra=`` field the record carries (e.g. the tool-call logger's ``tool``, ``outcome``).

    ``json.dumps`` keeps the default ``ensure_ascii=True``, so the output is pure ASCII and
    cannot raise a UnicodeEncodeError on a cp1252 console.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Send every log record, uvicorn's included, to stdout as JSON lines, at the level named by
    ``FOSS_MCP_LOG_LEVEL`` (default INFO). Idempotent: a repeat call replaces the handler this
    function installed earlier and leaves any other root handler alone.

    An unknown level name raises ``ValueError`` at startup rather than silently logging at a
    different level.
    """
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not getattr(h, _JSON_HANDLER_MARK, False)]
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    setattr(handler, _JSON_HANDLER_MARK, True)
    root.addHandler(handler)
    level_name = os.environ.get(LOG_LEVEL_ENV, "").strip().upper() or DEFAULT_LOG_LEVEL
    root.setLevel(level_name)


def shutdown_grace_seconds() -> int:
    """The uvicorn ``timeout_graceful_shutdown`` window: ``FOSS_MCP_SHUTDOWN_GRACE_SECONDS``
    when set and non-empty, else 25. Uvicorn itself handles SIGTERM; no extra signal handler.
    """
    raw = os.environ.get(SHUTDOWN_GRACE_ENV, "").strip()
    seconds = int(raw) if raw else DEFAULT_SHUTDOWN_GRACE_SECONDS
    if seconds < 0:
        raise ValueError(f"{SHUTDOWN_GRACE_ENV} must be >= 0, got {seconds}")
    return seconds


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


def _metrics_route(usage_recorder: UsageRecorder) -> Route:
    """The ``/metrics`` route: real, live counters read from the SAME ``UsageRecorder``
    instance ``create_server``'s own ``_call_tool`` records every tool call into (TC-142) -
    never a separate or mocked one. A metrics scrape is not an MCP request, so this is exempted
    from ``RejectionMiddleware`` for the identical reason ``/healthz``/``/readyz`` are (see
    module docstring).
    """

    async def metrics(request: Request) -> JSONResponse:
        del request  # the response depends only on the shared recorder's own live state.
        return JSONResponse(
            {
                "usage_events_queued": len(usage_recorder),
                "usage_events_dropped": usage_recorder.dropped_count,
            }
        )

    return Route(METRICS_PATH, metrics, methods=["GET"])


def build_app(
    deployment_config: DeploymentConfig,
    manifest_store: GenerationManifestStore | None = None,
    allowed_origins: list[str] | None = None,
    usage_recorder: UsageRecorder | None = None,
) -> ASGIApp:
    """The ASGI app this container serves: the same ``create_server`` dispatch core as
    stdio, wrapped in Origin/protocol-version rejection, plus real ``/healthz``, ``/readyz``,
    and ``/metrics`` probes added into the SAME app (so they share the SDK's own
    session-manager lifespan) and exempted from that rejection layer (see module docstring for
    why).

    ``manifest_store``, ``allowed_origins``, and ``usage_recorder`` default to the real
    deployment's own store (``foss_mcp.mcp.server``'s ``/data/manifests``), the
    ``FOSS_MCP_ALLOWED_ORIGINS`` env var, and a fresh real ``UsageRecorder()`` respectively; all
    three are overridable so tests never touch any of them. ``manifest_store`` and
    ``usage_recorder`` are each resolved ONCE, here, and handed to both ``create_server`` and
    their own route (``/readyz``, ``/metrics``) - never two separate instances for one running
    deployment, so ``/metrics`` always reports the exact counters ``_call_tool`` is live
    updating.
    """
    resolved_store = manifest_store if manifest_store is not None else _default_manifest_store()
    resolved_usage_recorder = usage_recorder if usage_recorder is not None else UsageRecorder()
    server = create_server(deployment_config, resolved_store, usage_recorder=resolved_usage_recorder)
    allowed_origins = allowed_origins_from_env() if allowed_origins is None else allowed_origins
    inner = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        custom_starlette_routes=[
            _healthz_route(),
            _readyz_route(deployment_config, resolved_store),
            _metrics_route(resolved_usage_recorder),
        ],
    )
    return RejectionMiddleware(
        inner, allowed_origins, exempt_paths=frozenset({HEALTHZ_PATH, READYZ_PATH, METRICS_PATH})
    )


def main() -> None:
    import uvicorn

    configure_logging()
    config = DeploymentConfig(
        family=os.environ.get("FOSS_MCP_FAMILY", "pdf"),
        platform=os.environ.get("FOSS_MCP_PLATFORM", "net"),
        source_kind=os.environ.get("FOSS_MCP_SOURCE_KIND", "self_extracted"),
    )
    # log_config=None: uvicorn must not install its own dictConfig over configure_logging(),
    # or its access/error lines would bypass the JSON formatter and go to stderr as plain text.
    uvicorn.run(
        build_app(config),
        host="0.0.0.0",
        port=DEFAULT_PORT,
        log_config=None,
        timeout_graceful_shutdown=shutdown_grace_seconds(),
    )


if __name__ == "__main__":
    main()
