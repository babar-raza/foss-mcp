"""G3/TC-391 (REQ-G3-006): a malformed JSON-RPC body gets a short fixed 400 message, and the session
manager is bounded.

The SDK answers HTTP 400 with ``f"Parse error: {e}"`` / ``f"Validation error: {e}"`` - a multi-line
pydantic dump with union internals and an errors.pydantic.dev URL. ``infra/serve_http.py`` replaces
exactly those two messages and nothing else. Offline: the real ``build_app`` driven by Starlette's
``TestClient``, a tmp store, no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig

sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

PARSE_MESSAGE = "Parse error: the request body is not valid JSON"
INVALID_MESSAGE = "Invalid request: the body is not a valid JSON-RPC message for this server"

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0.0.1"},
    },
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(serve_http.SESSION_IDLE_ENV, raising=False)
    monkeypatch.delenv(serve_http.MAX_SESSIONS_ENV, raising=False)


def _app(tmp_path: Path):
    return serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=GenerationManifestStore(tmp_path / "manifests"),
        allowed_origins=[],
    )


def _post_raw(client: TestClient, content: bytes):
    return client.post("/mcp", content=content, headers=HEADERS)


def _assert_fixed_400(response, expected_message: str) -> dict:
    assert response.status_code == 400, response.text
    assert response.headers["content-type"].startswith("application/json")
    envelope = response.json()
    assert envelope["jsonrpc"] == "2.0"
    message = envelope["error"]["message"]
    assert message == expected_message
    lowered = message.lower()
    assert "pydantic" not in lowered
    assert "http" not in lowered
    assert "\n" not in message
    assert "pydantic" not in response.text.lower()
    assert "http" not in response.text.lower()
    assert int(response.headers["content-length"]) == len(response.content)
    return envelope


def test_an_empty_array_body_gets_the_fixed_invalid_request_message(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        envelope = _assert_fixed_400(_post_raw(client, b"[]"), INVALID_MESSAGE)
    assert envelope["error"]["code"] == -32602


def test_string_params_get_the_fixed_invalid_request_message(tmp_path: Path) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "str"}).encode()
    with TestClient(_app(tmp_path)) as client:
        _assert_fixed_400(_post_raw(client, body), INVALID_MESSAGE)


def test_a_body_that_is_not_json_gets_the_fixed_parse_message(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        envelope = _assert_fixed_400(_post_raw(client, b"{not json at all"), PARSE_MESSAGE)
    assert envelope["error"]["code"] == -32700


def test_an_empty_body_gets_the_fixed_parse_message(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        _assert_fixed_400(_post_raw(client, b""), PARSE_MESSAGE)


def test_a_wrong_jsonrpc_value_gets_the_fixed_invalid_request_message(tmp_path: Path) -> None:
    body = json.dumps({"jsonrpc": "1.0", "id": 1, "method": "tools/list"}).encode()
    with TestClient(_app(tmp_path)) as client:
        _assert_fixed_400(_post_raw(client, body), INVALID_MESSAGE)


def test_the_envelope_keeps_id_and_code_and_only_the_message_changes(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        envelope = _assert_fixed_400(_post_raw(client, b"[]"), INVALID_MESSAGE)
    assert set(envelope) == {"jsonrpc", "id", "error"}
    assert envelope["id"] is None
    assert set(envelope["error"]) == {"code", "message"}


def test_the_sdk_really_does_emit_the_verbose_message_without_the_wrapper(tmp_path: Path) -> None:
    """Guards the premise: if the SDK ever stops sending a verbose message this card is moot."""
    app = _app(tmp_path)
    inner = app.app.app  # RejectionMiddleware -> ErrorSanitisingMiddleware -> SDK app
    with TestClient(inner) as client:
        message = _post_raw(client, b"[]").json()["error"]["message"]
    assert message.startswith("Validation error:")
    assert message != INVALID_MESSAGE


def test_a_valid_initialize_still_succeeds(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.post("/mcp", json=INITIALIZE, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert "mcp-session-id" in response.headers
    assert "serverInfo" in response.text


def test_an_unknown_session_error_passes_through_untouched(tmp_path: Path) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with TestClient(_app(tmp_path)) as client:
        response = client.post("/mcp", json=body, headers=HEADERS | {"mcp-session-id": "nope"})
    assert response.status_code == 404
    assert PARSE_MESSAGE not in response.text and INVALID_MESSAGE not in response.text


def test_a_transport_rejection_is_not_rewritten(tmp_path: Path) -> None:
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=GenerationManifestStore(tmp_path / "manifests"),
        allowed_origins=["https://example.com"],
    )
    with TestClient(app) as client:
        response = client.post("/mcp", json=INITIALIZE, headers=HEADERS | {"Origin": "https://evil.example"})
    assert response.status_code == 400
    assert response.json()["error"] == "rejected"


def test_healthz_is_untouched(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "alive"


def test_sanitiser_leaves_other_messages_alone() -> None:
    other = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Bad session"}})
    assert serve_http._sanitised_error_body(other.encode()) is None
    assert serve_http._sanitised_error_body(b"not json") is None
    assert serve_http._sanitised_error_body(b"[1]") is None


def test_the_session_manager_is_bounded_with_the_tc421_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    real_create_server = serve_http.create_server

    def spy(*args: object, **kwargs: object):
        server = real_create_server(*args, **kwargs)  # type: ignore[arg-type]
        real_app = server.streamable_http_app

        def recording_app(**kw: object):
            captured.update(kw)
            return real_app(**kw)  # type: ignore[arg-type]

        server.streamable_http_app = recording_app  # type: ignore[method-assign]
        return server

    monkeypatch.setattr(serve_http, "create_server", spy)
    _app(tmp_path)
    assert captured["session_idle_timeout"] == serve_http.SESSION_IDLE_TIMEOUT_SECONDS == 300
    assert captured["max_sessions"] == serve_http.MAX_SESSIONS == 200
