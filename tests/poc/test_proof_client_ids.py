"""G3/TC-513: the proof client numbers its JSON-RPC requests, checks the answer's id, and takes its time limits from parameters.

Offline. The e2e module's ``httpx.post`` is replaced by a scripted fake that records the JSON body, headers and ``timeout`` of
every call and answers with an SSE-style response object, so no network and no container is needed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "poc" / "pilot_workflow_proof.py"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("pilot_workflow_proof_ids", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_e2e() -> Any:
    # The same import open_live_client performs.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tests.e2e import test_cells_rust_live_content as e2e

    return e2e


pwp = _load_script()
e2e = _load_e2e()

URL = "http://127.0.0.1:9"


class FakeResponse:
    def __init__(
        self, body: dict[str, Any] | None, *, status_code: int = 200, headers: dict[str, str] | None = None
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = "" if body is None else f"event: message\ndata: {json.dumps(body)}\n\n"


Answer = Callable[[dict[str, Any]], "FakeResponse | Exception"]


class FakeHttp:
    """Scripted ``httpx.post``. The handshake is answered by itself; each tools/call or tools/list takes the next step."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.steps: list[Answer] = []

    def echo(self, result: Any = None) -> Answer:
        def step(body: dict[str, Any]) -> FakeResponse:
            return FakeResponse(self.rpc_result(body, body["id"], result))

        return step

    @staticmethod
    def rpc_result(body: dict[str, Any], answer_id: Any, result: Any = None) -> dict[str, Any]:
        if body["method"] == "tools/list":
            payload: Any = {"tools": [{"name": "lookup", "inputSchema": {}}]}
        else:
            payload = {"structuredContent": {"result": result if result is not None else []}}
        return {"jsonrpc": "2.0", "id": answer_id, "result": payload}

    def answer_with_id(self, answer_id: Any) -> Answer:
        return lambda body: FakeResponse(self.rpc_result(body, answer_id))

    def timed_out(self) -> Answer:
        def step(body: dict[str, Any]) -> Exception:
            return e2e.httpx.ReadTimeout("timed out")

        return step

    def __call__(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": dict(headers), "timeout": timeout})
        method = json.get("method")
        if method == "initialize":
            return FakeResponse(
                {"jsonrpc": "2.0", "id": json["id"], "result": {}}, headers={"mcp-session-id": "s-1"}
            )
        if method == "notifications/initialized":
            return FakeResponse(None, status_code=202)
        assert self.steps, f"no scripted answer left for {method}"
        outcome = self.steps.pop(0)(json)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def rpc_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["json"].get("method") in ("tools/call", "tools/list")]

    def sent_ids(self) -> list[int]:
        return [c["json"]["id"] for c in self.rpc_calls()]


@pytest.fixture()
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(e2e.httpx, "post", fake)
    monkeypatch.setattr(e2e, "BASE_URL", URL)
    return fake


def _session(**kwargs: Any) -> Any:
    return e2e._McpSession(**kwargs)


def test_call_tool_ids_increase_monotonically_across_calls_and_tools(http: FakeHttp) -> None:
    http.steps = [http.echo(), http.echo(), http.echo(), http.echo()]
    session = _session()
    for tool in ("search_symbols", "lookup", "find_examples", "search_symbols"):
        session.call_tool(tool, {"query": "x"})
    assert http.sent_ids() == [1, 2, 3, 4]


def test_an_explicit_request_id_is_still_honoured(http: FakeHttp) -> None:
    http.steps = [http.echo(), http.echo()]
    session = _session()
    session.call_tool("lookup", {"query": "x"}, request_id=7)
    session.call_tool("lookup", {"query": "x"})
    assert http.sent_ids() == [7, 1]


def test_a_timed_out_call_cannot_poison_the_next_one(http: FakeHttp) -> None:
    session = _session()
    http.steps = [http.timed_out()]
    with pytest.raises(e2e.httpx.ReadTimeout):
        session.call_tool("find_examples", {"query": "x"})
    # The late answer to call 1 arrives as the answer to call 2: it must be refused, not believed.
    http.steps = [http.answer_with_id(1)]
    with pytest.raises(AssertionError, match="does not answer request id 2"):
        session.call_tool("lookup", {"query": "x"})
    http.steps = [http.echo(["fine"])]
    body = session.call_tool("lookup", {"query": "x"})
    assert body["result"]["structuredContent"]["result"] == ["fine"]
    assert http.sent_ids() == [1, 2, 3]


def test_a_response_id_mismatch_is_rejected(http: FakeHttp) -> None:
    session = _session()
    for wrong in (99, None, "1", 0):
        http.steps = [http.answer_with_id(wrong)]
        with pytest.raises(AssertionError, match="JSON-RPC response id"):
            session.call_tool("lookup", {"query": "x"})
    http.steps = [http.answer_with_id(5)]
    with pytest.raises(AssertionError, match="does not answer request id 3"):
        session.call_tool("lookup", {"query": "x"}, request_id=3)


def test_the_default_timeout_is_fifteen_seconds_for_every_tool_including_find_examples(
    http: FakeHttp,
) -> None:
    assert e2e.DEFAULT_CALL_TIMEOUT_SECONDS == 15.0
    http.steps = [http.echo() for _ in pwp.TOOL_NAMES]
    session = _session()
    for tool in pwp.TOOL_NAMES:
        session.call_tool(tool, {})
    assert [c["timeout"] for c in http.rpc_calls()] == [15.0] * len(pwp.TOOL_NAMES)
    assert "find_examples" in pwp.TOOL_NAMES


def test_a_tool_timeout_applies_only_to_that_tool(http: FakeHttp) -> None:
    http.steps = [http.echo(), http.echo(), http.echo()]
    session = _session(call_timeout=20, tool_timeouts={"find_examples": 90})
    session.call_tool("search_symbols", {"query": "x"})
    session.call_tool("find_examples", {"query": "x"})
    session.call_tool("lookup", {"query": "x"})
    assert [c["timeout"] for c in http.rpc_calls()] == [20, 90, 20]


def test_an_explicit_timeout_beats_the_tool_timeout(http: FakeHttp) -> None:
    http.steps = [http.echo(), http.echo()]
    session = _session(tool_timeouts={"find_examples": 90})
    session.call_tool("find_examples", {"query": "x"}, timeout=5)
    session.call_tool("find_examples", {"query": "x"})
    assert [c["timeout"] for c in http.rpc_calls()] == [5, 90]


def test_live_client_rpc_uses_fresh_ids_and_checks_the_answer(http: FakeHttp) -> None:
    client = pwp.LiveMcpClient(e2e, URL)
    http.steps = [http.echo(), http.echo(["a"]), http.echo()]
    assert client.list_tools() == [{"name": "lookup", "inputSchema": {}}]
    assert client.invoke("lookup", {"query": "x"}) == ["a"]
    client.list_tools()
    assert http.sent_ids() == [1, 2, 3]
    # A stale answer to an earlier request is refused by the wrapper's own request path.
    http.steps = [http.answer_with_id(1)]
    with pytest.raises(RuntimeError, match=r"tools/list response id 1 does not answer request id 4"):
        client.list_tools()
    # ... and by the session path that invoke uses.
    http.steps = [http.answer_with_id(1)]
    with pytest.raises(AssertionError, match="does not answer request id 5"):
        client.invoke("lookup", {"query": "x"})


def test_live_client_passes_the_timeouts_to_the_session(http: FakeHttp) -> None:
    client = pwp.LiveMcpClient(e2e, URL, call_timeout=30, tool_timeouts={"find_examples": 90})
    http.steps = [http.echo(), http.echo(), http.echo()]
    client.list_tools()
    client.invoke("find_examples", {"query": "x"})
    client.invoke("lookup", {"query": "x"})
    assert [c["timeout"] for c in http.rpc_calls()] == [30, 90, 30]
    default = pwp.LiveMcpClient(e2e, URL)
    http.steps = [http.echo()]
    default.list_tools()
    assert http.rpc_calls()[-1]["timeout"] == 15.0


def test_parse_args_defaults_leave_every_timeout_at_fifteen_and_min_content_off() -> None:
    base = ["--pilot", "p_q", "--url", URL, "--fixtures", "f", "--report", "r.json"]
    args = pwp.parse_args(base)
    assert args.call_timeout == 15.0
    assert args.find_examples_timeout is None
    assert args.min_content is False
    given = pwp.parse_args([*base, "--call-timeout", "30", "--find-examples-timeout", "90", "--min-content"])
    assert (given.call_timeout, given.find_examples_timeout, given.min_content) == (30.0, 90.0, True)


def _write_fixtures(root: Path) -> None:
    for pilot in ("cells_rust", "pdf_net"):
        (root / pilot).mkdir(parents=True)
        (root / pilot / "api_surface.json").write_text(
            json.dumps(
                {
                    "source_commit": "a" * 40,
                    "types": [{"name": "Workbook", "kind": "struct_item", "methods": [{"name": "new"}]}],
                }
            ),
            encoding="utf-8",
        )


class _DeadClient:
    def handshake(self) -> dict[str, Any]:
        raise RuntimeError("dead")

    def list_tools(self) -> list[dict[str, Any]]:
        raise RuntimeError("dead")

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        raise RuntimeError("dead")


def test_main_builds_the_default_client_with_the_flag_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixtures(tmp_path / "fixtures")
    recorded: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def recorder(*args: Any, **kwargs: Any) -> _DeadClient:
        recorded.append((args, kwargs))
        return _DeadClient()

    monkeypatch.setattr(pwp, "open_live_client", recorder)
    common = ["--pilot", "cells_rust", "--url", URL + "/", "--fixtures", str(tmp_path / "fixtures")]
    pwp.main(
        [
            *common,
            "--report",
            str(tmp_path / "a.json"),
            "--call-timeout",
            "30",
            "--find-examples-timeout",
            "90",
        ]
    )
    pwp.main([*common, "--report", str(tmp_path / "b.json")])
    assert recorded[0] == ((URL,), {"call_timeout": 30.0, "tool_timeouts": {"find_examples": 90.0}})
    assert recorded[1] == ((URL,), {"call_timeout": 15.0, "tool_timeouts": None})


def test_an_injected_client_factory_is_still_called_with_the_url_only(tmp_path: Path) -> None:
    _write_fixtures(tmp_path / "fixtures")
    recorded: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def factory(*args: Any, **kwargs: Any) -> _DeadClient:
        recorded.append((args, kwargs))
        return _DeadClient()

    pwp.main(
        [
            "--pilot",
            "cells_rust",
            "--url",
            URL,
            "--fixtures",
            str(tmp_path / "fixtures"),
            "--report",
            str(tmp_path / "r.json"),
            "--call-timeout",
            "30",
            "--find-examples-timeout",
            "90",
            "--min-content",
        ],
        client_factory=factory,
    )
    assert recorded == [((URL,), {})]
