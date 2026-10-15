"""G3/TC-509 (REQ-G3-008, PS-04): a session refused at the cap is no longer silent.

The SDK answers a new session over the cap with a bare HTTP 503 ("Too many open sessions"). That answer
had no ``Retry-After``, no counter and (apart from the SDK's own warning, which has no pilot) no log
record, so a flood of idle initialises was not distinguishable from a healthy pod. Offline: the real
``serve_http.build_app`` with ``max_session_count=1``, so the second initialise is refused by the real
SDK, driven through ``starlette.testclient.TestClient``.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402

BAKED_PDF_GO = REPO_ROOT / "infra" / "demo-manifests" / "pdf__go__self_extracted"
CONFIG = DeploymentConfig(family="pdf", platform="go")
CONSTANTS = 'family="pdf",platform="go"'
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Origin": "https://example.com",
    "MCP-Protocol-Version": "2025-06-18",
}
CAP_MESSAGE = "session cap reached, new session refused"


def _initialize(client: TestClient, headers: dict[str, str] | None = None) -> Any:
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "cap-test", "version": "0.0.1"},
            },
        },
        headers=HEADERS if headers is None else headers,
    )


def _app(tmp_path: Path, *, empty: bool = False) -> Any:
    root = tmp_path / "manifests"
    if not empty:
        shutil.copytree(BAKED_PDF_GO, root / "pdf__go__self_extracted")
    return serve_http.build_app(
        CONFIG,
        manifest_store=GenerationManifestStore(root),
        allowed_origins=["https://example.com"],
        manifests_dir=tmp_path / "no-sidecars",
        max_session_count=1,
    )


def _full_client(tmp_path: Path) -> TestClient:
    """A client whose single allowed session is already open (its id is on ``client.session_id``)."""
    client = TestClient(_app(tmp_path))
    client.__enter__()
    first = _initialize(client)
    assert first.status_code == 200, first.text
    client.session_id = first.headers["mcp-session-id"]  # type: ignore[attr-defined]
    return client


def _rejection_count(client: TestClient) -> float:
    text = client.get("/metrics", headers={"Accept": "text/plain"}).text
    series = f"foss_mcp_session_rejections_total{{{CONSTANTS}}}"
    return next(float(row.rsplit(" ", 1)[1]) for row in text.splitlines() if row.startswith(series))


def _cap_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == CAP_MESSAGE]


def test_the_503_at_the_cap_carries_retry_after_5(tmp_path: Path) -> None:
    client = _full_client(tmp_path)
    try:
        refused = _initialize(client)
    finally:
        client.__exit__(None, None, None)
    assert refused.status_code == 503
    assert refused.headers["retry-after"] == "5"
    assert json.loads(refused.text)["error"]["message"] == "Too many open sessions"


def test_the_rejection_counter_increments_for_each_rejection(tmp_path: Path) -> None:
    client = _full_client(tmp_path)
    try:
        assert _rejection_count(client) == 0
        for expected in (1, 2, 3):
            assert _initialize(client).status_code == 503
            assert _rejection_count(client) == expected
    finally:
        client.__exit__(None, None, None)


def test_a_warning_with_family_platform_and_limits_is_logged_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serve_http, "_monotonic", lambda: 1000.0)
    client = _full_client(tmp_path)
    try:
        with caplog.at_level(logging.WARNING, logger="serve_http"):
            for _ in range(3):
                assert _initialize(client).status_code == 503
    finally:
        client.__exit__(None, None, None)
    records = _cap_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    assert (record.family, record.platform) == ("pdf", "go")  # type: ignore[attr-defined]
    assert record.max_sessions == 1  # type: ignore[attr-defined]
    assert record.open_sessions == 1  # type: ignore[attr-defined]
    assert record.suppressed == 0  # type: ignore[attr-defined]
    assert client.session_id not in json.dumps(record.__dict__, default=str)  # type: ignore[attr-defined]


def test_rejection_warnings_are_rate_limited_and_report_the_suppressed_count(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [100.0]
    monkeypatch.setattr(serve_http, "_monotonic", lambda: now[0])
    client = _full_client(tmp_path)
    try:
        with caplog.at_level(logging.WARNING, logger="serve_http"):
            for moment in (100.0, 101.0, 105.0, 109.9):
                now[0] = moment
                assert _initialize(client).status_code == 503
            assert len(_cap_records(caplog)) == 1
            now[0] = 110.0
            assert _initialize(client).status_code == 503
            now[0] = 111.0
            assert _initialize(client).status_code == 503
            now[0] = 125.0
            assert _initialize(client).status_code == 503
        assert _rejection_count(client) == 7
    finally:
        client.__exit__(None, None, None)
    records = _cap_records(caplog)
    assert [record.suppressed for record in records] == [0, 3, 1]  # type: ignore[attr-defined]


def test_other_responses_have_no_retry_after(tmp_path: Path) -> None:
    client = _full_client(tmp_path)
    try:
        session_headers = dict(HEADERS, **{"mcp-session-id": client.session_id})  # type: ignore[attr-defined]
        unknown_session = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers=dict(HEADERS, **{"mcp-session-id": "0" * 32}),
        )
        bad_origin = _initialize(client, dict(HEADERS, Origin="https://evil.example"))
        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=session_headers,
        )
        probes = [client.get(path) for path in ("/healthz", "/readyz", "/metrics")]
        refusals_after = _rejection_count(client)
    finally:
        client.__exit__(None, None, None)
    assert unknown_session.status_code == 404
    assert bad_origin.status_code == 400
    assert listed.status_code == 200
    for response in (unknown_session, bad_origin, listed, *probes):
        assert "retry-after" not in response.headers
    assert refusals_after == 0


def test_a_not_ready_probe_is_not_a_session_refusal(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, empty=True)) as client:
        readyz = client.get("/readyz")
        assert readyz.status_code == 503
        assert "retry-after" not in readyz.headers
        assert _rejection_count(client) == 0


def test_the_cap_and_idle_constants_are_unchanged() -> None:
    assert serve_http.MAX_SESSIONS == 200
    assert serve_http.SESSION_IDLE_TIMEOUT_SECONDS == 300
    assert serve_http.SESSION_IDLE_ENV == "FOSS_MCP_SESSION_IDLE_SECONDS"
    assert serve_http.MAX_SESSIONS_ENV == "FOSS_MCP_MAX_SESSIONS"
    assert serve_http._REJECT_LOG_INTERVAL_SECONDS == 10.0


def test_metrics_still_answers_at_the_cap(tmp_path: Path) -> None:
    client = _full_client(tmp_path)
    try:
        assert _initialize(client).status_code == 503
        as_json = client.get("/metrics")
        as_text = client.get("/metrics", headers={"Accept": "text/plain"})
    finally:
        client.__exit__(None, None, None)
    assert as_json.status_code == 200
    assert as_json.json() == {"usage_events_queued": 0, "usage_events_dropped": 0}
    assert as_text.status_code == 200
    assert f"foss_mcp_open_sessions{{{CONSTANTS}}} 1" in as_text.text
    assert f"foss_mcp_session_rejections_total{{{CONSTANTS}}} 1" in as_text.text


def test_the_retry_after_constant_value() -> None:
    assert serve_http.SESSION_CAP_RETRY_AFTER_SECONDS == 5
