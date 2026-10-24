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

``/metrics`` answers the Prometheus text exposition (``foss_mcp.telemetry.metrics``) by default and the
original two-field JSON under ``?format=json`` (G3/TC-564; before it the text needed an ``Accept``
header); a new session refused at the session cap is answered with ``Retry-After``, counted and logged with
the pilot (G3/TC-509).

G3/TC-564 (REQ-G3-009) added, all wired in ``build_app``: ``SidecarWatcher`` (sidecars re-read when they
change; a missing required one is logged, warned in ``/readyz`` and counted in ``/metrics``), Host validation
and one JSON-RPC error body for every rejection (``RejectionMiddleware``), ``SessionEvictionMiddleware``
(the oldest idle session is evicted at the cap), ``RequestIdMiddleware`` (an invalid JSON-RPC id is an
error, not an empty 202), ``ReadTimeoutMiddleware`` (a stalled body is a 408), ``ClientDisconnectMiddleware``
(a client hanging up is a DEBUG line) and, in ``main``, ``header_timeout_protocol`` (a stalled request head
closes the connection).
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import json
import logging
import os
import re
import socket
import sys
import threading
import time
from collections.abc import Callable, MutableMapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import anyio
import anyio.to_thread
from fetch_product_reference import load_manifest_sidecar, sidecar_name
from fetch_recent_releases import load_recent_releases_sidecar, recent_releases_sidecar_name
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Send
from starlette.types import Scope as ASGIScope
from verify_package_registry import load_package_registry_sidecar, verify_package_registry_sidecar_name

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, DocumentPresent
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import sidecar_state
from foss_mcp.mcp.health import (
    DeploymentGenerationStore,
    ReadinessReport,
    is_alive,
    readiness_report,
    round_trip_check,
)
from foss_mcp.mcp.routing import DeploymentConfig, resolve_scope
from foss_mcp.mcp.server import SidecarSnapshot, create_server, server_version
from foss_mcp.mcp.sidecar_state import (
    SIDECAR_FETCH_FAILED,
    SIDECAR_NOT_PRESENT,
    SIDECAR_PRESENT,
)
from foss_mcp.mcp.tools.example_search_mode import EXAMPLE_SEARCH_LEXICAL_ONLY, example_search_mode
from foss_mcp.mcp.tools.get_product_reference import (
    KnownHazard,
    ProductReferenceInputs,
    known_hazards_from_manifest,
)
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from foss_mcp.mcp.transport_security import Rejection, reject_request_detail, rejection_body
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
# G3/TC-582 (rework): config/products/ is a sibling of infra/ at the repository root, and both are
# baked verbatim into the serving image at that same relative layout (Dockerfile.serving's
# `COPY config/ ./config/` and `COPY infra/serve_http.py ./infra/serve_http.py`, both landing under
# WORKDIR /app) - so this is the real, always-local path to a deployment's own product manifest at
# serve time, independent of the process's current working directory and needing no network fetch.
_PRODUCT_MANIFESTS_ROOT = Path(__file__).resolve().parent.parent / "config" / "products"
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
# The idle clock of ``SessionEvictionMiddleware``: its own name, so a test that replaces ``_monotonic`` to move
# the log-rate-limit windows does not also age the sessions.
_eviction_clock = time.monotonic

# G3/TC-564 (RD3-125). At the session cap the OLDEST session that has been idle for at least this many
# seconds (no request in flight, none for that long) is evicted to make room, instead of every new client
# being refused until the idle sweep; a session used within the window is never evicted, so a busy client
# is never cut off to admit a new one. Overridable; 0 evicts the oldest idle session at once.
SESSION_EVICT_MIN_IDLE_SECONDS = 10
SESSION_EVICT_MIN_IDLE_ENV = "FOSS_MCP_SESSION_EVICT_MIN_IDLE_SECONDS"
# A client that opens a connection and then stalls (the header block or the request body never completes) is
# disconnected after this long. Both are overridable through the environment.
HEADER_READ_TIMEOUT_SECONDS = 15
BODY_READ_TIMEOUT_SECONDS = 30
HEADER_TIMEOUT_ENV = "FOSS_MCP_HEADER_TIMEOUT_SECONDS"
BODY_TIMEOUT_ENV = "FOSS_MCP_BODY_TIMEOUT_SECONDS"
# Host names accepted besides the defaults (loopback, IP literals, single-label names, the machine's own
# host name, in-cluster service domains): a comma separated list of names, ``*.suffix`` patterns or ``*``.
ALLOWED_HOSTS_ENV = "FOSS_MCP_ALLOWED_HOSTS"
CONTAINER_NAME_ENV = "FOSS_MCP_CONTAINER_NAME"
MCP_PATH = "/mcp"
SESSION_HEADER = "mcp-session-id"
_REJECTION_REASON_LOG_MAX = 200
_LOGGED_PATH_MAX = 100
_LOGGED_SESSION_ID_MAX = 64
# The most a JSON-RPC body is read into memory to check its request id; a larger one skips the id
# check and is passed through as-is (still subject to ``_MAX_REQUEST_BODY_BYTES`` below).
_ID_CHECK_MAX_BODY_BYTES = 1024 * 1024
# The hard ceiling on any ``/mcp`` POST body, enforced independently of whatever the underlying SDK
# does on its own. Confirmed live in-process (G3/TC-581, 2026-10-24): the installed SDK (``mcp``
# 2.3.0) does wrap its Streamable HTTP endpoint in its own ``RequestBodyLimitMiddleware`` with a
# default 4 MiB cap - but that middleware sits INSIDE the Starlette router, strictly behind this
# one, so a request between the 1 MiB id-check threshold above and that 4 MiB cap was never
# rejected by anything (live-verified: a 2 MiB POST was fully read, parsed and Pydantic-validated).
# Worse, when the SDK's own cap does fire it answers a bare ``text/plain`` 413 ("Request body too
# large"), not this server's one shared JSON-RPC error envelope - relying on it would mean two
# different error shapes depending on body size. Every real request this server actually serves
# (a ``tools/call`` with a ``get_symbol``/``search_docs``/``find_examples``-style argument: a
# handful of short strings - fqn, doc_id, a search query) is well under a few KiB, so 4 MiB is
# comfortably above any legitimate request while matching the SDK's own ceiling: this check always
# runs first (this middleware wraps the SDK's app, never the reverse), so an oversized body is
# always rejected HERE, with this server's own envelope, before the SDK's differently-shaped
# limiter ever gets a chance to see it.
_MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024
JSONRPC_INVALID_REQUEST_CODE = -32600
INVALID_ID_MESSAGE = "Invalid Request: a JSON-RPC id must be a string or an integer"
REQUEST_TOO_LARGE_MESSAGE = "Invalid Request: the request body exceeds this server's maximum size"
UNKNOWN_SESSION_MESSAGE = (
    "Session not found: this server has no session with that id (it was closed, evicted as idle, or the "
    "server restarted). Send a new initialize request without an Mcp-Session-Id header to start one."
)

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


def _non_negative_int_from_env(name: str, default: int) -> int:
    """``name`` as a whole number of 0 or more when set and non-empty, else ``default``. Raises
    ``ValueError`` (never echoing the value) on a non-integer or a negative value."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number") from None
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def session_evict_min_idle_seconds() -> int:
    """Seconds a session must have been idle to be evicted at the cap: ``FOSS_MCP_SESSION_EVICT_MIN_IDLE_SECONDS``
    when set and non-empty, else ``SESSION_EVICT_MIN_IDLE_SECONDS``. 0 is allowed."""
    return _non_negative_int_from_env(SESSION_EVICT_MIN_IDLE_ENV, SESSION_EVICT_MIN_IDLE_SECONDS)


def header_timeout_seconds() -> int:
    """The stalled-header disconnect window: ``FOSS_MCP_HEADER_TIMEOUT_SECONDS`` when set, else 15."""
    return _positive_int_from_env(HEADER_TIMEOUT_ENV, HEADER_READ_TIMEOUT_SECONDS)


def body_timeout_seconds() -> int:
    """The stalled-body disconnect window: ``FOSS_MCP_BODY_TIMEOUT_SECONDS`` when set, else 30."""
    return _positive_int_from_env(BODY_TIMEOUT_ENV, BODY_READ_TIMEOUT_SECONDS)


def allowed_origins_from_env() -> list[str]:
    raw = os.environ.get("FOSS_MCP_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def allowed_hosts_from_env() -> list[str]:
    """The extra Host names this container answers to (G3/TC-564): ``FOSS_MCP_ALLOWED_HOSTS`` (comma
    separated), the container name (``FOSS_MCP_CONTAINER_NAME``) and the machine's own host name, which is
    the container id inside Docker. Loopback, IP literals and single-label names are always accepted by
    ``transport_security.host_is_allowed``; this list is for dotted names a peer reaches the server by."""
    hosts = [host.strip() for host in os.environ.get(ALLOWED_HOSTS_ENV, "").split(",") if host.strip()]
    container = os.environ.get(CONTAINER_NAME_ENV, "").strip()
    if container:
        hosts.append(container)
    try:
        hosts.append(socket.gethostname())
    except OSError:
        pass
    return hosts


class _RateLimitedLog:
    """WARNING records of one kind, at most ``burst`` per ``interval`` seconds; the records dropped in a window
    are counted and reported (``suppressed``) on the first record of the next one. Thread safe."""

    def __init__(self, logger: logging.Logger, *, interval: float = 10.0, burst: int = 5) -> None:
        self._logger = logger
        self._interval = interval
        self._burst = burst
        self._lock = threading.Lock()
        self._window_start: float | None = None
        self._emitted = 0
        self._suppressed = 0

    def warning(self, message: str, extra: dict[str, Any]) -> None:
        with self._lock:
            now = _monotonic()
            carried = 0
            if self._window_start is None or now - self._window_start >= self._interval:
                self._window_start = now
                self._emitted = 0
                carried, self._suppressed = self._suppressed, 0
            if self._emitted >= self._burst:
                self._suppressed += 1
                return
            self._emitted += 1
        self._logger.warning(message, extra={**extra, "suppressed": carried})


class RejectionMiddleware:
    """Wraps an ASGI app: every HTTP request is checked against
    ``transport_security.reject_request`` before it can reach the wrapped app at all.

    A rejection is an HTTP 400 (421 for a Host that is not allowed) whose body is a JSON-RPC error with a null
    id (``transport_security.rejection_body``, the shape the SDK itself answers with, G3/TC-564), so it can
    never be confused with a tool call that ran and reported no match, which is a 200 ``CallToolResult``.

    ``exempt_paths`` (``/healthz`` and ``/readyz``, in practice) never reach ``reject_request``
    at all: the Origin/protocol-version MUSTs are an MCP-transport contract, and a plain
    container healthcheck request is not an MCP request and carries neither header.
    """

    def __init__(
        self,
        app: ASGIApp,
        allowed_origins: list[str],
        exempt_paths: frozenset[str] = frozenset(),
        *,
        allowed_hosts: Sequence[str] = (),
        deployment_config: DeploymentConfig | None = None,
    ) -> None:
        self.app = app
        self.allowed_origins = allowed_origins
        self.exempt_paths = exempt_paths
        self.allowed_hosts = tuple(allowed_hosts)
        self._deployment_config = deployment_config
        self._log_limiter = _RateLimitedLog(LOG)

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])}
        rejection = reject_request_detail(
            headers, allowed_origins=self.allowed_origins, allowed_hosts=self.allowed_hosts
        )
        if rejection is not None:
            self._log_rejection(rejection, scope)
            # G3/TC-564: the one body shape every transport rejection shares (a JSON-RPC error, id null).
            response = JSONResponse(rejection_body(rejection.reason), status_code=rejection.status)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _log_rejection(self, rejection: Rejection, scope: ASGIScope) -> None:
        """One WARNING per rejection, rate limited, with the reason (G3/TC-564). The reason is bounded: it can
        quote a client-chosen header value."""
        config = self._deployment_config
        self._log_limiter.warning(
            "request rejected",
            {
                "reject_kind": rejection.kind,
                "reason": rejection.reason[:_REJECTION_REASON_LOG_MAX],
                "status": rejection.status,
                "method": str(scope.get("method", ""))[:16],
                "path": str(scope.get("path", ""))[:_LOGGED_PATH_MAX],
                "family": config.family if config else None,
                "platform": config.platform if config else None,
            },
        )


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
    elif message == "Session not found":
        # G3/TC-564 (RD3-125): the SDK's bare 404 gives a client that lost its session (a restart, an
        # idle eviction) nothing to recover with; the MCP rule is to initialize a new session.
        error["message"] = UNKNOWN_SESSION_MESSAGE
    else:
        return None
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


class ErrorSanitisingMiddleware:
    """Wraps the Streamable HTTP app. The SDK answers a malformed JSON-RPC body with HTTP 400 and
    ``f"Parse error: {e}"`` / ``f"Validation error: {e}"``, i.e. a multi-line pydantic dump with
    union internals and a documentation URL. For exactly those responses the message becomes one
    of two fixed strings; status, id, code and the rest of the envelope are kept. The bare 404
    "Session not found" gains the recovery instruction (G3/TC-564). Every other
    response, and every streamed (SSE) response, passes through untouched and unbuffered: only a
    400 or 404 whose content type is ``application/json`` is ever held back.
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
                if message["status"] in (400, 404) and content_type.lower().startswith(b"application/json"):
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


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """The exceptions inside *exc*: itself, or every leaf of a (nested) exception group."""
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for inner in exc.exceptions:
            leaves.extend(_leaf_exceptions(inner))
        return leaves
    return [exc]


def _request_ids(scope: ASGIScope, config: DeploymentConfig | None) -> dict[str, Any]:
    """The identifiers a transport-level log record carries: the pilot, the method and path, and the MCP
    session id (bounded and restricted to safe characters, because the client chooses it)."""
    session_id: str | None = None
    for key, value in scope.get("headers", []):
        if key.lower() == SESSION_HEADER.encode("ascii"):
            session_id = re.sub(r"[^A-Za-z0-9._:\-]", "?", value.decode("latin-1")[:_LOGGED_SESSION_ID_MAX])
            break
    return {
        "family": config.family if config else None,
        "platform": config.platform if config else None,
        "method": str(scope.get("method", ""))[:16],
        "path": str(scope.get("path", ""))[:_LOGGED_PATH_MAX],
        "session_id": session_id,
    }


# The identifiers of the request the current task is serving, for log records emitted by code that does not
# know the request (the SDK's own POST handler). Set by ``ClientDisconnectMiddleware``.
_REQUEST_IDS: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "foss_mcp_request_ids", default=None
)
# The loggers that report a client hanging up mid-request as an exception: the SDK's POST handler catches
# Starlette's ``ClientDisconnect`` itself and logs "Error handling POST request" at ERROR with a traceback;
# uvicorn logs an escaped one as "Exception in ASGI application".
_DISCONNECT_LOGGERS = ("mcp.server.streamable_http", "uvicorn.error")


class _ClientDisconnectLogFilter(logging.Filter):
    """Turns a log record whose exception is only ``ClientDisconnect`` into ONE DEBUG line carrying the
    request's pilot, method, path and session id, with no traceback (G3/TC-564, RD3-124). A client that hangs
    up is the client's choice, not a server fault. When DEBUG is not enabled the record is dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        info = record.exc_info
        if not (info and info[1] is not None):
            return True
        if not all(isinstance(leaf, ClientDisconnect) for leaf in _leaf_exceptions(info[1])):
            return True
        if not logging.getLogger(record.name).isEnabledFor(logging.DEBUG):
            return False
        record.levelno = logging.DEBUG
        record.levelname = "DEBUG"
        record.msg = "client disconnected"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        for key, value in (_REQUEST_IDS.get() or {}).items():
            setattr(record, key, value)
        return True


def install_client_disconnect_log_filter() -> None:
    """Attach ``_ClientDisconnectLogFilter`` to the loggers in ``_DISCONNECT_LOGGERS`` (once)."""
    for name in _DISCONNECT_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(existing, _ClientDisconnectLogFilter) for existing in target.filters):
            target.addFilter(_ClientDisconnectLogFilter())


class ClientDisconnectMiddleware:
    """Wraps the Streamable HTTP app. A client that hangs up while the app is still reading its request makes
    Starlette raise ``ClientDisconnect``, which uvicorn used to log as ``Exception in ASGI application`` at
    ERROR with a traceback and no ids (and the SDK logs as ``Error handling POST request``). That is the
    client's choice, not a server fault (G3/TC-564, RD3-124): an exception that escapes is absorbed here and
    recorded as ONE DEBUG line carrying the pilot, method, path and session id, and the request's ids are
    published to ``_ClientDisconnectLogFilter`` for the records the SDK logs itself. Any other exception, and
    any group holding one, propagates untouched."""

    def __init__(self, app: ASGIApp, deployment_config: DeploymentConfig | None = None) -> None:
        self.app = app
        self._deployment_config = deployment_config

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ids = _request_ids(scope, self._deployment_config)
        token = _REQUEST_IDS.set(ids)
        try:
            await self.app(scope, receive, send)
        except (ClientDisconnect, BaseExceptionGroup) as exc:
            if not all(isinstance(leaf, ClientDisconnect) for leaf in _leaf_exceptions(exc)):
                raise
            LOG.debug("client disconnected", extra=ids)
        finally:
            _REQUEST_IDS.reset(token)


class _BodyReadTimeoutError(BaseException):
    """The request body stopped arriving (raised inside the app, answered 408 by ``ReadTimeoutMiddleware``)."""


class ReadTimeoutMiddleware:
    """Wraps the Streamable HTTP app. While a request body is still being received, each read of the next
    chunk may take at most ``timeout_seconds``; a client that sends the headers and then stalls is answered
    408 (a JSON-RPC error, ``Connection: close``) instead of holding the connection and its task for ever.
    Once the body is complete, reads are untimed: a long-lived GET stream waits on ``receive`` for the
    client to disconnect and must not be cut off (G3/TC-564, RD3-125)."""

    def __init__(
        self,
        app: ASGIApp,
        timeout_seconds: float = BODY_READ_TIMEOUT_SECONDS,
        deployment_config: DeploymentConfig | None = None,
    ) -> None:
        self.app = app
        self.timeout_seconds = timeout_seconds
        self._deployment_config = deployment_config
        self._log_limiter = _RateLimitedLog(LOG)

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body_complete = False
        response_started = False

        async def timed_receive() -> MutableMapping[str, Any]:
            nonlocal body_complete
            if body_complete:
                return await receive()
            with anyio.move_on_after(self.timeout_seconds) as timeout:
                message = await receive()
            if timeout.cancelled_caught:
                raise _BodyReadTimeoutError
            if message["type"] == "http.request" and not message.get("more_body", False):
                body_complete = True
            return message

        async def tracking_send(message: MutableMapping[str, Any]) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, timed_receive, tracking_send)
        except (_BodyReadTimeoutError, BaseExceptionGroup) as exc:
            if not all(isinstance(leaf, _BodyReadTimeoutError) for leaf in _leaf_exceptions(exc)):
                raise
            self._log_limiter.warning(
                "request body read timed out",
                {**_request_ids(scope, self._deployment_config), "timeout_seconds": self.timeout_seconds},
            )
            if not response_started:
                response = JSONResponse(
                    rejection_body("Request Timeout: the request body did not arrive in time"),
                    status_code=408,
                    headers={"connection": "close"},
                )
                await response(scope, receive, send)


def _id_is_valid(value: object) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _invalid_id_present(document: object) -> bool:
    """True when a decoded JSON-RPC message (or a batch of them) carries an ``id`` that is neither a string
    nor an integer. A message with no ``id`` at all is a notification and is fine."""
    messages = document if isinstance(document, list) else [document]
    return any(isinstance(item, dict) and "id" in item and not _id_is_valid(item["id"]) for item in messages)


class RequestIdMiddleware:
    """Wraps the Streamable HTTP app. The SDK treats a JSON-RPC message whose ``id`` is ``null``, a boolean, a
    fraction, an array or an object as a NOTIFICATION and answers it with an empty HTTP 202, so a client with
    a bug gets silence. A POST to the MCP path with such an id is answered 400 with the JSON-RPC "Invalid
    Request" error (code -32600, id null) instead (G3/TC-564, RD3-125). The body (up to 1 MiB; a larger one
    is passed through unchecked) is buffered here, in full, and replayed to the app byte for byte.

    A second, independent, always-enforced ceiling (``max_request_body_bytes``, G3/TC-581) bounds that same
    buffering loop regardless of the id check: the running total is tracked all the way to the body's
    natural end (never abandoned early just because the id-check threshold was already crossed - that was
    the original defect, G3/TC-581's audit finding), and the moment it crosses the ceiling, buffering stops
    for good - no further byte is ever pulled from the client or handed to the app - and the request is
    rejected with this server's own JSON-RPC error envelope (never the differently-shaped one the SDK's own
    internal limiter would otherwise answer with, and never left for the SDK to fully buffer and parse
    first). A body over the id-check threshold but at or under this ceiling is still buffered in full (so
    the ceiling can actually be enforced against its real size) and still skips the id check, exactly as
    before - only its internal delivery changed, not its observable outcome."""

    def __init__(
        self,
        app: ASGIApp,
        path: str = MCP_PATH,
        max_body_bytes: int = _ID_CHECK_MAX_BODY_BYTES,
        max_request_body_bytes: int = _MAX_REQUEST_BODY_BYTES,
    ) -> None:
        self.app = app
        self.path = path
        self.max_body_bytes = max_body_bytes
        self.max_request_body_bytes = max_request_body_bytes

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != self.path:
            await self.app(scope, receive, send)
            return
        buffered: list[MutableMapping[str, Any]] = []
        total = 0
        complete = False
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > self.max_request_body_bytes:
                # The hard ceiling: stop buffering immediately, before reading another byte from the
                # client or ever calling ``self.app`` - the remainder of the body is never read at all.
                response = JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": JSONRPC_INVALID_REQUEST_CODE, "message": REQUEST_TOO_LARGE_MESSAGE},
                    },
                    status_code=400,
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                complete = True
                break
        if complete and total <= self.max_body_bytes:
            body = b"".join(item.get("body", b"") for item in buffered)
            try:
                document = json.loads(body)
            except ValueError:
                document = None  # not JSON: the SDK answers its own parse error
            if _invalid_id_present(document):
                response = JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": JSONRPC_INVALID_REQUEST_CODE, "message": INVALID_ID_MESSAGE},
                    },
                    status_code=400,
                )
                await response(scope, receive, send)
                return
        replay = iter(buffered)

        async def replaying_receive() -> MutableMapping[str, Any]:
            item = next(replay, None)
            return item if item is not None else await receive()

        await self.app(scope, replaying_receive, send)


class _EvictionCounter:
    """How many sessions ``SessionEvictionMiddleware`` has evicted (read by the ``/metrics`` route, which is
    built before Starlette builds the middleware)."""

    def __init__(self) -> None:
        self.count = 0

    def value(self) -> int:
        return self.count


class SessionEvictionMiddleware:
    """Wraps the Streamable HTTP app (innermost). At the session cap the SDK refuses every new session until
    its idle sweep (5 minutes by default), so 200 abandoned sessions lock every new client out. Before a
    request that would open a session reaches the SDK, this evicts the OLDEST session that has no request in
    flight and has been idle for at least ``min_idle_seconds`` when the cap is reached; the evicted
    session's next request is the usual 404 with the recovery instruction. A session used more recently than
    that is never evicted, so with nothing idle enough the SDK's own 503 (with ``Retry-After``) stands.

    Activity is tracked here, per session id, from the ``Mcp-Session-Id`` header of each request and of each
    initialize response. The session table itself belongs to the SDK; it is reached through
    ``get_manager`` (a callable, because the manager exists only after the Starlette app is built)."""

    def __init__(
        self,
        app: ASGIApp,
        get_manager: Callable[[], Any],
        *,
        max_sessions: int,
        min_idle_seconds: float,
        deployment_config: DeploymentConfig | None = None,
        path: str = MCP_PATH,
        counter: _EvictionCounter | None = None,
    ) -> None:
        self.app = app
        self._get_manager = get_manager
        self._max_sessions = max_sessions
        self._min_idle_seconds = min_idle_seconds
        self._deployment_config = deployment_config
        self._path = path
        self._last_seen: dict[str, float] = {}
        self._in_flight: dict[str, int] = {}
        self._lock = anyio.Lock()
        self._log_limiter = _RateLimitedLog(LOG)
        self._counter = counter if counter is not None else _EvictionCounter()

    @staticmethod
    def _session_id(scope: ASGIScope) -> str | None:
        for key, value in scope.get("headers", []):
            if key.lower() == SESSION_HEADER.encode("ascii"):
                return value.decode("latin-1")
        return None

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != self._path:
            await self.app(scope, receive, send)
            return
        session_id = self._session_id(scope)
        if session_id is not None:
            self._last_seen[session_id] = _eviction_clock()
            self._in_flight[session_id] = self._in_flight.get(session_id, 0) + 1
            try:
                await self.app(scope, receive, send)
            finally:
                remaining = self._in_flight.get(session_id, 1) - 1
                if remaining > 0:
                    self._in_flight[session_id] = remaining
                else:
                    self._in_flight.pop(session_id, None)
                if session_id in self._last_seen:
                    self._last_seen[session_id] = _eviction_clock()
            return
        if scope.get("method") == "POST":
            await self._make_room()

        async def observing_send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start" and message["status"] < 400:
                for key, value in message.get("headers", []):
                    if key.lower() == SESSION_HEADER.encode("ascii"):
                        self._last_seen[value.decode("latin-1")] = _eviction_clock()
            await send(message)

        await self.app(scope, receive, observing_send)

    async def _make_room(self) -> None:
        """Evict the oldest idle session when the cap is reached. Never raises."""
        try:
            manager = self._get_manager()
            instances = getattr(manager, "_server_instances", None)
            if instances is None or len(instances) < self._max_sessions:
                return
            async with self._lock:
                if len(instances) < self._max_sessions:
                    return
                now = _eviction_clock()
                live = set(instances)
                for stale in [sid for sid in self._last_seen if sid not in live]:
                    del self._last_seen[stale]
                candidates: list[tuple[float, str]] = []
                for sid in live:
                    if self._in_flight.get(sid, 0) > 0:
                        continue
                    seen = self._last_seen.setdefault(sid, now)
                    if now - seen >= self._min_idle_seconds:
                        candidates.append((seen, sid))
                if not candidates:
                    return
                seen, victim = min(candidates)
                transport = instances.get(victim)
                discard = getattr(manager, "_discard_session", None)
                if transport is None or discard is None:
                    return
                open_before = len(instances)
                await discard(victim, transport)
                self._last_seen.pop(victim, None)
                self._counter.count += 1
                config = self._deployment_config
                self._log_limiter.warning(
                    "oldest idle session evicted to admit a new one",
                    {
                        "family": config.family if config else None,
                        "platform": config.platform if config else None,
                        "idle_seconds": round(now - seen, 1),
                        "open_sessions": open_before,
                        "max_sessions": self._max_sessions,
                        "evictions_total": self._counter.count,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - making room must never fail the request that asked.
            LOG.warning("session eviction failed", extra={"error": type(exc).__name__})


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


def _readyz_route(
    deployment_config: DeploymentConfig,
    manifest_store: GenerationManifestStore,
    sidecars: SidecarWatcher | None = None,
) -> Route:
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

    G3/TC-564: with *sidecars*, a required sidecar that is not on the server adds a ``warnings`` entry
    (never a failure: ``ready`` and the status are unchanged).
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
            if sidecars is not None:
                missing = sidecars.warnings()
                if missing:
                    report = dataclasses.replace(report, warnings=report.warnings + missing)
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


def _wants_json(request: Request) -> bool:
    """True only for an explicit ``?format=json``: the two-field JSON body is the legacy form (G3/TC-564).
    Every other request, a bare ``curl`` and a Prometheus scraper alike, gets the Prometheus text."""
    return request.query_params.get("format") == "json"


def _metrics_route(
    usage_recorder: UsageRecorder,
    registry: MetricsRegistry,
    scrape: _MetricsScrape,
    sidecars: SidecarWatcher | None = None,
    session_evictions: Callable[[], int] | None = None,
) -> Route:
    """The ``/metrics`` route: real, live counters read from the SAME ``UsageRecorder`` instance
    ``create_server``'s own ``_call_tool`` records every tool call into (TC-142) and the SAME
    ``MetricsRegistry`` it observes every call into (TC-509) - never a separate or mocked one. A metrics
    scrape is not an MCP request, so this is exempted from ``RejectionMiddleware`` for the identical
    reason ``/healthz``/``/readyz`` are (see module docstring).

    Two representations. G3/TC-564 (RD3-124): the DEFAULT is the Prometheus text exposition
    (``foss_mcp.telemetry.metrics``) - until now a bare ``curl`` got two counters and the real metrics
    needed an ``Accept`` header - followed by the sidecar-state series (``SidecarWatcher``) and the
    session-eviction counter. The original two-field JSON body (``usage_events_queued``,
    ``usage_events_dropped``) stays under an explicit ``?format=json``.
    """

    def render() -> str:
        text = registry.render_prometheus(
            open_sessions=scrape.open_sessions(),
            ready=scrape.ready(),
            generation_info=scrape.generation_info(),
            usage_queued=len(usage_recorder),
            usage_dropped=usage_recorder.dropped_count,
        )
        if not text.endswith("\n"):
            text += "\n"
        if sidecars is not None:
            text += sidecars.render_prometheus()
        if session_evictions is not None:
            constant = f'family="{registry.family}",platform="{registry.platform}"'
            text += (
                "# HELP foss_mcp_session_evictions_total Idle sessions evicted to admit a new one at the cap.\n"
                "# TYPE foss_mcp_session_evictions_total counter\n"
                f"foss_mcp_session_evictions_total{{{constant}}} {session_evictions()}\n"
            )
        return text

    async def metrics(request: Request) -> Response:
        if _wants_json(request):
            return JSONResponse(
                {
                    "usage_events_queued": len(usage_recorder),
                    "usage_events_dropped": usage_recorder.dropped_count,
                }
            )
        # Readiness reads the generation from disk, so the scrape runs off the event loop.
        text = await anyio.to_thread.run_sync(render)
        return Response(text, media_type=PROMETHEUS_CONTENT_TYPE)

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


def _serving_product_reference_inputs_without_known_hazards(
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

    G3/TC-582 (rework): named "without_known_hazards" because it never reads this deployment's own
    ``known_hazards`` (config/products/<family>/<platform>.yaml, never the sidecar) - that merge is
    ``_serving_product_reference_inputs``'s own job, applied uniformly to every path this returns,
    this function's ``None`` included. Keep reading this docstring at that wrapper, not here, for
    how ``known_hazards`` actually reaches the answer.
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


# G3/TC-582 (rework): a dependency-free, deliberately narrow reader for ONLY the OPTIONAL
# `known_hazards:` top-level block of config/products/<family>/<platform>.yaml
# (schemas/product/product-manifest.schema.json) - this project's own, already-committed, local
# identity config, read directly from _PRODUCT_MANIFESTS_ROOT at serve time: no sidecar, no network
# fetch. PyYAML is dev/ops-only (requirements-serving.in's own comment) and is NOT installed in the
# serving image this module ships in, so a real YAML parser cannot be imported here. This handles
# exactly the shape the schema allows for this one field - a block sequence of mappings, each with
# only a `text` and a `verified_at` string, `text` as a plain/quoted scalar or a `|`/`>` block
# scalar - and nothing more; it is not a general YAML implementation. Anything it does not
# recognise (a missing file, a key it cannot parse, a manifest with no such key - every pilot but
# cells/go, today) answers "no known_hazards" rather than guessing or raising: this merge is a
# bonus disclosure that must never be able to take serving down over its own config file's shape.
_KNOWN_HAZARDS_TOP_KEY_RE = re.compile(r"^known_hazards:[ \t]*$")
_YAML_DASH_ITEM_RE = re.compile(r"^([ \t]*)-[ \t]+(.*)$")
_YAML_KEY_VALUE_RE = re.compile(r"^([A-Za-z0-9_]+):[ \t]*(.*)$")
_YAML_BLOCK_SCALAR_HEADER_RE = re.compile(r"^([|>])[+-]?[ \t]*$")


def _dequote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _fold_yaml_block_scalar(block_lines: list[str], style: str) -> str:
    """*block_lines* (the raw, still-indented lines collected under a `|`/`>` block scalar
    header), dedented from the first non-blank line's own indentation and joined the way a real
    YAML parser would - `|` (literal) keeps every line break, `>` (folded) joins with a single
    space. Good enough for the plain hazard prose this narrow reader exists to read; blank lines
    are not expected inside one hazard's text and are dropped rather than turned into a paragraph
    break."""
    content_indent: int | None = None
    for raw in block_lines:
        if raw.strip():
            content_indent = len(raw) - len(raw.lstrip(" "))
            break
    if content_indent is None:
        return ""
    dedented = [raw[content_indent:] if len(raw) > content_indent else raw.lstrip(" ") for raw in block_lines]
    if style == "|":
        return "\n".join(dedented).rstrip("\n")
    return " ".join(line.strip() for line in dedented if line.strip())


def _known_hazard_entries_from_manifest_yaml_text(text: str) -> list[dict[str, str]]:
    """``[{"text": ..., "verified_at": ...}, ...]`` parsed from *text*'s top-level
    ``known_hazards:`` block, in file order - or ``[]`` when *text* carries no such key. Feeds
    ``known_hazards_from_manifest`` (which does the actual validation this function does not
    duplicate: a missing/blank ``text`` is its job to skip, not this one's)."""
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if _KNOWN_HAZARDS_TOP_KEY_RE.match(line):
            start = index + 1
            break
    if start is None:
        return []

    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    dash_indent: int | None = None
    field_indent: int | None = None
    block_key: str | None = None
    block_style = ">"
    block_lines: list[str] = []

    def close_block() -> None:
        nonlocal block_key, block_lines
        if block_key is not None and current is not None:
            current[block_key] = _fold_yaml_block_scalar(block_lines, block_style)
        block_key = None
        block_lines = []

    def handle_key_value(key: str, value: str) -> None:
        nonlocal block_key, block_style, block_lines
        header = _YAML_BLOCK_SCALAR_HEADER_RE.match(value.strip())
        if header is not None:
            block_key = key
            block_style = header.group(1)
            block_lines = []
        elif current is not None:
            current[key] = _dequote_yaml_scalar(value)

    index = start
    total = len(lines)
    while index < total:
        raw = lines[index]
        if raw.strip() == "":
            if block_key is not None:
                block_lines.append("")
            index += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))

        if block_key is not None and indent > (field_indent or 0):
            block_lines.append(raw)
            index += 1
            continue
        if block_key is not None:
            close_block()

        if indent == 0:
            break  # the next top-level manifest key - the known_hazards block has ended

        dash_match = _YAML_DASH_ITEM_RE.match(raw)
        if dash_match is not None and (dash_indent is None or indent <= dash_indent):
            if current is not None:
                entries.append(current)
            current = {}
            dash_indent = indent
            field_indent = None
            rest = dash_match.group(2)
            key_match = _YAML_KEY_VALUE_RE.match(rest)
            if key_match is not None:
                field_indent = len(raw) - len(rest)
                handle_key_value(key_match.group(1), key_match.group(2))
            index += 1
            continue

        key_match = _YAML_KEY_VALUE_RE.match(raw.strip())
        if key_match is not None and current is not None:
            if field_indent is None:
                field_indent = indent
            if indent == field_indent:
                handle_key_value(key_match.group(1), key_match.group(2))
        index += 1

    close_block()
    if current is not None:
        entries.append(current)
    return entries


def _known_hazards_for_deployment(deployment_config: DeploymentConfig) -> tuple[KnownHazard, ...]:
    """This deployment's own ``known_hazards`` (``()`` for every pilot but cells/go, today), read
    straight from its committed config/products/<family>/<platform>.yaml - independent local data,
    never the sidecar, never a network fetch. Any problem reading or parsing the file (absent,
    unreadable, no such key, a shape this narrow reader does not understand) answers ``()``, the
    same as a manifest that genuinely carries no ``known_hazards`` field; this bonus disclosure
    must never be able to raise and take serving down over its own config file."""
    path = _PRODUCT_MANIFESTS_ROOT / deployment_config.family / f"{deployment_config.platform}.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    try:
        entries = _known_hazard_entries_from_manifest_yaml_text(text)
    except Exception:  # noqa: BLE001 - a narrow, non-general parser; any surprise is "no hazards".
        LOG.warning(
            "known_hazards config unreadable, serving without it",
            extra={"family": deployment_config.family, "platform": deployment_config.platform},
        )
        return ()
    return known_hazards_from_manifest({"known_hazards": entries})


def _with_deployment_known_hazards(
    inputs: ProductReferenceInputs | None, deployment_config: DeploymentConfig
) -> ProductReferenceInputs | None:
    """*inputs* with this deployment's own ``known_hazards`` merged in - the ONE place that merge
    happens, applied uniformly to every path ``_serving_product_reference_inputs`` returns.

    ``known_hazards`` is independent local config, not something a missing or failed sidecar
    should be able to suppress, so a hazard-bearing deployment's answer is upgraded even where
    *inputs* is ``None`` (no sidecar, no failure marker) - a fresh ``ProductReferenceInputs``
    carrying the hazards is returned instead of bare ``None``. A deployment with no
    ``known_hazards`` (every pilot but cells/go, today) passes *inputs* through completely
    unchanged - ``None`` stays ``None``, and every existing fetch_failed/success path keeps its
    exact prior shape - since ``()`` merges to nothing either way."""
    hazards = _known_hazards_for_deployment(deployment_config)
    if not hazards:
        return inputs
    if inputs is None:
        return ProductReferenceInputs(platform=deployment_config.platform, known_hazards=hazards)
    return dataclasses.replace(inputs, known_hazards=hazards)


def _serving_product_reference_inputs(
    manifests_dir: Path, deployment_config: DeploymentConfig
) -> ProductReferenceInputs | None:
    """``_serving_product_reference_inputs_without_known_hazards`` (the sidecar-derived answer -
    see that function's own docstring for the full sidecar contract) with this deployment's own
    ``known_hazards`` (config/products/<family>/<platform>.yaml) merged into EVERY path it can
    return, via ``_with_deployment_known_hazards`` (G3/TC-582 rework): the ``None`` case, every
    ``fetch_failed`` placeholder, and the real sidecar-success case alike. ``known_hazards`` is
    local, already-committed config, independent of ingestion - it must reach the served
    "limitations" answer even for a pilot that has never successfully ingested."""
    raw = _serving_product_reference_inputs_without_known_hazards(manifests_dir, deployment_config)
    return _with_deployment_known_hazards(raw, deployment_config)


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


_SIDECAR_STATES = (SIDECAR_PRESENT, SIDECAR_NOT_PRESENT, SIDECAR_FETCH_FAILED)
# The sidecars whose ABSENCE is a fault: the ingestion Job always writes the product reference and the recent
# releases (an empty list when there are none), so a missing file means the data never reached this server.
# The package-registry verification is genuinely optional (not every pilot has a registry package), so it is
# counted in the metric but never warned about.
_REQUIRED_SIDECARS = (_KIND_PRODUCT_REFERENCE, _KIND_RECENT_RELEASES)


class SidecarWatcher:
    """The optional sidecars of one deployment, re-read when they change (G3/TC-564, RD3-120).

    ``build_app`` used to read the sidecars once, at boot: a file added later was ignored until a restart,
    and a file that was simply missing was served as an empty, "complete" answer with no log line and a green
    ``/readyz``. Now ``snapshot()`` (called on every tool call that reads a sidecar) compares the size and
    modification time of the three files and their ``.failed`` markers with the last reading and re-resolves
    them only when something changed, so a sidecar copied in later is served on the NEXT request.

    While ``sidecar_state.SIDECAR_STATE_WARNINGS`` is on, a required sidecar (product reference, recent
    releases) that is not present is logged at WARNING when first seen (naming the file), appears in
    ``warnings()`` (``/readyz`` carries it) and is counted by ``render_prometheus()``. With the switch off none
    of that happens and the server behaves exactly as it did before the card. Thread safe."""

    def __init__(self, manifests_dir: Path, deployment_config: DeploymentConfig) -> None:
        self._dir = manifests_dir
        self._config = deployment_config
        family, platform = deployment_config.family, deployment_config.platform
        self._paths = {
            _KIND_PRODUCT_REFERENCE: manifests_dir / sidecar_name(family, platform),
            _KIND_RECENT_RELEASES: manifests_dir / recent_releases_sidecar_name(family, platform),
            _KIND_PACKAGE_REGISTRY: manifests_dir / verify_package_registry_sidecar_name(family, platform),
        }
        self._lock = threading.Lock()
        self._signature: tuple[tuple[int, int] | None, ...] | None = None
        self._snapshot = SidecarSnapshot(ProductReferenceInputs())
        self._states = dict.fromkeys(self._paths, SIDECAR_NOT_PRESENT)
        self._reported: dict[str, str] = {}
        self.snapshot()

    def _signature_of(self) -> tuple[tuple[int, int] | None, ...]:
        parts: list[tuple[int, int] | None] = []
        for path in self._paths.values():
            for candidate in (path, path.with_name(path.name + _FAILURE_MARKER_SUFFIX)):
                try:
                    stat = candidate.stat()
                except OSError:
                    parts.append(None)
                else:
                    parts.append((stat.st_mtime_ns, stat.st_size))
        return tuple(parts)

    def snapshot(self) -> SidecarSnapshot:
        """The current sidecar data; the files are re-resolved only when one of them changed."""
        signature = self._signature_of()
        with self._lock:
            if signature != self._signature:
                self._signature = signature
                try:
                    self._reload()
                except Exception as exc:  # noqa: BLE001 - a bad reload keeps the last good snapshot.
                    LOG.warning(
                        "sidecar reload failed, serving the last good sidecars",
                        extra={
                            "error": type(exc).__name__,
                            "family": self._config.family,
                            "platform": self._config.platform,
                        },
                    )
            return self._snapshot

    def _reload(self) -> None:
        inputs = _serving_product_reference_inputs(self._dir, self._config)
        releases, releases_state = _resolve_recent_releases(
            self._dir,
            self._config,
            source_repository=inputs.source_repository if inputs else None,
        )
        registry_state, registry_payload = _read_sidecar_json(self._paths[_KIND_PACKAGE_REGISTRY])
        if registry_state == SIDECAR_PRESENT and not (
            isinstance(registry_payload, dict)
            and isinstance(registry_payload.get("verified"), bool)
            and isinstance(registry_payload.get("coordinate"), str)
            and isinstance(registry_payload.get("checked_at"), str)
        ):
            registry_state = SIDECAR_FETCH_FAILED
        self._states = {
            _KIND_PRODUCT_REFERENCE: SIDECAR_NOT_PRESENT if inputs is None else inputs.sidecar_state,
            _KIND_RECENT_RELEASES: releases_state,
            _KIND_PACKAGE_REGISTRY: registry_state,
        }
        self._snapshot = SidecarSnapshot(inputs or ProductReferenceInputs(), releases, releases_state)
        self._report_changes()

    def _report_changes(self) -> None:
        """One WARNING when a required sidecar is first seen missing, one INFO when it appears later."""
        if not sidecar_state.SIDECAR_STATE_WARNINGS:
            return
        for kind in _REQUIRED_SIDECARS:
            state = self._states[kind]
            previous = self._reported.get(kind)
            self._reported[kind] = state
            extra = {
                "sidecar": kind,
                "file": self._paths[kind].name,
                "family": self._config.family,
                "platform": self._config.platform,
            }
            if state == SIDECAR_NOT_PRESENT and previous != SIDECAR_NOT_PRESENT:
                LOG.warning("sidecar not loaded, the file was not found", extra=extra)
            elif previous == SIDECAR_NOT_PRESENT and state != SIDECAR_NOT_PRESENT:
                LOG.info("sidecar loaded after startup", extra={**extra, "state": state})

    def states(self) -> dict[str, str]:
        """``{sidecar kind: present | not_present | fetch_failed}`` as last read."""
        self.snapshot()
        with self._lock:
            return dict(self._states)

    def warnings(self) -> tuple[str, ...]:
        """The ``/readyz`` ``warnings`` entries for required sidecars that are not on the server. Empty while
        ``SIDECAR_STATE_WARNINGS`` is off."""
        if not sidecar_state.SIDECAR_STATE_WARNINGS:
            return ()
        states = self.states()
        return tuple(
            f"{kind} sidecar not loaded: {self._paths[kind].name} was not found"
            for kind in _REQUIRED_SIDECARS
            if states[kind] == SIDECAR_NOT_PRESENT
        )

    def render_prometheus(self) -> str:
        """The sidecar-state series for ``/metrics``: one 0/1 gauge per sidecar and state, and a count of the
        sidecars in each state. Empty while ``SIDECAR_STATE_WARNINGS`` is off."""
        if not sidecar_state.SIDECAR_STATE_WARNINGS:
            return ""
        states = self.states()
        constant = f'family="{self._config.family}",platform="{self._config.platform}"'
        lines = [
            "# HELP foss_mcp_sidecar_state Whether each optional sidecar file is in the given state (1) or not (0).",
            "# TYPE foss_mcp_sidecar_state gauge",
        ]
        for kind in self._paths:
            for state in _SIDECAR_STATES:
                lines.append(
                    f'foss_mcp_sidecar_state{{{constant},sidecar="{kind}",state="{state}"}} '
                    f"{1 if states[kind] == state else 0}"
                )
        lines += [
            "# HELP foss_mcp_sidecars How many optional sidecar files are in each state.",
            "# TYPE foss_mcp_sidecars gauge",
        ]
        for state in _SIDECAR_STATES:
            count = sum(1 for kind in self._paths if states[kind] == state)
            lines.append(f'foss_mcp_sidecars{{{constant},state="{state}"}} {count}')
        return "\n".join(lines) + "\n"


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
    allowed_hosts: list[str] | None = None,
    session_evict_min_idle: float | None = None,
    body_timeout: float | None = None,
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

    G3/TC-564 (RD3-120, 124, 125). The sidecars are read through ONE ``SidecarWatcher`` that re-resolves
    them when a file changes (a sidecar added later is served on the next request) and reports a missing
    required one (WARNING at boot, a ``/readyz`` warning, a ``/metrics`` series, an ``unavailable`` answer from
    ``list_recent_changes``). ``allowed_hosts`` (default: ``FOSS_MCP_ALLOWED_HOSTS``, the container name
    and the machine host name) extends the Host allow-list of ``transport_security.host_is_allowed``;
    ``session_evict_min_idle`` (default ``FOSS_MCP_SESSION_EVICT_MIN_IDLE_SECONDS``, else 10) is how long a
    session must have been idle to be evicted at the cap; ``body_timeout`` (default
    ``FOSS_MCP_BODY_TIMEOUT_SECONDS``, else 30) bounds each read of a request body. Stack, outermost first:
    rejection (Origin, Host, protocol version) - error sanitising - cap observer - the SDK's Starlette app,
    whose own middleware is, outermost first: client-disconnect absorber - body read timeout - request-id
    check - idle eviction.
    """
    install_client_disconnect_log_filter()
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
    # Neither sidecar can stop the server starting (G3/TC-507): a bad one degrades to fetch_failed. One
    # watcher serves them to every tool call and re-reads a file that changes (G3/TC-564).
    sidecars = SidecarWatcher(resolved_manifests_dir, deployment_config)
    readyz_route = _readyz_route(deployment_config, resolved_store, sidecars)
    startup_generation_store = DeploymentGenerationStore.for_scope(
        resolved_store, resolve_scope(deployment_config, request=None), deployment_config.source_kind
    )
    _log_example_search_mode(startup_generation_store, deployment_config)
    _log_startup_readiness(startup_generation_store, deployment_config)
    server = create_server(
        deployment_config,
        resolved_store,
        usage_recorder=resolved_usage_recorder,
        metrics=resolved_metrics,
        sidecars=sidecars.snapshot,
    )
    scrape = _MetricsScrape(server, startup_generation_store, deployment_config)
    on_reject = _session_cap_rejection_handler(
        resolved_metrics, deployment_config, scrape, max_sessions=resolved_max_sessions
    )
    allowed_origins = allowed_origins_from_env() if allowed_origins is None else allowed_origins
    resolved_hosts = allowed_hosts_from_env() if allowed_hosts is None else allowed_hosts
    resolved_evict_idle = (
        session_evict_min_idle if session_evict_min_idle is not None else session_evict_min_idle_seconds()
    )
    resolved_body_timeout = body_timeout if body_timeout is not None else body_timeout_seconds()
    evictions = _EvictionCounter()
    inner = server.streamable_http_app(
        session_idle_timeout=resolved_idle_timeout,
        max_sessions=resolved_max_sessions,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        custom_starlette_routes=[
            _healthz_route(),
            readyz_route,
            _metrics_route(resolved_usage_recorder, resolved_metrics, scrape, sidecars, evictions.value),
        ],
    )
    # A trailing-slash path is a 404, never a redirect built from the client's Host header (TC-544).
    inner.router.redirect_slashes = False
    # G3/TC-564: the guards run INSIDE the Starlette app (around its router), so they see exactly the requests the
    # SDK sees and the layers outside it are unchanged. Starlette makes the last one added the outermost:
    # client-disconnect absorber, body read timeout, request-id check, idle-session eviction, the SDK.
    inner.add_middleware(
        SessionEvictionMiddleware,
        get_manager=lambda: server.session_manager,
        max_sessions=resolved_max_sessions,
        min_idle_seconds=resolved_evict_idle,
        deployment_config=deployment_config,
        counter=evictions,
    )
    inner.add_middleware(RequestIdMiddleware)
    inner.add_middleware(
        ReadTimeoutMiddleware, timeout_seconds=resolved_body_timeout, deployment_config=deployment_config
    )
    inner.add_middleware(ClientDisconnectMiddleware, deployment_config=deployment_config)
    return RejectionMiddleware(
        ErrorSanitisingMiddleware(SessionCapObserverMiddleware(inner, on_reject)),
        allowed_origins,
        exempt_paths=frozenset({HEALTHZ_PATH, READYZ_PATH, METRICS_PATH}),
        allowed_hosts=resolved_hosts,
        deployment_config=deployment_config,
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


def header_timeout_protocol(timeout_seconds: float) -> type[Any]:
    """An uvicorn HTTP protocol class that closes a connection whose request line and headers do not
    complete within *timeout_seconds* (G3/TC-564, RD3-125).

    uvicorn arms a timer only for an IDLE keep-alive connection, and cancels it at the first received byte, so
    a client that connects and sends nothing, or one byte every few seconds, held its connection for ever.
    Here a timer starts when the connection opens (and again after each response, for the next request on a
    keep-alive connection) and is cancelled only when a complete request head has been parsed. A request that
    is being processed, a long-lived stream included, is never affected; a stalled BODY is
    ``ReadTimeoutMiddleware``'s job. Built lazily: it needs uvicorn, which only the server process imports.
    """
    from uvicorn.protocols.http.h11_impl import H11Protocol

    class HeaderTimeoutProtocol(H11Protocol):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._header_timer: asyncio.TimerHandle | None = None
            self._header_cycle: Any = None

        def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
            super().connection_made(transport)
            self._arm_header_timer()

        def connection_lost(self, exc: Exception | None) -> None:
            self._cancel_header_timer()
            super().connection_lost(exc)

        def data_received(self, data: bytes) -> None:
            super().data_received(data)
            if (
                self._header_timer is not None
                and self.cycle is not None
                and self.cycle is not self._header_cycle
            ):
                # A request head arrived whole: the timer's job is done until the next request.
                self._header_cycle = self.cycle
                self._cancel_header_timer()

        def on_response_complete(self) -> None:
            super().on_response_complete()
            if not self.transport.is_closing():
                self._arm_header_timer()

        def _arm_header_timer(self) -> None:
            self._cancel_header_timer()
            self._header_timer = self.loop.call_later(timeout_seconds, self._header_timeout_expired)

        def _cancel_header_timer(self) -> None:
            if self._header_timer is not None:
                self._header_timer.cancel()
                self._header_timer = None

        def _header_timeout_expired(self) -> None:
            self._header_timer = None
            if self.transport.is_closing():
                return
            LOG.info("connection closed: the request head did not arrive in time")
            self.transport.close()

    return HeaderTimeoutProtocol


def uvicorn_config(
    app: ASGIApp,
    *,
    host: str,
    port: int,
    grace_seconds: int,
    header_timeout: float,
) -> Any:
    """A ``uvicorn.Config`` with the same keyword arguments ``main`` passes to ``uvicorn.run``:
    ``log_config=None`` (uvicorn must not install its own dictConfig over ``configure_logging()``, or its
    lines would bypass the JSON formatter), the graceful shutdown window, and the stalled-header protocol
    (``header_timeout_protocol``). Tests use it to start the real server on an ephemeral port."""
    import uvicorn

    return uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        timeout_graceful_shutdown=grace_seconds,
        http=header_timeout_protocol(header_timeout),
    )


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
    for variable, resolver, expected in (
        (SESSION_EVICT_MIN_IDLE_ENV, session_evict_min_idle_seconds, "a whole number of seconds, 0 or more"),
        (HEADER_TIMEOUT_ENV, header_timeout_seconds, "a whole number of seconds, 1 or more"),
        (BODY_TIMEOUT_ENV, body_timeout_seconds, "a whole number of seconds, 1 or more"),
    ):
        try:
            resolver()
        except ValueError:
            _exit_invalid_setting(variable, f"expected {expected}")
    # log_config=None: uvicorn must not install its own dictConfig over configure_logging(),
    # or its access/error lines would bypass the JSON formatter and go to stderr as plain text.
    # http=: the protocol that also times out a request head that never completes (G3/TC-564).
    uvicorn.run(
        build_app(config, session_idle_timeout=idle_timeout, max_session_count=session_cap),
        host="0.0.0.0",
        port=DEFAULT_PORT,
        log_config=None,
        timeout_graceful_shutdown=grace,
        http=header_timeout_protocol(header_timeout_seconds()),
    )


if __name__ == "__main__":
    main()
