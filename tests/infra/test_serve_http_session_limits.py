"""G3/TC-421 (REQ-G3-006): the HTTP transport bounds MCP sessions, so abandoned sessions cannot
exhaust a peer-run container's memory.

An independent reviewer opened MCP sessions (initialize plus one call, never closed) against the
standalone image under the demo limits (192m): each session costs about 100 KB and the container was
OOM-killed after roughly 1,300 sessions. ``infra/serve_http.py`` used to build the transport with the
SDK defaults (30 minutes idle, 10,000 sessions). These tests pin the explicit limits.

Everything is offline and deterministic: the app is built by the real ``serve_http.build_app`` and the
limits are read back from the real ``StreamableHTTPSessionManager`` the SDK configured - no mock of
the session manager, no clock, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import DEFAULT_MAX_SESSIONS, DEFAULT_SESSION_IDLE_TIMEOUT

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig

sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

DEPLOYMENT_CONFIG = DeploymentConfig(family="pdf", platform="net")


def _build_and_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> Server:
    """Run the real ``build_app`` and return the real ``Server`` it built (the session manager the
    SDK configured hangs off it)."""
    captured: list[Server] = []
    real_create_server = serve_http.create_server

    def spy(*args: object, **kw: object) -> Server:
        server = real_create_server(*args, **kw)  # type: ignore[arg-type]
        captured.append(server)
        return server

    monkeypatch.setattr(serve_http, "create_server", spy)
    serve_http.build_app(
        DEPLOYMENT_CONFIG,
        manifest_store=GenerationManifestStore(tmp_path / "manifests"),
        allowed_origins=[],
        **kwargs,  # type: ignore[arg-type]
    )
    assert len(captured) == 1
    return captured[0]


def _assert_bounded(server: Server) -> None:
    """The defect-shaped check: raise unless the transport is tighter than the SDK defaults."""
    manager = server.session_manager
    assert manager.session_idle_timeout is not None, "sessions never expire"
    assert manager.max_sessions is not None, "session count is unbounded"
    assert manager.session_idle_timeout < DEFAULT_SESSION_IDLE_TIMEOUT, (
        f"idle timeout {manager.session_idle_timeout} is not tighter than the SDK default"
    )
    assert manager.max_sessions < DEFAULT_MAX_SESSIONS, (
        f"max sessions {manager.max_sessions} is not tighter than the SDK default"
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(serve_http.SESSION_IDLE_ENV, raising=False)
    monkeypatch.delenv(serve_http.MAX_SESSIONS_ENV, raising=False)


def test_constants_have_the_stated_values() -> None:
    assert serve_http.SESSION_IDLE_TIMEOUT_SECONDS == 300
    assert serve_http.MAX_SESSIONS == 200
    assert serve_http.SESSION_IDLE_ENV == "FOSS_MCP_SESSION_IDLE_SECONDS"
    assert serve_http.MAX_SESSIONS_ENV == "FOSS_MCP_MAX_SESSIONS"


def test_sdk_defaults_are_what_the_card_was_written_against() -> None:
    # If the SDK ever changes its defaults, the card's premise (and the negative below) must be re-read.
    assert DEFAULT_SESSION_IDLE_TIMEOUT == 1800
    assert DEFAULT_MAX_SESSIONS == 10_000


def test_app_is_built_with_the_explicit_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _build_and_capture(tmp_path, monkeypatch)
    manager = server.session_manager
    assert manager.session_idle_timeout == serve_http.SESSION_IDLE_TIMEOUT_SECONDS == 300
    assert manager.max_sessions == serve_http.MAX_SESSIONS == 200
    assert manager.stateless is False
    _assert_bounded(server)


def test_streamable_http_app_receives_the_limits_as_keyword_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    real = Server.streamable_http_app

    def spy(self: Server, **kwargs: object) -> object:
        calls.append(kwargs)
        return real(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Server, "streamable_http_app", spy)
    serve_http.build_app(
        DEPLOYMENT_CONFIG,
        manifest_store=GenerationManifestStore(tmp_path / "manifests"),
        allowed_origins=[],
    )
    assert len(calls) == 1
    assert calls[0]["session_idle_timeout"] == 300
    assert calls[0]["max_sessions"] == 200


def test_environment_overrides_are_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(serve_http.SESSION_IDLE_ENV, "42")
    monkeypatch.setenv(serve_http.MAX_SESSIONS_ENV, "7")
    assert serve_http.session_idle_timeout_seconds() == 42
    assert serve_http.max_sessions() == 7
    manager = _build_and_capture(tmp_path, monkeypatch).session_manager
    assert manager.session_idle_timeout == 42
    assert manager.max_sessions == 7


def test_blank_environment_values_fall_back_to_the_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(serve_http.SESSION_IDLE_ENV, "  ")
    monkeypatch.setenv(serve_http.MAX_SESSIONS_ENV, "")
    assert serve_http.session_idle_timeout_seconds() == serve_http.SESSION_IDLE_TIMEOUT_SECONDS
    assert serve_http.max_sessions() == serve_http.MAX_SESSIONS


def test_explicit_arguments_win_over_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(serve_http.SESSION_IDLE_ENV, "42")
    monkeypatch.setenv(serve_http.MAX_SESSIONS_ENV, "7")
    server = _build_and_capture(tmp_path, monkeypatch, session_idle_timeout=11, max_session_count=3)
    assert server.session_manager.session_idle_timeout == 11
    assert server.session_manager.max_sessions == 3


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "1.5"])
@pytest.mark.parametrize(
    ("variable", "reader"),
    [
        ("FOSS_MCP_SESSION_IDLE_SECONDS", "session_idle_timeout_seconds"),
        ("FOSS_MCP_MAX_SESSIONS", "max_sessions"),
    ],
)
def test_bad_values_are_rejected_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, variable: str, reader: str, bad: str
) -> None:
    monkeypatch.setenv(variable, bad)
    with pytest.raises(ValueError) as excinfo:
        getattr(serve_http, reader)()
    assert variable in str(excinfo.value)
    assert bad not in str(excinfo.value)


@pytest.mark.parametrize(
    ("variable", "bad"),
    [
        ("FOSS_MCP_SESSION_IDLE_SECONDS", "abc"),
        ("FOSS_MCP_SESSION_IDLE_SECONDS", "0"),
        ("FOSS_MCP_MAX_SESSIONS", "not-a-number"),
        ("FOSS_MCP_MAX_SESSIONS", "-1"),
    ],
)
def test_main_exits_2_with_one_line_on_a_bad_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], variable: str, bad: str
) -> None:
    monkeypatch.setattr(serve_http, "configure_logging", lambda: None)
    monkeypatch.setenv(serve_http.FAMILY_ENV, "pdf")
    monkeypatch.setenv(serve_http.PLATFORM_ENV, "net")
    monkeypatch.setenv(variable, bad)

    def must_not_build(*args: object, **kwargs: object) -> object:
        raise AssertionError("build_app must not be reached with an invalid setting")

    monkeypatch.setattr(serve_http, "build_app", must_not_build)
    with pytest.raises(SystemExit) as excinfo:
        serve_http.main()
    assert excinfo.value.code == 2
    err_lines = capsys.readouterr().err.strip().splitlines()
    assert len(err_lines) == 1
    assert variable in err_lines[0]
    assert bad not in err_lines[0]
    assert "Traceback" not in err_lines[0]


def test_main_passes_the_limits_to_build_app_and_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    monkeypatch.setattr(serve_http, "configure_logging", lambda: None)
    monkeypatch.setenv(serve_http.FAMILY_ENV, "pdf")
    monkeypatch.setenv(serve_http.PLATFORM_ENV, "net")
    monkeypatch.setenv(serve_http.MAX_SESSIONS_ENV, "9")
    built: list[dict[str, object]] = []
    ran: list[object] = []

    def fake_build_app(config: object, **kwargs: object) -> str:
        built.append(kwargs)
        return "app"

    monkeypatch.setattr(serve_http, "build_app", fake_build_app)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: ran.append(app))
    serve_http.main()
    assert built == [{"session_idle_timeout": 300, "max_session_count": 9}]
    assert ran == ["app"]


def test_building_with_the_sdk_defaults_is_detected_as_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect-shaped negative: the pre-TC-421 construction (SDK defaults) must fail the bound check."""
    server = _build_and_capture(
        tmp_path,
        monkeypatch,
        session_idle_timeout=DEFAULT_SESSION_IDLE_TIMEOUT,
        max_session_count=DEFAULT_MAX_SESSIONS,
    )
    assert server.session_manager.session_idle_timeout == 1800
    assert server.session_manager.max_sessions == 10_000
    with pytest.raises(AssertionError, match="not tighter than the SDK default"):
        _assert_bounded(server)
