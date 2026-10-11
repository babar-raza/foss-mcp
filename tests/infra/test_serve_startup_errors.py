"""G3/TC-390: a bad FOSS_MCP_LOG_LEVEL or FOSS_MCP_SHUTDOWN_GRACE_SECONDS ends ``serve_http.main`` with one
line on stderr and exit code 2 - never a Python traceback.

``main`` runs in-process with monkeypatched environment. ``uvicorn.run`` and ``build_app`` are replaced by
recorders, so nothing listens and nothing touches ``/data/manifests``. Messages name the variable and never
its value, which the canary tests pin.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

LOG_LEVEL_VAR = "FOSS_MCP_LOG_LEVEL"
GRACE_VAR = "FOSS_MCP_SHUTDOWN_GRACE_SECONDS"
CANARY = "canary-value-7f3a91"


@pytest.fixture
def restore_root_logging():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, restore_root_logging) -> dict:
    """Identity set, uvicorn and build_app stubbed; the dict records what reached uvicorn."""
    monkeypatch.setenv(serve_http.FAMILY_ENV, "pdf")
    monkeypatch.setenv(serve_http.PLATFORM_ENV, "net")
    monkeypatch.delenv(LOG_LEVEL_VAR, raising=False)
    monkeypatch.delenv(GRACE_VAR, raising=False)
    seen: dict = {"runs": []}
    app_sentinel = object()
    monkeypatch.setattr(serve_http, "build_app", lambda config: app_sentinel)

    def fake_run(app, **kwargs):
        seen["runs"].append((app, kwargs))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    seen["app"] = app_sentinel
    return seen


def run_main_expecting_exit_two(capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as raised:
        serve_http.main()
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    return captured.err


@pytest.mark.parametrize("level", ["LOUD", "verbose", "10x", "10"])
def test_an_unknown_log_level_exits_two_with_one_line(
    level: str, stubbed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(LOG_LEVEL_VAR, level)
    err = run_main_expecting_exit_two(capsys)
    assert err.splitlines() == [
        f"serve_http: {LOG_LEVEL_VAR} is invalid: expected one of CRITICAL, ERROR, WARNING, INFO, DEBUG"
    ]
    assert stubbed["runs"] == []


@pytest.mark.parametrize("level", ["", "   "])
def test_a_blank_log_level_means_unset_and_the_server_starts(
    level: str, stubbed: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LOG_LEVEL_VAR, level)
    serve_http.main()
    assert len(stubbed["runs"]) == 1


@pytest.mark.parametrize("seconds", ["abc", "1.5", "-1", "-30", "ten", "25s"])
def test_an_invalid_or_negative_grace_exits_two_with_one_line(
    seconds: str, stubbed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(GRACE_VAR, seconds)
    err = run_main_expecting_exit_two(capsys)
    assert err.splitlines() == [
        f"serve_http: {GRACE_VAR} is invalid: expected a whole number of seconds, 0 or more"
    ]
    assert stubbed["runs"] == []


def test_an_invalid_log_level_message_never_contains_the_value(
    stubbed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(LOG_LEVEL_VAR, CANARY)
    assert CANARY not in run_main_expecting_exit_two(capsys)


def test_an_invalid_grace_message_never_contains_the_value(
    stubbed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(GRACE_VAR, CANARY)
    assert CANARY not in run_main_expecting_exit_two(capsys)


def test_when_both_settings_are_invalid_the_log_level_is_reported(
    stubbed: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(LOG_LEVEL_VAR, "LOUD")
    monkeypatch.setenv(GRACE_VAR, "abc")
    err = run_main_expecting_exit_two(capsys)
    assert LOG_LEVEL_VAR in err
    assert GRACE_VAR not in err


def test_valid_values_still_reach_uvicorn_with_the_same_arguments(
    stubbed: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LOG_LEVEL_VAR, "warning")
    monkeypatch.setenv(GRACE_VAR, "40")
    serve_http.main()
    assert len(stubbed["runs"]) == 1
    app, kwargs = stubbed["runs"][0]
    assert app is stubbed["app"]
    assert kwargs == {
        "host": "0.0.0.0",
        "port": serve_http.DEFAULT_PORT,
        "log_config": None,
        "timeout_graceful_shutdown": 40,
    }
    assert logging.getLogger().level == logging.WARNING


def test_the_defaults_still_reach_uvicorn_when_nothing_is_set(stubbed: dict) -> None:
    serve_http.main()
    _, kwargs = stubbed["runs"][0]
    assert kwargs["timeout_graceful_shutdown"] == 25
    assert kwargs["port"] == 8080
    assert logging.getLogger().level == logging.INFO


def test_a_zero_grace_is_valid(stubbed: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRACE_VAR, "0")
    serve_http.main()
    assert stubbed["runs"][0][1]["timeout_graceful_shutdown"] == 0


def test_the_helpers_still_raise_value_error_for_other_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRACE_VAR, "abc")
    with pytest.raises(ValueError):
        serve_http.shutdown_grace_seconds()
    monkeypatch.setenv(GRACE_VAR, "-1")
    with pytest.raises(ValueError):
        serve_http.shutdown_grace_seconds()
