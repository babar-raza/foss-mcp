#!/usr/bin/env python3
"""Attack the exact Docker image that would ship, the way a peer who wants it to fail will (TC-355).

Usage:
    python scripts/release/adversarial_suite.py --image NAME:TAG [--pilot FAMILY/PLATFORM ...]
        [--other-pilot FAMILY/PLATFORM] [--fixtures DIR] [--report-dir DIR] [--memory 256m] [--cpus 0.5]
        [--ready-timeout 120] [--url URL] [--dry-run]

Requirement met here: REQ-G3-006 (adversarial robustness). The happy path is proved by
scripts/release/image_workflow_gate.py (TC-345); nothing there attacks a running container. This suite does.

For every selected pilot (default: three with different languages, chosen from the chart) the suite starts ONE
container exactly as a peer does, by reusing the helpers of image_workflow_gate (never copying them), and runs six
named, independent scenarios against its real MCP endpoint:

  protocol     invalid JSON, a batch, an unknown method, a missing or wrong Content-Type, a 5 MB body, a wrong
               Origin, an unsupported protocol-version header, a request with no session: a 4xx or a JSON-RPC
               error object, never a 5xx, never a hang over 10 s, never a stack trace in the body;
  arguments    for every tool and every argument that tools/list declares: hostile strings, hostile integers and
               wrong types; a structured tool error or an honest empty answer within 5 s, never exception text;
  isolation    a symbol that exists only in another pilot's fixture, asked with and without identity-looking
               arguments (family, platform, scope, source_kind, generation_id): none of it may come back;
  disclosure   every error body gathered above: no container filesystem path, no environment variable value, no
               stack trace;
  concurrency  20 parallel MCP sessions of 10 mixed valid calls each: zero errors, p95 within max(2 s, 8 times
               the median of a sequential warm-up pass on the same container), and the container's memory
               within 25 percent of what it was before;
  restart      ``docker restart`` the container: the same generation id and a known symbol still resolves.

After EVERY scenario the suite re-checks /readyz, and a scenario that leaves the server unready fails. The
container is ALWAYS removed, on success, failure, timeout or Ctrl-C.

Each scenario is a function that returns a list of violations (empty means it passed). A violation carries a case
name, the problem and a short excerpt of the evidence, never a secret: environment values read from the container
(for scanning only) and the optional gateway variables are removed from everything printed or written.

Report: ``<family>_<platform>.json`` per pilot plus ``summary.json`` in --report-dir (schema_version 1, image,
image_id, generated_at, per-scenario status and violations, verdict PASS or FAIL). Exit 0 only on PASS; 1 on FAIL
(or after an interrupt: 130); 2 when the request was refused before anything ran.

``--url URL`` attacks an already-running endpoint instead of starting a container. It needs exactly one --pilot
(the identity that endpoint serves); the restart scenario and the memory half of the concurrency scenario need a
container, so they are skipped and the report says so.

The MCP client here talks plain HTTP on purpose: the attacks need control over bytes, headers and status codes
that tests/e2e's _McpSession (which asserts HTTP 200 and keeps its base URL in a module global) cannot give, and
twenty sessions at once need per-session state.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shlex
import statistics
import sys
import tempfile
import time
import urllib.parse
import uuid
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_RELEASE_DIR = Path(__file__).resolve().parent
if str(_RELEASE_DIR) not in sys.path:
    # scripts/release is not a package; the TC-345 gate owns container start/stop, ports and readiness.
    sys.path.insert(0, str(_RELEASE_DIR))

import image_workflow_gate as gate  # noqa: E402

chart = gate.chart
REPO_ROOT = gate.REPO_ROOT

SCHEMA_VERSION = 1
SUMMARY_NAME = "summary.json"
DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / "foss-mcp-adversarial-suite"
# Three pilots in three different languages (.NET, Python, Rust), all declared by the chart.
DEFAULT_PILOTS = ("pdf/net", "slides/python", "cells/rust")
PILOT_PATTERN = re.compile(r"[a-z0-9]+/[a-z0-9]+")

MCP_PATH = "/mcp"
READY_PATH = gate.READY_PATH
PROTOCOL_VERSION = "2025-06-18"
SESSION_HEADER = "Mcp-Session-Id"
EVIL_ORIGIN = "https://evil.example.test"

EXIT_FAILED = gate.EXIT_FAILED
EXIT_REFUSED = gate.EXIT_REFUSED
EXIT_INTERRUPTED = gate.EXIT_INTERRUPTED

EXCERPT_WIDTH = 160
MAX_STORED_BODY = 1_200_000  # what is kept as evidence; a reply is parsed in full BEFORE it is capped
MAX_REPLY_BYTES = (
    16 << 20
)  # the hard ceiling on a reply read into memory; a bigger one is a violation, not parsed
MAX_VIOLATIONS_PER_SCENARIO = 40
MIN_SECRET_LENGTH = 12
RESTART_TIMEOUT_S = 120.0

SCENARIO_NAMES = ("protocol", "arguments", "isolation", "disclosure", "concurrency", "restart")


@dataclass(frozen=True)
class Limits:
    """Every number the card fixes. The tests lower them; the command line does not expose them."""

    protocol_timeout_s: float = 10.0
    tool_timeout_s: float = 5.0
    p95_s: float = 2.0  # the floor of the latency rule: p95 <= max(p95_s, baseline_factor * warm-up median)
    baseline_factor: float = 8.0
    memory_growth: float = 0.25
    ready_recheck_s: float = 10.0
    sessions: int = 20
    calls_per_session: int = 10
    big_string_bytes: int = 1 << 20
    big_body_bytes: int = 5 << 20
    nesting: int = 5000


class Refused(gate.Refused):
    """The request cannot be run as asked. Nothing has been started; the exit code is 2."""


class Skipped(Exception):
    """A scenario that cannot apply to this target. Recorded as skipped, with the reason."""


class SessionError(Exception):
    """An MCP session could not be opened."""


# ---------------------------------------------------------------------------------------------------------------
# Violations, and what counts as leaking
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Violation:
    scenario: str
    case: str
    problem: str
    evidence: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"case": self.case, "problem": self.problem, "evidence": self.evidence}


# A path or value from inside the container. /app/ is the image's code, /data/ its baked manifests.
LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("container filesystem path /app/", re.compile(r"/app/")),
    ("container filesystem path /data/", re.compile(r"/data/")),
    ("Windows drive path", re.compile(r"[A-Za-z]:\\{1,2}")),
    ("contents of /etc/passwd", re.compile(r"root:[x*!]?:0:0")),
    ("contents of win.ini", re.compile(r"for 16-bit app support", re.IGNORECASE)),
)

# Exception text a client must never see. The frame and name rules are the supporting evidence for the one that
# names the interpreter's own stack-trace header.
_EXCEPTION_NAMES = (
    "KeyError|ValueError|TypeError|AttributeError|IndexError|RuntimeError|UnicodeEncodeError|UnicodeDecodeError|"
    "RecursionError|MemoryError|OSError|ZeroDivisionError|OverflowError|AssertionError|NameError|ImportError|"
    "JSONDecodeError"
)
STACK_FRAME_PATTERN = re.compile(r'File "[^"]+", line \d+')
EXCEPTION_NAME_PATTERN = re.compile(rf"\b(?:{_EXCEPTION_NAMES})\b")
STACK_TRACE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("python stack frame", STACK_FRAME_PATTERN),
    ("python stack trace", re.compile(r"Traceback")),
)


def unhandled_findings(text: str, *, is_error: bool) -> list[str]:
    """Names of the exception-text rules that match. Exception class names count only inside an error body."""
    found = [name for name, pattern in STACK_TRACE_PATTERNS if pattern.search(text)]
    if is_error and EXCEPTION_NAME_PATTERN.search(text):
        found.append("python exception name")
    return found


def secret_values(environ: Mapping[str, str]) -> list[str]:
    """The optional gateway variables' values, read only so they can be removed from anything printed."""
    return [value for value in gate.gateway_values(environ) if value]


@dataclass
class ErrorBody:
    case: str
    status: int | None
    text: str


# ---------------------------------------------------------------------------------------------------------------
# HTTP and the MCP session
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Reply:
    status: int | None
    headers: dict[str, str]
    body: str
    elapsed: float
    error: str | None = None
    oversize: bool = False  # the reply was larger than MAX_REPLY_BYTES and was not read to the end


def http_request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> Reply:
    """One request with full control of bytes and headers. A failure is a Reply with ``error``, never a raise.

    The whole reply is kept (up to MAX_REPLY_BYTES) so it can be parsed before anything is truncated: an honest
    answer may be larger than the evidence cap because it echoes the request back. Only what is stored as
    evidence is capped, later. A reply past the ceiling is not parsed; it comes back as an error."""
    started = time.monotonic()
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    sent = dict(headers or {})
    sent["Connection"] = "close"
    connection = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port or 80, timeout=timeout)
    try:
        try:
            connection.request(method, path, body=body, headers=sent)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # a server may answer and close before it has read a huge body; its answer is still readable
        response = connection.getresponse()
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_REPLY_BYTES:
                return Reply(
                    int(response.status),
                    {},
                    "",
                    time.monotonic() - started,
                    f"reply larger than the {MAX_REPLY_BYTES >> 20} MB ceiling",
                    oversize=True,
                )
            chunks.append(chunk)
            if time.monotonic() - started > timeout:
                return Reply(
                    None, {}, "", time.monotonic() - started, f"no complete reply within {timeout:g} s"
                )
        text = b"".join(chunks).decode("utf-8", "replace")
        reply_headers = {key.lower(): value for key, value in response.getheaders()}
        return Reply(int(response.status), reply_headers, text, time.monotonic() - started)
    except TimeoutError:
        return Reply(None, {}, "", time.monotonic() - started, f"no reply within {timeout:g} s")
    except (OSError, http.client.HTTPException) as exc:
        return Reply(None, {}, "", time.monotonic() - started, f"{type(exc).__name__}: no usable reply")
    finally:
        connection.close()


def parse_message(text: str) -> Any:
    """The JSON a reply carries: a plain JSON body, or the first ``data:`` line of an event stream. None if neither."""
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[len("data:") :].strip())
            except ValueError:
                continue
    return None


def has_rpc_error(message: Any) -> bool:
    """True when the message is a JSON-RPC error object, or a batch in which every answer is one."""
    if isinstance(message, dict):
        return isinstance(message.get("error"), dict)
    if isinstance(message, list):
        return bool(message) and all(
            isinstance(item, dict) and isinstance(item.get("error"), dict) for item in message
        )
    return False


@dataclass
class Outcome:
    """What a tools/call came back as."""

    kind: str  # ok, tool_error, rpc_error, http_4xx, http_5xx, http_other, unparseable, transport
    status: int | None
    elapsed: float
    text: str = ""
    value: Any = None
    detail: str = ""

    @property
    def is_error_body(self) -> bool:
        return self.kind != "ok"


def classify(reply: Reply) -> Outcome:
    if reply.error is not None:
        return Outcome("transport", reply.status, reply.elapsed, detail=reply.error)
    status = reply.status or 0
    if status >= 500:
        return Outcome(
            "http_5xx", status, reply.elapsed, reply.body[:MAX_STORED_BODY], detail=f"HTTP {status}"
        )
    if 400 <= status < 500:
        return Outcome(
            "http_4xx", status, reply.elapsed, reply.body[:MAX_STORED_BODY], detail=f"HTTP {status}"
        )
    if not 200 <= status < 300:
        return Outcome(
            "http_other", status, reply.elapsed, reply.body[:MAX_STORED_BODY], detail=f"HTTP {status}"
        )
    message = parse_message(reply.body)  # the complete text: parse first, cap what is stored afterwards
    stored = reply.body[:MAX_STORED_BODY]
    if not isinstance(message, dict):
        return Outcome("unparseable", status, reply.elapsed, stored, detail="reply is not a JSON-RPC message")
    if isinstance(message.get("error"), dict):
        return Outcome("rpc_error", status, reply.elapsed, stored, detail="JSON-RPC error")
    result = message.get("result")
    if isinstance(result, dict) and result.get("isError"):
        return Outcome("tool_error", status, reply.elapsed, stored, detail="tool error")
    value: Any = result
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), dict):
        structured = result["structuredContent"]
        value = structured.get("result", structured)
    return Outcome("ok", status, reply.elapsed, stored, value=value)


class McpSession:
    """One MCP session over Streamable HTTP: initialize, acknowledge, then any request the caller needs."""

    def __init__(self, base_url: str, limits: Limits) -> None:
        self.base_url = base_url.rstrip("/")
        self.limits = limits
        self.session_id: str | None = None
        self._next_id = 1

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return headers

    def open(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "foss-mcp-adversarial-suite", "version": "1"},
            },
        }
        reply = http_request(
            "POST",
            f"{self.base_url}{MCP_PATH}",
            body=json.dumps(body).encode("utf-8"),
            headers=self.headers,
            timeout=self.limits.protocol_timeout_s,
        )
        if reply.error is not None or reply.status != 200:
            raise SessionError(f"initialize failed ({reply.error or f'HTTP {reply.status}'})")
        self.session_id = reply.headers.get(SESSION_HEADER.lower())
        http_request(
            "POST",
            f"{self.base_url}{MCP_PATH}",
            body=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8"),
            headers=self.headers,
            timeout=self.limits.protocol_timeout_s,
        )

    def post(
        self, body: bytes, headers: Mapping[str, str] | None = None, timeout: float | None = None
    ) -> Reply:
        return http_request(
            "POST",
            f"{self.base_url}{MCP_PATH}",
            body=body,
            headers=self.headers if headers is None else headers,
            timeout=timeout or self.limits.protocol_timeout_s,
        )

    def rpc(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Outcome:
        request_id = self._next_id
        self._next_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        return classify(self.post(body.encode("utf-8"), timeout=timeout))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Outcome:
        """tools/call. The abort deadline is twice the allowed latency, so a slow answer is measured, not cut off."""
        return self.rpc(
            "tools/call", {"name": name, "arguments": arguments}, timeout=self.limits.tool_timeout_s * 2
        )

    def list_tools(self) -> dict[str, dict[str, Any]]:
        outcome = self.rpc("tools/list")
        tools = (
            outcome.value.get("tools") if outcome.kind == "ok" and isinstance(outcome.value, dict) else None
        )
        if not isinstance(tools, list):
            raise SessionError(f"tools/list failed ({outcome.kind}: {outcome.detail})")
        return {
            str(tool["name"]): tool.get("inputSchema") or {}
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }


# ---------------------------------------------------------------------------------------------------------------
# Tool schemas and the arguments built from them
# ---------------------------------------------------------------------------------------------------------------


def schema_kinds(prop: Mapping[str, Any]) -> set[str]:
    """The JSON types a property accepts, looking through anyOf, oneOf and a type list."""
    kinds: set[str] = set()
    declared = prop.get("type")
    if isinstance(declared, str):
        kinds.add(declared)
    elif isinstance(declared, list):
        kinds.update(str(item) for item in declared)
    for key in ("anyOf", "oneOf"):
        for branch in prop.get(key) or []:
            if isinstance(branch, Mapping):
                kinds |= schema_kinds(branch)
    return kinds - {"null"}


def _first_enum(prop: Mapping[str, Any]) -> Any:
    if prop.get("enum"):
        return prop["enum"][0]
    for key in ("anyOf", "oneOf"):
        for branch in prop.get(key) or []:
            if isinstance(branch, Mapping) and branch.get("enum"):
                return branch["enum"][0]
    return None


SAMPLE_STRINGS = {
    "content_type": "getting_started",
    "section": "license",
    "source_kind": "self_extracted",
}


def sample_value(name: str, prop: Mapping[str, Any], symbol: str, fqn: str) -> Any:
    enum = _first_enum(prop)
    if enum is not None:
        return enum
    kinds = schema_kinds(prop)
    if "integer" in kinds:
        return 5
    if "number" in kinds:
        return 1.0
    if "boolean" in kinds:
        return False
    if name == "fqn":
        return fqn
    return SAMPLE_STRINGS.get(name, symbol)


def valid_arguments(schema: Mapping[str, Any], symbol: str, fqn: str) -> dict[str, Any]:
    """Only the required arguments, each with a plausible value: the baseline every hostile case starts from."""
    properties = schema.get("properties") or {}
    return {
        name: sample_value(name, properties.get(name) or {}, symbol, fqn)
        for name in schema.get("required") or []
    }


def string_payloads(limits: Limits) -> list[tuple[str, str]]:
    return [
        ("1 MB string", "A" * limits.big_string_bytes),
        ("NUL byte", "abc\x00def"),
        ("unpaired surrogate", "ab\ud800cd"),
        ("right-to-left override", "‮evil‬‭text‏؜"),
        ("5000 nested brackets", "[" * limits.nesting + "]" * limits.nesting),
        ("regex bomb pattern", "(a+)+$"),
        ("regex bomb subject", "a" * 5000 + "!"),
        ("path traversal posix", "../../etc/passwd"),
        ("path traversal windows", "..\\..\\windows\\win.ini"),
        ("shell metacharacters", "; rm -rf / $(id) `id` | cat /etc/passwd && echo pwned > /tmp/x"),
        ("sql metacharacters", "' OR 1=1; DROP TABLE users; -- \" ) UNION SELECT NULL--"),
    ]


INTEGER_PAYLOADS: tuple[tuple[str, Any], ...] = (
    ("zero", 0),
    ("minus one", -1),
    ("2**31", 2**31),
    ("2**63", 2**63),
    ("float", 1.5),
)

WRONG_TYPES: dict[str, tuple[tuple[str, Any], ...]] = {
    "string": (
        ("integer", 123),
        ("float", 1.5),
        ("boolean", True),
        ("null", None),
        ("array", ["x"]),
        ("object", {"a": 1}),
    ),
    "integer": (
        ("string", "abc"),
        ("numeric string", "7"),
        ("null", None),
        ("array", [1]),
        ("object", {"a": 1}),
    ),
    "other": (("string", "abc"), ("integer", 123), ("array", [1]), ("object", {"a": 1})),
}


def argument_kind(prop: Mapping[str, Any]) -> str:
    kinds = schema_kinds(prop)
    if "string" in kinds:
        return "string"
    if "integer" in kinds or "number" in kinds:
        return "integer"
    return "other"


# ---------------------------------------------------------------------------------------------------------------
# The target, and small text helpers
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class OtherPilot:
    family: str
    platform: str
    symbol: str


def elapsed_of(outcome: Outcome) -> float:
    """The latency the concurrency scenario judges: the measured round trip. Tests replace it with exact numbers."""
    return outcome.elapsed


@dataclass
class Target:
    """Everything a scenario needs about the one endpoint under attack. ``base_url`` changes after a restart."""

    base_url: str
    family: str
    platform: str
    symbol: str
    other: OtherPilot | None = None
    limits: Limits = field(default_factory=Limits)
    deps: gate.Deps = field(default_factory=gate.Deps)
    container: str | None = None
    gate_ctx: Any = None  # a gate.Context when a container is running; None when attacking a --url endpoint
    env_secrets: dict[str, str] = field(default_factory=dict)  # NAME -> value, read only to scan for leaks
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    fqn: str = ""
    generation_id: str = ""
    error_bodies: list[ErrorBody] = field(default_factory=list)
    notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    redact: gate.Redactor = field(default_factory=lambda: gate.Redactor([]))
    # The seam for the latency rule: maps a finished call to the seconds it is judged by. Production uses the
    # measured round trip; tests inject a deterministic function so the arithmetic is asserted, never real timing.
    latency_of: Callable[[Outcome], float] = elapsed_of

    @property
    def key(self) -> str:
        return f"{self.family}/{self.platform}"

    def scrub(self, text: str) -> str:
        for value in self.env_secrets.values():
            if value:
                text = text.replace(value, "[redacted]")
        return self.redact.text(text)

    def excerpt(self, text: str, around: str | None = None) -> str:
        flat = " ".join(text.split())
        start = 0
        if around:
            index = flat.find(around)
            if index >= 0:
                start = max(0, index - 60)
        return gate.ascii_safe(self.scrub(flat[start : start + EXCERPT_WIDTH]))

    def record_error(self, case: str, outcome_or_reply: Outcome | Reply) -> None:
        status = outcome_or_reply.status
        text = outcome_or_reply.text if isinstance(outcome_or_reply, Outcome) else outcome_or_reply.body
        if text:
            self.error_bodies.append(ErrorBody(case, status, text[:MAX_STORED_BODY]))

    def open_session(self) -> McpSession:
        session = McpSession(self.base_url, self.limits)
        session.open()
        return session

    def valid_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (name, valid_arguments(schema, self.symbol, self.fqn or self.symbol))
            for name, schema in sorted(self.schemas.items())
        ]


_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_FINAL_SEGMENT = re.compile(r"\.|::")


def final_segment(fqn: str) -> str:
    return _FINAL_SEGMENT.split(fqn.strip())[-1]


# ---------------------------------------------------------------------------------------------------------------
# Readiness between scenarios, and memory
# ---------------------------------------------------------------------------------------------------------------


def ready_status(target: Target) -> int | None:
    http_get = target.deps.http_get or gate.http_status
    return http_get(f"{target.base_url}{READY_PATH}", gate.HTTP_TIMEOUT_S)


def recheck_ready(target: Target) -> str | None:
    """None while /readyz answers 200 (polled for up to ready_recheck_s); otherwise why it did not."""
    clock = target.deps.clock or time.monotonic
    sleep = target.deps.sleep or time.sleep
    deadline = clock() + target.limits.ready_recheck_s
    while True:
        status = ready_status(target)
        if status == 200:
            return None
        last = f"HTTP {status}" if status is not None else "no connection"
        if clock() >= deadline:
            return f"{READY_PATH} did not return 200 within {target.limits.ready_recheck_s:g} s after the scenario ({last})"
        sleep(0.5)


def sample_memory_mib(target: Target) -> float | None:
    if target.container is None or target.gate_ctx is None:
        return None
    return gate.sample_memory(target.gate_ctx, target.container)


# ---------------------------------------------------------------------------------------------------------------
# Scenario: protocol
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class ProtocolCase:
    name: str
    body: bytes
    headers: dict[str, str]


def _rpc_bytes(method: str, request_id: int = 1, params: dict[str, Any] | None = None) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}).encode(
        "utf-8"
    )


def protocol_cases(session: McpSession, limits: Limits) -> list[ProtocolCase]:
    good = session.headers
    no_content_type = {key: value for key, value in good.items() if key.lower() != "content-type"}
    no_accept = {key: value for key, value in good.items() if key.lower() != "accept"}
    no_session = {key: value for key, value in good.items() if key.lower() != SESSION_HEADER.lower()}
    listing = _rpc_bytes("tools/list")
    padding = "x" * limits.big_body_bytes
    return [
        ProtocolCase("invalid JSON", b'{"jsonrpc": "2.0", "id": 1, "method": ', good),
        ProtocolCase("empty body", b"", good),
        ProtocolCase("JSON array batch", b"[" + _rpc_bytes("no/such/method") + b"]", good),
        ProtocolCase("unknown method", _rpc_bytes("no/such/method"), good),
        ProtocolCase("missing Content-Type", listing, no_content_type),
        ProtocolCase("wrong Content-Type", listing, dict(good, **{"Content-Type": "text/plain"})),
        ProtocolCase("missing Accept", listing, no_accept),
        ProtocolCase(
            f"{limits.big_body_bytes / (1 << 20):g} MB body",
            _rpc_bytes("no/such/method", params={"pad": padding}),
            good,
        ),
        ProtocolCase("wrong Origin", listing, dict(good, Origin=EVIL_ORIGIN)),
        ProtocolCase(
            "unsupported protocol version", listing, dict(good, **{"MCP-Protocol-Version": "1999-01-01"})
        ),
        ProtocolCase(
            "malformed protocol version", listing, dict(good, **{"MCP-Protocol-Version": "not a version"})
        ),
        ProtocolCase("no session", listing, no_session),
        ProtocolCase("unknown session", listing, dict(good, **{SESSION_HEADER: uuid.uuid4().hex})),
    ]


def judge_protocol(target: Target, case: ProtocolCase, reply: Reply) -> list[Violation]:
    where = "protocol"
    limit = target.limits.protocol_timeout_s
    if reply.oversize:
        return [Violation(where, case.name, reply.error or "reply larger than the ceiling")]
    if reply.error is not None:
        return [Violation(where, case.name, f"no usable reply within {limit:g} s: {reply.error}")]
    found: list[Violation] = []
    status = reply.status or 0
    body = target.excerpt(reply.body)
    if reply.elapsed > limit:
        found.append(
            Violation(where, case.name, f"answered after {reply.elapsed:.1f} s, more than {limit:g} s")
        )
    if status >= 500:
        found.append(Violation(where, case.name, f"server error HTTP {status}", body))
    elif not 400 <= status < 500 and not has_rpc_error(parse_message(reply.body)):
        found.append(
            Violation(
                where, case.name, f"hostile request accepted: HTTP {status} without a JSON-RPC error", body
            )
        )
    for name in unhandled_findings(
        reply.body, is_error=status >= 400 or has_rpc_error(parse_message(reply.body))
    ):
        found.append(Violation(where, case.name, f"reply carries {name}", body))
    return found


def scenario_protocol(target: Target) -> list[Violation]:
    session = target.open_session()
    violations: list[Violation] = []
    for case in protocol_cases(session, target.limits):
        reply = session.post(case.body, case.headers)
        target.record_error(f"protocol: {case.name}", reply)
        violations += judge_protocol(target, case, reply)
    # The server must still serve a well-formed request on a fresh session after all of that.
    try:
        target.open_session().list_tools()
    except SessionError as exc:
        violations.append(Violation("protocol", "afterwards", f"a valid session no longer works: {exc}"))
    return violations


# ---------------------------------------------------------------------------------------------------------------
# Scenario: arguments
# ---------------------------------------------------------------------------------------------------------------


def argument_cases(target: Target) -> Iterable[tuple[str, str, dict[str, Any]]]:
    """(tool, case name, arguments) for every tool and every argument the server's own tools/list declares."""
    for tool, schema in sorted(target.schemas.items()):
        base = valid_arguments(schema, target.symbol, target.fqn or target.symbol)
        properties = schema.get("properties") or {}
        for name, prop in sorted(properties.items()):
            kind = argument_kind(prop)
            label = f"{tool}.{name}"
            if kind == "string":
                for payload_name, payload in string_payloads(target.limits):
                    yield tool, f"{label}: {payload_name}", dict(base, **{name: payload})
            elif kind == "integer":
                for payload_name, payload in INTEGER_PAYLOADS:
                    yield tool, f"{label}: {payload_name}", dict(base, **{name: payload})
            for wrong_name, wrong in WRONG_TYPES[kind]:
                yield tool, f"{label}: wrong type ({wrong_name})", dict(base, **{name: wrong})
        for name in schema.get("required") or []:
            yield (
                tool,
                f"{tool}.{name}: argument missing",
                {key: value for key, value in base.items() if key != name},
            )
        if not properties:
            yield tool, f"{tool}: unexpected argument", {"unexpected": "x"}


ACCEPTABLE_KINDS = frozenset({"ok", "tool_error", "rpc_error", "http_4xx"})


def judge_call(target: Target, scenario: str, case: str, outcome: Outcome) -> list[Violation]:
    limit = target.limits.tool_timeout_s
    found: list[Violation] = []
    shown = target.excerpt(outcome.text)
    if outcome.kind not in ACCEPTABLE_KINDS:
        found.append(Violation(scenario, case, f"{outcome.kind}: {outcome.detail}", shown))
    if outcome.elapsed > limit:
        found.append(
            Violation(scenario, case, f"answered after {outcome.elapsed:.1f} s, more than {limit:g} s")
        )
    for name in unhandled_findings(outcome.text, is_error=outcome.is_error_body):
        found.append(Violation(scenario, case, f"reply carries {name}", shown))
    return found


def scenario_arguments(target: Target) -> list[Violation]:
    session = target.open_session()
    violations: list[Violation] = []
    for tool, case, arguments in argument_cases(target):
        outcome = session.call_tool(tool, arguments)
        if outcome.is_error_body:
            target.record_error(f"arguments: {case}", outcome)
        violations += judge_call(target, "arguments", case, outcome)
        if len(violations) >= MAX_VIOLATIONS_PER_SCENARIO:
            violations.append(
                Violation("arguments", "(stopped)", "too many violations; the rest were not run")
            )
            break
    return violations


# ---------------------------------------------------------------------------------------------------------------
# Scenario: isolation
# ---------------------------------------------------------------------------------------------------------------

# Keys whose text may legitimately echo the question that was asked.
ECHO_KEYS = frozenset(
    {"reason", "message", "query", "error", "detail", "code", "correlation_id", "suggestions"}
)


# What the isolation scenario still reads: the keys above, except `suggestions`. A suggestion is the server
# offering a symbol from its own index, so another pilot's symbol there is a leak, never an echo.
ISOLATION_SKIPPED_KEYS = ECHO_KEYS - {"suggestions"}


def data_strings(value: Any, key: str | None = None, skip: frozenset[str] = ECHO_KEYS) -> Iterable[str]:
    if key in skip:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child_key, child in value.items():
            yield from data_strings(child, str(child_key), skip)
    elif isinstance(value, list):
        for child in value:
            yield from data_strings(child, key, skip)


def _argument_strings(arguments: Any) -> set[str]:
    """Every string value in the request arguments, at any depth (dictionary keys are not values)."""
    if isinstance(arguments, str):
        return {arguments}
    found: set[str] = set()
    if isinstance(arguments, Mapping):
        for child in arguments.values():
            found |= _argument_strings(child)
    elif isinstance(arguments, (list, tuple)):
        for child in arguments:
            found |= _argument_strings(child)
    return found


def strip_request_echo(value: Any, arguments: Any) -> Any:
    """``value`` with every string that is EXACTLY one of the request's string arguments replaced by "".

    An honest "not found" answer repeats the requested name back (``fqn``, ``query``, a scope field). That is the
    asker's own text, not data from the index. A string that merely CONTAINS the requested text (a definition, a
    signature, a member list entry) is kept, because that is content, and so is every non-string value."""
    echoed = _argument_strings(arguments)

    def clean(node: Any) -> Any:
        if isinstance(node, str):
            return "" if node in echoed else node
        if isinstance(node, Mapping):
            return {key: clean(child) for key, child in node.items()}
        if isinstance(node, list):
            return [clean(child) for child in node]
        return node

    return clean(value)


def walk(value: Any) -> Iterable[tuple[str | None, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), child
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def isolation_findings(
    target: Target, other: OtherPilot, value: Any, arguments: Mapping[str, Any] | None = None
) -> list[str]:
    """What in one answer belongs to another pilot: its symbol, its scope, or its generation.

    The symbol is searched for only in what remains after the request's own text is removed from the answer."""
    found: list[str] = []
    for text in data_strings(strip_request_echo(value, arguments or {}), skip=ISOLATION_SKIPPED_KEYS):
        if gate.names_symbol(other.symbol, text):
            found.append(
                f"the other pilot's symbol {other.symbol!r} appears in the answer: {target.excerpt(text, other.symbol)}"
            )
            break
    own_prefix = f"{target.family}::{target.platform}::"
    for key, child in walk(value):
        if key == "scope" and isinstance(child, Mapping):
            family, platform = child.get("family"), child.get("platform")
            if (family, platform) != (target.family, target.platform) and (family or platform):
                found.append(f"an answer carries scope {family}/{platform}, not this pilot's {target.key}")
        elif key is not None and key.endswith("generation_id") and isinstance(child, str) and child:
            if not child.startswith(own_prefix):
                found.append(f"an answer carries generation id {target.excerpt(child)}, not this pilot's")
    return found


def identity_variants(other: OtherPilot) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("no identity arguments", {}),
        ("family and platform", {"family": other.family, "platform": other.platform}),
        ("scope as text", {"scope": f"{other.family}/{other.platform}"}),
        (
            "scope as object",
            {"scope": {"family": other.family, "platform": other.platform, "source_kind": "self_extracted"}},
        ),
        ("source_kind", {"source_kind": "self_extracted"}),
        ("generation_id", {"generation_id": f"{other.family}::{other.platform}::self_extracted::0"}),
        (
            "every identity argument",
            {
                "family": other.family,
                "platform": other.platform,
                "scope": f"{other.family}/{other.platform}",
                "source_kind": "self_extracted",
                "generation_id": f"{other.family}::{other.platform}::self_extracted::0",
            },
        ),
    ]


def isolation_tool_arguments(target: Target, other: OtherPilot) -> list[tuple[str, dict[str, Any]]]:
    """The calls that ask for the other pilot's symbol, for each tool that takes a query or a name."""
    calls: list[tuple[str, dict[str, Any]]] = []
    for tool, schema in sorted(target.schemas.items()):
        properties = schema.get("properties") or {}
        if "query" in properties:
            asked: dict[str, Any] = valid_arguments(schema, other.symbol, other.symbol)
            asked["query"] = other.symbol
            calls.append((tool, asked))
        elif "fqn" in properties:
            calls.append((tool, valid_arguments(schema, other.symbol, other.symbol)))
    return calls


def scenario_isolation(target: Target) -> list[Violation]:
    other = target.other
    if other is None:
        return [
            Violation(
                "isolation", "setup", "no symbol that exists only in another pilot's fixture could be found"
            )
        ]
    session = target.open_session()
    violations: list[Violation] = []

    # The control: an endpoint that answers "nothing" to everything would pass every check below.
    if "search_symbols" in target.schemas:
        control = session.call_tool("search_symbols", {"query": target.symbol})
        named = control.kind == "ok" and any(
            gate.names_symbol(target.symbol, text) for text in data_strings(control.value)
        )
        if not named:
            violations.append(
                Violation(
                    "isolation",
                    "control",
                    f"this pilot's own symbol {target.symbol!r} returned no content naming it",
                    target.excerpt(control.text),
                )
            )

    for tool, base in isolation_tool_arguments(target, other):
        for variant, extra in identity_variants(other):
            case = f"{tool}: {variant}"
            asked = dict(base, **extra)
            outcome = session.call_tool(tool, asked)
            if outcome.is_error_body:
                target.record_error(f"isolation: {case}", outcome)
            violations += judge_call(target, "isolation", case, outcome)
            if outcome.kind == "ok":
                for problem in isolation_findings(target, other, outcome.value, asked):
                    violations.append(Violation("isolation", case, problem))
    return violations


# ---------------------------------------------------------------------------------------------------------------
# Scenario: disclosure
# ---------------------------------------------------------------------------------------------------------------


def disclosure_findings(target: Target, body: ErrorBody) -> list[Violation]:
    found: list[Violation] = []
    case = body.case
    for name, pattern in (*LEAK_PATTERNS, *STACK_TRACE_PATTERNS):
        match = pattern.search(body.text)
        if match is not None:
            found.append(
                Violation(
                    "disclosure",
                    case,
                    f"error body contains {name}",
                    target.excerpt(body.text, match.group(0)),
                )
            )
    for variable, value in target.env_secrets.items():
        if value and value in body.text:
            found.append(
                Violation(
                    "disclosure", case, f"error body contains the value of environment variable {variable}"
                )
            )
    return found


def scenario_disclosure(target: Target) -> list[Violation]:
    if not target.error_bodies:
        return [
            Violation(
                "disclosure",
                "setup",
                "no error body was collected, so nothing could be inspected (the protocol and arguments scenarios produce them)",
            )
        ]
    violations: list[Violation] = []
    for body in target.error_bodies:
        violations += disclosure_findings(target, body)
        if len(violations) >= MAX_VIOLATIONS_PER_SCENARIO:
            break
    target.notes.setdefault("disclosure", {})["error_bodies_inspected"] = len(target.error_bodies)
    return violations


# ---------------------------------------------------------------------------------------------------------------
# Scenario: concurrency
# ---------------------------------------------------------------------------------------------------------------


def percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)]


def latency_rule(
    latencies: list[float], warm_latencies: list[float], floor: float, factor: float
) -> tuple[float, float, float]:
    """(p95, allowed, baseline median). The rule: p95 <= max(floor, factor * median of the warm-up calls)."""
    baseline_median = statistics.median(warm_latencies) if warm_latencies else 0.0
    return percentile_95(latencies), max(floor, factor * baseline_median), baseline_median


def _session_worker(
    target: Target, calls: list[tuple[str, dict[str, Any]]], index: int
) -> list[Outcome | str]:
    results: list[Outcome | str] = []
    try:
        session = target.open_session()
    except SessionError as exc:
        return [str(exc)]
    for step in range(target.limits.calls_per_session):
        tool, arguments = calls[(index + step) % len(calls)]
        outcome = session.call_tool(tool, arguments)
        outcome.detail = f"{tool}: {outcome.detail}" if outcome.detail else tool
        results.append(outcome)
    return results


def scenario_concurrency(target: Target) -> list[Violation]:
    calls = target.valid_calls()
    if not calls:
        return [
            Violation("concurrency", "setup", "tools/list declared no tool, so no valid call could be built")
        ]
    violations: list[Violation] = []
    warm = target.open_session()
    warm_outcomes = []
    for tool, arguments in calls:
        warmed = warm.call_tool(tool, arguments)
        warmed.detail = f"{tool}: {warmed.detail}" if warmed.detail else tool
        warm_outcomes.append(warmed)
    warm_latencies = [target.latency_of(outcome) for outcome in warm_outcomes]
    before = sample_memory_mib(target)

    with ThreadPoolExecutor(max_workers=target.limits.sessions) as pool:
        batches = list(
            pool.map(lambda index: _session_worker(target, calls, index), range(target.limits.sessions))
        )

    latencies: list[float] = []
    errors: list[Violation] = []
    total = 0
    for batch in batches:
        for item in batch:
            if isinstance(item, str):
                errors.append(Violation("concurrency", "session", item))
                continue
            total += 1
            latencies.append(target.latency_of(item))
            if item.kind != "ok":
                errors.append(
                    Violation("concurrency", item.detail, f"{item.kind}", target.excerpt(item.text))
                )
    violations += errors[:10]
    if len(errors) > 10:
        violations.append(
            Violation("concurrency", "errors", f"{len(errors)} errors in all; the first 10 are listed")
        )
    floor, factor = target.limits.p95_s, target.limits.baseline_factor
    p95, allowed, baseline_median = latency_rule(latencies, warm_latencies, floor, factor)
    if p95 > allowed:
        violations.append(
            Violation(
                "concurrency",
                "latency",
                f"p95 latency {p95:.2f} s exceeds {allowed:.2f} s, the larger of the {floor:g} s floor and "
                f"{factor:g} times the {baseline_median:.3f} s median of the sequential warm-up calls",
            )
        )
    note = target.notes.setdefault("concurrency", {})
    note.update(
        sessions=target.limits.sessions,
        calls=total,
        errors=len(errors),
        p95_seconds=round(p95, 3),
        baseline_median_seconds=round(baseline_median, 3),
        p95_allowed_seconds=round(allowed, 3),
    )

    after = sample_memory_mib(target)
    if target.container is None:
        note["memory"] = "skipped: --url attacks a running endpoint, so its memory cannot be measured"
    elif before is None or after is None:
        violations.append(
            Violation("concurrency", "memory", "memory use could not be measured with docker stats")
        )
    else:
        note.update(memory_before_mib=round(before, 2), memory_after_mib=round(after, 2))
        if after > before * (1 + target.limits.memory_growth):
            violations.append(
                Violation(
                    "concurrency",
                    "memory",
                    f"memory grew from {before:.1f} MiB to {after:.1f} MiB, more than {target.limits.memory_growth:.0%}",
                )
            )
    return violations


# ---------------------------------------------------------------------------------------------------------------
# Scenario: restart
# ---------------------------------------------------------------------------------------------------------------


def generation_of(session: McpSession) -> str:
    outcome = session.call_tool("report_index_freshness", {})
    value = outcome.value if outcome.kind == "ok" else None
    generation = value.get("indexed_generation_id") if isinstance(value, dict) else None
    return generation if isinstance(generation, str) else ""


def symbol_resolves(session: McpSession, symbol: str) -> bool:
    outcome = session.call_tool("search_symbols", {"query": symbol})
    return outcome.kind == "ok" and any(
        gate.names_symbol(symbol, text) for text in data_strings(outcome.value)
    )


def scenario_restart(target: Target) -> list[Violation]:
    if target.container is None or target.gate_ctx is None:
        raise Skipped(
            "restart persistence is skipped: --url attacks an already-running endpoint, not a container"
        )
    ctx = target.gate_ctx
    restarted = gate.run_docker(target.deps, ["restart", target.container], timeout=RESTART_TIMEOUT_S)
    if restarted.rc != 0:
        return [
            Violation(
                "restart",
                "docker restart",
                f"docker restart failed (exit {restarted.rc})",
                " ".join(restarted.stderr.split())[:160],
            )
        ]
    # The published host port is assigned afresh when a container starts, so it must be read again.
    mapped = gate.run_docker(target.deps, gate.port_command(target.container)[1:])
    port = gate.host_port(mapped.stdout) if mapped.rc == 0 else None
    if port is None:
        return [Violation("restart", "port", "docker port reported no loopback host port after the restart")]
    target.base_url = f"http://127.0.0.1:{port}"
    not_ready = gate.wait_until_ready(ctx, target.container, target.base_url)
    if not_ready is not None:
        return [Violation("restart", "ready", f"the container did not become ready again: {not_ready}")]
    try:
        session = target.open_session()
    except SessionError as exc:
        return [Violation("restart", "session", f"no MCP session after the restart: {exc}")]
    violations: list[Violation] = []
    after = generation_of(session)
    if not after or after != target.generation_id:
        violations.append(
            Violation(
                "restart",
                "generation",
                f"generation id changed or vanished across the restart (before {target.excerpt(target.generation_id) or 'none'}, "
                f"after {target.excerpt(after) or 'none'})",
            )
        )
    if not symbol_resolves(session, target.symbol):
        violations.append(
            Violation("restart", "symbol", f"{target.symbol!r} no longer resolves after the restart")
        )
    return violations


# ---------------------------------------------------------------------------------------------------------------
# Running scenarios
# ---------------------------------------------------------------------------------------------------------------

SCENARIOS: tuple[tuple[str, Callable[[Target], list[Violation]]], ...] = (
    ("protocol", scenario_protocol),
    ("arguments", scenario_arguments),
    ("isolation", scenario_isolation),
    ("disclosure", scenario_disclosure),
    ("concurrency", scenario_concurrency),
    ("restart", scenario_restart),
)


@dataclass
class ScenarioResult:
    name: str
    status: str  # pass, fail or skipped
    violations: list[Violation] = field(default_factory=list)
    note: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


def run_scenario(target: Target, name: str, scenario: Callable[[Target], list[Violation]]) -> ScenarioResult:
    """One scenario, then a /readyz re-check. A crash in the scenario or an unready server is a failure."""
    violations: list[Violation] = []
    note = ""
    skipped = False
    try:
        violations = list(scenario(target))
    except Skipped as exc:
        skipped, note = True, str(exc)
    except SessionError as exc:
        violations.append(Violation(name, "session", f"no MCP session could be opened: {exc}"))
    except Exception as exc:
        violations.append(
            Violation(
                name, "crash", f"the scenario crashed: {type(exc).__name__}: {target.excerpt(str(exc))}"
            )
        )
    unready = recheck_ready(target)
    if unready is not None:
        violations.append(Violation(name, "readyz", unready))
    status = "fail" if violations else "skipped" if skipped else "pass"
    return ScenarioResult(name, status, violations, note, dict(target.notes.get(name, {})))


def baseline(target: Target) -> list[Violation]:
    """The facts the scenarios compare against: the tool schemas, one FQN, and the active generation id."""
    try:
        session = target.open_session()
        target.schemas = session.list_tools()
    except SessionError as exc:
        return [
            Violation("baseline", "session", f"the endpoint could not be queried before any attack: {exc}")
        ]
    if not target.schemas:
        return [Violation("baseline", "tools/list", "tools/list returned an empty tool registry")]
    found = (
        session.call_tool("search_symbols", {"query": target.symbol})
        if "search_symbols" in target.schemas
        else None
    )
    if found is not None and found.kind == "ok":
        for text in data_strings(found.value):
            for fqn in _FQN_LINE.findall(text):
                if final_segment(fqn) == target.symbol:
                    target.fqn = fqn.strip()
                    break
            if target.fqn:
                break
    target.generation_id = generation_of(session) if "report_index_freshness" in target.schemas else ""
    problems: list[Violation] = []
    if not target.fqn:
        problems.append(
            Violation(
                "baseline",
                "known symbol",
                f"{target.symbol!r} did not resolve before any attack; the endpoint serves no content",
            )
        )
    if not target.generation_id:
        problems.append(
            Violation("baseline", "generation", "no active generation id was reported before any attack")
        )
    return problems


# ---------------------------------------------------------------------------------------------------------------
# Pilots
# ---------------------------------------------------------------------------------------------------------------


def parse_pilot(text: str) -> tuple[str, str]:
    if PILOT_PATTERN.fullmatch(text) is None:
        raise Refused(
            f"pilot {text[:40]!r} must look like FAMILY/PLATFORM, for example pdf/net. Nothing was run."
        )
    family, platform = text.split("/")
    return family, platform


def fixture_names(fixtures: Path, key: str, proof: Any) -> list[str]:
    path = fixtures / key / "api_surface.json"
    types = (json.loads(path.read_text(encoding="utf-8")).get("types") or []) if path.is_file() else []
    return [
        t["name"]
        for t in types[: proof.INDEXED_TYPE_LIMIT]
        if isinstance(t, dict) and isinstance(t.get("name"), str)
    ]


def choose_other(fixtures: Path, proof: Any, pilot: Any, requested: str | None) -> OtherPilot | None:
    """A symbol that exists only in another pilot's fixture. An explicit --other-pilot wins unless it is this pilot."""
    if requested is not None:
        family, platform = parse_pilot(requested)
        key = f"{family}_{platform}"
        if key != pilot.name:
            api = fixtures / pilot.name / "api_surface.json"
            own = {
                t["name"]
                for t in (json.loads(api.read_text(encoding="utf-8")).get("types") or [])
                if isinstance(t, dict) and isinstance(t.get("name"), str)
            }
            names = sorted(
                {n for n in fixture_names(fixtures, key, proof) if n not in own and n != proof.ABSENT_SYMBOL}
            )
            if names:
                return OtherPilot(family, platform, max(names, key=lambda n: (len(n), n)))
            return None
    if pilot.other_pilot and pilot.other_symbol:
        family, _, platform = pilot.other_pilot.partition("_")
        return OtherPilot(family, platform, pilot.other_symbol)
    return None


def container_name(family: str, platform: str, run_id: str) -> str:
    return f"foss-mcp-adv-{family}-{platform}-{run_id}"


def env_command(name: str) -> list[str]:
    return ["docker", "inspect", "--format", "{{json .Config.Env}}", name]


def read_env_secrets(deps: gate.Deps, name: str) -> dict[str, str]:
    """NAME -> value for the container's own environment, kept in memory to scan for leaks. Never printed or written."""
    result = gate.run_docker(deps, env_command(name)[1:])
    if result.rc != 0:
        return {}
    try:
        entries = json.loads(result.stdout.strip() or "[]")
    except ValueError:
        return {}
    secrets: dict[str, str] = {}
    for entry in entries if isinstance(entries, list) else []:
        variable, separator, value = str(entry).partition("=")
        if separator and len(value) >= MIN_SECRET_LENGTH:
            secrets[variable] = value
    return secrets


@dataclass
class Settings:
    image: str
    memory: str
    cpus: float
    ready_timeout: float
    fixtures: Path
    report_dir: Path
    run_id: str
    other_pilot: str | None
    url: str | None
    limits: Limits
    deps: gate.Deps
    proof: Any
    containers: gate.Containers
    redact: gate.Redactor
    gateway_env: dict[str, str] = field(default_factory=dict)


def run_pilot(entry: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Attack one pilot start to finish; returns its report document. Always removes the container."""
    family, platform = entry["family"], entry["platform"]
    key = f"{family}/{platform}"
    name = container_name(family, platform, settings.run_id)
    reasons: list[str] = []
    results: list[ScenarioResult] = []
    started = False
    try:
        pilot = settings.proof.load_pilot(
            f"{family}_{platform}", settings.fixtures, chart_values=chart.CHART_VALUES
        )
        if pilot.symbol is None:
            reasons.append(pilot.probe_error or f"{key} has no probe symbol")
        else:
            target = Target(
                base_url="",
                family=family,
                platform=platform,
                symbol=pilot.symbol,
                other=choose_other(settings.fixtures, settings.proof, pilot, settings.other_pilot),
                limits=settings.limits,
                deps=settings.deps,
                redact=settings.redact,
                env_secrets=dict(settings.gateway_env),
            )
            ctx = gate.Context(
                image=settings.image,
                memory=settings.memory,
                memory_mib=gate.parse_memory_limit_mib(settings.memory),
                cpus=settings.cpus,
                ready_timeout=settings.ready_timeout,
                fixtures=settings.fixtures,
                report_dir=settings.report_dir,
                with_gateway=False,
                run_id=settings.run_id,
                deps=settings.deps,
                proof=settings.proof,
                containers=settings.containers,
                redact=settings.redact,
            )
            if settings.url is not None:
                target.base_url = settings.url
                not_ready = wait_url_ready(target, settings.ready_timeout)
            else:
                settings.containers.add(
                    name
                )  # recorded before `docker run`: a failed run may still leave one
                started = True
                target.container, target.gate_ctx = name, ctx
                not_ready = start_container(entry, name, settings, target, ctx)
            if not_ready is not None:
                reasons.append(not_ready)
            else:
                results = attack(target)
    except gate.Stopped:
        reasons.append("the run was interrupted before this pilot finished")
    except Exception as exc:
        reasons.append(f"suite error: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        if started:
            settings.containers.remove(name)
    return pilot_document(key, name if settings.url is None else None, settings, results, reasons)


def start_container(
    entry: dict[str, Any], name: str, settings: Settings, target: Target, ctx: Any
) -> str | None:
    command = gate.run_command(
        settings.image, name, entry, settings.memory, settings.cpus, with_gateway=False
    )
    launched = gate.run_docker(settings.deps, command[1:], timeout=gate.DOCKER_RUN_TIMEOUT_S)
    if launched.rc != 0:
        return f"docker run failed (exit {launched.rc}): {' '.join((launched.stderr or launched.stdout).split())[:300]}"
    mapped = gate.run_docker(settings.deps, gate.port_command(name)[1:])
    port = gate.host_port(mapped.stdout) if mapped.rc == 0 else None
    if port is None:
        return "docker port did not report a loopback host port for the container"
    target.base_url = f"http://127.0.0.1:{port}"
    not_ready = gate.wait_until_ready(ctx, name, target.base_url)
    if not_ready is not None:
        tail = gate.log_tail(ctx, name)
        return f"the container never became ready: {not_ready}" + (
            f"; last log lines: {tail}" if tail else ""
        )
    target.env_secrets.update(read_env_secrets(settings.deps, name))
    return None


def wait_url_ready(target: Target, timeout: float) -> str | None:
    clock = target.deps.clock or time.monotonic
    sleep = target.deps.sleep or time.sleep
    deadline = clock() + timeout
    while True:
        status = ready_status(target)
        if status == 200:
            return None
        if clock() >= deadline:
            return f"{READY_PATH} at the given --url did not answer 200 within {timeout:g} s (last: {status})"
        sleep(gate.POLL_INTERVAL_S)


def attack(target: Target) -> list[ScenarioResult]:
    problems = baseline(target)
    if problems:
        return [ScenarioResult("baseline", "fail", problems)]
    results = []
    for name, scenario in SCENARIOS:
        results.append(run_scenario(target, name, scenario))
    return results


def pilot_document(
    key: str, container: str | None, settings: Settings, results: list[ScenarioResult], reasons: list[str]
) -> dict[str, Any]:
    failed = [result for result in results if result.status == "fail"]
    passed = [result for result in results if result.status == "pass"]
    status = "fail" if reasons or failed or not passed else "pass"
    document = {
        "key": key,
        "status": status,
        "image": settings.image,
        "container": container,
        "budget": {"memory": settings.memory, "cpus": settings.cpus},
        "reasons": list(reasons),
        "scenarios": {
            result.name: {
                "status": result.status,
                "note": result.note,
                "metrics": result.metrics,
                "violations": [violation.as_dict() for violation in result.violations],
            }
            for result in results
        },
    }
    return settings.redact.scrub(document)


# ---------------------------------------------------------------------------------------------------------------
# Arguments, reports and output
# ---------------------------------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attack the exact shipped image the way a peer who wants it to fail will.",
    )
    parser.add_argument("--image", help="image NAME:TAG to attack (an explicit tag, never latest)")
    parser.add_argument(
        "--pilot",
        action="append",
        default=[],
        metavar="FAMILY/PLATFORM",
        help=f"pilot to attack (repeatable; default {', '.join(DEFAULT_PILOTS)})",
    )
    parser.add_argument(
        "--other-pilot",
        metavar="FAMILY/PLATFORM",
        help="the pilot whose symbols the isolation scenario asks for (default: chosen from the fixtures)",
    )
    parser.add_argument("--fixtures", type=Path, default=gate.DEFAULT_FIXTURES, help="fixtures root")
    parser.add_argument(
        "--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="where the reports are written"
    )
    parser.add_argument(
        "--memory", type=gate._memory_arg, default=gate.DEFAULT_MEMORY, help="container memory limit"
    )
    parser.add_argument(
        "--cpus", type=gate._positive_float, default=gate.DEFAULT_CPUS, help="container CPU limit"
    )
    parser.add_argument(
        "--ready-timeout",
        type=gate._positive_float,
        default=gate.DEFAULT_READY_TIMEOUT,
        help="seconds to wait for the readiness probe",
    )
    parser.add_argument("--url", help="attack this running endpoint instead of starting a container")
    parser.add_argument("--dry-run", action="store_true", help="print the docker commands and run nothing")
    return parser.parse_args(argv)


def validate_request(args: argparse.Namespace) -> list[dict[str, Any]]:
    """The chart entries to attack. Raises Refused for anything that must not run."""
    if args.url is None and not args.image:
        raise Refused("--image NAME:TAG is required unless --url is given. Nothing was run.")
    if args.image:
        try:
            gate.validate_image(args.image)
        except gate.Refused as exc:
            raise Refused(str(exc)) from exc
    pilots = list(args.pilot) or list(DEFAULT_PILOTS)
    keys = [f"{family}_{platform}" for family, platform in (parse_pilot(item) for item in pilots)]
    if args.other_pilot is not None:
        parse_pilot(args.other_pilot)
    try:
        selected = chart.select_pilots(chart.load_pilots(), list(dict.fromkeys(keys)))
    except (ValueError, OSError) as exc:
        raise Refused(str(exc)) from exc
    if args.url is not None:
        parts = urllib.parse.urlsplit(args.url)
        if parts.scheme != "http" or not parts.hostname:
            raise Refused("--url must be an http://HOST:PORT base URL. Nothing was run.")
        if len(args.pilot) != 1:
            raise Refused(
                "--url needs exactly one --pilot: the identity the endpoint serves. Nothing was run."
            )
    return selected


def write_json(path: Path, document: dict[str, Any]) -> None:
    gate.write_json(path, document)


def build_summary(
    *, image: str, image_id: str | None, mode: str, pilots: list[dict[str, Any]], memory: str, cpus: float
) -> dict[str, Any]:
    passed = sum(1 for pilot in pilots if pilot["status"] == "pass")
    return {
        "schema_version": SCHEMA_VERSION,
        "image": image,
        "image_id": image_id,
        "mode": mode,
        "budget": {"memory": memory, "cpus": cpus},
        "generated_at": gate.utc_timestamp(),
        "verdict": "PASS" if pilots and passed == len(pilots) else "FAIL",
        "pilots_total": len(pilots),
        "pilots_passed": passed,
        "pilots": pilots,
    }


def format_row(pilot: dict[str, Any]) -> str:
    cells = "  ".join(f"{name} {data['status'].upper()}" for name, data in pilot["scenarios"].items())
    return f"{pilot['key']:<18} {pilot['status'].upper():<4}  {cells}"


def emit(redact: gate.Redactor, text: str, *, stream: Any = None) -> None:
    print(gate.ascii_safe(redact.text(text)), file=stream if stream is not None else sys.stdout, flush=True)


def print_dry_run(args: argparse.Namespace, selected: list[dict[str, Any]], run_id: str) -> None:
    if args.url is not None:
        print(f"attack the running endpoint {args.url} (no container is started)")
    else:
        print(shlex.join(gate.image_id_command(args.image)))
        for entry in selected:
            name = container_name(entry["family"], entry["platform"], run_id)
            print(
                shlex.join(
                    gate.run_command(args.image, name, entry, args.memory, args.cpus, with_gateway=False)
                )
            )
            for command in (
                gate.port_command(name),
                env_command(name),
                gate.stats_command(name),
                ["docker", "restart", name],
                gate.remove_command(name),
            ):
                print(shlex.join(command))
    print(
        f"adversarial_suite: dry run for {len(selected)} pilot(s); nothing was run and nothing was written.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None, deps: gate.Deps | None = None, limits: Limits | None = None) -> int:
    deps = deps or gate.Deps()
    args = parse_args(argv)
    try:
        selected = validate_request(args)
    except Refused as exc:
        print(f"adversarial_suite: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    run_id = deps.run_id or uuid.uuid4().hex[:8]
    if args.dry_run:
        print_dry_run(args, selected, run_id)
        return 0

    environ = deps.environ if deps.environ is not None else os.environ
    redact = gate.Redactor(secret_values(environ))
    image_id: str | None = None
    if args.url is None:
        try:
            image_id, _size = gate.inspect_image(deps, args.image)
        except gate.Refused as exc:
            print(f"adversarial_suite: {exc}", file=sys.stderr)
            return EXIT_FAILED

    containers = gate.Containers(deps)
    settings = Settings(
        image=args.image or "",
        memory=args.memory,
        cpus=args.cpus,
        ready_timeout=args.ready_timeout,
        fixtures=args.fixtures,
        report_dir=args.report_dir,
        run_id=run_id,
        other_pilot=args.other_pilot,
        url=args.url.rstrip("/") if args.url else None,
        limits=limits or Limits(),
        deps=deps,
        proof=gate._load_proof_module(),
        containers=containers,
        redact=redact,
        gateway_env={name: environ[name] for name in gate.GATEWAY_ENV_NAMES if environ.get(name)},
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)

    pilots: list[dict[str, Any]] = []
    try:
        for entry in selected:  # one container at a time: the host is shared and these attacks are heavy
            document = run_pilot(entry, settings)
            pilots.append(document)
            write_json(args.report_dir / f"{entry['family']}_{entry['platform']}.json", document)
            emit(
                redact,
                f"adversarial_suite: {document['key']} finished: {document['status']}",
                stream=sys.stderr,
            )
    except KeyboardInterrupt:
        emit(
            redact,
            "adversarial_suite: interrupted; removing every container this run started",
            stream=sys.stderr,
        )
        containers.remove_all()
        return EXIT_INTERRUPTED
    finally:
        containers.remove_all()

    summary = redact.scrub(
        build_summary(
            image=args.image or "",
            image_id=image_id,
            mode="endpoint" if args.url else "container",
            pilots=pilots,
            memory=args.memory,
            cpus=args.cpus,
        )
    )
    write_json(args.report_dir / SUMMARY_NAME, summary)
    for pilot in pilots:
        emit(redact, format_row(pilot))
        for reason in pilot["reasons"]:
            emit(redact, f"    reason: {reason}")
        for name, data in pilot["scenarios"].items():
            if data["note"]:
                emit(redact, f"    {name}: {data['note']}")
            for violation in data["violations"][:5]:
                emit(redact, f"    violation [{name}] {violation['case']}: {violation['problem']}")
    emit(
        redact,
        f"verdict: {summary['verdict']} ({summary['pilots_passed']} of {summary['pilots_total']} pilots passed); "
        f"reports: {args.report_dir}",
    )
    return 0 if summary["verdict"] == "PASS" else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
