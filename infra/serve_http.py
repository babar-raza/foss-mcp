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
readiness. ``/readyz`` calls ``foss_mcp.mcp.health.readiness_report`` against the SAME
``GenerationManifestStore`` instance ``create_server`` was built with: not merely that the
deployment's own active-generation pointer exists, but that the generation it names belongs to
THIS deployment, is well formed and carries symbols and documentation (the real reference-system
defect this replaces: a readiness probe that reported healthy while serving nothing, or serving
another product). Its answer is a JSON body with the reasons and the composition, 200 or 503; the
reasons are logged at WARNING on state transitions only (G3/TC-506). ``build_app`` also calls
``round_trip_check`` once at startup (``_log_startup_readiness``) and logs the reasons when the
deployment would not be ready.

``/metrics`` answers the Prometheus text exposition (``foss_mcp.telemetry.metrics``) to a scraper that
asks for it and the original two-field JSON otherwise; a new session refused at the session cap is
answered with ``Retry-After``, counted and logged with the pilot (G3/TC-509).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, MutableMapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import anyio.to_thread
from fetch_product_reference import load_manifest_sidecar, sidecar_name
from fetch_recent_releases import load_recent_releases_sidecar, recent_releases_sidecar_name
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Send
from starlette.types import Scope as ASGIScope
from verify_package_registry import load_package_registry_sidecar, verify_package_registry_sidecar_name

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, DocumentPresent
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.health import (
    DeploymentGenerationStore,
    ReadinessReport,
    is_alive,
    readiness_report,
    round_trip_check,
)
from foss_mcp.mcp.routing import DeploymentConfig, resolve_scope
from foss_mcp.mcp.server import create_server, server_version
from foss_mcp.mcp.sidecar_state import (
    SIDECAR_FETCH_FAILED,
    SIDECAR_NOT_PRESENT,
    SIDECAR_PRESENT,
)
from foss_mcp.mcp.tools.example_search_mode import EXAMPLE_SEARCH_LEXICAL_ONLY, example_search_mode
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from foss_mcp.mcp.transport_security import reject_request
from foss_mcp.telemetry.metrics import CONTENT_TYPE as PROMETHEUS_CONTENT_TYPE
from foss_mcp.telemetry.metrics import MetricsRegistry
from foss_mcp.telemetry.usage_recorder import UsageRecorder

DEFAULT_PORT = 8080
# The chart sets FOSS_MCP_FAMILY and FOSS_MCP_PLATFORM on every serving deployment. Together they
# name the one identity this container serves, and the only sidecar it reads is that identity's.
# There are deliberately no defaults: an unset identity must never fall back to another product.
FAMILY_ENV = "FOSS_MCP_FAMILY"
PLATFORM_ENV = "FOSS_MCP_PLATFORM"
# The chart mounts the manifests claim at this path (values.yaml manifests.mountPath).
MANIFESTS_DIR_ENV = "FOSS_MCP_MANIFESTS_DIR"
DEFAULT_MANIFESTS_DIR = "/data/manifests"
HEALTHZ_PATH = "/healthz"
READYZ_PATH = "/readyz"
METRICS_PATH = "/metrics"

# How this container searches verified examples (G3/TC-443, REQ-G3-007): stated in one startup log
# record and in this /readyz response header, from the same helper report_index_freshness uses.
EXAMPLE_SEARCH_HEADER = "X-Example-Search"
REASON_NO_ACTIVE_GENERATION = "no active generation"
REASON_MODE_UNKNOWN = "example search mode could not be determined"
LOG = logging.getLogger(__name__)

LOG_LEVEL_ENV = "FOSS_MCP_LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"
SHUTDOWN_GRACE_ENV = "FOSS_MCP_SHUTDOWN_GRACE_SECONDS"
# Inside the 30-second terminationGracePeriodSeconds default of the Helm chart (TC-177), so
# uvicorn finishes in-flight tool calls before the kubelet sends SIGKILL.
DEFAULT_SHUTDOWN_GRACE_SECONDS = 25

# Bounds on MCP sessions (G3/TC-421, REQ-G3-006). A session costs about 100 KB and the SDK defaults
# (30 minutes idle, 10,000 sessions) let any peer that opens sessions and never closes them OOM-kill
# the 192 MiB demo container in under two minutes. 200 live sessions is about 20 MB; 5 minutes of
# idleness reaps an abandoned one. Both are overridable through the environment.
SESSION_IDLE_TIMEOUT_SECONDS = 300
MAX_SESSIONS = 200
SESSION_IDLE_ENV = "FOSS_MCP_SESSION_IDLE_SECONDS"
MAX_SESSIONS_ENV = "FOSS_MCP_MAX_SESSIONS"
# A refused session (HTTP 503 at the cap) tells the client when to retry, and is counted and logged
# once per window with the pilot (G3/TC-509). ``_monotonic`` is a module-level name so a test can
# replace the clock.
SESSION_CAP_RETRY_AFTER_SECONDS = 5
_REJECT_LOG_INTERVAL_SECONDS = 10.0
_monotonic = time.monotonic

# Attributes every LogRecord carries. Anything else on a record came from ``extra=`` and is
# emitted as a top-level JSON field.
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}
# Extras that must never reach the JSON line. uvicorn logs with ``extra={"color_message": ...}``, a
# second copy of the message with ANSI colour escapes (G3/TC-474).
DROPPED_LOG_ATTRS = frozenset({"color_message"})
_JSON_HANDLER_MARK = "_foss_mcp_json_stdout"


class JsonLogFormatter(logging.Formatter):
    """One JSON object per record: ``ts``, ``level``, ``logger``, ``message``, then every
    ``extra=`` field the record carries (e.g. the tool-call logger's ``tool``, ``outcome``).

    ``json.dumps`` keeps the default ``ensure_ascii=True``, so the output is pure ASCII and
    cannot raise a UnicodeEncodeError on a cp1252 console.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in DROPPED_LOG_ATTRS and key not in payload:
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


def _positive_int_from_env(name: str, default: int) -> int:
    """``name`` as a positive whole number when set and non-empty, else ``default``. Raises
    ``ValueError`` (never echoing the value) on a non-integer or a value below 1."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number") from None
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value


def session_idle_timeout_seconds() -> int:
    """Seconds of inactivity after which an MCP session is closed: ``FOSS_MCP_SESSION_IDLE_SECONDS``
    when set and non-empty, else ``SESSION_IDLE_TIMEOUT_SECONDS``."""
    return _positive_int_from_env(SESSION_IDLE_ENV, SESSION_IDLE_TIMEOUT_SECONDS)


def max_sessions() -> int:
    """Most MCP sessions open at once: ``FOSS_MCP_MAX_SESSIONS`` when set and non-empty, else
    ``MAX_SESSIONS``. Further sessions are refused until room frees up."""
    return _positive_int_from_env(MAX_SESSIONS_ENV, MAX_SESSIONS)


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


PARSE_ERROR_MESSAGE = "Parse error: the request body is not valid JSON"
INVALID_REQUEST_MESSAGE = "Invalid request: the body is not a valid JSON-RPC message for this server"


def _sanitised_error_body(body: bytes) -> bytes | None:
    """*body* with its JSON-RPC error message replaced by a fixed string when the SDK produced it
    from a parse or validation failure, else None (leave the response alone)."""
    try:
        envelope = json.loads(body)
    except ValueError:
        return None
    if not isinstance(envelope, dict):
        return None
    error = envelope.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    if not isinstance(message, str):
        return None
    if message.startswith("Parse error:"):
        error["message"] = PARSE_ERROR_MESSAGE
    elif message.startswith("Validation error:"):
        error["message"] = INVALID_REQUEST_MESSAGE
    else:
        return None
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


class ErrorSanitisingMiddleware:
    """Wraps the Streamable HTTP app. The SDK answers a malformed JSON-RPC body with HTTP 400 and
    ``f"Parse error: {e}"`` / ``f"Validation error: {e}"``, i.e. a multi-line pydantic dump with
    union internals and a documentation URL. For exactly those responses the message becomes one
    of two fixed strings; status, id, code and the rest of the envelope are kept. Every other
    response, and every streamed (SSE) response, passes through untouched and unbuffered: only a
    400 whose content type is ``application/json`` is ever held back.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        held_start: dict[str, Any] | None = None
        chunks: list[bytes] = []

        async def sanitising_send(message: MutableMapping[str, Any]) -> None:
            nonlocal held_start
            if held_start is None and message["type"] == "http.response.start":
                content_type = next(
                    (v for k, v in message.get("headers", []) if k.lower() == b"content-type"), b""
                )
                if message["status"] == 400 and content_type.lower().startswith(b"application/json"):
                    held_start = dict(message)
                    return
                await send(message)
                return
            if held_start is None or message["type"] != "http.response.body":
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            original = b"".join(chunks)
            body = _sanitised_error_body(original)
            start = dict(held_start)
            if body is None:
                body = original
            else:
                start["headers"] = [
                    (k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"
                ] + [(b"content-length", str(len(body)).encode("ascii"))]
            await send(start)
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, sanitising_send)


class SessionCapObserverMiddleware:
    """Wraps the Streamable HTTP app (inside ``ErrorSanitisingMiddleware``). The SDK refuses a new
    session at the cap with a bare HTTP 503 ("Too many open sessions") that has no ``Retry-After``
    and is counted nowhere. For every HTTP response with status 503 on a path that is not a probe
    path, this adds ``Retry-After: SESSION_CAP_RETRY_AFTER_SECONDS`` and calls ``on_reject()`` once.
    The probe paths are exempt: a 503 from ``/readyz`` is a readiness answer, not a session refusal.
    Nothing else about the response changes, and a failure in ``on_reject`` never reaches the client.
    """

    def __init__(
        self,
        app: ASGIApp,
        on_reject: Callable[[], None],
        exempt_paths: frozenset[str] = frozenset({HEALTHZ_PATH, READYZ_PATH, METRICS_PATH}),
    ) -> None:
        self.app = app
        self.on_reject = on_reject
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        async def observing_send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start" and message["status"] == 503:
                headers = list(message.get("headers", []))
                if not any(key.lower() == b"retry-after" for key, _ in headers):
                    headers.append((b"retry-after", str(SESSION_CAP_RETRY_AFTER_SECONDS).encode("ascii")))
                message = {**message, "headers": headers}
                try:
                    self.on_reject()
                except Exception:  # noqa: BLE001 - observing a refusal must never break the response.
                    pass
            await send(message)

        await self.app(scope, receive, observing_send)


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
    """The ``/readyz`` route: 200 only when ``foss_mcp.mcp.health.readiness_report`` says this
    deployment's own scope names a real active generation that belongs to it, is well formed and
    carries symbols and documentation; 503 otherwise - never process reachability, and never
    merely "a generation pointer exists".

    The body is always JSON (``ready``, ``reasons``, ``composition``, ``generation_id``,
    ``warnings``) and the ``X-Example-Search`` header is attached to both statuses. A corrupt
    pointer is a clean 503, never a traceback: no exception can escape the handler. The failure
    reasons are logged at WARNING on a state transition only (the first probe, or whenever the
    ``(ready, reasons)`` pair changes), so a probe loop does not flood the log.

    Binds to *manifest_store* directly (the SAME instance ``create_server`` was built with, per
    ``build_app`` below) and to the scope this deployment - never a request - resolves to
    (``foss_mcp.mcp.routing.resolve_scope``), so no per-request work decides which generation
    matters.
    """
    scope = resolve_scope(deployment_config, request=None)
    generation_store = DeploymentGenerationStore.for_scope(
        manifest_store, scope, deployment_config.source_kind
    )
    last: tuple[bool, tuple[str, ...]] | None = None

    async def readyz(request: Request) -> JSONResponse:
        del request  # nothing about the request influences readiness - see module docstring.
        nonlocal last
        try:
            report = readiness_report(generation_store)
            current = (report.ready, report.reasons)
            if current != last:
                _log_readiness_transition(report, previous=last, deployment_config=deployment_config)
                last = current
            # The mode is read at request time, from the generation active now. It never decides
            # the status: a failure computing it is already folded into ``lexical-only``.
            headers = {EXAMPLE_SEARCH_HEADER: _active_example_search_mode(generation_store)[0]}
            return JSONResponse(report.to_dict(), status_code=200 if report.ready else 503, headers=headers)
        except Exception:  # noqa: BLE001 - a probe must answer, never raise.
            return JSONResponse(
                {
                    "ready": False,
                    "reasons": ["readiness check failed"],
                    "composition": {"symbol": 0, "doc": 0, "example": 0},
                    "generation_id": None,
                    "warnings": [],
                },
                status_code=503,
                headers={EXAMPLE_SEARCH_HEADER: EXAMPLE_SEARCH_LEXICAL_ONLY},
            )

    return Route(READYZ_PATH, readyz, methods=["GET"])


def _log_readiness_transition(
    report: ReadinessReport,
    *,
    previous: tuple[bool, tuple[str, ...]] | None,
    deployment_config: DeploymentConfig,
) -> None:
    """ONE record per readiness state change: WARNING "readiness failed" when not ready, INFO
    "readiness restored" when ready after a failure, INFO "readiness ok" for a first ready probe.
    The reasons are fixed phrases and exception class names, never a path or an exception message.
    """
    extra = {
        "ready": report.ready,
        "reasons": list(report.reasons),
        "generation_id": report.generation_id,
        "family": deployment_config.family,
        "platform": deployment_config.platform,
    }
    if not report.ready:
        LOG.warning("readiness failed", extra=extra)
    elif previous is not None and not previous[0]:
        LOG.info("readiness restored", extra=extra)
    else:
        LOG.info("readiness ok", extra=extra)


def _log_startup_readiness(
    generation_store: DeploymentGenerationStore, deployment_config: DeploymentConfig
) -> None:
    """One WARNING "readiness failed at startup" (with the reasons) when this deployment would
    not pass ``/readyz`` right now; nothing when it would. Never raises. The container still
    starts: ``/healthz`` stays 200 and ``/readyz`` keeps answering 503 with the reasons.
    """
    if round_trip_check(generation_store):
        return
    report = readiness_report(generation_store)
    LOG.warning(
        "readiness failed at startup",
        extra={
            "ready": report.ready,
            "reasons": list(report.reasons),
            "generation_id": report.generation_id,
            "family": deployment_config.family,
            "platform": deployment_config.platform,
        },
    )


def _active_example_search_mode(generation_store: DeploymentGenerationStore) -> tuple[str, str]:
    """``(mode, reason)`` of the deployment scope's active generation, from the one helper
    ``report_index_freshness`` also uses (``example_search_mode``), so the startup record, the
    ``/readyz`` header and the freshness report can never disagree.

    No active generation counts as lexical-only with the reason ``no active generation``. Any
    failure while reading the generation is also lexical-only: this is a statement about search,
    never a readiness decision, so it must not raise. The reason never carries an environment
    value or an exception message.
    """
    try:
        generation_id = generation_store.active_generation_id()
        if generation_id is None:
            return EXAMPLE_SEARCH_LEXICAL_ONLY, REASON_NO_ACTIVE_GENERATION
        manifest = generation_store.manifest_store.read_generation(generation_store.scope_key, generation_id)
        return example_search_mode(manifest.payload)
    except Exception:
        return EXAMPLE_SEARCH_LEXICAL_ONLY, REASON_MODE_UNKNOWN


def _log_example_search_mode(
    generation_store: DeploymentGenerationStore, deployment_config: DeploymentConfig
) -> None:
    """The one startup INFO record that states how this container searches verified examples."""
    mode, reason = _active_example_search_mode(generation_store)
    LOG.info(
        "example search mode",
        extra={
            "example_search": mode,
            "example_search_reason": reason,
            "family": deployment_config.family,
            "platform": deployment_config.platform,
        },
    )


_SAFE_GENERATION_LABEL = re.compile(r"[A-Za-z0-9._:\-]{1,200}")
_UNKNOWN_LABEL = "unknown"


class _MetricsScrape:
    """The scrape-time values behind ``/metrics``: open sessions, readiness and the active
    generation. Each accessor is shielded - any failure gives ``None`` and that gauge is omitted
    from the exposition, so a scrape never fails because one value could not be read."""

    def __init__(
        self, server: Any, generation_store: DeploymentGenerationStore, deployment_config: DeploymentConfig
    ) -> None:
        self._server = server
        self._generation_store = generation_store
        self._deployment_config = deployment_config
        self._commit_by_generation: tuple[str, str] | None = None

    def open_sessions(self) -> int | None:
        """MCP sessions the SDK's session manager holds right now. ``_server_instances`` is a private
        SDK attribute: it is read through ``getattr`` so an SDK change gives ``None``, and a test pins
        the real value against the real SDK, which is the drift guard."""
        try:
            instances = getattr(self._server.session_manager, "_server_instances", None)
            return len(instances) if instances is not None else None
        except Exception:  # noqa: BLE001 - a scrape must answer.
            return None

    def ready(self) -> bool | None:
        try:
            return readiness_report(self._generation_store).ready
        except Exception:  # noqa: BLE001 - a scrape must answer.
            return None

    def generation_info(self) -> dict[str, str] | None:
        """``generation_id``, ``source_commit`` and ``build_revision`` of the active generation, or
        None when no generation is active."""
        try:
            generation_id = self._generation_store.active_generation_id()
            if generation_id is None:
                return None
            if not isinstance(generation_id, str) or not _SAFE_GENERATION_LABEL.fullmatch(generation_id):
                generation_id = _UNKNOWN_LABEL
            return {
                "generation_id": generation_id,
                "source_commit": self._source_commit(generation_id),
                "build_revision": server_version().partition("+")[2] or _UNKNOWN_LABEL,
            }
        except Exception:  # noqa: BLE001 - a scrape must answer.
            return None

    def _source_commit(self, generation_id: str) -> str:
        cached = self._commit_by_generation
        if cached is not None and cached[0] == generation_id:
            return cached[1]
        try:
            scope = resolve_scope(self._deployment_config, request=None)
            report = report_index_freshness(
                self._generation_store.manifest_store, scope, self._deployment_config.source_kind, None
            )
            commit = report.indexed_source_commit
        except Exception:  # noqa: BLE001 - an unreadable commit is "unknown", not a failed scrape.
            commit = None
        value = (
            commit if isinstance(commit, str) and _SAFE_GENERATION_LABEL.fullmatch(commit) else _UNKNOWN_LABEL
        )
        if value != _UNKNOWN_LABEL:
            self._commit_by_generation = (generation_id, value)
        return value


def _wants_prometheus(request: Request) -> bool:
    """``?format=prometheus`` always, ``?format=<anything else>`` never; with no ``format`` parameter
    an Accept header naming ``text/plain`` or ``application/openmetrics-text``. A client sending
    ``*/*`` (curl, the TestClient) keeps the JSON body."""
    requested = request.query_params.get("format")
    if requested is not None:
        return requested == "prometheus"
    accept = request.headers.get("accept", "").lower()
    return "text/plain" in accept or "application/openmetrics-text" in accept


def _metrics_route(usage_recorder: UsageRecorder, registry: MetricsRegistry, scrape: _MetricsScrape) -> Route:
    """The ``/metrics`` route: real, live counters read from the SAME ``UsageRecorder`` instance
    ``create_server``'s own ``_call_tool`` records every tool call into (TC-142) and the SAME
    ``MetricsRegistry`` it observes every call into (TC-509) - never a separate or mocked one. A metrics
    scrape is not an MCP request, so this is exempted from ``RejectionMiddleware`` for the identical
    reason ``/healthz``/``/readyz`` are (see module docstring).

    Two representations: the Prometheus text exposition (``foss_mcp.telemetry.metrics``) when the
    scraper asks for it (see ``_wants_prometheus``), and otherwise the EXISTING two-field JSON body
    (``usage_events_queued``, ``usage_events_dropped``), which tests pin exactly.
    """

    def render() -> str:
        return registry.render_prometheus(
            open_sessions=scrape.open_sessions(),
            ready=scrape.ready(),
            generation_info=scrape.generation_info(),
            usage_queued=len(usage_recorder),
            usage_dropped=usage_recorder.dropped_count,
        )

    async def metrics(request: Request) -> Response:
        if _wants_prometheus(request):
            # Readiness reads the generation from disk, so the scrape runs off the event loop.
            text = await anyio.to_thread.run_sync(render)
            return Response(text, media_type=PROMETHEUS_CONTENT_TYPE)
        return JSONResponse(
            {
                "usage_events_queued": len(usage_recorder),
                "usage_events_dropped": usage_recorder.dropped_count,
            }
        )

    return Route(METRICS_PATH, metrics, methods=["GET"])


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set: serving reads only its own identity's sidecar")
    return value


# ---------------------------------------------------------------------
# Optional sidecars (G3/TC-507). Every sidecar problem degrades that ONE sidecar to the state
# ``fetch_failed``, logged once at WARNING; the container still starts and everything else is
# served. Nothing below raises for any sidecar content. The loaders in ``infra/fetch_*.py`` and
# ``infra/verify_package_registry.py`` still raise by design; this module catches them.
# ---------------------------------------------------------------------

# The largest baked sidecar is about 80 KB; a file over this is not a sidecar.
_SIDECAR_MAX_BYTES = 8 * 1024 * 1024
_FAILURE_MARKER_SUFFIX = ".failed"
_FAILURE_MARKER_MAX_BYTES = 4096
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?")
_GITHUB_REPOSITORY_URL = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)", re.IGNORECASE)
_RELEASE_RICHNESS = frozenset({"detailed", "templated", "none"})

_PROBLEM_OVERSIZED = "file is larger than 8 MiB"
_PROBLEM_NOT_UTF8 = "file is not UTF-8 text"
_PROBLEM_NOT_JSON = "file is not valid JSON"
_PROBLEM_NOT_OBJECT = "JSON is not an object"
_PROBLEM_NOT_LIST = "JSON is not a list"
_PROBLEM_MARKER_ONLY = "failure marker without a sidecar"
_PROBLEM_WRITER_FAILED = "the writer recorded a failed fetch"
_PROBLEM_WRONG_TYPES = "a field has the wrong type"
_PROBLEM_PLATFORM = "sidecar platform differs from the deployment"
_PROBLEM_FAMILY = "sidecar family differs from the deployment"
_PROBLEM_REPOSITORY = "source repository does not belong to the deployment family"
_PROBLEM_RELEASES_REPOSITORY = "release url does not belong to the deployment family"
_PROBLEM_RELEASES_OTHER_REPOSITORY = "release url differs from the product reference repository"
_PROBLEM_RELEASE_SHAPE = "a release entry has the wrong shape"
_PROBLEM_MISSING_KEYS = "a required key is missing"

_KIND_PRODUCT_REFERENCE = "product_reference"
_KIND_RECENT_RELEASES = "recent_releases"
_KIND_PACKAGE_REGISTRY = "package_registry"


class _ServedReleases(tuple[Release, ...]):
    """The releases ``_serving_recent_releases`` returns: an ordinary tuple of ``Release`` that also
    records the sidecar state they were resolved with, so a caller that takes the tuple (stdio) still
    learns whether an empty one means "no releases" or "could not be fetched"."""

    state: str

    def __new__(cls, releases: Sequence[Release], state: str) -> _ServedReleases:
        served = super().__new__(cls, releases)
        served.state = state
        return served


def _failure_marker(path: Path) -> tuple[bool, str | None]:
    """``(exists, stamp)`` for the ``<name>.failed`` marker beside *path*: *stamp* is the marker's last
    whitespace-separated token when it looks like an ISO timestamp, else None. Never raises."""
    marker = path.with_name(path.name + _FAILURE_MARKER_SUFFIX)
    try:
        if not marker.is_file():
            return False, None
        with marker.open("rb") as handle:
            text = handle.read(_FAILURE_MARKER_MAX_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return True, None
    tokens = text.split()
    if tokens and _ISO_TIMESTAMP.fullmatch(tokens[-1]):
        return True, tokens[-1]
    return True, None


def _read_sidecar_json(path: Path) -> tuple[str, Any]:
    """``(state, payload)`` for the JSON file *path*; never raises. An absent file is
    ``(SIDECAR_NOT_PRESENT, None)``. A file over ``_SIDECAR_MAX_BYTES``, non-UTF-8 bytes, invalid JSON or
    an OS error is ``(SIDECAR_FETCH_FAILED, problem)`` where *problem* is a fixed phrase or an exception
    class name, never file content. Otherwise ``(SIDECAR_PRESENT, parsed_json)``."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(_SIDECAR_MAX_BYTES + 1)
    except FileNotFoundError:
        return SIDECAR_NOT_PRESENT, None
    except OSError as exc:
        return SIDECAR_FETCH_FAILED, type(exc).__name__
    if len(raw) > _SIDECAR_MAX_BYTES:
        return SIDECAR_FETCH_FAILED, _PROBLEM_OVERSIZED
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return SIDECAR_FETCH_FAILED, _PROBLEM_NOT_UTF8
    try:
        return SIDECAR_PRESENT, json.loads(text)
    except (ValueError, RecursionError):
        return SIDECAR_FETCH_FAILED, _PROBLEM_NOT_JSON


def _normalised_slug(repository: str) -> str:
    """The lowercase ``owner/name`` of *repository* (a slug or a full URL), without a trailing ``.git``."""
    value = repository.strip()
    if "://" in value:
        value = value.split("://", 1)[1].partition("/")[2]
    value = value.strip("/").lower()
    return value.removesuffix(".git")


def _family_in_repository(family: str, repository: str) -> bool:
    """True when the lowercased *family* appears in the repository slug with every character outside
    a-z and 0-9 removed (the family word is part of the upstream repository name for every baked pilot)."""
    slug = re.sub(r"[^a-z0-9]", "", _normalised_slug(repository))
    return bool(family) and family.lower() in slug


def _log_sidecar_unusable(kind: str, problem: str, path: Path, deployment_config: DeploymentConfig) -> None:
    """The ONE WARNING a degraded sidecar logs. *problem* is a fixed phrase or an exception class name;
    the file is named by its file name only. Neither file content nor an exception message is logged."""
    LOG.warning(
        "sidecar unusable, degraded to fetch_failed",
        extra={
            "sidecar": kind,
            "problem": problem,
            "file": path.name,
            "family": deployment_config.family,
            "platform": deployment_config.platform,
        },
    )


def _log_refresh_failed(kind: str, path: Path, deployment_config: DeploymentConfig) -> None:
    LOG.warning(
        "refresh failed, serving the last good sidecar",
        extra={
            "sidecar": kind,
            "file": path.name,
            "family": deployment_config.family,
            "platform": deployment_config.platform,
        },
    )


def _product_reference_fetch_failed(
    path: Path, problem: str, deployment_config: DeploymentConfig, fetched_at: str | None
) -> ProductReferenceInputs:
    _log_sidecar_unusable(_KIND_PRODUCT_REFERENCE, problem, path, deployment_config)
    return ProductReferenceInputs(
        platform=deployment_config.platform,
        sidecar_state=SIDECAR_FETCH_FAILED,
        sidecar_fetched_at=fetched_at,
    )


def _document_is_well_typed(document: object) -> bool:
    if isinstance(document, DocumentPresent):
        return all(isinstance(value, str) for value in (document.path, document.sha, document.content))
    return document is None or isinstance(document, DocumentNotPresent)


def _inputs_are_well_typed(inputs: ProductReferenceInputs) -> bool:
    """The loader trusts the JSON it reads; the fields a tool later parses must be what it expects."""
    return (
        (inputs.manifest_text is None or isinstance(inputs.manifest_text, str))
        and isinstance(inputs.platform, str)
        and _document_is_well_typed(inputs.contributing)
        and _document_is_well_typed(inputs.agent_guidance)
        and _document_is_well_typed(inputs.license_document)
    )


def _merge_registry_verification(
    sidecar: ProductReferenceInputs, manifests_dir: Path, deployment_config: DeploymentConfig
) -> ProductReferenceInputs:
    """Merge the identity's registry-verification sidecar into *sidecar*. A missing file is as before; a
    corrupt or wrong-shaped file (or ``verified`` that is not a bool, or ``coordinate`` / ``checked_at``
    that are not strings) is ignored with ONE WARNING and the answers stay as if never verified. It never
    changes the product-reference state."""
    family = deployment_config.family
    platform = deployment_config.platform
    path = manifests_dir / verify_package_registry_sidecar_name(family, platform)
    state, payload = _read_sidecar_json(path)
    if state == SIDECAR_NOT_PRESENT:
        return sidecar
    problem: str | None = None
    registry_verification: dict[str, Any] = {}
    if state == SIDECAR_FETCH_FAILED:
        problem = str(payload)
    else:
        try:
            loaded = load_package_registry_sidecar(path)
        except Exception as exc:  # noqa: BLE001 - any loader failure is one ignored sidecar.
            problem = type(exc).__name__
        else:
            if (
                not isinstance(loaded, dict)
                or not isinstance(loaded.get("verified"), bool)
                or not isinstance(loaded.get("coordinate"), str)
                or not isinstance(loaded.get("checked_at"), str)
            ):
                problem = _PROBLEM_WRONG_TYPES
            else:
                registry_verification = loaded
    if problem is not None:
        LOG.warning(
            "sidecar unusable, ignored",
            extra={
                "sidecar": _KIND_PACKAGE_REGISTRY,
                "problem": problem,
                "file": path.name,
                "family": family,
                "platform": platform,
            },
        )
        return sidecar
    return dataclasses.replace(
        sidecar,
        install_verified=registry_verification["verified"],
        install_verified_coordinate=registry_verification["coordinate"],
        install_verified_checked_at=registry_verification["checked_at"],
    )


def _serving_product_reference_inputs(
    manifests_dir: Path, deployment_config: DeploymentConfig
) -> ProductReferenceInputs | None:
    """The product's own packaging manifest, from the sidecar for this deployment's identity in
    *manifests_dir*, or None when that sidecar does not exist (and no failure marker is left in its
    place): create_server then answers the explicit NotAvailable for every manifest-derived section.

    The identity is the deployment's family and platform, which ``main`` reads from the required
    ``FOSS_MCP_FAMILY`` and ``FOSS_MCP_PLATFORM``. Only the file ``sidecar_name`` gives for that
    identity is read; there is no fallback to another file.

    G3/TC-507: this never raises for sidecar content. Every problem (unreadable, corrupt, truncated, over
    8 MiB, wrong shape, wrong type, a failure placeholder, a failure marker with no data, a platform, a
    family or a source repository that is not this deployment's) is logged once at WARNING and degrades
    the sidecar to a ``ProductReferenceInputs`` whose ``sidecar_state`` is ``fetch_failed``, which
    ``get_product_reference`` answers as "could not be fetched", never as an absence. A failure marker
    beside a good sidecar does not degrade it: the writer keeps good data on a failed refresh.

    G2/TC-275 (C2 part 2): ALSO attempts to load this identity's own registry-verification
    sidecar (``package_registry_<family>_<platform>.json``) and, when it is usable, merges its
    ``verified``/``coordinate``/``checked_at`` fields into the returned ``ProductReferenceInputs``. An
    absent or unusable registry sidecar is never a hard requirement: the answers stay as if never
    verified.
    """
    family = deployment_config.family
    platform = deployment_config.platform
    path = manifests_dir / sidecar_name(family, platform)
    marker_exists, marker_stamp = _failure_marker(path)
    state, payload = _read_sidecar_json(path)
    if state == SIDECAR_NOT_PRESENT:
        if not marker_exists:
            return None
        return _product_reference_fetch_failed(path, _PROBLEM_MARKER_ONLY, deployment_config, marker_stamp)
    if state == SIDECAR_FETCH_FAILED:
        return _product_reference_fetch_failed(path, str(payload), deployment_config, marker_stamp)
    if not isinstance(payload, dict):
        return _product_reference_fetch_failed(path, _PROBLEM_NOT_OBJECT, deployment_config, marker_stamp)
    recorded_at = payload.get("fetched_at")
    fetched_at = recorded_at if isinstance(recorded_at, str) else marker_stamp
    if payload.get("state") == SIDECAR_FETCH_FAILED:
        return _product_reference_fetch_failed(path, _PROBLEM_WRITER_FAILED, deployment_config, fetched_at)
    if "family" in payload and payload["family"] != family:
        return _product_reference_fetch_failed(path, _PROBLEM_FAMILY, deployment_config, fetched_at)
    try:
        sidecar = load_manifest_sidecar(path)
    except Exception as exc:  # noqa: BLE001 - the loader raises by design; any failure is one sidecar.
        return _product_reference_fetch_failed(path, type(exc).__name__, deployment_config, fetched_at)
    if sidecar is None:
        return _product_reference_fetch_failed(path, _PROBLEM_MARKER_ONLY, deployment_config, fetched_at)
    if not _inputs_are_well_typed(sidecar):
        return _product_reference_fetch_failed(path, _PROBLEM_WRONG_TYPES, deployment_config, fetched_at)
    if sidecar.platform != platform:
        return _product_reference_fetch_failed(path, _PROBLEM_PLATFORM, deployment_config, fetched_at)
    if sidecar.source_repository is not None and not _family_in_repository(family, sidecar.source_repository):
        return _product_reference_fetch_failed(path, _PROBLEM_REPOSITORY, deployment_config, fetched_at)
    if marker_exists:
        _log_refresh_failed(_KIND_PRODUCT_REFERENCE, path, deployment_config)
    if isinstance(recorded_at, str):
        sidecar = dataclasses.replace(sidecar, sidecar_fetched_at=recorded_at)
    return _merge_registry_verification(sidecar, manifests_dir, deployment_config)


def _releases_problem(entries: list[Any], family: str, source_repository: str | None) -> str | None:
    """The fixed problem phrase for the first thing wrong with the decoded release *entries*, or None."""
    expected = _normalised_slug(source_repository) if source_repository else None
    for entry in entries:
        if not isinstance(entry, dict):
            return _PROBLEM_RELEASE_SHAPE
        if not isinstance(entry.get("tag_name"), str) or not isinstance(entry.get("body"), str):
            return _PROBLEM_RELEASE_SHAPE
        if entry.get("richness") not in _RELEASE_RICHNESS:
            return _PROBLEM_RELEASE_SHAPE
        for key in ("url", "published_at"):
            if entry.get(key) is not None and not isinstance(entry[key], str):
                return _PROBLEM_RELEASE_SHAPE
        url = entry.get("url")
        if url is None:
            continue
        match = _GITHUB_REPOSITORY_URL.search(url)
        if match is None:
            return _PROBLEM_RELEASES_REPOSITORY
        release_slug = f"{match.group(1)}/{match.group(2)}".lower().removesuffix(".git")
        if not _family_in_repository(family, release_slug):
            return _PROBLEM_RELEASES_REPOSITORY
        if expected is not None and release_slug != expected:
            return _PROBLEM_RELEASES_OTHER_REPOSITORY
    return None


def _resolve_recent_releases(
    manifests_dir: Path, deployment_config: DeploymentConfig, *, source_repository: str | None = None
) -> tuple[tuple[Release, ...], str]:
    """``(releases, state)`` for this deployment's recent-releases sidecar; never raises.

    An absent sidecar with no failure marker is ``((), not_present)``. An unusable sidecar (unreadable,
    corrupt, over 8 MiB, not a list, an entry of the wrong shape, a loader failure, a release url that is
    not this family's or, when *source_repository* is given, not that repository's) and an EMPTY list with a
    failure marker (the placeholder the releases writer leaves) are ``((), fetch_failed)``, logged once at
    WARNING. A non-empty list with a failure marker is kept (``present``) with one "refresh failed" WARNING.
    """
    family = deployment_config.family
    platform = deployment_config.platform
    path = manifests_dir / recent_releases_sidecar_name(family, platform)
    marker_exists, _ = _failure_marker(path)

    def failed(problem: str) -> tuple[tuple[Release, ...], str]:
        _log_sidecar_unusable(_KIND_RECENT_RELEASES, problem, path, deployment_config)
        return (), SIDECAR_FETCH_FAILED

    state, payload = _read_sidecar_json(path)
    if state == SIDECAR_NOT_PRESENT:
        return failed(_PROBLEM_MARKER_ONLY) if marker_exists else ((), SIDECAR_NOT_PRESENT)
    if state == SIDECAR_FETCH_FAILED:
        return failed(str(payload))
    if not isinstance(payload, list):
        return failed(_PROBLEM_NOT_LIST)
    problem = _releases_problem(payload, family, source_repository)
    if problem is not None:
        return failed(problem)
    try:
        releases = load_recent_releases_sidecar(path)
    except Exception as exc:  # noqa: BLE001 - the loader raises by design; any failure is one sidecar.
        return failed(type(exc).__name__)
    if releases is None:
        return failed(_PROBLEM_MARKER_ONLY)
    if marker_exists:
        if not releases:
            return failed(_PROBLEM_MARKER_ONLY)
        _log_refresh_failed(_KIND_RECENT_RELEASES, path, deployment_config)
    return releases, SIDECAR_PRESENT


def _serving_recent_releases(
    manifests_dir: Path, deployment_config: DeploymentConfig, *, source_repository: str | None = None
) -> _ServedReleases:
    """The product's own recent GitHub releases: the first element of ``_resolve_recent_releases``, an
    empty tuple when the sidecar is absent or unusable. The result also carries the resolved sidecar
    state as a ``state`` attribute, so a caller that takes the tuple (stdio) does not lose the difference
    between "no releases" and "could not be fetched". Never raises for sidecar content (G3/TC-507).
    """
    releases, state = _resolve_recent_releases(
        manifests_dir, deployment_config, source_repository=source_repository
    )
    return _ServedReleases(releases, state)


def _session_cap_rejection_handler(
    registry: MetricsRegistry,
    deployment_config: DeploymentConfig,
    scrape: _MetricsScrape,
    *,
    max_sessions: int,
) -> Callable[[], None]:
    """The ``on_reject`` callback for ``SessionCapObserverMiddleware``: count every refused session,
    and log ONE WARNING "session cap reached, new session refused" per
    ``_REJECT_LOG_INTERVAL_SECONDS`` window, carrying the pilot, the cap, the open sessions and how
    many refusals were counted but not logged since the previous record."""
    last_logged: float | None = None
    suppressed = 0

    def on_reject() -> None:
        nonlocal last_logged, suppressed
        registry.count_session_rejection()
        now = _monotonic()
        if last_logged is not None and now - last_logged < _REJECT_LOG_INTERVAL_SECONDS:
            suppressed += 1
            return
        previously_suppressed, suppressed = suppressed, 0
        last_logged = now
        LOG.warning(
            "session cap reached, new session refused",
            extra={
                "family": deployment_config.family,
                "platform": deployment_config.platform,
                "max_sessions": max_sessions,
                "open_sessions": scrape.open_sessions(),
                "suppressed": previously_suppressed,
            },
        )

    return on_reject


def build_app(
    deployment_config: DeploymentConfig,
    manifest_store: GenerationManifestStore | None = None,
    allowed_origins: list[str] | None = None,
    usage_recorder: UsageRecorder | None = None,
    manifests_dir: Path | None = None,
    session_idle_timeout: int | None = None,
    max_session_count: int | None = None,
    metrics: MetricsRegistry | None = None,
) -> ASGIApp:
    """The ASGI app this container serves: the same ``create_server`` dispatch core as
    stdio, wrapped in Origin/protocol-version rejection, plus real ``/healthz``, ``/readyz``,
    and ``/metrics`` probes added into the SAME app (so they share the SDK's own
    session-manager lifespan) and exempted from that rejection layer (see module docstring for
    why).

    ``manifest_store``, ``allowed_origins``, and ``usage_recorder`` default to the real
    deployment's own store (built over ``manifests_dir``, below), the
    ``FOSS_MCP_ALLOWED_ORIGINS`` env var, and a fresh real ``UsageRecorder()`` respectively; all
    three are overridable so tests never touch any of them. ``manifests_dir`` defaults to
    ``FOSS_MCP_MANIFESTS_DIR`` or ``/data/manifests``; the product reference sidecar in it, when
    present, is passed to ``create_server`` as ``product_reference_inputs``, and the recent-releases
    sidecar in it, when present, is passed as ``recent_releases`` (G2/TC-244) - the one line that
    was previously missing, which left every deployment's ``list_recent_changes`` answering ``[]``
    regardless of real tagged upstream releases. No sidecar content can stop the app being built: an
    unusable one degrades to ``fetch_failed`` and is logged (G3/TC-507). ``manifest_store`` and
    ``usage_recorder`` are each resolved ONCE, here, and handed to both ``create_server`` and
    their own route (``/readyz``, ``/metrics``) - never two separate instances for one running
    deployment, so ``/metrics`` always reports the exact counters ``_call_tool`` is live
    updating.

    ``session_idle_timeout`` and ``max_session_count`` bound the MCP sessions the transport keeps
    (G3/TC-421); each defaults to its environment variable, else ``SESSION_IDLE_TIMEOUT_SECONDS``
    / ``MAX_SESSIONS``, and is always passed to the SDK explicitly, never left to its defaults.

    ``metrics`` (G3/TC-509) is resolved ONCE like ``usage_recorder`` and handed to both
    ``create_server`` (which observes every tool call into it) and the ``/metrics`` route. A session
    refused at the cap is answered with ``Retry-After``, counted in it and logged once per window with
    the pilot (``SessionCapObserverMiddleware``). When ``usage_recorder`` is not given the production
    one is a ring buffer (``overflow="drop_oldest"``): nothing drains it, so it must hold the latest
    events rather than sit pinned at its cap with the first ones.
    """
    resolved_idle_timeout = (
        session_idle_timeout if session_idle_timeout is not None else session_idle_timeout_seconds()
    )
    resolved_max_sessions = max_session_count if max_session_count is not None else max_sessions()
    resolved_manifests_dir = (
        manifests_dir
        if manifests_dir is not None
        else Path(os.environ.get(MANIFESTS_DIR_ENV, DEFAULT_MANIFESTS_DIR))
    )
    # One directory for symbols, documents and sidecars (G3/TC-474).
    resolved_store = (
        manifest_store if manifest_store is not None else GenerationManifestStore(resolved_manifests_dir)
    )
    resolved_usage_recorder = (
        usage_recorder if usage_recorder is not None else UsageRecorder(overflow="drop_oldest")
    )
    resolved_metrics = (
        metrics
        if metrics is not None
        else MetricsRegistry(family=deployment_config.family, platform=deployment_config.platform)
    )
    readyz_route = _readyz_route(deployment_config, resolved_store)
    startup_generation_store = DeploymentGenerationStore.for_scope(
        resolved_store, resolve_scope(deployment_config, request=None), deployment_config.source_kind
    )
    _log_example_search_mode(startup_generation_store, deployment_config)
    _log_startup_readiness(startup_generation_store, deployment_config)
    # Neither sidecar can stop the server starting (G3/TC-507): a bad one degrades to fetch_failed.
    product_reference_inputs = _serving_product_reference_inputs(resolved_manifests_dir, deployment_config)
    recent_releases, recent_releases_state = _resolve_recent_releases(
        resolved_manifests_dir,
        deployment_config,
        source_repository=product_reference_inputs.source_repository if product_reference_inputs else None,
    )
    server = create_server(
        deployment_config,
        resolved_store,
        product_reference_inputs=product_reference_inputs,
        recent_releases=recent_releases,
        usage_recorder=resolved_usage_recorder,
        recent_releases_state=recent_releases_state,
        metrics=resolved_metrics,
    )
    scrape = _MetricsScrape(server, startup_generation_store, deployment_config)
    on_reject = _session_cap_rejection_handler(
        resolved_metrics, deployment_config, scrape, max_sessions=resolved_max_sessions
    )
    allowed_origins = allowed_origins_from_env() if allowed_origins is None else allowed_origins
    inner = server.streamable_http_app(
        session_idle_timeout=resolved_idle_timeout,
        max_sessions=resolved_max_sessions,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        custom_starlette_routes=[
            _healthz_route(),
            readyz_route,
            _metrics_route(resolved_usage_recorder, resolved_metrics, scrape),
        ],
    )
    # A trailing-slash path is a 404, never a redirect built from the client's Host header (TC-544).
    inner.router.redirect_slashes = False
    return RejectionMiddleware(
        ErrorSanitisingMiddleware(SessionCapObserverMiddleware(inner, on_reject)),
        allowed_origins,
        exempt_paths=frozenset({HEALTHZ_PATH, READYZ_PATH, METRICS_PATH}),
    )


def deployment_config_from_env() -> DeploymentConfig:
    """The identity this container serves, from the environment. Raises when FOSS_MCP_FAMILY or
    FOSS_MCP_PLATFORM is unset, so a container with no identity fails at start."""
    return DeploymentConfig(
        family=_required_env(FAMILY_ENV),
        platform=_required_env(PLATFORM_ENV),
        source_kind=os.environ.get("FOSS_MCP_SOURCE_KIND", "self_extracted"),
    )


def _exit_invalid_setting(variable: str, expected: str) -> NoReturn:
    """One line on stderr naming the variable (never its value), then exit 2 - no traceback."""
    print(f"serve_http: {variable} is invalid: {expected}", file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    import uvicorn

    try:
        configure_logging()
    except ValueError:
        _exit_invalid_setting(LOG_LEVEL_ENV, "expected one of CRITICAL, ERROR, WARNING, INFO, DEBUG")
    config = deployment_config_from_env()
    try:
        grace = shutdown_grace_seconds()
    except ValueError:
        _exit_invalid_setting(SHUTDOWN_GRACE_ENV, "expected a whole number of seconds, 0 or more")
    try:
        idle_timeout = session_idle_timeout_seconds()
    except ValueError:
        _exit_invalid_setting(SESSION_IDLE_ENV, "expected a whole number of seconds, 1 or more")
    try:
        session_cap = max_sessions()
    except ValueError:
        _exit_invalid_setting(MAX_SESSIONS_ENV, "expected a whole number of sessions, 1 or more")
    # log_config=None: uvicorn must not install its own dictConfig over configure_logging(),
    # or its access/error lines would bypass the JSON formatter and go to stderr as plain text.
    uvicorn.run(
        build_app(config, session_idle_timeout=idle_timeout, max_session_count=session_cap),
        host="0.0.0.0",
        port=DEFAULT_PORT,
        log_config=None,
        timeout_graceful_shutdown=grace,
    )


if __name__ == "__main__":
    main()
