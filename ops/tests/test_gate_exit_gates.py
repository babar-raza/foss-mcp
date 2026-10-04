"""Canary tests for the D8 gate-exit live-content smoke step (DECISION_LOG 2026-10-04).

These fake the Docker probe and the test run. They prove the three outcomes are distinct:
an absent engine is BLOCKED_ENV, a passing smoke is PASS, and a failing smoke is FAIL.
A missing engine must never look like a pass.
"""

from __future__ import annotations

import gatecli as C


def _fake_run(responses):
    calls = []

    def run(cmd, cwd=None, env=None, timeout=None, text=True):
        calls.append(cmd)
        key = "docker" if cmd[0] == "docker" else "pytest"
        return responses[key]

    return run, calls


def test_no_docker_engine_is_blocked_env_not_a_pass(monkeypatch):
    run, calls = _fake_run({"docker": (1, "", "engine not reachable"), "pytest": (0, "", "")})
    monkeypatch.setattr(C.G, "run", run)
    status, _ = C._live_smoke()
    assert status == "BLOCKED_ENV"
    assert not any("pytest" in c for c in calls), "must not run the live tests without an engine"


def test_a_passing_live_smoke_is_pass(monkeypatch):
    run, calls = _fake_run({"docker": (0, "", ""), "pytest": (0, "1 passed\n", "")})
    monkeypatch.setattr(C.G, "run", run)
    status, detail = C._live_smoke()
    assert status == "PASS"
    assert "1 passed" in detail


def test_a_failing_live_smoke_is_fail_not_blocked(monkeypatch):
    run, _ = _fake_run({"docker": (0, "", ""), "pytest": (1, "1 failed\n", "")})
    monkeypatch.setattr(C.G, "run", run)
    status, _ = C._live_smoke()
    assert status == "FAIL"


def test_the_live_smoke_is_wired_into_gate_exit():
    # Liveness control: the step must be called from the real gate-exit path.
    import inspect

    src = inspect.getsource(C.cmd_gate_exit)
    assert "_live_smoke()" in src
    assert "owner_open" in src, "an OPEN owner item consumed by the gate must block gate-exit"
    assert "BLOCKED_ENV" in src, "a blocked card must be reported as BLOCKED_ENV, not FAIL"
