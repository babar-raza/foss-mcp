"""G3/TC-509 (REQ-G3-008, PS-10): every tool-call log line says which pilot, which code and hit or miss.

Before this card the per-call line had the tool name, outcome, latency and a correlation id but no pilot,
no error code and no hit or miss, so a 51 percent ``search_symbols`` miss rate was invisible to an operator,
and an unknown tool's client-chosen name was logged unbounded. The log line, the usage event and the metrics
observation now come from one place in the dispatcher (``_finish``); these tests pin that all three agree on
every exit path and that a failure in the accounting never changes what the client receives.

Offline and deterministic. Dispatch goes through ``server.get_request_handler("tools/call")`` exactly as
``tests/mcp/test_dispatch_offload.py`` does, over a tmp copy of the baked ``pdf/go`` scope.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import _result_hit, create_server
from foss_mcp.mcp.sidecar_state import SIDECAR_FETCH_FAILED
from foss_mcp.telemetry.metrics import MetricsRegistry
from foss_mcp.telemetry.usage_recorder import EVENT_FIELDS, UsageRecorder

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402

BAKED_PDF_GO = REPO_ROOT / "infra" / "demo-manifests" / "pdf__go__self_extracted"
CONFIG = DeploymentConfig(family="pdf", platform="go")
GUARD_SECONDS = 60
LOGGER_NAME = "foss_mcp.mcp.server"


class RecordingRegistry(MetricsRegistry):
    """A real registry that also remembers what it was asked to observe."""

    def __init__(self) -> None:
        super().__init__(family="pdf", platform="go")
        self.observed: list[dict[str, Any]] = []

    def observe_tool_call(self, **kwargs: Any) -> None:
        self.observed.append(kwargs)
        super().observe_tool_call(**kwargs)


class FailingRegistry(MetricsRegistry):
    def observe_tool_call(self, **kwargs: Any) -> None:
        raise RuntimeError("metrics are broken")


def _call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Dispatch exactly as the transport does: through the server's registered tools/call handler."""
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


def _baked_store(tmp_path: Path) -> GenerationManifestStore:
    root = tmp_path / "manifests"
    shutil.copytree(BAKED_PDF_GO, root / "pdf__go__self_extracted")
    return GenerationManifestStore(root)


def _dangling_pointer_store(tmp_path: Path) -> GenerationManifestStore:
    scope_dir = tmp_path / "dangling" / "pdf__go__self_extracted"
    scope_dir.mkdir(parents=True)
    (scope_dir / "active_pointer.json").write_text(
        json.dumps({"generation_id": "pdf::go::self_extracted::20260101T000000Z-deadbeef"}), encoding="utf-8"
    )
    return GenerationManifestStore(tmp_path / "dangling")


def _server(store: GenerationManifestStore, **kwargs: Any) -> Any:
    return create_server(CONFIG, store, **kwargs)


def _call_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage().startswith("tool call: ")]


def _logged_call(
    caplog: pytest.LogCaptureFixture, server: Any, name: str, arguments: dict[str, Any]
) -> tuple[CallToolResult, logging.LogRecord]:
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = _call(server, name, arguments)
    records = _call_records(caplog)
    assert len(records) == 1, [record.getMessage() for record in caplog.records]
    return result, records[0]


def test_a_hit_logs_family_platform_code_ok_and_hit_true(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    result, record = _logged_call(
        caplog, _server(_baked_store(tmp_path)), "search_symbols", {"query": "Document"}
    )
    assert result.is_error is False
    assert record.family == "pdf"  # type: ignore[attr-defined]
    assert record.platform == "go"  # type: ignore[attr-defined]
    assert record.code == "ok"  # type: ignore[attr-defined]
    assert record.hit is True  # type: ignore[attr-defined]
    assert record.outcome == "success"  # type: ignore[attr-defined]


def test_a_miss_logs_hit_false(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    server = _server(_baked_store(tmp_path))
    result, record = _logged_call(caplog, server, "search_symbols", {"query": "zzqxjvwk"})
    assert result.is_error is False  # an honest miss is still a successful answer
    assert record.hit is False  # type: ignore[attr-defined]
    assert record.code == "ok"  # type: ignore[attr-defined]
    _, other = _logged_call(caplog, server, "get_symbol", {"fqn": "No.Such.Symbol"})
    assert other.hit is False  # type: ignore[attr-defined]


def test_an_invalid_argument_logs_its_code_and_hit_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    result, record = _logged_call(caplog, _server(_baked_store(tmp_path)), "search_symbols", {"query": ""})
    assert result.is_error is True
    assert record.code == "invalid_argument"  # type: ignore[attr-defined]
    assert record.hit is None  # type: ignore[attr-defined]
    assert record.outcome == "error"  # type: ignore[attr-defined]


def test_an_unknown_tool_logs_not_found_with_a_capped_tool_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    hostile = "y" * 300
    result, record = _logged_call(caplog, _server(_baked_store(tmp_path)), hostile, {})
    assert result.is_error is True
    assert record.code == "not_found"  # type: ignore[attr-defined]
    assert record.hit is None  # type: ignore[attr-defined]
    assert len(record.tool) <= 64  # type: ignore[attr-defined]
    assert record.tool == hostile[:64]  # type: ignore[attr-defined]
    assert hostile not in record.getMessage()


def test_an_unavailable_error_logs_the_unavailable_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    result, record = _logged_call(
        caplog, _server(_dangling_pointer_store(tmp_path)), "search_symbols", {"query": "Document"}
    )
    assert result.is_error is True
    assert record.code == "unavailable"  # type: ignore[attr-defined]
    assert record.hit is None  # type: ignore[attr-defined]


def test_a_non_retrieval_tool_logs_hit_none(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    server = _server(_baked_store(tmp_path))
    for name, arguments in (
        ("list_recent_changes", {}),
        ("get_product_reference", {"section": "license"}),
        ("report_index_freshness", {}),
    ):
        result, record = _logged_call(caplog, server, name, arguments)
        assert result.is_error is False
        assert record.code == "ok"  # type: ignore[attr-defined]
        assert record.hit is None, name  # type: ignore[attr-defined]


def test_the_message_and_existing_extras_are_unchanged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _, record = _logged_call(caplog, _server(_baked_store(tmp_path)), "search_symbols", {"query": "Document"})
    assert record.getMessage() == "tool call: search_symbols (success)"
    assert record.name == LOGGER_NAME
    assert record.levelno == logging.INFO
    assert record.tool == "search_symbols"  # type: ignore[attr-defined]
    assert re.fullmatch(r"[0-9a-f]{32}", record.correlation_id)  # type: ignore[attr-defined]
    assert record.outcome == "success"  # type: ignore[attr-defined]
    assert isinstance(record.latency_ms, float) and record.latency_ms >= 0  # type: ignore[attr-defined]


def test_the_json_formatter_emits_the_new_fields(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    server = _server(_baked_store(tmp_path))
    formatter = serve_http.JsonLogFormatter()
    _, hit = _logged_call(caplog, server, "search_symbols", {"query": "Document"})
    _, miss = _logged_call(caplog, server, "search_symbols", {"query": "zzqxjvwk"})
    _, other = _logged_call(caplog, server, "list_recent_changes", {})
    hit_line, miss_line, other_line = (json.loads(formatter.format(record)) for record in (hit, miss, other))
    assert hit_line["family"] == "pdf" and hit_line["platform"] == "go"
    assert hit_line["code"] == "ok" and hit_line["hit"] is True
    assert miss_line["hit"] is False
    assert "hit" in other_line and other_line["hit"] is None
    for line in (hit_line, miss_line, other_line):
        assert line["tool"] and line["outcome"] == "success" and line["correlation_id"]


@pytest.fixture
def broken_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make search_symbols raise a ValueError, get_symbol an unexpected error; the other tools are real."""

    def bad_value(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("a caller-correctable problem")

    def bad_internal(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("something broke")

    monkeypatch.setattr(server_module, "search_symbols", bad_value)
    monkeypatch.setattr(server_module, "get_symbol", bad_internal)


def test_metrics_and_log_agree_on_outcome_code_and_miss_for_each_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, broken_handlers: None
) -> None:
    store = _baked_store(tmp_path)
    cases: list[tuple[Any, str, dict[str, Any], RecordingRegistry]] = []
    for kwargs, name, arguments in (
        ({}, "list_recent_changes", {}),
        ({}, "not_a_tool", {}),
        ({}, "search_docs", {"query": ""}),
        ({}, "search_symbols", {"query": "Document"}),  # ValueError from the handler
        ({}, "get_symbol", {"fqn": "Document"}),  # unexpected error from the handler
        ({"recent_releases_state": SIDECAR_FETCH_FAILED}, "list_recent_changes", {}),
        ({}, "lookup", {"query": "zzqxjvwk"}),  # a real miss
        ({}, "find_examples", {"query": "Document"}),
    ):
        registry = RecordingRegistry()
        cases.append((_server(store, metrics=registry, **kwargs), name, arguments, registry))
    dangling = RecordingRegistry()
    cases.append(
        (
            _server(_dangling_pointer_store(tmp_path), metrics=dangling),
            "lookup",
            {"query": "Document"},
            dangling,
        )
    )
    expected_codes = [
        "ok",
        "not_found",
        "invalid_argument",
        "invalid_argument",
        "internal",
        "unavailable",
        "ok",
        "ok",
        "unavailable",
    ]
    for (server, name, arguments, registry), code in zip(cases, expected_codes, strict=True):
        _, record = _logged_call(caplog, server, name, arguments)
        assert len(registry.observed) == 1, name
        observed = registry.observed[0]
        assert observed["code"] == record.code == code, name  # type: ignore[attr-defined]
        assert observed["outcome"] == record.outcome, name  # type: ignore[attr-defined]
        assert observed["miss"] is (record.hit is False), name  # type: ignore[attr-defined]
        assert observed["tool"] == (name if name != "not_a_tool" else "unknown")
        assert observed["latency_seconds"] >= 0
    assert cases[6][3].observed[0]["miss"] is True


def test_a_usage_event_is_recorded_on_every_exit_path(tmp_path: Path, broken_handlers: None) -> None:
    store = _baked_store(tmp_path)
    plan = [
        ({}, store, "list_recent_changes", {}, "success"),
        ({}, store, "not_a_tool", {}, "error"),
        ({}, store, "search_docs", {"query": ""}, "error"),
        ({}, store, "search_symbols", {"query": "Document"}, "error"),
        ({}, store, "get_symbol", {"fqn": "Document"}, "error"),
        ({"recent_releases_state": SIDECAR_FETCH_FAILED}, store, "list_recent_changes", {}, "error"),
        ({}, _dangling_pointer_store(tmp_path), "lookup", {"query": "Document"}, "error"),
    ]
    for kwargs, plan_store, name, arguments, outcome in plan:
        recorder = UsageRecorder()
        _call(_server(plan_store, usage_recorder=recorder, **kwargs), name, arguments)
        assert len(recorder) == 1, name
        (event,) = recorder.drain()
        assert event.tool_name == name
        assert event.outcome == outcome
        assert event.deployment_id == "pdf::go"
        assert tuple(event.to_dict()) == EVENT_FIELDS


def test_a_failing_metrics_registry_never_changes_the_tool_result(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _baked_store(tmp_path)
    healthy = _server(store, metrics=MetricsRegistry(family="pdf", platform="go"))
    broken = _server(store, metrics=FailingRegistry(family="pdf", platform="go"))
    for name, arguments in (
        ("search_symbols", {"query": "Document"}),
        ("search_symbols", {"query": "zzqxjvwk"}),
        ("list_recent_changes", {}),
    ):
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            expected, actual = _call(healthy, name, arguments), _call(broken, name, arguments)
        # TC-564: a success carries a fresh per-call `_meta.correlation_id`, so two calls differ in it alone; the
        # results are compared without it (and each must carry one).
        assert (
            actual.meta and expected.meta and actual.meta["correlation_id"] != expected.meta["correlation_id"]
        )
        assert actual.model_copy(update={"meta": None}) == expected.model_copy(update={"meta": None})
    for name, arguments in (("search_symbols", {"query": ""}), ("not_a_tool", {})):
        expected, actual = _call(healthy, name, arguments), _call(broken, name, arguments)
        assert actual.is_error is True
        assert actual.content[0].text == expected.content[0].text
        assert actual.structured_content["code"] == expected.structured_content["code"]
        assert actual.structured_content["correlation_id"]
    assert any("failed to record tool metrics" in record.getMessage() for record in caplog.records)
    assert "metrics are broken" not in caplog.text


class Miss:
    pass


class NotFound:
    found = False


class NoExampleFound:
    found = False


class SomethingElse:
    found = False


class Found:
    found = True


@pytest.mark.parametrize(
    ("tool", "result", "expected"),
    [
        ("search_symbols", Miss(), False),
        ("get_symbol", NotFound(), False),
        ("list_members", NotFound(), False),
        ("find_examples", NoExampleFound(), False),
        ("lookup", SomethingElse(), False),
        ("lookup", [], True),
        ("search_docs", [object()], True),
        ("get_symbol", Found(), True),
        ("list_recent_changes", [], None),
        ("report_index_freshness", Miss(), None),
        ("get_product_reference", NotFound(), None),
    ],
)
def test_result_hit_classification(tool: str, result: Any, expected: bool | None) -> None:
    assert _result_hit(tool, result) is expected
