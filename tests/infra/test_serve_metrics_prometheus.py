"""G3/TC-509 (REQ-G3-008): ``/metrics`` answers the Prometheus text exposition to a scraper that asks for
it, and the EXISTING two-field JSON to everything else.

Offline: the real ``serve_http.build_app`` over a tmp copy of the baked ``pdf/go`` generation, driven
through the SDK's own ASGI app with ``starlette.testclient.TestClient`` and a real MCP session (initialise,
acknowledge, ``tools/call``) the way ``tests/mcp/test_server_wiring.py`` does it. Nothing is mocked except
where a test says so.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig, resolve_scope
from foss_mcp.mcp.server import server_version
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from foss_mcp.telemetry.metrics import CONTENT_TYPE

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402

BAKED_PDF_GO = REPO_ROOT / "infra" / "demo-manifests" / "pdf__go__self_extracted"
CONFIG = DeploymentConfig(family="pdf", platform="go")
CONSTANTS = 'family="pdf",platform="go"'
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Origin": "https://example.com",
    "MCP-Protocol-Version": "2025-06-18",
}
SCRAPE = {"Accept": "text/plain;version=0.0.4;q=0.5,*/*;q=0.1"}
CANARY = "CANARY-7f3a9c-query"


class _McpSession:
    """A real MCP-over-HTTP session: initialise, acknowledge, then whatever the test sends."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "metrics-test", "version": "0.0.1"},
                },
            },
            headers=MCP_HEADERS,
        )
        assert response.status_code == 200, response.text
        self.headers = dict(MCP_HEADERS, **{"mcp-session-id": response.headers["mcp-session-id"]})
        client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=self.headers
        )
        self._next_id = 1

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        response = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=self.headers,
        )
        assert response.status_code == 200, response.text
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())["result"]
        return response.json()["result"]


def _baked_store(tmp_path: Path) -> GenerationManifestStore:
    root = tmp_path / "manifests"
    shutil.copytree(BAKED_PDF_GO, root / "pdf__go__self_extracted")
    return GenerationManifestStore(root)


def _empty_store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "empty-manifests")


def _dangling_pointer_store(tmp_path: Path) -> GenerationManifestStore:
    scope_dir = tmp_path / "dangling" / "pdf__go__self_extracted"
    scope_dir.mkdir(parents=True)
    (scope_dir / "active_pointer.json").write_text(
        json.dumps({"generation_id": "pdf::go::self_extracted::20260101T000000Z-deadbeef"}), encoding="utf-8"
    )
    return GenerationManifestStore(tmp_path / "dangling")


def _app(tmp_path: Path, store: GenerationManifestStore, **kwargs: Any) -> Any:
    return serve_http.build_app(
        CONFIG,
        manifest_store=store,
        allowed_origins=["https://example.com"],
        manifests_dir=tmp_path / "no-sidecars",
        **kwargs,
    )


def _scrape(client: TestClient) -> str:
    response = client.get("/metrics", headers=SCRAPE)
    assert response.status_code == 200
    return response.text


def _samples(text: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            series, value = line.rsplit(" ", 1)
            found[series] = float(value)
    return found


def _calls(tool: str, outcome: str, code: str) -> str:
    return f'foss_mcp_tool_calls_total{{{CONSTANTS},tool="{tool}",outcome="{outcome}",code="{code}"}}'


def test_a_default_scrape_is_still_the_two_field_json(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        for response in (client.get("/metrics"), client.get("/metrics", headers={"Accept": "*/*"})):
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/json")
            assert response.json() == {"usage_events_queued": 0, "usage_events_dropped": 0}


def test_accept_text_plain_returns_the_prometheus_exposition_with_its_content_type(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        response = client.get("/metrics", headers={"Accept": "text/plain"})
    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE
    assert response.text.startswith("# HELP ")
    assert response.text.endswith("\n")
    assert "# TYPE foss_mcp_tool_calls_total counter" in response.text


def test_accept_openmetrics_returns_the_prometheus_exposition(tmp_path: Path) -> None:
    accept = "application/openmetrics-text;version=1.0.0,text/plain;version=0.0.4;q=0.5,*/*;q=0.1"
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        only_openmetrics = client.get("/metrics", headers={"Accept": "application/openmetrics-text"})
        prometheus_style = client.get("/metrics", headers={"Accept": accept})
    for response in (only_openmetrics, prometheus_style):
        assert response.status_code == 200
        assert response.headers["content-type"] == CONTENT_TYPE
        assert "# TYPE foss_mcp_open_sessions gauge" in response.text


def test_the_format_parameter_overrides_the_accept_header_both_ways(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        forced_json = client.get("/metrics?format=json", headers={"Accept": "text/plain"})
        forced_text = client.get("/metrics?format=prometheus", headers={"Accept": "application/json"})
        forced_text_from_star = client.get("/metrics?format=prometheus")
        unknown_format = client.get("/metrics?format=xml", headers={"Accept": "text/plain"})
    assert forced_json.json() == {"usage_events_queued": 0, "usage_events_dropped": 0}
    assert forced_text.headers["content-type"] == CONTENT_TYPE
    assert forced_text_from_star.headers["content-type"] == CONTENT_TYPE
    assert unknown_format.headers["content-type"].startswith("application/json")


def test_a_search_symbols_hit_is_counted_with_family_platform_tool_outcome_and_code(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        result = _McpSession(client).call("search_symbols", {"query": "Document"})
        samples = _samples(_scrape(client))
    assert result["isError"] is False
    assert samples[_calls("search_symbols", "success", "ok")] == 1


def test_a_miss_is_counted_in_misses_total_and_a_hit_is_not(tmp_path: Path) -> None:
    misses = f'foss_mcp_tool_misses_total{{{CONSTANTS},tool="search_symbols"}}'
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        session = _McpSession(client)
        assert session.call("search_symbols", {"query": "Document"})["isError"] is False
        assert misses not in _samples(_scrape(client))
        assert session.call("search_symbols", {"query": "zzqxjvwk"})["isError"] is False
        samples = _samples(_scrape(client))
    assert samples[misses] == 1
    assert samples[_calls("search_symbols", "success", "ok")] == 2


def test_an_invalid_argument_error_has_the_invalid_argument_code(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        result = _McpSession(client).call("search_symbols", {"query": ""})
        samples = _samples(_scrape(client))
    assert result["isError"] is True
    assert samples[_calls("search_symbols", "error", "invalid_argument")] == 1
    assert not any(series.startswith("foss_mcp_tool_misses_total{") for series in samples)


def test_an_unregistered_tool_name_collapses_to_the_unknown_label(tmp_path: Path) -> None:
    hostile = "x" * 200
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        result = _McpSession(client).call(hostile, {})
        text = _scrape(client)
    assert result["isError"] is True
    assert _samples(text)[_calls("unknown", "error", "not_found")] == 1
    assert hostile not in text
    assert "xxxxxxxxxx" not in text


def test_the_latency_histogram_count_equals_the_number_of_calls(tmp_path: Path) -> None:
    plan = [
        ("search_symbols", {"query": "Document"}),
        ("search_symbols", {"query": "zzqxjvwk"}),
        ("get_symbol", {"fqn": "No.Such.Symbol"}),
        ("list_recent_changes", {}),
        ("search_symbols", {"query": ""}),
        ("not_a_registered_tool", {}),
    ]
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        session = _McpSession(client)
        for name, arguments in plan:
            session.call(name, arguments)
        samples = _samples(_scrape(client))
    counted = sum(
        value
        for series, value in samples.items()
        if series.startswith("foss_mcp_tool_latency_seconds_count{")
    )
    total = sum(value for series, value in samples.items() if series.startswith("foss_mcp_tool_calls_total{"))
    assert counted == total == len(plan)
    inf = sum(value for series, value in samples.items() if 'le="+Inf"' in series)
    assert inf == len(plan)


def test_open_sessions_tracks_real_sessions_against_the_real_sdk(tmp_path: Path) -> None:
    series = f"foss_mcp_open_sessions{{{CONSTANTS}}}"
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        assert _samples(_scrape(client))[series] == 0
        _McpSession(client)
        assert _samples(_scrape(client))[series] == 1
        _McpSession(client)
        assert _samples(_scrape(client))[series] == 2


def test_generation_info_carries_generation_id_source_commit_and_build_revision(tmp_path: Path) -> None:
    store = _baked_store(tmp_path)
    expected_id = json.loads((BAKED_PDF_GO / "active_pointer.json").read_text(encoding="utf-8"))[
        "generation_id"
    ]
    freshness = report_index_freshness(store, resolve_scope(CONFIG, request=None), "self_extracted", None)
    expected_commit = freshness.indexed_source_commit or "unknown"
    expected_revision = server_version().partition("+")[2]
    with TestClient(_app(tmp_path, store)) as client:
        text = _scrape(client)
    line = next(row for row in text.splitlines() if row.startswith("foss_mcp_generation_info{"))
    labels = dict(re.findall(r'([a-z_]+)="([^"]*)"', line.rsplit(" ", 1)[0]))
    assert labels == {
        "family": "pdf",
        "platform": "go",
        "generation_id": expected_id,
        "source_commit": expected_commit,
        "build_revision": expected_revision,
    }
    assert line.endswith(" 1")


def test_ready_is_one_for_a_ready_generation_and_zero_for_an_empty_store(tmp_path: Path) -> None:
    series = f"foss_mcp_ready{{{CONSTANTS}}}"
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        assert _samples(_scrape(client))[series] == 1
    with TestClient(_app(tmp_path, _empty_store(tmp_path))) as client:
        empty = _scrape(client)
    assert _samples(empty)[series] == 0
    assert "foss_mcp_generation_info" not in empty


def test_a_tool_over_an_empty_store_is_counted_with_the_unavailable_code(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _dangling_pointer_store(tmp_path))) as client:
        result = _McpSession(client).call("search_symbols", {"query": "Document"})
        samples = _samples(_scrape(client))
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "unavailable"
    assert samples[_calls("search_symbols", "error", "unavailable")] == 1


def test_no_series_contains_the_query_text(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        session = _McpSession(client)
        session.call("search_symbols", {"query": CANARY})
        session.call("lookup", {"query": CANARY})
        session.call("get_symbol", {"fqn": CANARY})
        session.call("search_docs", {"query": CANARY, "content_type": "no-such-type"})
        session.call(CANARY, {"query": CANARY})
        text = _scrape(client)
    assert CANARY not in text
    assert CANARY.lower() not in text.lower()


def test_the_legacy_usage_numbers_are_also_exposed(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        session = _McpSession(client)
        for _ in range(3):
            session.call("search_symbols", {"query": "Document"})
        legacy = client.get("/metrics").json()
        samples = _samples(_scrape(client))
    assert samples[f"foss_mcp_usage_events_queued{{{CONSTANTS}}}"] == legacy["usage_events_queued"] == 3
    assert (
        samples[f"foss_mcp_usage_events_dropped_total{{{CONSTANTS}}}"] == legacy["usage_events_dropped"] == 0
    )


def test_the_scrape_needs_no_origin_or_protocol_headers(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _baked_store(tmp_path))) as client:
        response = client.get("/metrics", headers={"Accept": "text/plain"})
    assert response.status_code == 200
    assert "origin" not in {key.lower() for key in response.request.headers}
    assert "mcp-protocol-version" not in {key.lower() for key in response.request.headers}
    assert response.headers["content-type"] == CONTENT_TYPE


def test_build_app_default_recorder_is_a_ring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = serve_http.UsageRecorder
    built: list[dict[str, Any]] = []

    class SpyRecorder(real):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(serve_http, "UsageRecorder", SpyRecorder)
    store = _baked_store(tmp_path)
    _app(tmp_path, store)
    assert built == [{"overflow": "drop_oldest"}]
    caller_recorder = real(max_queued=3)
    _app(tmp_path, store, usage_recorder=caller_recorder)
    assert len(built) == 1  # a recorder the caller supplies is used as it is


def test_serve_http_and_server_source_wire_the_registry() -> None:
    """The wiring proof AGENTS.md requires: the production entrypoints reference the registry."""
    http_source = (REPO_ROOT / "infra" / "serve_http.py").read_text(encoding="utf-8")
    server_source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "MetricsRegistry" in http_source
    assert "MetricsRegistry" in server_source
    assert "metrics=resolved_metrics" in http_source
    assert "_metrics_route(resolved_usage_recorder, resolved_metrics" in http_source
    assert "SessionCapObserverMiddleware(inner, on_reject)" in http_source
    assert "metrics.observe_tool_call(" in server_source
