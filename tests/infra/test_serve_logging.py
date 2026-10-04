"""The serving entrypoint's logging and graceful-shutdown configuration (REQ-G2-043).

Tool-call records are emitted by ``foss_mcp.mcp.server`` through its own module logger; they
must reach stdout as one valid JSON object per line with their ``extra=`` fields. The log level
comes from ``FOSS_MCP_LOG_LEVEL`` and the uvicorn shutdown window from
``FOSS_MCP_SHUTDOWN_GRACE_SECONDS``.

The tests call the real ``serve_http`` functions, and ``main()`` itself for the end-to-end
wiring: ``uvicorn.run`` is replaced by a recorder, so the test sees the exact keyword arguments
the real entrypoint passes, never a copy of them.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
import uvicorn

# infra/ is not a package (no __init__.py, matching scripts/ convention) - import its module
# directly from the path, the same way tests/infra/test_serve_http.py does it.
sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

TOOL_CALL_LOGGER = "foss_mcp.mcp.server"
# The environment variable names are the deployment contract, so the tests spell them out as
# literals. Referencing serve_http's constants would let a renamed constant pass unnoticed.
LOG_LEVEL_VAR = "FOSS_MCP_LOG_LEVEL"
GRACE_VAR = "FOSS_MCP_SHUTDOWN_GRACE_SECONDS"


@pytest.fixture
def restore_root_logging():
    """configure_logging() mutates the process-wide root logger; put it back afterwards."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _stdout_json_lines(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    lines = capsys.readouterr().out.splitlines()
    return [json.loads(line) for line in lines]


def test_tool_call_record_reaches_stdout_as_one_json_line(capsys, monkeypatch, restore_root_logging):
    monkeypatch.delenv(LOG_LEVEL_VAR, raising=False)
    serve_http.configure_logging()

    logging.getLogger(TOOL_CALL_LOGGER).info(
        "tool call: %s (%s)",
        "convert",
        "ok",
        extra={
            "tool": "convert",
            "correlation_id": "abc123",
            "outcome": "ok",
            "latency_ms": 12.5,
        },
    )

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) == 1, out
    record = json.loads(lines[0])
    assert record["logger"] == TOOL_CALL_LOGGER
    assert record["level"] == "INFO"
    assert record["message"] == "tool call: convert (ok)"
    assert record["tool"] == "convert"
    assert record["correlation_id"] == "abc123"
    assert record["outcome"] == "ok"
    assert record["latency_ms"] == 12.5
    assert "ts" in record


def test_log_level_defaults_to_info(capsys, monkeypatch, restore_root_logging):
    monkeypatch.delenv(LOG_LEVEL_VAR, raising=False)
    serve_http.configure_logging()

    logging.getLogger(TOOL_CALL_LOGGER).info("default level record")

    assert [r["message"] for r in _stdout_json_lines(capsys)] == ["default level record"]


def test_log_level_warning_suppresses_info(capsys, monkeypatch, restore_root_logging):
    monkeypatch.setenv(LOG_LEVEL_VAR, "WARNING")
    serve_http.configure_logging()

    logging.getLogger(TOOL_CALL_LOGGER).info("info record must be dropped")
    logging.getLogger(TOOL_CALL_LOGGER).warning("warning record must survive")

    records = _stdout_json_lines(capsys)
    assert [(r["level"], r["message"]) for r in records] == [("WARNING", "warning record must survive")]


def test_unknown_log_level_fails_at_startup(monkeypatch, restore_root_logging):
    monkeypatch.setenv(LOG_LEVEL_VAR, "LOUD")
    with pytest.raises(ValueError):
        serve_http.configure_logging()


def test_shutdown_grace_defaults_to_25(monkeypatch):
    monkeypatch.delenv(GRACE_VAR, raising=False)
    assert serve_http.shutdown_grace_seconds() == 25


def test_shutdown_grace_reads_env_var(monkeypatch):
    monkeypatch.setenv(GRACE_VAR, "40")
    assert serve_http.shutdown_grace_seconds() == 40


def test_shutdown_grace_rejects_negative(monkeypatch):
    monkeypatch.setenv(GRACE_VAR, "-1")
    with pytest.raises(ValueError):
        serve_http.shutdown_grace_seconds()


def test_main_wires_json_logging_and_grace_into_uvicorn(capsys, monkeypatch, restore_root_logging):
    """The real entrypoint: main() configures logging, then calls uvicorn.run with the grace
    window read from the environment. A record emitted while uvicorn is 'running' must reach
    stdout as JSON."""
    monkeypatch.delenv(LOG_LEVEL_VAR, raising=False)
    monkeypatch.setenv(GRACE_VAR, "40")
    # main() requires the deployment identity; any valid pair serves this test.
    monkeypatch.setenv(serve_http.FAMILY_ENV, "pdf")
    monkeypatch.setenv(serve_http.PLATFORM_ENV, "net")
    app_sentinel = object()
    monkeypatch.setattr(serve_http, "build_app", lambda config: app_sentinel)

    seen: dict = {}

    def fake_run(app, **kwargs):
        seen["app"] = app
        seen["kwargs"] = kwargs
        logging.getLogger(TOOL_CALL_LOGGER).info(
            "tool call: %s (%s)", "search", "ok", extra={"tool": "search", "outcome": "ok"}
        )

    monkeypatch.setattr(uvicorn, "run", fake_run)
    serve_http.main()

    assert seen["app"] is app_sentinel
    assert seen["kwargs"]["timeout_graceful_shutdown"] == 40
    assert seen["kwargs"]["log_config"] is None
    records = _stdout_json_lines(capsys)
    assert len(records) == 1
    assert records[0]["tool"] == "search"
    assert records[0]["message"] == "tool call: search (ok)"
