"""G3/TC-564 (REQ-G3-009, RD3-120, RD3-124, RD3-125, RD3-126): the HTTP server reports what it cannot serve and
survives what peers do to it.

Round 3 found that an ABSENT sidecar was served as an empty, "complete" answer with no log line and a green
``/readyz``; that the default ``/metrics`` body was two counters; that call logs lacked the generation and
commit; that ``ClientDisconnect`` was an ERROR with a traceback; that 200 abandoned sessions locked out every
new client, stalled connections were never timed out, Host was never validated, an invalid request id got an
empty 202, and transport rejections used a different error shape from the SDK's own.

The tests start the REAL ``serve_http.build_app`` over a COPY of the committed ``pdf/go`` baked scope. Most use
Starlette's ``TestClient``; the transport tests start the real uvicorn server (the same ``uvicorn_config`` the
container uses, on an ephemeral loopback port that the test shuts down) and speak raw HTTP bytes over a socket.
Expectations are independent of the code under test: the release count is read from the baked sidecar file as
plain JSON, the generation id from the pointer file on disk, and every wire shape is asserted on the bytes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import ClientDisconnect
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import sidecar_state
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.sidecar_state import RELEASES_FETCH_FAILED_MESSAGE, RELEASES_NOT_LOADED_MESSAGE
from foss_mcp.mcp.transport_security import host_is_allowed

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402

BAKED_ROOT = REPO_ROOT / "infra" / "demo-manifests"
BAKED_SCOPE = BAKED_ROOT / "pdf__go__self_extracted"
CONFIG = DeploymentConfig(family="pdf", platform="go")
CONSTANTS = 'family="pdf",platform="go"'
SIDECAR_NAMES = {
    "product_reference": "product_reference_pdf_go.json",
    "recent_releases": "recent_releases_pdf_go.json",
    "package_registry": "package_registry_pdf_go.json",
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "robustness-test", "version": "0.0.1"},
    },
}
SERVE_HTTP_LOGGER = "serve_http"


# --- helpers ---------------------------------------------------------------------------------------------


def make_roots(tmp_path: Path, *, sidecars: tuple[str, ...] = ()) -> tuple[Path, Path]:
    """A manifests root holding a copy of the baked pdf/go scope and, from the baked sidecars, only the named
    ones (keys of ``SIDECAR_NAMES``). Returns (root, root)."""
    root = tmp_path / "manifests"
    shutil.copytree(BAKED_SCOPE, root / "pdf__go__self_extracted")
    for kind in sidecars:
        shutil.copyfile(BAKED_ROOT / SIDECAR_NAMES[kind], root / SIDECAR_NAMES[kind])
    return root, root


def make_app(root: Path, **kwargs: Any) -> Any:
    options: dict[str, Any] = {"allowed_origins": ["https://example.com"], "allowed_hosts": []}
    options.update(kwargs)
    return serve_http.build_app(
        CONFIG, manifest_store=GenerationManifestStore(root), manifests_dir=root, **options
    )


def open_session(client: TestClient, headers: dict[str, str] | None = None) -> str:
    response = client.post("/mcp", json=INITIALIZE, headers={**HEADERS, **(headers or {})})
    assert response.status_code == 200, response.text
    session_id = response.headers["mcp-session-id"]
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**HEADERS, "mcp-session-id": session_id, **(headers or {})},
    )
    return session_id


def rpc(client: TestClient, session_id: str, method: str, params: dict[str, Any], request_id: int = 1) -> Any:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        headers={**HEADERS, "mcp-session-id": session_id},
    )
    assert response.status_code == 200, response.text
    data_line = next(line for line in response.text.splitlines() if line.startswith("data:"))
    return json.loads(data_line[len("data:") :])["result"]


def call_tool(client: TestClient, session_id: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
    return rpc(client, session_id, "tools/call", {"name": name, "arguments": arguments or {}})


def messages(caplog: pytest.LogCaptureFixture, text: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == text]


def metric(client: TestClient, series: str) -> float:
    for row in client.get("/metrics").text.splitlines():
        if row.startswith(series + " "):
            return float(row.rsplit(" ", 1)[1])
    raise AssertionError(f"{series} not exposed")


# --- (a) a missing sidecar is reported, never served as "no data" ---------------------------------------


def test_boot_without_the_required_sidecars_logs_a_warning_naming_each_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, _ = make_roots(tmp_path)
    with caplog.at_level(logging.INFO, logger=SERVE_HTTP_LOGGER):
        make_app(root)

    records = messages(caplog, "sidecar not loaded, the file was not found")
    assert {record.file for record in records} == {  # type: ignore[attr-defined]
        SIDECAR_NAMES["product_reference"],
        SIDECAR_NAMES["recent_releases"],
    }, "the package-registry sidecar is optional by design and is not warned about"
    assert all(record.levelno == logging.WARNING for record in records)
    assert all((record.family, record.platform) == ("pdf", "go") for record in records)  # type: ignore[attr-defined]
    assert str(tmp_path) not in json.dumps([record.__dict__ for record in records], default=str)


def test_boot_with_every_sidecar_present_logs_no_such_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with caplog.at_level(logging.INFO, logger=SERVE_HTTP_LOGGER):
        make_app(root)
    assert messages(caplog, "sidecar not loaded, the file was not found") == []


def test_readyz_carries_a_warning_per_missing_sidecar_and_stays_200(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path)
    with TestClient(make_app(root)) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["warnings"] == [
        f"product_reference sidecar not loaded: {SIDECAR_NAMES['product_reference']} was not found",
        f"recent_releases sidecar not loaded: {SIDECAR_NAMES['recent_releases']} was not found",
    ]


def test_readyz_has_no_sidecar_warning_when_the_sidecars_are_present(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as client:
        assert client.get("/readyz").json()["warnings"] == []


def test_metrics_count_the_sidecar_states(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=("package_registry",))
    with TestClient(make_app(root)) as client:
        state = lambda sidecar, value: metric(  # noqa: E731
            client, f'foss_mcp_sidecar_state{{{CONSTANTS},sidecar="{sidecar}",state="{value}"}}'
        )
        count = lambda value: metric(client, f'foss_mcp_sidecars{{{CONSTANTS},state="{value}"}}')  # noqa: E731
        assert state("recent_releases", "not_present") == 1
        assert state("recent_releases", "present") == 0
        assert state("product_reference", "not_present") == 1
        assert state("package_registry", "present") == 1
        assert count("not_present") == 2
        assert count("present") == 1
        assert count("fetch_failed") == 0


def test_a_missing_releases_sidecar_is_unavailable_through_the_real_transport_never_a_complete_answer(
    tmp_path: Path,
) -> None:
    root, _ = make_roots(tmp_path)
    with TestClient(make_app(root)) as client:
        session = open_session(client, {"Origin": "https://example.com"})
        result = call_tool(client, session, "list_recent_changes")

    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "unavailable"
    assert result["structuredContent"]["message"] == RELEASES_NOT_LOADED_MESSAGE
    assert "complete answer" not in json.dumps(result)


def test_a_sidecar_added_later_is_served_on_the_next_request_and_the_warning_clears(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, _ = make_roots(tmp_path, sidecars=("product_reference",))
    oracle_releases = json.loads((BAKED_ROOT / SIDECAR_NAMES["recent_releases"]).read_text(encoding="utf-8"))
    assert len(oracle_releases) == 12
    with TestClient(make_app(root)) as client:
        session = open_session(client)
        assert call_tool(client, session, "list_recent_changes")["isError"] is True
        assert client.get("/readyz").json()["warnings"] != []

        with caplog.at_level(logging.INFO, logger=SERVE_HTTP_LOGGER):
            shutil.copyfile(
                BAKED_ROOT / SIDECAR_NAMES["recent_releases"], root / SIDECAR_NAMES["recent_releases"]
            )
            result = call_tool(client, session, "list_recent_changes", {"limit": 100})

        assert result["isError"] is False
        served = [entry["tag_name"] for entry in result["structuredContent"]["result"]]
        # The response budget may omit the oldest entries of a long list; what is served is the newest first.
        assert len(served) >= 5
        assert served == [release["tag_name"] for release in oracle_releases][: len(served)]
        assert client.get("/readyz").json()["warnings"] == []
        assert (
            metric(client, f'foss_mcp_sidecar_state{{{CONSTANTS},sidecar="recent_releases",state="present"}}')
            == 1
        )

    loaded = messages(caplog, "sidecar loaded after startup")
    assert [record.sidecar for record in loaded] == ["recent_releases"]  # type: ignore[attr-defined]


def test_a_present_but_empty_releases_file_is_a_complete_answer_with_no_warning(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=("product_reference",))
    (root / SIDECAR_NAMES["recent_releases"]).write_text("[]", encoding="utf-8")
    with TestClient(make_app(root)) as client:
        session = open_session(client)
        result = call_tool(client, session, "list_recent_changes")
        warnings = client.get("/readyz").json()["warnings"]

    assert result["isError"] is False
    assert result["structuredContent"]["result"] == []
    assert "An empty list is a complete answer" in result["structuredContent"]["note"]
    assert warnings == []


def test_with_the_switch_off_the_server_is_exactly_as_silent_as_before(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_state, "SIDECAR_STATE_WARNINGS", False)
    root, _ = make_roots(tmp_path)
    with caplog.at_level(logging.INFO, logger=SERVE_HTTP_LOGGER):
        app = make_app(root)
    with TestClient(app) as client:
        session = open_session(client)
        result = call_tool(client, session, "list_recent_changes")
        readyz = client.get("/readyz").json()
        scrape = client.get("/metrics").text

    assert messages(caplog, "sidecar not loaded, the file was not found") == []
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == []
    assert "An empty list is a complete answer" in result["structuredContent"]["note"]
    assert readyz["warnings"] == []
    assert "foss_mcp_sidecar_state" not in scrape


@pytest.mark.parametrize(
    ("releases_file", "marker", "expected"),
    [
        (None, False, "not_present"),
        ("[]", False, "present"),
        ("not json {", False, "fetch_failed"),
        ('{"tag_name": "v1"}', False, "fetch_failed"),
        ('[{"tag_name": 1}]', False, "fetch_failed"),
        (None, True, "fetch_failed"),
        ("[]", True, "fetch_failed"),
        ("BAKED", False, "present"),
        ("BAKED", True, "present"),
    ],
    ids=[
        "absent",
        "empty-list",
        "not-json",
        "object-not-list",
        "wrong-entry-type",
        "marker-only",
        "empty-list-with-marker",
        "baked-12-releases",
        "baked-with-marker",
    ],
)
def test_sidecar_matrix_for_the_releases_file(
    tmp_path: Path, releases_file: str | None, marker: bool, expected: str
) -> None:
    root, _ = make_roots(tmp_path, sidecars=("product_reference",))
    target = root / SIDECAR_NAMES["recent_releases"]
    if releases_file == "BAKED":
        shutil.copyfile(BAKED_ROOT / SIDECAR_NAMES["recent_releases"], target)
    elif releases_file is not None:
        target.write_text(releases_file, encoding="utf-8")
    if marker:
        (root / (SIDECAR_NAMES["recent_releases"] + ".failed")).write_text(
            "rate limited 2026-10-17T04:33:13Z"
        )
    watcher = serve_http.SidecarWatcher(root, CONFIG)

    assert watcher.states()["recent_releases"] == expected
    with TestClient(make_app(root)) as client:
        session = open_session(client)
        result = call_tool(client, session, "list_recent_changes", {"limit": 100})
    if expected == "not_present":
        assert result["structuredContent"]["message"] == RELEASES_NOT_LOADED_MESSAGE
    elif expected == "fetch_failed":
        assert result["structuredContent"]["message"] == RELEASES_FETCH_FAILED_MESSAGE
    else:
        assert result["isError"] is False


@pytest.mark.parametrize(
    ("kind", "content", "expected"),
    [
        ("product_reference", "garbage", "fetch_failed"),
        ("product_reference", '{"state": "fetch_failed"}', "fetch_failed"),
        ("package_registry", "garbage", "fetch_failed"),
        ("package_registry", "BAKED", "present"),
    ],
    ids=["reference-corrupt", "reference-writer-failed", "registry-corrupt", "registry-baked"],
)
def test_sidecar_matrix_for_the_other_two_files(
    tmp_path: Path, kind: str, content: str, expected: str
) -> None:
    root, _ = make_roots(tmp_path, sidecars=("product_reference", "recent_releases"))
    target = root / SIDECAR_NAMES[kind]
    if content == "BAKED":
        shutil.copyfile(BAKED_ROOT / SIDECAR_NAMES[kind], target)
    else:
        target.write_text(content, encoding="utf-8")

    assert serve_http.SidecarWatcher(root, CONFIG).states()[kind] == expected


def test_the_install_notice_follows_the_active_generation_when_a_newer_sidecar_is_served(
    tmp_path: Path,
) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    oracle_commit = re.search(
        r"Source-Commit: ([0-9a-f]{40})",
        next(BAKED_SCOPE.glob("generation__*.json")).read_text(encoding="utf-8"),
    ).group(1)  # type: ignore[union-attr]
    reference = json.loads((root / SIDECAR_NAMES["product_reference"]).read_text(encoding="utf-8"))
    reference["source_ref"] = "e" * 40  # the sidecar a rollback leaves behind: a newer commit
    (root / SIDECAR_NAMES["product_reference"]).write_text(json.dumps(reference), encoding="utf-8")
    registry = json.loads((root / SIDECAR_NAMES["package_registry"]).read_text(encoding="utf-8"))
    registry["verified"] = False
    (root / SIDECAR_NAMES["package_registry"]).write_text(json.dumps(registry), encoding="utf-8")
    with TestClient(make_app(root)) as client:
        session = open_session(client)
        install = call_tool(client, session, "get_product_reference", {"section": "install"})

    text = json.dumps(install["structuredContent"]["result"])
    assert oracle_commit in text
    assert "e" * 40 not in text


# --- (d) observability -----------------------------------------------------------------------------------


def test_metrics_default_to_the_prometheus_text_and_json_needs_an_explicit_format(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as client:
        bare = client.get("/metrics")
        star = client.get("/metrics", headers={"Accept": "*/*"})
        json_accept = client.get("/metrics", headers={"Accept": "application/json"})
        explicit = client.get("/metrics?format=json")

    for response in (bare, star, json_accept):
        assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
        assert "# TYPE foss_mcp_tool_calls_total counter" in response.text
        assert f"foss_mcp_session_evictions_total{{{CONSTANTS}}} 0" in response.text
    assert explicit.headers["content-type"].startswith("application/json")
    assert explicit.json() == {"usage_events_queued": 0, "usage_events_dropped": 0}


def test_server_info_version_and_build_revision_come_from_the_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision = "89abcdef0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("FOSS_MCP_BUILD_REVISION", revision)
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as client:
        initialize = client.post("/mcp", json=INITIALIZE, headers=HEADERS)
        data_line = next(line for line in initialize.text.splitlines() if line.startswith("data:"))
        version = json.loads(data_line[5:])["result"]["serverInfo"]["version"]
        scrape = client.get("/metrics").text

    assert version == f"0.1.0+{revision[:12]}"
    assert f'build_revision="{revision[:12]}"' in scrape


def test_a_transport_rejection_is_logged_with_its_reason_and_kind(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as client, caplog.at_level(logging.WARNING, logger=SERVE_HTTP_LOGGER):
        origin = client.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Origin": "https://evil.example"})
        host = client.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Host": "evil.example.org"})
        version = client.post(
            "/mcp", json=INITIALIZE, headers={**HEADERS, "MCP-Protocol-Version": "1999-01-01"}
        )

    assert [origin.status_code, host.status_code, version.status_code] == [400, 421, 400]
    records = messages(caplog, "request rejected")
    assert [record.reject_kind for record in records] == ["origin", "host", "protocol_version"]  # type: ignore[attr-defined]
    assert "evil.example" in records[0].reason  # type: ignore[attr-defined]
    assert "evil.example.org" in records[1].reason  # type: ignore[attr-defined]
    assert records[0].status == 400 and records[1].status == 421  # type: ignore[attr-defined]
    assert all(record.path == "/mcp" and record.method == "POST" for record in records)  # type: ignore[attr-defined]
    assert all(record.family == "pdf" for record in records)  # type: ignore[attr-defined]


def test_rejection_logging_is_rate_limited_and_counts_what_it_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(serve_http, "_monotonic", lambda: clock[0])
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as client, caplog.at_level(logging.WARNING, logger=SERVE_HTTP_LOGGER):
        for _ in range(9):
            client.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Origin": "https://evil.example"})
        clock[0] += 11
        client.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Origin": "https://evil.example"})

    records = messages(caplog, "request rejected")
    assert len(records) == 6, "five in the first window, one in the next"
    assert records[-1].suppressed == 4  # type: ignore[attr-defined]


# --- a client that hangs up -----------------------------------------------------------------------------------


def _drive(middleware: Any, scope_extra: dict[str, Any] | None = None) -> None:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"mcp-session-id", b"abc123"), (b"host", b"localhost")],
        **(scope_extra or {}),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Any) -> None:
        return None

    asyncio.run(middleware(scope, receive, send))


def test_a_client_disconnect_is_one_debug_line_with_ids_and_never_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def hangs_up(scope: Any, receive: Any, send: Any) -> None:
        raise ClientDisconnect

    middleware = serve_http.ClientDisconnectMiddleware(hangs_up, CONFIG)
    with caplog.at_level(logging.DEBUG, logger=SERVE_HTTP_LOGGER):
        _drive(middleware)

    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    (record,) = messages(caplog, "client disconnected")
    assert record.levelno == logging.DEBUG
    assert (record.family, record.platform, record.method, record.path, record.session_id) == (  # type: ignore[attr-defined]
        "pdf",
        "go",
        "POST",
        "/mcp",
        "abc123",
    )
    assert record.exc_info is None


def test_a_group_of_disconnects_is_absorbed_but_any_other_error_still_propagates() -> None:
    async def group_of_disconnects(scope: Any, receive: Any, send: Any) -> None:
        raise BaseExceptionGroup("task group", [ClientDisconnect(), ClientDisconnect()])

    async def mixed(scope: Any, receive: Any, send: Any) -> None:
        raise BaseExceptionGroup("task group", [ClientDisconnect(), RuntimeError("real fault")])

    async def real_fault(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("real fault")

    _drive(serve_http.ClientDisconnectMiddleware(group_of_disconnects, CONFIG))
    with pytest.raises(BaseExceptionGroup):
        _drive(serve_http.ClientDisconnectMiddleware(mixed, CONFIG))
    with pytest.raises(RuntimeError, match="real fault"):
        _drive(serve_http.ClientDisconnectMiddleware(real_fault, CONFIG))


def test_the_sdk_logging_of_a_disconnect_is_downgraded_to_debug_with_the_request_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The SDK's POST handler catches ClientDisconnect itself and logs it at ERROR with a traceback."""
    serve_http.install_client_disconnect_log_filter()
    sdk_logger = logging.getLogger("mcp.server.streamable_http")

    async def sdk_handler_logs_the_exception(scope: Any, receive: Any, send: Any) -> None:
        try:
            raise ClientDisconnect
        except ClientDisconnect:
            sdk_logger.exception("Error handling POST request")

    middleware = serve_http.ClientDisconnectMiddleware(sdk_handler_logs_the_exception, CONFIG)
    with caplog.at_level(logging.DEBUG):
        _drive(middleware)

    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    (record,) = messages(caplog, "client disconnected")
    assert record.levelno == logging.DEBUG and record.exc_info is None
    assert (record.family, record.path, record.session_id) == ("pdf", "/mcp", "abc123")  # type: ignore[attr-defined]

    caplog.clear()
    with caplog.at_level(logging.INFO):
        _drive(middleware)
    assert caplog.records == [], "at INFO the disconnect leaves no trace at all"


def test_other_errors_from_the_sdk_logger_are_untouched(caplog: pytest.LogCaptureFixture) -> None:
    serve_http.install_client_disconnect_log_filter()
    sdk_logger = logging.getLogger("mcp.server.streamable_http")
    with caplog.at_level(logging.DEBUG):
        try:
            raise RuntimeError("a real fault")
        except RuntimeError:
            sdk_logger.exception("Error handling POST request")

    (record,) = [r for r in caplog.records if r.name == "mcp.server.streamable_http"]
    assert record.levelno == logging.ERROR and record.exc_info is not None


# --- the real uvicorn server, raw HTTP ---------------------------------------------------------------------------


@contextlib.contextmanager
def running_server(app: Any, *, header_timeout: float = 15) -> Iterator[int]:
    """The container's uvicorn configuration on an ephemeral loopback port; shut down on exit."""
    import uvicorn

    server = uvicorn.Server(
        serve_http.uvicorn_config(
            app, host="127.0.0.1", port=0, grace_seconds=1, header_timeout=header_timeout
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "the server did not start"
        time.sleep(0.02)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(15)


def raw_exchange(
    port: int, payload: bytes, *, hold: float = 0.0, timeout: float = 8.0
) -> tuple[bytes, float]:
    """Send *payload*, optionally keep the connection open for *hold* seconds, and read until the server closes
    it (or *timeout*). Returns (bytes received, seconds taken)."""
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
        if payload:
            connection.sendall(payload)
        if hold:
            time.sleep(hold)
        connection.settimeout(timeout)
        received = b""
        try:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                received += chunk
        except TimeoutError:
            received += b"<timeout>"
    return received, time.monotonic() - started


POST_HEAD = (
    b"POST /mcp HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
    b"Accept: application/json, text/event-stream\r\n"
)


def test_a_connection_that_never_finishes_its_request_head_is_closed(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with running_server(make_app(root), header_timeout=1) as port:
        stalled, stalled_seconds = raw_exchange(port, POST_HEAD)  # no blank line ends the head
        silent, silent_seconds = raw_exchange(port, b"")
        healthy, _ = raw_exchange(
            port, b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )

    assert stalled == b"" and 0.8 <= stalled_seconds < 5
    assert silent == b"" and 0.8 <= silent_seconds < 5
    assert healthy.startswith(b"HTTP/1.1 200 OK") and healthy.endswith(b"alive")


def test_a_trickling_head_does_not_postpone_the_timeout(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with running_server(make_app(root), header_timeout=1) as port:
        started = time.monotonic()
        closed_after = None
        with socket.create_connection(("127.0.0.1", port), timeout=8) as connection:
            connection.sendall(POST_HEAD + b"X-Slow: ")  # a valid header line that never ends
            while time.monotonic() - started < 6:
                try:
                    connection.sendall(b"a")
                except OSError:
                    closed_after = time.monotonic() - started
                    break
                time.sleep(0.05)
            else:
                pytest.fail("the server kept a trickling connection open for 6 seconds")

    assert closed_after is not None and 0.8 <= closed_after < 6


def test_a_request_that_completes_its_head_is_not_cut_off_by_the_header_timer(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    body = json.dumps(INITIALIZE).encode()
    request = POST_HEAD + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
    with running_server(make_app(root), header_timeout=1) as port:
        response, _ = raw_exchange(port, request, hold=0.0)

    assert response.startswith(b"HTTP/1.1 200 OK")
    assert b'"serverInfo"' in response


def test_a_stalled_request_body_is_answered_408_with_the_shared_envelope(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    request = POST_HEAD + b"Content-Length: 500\r\n\r\n" + b'{"jsonrpc"'
    with running_server(make_app(root, body_timeout=1)) as port:
        response, seconds = raw_exchange(port, request)

    head, _, body = response.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 408 Request Timeout")
    assert b"connection: close" in head.lower()
    assert 0.8 <= seconds < 6
    assert json.loads(body) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Request Timeout: the request body did not arrive in time"},
    }


def test_a_client_that_hangs_up_mid_body_leaves_no_error_in_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    request = POST_HEAD + b"Content-Length: 500\r\n\r\n" + b'{"jsonrpc"'
    with caplog.at_level(logging.DEBUG), running_server(make_app(root)) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            connection.sendall(request)
            time.sleep(0.3)
        deadline = time.monotonic() + 5
        while not messages(caplog, "client disconnected") and time.monotonic() < deadline:
            time.sleep(0.05)

    assert not [record for record in caplog.records if record.levelno >= logging.ERROR], [
        record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR
    ]
    (record,) = messages(caplog, "client disconnected")
    assert (record.family, record.platform, record.method, record.path) == ("pdf", "go", "POST", "/mcp")  # type: ignore[attr-defined]


def test_main_hands_the_stalled_header_protocol_to_uvicorn_run() -> None:
    import ast

    from uvicorn.protocols.http.h11_impl import H11Protocol

    config = serve_http.uvicorn_config(  # type: ignore[arg-type]
        lambda *_: None,
        host="0.0.0.0",
        port=8080,
        grace_seconds=25,
        header_timeout=7,  # noqa: S104
    )
    assert (config.host, config.port, config.timeout_graceful_shutdown) == ("0.0.0.0", 8080, 25)  # noqa: S104
    assert config.log_config is None
    assert issubclass(config.http, H11Protocol) and config.http is not H11Protocol

    tree = ast.parse((REPO_ROOT / "infra" / "serve_http.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    (run,) = [n for n in ast.walk(main) if isinstance(n, ast.Call) and ast.unparse(n.func) == "uvicorn.run"]
    keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in run.keywords}
    assert keywords["http"] == "header_timeout_protocol(header_timeout_seconds())"
    assert keywords["log_config"] == "None" and "timeout_graceful_shutdown" in keywords


# --- (e) Host, request ids, one envelope, sessions -------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("localhost", True),
        ("localhost:8080", True),
        ("LOCALHOST", True),
        ("127.0.0.1", True),
        ("127.0.0.1:8080", True),
        ("[::1]", True),
        ("[::1]:8080", True),
        ("10.20.30.40:8080", True),
        ("[fe80::1]:8080", True),
        ("foss-mcp-pdf-go", True),
        ("foss-mcp-pdf-go:8080", True),
        ("testserver", True),
        ("foss-mcp-pdf-go.demo.svc", True),
        ("foss-mcp-pdf-go.demo.svc.cluster.local:8080", True),
        (None, True),
        ("evil.example.com", False),
        ("evil.example.com:8080", False),
        ("localhost.evil.example", False),
        ("127.0.0.1.evil.example", False),
        ("", False),
        (":8080", False),
    ],
)
def test_the_default_host_policy(host: str | None, allowed: bool) -> None:
    assert host_is_allowed(host) is allowed


@pytest.mark.parametrize(
    ("host", "extra", "allowed"),
    [
        ("mcp.example.org", ["mcp.example.org"], True),
        ("MCP.Example.org:443", ["mcp.example.org"], True),
        ("api.corp.example", ["*.corp.example"], True),
        ("api.corp.example", [".corp.example"], True),
        ("corp.example", ["*.corp.example"], False),
        ("evilcorp.example", ["*.corp.example"], False),
        ("anything.example", ["*"], True),
        ("anything.example", ["", "  "], False),
    ],
)
def test_the_allow_list_extends_the_host_policy(host: str, extra: list[str], allowed: bool) -> None:
    assert host_is_allowed(host, extra) is allowed


def test_a_foreign_host_is_refused_with_421_and_an_allowed_one_reaches_the_server(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root, allowed_hosts=["mcp.example.org"])) as client:
        refused = client.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Host": "evil.example.com"})
        listed = client.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Host": "mcp.example.org"})
        loopback = client.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Host": "127.0.0.1:8080"})
        probe = client.get("/healthz", headers={"Host": "evil.example.com"})

    assert refused.status_code == 421
    assert refused.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "host 'evil.example.com' is not in the allowed host list"},
    }
    assert listed.status_code == 200 and loopback.status_code == 200
    assert probe.status_code == 200, "probes are not MCP requests and are exempt from the transport checks"


def test_the_allowed_hosts_environment_variable_and_the_machine_name_are_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOSS_MCP_ALLOWED_HOSTS", " a.example.org, *.b.example ,")
    monkeypatch.setenv("FOSS_MCP_CONTAINER_NAME", "foss-mcp-demo.internal")
    hosts = serve_http.allowed_hosts_from_env()

    assert hosts[:3] == ["a.example.org", "*.b.example", "foss-mcp-demo.internal"]
    assert socket.gethostname() in hosts


@pytest.mark.parametrize("bad", [None, True, False, 1.5, 0.0, [], [1], {}, {"a": 1}])
def test_an_invalid_request_id_gets_a_json_rpc_error_not_an_empty_202(tmp_path: Path, bad: object) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as client:
        session = open_session(client)
        response = client.post(
            "/mcp",
            content=json.dumps({"jsonrpc": "2.0", "id": bad, "method": "tools/list"}),
            headers={**HEADERS, "mcp-session-id": session},
        )

    assert response.status_code == 400
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Invalid Request: a JSON-RPC id must be a string or an integer"},
    }


def test_valid_ids_notifications_and_non_json_bodies_are_left_to_the_sdk(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as client:
        session = open_session(client)
        headers = {**HEADERS, "mcp-session-id": session}
        for valid in (7, "a-string-id", "", 0):
            reply = client.post(
                "/mcp",
                content=json.dumps({"jsonrpc": "2.0", "id": valid, "method": "tools/list"}),
                headers=headers,
            )
            assert reply.status_code == 200, valid
        notification = client.post(
            "/mcp", content=json.dumps({"jsonrpc": "2.0", "method": "tools/list"}), headers=headers
        )
        assert notification.status_code == 202 and notification.content == b""
        parse = client.post("/mcp", content=b"{", headers=headers)
        assert parse.status_code == 400 and parse.json()["error"]["code"] == -32700
        batch = client.post(
            "/mcp", content=json.dumps([{"jsonrpc": "2.0", "id": None, "method": "x"}]), headers=headers
        )
        assert batch.status_code == 400 and batch.json()["error"]["code"] == -32600


def test_every_rejection_shares_one_envelope(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as client:
        session = open_session(client)
        valid = {**HEADERS, "mcp-session-id": session}
        responses = {
            "origin": client.post(
                "/mcp", json=INITIALIZE, headers={**HEADERS, "Origin": "https://evil.example"}
            ),
            "host": client.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Host": "evil.example.com"}),
            "protocol": client.post(
                "/mcp", json=INITIALIZE, headers={**HEADERS, "MCP-Protocol-Version": "1999-01-01"}
            ),
            "id": client.post(
                "/mcp", content=json.dumps({"jsonrpc": "2.0", "id": None, "method": "x"}), headers=valid
            ),
            "parse": client.post("/mcp", content=b"{", headers=valid),
            "session": client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={**HEADERS, "mcp-session-id": "nope"},
            ),
        }

    for name, response in responses.items():
        assert response.status_code >= 400, name
        body = response.json()
        assert set(body) == {"jsonrpc", "id", "error"}, (name, body)
        assert body["jsonrpc"] == "2.0" and body["id"] is None, name
        assert set(body["error"]) == {"code", "message"}, (name, body)
        assert isinstance(body["error"]["code"], int) and isinstance(body["error"]["message"], str), name


def test_a_lost_session_after_a_restart_gets_the_recovery_instruction_and_a_new_initialize_works(
    tmp_path: Path,
) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root)) as before_restart:
        old_session = open_session(before_restart)
    with TestClient(make_app(root)) as after_restart:  # a new process: its session table is empty
        lost = after_restart.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**HEADERS, "mcp-session-id": old_session},
        )
        recovered = after_restart.post("/mcp", json=INITIALIZE, headers=HEADERS)

    assert lost.status_code == 404
    message = lost.json()["error"]["message"]
    assert message.startswith("Session not found")
    assert "initialize" in message and "without an Mcp-Session-Id header" in message
    assert recovered.status_code == 200 and recovered.headers["mcp-session-id"] != old_session


# --- session eviction at the cap ----------------------------------------------------------------------------------


def _initialize_status(client: TestClient) -> tuple[int, str | None]:
    response = client.post("/mcp", json=INITIALIZE, headers=HEADERS)
    return response.status_code, response.headers.get("mcp-session-id")


def test_at_the_cap_the_oldest_idle_session_is_evicted_to_admit_a_new_client(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root, max_session_count=2, session_evict_min_idle=0)) as client:
        (_, first), (_, second) = _initialize_status(client), _initialize_status(client)
        with caplog.at_level(logging.WARNING, logger=SERVE_HTTP_LOGGER):
            status, third = _initialize_status(client)
        oldest = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**HEADERS, "mcp-session-id": first or ""},
        )
        newer = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={**HEADERS, "mcp-session-id": second or ""},
        )
        evictions = metric(client, f"foss_mcp_session_evictions_total{{{CONSTANTS}}}")

    assert status == 200 and third and third not in (first, second)
    assert oldest.status_code == 404, "the oldest session was evicted"
    assert "new initialize request" in oldest.json()["error"]["message"]
    assert newer.status_code == 200, "only ONE session is evicted"
    assert evictions == 1
    (record,) = messages(caplog, "oldest idle session evicted to admit a new one")
    assert (record.max_sessions, record.open_sessions, record.evictions_total) == (2, 2, 1)  # type: ignore[attr-defined]
    assert (first or "") not in json.dumps(record.__dict__, default=str), "a session id is never logged here"


def test_a_session_used_within_the_window_is_never_evicted_and_the_503_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(serve_http, "_eviction_clock", lambda: clock[0])
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root, max_session_count=2, session_evict_min_idle=60)) as client:
        _, first = _initialize_status(client)
        clock[0] += 5
        _, second = _initialize_status(client)
        clock[0] += 5
        refused = client.post("/mcp", json=INITIALIZE, headers=HEADERS)

        assert refused.status_code == 503
        assert refused.headers["retry-after"] == "5"

        clock[0] += 100  # both idle for over a minute; `second` is touched again just now
        keep_alive = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            headers={**HEADERS, "mcp-session-id": second or ""},
        )
        assert keep_alive.status_code == 200
        clock[0] += 1
        admitted, third = _initialize_status(client)
        first_after = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
            headers={**HEADERS, "mcp-session-id": first or ""},
        )
        second_after = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
            headers={**HEADERS, "mcp-session-id": second or ""},
        )

    assert admitted == 200 and third
    assert first_after.status_code == 404, "the session idle for 106 seconds went"
    assert second_after.status_code == 200, "the session used 1 second ago stayed"


def test_two_hundred_abandoned_sessions_no_longer_lock_a_new_client_out(tmp_path: Path) -> None:
    root, _ = make_roots(tmp_path, sidecars=tuple(SIDECAR_NAMES))
    with TestClient(make_app(root, max_session_count=200, session_evict_min_idle=0)) as client:
        abandoned = [_initialize_status(client)[1] for _ in range(200)]
        assert all(abandoned)

        status, fresh = _initialize_status(client)

        assert status == 200 and fresh
        gone = [
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={**HEADERS, "mcp-session-id": session or ""},
            ).status_code
            for session in abandoned
        ]
        assert gone.count(404) == 1 and gone.count(200) == 199


def test_the_environment_settings_for_the_new_limits_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (serve_http.session_evict_min_idle_seconds(), serve_http.header_timeout_seconds()) == (10, 15)
    assert serve_http.body_timeout_seconds() == 30
    monkeypatch.setenv("FOSS_MCP_SESSION_EVICT_MIN_IDLE_SECONDS", "0")
    monkeypatch.setenv("FOSS_MCP_HEADER_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("FOSS_MCP_BODY_TIMEOUT_SECONDS", "4")
    assert (serve_http.session_evict_min_idle_seconds(), serve_http.header_timeout_seconds()) == (0, 3)
    assert serve_http.body_timeout_seconds() == 4
    for variable, resolver in (
        ("FOSS_MCP_SESSION_EVICT_MIN_IDLE_SECONDS", serve_http.session_evict_min_idle_seconds),
        ("FOSS_MCP_HEADER_TIMEOUT_SECONDS", serve_http.header_timeout_seconds),
        ("FOSS_MCP_BODY_TIMEOUT_SECONDS", serve_http.body_timeout_seconds),
    ):
        for bad in ("-1", "abc", "1.5"):
            monkeypatch.setenv(variable, bad)
            with pytest.raises(ValueError) as raised:
                resolver()
            assert bad not in str(raised.value), "an invalid value is never echoed back"
        monkeypatch.delenv(variable)


# --- the call sites exist in the real entrypoints (AGENTS.md, "Integration and liveness") ---------------------


def test_every_new_guard_is_wired_into_build_app_the_production_entrypoint() -> None:
    """A green unit test proves a function is correct, never that it is called. These are grep assertions on the
    committed source of the files the container actually runs."""
    serve_source = (REPO_ROOT / "infra" / "serve_http.py").read_text(encoding="utf-8")
    build_app = serve_source.split("def build_app(", 1)[1].split("def deployment_config_from_env", 1)[0]
    for call_site in (
        "install_client_disconnect_log_filter()",
        "SidecarWatcher(resolved_manifests_dir, deployment_config)",
        "_readyz_route(deployment_config, resolved_store, sidecars)",
        "sidecars=sidecars.snapshot",
        "        SessionEvictionMiddleware,",
        "inner.add_middleware(RequestIdMiddleware)",
        "ReadTimeoutMiddleware, timeout_seconds=resolved_body_timeout",
        "inner.add_middleware(ClientDisconnectMiddleware, deployment_config=deployment_config)",
        "allowed_hosts=resolved_hosts",
        "ErrorSanitisingMiddleware(SessionCapObserverMiddleware(inner, on_reject))",
    ):
        assert call_site in build_app, f"build_app no longer calls {call_site}"
    server_source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "server.py").read_text(encoding="utf-8")
    for call_site in (
        "GenerationWatch(store, generation_scope_key)",
        "_run_tool_handler,",
        "generation_watch,",
        "except _STORAGE_FAULTS as exc:",
        "except SidecarNotLoadedError:",
        "active_generation.peek()",
        "meta={CORRELATION_ID_META_KEY: correlation_id}",
    ):
        assert call_site in server_source, f"server.py no longer contains {call_site}"
    rollback_source = (REPO_ROOT / "infra" / "rollback.py").read_text(encoding="utf-8")
    assert "readiness_report(DeploymentGenerationStore(store, scope))" in rollback_source
