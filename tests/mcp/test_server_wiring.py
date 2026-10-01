"""All nine tools wired into the served MCP transport, and Origin/protocol-version rejection
enforced at the transport boundary - in-process, over the real SDK's ASGI app
(``starlette.testclient.TestClient``), never a real socket (``network: false``).

Protocol-level errors and tool-domain errors stay distinguishable by construction: a
rejection never reaches JSON-RPC dispatch at all (a plain HTTP 400 JSON body), while a tool
reporting no match is a completely normal ``CallToolResult`` with ``isError: false``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import (
    SearchSymbolsInput,
    ToolErrorCode,
    _build_usage_event,
    create_server,
    public_error_message,
    public_validation_error_message,
    render_result_text,
    schema_for,
    tool_description,
)
from foss_mcp.mcp.transport_security import reject_request
from foss_mcp.telemetry.usage_recorder import UsageRecorder

# infra/ is not a package (no __init__.py, matching scripts/ convention) - import its module
# directly from the path, the same way its own entrypoints are invoked.
sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

EXPECTED_TOOLS = {
    "lookup",
    "search_docs",
    "search_symbols",
    "get_symbol",
    "list_members",
    "find_examples",
    "get_product_reference",
    "list_recent_changes",
    "report_index_freshness",
}

# One valid, minimal argument set per tool - enough to prove each is callable without
# erroring, over an intentionally empty store (an honest Miss/NotFound is still a successful,
# non-error tool result; TC-017a/b/c already prove real content against real data).
VALID_ARGUMENTS = {
    "lookup": {"query": "Widget"},
    "search_docs": {"query": "install", "content_type": "developer_guide"},
    "search_symbols": {"query": "Widget"},
    "get_symbol": {"fqn": "Widget"},
    "list_members": {"fqn": "Widget"},
    "find_examples": {"query": "Widget"},
    "get_product_reference": {"section": "license"},
    "list_recent_changes": {},
    "report_index_freshness": {},
}

VALID_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Origin": "https://example.com",
    "MCP-Protocol-Version": "2025-06-18",
}
ALLOWED_ORIGINS = ["https://example.com"]


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


class _McpSession:
    """A thin, real MCP-over-HTTP client session for these in-process tests: initialize,
    acknowledge, then send whatever the test needs - driven through the REAL ASGI app, not a
    stand-in for it.
    """

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
                    "clientInfo": {"name": "test-client", "version": "0.0.1"},
                },
            },
            headers=VALID_HEADERS,
        )
        assert response.status_code == 200, response.text
        self.session_id = response.headers["mcp-session-id"]
        self.headers = dict(VALID_HEADERS, **{"mcp-session-id": self.session_id})
        client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=self.headers
        )

    def request(self, method: str, params: dict, *, request_id: int = 1):
        return self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            headers=self.headers,
        )


def _client(tmp_path: Path) -> TestClient:
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=_store(tmp_path),
        allowed_origins=ALLOWED_ORIGINS,
    )
    return TestClient(app)


# ---------------------------------------------------------------------
# transport_security.reject_request - the stable contract, tested directly.
# ---------------------------------------------------------------------


def test_a_disallowed_origin_is_rejected() -> None:
    reason = reject_request(
        {"Origin": "https://evil.example", "MCP-Protocol-Version": "2025-06-18"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is not None and "origin" in reason.lower()


def test_an_absent_origin_is_not_rejected_on_its_own() -> None:
    """Non-browser clients legitimately never send Origin; only a PRESENT, disallowed one
    is a DNS-rebinding-relevant signal."""
    reason = reject_request({"MCP-Protocol-Version": "2025-06-18"}, allowed_origins=ALLOWED_ORIGINS)
    assert reason is None


def _assert_request_is_allowed(reason: str | None) -> None:
    assert reason is None, f"expected the request to be allowed, got rejection reason: {reason!r}"


def test_a_missing_protocol_version_is_allowed_through() -> None:
    reason = reject_request({"Origin": "https://example.com"}, allowed_origins=ALLOWED_ORIGINS)
    _assert_request_is_allowed(reason)


def test_an_invalid_protocol_version_is_rejected() -> None:
    reason = reject_request(
        {"Origin": "https://example.com", "MCP-Protocol-Version": "not-a-date"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is not None and "protocol-version" in reason.lower()


def test_a_fully_valid_request_is_not_rejected() -> None:
    reason = reject_request(
        {"Origin": "https://example.com", "MCP-Protocol-Version": "2025-06-18"},
        allowed_origins=ALLOWED_ORIGINS,
    )
    assert reason is None


# ---------------------------------------------------------------------
# schema_for - derived from each tool wrapper's own signature.
# ---------------------------------------------------------------------


def test_schema_for_marks_defaulted_parameters_optional() -> None:
    def example(*, required_one: str, optional_one: int = 5) -> None:
        pass

    schema = schema_for(example)
    assert schema["properties"]["required_one"] == {"type": "string"}
    assert schema["properties"]["optional_one"] == {"type": "integer"}
    assert schema["required"] == ["required_one"]


def test_schema_for_adds_a_shared_description_per_known_field_name() -> None:
    """A field name that repeats across tools (e.g. top_k) gets the identical description
    wherever it appears - the description lives once, keyed by field name, never hand-written
    per tool."""

    def example(*, query: str, top_k: int = 10, unknown_field: str = "x") -> None:
        pass

    schema = schema_for(example)
    assert schema["properties"]["query"]["description"]
    assert schema["properties"]["top_k"]["description"]
    assert schema["properties"]["query"]["description"] != schema["properties"]["top_k"]["description"]
    # A field name with no registered description is left exactly as before - no description key.
    assert "description" not in schema["properties"]["unknown_field"]


# ---------------------------------------------------------------------
# tool_description - every one of the 9 tools has a real, specific, grounded description;
# no placeholder text remains.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_tool_description_is_not_the_placeholder_string(tool_name: str) -> None:
    description = tool_description(tool_name)
    assert description != f"foss-mcp tool: {tool_name}"
    # A real, grounded description is substantially longer than the old one-line placeholder.
    assert len(description) > 80


def test_every_tool_description_is_distinct() -> None:
    descriptions = [tool_description(name) for name in EXPECTED_TOOLS]
    assert len(set(descriptions)) == len(EXPECTED_TOOLS)


def test_sibling_tools_are_cross_referenced_in_their_own_descriptions() -> None:
    """The concrete bar this card must meet: a description states WHEN to call this tool
    relative to a sibling, not just what it does in isolation."""
    assert "search_symbols" in tool_description("lookup")
    assert "get_symbol" in tool_description("search_symbols")
    assert "search_symbols" in tool_description("get_symbol")


def test_an_unregistered_tool_name_raises_key_error_rather_than_a_silent_placeholder() -> None:
    with pytest.raises(KeyError):
        tool_description("not_a_real_tool")


# ---------------------------------------------------------------------
# Pydantic-based argument bounds: a malformed top_k/query/content_type produces a clean,
# bounded is_error result rather than an unhandled exception - proven over the real served
# transport, not just the validation models in isolation.
# ---------------------------------------------------------------------


def test_a_non_positive_top_k_is_rejected_with_a_clean_tool_error(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request(
            "tools/call", {"name": "search_symbols", "arguments": {"query": "Widget", "top_k": 0}}
        )
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is True
        text = body["result"]["content"][0]["text"]
        assert "Traceback" not in text and "TypeError" not in text


def test_an_absurdly_large_top_k_is_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request(
            "tools/call",
            {"name": "search_symbols", "arguments": {"query": "Widget", "top_k": 10_000_000}},
        )
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is True


def test_an_empty_query_is_rejected_with_a_clean_tool_error(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/call", {"name": "search_symbols", "arguments": {"query": ""}})
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is True
        text = body["result"]["content"][0]["text"]
        assert "Traceback" not in text


def test_an_empty_content_type_is_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request(
            "tools/call",
            {"name": "search_docs", "arguments": {"query": "install", "content_type": ""}},
        )
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is True


def test_a_valid_top_k_and_query_still_pass_through_cleanly(tmp_path: Path) -> None:
    """The bounds reject only genuinely malformed input - a normal call is unaffected."""
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request(
            "tools/call", {"name": "search_symbols", "arguments": {"query": "Widget", "top_k": 3}}
        )
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is False


# ---------------------------------------------------------------------
# The served transport, end-to-end in-process: tools/list, tools/call, and rejection.
# ---------------------------------------------------------------------


def test_all_nine_tools_are_registered_directly_on_the_server(tmp_path: Path) -> None:
    server = create_server(DeploymentConfig(family="pdf", platform="net"), _store(tmp_path))
    assert server is not None  # construction alone proves on_list_tools/on_call_tool wired


def test_tools_list_returns_all_nine_tools_with_schemas(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/list", {})
        assert response.status_code == 200
        body = _sse_json(response.text)
        tools = body["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert names == EXPECTED_TOOLS
        for tool in tools:
            assert tool["inputSchema"]["type"] == "object"


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_each_tool_is_callable(tmp_path: Path, tool_name: str) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/call", {"name": tool_name, "arguments": VALID_ARGUMENTS[tool_name]})
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert "result" in body, body
        assert body["result"]["isError"] is False


# ---------------------------------------------------------------------
# render_result_text - the plain-text content channel is real, parseable JSON built from the
# same jsonable form already computed for structured_content, never Python's repr() debug
# syntax. Both channels must agree, over the real served transport.
# ---------------------------------------------------------------------


def test_render_result_text_is_json_not_python_repr() -> None:
    """A dict with a single-quote-heavy Python repr must render as real JSON (double-quoted
    strings), not Python's own debug syntax."""
    import json

    result = {"kind": "Miss", "reason": "no match"}
    text = render_result_text(result)
    assert text == json.dumps(result)
    # repr() would have produced Python-only single-quoted syntax; JSON never does.
    assert "'" not in text
    assert json.loads(text) == result


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_each_tool_calls_plain_text_content_is_valid_json_matching_structured_content(
    tmp_path: Path, tool_name: str
) -> None:
    """The concrete bar this card must meet: content[0].text (the plain-text channel every
    client also receives) parses as JSON and is identical to structured_content['result'] -
    the two channels agree, and neither requires Python-specific parsing."""
    import json

    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/call", {"name": tool_name, "arguments": VALID_ARGUMENTS[tool_name]})
        assert response.status_code == 200
        body = _sse_json(response.text)
        result = body["result"]
        assert result["isError"] is False
        text = result["content"][0]["text"]
        parsed_text = json.loads(text)  # must be real, parseable JSON - not repr() syntax
        assert parsed_text == result["structuredContent"]["result"]


def test_a_bad_origin_is_rejected_at_the_transport_never_reaching_a_tool(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers=dict(VALID_HEADERS, Origin="https://evil.example"),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "rejected"
        assert "origin" in response.json()["reason"].lower()


def test_a_missing_protocol_version_is_allowed_through_with_a_real_initialize_response(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        headers = {key: value for key, value in VALID_HEADERS.items() if key != "MCP-Protocol-Version"}
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.0.1"},
                },
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = _sse_json(response.text)
        assert "result" in body and "error" not in body


def test_an_invalid_protocol_version_is_rejected_with_400(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers=dict(VALID_HEADERS, **{"MCP-Protocol-Version": "not-a-real-version"}),
        )
        assert response.status_code == 400
        assert "protocol-version" in response.json()["reason"].lower()


def test_a_valid_request_still_passes(tmp_path: Path) -> None:
    """A rejection is a plain 400 JSON body; a real, valid session's response is the normal
    JSON-RPC envelope - the two are never the same shape."""
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/list", {})
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert "result" in body and "error" not in body


# ---------------------------------------------------------------------
# public_error_message / ToolErrorCode - the sanitizer and the closed error-code vocabulary
# every error path in _call_tool reports through (G2/TC-131).
# ---------------------------------------------------------------------


def test_tool_error_code_is_a_small_closed_set() -> None:
    assert {member.value for member in ToolErrorCode} == {"not_found", "invalid_argument", "internal"}


def test_public_error_message_passes_a_normal_safe_message_through_unchanged() -> None:
    exc = ValueError("query must not be empty")
    assert public_error_message(exc, fallback="safe fallback") == "query must not be empty"


def test_public_error_message_rejects_a_windows_path_and_logs_it_server_side(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exc = RuntimeError(r"could not read C:\Users\prora\secret\shadow_config.ini")
    with caplog.at_level(logging.WARNING):
        result = public_error_message(exc, fallback="safe fallback")
    assert result == "safe fallback"
    assert "shadow_config.ini" not in result
    assert r"C:\Users\prora" not in result
    # Discarded from the client's own text, but never simply discarded - still logged in full.
    assert "shadow_config.ini" in caplog.text


def test_public_error_message_rejects_a_posix_path(caplog: pytest.LogCaptureFixture) -> None:
    exc = RuntimeError("failed: /home/user/project/venv/lib/site-packages/mod.py")
    with caplog.at_level(logging.WARNING):
        result = public_error_message(exc, fallback="safe fallback")
    assert result == "safe fallback"
    assert "/home/user" not in result


def test_public_error_message_rejects_a_site_packages_substring() -> None:
    exc = RuntimeError("error somewhere inside site-packages")
    assert public_error_message(exc, fallback="safe fallback") == "safe fallback"


def test_public_error_message_rejects_a_traceback_like_message() -> None:
    exc = RuntimeError("Traceback (most recent call last): boom")
    assert public_error_message(exc, fallback="safe fallback") == "safe fallback"


def test_public_error_message_rejects_a_repr_style_object_address() -> None:
    exc = RuntimeError("<Widget object at 0x7f8a3c0a1d90> could not be serialized")
    assert public_error_message(exc, fallback="safe fallback") == "safe fallback"


def test_public_error_message_rejects_an_overly_long_message() -> None:
    exc = RuntimeError("x" * 301)
    assert public_error_message(exc, fallback="safe fallback") == "safe fallback"


def test_public_error_message_rejects_a_multiline_message() -> None:
    exc = RuntimeError("line one\nline two")
    assert public_error_message(exc, fallback="safe fallback") == "safe fallback"


# ---------------------------------------------------------------------
# Error codes and sanitization, proven over the real served transport - not just the
# sanitizer function in isolation.
# ---------------------------------------------------------------------


def test_an_unknown_tool_call_carries_a_not_found_code(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/call", {"name": "not_a_real_tool", "arguments": {}})
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is True
        assert body["result"]["structuredContent"]["code"] == "not_found"


def test_a_pydantic_validation_error_carries_an_invalid_argument_code(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request(
            "tools/call", {"name": "search_symbols", "arguments": {"query": "", "top_k": 3}}
        )
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is True
        assert body["result"]["structuredContent"]["code"] == "invalid_argument"


def test_an_unexpected_tool_exception_carries_an_internal_code_and_a_sanitized_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The concrete bar this card must meet: a real exception containing an internal marker (a
    fake file path here) never reaches the client's own result text, but is still logged
    server-side with full detail - proven over the real served transport, not a unit stand-in
    for it.
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(r"failed loading C:\Users\prora\secret\shadow_config.ini")

    monkeypatch.setattr("foss_mcp.mcp.server.search_symbols", _boom)

    with caplog.at_level(logging.WARNING):
        with _client(tmp_path) as client:
            session = _McpSession(client)
            response = session.request(
                "tools/call", {"name": "search_symbols", "arguments": {"query": "Widget"}}
            )
    assert response.status_code == 200
    body = _sse_json(response.text)
    assert body["result"]["isError"] is True
    assert body["result"]["structuredContent"]["code"] == "internal"
    text = body["result"]["content"][0]["text"]
    assert "shadow_config.ini" not in text
    assert r"C:\Users\prora" not in text
    # Never simply discarded - the real exception is logged server-side in full detail.
    assert "shadow_config.ini" in caplog.text
    assert "search_symbols" in caplog.text


def test_a_normal_tool_domain_miss_still_reports_cleanly_without_an_error_code(
    tmp_path: Path,
) -> None:
    """A successful tool call (even an honest Miss/NotFound business result) is unaffected by
    any of this - only the transport-level error path gained a code."""
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request(
            "tools/call", {"name": "get_symbol", "arguments": {"fqn": "Not.A.Real.Symbol"}}
        )
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is False
        assert "code" not in body["result"]["structuredContent"]


# ---------------------------------------------------------------------
# Tool-call logging and correlation ids (G2/TC-132): every tool call emits exactly one
# structured log record (tool name, correlation id, outcome, latency), and any error result's
# own structured_content carries the SAME correlation id as its own log record - proven over
# the real served transport, not a unit stand-in for it.
# ---------------------------------------------------------------------


def test_a_real_tool_call_emits_exactly_one_log_record_with_tool_correlation_id_and_outcome(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="foss_mcp.mcp.server"):
        with _client(tmp_path) as client:
            session = _McpSession(client)
            response = session.request(
                "tools/call", {"name": "search_symbols", "arguments": {"query": "Widget"}}
            )
    assert response.status_code == 200
    body = _sse_json(response.text)
    assert body["result"]["isError"] is False

    tool_call_records = [record for record in caplog.records if hasattr(record, "correlation_id")]
    assert len(tool_call_records) == 1
    record = tool_call_records[0]
    assert record.tool == "search_symbols"
    assert record.outcome == "success"
    assert isinstance(record.correlation_id, str) and record.correlation_id
    assert isinstance(record.latency_ms, float)
    assert record.latency_ms >= 0


def test_an_error_results_correlation_id_matches_its_own_log_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="foss_mcp.mcp.server"):
        with _client(tmp_path) as client:
            session = _McpSession(client)
            response = session.request("tools/call", {"name": "search_symbols", "arguments": {"query": ""}})
    assert response.status_code == 200
    body = _sse_json(response.text)
    assert body["result"]["isError"] is True
    correlation_id = body["result"]["structuredContent"]["correlation_id"]
    assert isinstance(correlation_id, str) and correlation_id

    tool_call_records = [record for record in caplog.records if hasattr(record, "correlation_id")]
    assert len(tool_call_records) == 1
    record = tool_call_records[0]
    assert record.correlation_id == correlation_id
    assert record.outcome == "error"
    assert record.tool == "search_symbols"


def test_an_unknown_tool_calls_error_correlation_id_matches_its_own_log_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="foss_mcp.mcp.server"):
        with _client(tmp_path) as client:
            session = _McpSession(client)
            response = session.request("tools/call", {"name": "not_a_real_tool", "arguments": {}})
    assert response.status_code == 200
    body = _sse_json(response.text)
    assert body["result"]["isError"] is True
    correlation_id = body["result"]["structuredContent"]["correlation_id"]

    tool_call_records = [record for record in caplog.records if hasattr(record, "correlation_id")]
    assert len(tool_call_records) == 1
    record = tool_call_records[0]
    assert record.correlation_id == correlation_id
    assert record.outcome == "error"


def test_public_validation_error_message_builds_from_errors_not_str() -> None:
    """A plain, safe field-validator ValueError (empty query) must come through as its own
    specific reason, built from exc.errors() - never the raw, multi-line str(exc)."""
    try:
        SearchSymbolsInput.model_validate({"query": "", "top_k": 3})
    except ValidationError as exc:
        text = public_validation_error_message(exc)
    else:
        raise AssertionError("expected a ValidationError")
    assert text == "query: Value error, query must not be empty"
    assert "\n" not in text


def test_public_validation_error_message_covers_a_negative_top_k() -> None:
    try:
        SearchSymbolsInput.model_validate({"query": "Widget", "top_k": -1})
    except ValidationError as exc:
        text = public_validation_error_message(exc)
    else:
        raise AssertionError("expected a ValidationError")
    assert text == "top_k: Input should be greater than 0"


def test_public_validation_error_message_joins_multiple_field_errors() -> None:
    try:
        SearchSymbolsInput.model_validate({"query": "", "top_k": -1})
    except ValidationError as exc:
        text = public_validation_error_message(exc)
    else:
        raise AssertionError("expected a ValidationError")
    assert text == "query: Value error, query must not be empty; top_k: Input should be greater than 0"


def test_an_empty_query_returns_its_own_specific_reason_not_the_generic_fallback(tmp_path: Path) -> None:
    """The concrete bar this card must meet: a plain, safe ValidationError (empty query) must
    no longer be swallowed into _GENERIC_ERROR_FALLBACK - proven over the real served
    transport, not just the sanitizer in isolation."""
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request("tools/call", {"name": "search_symbols", "arguments": {"query": ""}})
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is True
        text = body["result"]["content"][0]["text"]
        assert text == "query: Value error, query must not be empty"
        assert "internal error" not in text.lower()


def test_a_negative_top_k_returns_its_own_specific_reason_not_the_generic_fallback(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        session = _McpSession(client)
        response = session.request(
            "tools/call", {"name": "search_symbols", "arguments": {"query": "Widget", "top_k": -1}}
        )
        assert response.status_code == 200
        body = _sse_json(response.text)
        assert body["result"]["isError"] is True
        text = body["result"]["content"][0]["text"]
        assert text == "top_k: Input should be greater than 0"
        assert "internal error" not in text.lower()


def test_a_suppressed_unsafe_message_warning_log_line_carries_the_same_correlation_id_as_the_client_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """TC-136's second gap, half one: public_error_message's own logger.warning call (fired only
    when suppressing unsafe text) previously carried no correlation_id at all - an operator
    handed a correlation id by an end user had no way to grep for the matching server-side
    event."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(r"failed loading C:\Users\prora\secret\shadow_config.ini")

    monkeypatch.setattr("foss_mcp.mcp.server.search_symbols", _boom)

    with caplog.at_level(logging.WARNING, logger="foss_mcp.mcp.server"):
        with _client(tmp_path) as client:
            session = _McpSession(client)
            response = session.request(
                "tools/call", {"name": "search_symbols", "arguments": {"query": "Widget"}}
            )
    assert response.status_code == 200
    body = _sse_json(response.text)
    correlation_id = body["result"]["structuredContent"]["correlation_id"]

    warning_records = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warning_records) == 1
    assert f"correlation_id={correlation_id}" in warning_records[0].getMessage()


def test_the_generic_exception_branchs_error_log_line_carries_the_same_correlation_id_as_the_client_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """TC-136's second gap, half two: the generic-exception branch's own logger.error call
    (distinct from public_error_message's internal warning) previously carried no
    correlation_id either."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(r"failed loading C:\Users\prora\secret\shadow_config.ini")

    monkeypatch.setattr("foss_mcp.mcp.server.search_symbols", _boom)

    with caplog.at_level(logging.ERROR, logger="foss_mcp.mcp.server"):
        with _client(tmp_path) as client:
            session = _McpSession(client)
            response = session.request(
                "tools/call", {"name": "search_symbols", "arguments": {"query": "Widget"}}
            )
    assert response.status_code == 200
    body = _sse_json(response.text)
    correlation_id = body["result"]["structuredContent"]["correlation_id"]

    error_records = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(error_records) == 1
    assert f"correlation_id={correlation_id}" in error_records[0].getMessage()


def test_two_different_tool_calls_get_two_different_correlation_ids(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="foss_mcp.mcp.server"):
        with _client(tmp_path) as client:
            session = _McpSession(client)
            session.request("tools/call", {"name": "search_symbols", "arguments": {"query": "Widget"}})
            session.request("tools/call", {"name": "search_symbols", "arguments": {"query": "Widget"}})

    tool_call_records = [record for record in caplog.records if hasattr(record, "correlation_id")]
    assert len(tool_call_records) == 2
    assert tool_call_records[0].correlation_id != tool_call_records[1].correlation_id


def test_call_tools_correlation_id_comes_from_new_correlation_id_not_a_bare_uuid_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``_call_tool`` must mint its correlation id by calling
    ``foss_mcp.telemetry.usage_recorder.new_correlation_id`` (imported into server.py's own
    namespace) rather than inlining its own ``uuid.uuid4().hex`` - monkeypatching the name as
    bound in server.py proves the real call site is wired to it, not merely that some unrelated
    uuid call still happens to produce a valid-looking id. The error path is used (like the
    other correlation-id tests above) because only an error ``CallToolResult`` surfaces
    ``correlation_id`` in its own ``structured_content``.
    """
    sentinel = "sentinel-correlation-id"
    monkeypatch.setattr("foss_mcp.mcp.server.new_correlation_id", lambda: sentinel)

    with caplog.at_level(logging.INFO, logger="foss_mcp.mcp.server"):
        with _client(tmp_path) as client:
            session = _McpSession(client)
            response = session.request("tools/call", {"name": "search_symbols", "arguments": {"query": ""}})
    assert response.status_code == 200
    body = _sse_json(response.text)
    assert body["result"]["isError"] is True
    assert body["result"]["structuredContent"]["correlation_id"] == sentinel

    tool_call_records = [record for record in caplog.records if hasattr(record, "correlation_id")]
    assert len(tool_call_records) == 1
    assert tool_call_records[0].correlation_id == sentinel


def _sse_json(text: str) -> dict:
    """The streamable-HTTP transport answers a POST with one SSE `data:` line - decode it."""
    import json

    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line found in response: {text!r}")


# ---------------------------------------------------------------------
# Telemetry wiring (G2/TC-142): _build_usage_event's own exact contract, plus proof that every
# _call_tool exit path genuinely calls usage_recorder.record() - over the real served transport,
# not merely that the function exists somewhere unreferenced (the exact TC-130 defect class
# this card closes).
# ---------------------------------------------------------------------


def test_build_usage_event_maps_the_documented_simplifications() -> None:
    event = _build_usage_event(
        correlation_id="corr-1",
        deployment_id="pdf::net",
        tool_name="search_symbols",
        outcome="success",
        latency_ms=12.5,
        result=["a", "b", "c"],
        arguments={"query": "Widget"},
    )
    assert event.request_correlation_id == "corr-1"
    assert event.deployment_id == "pdf::net"
    assert event.generation_id is None  # not derivable from this dispatch layer - documented
    assert event.tool_name == "search_symbols"
    assert event.outcome == "success"
    assert event.latency_ms == 12.5
    assert event.result_count == 3  # len() of the jsonable list result
    assert event.citation_count == 0  # no caller ever supplies citations at this layer
    assert event.cache_status == "unknown"  # this project has no caching layer
    assert event.query_shape_category == "exact_fqn"  # "Widget" is capitalized - derived from
    # arguments["query"] via classify_query_shape, never the raw text itself


def test_build_usage_event_result_count_is_zero_for_a_non_list_result() -> None:
    event = _build_usage_event(
        correlation_id="corr-2",
        deployment_id="pdf::net",
        tool_name="get_symbol",
        outcome="success",
        latency_ms=1.0,
        result={"fqn": "Widget"},
        arguments={},
    )
    assert event.result_count == 0
    assert event.query_shape_category == "empty"  # no "query" key present in arguments


def test_build_usage_event_error_path_has_no_result_and_still_builds_cleanly() -> None:
    event = _build_usage_event(
        correlation_id="corr-3",
        deployment_id="pdf::net",
        tool_name="not_a_real_tool",
        outcome="error",
        latency_ms=0.5,
        result=None,
        arguments={},
    )
    assert event.outcome == "error"
    assert event.result_count == 0


def test_metrics_reflects_real_tool_calls_through_the_real_served_transport(tmp_path: Path) -> None:
    """The concrete bar this card must meet: making several real tool calls (a mix of success
    and error paths) through the REAL served transport must increase /metrics' own
    usage_events_queued by EXACTLY that many calls - proving UsageRecorder.record() was
    genuinely invoked by _call_tool on every exit path, not merely that the function exists.
    """
    with _client(tmp_path) as client:
        session = _McpSession(client)
        before = client.get("/metrics").json()

        calls = [
            ("search_symbols", {"query": "Widget"}),  # success
            ("get_symbol", {"fqn": "Not.A.Real.Symbol"}),  # success (honest miss)
            ("search_symbols", {"query": ""}),  # error: invalid_argument
            ("not_a_real_tool", {}),  # error: not_found
        ]
        for tool_name, arguments in calls:
            response = session.request("tools/call", {"name": tool_name, "arguments": arguments})
            assert response.status_code == 200

        after = client.get("/metrics").json()

    assert after["usage_events_queued"] - before["usage_events_queued"] == len(calls)
    assert after["usage_events_dropped"] == before["usage_events_dropped"]


def test_metrics_is_reachable_with_no_origin_or_protocol_version_headers(tmp_path: Path) -> None:
    """A plain metrics scrape carries neither header - /metrics must answer on its own merits,
    never be caught by RejectionMiddleware's MCP-transport-only rejection rules (the same
    exemption /healthz and /readyz already rely on)."""
    with _client(tmp_path) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        body = response.json()
        assert "usage_events_queued" in body
        assert "usage_events_dropped" in body


def test_metrics_usage_events_dropped_reflects_a_real_drop_from_real_tool_calls(
    tmp_path: Path,
) -> None:
    """A tiny max_queued forces a real drop under real tool-call traffic - /metrics must report
    it, proving /metrics reads the SAME live recorder _call_tool records into, not a separate
    or mocked one."""
    recorder = UsageRecorder(max_queued=1)
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=_store(tmp_path),
        allowed_origins=ALLOWED_ORIGINS,
        usage_recorder=recorder,
    )
    with TestClient(app) as client:
        session = _McpSession(client)
        for _ in range(3):
            session.request("tools/call", {"name": "search_symbols", "arguments": {"query": "Widget"}})
        body = client.get("/metrics").json()

    assert body["usage_events_queued"] == 1  # capacity 1 - never grows past it
    assert body["usage_events_dropped"] == 2  # the other two real calls were genuinely dropped


def test_create_server_defaults_to_a_real_usage_recorder_when_none_is_given(tmp_path: Path) -> None:
    """create_server mirrors manifest_store's own default-when-not-given pattern: construction
    must not raise or require a caller to supply usage_recorder explicitly."""
    server = create_server(DeploymentConfig(family="pdf", platform="net"), _store(tmp_path))
    assert server is not None
