"""G3/TC-355: scripts/release/adversarial_suite.py, offline.

No docker command ever runs and no container is ever started: subprocess is replaced by a fake docker daemon, and
the MCP endpoint is a small HTTP server started on the loopback interface inside the test. That server is well
behaved by default; each ``Behavior`` flag turns on one deliberate defect (a 500 on a malformed body, a stack trace
in an error, a leaked ``/app/`` path, a cross-pilot symbol, a slow reply, a server that goes unready after an
attack, a memory jump, a generation change after a restart), and the matching scenario must detect it.
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
SUITE_SOURCE = RELEASE_DIR / "adversarial_suite.py"

sys.path.insert(0, str(RELEASE_DIR))

import adversarial_suite as suite  # noqa: E402
import image_workflow_gate as gate  # noqa: E402

chart = gate.chart

IMAGE = "foss-mcp-serving:rev-abc1234"
IMAGE_ID = "sha256:" + "ab12" * 16
RUN_ID = "advtest1"
FAMILY, PLATFORM = "pdf", "net"
PLANTED_KEY = "hunter2-planted-api-key-value-7c1d"
PLANTED_ENDPOINT = "https://gateway-planted.example.test/v1/9f3b"
CONTAINER_SECRET = "planted-container-secret-4f9c1e7a"
GENERATION = "pdf::net::self_extracted::gen-1"

PROOF = gate._load_proof_module()
PILOT = PROOF.load_pilot("pdf_net", suite.gate.DEFAULT_FIXTURES, chart_values=chart.CHART_VALUES)
SYMBOL = PILOT.symbol
OTHER = suite.choose_other(gate.DEFAULT_FIXTURES, PROOF, PILOT, None)
assert SYMBOL and OTHER

# Small limits keep the offline runs fast; the numbers the card fixes are pinned separately.
FAST = suite.Limits(
    protocol_timeout_s=3.0,
    tool_timeout_s=2.0,
    p95_s=2.0,
    ready_recheck_s=0.6,
    sessions=4,
    calls_per_session=3,
    big_string_bytes=64 * 1024,
    big_body_bytes=256 * 1024,
    nesting=500,
)

TOOLS: dict[str, dict[str, Any]] = {
    "lookup": {
        "properties": {
            "query": {"type": "string"},
            "content_type": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "top_k": {"type": "integer"},
        },
        "required": ["query"],
    },
    "search_docs": {
        "properties": {
            "query": {"type": "string"},
            "content_type": {"enum": ["getting_started", "faq"], "type": "string"},
            "top_k": {"type": "integer"},
        },
        "required": ["query", "content_type"],
    },
    "search_symbols": {
        "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
        "required": ["query"],
    },
    "get_symbol": {"properties": {"fqn": {"type": "string"}}, "required": ["fqn"]},
    "list_members": {"properties": {"fqn": {"type": "string"}}, "required": ["fqn"]},
    "find_examples": {
        "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
        "required": ["query"],
    },
    "get_product_reference": {"properties": {"section": {"type": "string"}}, "required": ["section"]},
    "list_recent_changes": {"properties": {"limit": {"type": "integer"}}, "required": []},
    "report_index_freshness": {
        "properties": {
            "source_kind": {"type": "string"},
            "current_source_commit": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": [],
    },
}


# --- the fake MCP endpoint -------------------------------------------------------------------------------------


@dataclass
class Behavior:
    """One flag per deliberate defect. All off is a well-behaved server."""

    crash_on_invalid_json: bool = False  # HTTP 500 on a malformed body
    accept_bad_origin: bool = False
    traceback_in_errors: bool = False
    leak_app_path: bool = False
    leak_secret: str | None = None
    leak_other_symbol: bool = False
    crash_on_nul: bool = False  # HTTP 500 from a tool call carrying a NUL byte
    slow_redos_s: float = 0.0  # search_symbols sleeps on the regex-bomb pattern
    slow_lookup_s: float = 0.0  # every valid lookup sleeps
    flaky_recent_changes: bool = False  # list_recent_changes answers 503
    unready_after_bad_origin: bool = False
    empty_index: bool = False  # answers "nothing" to everything
    generation_after_restart: str | None = None
    forget_symbol_after_restart: bool = False
    # TC-365: an honest miss echoes the request back; each leak flag puts another pilot's symbol somewhere else.
    honest_miss_shape: bool = False
    leak_in_suggestions: bool = False
    leak_in_definition: bool = False
    leak_in_members: bool = False
    pad_reply_bytes: int = 0  # get_product_reference echoes this many bytes back (twice: text and structured)


class FakeEndpoint:
    """A loopback HTTP server that speaks enough of Streamable HTTP MCP for the suite."""

    def __init__(self, behavior: Behavior | None = None) -> None:
        self.behavior = behavior or Behavior()
        self.generation = GENERATION
        self.ready = True
        self.knows_symbol = True
        self.sessions: set[str] = set()
        self.lock = threading.Lock()
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/readyz":
                    ok = endpoint.ready
                    self._send(200 if ok else 503, "ready" if ok else "not ready", "text/plain")
                elif self.path == "/healthz":
                    self._send(200, "alive", "text/plain")
                else:
                    self._send(404, "not found", "text/plain")

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                try:
                    endpoint.handle_post(self, body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _send(
                self, status: int, text: str, content_type: str, extra: dict[str, str] | None = None
            ) -> None:
                data = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 128

            def handle_error(self, request: Any, client_address: Any) -> None:
                return  # a client that gave up (a timeout test) is not a failure of the fake

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def restart(self) -> None:
        self.ready = True
        if self.behavior.generation_after_restart:
            self.generation = self.behavior.generation_after_restart
        if self.behavior.forget_symbol_after_restart:
            self.knows_symbol = False

    # -- request handling
    def leak(self, text: str) -> str:
        b = self.behavior
        if b.traceback_in_errors:
            text += "\nTraceback (most recent call last):\n  boom"
        if b.leak_app_path:
            text += " at /app/foss_mcp/mcp/server.py"
        if b.leak_secret:
            text += f" env={b.leak_secret}"
        return text

    def handle_post(self, h: BaseHTTPRequestHandler, body: bytes) -> None:
        b = self.behavior
        headers = h.headers
        if headers.get("Origin"):
            if b.unready_after_bad_origin:
                self.ready = False
            if not b.accept_bad_origin:
                return h._send(
                    400, json.dumps({"error": "rejected", "reason": self.leak("origin")}), "application/json"
                )
        version = headers.get("MCP-Protocol-Version")
        if version and version != suite.PROTOCOL_VERSION:
            return h._send(
                400, json.dumps({"error": "rejected", "reason": self.leak("version")}), "application/json"
            )
        if "application/json" not in (headers.get("Content-Type") or ""):
            return h._send(415, "unsupported media type", "text/plain")
        if "text/event-stream" not in (headers.get("Accept") or ""):
            return h._send(406, "not acceptable", "text/plain")
        try:
            message = json.loads(body)
        except ValueError:
            if b.crash_on_invalid_json:
                return h._send(500, "Internal Server Error", "text/plain")
            return self.rpc_http_error(h, 400, -32700, self.leak("parse error"))
        if isinstance(message, list):
            return self.rpc_http_error(h, 400, -32600, self.leak("batch not supported"))
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self.rpc_http_error(h, 400, -32600, "invalid request")
        method = message.get("method")
        if method == "initialize":
            sid = f"s{len(self.sessions) + 1}"
            with self.lock:
                self.sessions.add(sid)
            result = {"protocolVersion": suite.PROTOCOL_VERSION, "serverInfo": {"name": "fake-foss-mcp"}}
            return self.sse(h, message.get("id"), result, {"mcp-session-id": sid})
        sid = headers.get(suite.SESSION_HEADER)
        if not sid:
            return self.rpc_http_error(h, 400, -32600, "missing session id")
        if sid not in self.sessions:
            return self.rpc_http_error(h, 404, -32600, "unknown session")
        if method == "notifications/initialized":
            return h._send(202, "", "text/plain")
        if method == "tools/list":
            tools = [
                {"name": name, "inputSchema": dict(schema, type="object")} for name, schema in TOOLS.items()
            ]
            return self.sse(h, message.get("id"), {"tools": tools})
        if method == "tools/call":
            return self.tool_call(h, message)
        return self.sse_error(h, message.get("id"), -32601, self.leak("method not found"))

    def tool_call(self, h: BaseHTTPRequestHandler, message: dict[str, Any]) -> None:
        b = self.behavior
        params = message.get("params") or {}
        tool = params.get("name")
        arguments = params.get("arguments")
        request_id = message.get("id")
        if tool not in TOOLS:
            return self.tool_error(h, request_id, self.leak(f"unknown tool: {str(tool)[:40]}"))
        problem = self.validate(tool, arguments)
        strings = (
            [v for v in (arguments or {}).values() if isinstance(v, str)]
            if isinstance(arguments, dict)
            else []
        )
        if b.crash_on_nul and any("\x00" in value for value in strings):
            return h._send(500, "Internal Server Error", "text/plain")
        if problem is not None:
            return self.tool_error(h, request_id, self.leak(f"Input validation error: {problem}"))
        if b.slow_redos_s and tool == "search_symbols" and "(a+)+$" in strings:
            time.sleep(b.slow_redos_s)
        if b.slow_lookup_s and tool == "lookup":
            time.sleep(b.slow_lookup_s)
        if b.flaky_recent_changes and tool == "list_recent_changes":
            return h._send(503, "unavailable", "text/plain")
        value = self.answer(tool, arguments)
        if b.pad_reply_bytes and tool == "get_product_reference":
            value = dict(value, echo="A" * b.pad_reply_bytes)
        return self.sse(h, request_id, self.wrap(value))

    def validate(self, tool: str, arguments: Any) -> str | None:
        if not isinstance(arguments, dict):
            return "arguments must be an object"
        schema = TOOLS[tool]
        for name in schema["required"]:
            if name not in arguments:
                return f"Field required: {name}"
        for name, value in arguments.items():
            prop = schema["properties"].get(name)
            if prop is None:
                return f"Unexpected argument: {name[:20]}"
            kinds = suite.schema_kinds(prop)
            if "integer" in kinds:
                if isinstance(value, bool) or not isinstance(value, int):
                    return f"{name}: Input should be a valid integer"
            elif isinstance(value, str):
                continue
            elif value is None and "anyOf" in prop:
                continue
            else:
                return f"{name}: Input should be a valid string"
        return None

    def answer(self, tool: str, arguments: dict[str, Any]) -> Any:
        b = self.behavior
        scope = {"family": FAMILY, "platform": PLATFORM, "source_kind": "self_extracted"}
        query = arguments.get("query") or arguments.get("fqn") or ""
        if (
            b.honest_miss_shape
            and tool in ("search_symbols", "lookup", "search_docs", "find_examples")
            and query != SYMBOL
        ):
            return {"scope": scope, "query": query, "results": [], "suggestions": []}
        if tool in ("get_symbol", "list_members") and (
            b.honest_miss_shape or b.leak_in_suggestions or b.leak_in_definition or b.leak_in_members
        ):
            miss: dict[str, Any] = {"scope": scope, "fqn": query, "suggestions": []}
            if query == OTHER.symbol:
                if b.leak_in_suggestions:
                    miss["suggestions"] = [f"Other.{OTHER.symbol}"]
                if b.leak_in_definition:
                    miss["definition"] = f"public class {OTHER.symbol} : Base"
                if b.leak_in_members:
                    miss["members"] = [{"name": f"{OTHER.symbol}.Open"}]
            return miss
        if tool in ("search_symbols", "lookup"):
            if b.empty_index or not self.knows_symbol:
                return []
            if query == SYMBOL:
                return [{"text": f"FQN: Lib.{SYMBOL}\nKind: class", "scope": scope}]
            if query == OTHER.symbol and b.leak_other_symbol:
                other_scope = {"family": OTHER.family, "platform": OTHER.platform}
                return [{"text": f"FQN: Other.{OTHER.symbol}\nKind: class", "scope": other_scope}]
            return []
        if tool == "get_symbol":
            return {"reason": f"no symbol {query!r}", "suggestions": []}
        if tool == "get_product_reference":
            return {"section": arguments.get("section"), "content": "MIT"}
        if tool == "report_index_freshness":
            return {"indexed_generation_id": self.generation, "stale": False}
        return []

    @staticmethod
    def wrap(value: Any) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": json.dumps(value)}],
            "structuredContent": {"result": value},
            "isError": False,
        }

    def tool_error(self, h: BaseHTTPRequestHandler, request_id: Any, text: str) -> None:
        self.sse(h, request_id, {"content": [{"type": "text", "text": text}], "isError": True})

    def rpc_http_error(self, h: BaseHTTPRequestHandler, status: int, code: int, text: str) -> None:
        payload = {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": text}}
        h._send(status, json.dumps(payload), "application/json")

    def sse_error(self, h: BaseHTTPRequestHandler, request_id: Any, code: int, text: str) -> None:
        payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": text}}
        h._send(200, f"event: message\ndata: {json.dumps(payload)}\n\n", "text/event-stream")

    def sse(
        self, h: BaseHTTPRequestHandler, request_id: Any, result: Any, extra: dict[str, str] | None = None
    ) -> None:
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        h._send(200, f"event: message\ndata: {json.dumps(payload)}\n\n", "text/event-stream", extra)


@pytest.fixture
def endpoint():
    created: list[FakeEndpoint] = []

    def make(**flags: Any) -> FakeEndpoint:
        fake = FakeEndpoint(Behavior(**flags))
        created.append(fake)
        return fake

    yield make
    for fake in created:
        fake.close()


# --- the fake docker daemon ------------------------------------------------------------------------------------


class FakeDocker:
    def __init__(self, fake: FakeEndpoint, *, stats=("40.0MiB / 256MiB",), run_rc: int = 0) -> None:
        self.fake = fake
        self.stats = list(stats)
        self.run_rc = run_rc
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.running: set[str] = set()
        self.removed: list[str] = []
        self.env = [
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "FOSS_MCP_PILOT=pdf/net",
            f"SECRET_TOKEN={CONTAINER_SECRET}",
        ]
        self.lock = threading.Lock()

    def run(self, cmd, **kwargs):
        with self.lock:
            self.calls.append(list(cmd))
            self.kwargs.append(dict(kwargs))
            return self._docker(list(cmd))

    @staticmethod
    def _done(cmd, rc=0, out="", err=""):
        return subprocess.CompletedProcess(cmd, rc, out, err)

    def _docker(self, cmd):
        assert cmd[0] == "docker", cmd
        verb = cmd[1]
        if verb == "image":
            return self._done(cmd, out=(IMAGE_ID if "{{.Id}}" in cmd else "123456789") + "\n")
        if verb == "run":
            name = cmd[cmd.index("--name") + 1]
            if self.run_rc:
                return self._done(cmd, self.run_rc, "", "docker: error response from daemon")
            self.running.add(name)
            return self._done(cmd, out="0123456789abcdef\n")
        if verb == "port":
            return self._done(cmd, out=f"127.0.0.1:{self.fake.port}\n")
        if verb == "restart":
            self.fake.restart()
            return self._done(cmd, out=cmd[-1] + "\n")
        if verb == "stats":
            out = self.stats.pop(0) if len(self.stats) > 1 else self.stats[0]
            return self._done(cmd, out=out + "\n")
        if verb == "inspect":
            if "json .Config.Env" in cmd[3]:
                return self._done(cmd, out=json.dumps(self.env) + "\n")
            return self._done(cmd, out="true false\n")
        if verb == "logs":
            return self._done(cmd, out="serving\n")
        if verb == "rm":
            self.removed.append(cmd[-1])
            self.running.discard(cmd[-1])
            return self._done(cmd, out=cmd[-1] + "\n")
        raise AssertionError(f"unexpected docker command {cmd}")

    def verbs(self, verb: str) -> list[list[str]]:
        return [call for call in self.calls if call[1] == verb]


def make_deps(docker: FakeDocker | None = None, **environ: str) -> Any:
    return gate.Deps(
        run=docker.run if docker is not None else None,
        sleep=lambda seconds: time.sleep(min(seconds, 0.05)),
        environ=dict(environ),
        run_id=RUN_ID,
    )


def make_target(fake: FakeEndpoint, *, with_baseline: bool = True, **overrides: Any) -> suite.Target:
    target = suite.Target(
        base_url=fake.url,
        family=FAMILY,
        platform=PLATFORM,
        symbol=SYMBOL,
        other=OTHER,
        limits=FAST,
        deps=make_deps(),
        **overrides,
    )
    if with_baseline:
        assert suite.baseline(target) == []
    return target


def messages(violations: list[suite.Violation]) -> str:
    return " | ".join(f"{v.case}: {v.problem}" for v in violations)


# --- the card's numbers, the request, the helpers ---------------------------------------------------------------


def test_limits_are_the_numbers_the_card_fixes() -> None:
    limits = suite.Limits()
    assert (limits.protocol_timeout_s, limits.tool_timeout_s, limits.p95_s) == (10.0, 5.0, 2.0)
    assert limits.memory_growth == 0.25
    assert (limits.sessions, limits.calls_per_session) == (20, 10)
    assert limits.big_string_bytes == 1 << 20
    assert limits.big_body_bytes == 5 << 20
    assert limits.nesting == 5000
    assert suite.SCENARIO_NAMES == (
        "protocol",
        "arguments",
        "isolation",
        "disclosure",
        "concurrency",
        "restart",
    )
    assert [name for name, _ in suite.SCENARIOS] == list(suite.SCENARIO_NAMES)


def test_default_pilots_are_in_the_chart_and_differ_in_language() -> None:
    keys = {chart.pilot_key(entry) for entry in chart.load_pilots()}
    assert len(suite.DEFAULT_PILOTS) == 3
    platforms = set()
    for pilot in suite.DEFAULT_PILOTS:
        family, platform = suite.parse_pilot(pilot)
        assert f"{family}_{platform}" in keys
        platforms.add(platform)
    assert len(platforms) == 3


@pytest.mark.parametrize(
    "image", ["foss-mcp-serving:latest", "foss-mcp-serving", "foss-mcp-serving:LATEST", "x y:1"]
)
def test_latest_and_untagged_images_are_refused(image: str, capsys: pytest.CaptureFixture[str]) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("docker must not be called")

    code = suite.main(["--image", image], gate.Deps(run=explode, run_id=RUN_ID))
    assert code == suite.EXIT_REFUSED
    assert "Nothing was run" in capsys.readouterr().err


def test_an_image_is_required_unless_attacking_a_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert suite.main([], gate.Deps(run_id=RUN_ID)) == suite.EXIT_REFUSED
    assert "--image" in capsys.readouterr().err


@pytest.mark.parametrize("pilot", ["pdf", "PDF/net", "pdf/net/extra", "pdf/nonexistent", "../etc/passwd"])
def test_bad_pilot_names_are_refused(pilot: str) -> None:
    assert suite.main(["--image", IMAGE, "--pilot", pilot], gate.Deps(run_id=RUN_ID)) == suite.EXIT_REFUSED


def test_a_url_needs_exactly_one_pilot_and_an_http_address(capsys: pytest.CaptureFixture[str]) -> None:
    assert suite.main(["--url", "http://127.0.0.1:1"], gate.Deps(run_id=RUN_ID)) == suite.EXIT_REFUSED
    assert (
        suite.main(["--url", "ftp://x", "--pilot", "pdf/net"], gate.Deps(run_id=RUN_ID)) == suite.EXIT_REFUSED
    )
    two = ["--url", "http://127.0.0.1:1", "--pilot", "pdf/net", "--pilot", "slides/python"]
    assert suite.main(two, gate.Deps(run_id=RUN_ID)) == suite.EXIT_REFUSED
    assert "exactly one --pilot" in capsys.readouterr().err


def test_dry_run_runs_nothing_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("dry run must not run anything")

    report_dir = tmp_path / "reports"
    code = suite.main(
        ["--image", IMAGE, "--dry-run", "--report-dir", str(report_dir)],
        gate.Deps(run=explode, run_id=RUN_ID),
    )
    captured = capsys.readouterr()
    assert code == 0
    assert not report_dir.exists()
    assert captured.out.count("docker run") == 3
    assert "FOSS_MCP_PILOT=pdf/net" in captured.out
    assert "docker restart" in captured.out and "nothing was run" in captured.err


def test_percentile_is_nearest_rank() -> None:
    assert suite.percentile_95([]) == 0.0
    assert suite.percentile_95([1.0]) == 1.0
    assert suite.percentile_95([float(n) for n in range(1, 101)]) == 95.0
    assert suite.percentile_95([0.1] * 19 + [9.0]) == 0.1


@pytest.mark.parametrize(
    ("text", "is_error", "expected"),
    [
        ("all fine", True, []),
        ("Traceback (most recent call last):", False, ["python stack trace"]),
        ('File "/x/y.py", line 3', False, ["python stack frame"]),
        ("a KeyError happened", True, ["python exception name"]),
        ("the docs mention a KeyError", False, []),
    ],
)
def test_unhandled_findings(text: str, is_error: bool, expected: list[str]) -> None:
    assert suite.unhandled_findings(text, is_error=is_error) == expected


def test_schema_helpers_look_through_any_of_and_enums() -> None:
    assert suite.schema_kinds({"anyOf": [{"type": "string"}, {"type": "null"}]}) == {"string"}
    assert suite.schema_kinds({"type": ["integer", "null"]}) == {"integer"}
    schema = TOOLS["search_docs"] | {"type": "object"}
    assert suite.valid_arguments(schema, "Sym", "A.Sym") == {
        "query": "Sym",
        "content_type": "getting_started",
    }
    assert suite.valid_arguments(TOOLS["get_symbol"], "Sym", "A.Sym") == {"fqn": "A.Sym"}
    assert suite.valid_arguments(TOOLS["list_recent_changes"], "Sym", "A.Sym") == {}


def test_argument_cases_cover_every_tool_and_argument_with_every_payload(endpoint) -> None:
    target = make_target(endpoint())
    cases = list(suite.argument_cases(target))
    names = [case for _, case, _ in cases]
    payloads = [name for name, _ in suite.string_payloads(FAST)]
    assert len(payloads) == 11
    for tool, schema in TOOLS.items():
        for argument, prop in schema["properties"].items():
            kind = suite.argument_kind(prop)
            expected = payloads if kind == "string" else [name for name, _ in suite.INTEGER_PAYLOADS]
            for payload in expected:
                assert f"{tool}.{argument}: {payload}" in names
            assert any(name.startswith(f"{tool}.{argument}: wrong type") for name in names)
    assert {"zero", "minus one", "2**31", "2**63", "float"} == {name for name, _ in suite.INTEGER_PAYLOADS}
    required = [c for c in names if c.endswith("argument missing")]
    assert len(required) == sum(len(schema["required"]) for schema in TOOLS.values())
    assert any("regex bomb" in name for name in payloads)


# --- a well-behaved server passes every scenario ---------------------------------------------------------------


def test_protocol_passes_against_a_well_behaved_server(endpoint) -> None:
    target = make_target(endpoint())
    assert suite.scenario_protocol(target) == []
    names = {body.case for body in target.error_bodies}
    for expected in (
        "invalid JSON",
        "JSON array batch",
        "wrong Origin",
        "no session",
        "unsupported protocol version",
    ):
        assert f"protocol: {expected}" in names


def test_arguments_passes_against_a_well_behaved_server(endpoint) -> None:
    target = make_target(endpoint())
    assert suite.scenario_arguments(target) == []
    assert target.error_bodies, "hostile arguments produce error bodies that the disclosure scenario inspects"


def test_isolation_passes_against_a_well_behaved_server(endpoint) -> None:
    assert suite.scenario_isolation(make_target(endpoint())) == []


def test_disclosure_passes_after_the_attacks_against_a_well_behaved_server(endpoint) -> None:
    target = make_target(endpoint())
    suite.scenario_protocol(target)
    suite.scenario_arguments(target)
    assert suite.scenario_disclosure(target) == []
    assert target.notes["disclosure"]["error_bodies_inspected"] == len(target.error_bodies) > 10


def test_concurrency_passes_against_a_well_behaved_server(endpoint) -> None:
    target = make_target(endpoint())
    assert suite.scenario_concurrency(target) == []
    note = target.notes["concurrency"]
    assert note["calls"] == FAST.sessions * FAST.calls_per_session
    assert note["errors"] == 0
    assert "skipped" in note["memory"]


def test_the_default_concurrency_is_twenty_sessions_of_ten_calls(endpoint) -> None:
    target = make_target(endpoint())
    target.limits = suite.Limits(tool_timeout_s=5.0, p95_s=5.0)
    assert suite.scenario_concurrency(target) == []
    assert target.notes["concurrency"]["calls"] == 200


def test_restart_is_skipped_for_a_url_target_and_says_so(endpoint) -> None:
    target = make_target(endpoint())
    result = suite.run_scenario(target, "restart", suite.scenario_restart)
    assert result.status == "skipped"
    assert "--url" in result.note and "skipped" in result.note


# --- each scenario detects a deliberately bad server -----------------------------------------------------------


def test_protocol_detects_a_500_on_a_malformed_body(endpoint) -> None:
    violations = suite.scenario_protocol(make_target(endpoint(crash_on_invalid_json=True)))
    assert any(v.case == "invalid JSON" and "HTTP 500" in v.problem for v in violations), messages(violations)


def test_protocol_detects_a_wrong_origin_that_is_accepted(endpoint) -> None:
    violations = suite.scenario_protocol(make_target(endpoint(accept_bad_origin=True)))
    assert any(v.case == "wrong Origin" and "accepted" in v.problem for v in violations), messages(violations)


def test_protocol_detects_a_traceback_in_an_error_body(endpoint) -> None:
    violations = suite.scenario_protocol(make_target(endpoint(traceback_in_errors=True)))
    assert any("python stack trace" in v.problem for v in violations), messages(violations)


def test_protocol_detects_a_hang(endpoint) -> None:
    fake = endpoint()
    target = make_target(fake)
    target.limits = suite.Limits(protocol_timeout_s=0.6, tool_timeout_s=2.0, p95_s=2.0, ready_recheck_s=0.6)
    release = threading.Event()
    original = fake.handle_post

    def hang(h: Any, body: bytes) -> None:
        if body == b'{"jsonrpc": "2.0", "id": 1, "method": ':
            release.wait(2.0)
        return original(h, body)

    fake.handle_post = hang  # type: ignore[method-assign]
    try:
        violations = suite.scenario_protocol(target)
    finally:
        release.set()
    assert any(v.case == "invalid JSON" and "no usable reply" in v.problem for v in violations), messages(
        violations
    )


def test_arguments_detects_a_crash_on_a_nul_byte(endpoint) -> None:
    violations = suite.scenario_arguments(make_target(endpoint(crash_on_nul=True)))
    assert any("NUL byte" in v.case and "http_5xx" in v.problem for v in violations), messages(violations)


def test_arguments_detects_a_slow_reply(endpoint) -> None:
    violations = suite.scenario_arguments(make_target(endpoint(slow_redos_s=2.3)))
    assert any("regex bomb pattern" in v.case and "more than" in v.problem for v in violations), messages(
        violations
    )


def test_arguments_detects_exception_text_in_a_tool_error(endpoint) -> None:
    violations = suite.scenario_arguments(make_target(endpoint(traceback_in_errors=True)))
    assert any("python stack trace" in v.problem for v in violations), messages(violations)


def test_isolation_detects_a_cross_pilot_symbol(endpoint) -> None:
    violations = suite.scenario_isolation(make_target(endpoint(leak_other_symbol=True)))
    text = messages(violations)
    assert OTHER.symbol in text
    assert f"scope {OTHER.family}/{OTHER.platform}" in text


def test_isolation_control_detects_an_endpoint_that_serves_nothing(endpoint) -> None:
    target = make_target(endpoint(empty_index=True), with_baseline=False)
    target.schemas = {name: dict(schema, type="object") for name, schema in TOOLS.items()}
    violations = suite.scenario_isolation(target)
    assert any(v.case == "control" for v in violations), messages(violations)


def test_isolation_without_another_pilot_symbol_fails_rather_than_passing(endpoint) -> None:
    target = make_target(endpoint())
    target.other = None
    assert suite.scenario_isolation(target)[0].case == "setup"


def test_disclosure_detects_a_container_path_in_an_error_body(endpoint) -> None:
    target = make_target(endpoint(leak_app_path=True))
    suite.scenario_protocol(target)
    violations = suite.scenario_disclosure(target)
    assert any("/app/" in v.problem for v in violations), messages(violations)


def test_disclosure_detects_each_leak_pattern() -> None:
    target = suite.Target(base_url="x", family=FAMILY, platform=PLATFORM, symbol="S")
    cases = {
        "/app/infra/x.py": "/app/",
        "see /data/manifests/pdf": "/data/",
        "C:\\\\Users\\\\x": "Windows drive path",
        "root:x:0:0:root:/root": "/etc/passwd",
        "Traceback (most recent call last):": "python stack trace",
        'File "/x.py", line 4': "python stack frame",
    }
    for text, expected in cases.items():
        found = suite.disclosure_findings(target, suite.ErrorBody("case", 400, text))
        assert any(expected in v.problem for v in found), (text, messages(found))
    assert (
        suite.disclosure_findings(target, suite.ErrorBody("case", 400, "Input validation error: query")) == []
    )


def test_disclosure_detects_a_stack_trace_header() -> None:
    target = suite.Target(base_url="x", family=FAMILY, platform=PLATFORM, symbol="S")
    body = suite.ErrorBody("case", 500, "Internal error. Traceback (most recent call last): boom")
    found = suite.disclosure_findings(target, body)
    assert [v.problem for v in found] == ["error body contains python stack trace"]


def test_disclosure_detects_an_environment_value_without_repeating_it(endpoint) -> None:
    fake = endpoint(leak_secret=CONTAINER_SECRET)
    target = make_target(fake, env_secrets={"SECRET_TOKEN": CONTAINER_SECRET})
    suite.scenario_protocol(target)
    violations = suite.scenario_disclosure(target)
    assert any("environment variable SECRET_TOKEN" in v.problem for v in violations), messages(violations)
    for violation in violations:
        assert CONTAINER_SECRET not in json.dumps(violation.as_dict())


def test_disclosure_with_nothing_to_inspect_fails(endpoint) -> None:
    violations = suite.scenario_disclosure(make_target(endpoint()))
    assert [v.case for v in violations] == ["setup"]


def test_concurrency_detects_errors_from_valid_calls(endpoint) -> None:
    violations = suite.scenario_concurrency(make_target(endpoint(flaky_recent_changes=True)))
    assert any("list_recent_changes" in v.case and "http_5xx" in v.problem for v in violations), messages(
        violations
    )


def test_concurrency_detects_a_p95_over_the_limit(endpoint) -> None:
    # TC-365: the limit is relative to the warm-up baseline now, so the latencies are exact, not slept for.
    target = make_target(endpoint())
    target.latency_of = lambda outcome: 2.3 if outcome.detail.startswith("lookup") else 0.01
    violations = suite.scenario_concurrency(target)
    assert any(v.case == "latency" and "p95" in v.problem for v in violations), messages(violations)


def test_concurrency_detects_a_memory_jump(endpoint) -> None:
    fake = endpoint()
    docker = FakeDocker(fake, stats=("40.0MiB / 256MiB", "80.0MiB / 256MiB"))
    deps = make_deps(docker)
    target = make_target(fake, container="c1", gate_ctx=SimpleNamespace(deps=deps))
    target.deps = deps
    violations = suite.scenario_concurrency(target)
    assert any(v.case == "memory" and "grew" in v.problem for v in violations), messages(violations)
    assert target.notes["concurrency"]["memory_after_mib"] == 80.0


def test_concurrency_accepts_memory_within_a_quarter(endpoint) -> None:
    fake = endpoint()
    docker = FakeDocker(fake, stats=("40.0MiB / 256MiB", "49.0MiB / 256MiB"))
    deps = make_deps(docker)
    target = make_target(fake, container="c1", gate_ctx=SimpleNamespace(deps=deps))
    target.deps = deps
    assert suite.scenario_concurrency(target) == []


def test_a_scenario_that_leaves_the_server_unready_fails(endpoint) -> None:
    target = make_target(endpoint(unready_after_bad_origin=True, accept_bad_origin=True))
    result = suite.run_scenario(target, "protocol", suite.scenario_protocol)
    assert result.status == "fail"
    assert any(v.case == "readyz" and "/readyz" in v.problem for v in result.violations)


def test_a_crashing_scenario_is_a_failure_not_a_pass(endpoint) -> None:
    def boom(target: suite.Target) -> list[suite.Violation]:
        raise RuntimeError("scenario exploded")

    result = suite.run_scenario(make_target(endpoint()), "protocol", boom)
    assert result.status == "fail"
    assert result.violations[0].case == "crash"


# --- TC-365 fix 1: parse the whole reply, cap only what is stored ----------------------------------------------


def padded_call(fake: FakeEndpoint) -> tuple[suite.Reply, suite.Outcome]:
    session = make_target(fake).open_session()
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "get_product_reference", "arguments": {"section": "license"}},
        }
    ).encode("utf-8")
    reply = session.post(body, timeout=120.0)
    return reply, suite.classify(reply)


def test_the_reply_ceiling_is_far_above_the_evidence_cap() -> None:
    assert suite.MAX_REPLY_BYTES >= 8 << 20
    assert suite.MAX_REPLY_BYTES > 3 * suite.MAX_STORED_BODY
    assert suite.Limits().baseline_factor == 8.0


def test_a_valid_reply_of_about_two_and_a_half_megabytes_is_ok_not_unparseable(endpoint) -> None:
    reply, outcome = padded_call(endpoint(pad_reply_bytes=1_250_000))
    assert 2_400_000 < len(reply.body) < 2_700_000
    assert len(reply.body) > suite.MAX_STORED_BODY, "the reply really is bigger than the evidence cap"
    assert reply.error is None and not reply.oversize
    assert outcome.kind == "ok", (outcome.kind, outcome.detail)
    assert isinstance(outcome.value, dict) and len(outcome.value["echo"]) == 1_250_000


def test_a_big_reply_is_judged_and_its_stored_evidence_stays_small(endpoint) -> None:
    fake = endpoint(pad_reply_bytes=1_250_000)
    target = make_target(fake)
    target.limits = suite.Limits(tool_timeout_s=60.0)  # this test is about size, not speed
    _, outcome = padded_call(fake)
    assert len(outcome.text) <= suite.MAX_STORED_BODY
    assert suite.judge_call(target, "arguments", "big", outcome) == []
    assert len(target.excerpt(outcome.text)) <= suite.EXCERPT_WIDTH
    # a big reply that is also an error is stored capped, and still parses to the proper error kind
    error_text = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"isError": True, "content": [{"type": "text", "text": "x" * 2_500_000}]},
        }
    )
    big_error = suite.classify(suite.Reply(200, {}, f"event: message\ndata: {error_text}\n\n", 0.1))
    assert big_error.kind == "tool_error" and len(big_error.text) <= suite.MAX_STORED_BODY
    target.record_error("big error", big_error)
    assert len(target.error_bodies[-1].text) <= suite.MAX_STORED_BODY


def test_a_reply_over_the_ceiling_is_a_violation_and_is_not_parsed(endpoint) -> None:
    fake = endpoint(pad_reply_bytes=8_600_000)  # about 17 MB with the structured copy
    reply, outcome = padded_call(fake)
    assert reply.oversize and reply.error and "larger than" in reply.error and "ceiling" in reply.error
    assert reply.body == "", "an oversized reply is dropped, not kept"
    assert outcome.kind == "transport" and "larger than" in outcome.detail
    target = make_target(fake)
    violations = suite.judge_call(target, "arguments", "huge", outcome)
    assert violations and "larger than" in violations[0].problem


def test_an_oversized_protocol_reply_is_reported_as_larger_than_the_ceiling() -> None:
    target = suite.Target(base_url="x", family=FAMILY, platform=PLATFORM, symbol="S", limits=FAST)
    case = suite.ProtocolCase("huge", b"", {})
    reply = suite.Reply(200, {}, "", 0.5, "reply larger than the 16 MB ceiling", oversize=True)
    (violation,) = suite.judge_protocol(target, case, reply)
    assert "larger than the 16 MB ceiling" in violation.problem and "no usable reply" not in violation.problem


def test_a_truncated_reply_is_still_unparseable() -> None:
    cut = suite.Reply(200, {}, 'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {"a": "', 0.1)
    assert suite.classify(cut).kind == "unparseable"


# --- TC-365 fix 2: an echoed request is not another pilot's data --------------------------------------------------


def test_strip_request_echo_removes_exact_equal_strings_only() -> None:
    stripped = suite.strip_request_echo(
        {"fqn": "Ns.Widget", "definition": "class Ns.Widget : Base", "n": 3, "ok": True, "none": None},
        {"fqn": "Ns.Widget"},
    )
    assert stripped == {"fqn": "", "definition": "class Ns.Widget : Base", "n": 3, "ok": True, "none": None}


def test_strip_request_echo_handles_nested_structures_and_request_objects() -> None:
    arguments = {"query": "Widget", "scope": {"family": "pdf", "platform": "net"}, "tags": ["a", ["b"]]}
    value = {
        "scope": {"family": "pdf", "platform": "net", "extra": "Widget helper"},
        "items": [{"name": "Widget"}, {"name": "WidgetFactory"}, ["a", "b", "c"]],
        "count": 3,
    }
    assert suite.strip_request_echo(value, arguments) == {
        "scope": {"family": "", "platform": "", "extra": "Widget helper"},
        "items": [{"name": ""}, {"name": "WidgetFactory"}, ["", "", "c"]],
        "count": 3,
    }


def test_strip_request_echo_does_not_mutate_its_inputs_or_touch_non_string_arguments() -> None:
    value = {"a": ["x", {"b": "x"}]}
    arguments = {"top_k": 5, "flag": True, "nothing": None, "q": "x"}
    snapshot = json.dumps(value)
    assert suite.strip_request_echo(value, arguments) == {"a": ["", {"b": ""}]}
    assert json.dumps(value) == snapshot
    assert suite.strip_request_echo({"n": 5, "t": True, "s": "5"}, {"top_k": 5, "flag": True}) == {
        "n": 5,
        "t": True,
        "s": "5",
    }
    assert suite.strip_request_echo("plain", {"q": "plain"}) == ""
    assert suite.strip_request_echo(["plain"], {}) == ["plain"]


def test_isolation_findings_ignore_the_echo_but_keep_every_real_leak() -> None:
    target = suite.Target(base_url="x", family=FAMILY, platform=PLATFORM, symbol=SYMBOL, other=OTHER)
    asked = {"fqn": OTHER.symbol}
    echo = {"scope": {"family": FAMILY, "platform": PLATFORM}, "fqn": OTHER.symbol, "suggestions": []}
    assert suite.isolation_findings(target, OTHER, echo, asked) == []
    leaks = {
        "suggestions": {"fqn": OTHER.symbol, "suggestions": [f"Lib.{OTHER.symbol}"]},
        "definition": {"fqn": OTHER.symbol, "definition": f"class {OTHER.symbol} : Base"},
        "members": {"fqn": OTHER.symbol, "members": [f"{OTHER.symbol}.Open"]},
        "another field": {"fqn": OTHER.symbol, "summary": f"See {OTHER.symbol} for details"},
    }
    for name, answer in leaks.items():
        found = suite.isolation_findings(target, OTHER, answer, asked)
        assert any(OTHER.symbol in problem for problem in found), (name, found)
    # without the request there is nothing to strip: the old behaviour on a bare answer is unchanged
    assert suite.isolation_findings(target, OTHER, echo)


def test_isolation_passes_an_honest_miss_that_echoes_the_request(endpoint) -> None:
    target = make_target(endpoint(honest_miss_shape=True))
    target.limits = suite.Limits(tool_timeout_s=60.0)  # this test is about the echo, not about speed
    violations = suite.scenario_isolation(target)
    assert violations == [], messages(violations)


@pytest.mark.parametrize("flag", ["leak_in_suggestions", "leak_in_definition", "leak_in_members"])
def test_isolation_fails_a_symbol_leaked_beyond_the_echo(endpoint, flag: str) -> None:
    violations = suite.scenario_isolation(make_target(endpoint(honest_miss_shape=True, **{flag: True})))
    assert any(OTHER.symbol in v.problem and "appears in the answer" in v.problem for v in violations), (
        messages(violations)
    )


# --- TC-365 fix 3: the latency rule is relative to a measured baseline --------------------------------------------


def with_limits(target: suite.Target, **changes: Any) -> suite.Target:
    values = {
        "protocol_timeout_s": 3.0,
        "tool_timeout_s": 2.0,
        "p95_s": 2.0,
        "ready_recheck_s": 0.6,
        "sessions": 4,
        "calls_per_session": 3,
    }
    values.update(changes)
    target.limits = suite.Limits(**values)
    return target


# The latency rule is asserted on exact numbers: the scenario's `latency_of` seam is replaced by a function that
# returns a fixed latency per tool, so nothing below depends on how fast or loaded the host is.


def fixed_latencies(per_tool: dict[str, float], default: float) -> Any:
    def latency_of(outcome: suite.Outcome) -> float:
        return per_tool.get(outcome.detail.split(":")[0], default)

    return latency_of


def judged(
    endpoint: Any, per_tool: dict[str, float], default: float, **limits: Any
) -> tuple[Any, suite.Target]:
    target = with_limits(make_target(endpoint()), **limits)
    target.latency_of = fixed_latencies(per_tool, default)
    return suite.scenario_concurrency(target), target


def test_latency_rule_is_exact_arithmetic() -> None:
    # p95 of the calls, max(floor, factor * median of the warm-up), and that median
    assert suite.latency_rule([0.1] * 19 + [0.9], [0.01, 0.02, 0.03], 2.0, 8.0) == (0.1, 2.0, 0.02)
    assert suite.latency_rule([0.5, 0.6, 9.0], [0.5, 1.0, 3.0], 2.0, 8.0) == (9.0, 8.0, 1.0)
    assert suite.latency_rule([1.0, 2.0], [0.25, 0.25], 0.1, 8.0) == (2.0, 2.0, 0.25)
    assert suite.latency_rule([1.0], [], 2.0, 8.0) == (1.0, 2.0, 0.0)


def test_the_production_latency_is_the_measured_round_trip() -> None:
    assert suite.elapsed_of(suite.Outcome("ok", 200, 0.321)) == 0.321
    assert (
        suite.Target(base_url="x", family=FAMILY, platform=PLATFORM, symbol="S").latency_of
        is suite.elapsed_of
    )


def test_a_uniformly_slow_server_passes_the_relative_latency_rule(endpoint) -> None:
    violations, target = judged(endpoint, {}, 0.25, p95_s=0.1)
    assert violations == [], messages(violations)
    note = target.notes["concurrency"]
    assert note["baseline_median_seconds"] == 0.25
    assert note["p95_seconds"] == 0.25 > 0.1, "an absolute limit this small would have failed the same server"
    assert note["p95_allowed_seconds"] == 2.0 == 8 * 0.25


def test_a_few_very_slow_calls_among_fast_ones_fail_the_relative_rule(endpoint) -> None:
    violations, target = judged(endpoint, {"lookup": 0.9}, 0.005, p95_s=0.2)
    latency = [v for v in violations if v.case == "latency"]
    assert [v.problem for v in latency] == [
        "p95 latency 0.90 s exceeds 0.20 s, the larger of the 0.2 s floor and "
        "8 times the 0.005 s median of the sequential warm-up calls"
    ], messages(violations)
    assert target.notes["concurrency"]["p95_allowed_seconds"] == 0.2


def test_the_floor_is_honoured_when_the_baseline_is_tiny(endpoint) -> None:
    violations, target = judged(endpoint, {"lookup": 0.3}, 0.005, p95_s=1.0)
    assert violations == [], messages(violations)
    note = target.notes["concurrency"]
    assert note["p95_seconds"] == 0.3 > 8 * note["baseline_median_seconds"], "only the floor lets this pass"
    assert note["p95_allowed_seconds"] == 1.0
    # the same latencies fail once the floor is below what they need
    tight, _ = judged(endpoint, {"lookup": 0.3}, 0.005, p95_s=0.1)
    assert [v.case for v in tight] == ["latency"]


def test_the_default_floor_is_two_seconds_and_a_slow_server_over_it_still_fails(endpoint) -> None:
    assert suite.Limits().p95_s == 2.0 and suite.Limits().baseline_factor == 8.0
    violations, _ = judged(endpoint, {"lookup": 2.3}, 0.01, p95_s=suite.Limits().p95_s)
    assert [v.problem for v in violations] == [
        "p95 latency 2.30 s exceeds 2.00 s, the larger of the 2 s floor and "
        "8 times the 0.010 s median of the sequential warm-up calls"
    ]
    assert judged(endpoint, {"lookup": 2.0}, 0.01, p95_s=2.0)[0] == [], "exactly at the limit passes"


def test_the_allowed_p95_scales_with_the_measured_baseline_at_the_exact_boundary(endpoint) -> None:
    # baseline 0.5 s per call: allowed = max(2, 8 * 0.5) = 4.0 s
    assert judged(endpoint, {"lookup": 4.0}, 0.5, p95_s=2.0)[0] == []
    over, _ = judged(endpoint, {"lookup": 4.01}, 0.5, p95_s=2.0)
    assert [v.case for v in over] == ["latency"] and "exceeds 4.00 s" in over[0].problem


# --- end to end through main(), with a fake docker daemon ------------------------------------------------------


def run_main(
    tmp_path: Path,
    fake: FakeEndpoint,
    *extra: str,
    docker: FakeDocker | None = None,
    limits: suite.Limits = FAST,
    environ: dict[str, str] | None = None,
) -> tuple[int, FakeDocker, Path]:
    docker = docker or FakeDocker(fake)
    deps = make_deps(docker, **(environ or {}))
    report_dir = tmp_path / "reports"
    argv = [
        "--image",
        IMAGE,
        "--pilot",
        "pdf/net",
        "--report-dir",
        str(report_dir),
        "--ready-timeout",
        "30",
        *extra,
    ]
    return suite.main(argv, deps, limits), docker, report_dir


def test_the_ready_timeout_given_to_run_main_has_real_margin_above_five_seconds() -> None:
    source = inspect.getsource(run_main)
    match = re.search(r'"--ready-timeout",\s*\n\s*"(\d+)"', source)
    assert match is not None, "run_main must pass --ready-timeout as a literal"
    assert int(match.group(1)) > 5, (
        "the ready-timeout literal regressed back to a value with no real margin for a slow "
        "clean-room subprocess spawn"
    )


def load(report_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((report_dir / name).read_text(encoding="utf-8"))


def test_a_well_behaved_image_passes_end_to_end(
    tmp_path: Path, endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = endpoint()
    code, docker, report_dir = run_main(tmp_path, fake)
    out = capsys.readouterr().out
    assert code == 0, out
    summary = load(report_dir, "summary.json")
    assert summary["schema_version"] == 1
    assert summary["image"] == IMAGE and summary["image_id"] == IMAGE_ID
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", summary["generated_at"])
    assert summary["verdict"] == "PASS" and summary["pilots_total"] == summary["pilots_passed"] == 1
    pilot = load(report_dir, "pdf_net.json")
    assert set(pilot["scenarios"]) == set(suite.SCENARIO_NAMES)  # report files sort their keys
    assert all(data["status"] == "pass" for data in pilot["scenarios"].values()), pilot["scenarios"]
    assert pilot["scenarios"]["concurrency"]["metrics"]["errors"] == 0
    assert "verdict: PASS" in out
    # exactly one container, started the way a peer starts it, then removed
    (run_call,) = docker.verbs("run")
    assert "FOSS_MCP_PILOT=pdf/net" in run_call and run_call[-1] == IMAGE
    assert "--read-only" in run_call and "--memory" in run_call and "256m" in run_call
    assert docker.removed == [f"foss-mcp-adv-pdf-net-{RUN_ID}"]
    assert len(docker.verbs("restart")) == 1


def test_the_verdict_is_fail_when_any_scenario_fails(
    tmp_path: Path, endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = endpoint(crash_on_invalid_json=True)
    code, docker, report_dir = run_main(tmp_path, fake)
    out = capsys.readouterr().out
    assert code == suite.EXIT_FAILED
    summary = load(report_dir, "summary.json")
    assert summary["verdict"] == "FAIL" and summary["pilots_passed"] == 0
    pilot = load(report_dir, "pdf_net.json")
    assert pilot["scenarios"]["protocol"]["status"] == "fail"
    assert pilot["scenarios"]["protocol"]["violations"][0]["case"] == "invalid JSON"
    assert pilot["scenarios"]["restart"]["status"] == "pass"
    assert "verdict: FAIL" in out and "violation [protocol]" in out
    assert docker.removed, "the container is removed even when the verdict is FAIL"


def test_a_generation_change_after_restart_is_detected(tmp_path: Path, endpoint) -> None:
    code, docker, report_dir = run_main(
        tmp_path, endpoint(generation_after_restart="pdf::net::self_extracted::gen-2")
    )
    assert code == suite.EXIT_FAILED
    restart = load(report_dir, "pdf_net.json")["scenarios"]["restart"]
    assert restart["status"] == "fail"
    assert "generation id changed" in restart["violations"][0]["problem"]


def test_a_symbol_that_stops_resolving_after_restart_is_detected(tmp_path: Path, endpoint) -> None:
    code, _, report_dir = run_main(tmp_path, endpoint(forget_symbol_after_restart=True))
    assert code == suite.EXIT_FAILED
    restart = load(report_dir, "pdf_net.json")["scenarios"]["restart"]
    assert any("no longer resolves" in v["problem"] for v in restart["violations"])


def test_a_memory_jump_fails_the_run(tmp_path: Path, endpoint) -> None:
    fake = endpoint()
    code, _, report_dir = run_main(
        tmp_path, fake, docker=FakeDocker(fake, stats=("40.0MiB / 256MiB", "90.0MiB / 256MiB"))
    )
    assert code == suite.EXIT_FAILED
    concurrency = load(report_dir, "pdf_net.json")["scenarios"]["concurrency"]
    assert concurrency["status"] == "fail" and concurrency["metrics"]["memory_after_mib"] == 90.0


def test_a_server_that_goes_unready_after_an_attack_fails_the_run(tmp_path: Path, endpoint) -> None:
    fake = endpoint(unready_after_bad_origin=True, accept_bad_origin=True)
    code, _, report_dir = run_main(tmp_path, fake)
    assert code == suite.EXIT_FAILED
    pilot = load(report_dir, "pdf_net.json")
    assert any(v["case"] == "readyz" for v in pilot["scenarios"]["protocol"]["violations"])


def test_the_container_is_removed_when_docker_run_fails(tmp_path: Path, endpoint) -> None:
    fake = endpoint()
    code, docker, report_dir = run_main(tmp_path, fake, docker=FakeDocker(fake, run_rc=125))
    assert code == suite.EXIT_FAILED
    assert docker.removed == [f"foss-mcp-adv-pdf-net-{RUN_ID}"]
    assert "docker run failed" in load(report_dir, "pdf_net.json")["reasons"][0]


def test_the_container_is_removed_when_a_scenario_raises(
    tmp_path: Path, endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(target: suite.Target) -> list[suite.Violation]:
        raise RuntimeError("scenario exploded")

    monkeypatch.setattr(suite, "SCENARIOS", (("protocol", boom),))
    code, docker, report_dir = run_main(tmp_path, endpoint())
    assert code == suite.EXIT_FAILED
    assert docker.removed == [f"foss-mcp-adv-pdf-net-{RUN_ID}"]
    assert load(report_dir, "pdf_net.json")["scenarios"]["protocol"]["violations"][0]["case"] == "crash"


def test_the_container_is_removed_on_keyboard_interrupt(
    tmp_path: Path, endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(target: suite.Target) -> list[suite.Violation]:
        raise KeyboardInterrupt

    monkeypatch.setattr(suite, "SCENARIOS", (("protocol", interrupted),))
    code, docker, report_dir = run_main(tmp_path, endpoint())
    assert code == suite.EXIT_INTERRUPTED
    assert docker.removed == [f"foss-mcp-adv-pdf-net-{RUN_ID}"]
    assert not (report_dir / "summary.json").exists(), "an interrupted run issues no verdict"


def test_attacking_a_url_starts_no_container_and_skips_restart(
    tmp_path: Path, endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = endpoint()
    docker = FakeDocker(fake)
    deps = make_deps(docker)
    report_dir = tmp_path / "reports"
    code = suite.main(["--url", fake.url, "--pilot", "pdf/net", "--report-dir", str(report_dir)], deps, FAST)
    out = capsys.readouterr().out
    assert code == 0, out
    assert docker.calls == []
    pilot = load(report_dir, "pdf_net.json")
    assert pilot["scenarios"]["restart"]["status"] == "skipped"
    assert "skipped" in pilot["scenarios"]["restart"]["note"]
    assert "skipped" in pilot["scenarios"]["concurrency"]["metrics"]["memory"]
    assert load(report_dir, "summary.json")["mode"] == "endpoint"


def test_an_empty_endpoint_fails_before_any_attack(tmp_path: Path, endpoint) -> None:
    code, docker, report_dir = run_main(tmp_path, endpoint(empty_index=True))
    assert code == suite.EXIT_FAILED
    pilot = load(report_dir, "pdf_net.json")
    assert list(pilot["scenarios"]) == ["baseline"]
    assert "serves no content" in pilot["scenarios"]["baseline"]["violations"][0]["problem"]
    assert docker.removed


def test_no_environment_value_reaches_any_output_or_report(
    tmp_path: Path, endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {
        "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT": PLANTED_ENDPOINT,
        "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY": PLANTED_KEY,
    }
    # The server leaks both a container variable and the gateway key into every error it produces.
    fake = endpoint(leak_secret=f"{CONTAINER_SECRET} {PLANTED_KEY}")
    code, docker, report_dir = run_main(tmp_path, fake, environ=environ)
    captured = capsys.readouterr()
    assert code == suite.EXIT_FAILED
    pilot = load(report_dir, "pdf_net.json")
    assert any("SECRET_TOKEN" in v["problem"] for v in pilot["scenarios"]["disclosure"]["violations"])
    assert any(
        "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY" in v["problem"]
        for v in pilot["scenarios"]["disclosure"]["violations"]
    )
    everything = (
        captured.out
        + captured.err
        + "".join(path.read_text(encoding="utf-8") for path in report_dir.iterdir())
    )
    for planted in (CONTAINER_SECRET, PLANTED_KEY, PLANTED_ENDPOINT):
        assert planted not in everything
    for call in docker.calls:
        assert not any(
            planted in part for part in call for planted in (PLANTED_KEY, PLANTED_ENDPOINT, CONTAINER_SECRET)
        )
    (run_call,) = docker.verbs("run")
    assert not any("GATEWAY" in part for part in run_call), (
        "the gateway variables are never passed to the attacked container"
    )


def test_the_suite_reuses_the_gate_helpers_and_does_not_copy_them() -> None:
    source = SUITE_SOURCE.read_text(encoding="utf-8")
    assert "import image_workflow_gate as gate" in source
    for name in (
        "def run_docker",
        "def host_port",
        "def wait_until_ready",
        "class Containers",
        "def run_command",
    ):
        assert name not in source, f"{name} belongs to image_workflow_gate and is imported, not copied"
    assert "gate.validate_image" in source


def test_every_container_the_suite_starts_has_a_cleanup_path() -> None:
    source = SUITE_SOURCE.read_text(encoding="utf-8")
    assert "finally:" in source and "settings.containers.remove(name)" in source
    assert "containers.remove_all()" in source
    assert "except KeyboardInterrupt" in source
