"""The stdio serving entrypoint's identity resolution and logging (REQ-G2-043).

Two real defects, confirmed live against ``Dockerfile.serving``'s default container command:
(1) ``serve_stdio.main`` never called ``configure_logging``, so every tool-call log line (the
correlation-ID tracing ``foss_mcp.mcp.server`` emits) was silently dropped over stdio - no
handler, root logger at the default WARNING level. (2) ``FOSS_MCP_FAMILY``/``FOSS_MCP_PLATFORM``
silently defaulted to "pdf"/"net" when unset, so a misconfigured deployment served AS another
product instead of refusing to start - directly violating the rule ``infra/serve_http.py``'s own
comment states for the identical two variables.

``serve_stdio.py`` now reuses ``serve_http.configure_logging`` and ``serve_http._required_env``
directly rather than reimplementing either, so these tests call the real ``serve_stdio.main`` end
to end: ``run_stdio`` is replaced by a recorder (the same technique
``tests/infra/test_serve_logging.py``'s ``test_main_wires_json_logging_and_grace_into_uvicorn``
uses for ``uvicorn.run``), never a copy of the wiring it exercises.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path

import anyio
import pytest

# infra/ is not a package (no __init__.py, matching scripts/ convention) - import both modules
# directly from their path, the same way tests/infra/test_serve_http.py and
# tests/infra/test_helm_manifest_step.py already do.
sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402
import serve_stdio  # noqa: E402

TOOL_CALL_LOGGER = "foss_mcp.mcp.server"
# The environment variable names are the deployment contract, so the tests spell them out as
# literals rather than through serve_http's constants - a renamed constant would otherwise pass
# unnoticed, exactly as tests/infra/test_serve_logging.py already does for the log/grace vars.
FAMILY_VAR = "FOSS_MCP_FAMILY"
PLATFORM_VAR = "FOSS_MCP_PLATFORM"
LOG_LEVEL_VAR = "FOSS_MCP_LOG_LEVEL"


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


def test_main_raises_when_both_family_and_platform_are_unset(monkeypatch, restore_root_logging):
    monkeypatch.delenv(FAMILY_VAR, raising=False)
    monkeypatch.delenv(PLATFORM_VAR, raising=False)

    with pytest.raises(RuntimeError, match=FAMILY_VAR):
        anyio.run(serve_stdio.main)


def test_main_raises_naming_the_one_missing_var(monkeypatch, restore_root_logging):
    monkeypatch.setenv(FAMILY_VAR, "pdf")
    monkeypatch.delenv(PLATFORM_VAR, raising=False)

    with pytest.raises(RuntimeError, match=PLATFORM_VAR):
        anyio.run(serve_stdio.main)


def test_main_builds_deployment_config_from_the_real_env_values_not_a_default(
    monkeypatch, restore_root_logging
):
    monkeypatch.setenv(FAMILY_VAR, "slides")
    monkeypatch.setenv(PLATFORM_VAR, "java")
    monkeypatch.delenv("FOSS_MCP_SOURCE_KIND", raising=False)
    seen: dict = {}

    @contextlib.asynccontextmanager
    async def fake_run_stdio(config):
        seen["config"] = config

        async def _noop() -> None:
            return None

        yield _noop()

    monkeypatch.setattr(serve_stdio, "run_stdio", fake_run_stdio)

    anyio.run(serve_stdio.main)

    config = seen["config"]
    assert config.family == "slides"
    assert config.platform == "java"
    assert config.source_kind == "self_extracted"


def test_main_configures_logging_before_run_stdio_is_entered(capsys, monkeypatch, restore_root_logging):
    """main() must call configure_logging() before DeploymentConfig construction/run_stdio -
    proven the same way test_serve_logging.py proves it for serve_http.main(): a real log record
    emitted from inside the replaced downstream call must already reach stdout as one JSON line,
    which only happens if configure_logging() already installed its handler by then."""
    monkeypatch.delenv(LOG_LEVEL_VAR, raising=False)
    monkeypatch.setenv(FAMILY_VAR, "pdf")
    monkeypatch.setenv(PLATFORM_VAR, "net")

    @contextlib.asynccontextmanager
    async def fake_run_stdio(config):
        logging.getLogger(TOOL_CALL_LOGGER).info(
            "tool call: %s (%s)",
            "search",
            "ok",
            extra={"tool": "search", "outcome": "ok"},
        )

        async def _noop() -> None:
            return None

        yield _noop()

    monkeypatch.setattr(serve_stdio, "run_stdio", fake_run_stdio)

    anyio.run(serve_stdio.main)

    records = _stdout_json_lines(capsys)
    assert len(records) == 1
    assert records[0]["logger"] == TOOL_CALL_LOGGER
    assert records[0]["tool"] == "search"
    assert records[0]["message"] == "tool call: search (ok)"


def test_main_reuses_serve_http_configure_logging_and_required_env():
    """Fix 1 and fix 2 both reuse serve_http's own definitions directly - never a copy."""
    assert serve_stdio.configure_logging is serve_http.configure_logging
    assert serve_stdio._required_env is serve_http._required_env
