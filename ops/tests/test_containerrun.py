"""Tests for the D2 container runner (DECISION_LOG 2026-10-04).

These test the pure parts: mode selection, eligibility, the exact argv, and what the
container is allowed to see. They need no Docker. The environment-matrix canary, which
does need Docker, is a separate step and is recorded in the decision log.
"""

from __future__ import annotations

from pathlib import Path

import containerrun as CR
import gatectl as G
import pytest

REPO = Path(__file__).resolve().parents[2]


def test_the_default_runner_is_the_host_runner(monkeypatch):
    monkeypatch.delenv("FOSS_MCP_VERIFY_RUNNER", raising=False)
    assert CR.runner_mode() == "host"


def test_an_unknown_runner_mode_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.setenv("FOSS_MCP_VERIFY_RUNNER", "docker-ish")
    with pytest.raises(ValueError, match="FOSS_MCP_VERIFY_RUNNER"):
        CR.runner_mode()


@pytest.mark.parametrize("requires", [["msvc"], ["docker"], ["go", "msvc"]])
def test_a_card_needing_a_host_only_capability_is_not_container_eligible(requires):
    ok, why = CR.eligible({"requires": requires})
    assert ok is False
    assert "host-only" in why


def test_a_card_needing_only_container_capabilities_is_eligible():
    assert CR.eligible({"requires": ["go", "node", "rust"]}) == (True, "")
    assert CR.eligible({}) == (True, "")


def test_braces_python_becomes_the_image_python_and_no_host_path_leaks():
    cmd = CR.container_command("{python} -m pytest tests/x -q", None)
    assert cmd == "python -m pytest tests/x -q"
    assert ":\\" not in cmd, "no host drive path may reach the container command"


def test_a_junit_report_is_written_under_the_mounted_junit_directory():
    cmd = CR.container_command("{python} -m pytest tests/x -q", "0.xml")
    assert cmd.endswith('"--junitxml=/junit/0.xml"')


def test_an_offline_container_sees_no_proxy_and_no_host_path():
    env = CR.container_env(True, {"PYTHONUTF8": "1", "TZ": "UTC"})
    assert env["GATECTL_OFFLINE"] == "1"
    assert "HTTP_PROXY" not in env and "HTTPS_PROXY" not in env
    assert "PATH" not in env, "the image's own PATH is used, never the host's"
    assert env["PYTHONPATH"] == "/work/src"


def test_an_offline_card_gets_no_network_at_the_docker_boundary():
    argv = CR.docker_run_argv(
        workdir=r"E:\wt",
        junit_dir=r"E:\wt.junit",
        cwd_rel=".",
        command="python -m pytest -q",
        env={"PYTHONUTF8": "1"},
        offline=True,
    )
    i = argv.index("--network")
    assert argv[i + 1] == "none"


def test_an_online_card_gets_the_bridge_network_only_when_it_declares_network():
    argv = CR.docker_run_argv(
        workdir=r"E:\wt",
        junit_dir=r"E:\wt.junit",
        cwd_rel=".",
        command="python -m pytest -q",
        env={},
        offline=False,
    )
    assert argv[argv.index("--network") + 1] == "bridge"


def test_the_argv_is_a_list_with_the_command_as_one_argument_and_a_fixed_shape():
    argv = CR.docker_run_argv(
        workdir=r"E:\wt",
        junit_dir=r"E:\wt.junit",
        cwd_rel="sub",
        command="python -m pytest -q; echo done",
        env={"A": "1"},
        offline=True,
    )
    assert isinstance(argv, list)
    assert argv[0:2] == ["docker", "run"]
    assert argv[-3:] == [CR.VERIFY_IMAGE, "-c", "python -m pytest -q; echo done"]
    assert "/work/sub" in argv, "cwd is mapped inside the mount, normalised"


def test_the_check_runner_is_wired_into_do_verify_before_any_check():
    # Liveness control (AGENTS, Integration and liveness): the container branch must be
    # reachable from the real verify path, and the runner must be decided before a check runs.
    import gateverify as V

    src = Path(V.__file__).read_text(encoding="utf-8")
    start = src.index("def do_verify(")
    body = src[start : src.index("\ndef ", start + 1)]
    assert body.index("runner_used = ") < body.index(
        "run_checks(card, wt, py, log_lines, runner=runner_used)"
    )
    assert "runner=runner_used" in body


def test_the_runner_is_recorded_in_the_receipt_fingerprint():
    import gateverify as V

    src = Path(V.__file__).read_text(encoding="utf-8")
    assert '"verify_runner": runner_used' in src
    assert G.CANONICAL_ENV, "the canonical env the container inherits must exist"
