"""G2/TC-223: scripts/deploy/prove_pilots.py, offline.

subprocess.Popen and subprocess.run are replaced by fakes, and urllib answers from a table. The proof
report each fake proof writes is the summary the real proof script would write.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"

sys.path.insert(0, str(DEPLOY_DIR))

import prove_pilots as prove  # noqa: E402


class FakePopen:
    """A port-forward that stays up until it is stopped."""

    instances: list[FakePopen] = []

    def __init__(self, args, **kwargs):
        self.args = list(args)
        self.terminated = False
        self.killed = False
        self.wait_raises_first = False
        FakePopen.instances.append(self)

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.wait_raises_first:
            self.wait_raises_first = False
            raise subprocess.TimeoutExpired(self.args, timeout)
        return 0

    def kill(self):
        self.killed = True


class Answer:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def fast_and_isolated(monkeypatch):
    FakePopen.instances = []
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(prove, "HEALTH_POLL_S", 0)
    monkeypatch.setattr(prove, "HEALTH_TIMEOUT_S", 0.05)


def _health_200(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: Answer(200))


def _proof_writes(summary, rc=None):
    """A subprocess.run fake that writes a proof report with the given summary for each pilot."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        report_path = Path(cmd[cmd.index("--report") + 1])
        applicable = summary["applicable"]
        report = {
            "summary": summary,
            "all_applicable_passed": applicable > 0 and summary["fail"] == 0,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        code = rc if rc is not None else (0 if summary["fail"] == 0 and applicable > 0 else 1)
        return subprocess.CompletedProcess(cmd, code)

    return fake_run, calls


PASSING = {"applicable": 11, "pass": 11, "fail": 0, "not_applicable": 0}


def test_every_pilot_passing_every_check_exits_zero(tmp_path, monkeypatch, capsys):
    _health_200(monkeypatch)
    fake_run, calls = _proof_writes(PASSING)
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = prove.main(["--namespace", "ns", "--report-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert len(calls) == 20
    assert "20 of 20 pilots passed every applicable check" in out
    assert all(p.terminated for p in FakePopen.instances)


def test_a_failed_check_exits_nonzero(tmp_path, monkeypatch, capsys):
    _health_200(monkeypatch)
    failing = {"applicable": 11, "pass": 10, "fail": 1, "not_applicable": 0}
    fake_run, _ = _proof_writes(failing, rc=1)
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = prove.main(["--namespace", "ns", "--only", "pdf_net", "--report-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out
    assert "0 of 1 pilots passed" in out


def test_a_pilot_with_no_applicable_check_is_named_and_not_a_pass(tmp_path, monkeypatch, capsys):
    _health_200(monkeypatch)
    nothing = {"applicable": 0, "pass": 0, "fail": 0, "not_applicable": 11}
    fake_run, _ = _proof_writes(nothing, rc=1)
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = prove.main(["--namespace", "ns", "--only", "cells_cpp", "--report-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no applicable checks ran for: foss-mcp-cells-cpp" in out


def test_port_forward_is_stopped_when_a_proof_raises(tmp_path, monkeypatch, capsys):
    _health_200(monkeypatch)

    def raising(cmd, **kwargs):
        raise RuntimeError("proof blew up")

    monkeypatch.setattr(subprocess, "run", raising)
    rc = prove.main(["--namespace", "ns", "--only", "pdf_net", "--report-dir", str(tmp_path)])
    assert rc == 1
    assert len(FakePopen.instances) == 1
    assert FakePopen.instances[0].terminated is True
    assert "proof blew up" in capsys.readouterr().out


def test_port_forward_is_stopped_when_a_proof_raises_on_every_pilot(tmp_path, monkeypatch):
    _health_200(monkeypatch)

    def raising(cmd, **kwargs):
        raise RuntimeError("proof blew up")

    monkeypatch.setattr(subprocess, "run", raising)
    rc = prove.main(["--namespace", "ns", "--report-dir", str(tmp_path)])
    assert rc == 1
    assert len(FakePopen.instances) == 20
    assert all(p.terminated for p in FakePopen.instances)


def test_a_port_forward_that_will_not_terminate_is_killed(tmp_path, monkeypatch):
    _health_200(monkeypatch)
    fake_run, _ = _proof_writes(PASSING)
    monkeypatch.setattr(subprocess, "run", fake_run)
    original_init = FakePopen.__init__

    def stubborn_init(self, args, **kwargs):
        original_init(self, args, **kwargs)
        self.wait_raises_first = True

    monkeypatch.setattr(FakePopen, "__init__", stubborn_init)
    rc = prove.main(["--namespace", "ns", "--only", "pdf_net", "--report-dir", str(tmp_path)])
    assert rc == 0
    assert FakePopen.instances[0].killed is True


def test_a_pilot_that_never_becomes_ready_is_not_proved(tmp_path, monkeypatch):
    def unready(url, timeout=None):
        if url.endswith("/readyz"):
            raise urllib.error.URLError("503")
        return Answer(200)

    monkeypatch.setattr(urllib.request, "urlopen", unready)
    fake_run, calls = _proof_writes(PASSING)
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = prove.main(["--namespace", "ns", "--only", "pdf_net", "--report-dir", str(tmp_path)])
    assert rc == 1
    assert calls == []
    assert FakePopen.instances[0].terminated is True


def test_each_pilot_gets_its_own_local_port_from_the_base(tmp_path, monkeypatch):
    _health_200(monkeypatch)
    fake_run, _ = _proof_writes(PASSING)
    monkeypatch.setattr(subprocess, "run", fake_run)
    prove.main(["--namespace", "ns", "--base-port", "9000", "--report-dir", str(tmp_path)])
    ports = [p.args[-1] for p in FakePopen.instances]
    assert ports == [f"{9000 + i}:80" for i in range(20)]
    assert len(set(ports)) == 20


def test_port_forward_targets_the_release_service_in_the_namespace(tmp_path, monkeypatch):
    _health_200(monkeypatch)
    fake_run, _ = _proof_writes(PASSING)
    monkeypatch.setattr(subprocess, "run", fake_run)
    prove.main(["--namespace", "ns", "--only", "pdf_net", "--report-dir", str(tmp_path)])
    args = FakePopen.instances[0].args
    assert args[:6] == ["kubectl", "port-forward", "-n", "ns", "svc/foss-mcp-pdf-net", "8401:80"]


def test_the_proof_runs_against_the_forwarded_endpoint_for_its_pilot(tmp_path, monkeypatch):
    _health_200(monkeypatch)
    fake_run, calls = _proof_writes(PASSING)
    monkeypatch.setattr(subprocess, "run", fake_run)
    prove.main(["--namespace", "ns", "--only", "cells_rust", "--report-dir", str(tmp_path)])
    cmd = calls[0]
    assert cmd[1] == str(prove.PROOF_SCRIPT)
    assert cmd[cmd.index("--pilot") + 1] == "cells_rust"
    assert cmd[cmd.index("--url") + 1] == "http://127.0.0.1:8401"
    assert cmd[cmd.index("--fixtures") + 1] == str(prove.FIXTURES)


def test_unknown_only_name_exits_two(tmp_path, capsys):
    rc = prove.main(["--namespace", "ns", "--only", "nope_x", "--report-dir", str(tmp_path)])
    assert rc == 2
    assert FakePopen.instances == []
    assert "unknown pilot" in capsys.readouterr().err


def test_the_prover_source_names_no_uninstall_or_delete_command():
    text = (DEPLOY_DIR / "prove_pilots.py").read_text(encoding="utf-8")
    assert '"uninstall"' not in text
    assert '"delete"' not in text
