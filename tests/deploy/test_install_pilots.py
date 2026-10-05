"""G2/TC-223: scripts/deploy/install_pilots.py, offline. subprocess.run is replaced by a fake.

No helm or kubectl runs. Every test that could reach subprocess either fakes it or proves it is never
called (dry-run).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
INSTALL_SOURCE = DEPLOY_DIR / "install_pilots.py"

sys.path.insert(0, str(DEPLOY_DIR))

import install_pilots as install  # noqa: E402
import pilots  # noqa: E402

CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"


def _done(args, rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, rc, stdout, stderr)


class FakeRun:
    """Stands in for subprocess.run. Records every command; the handler decides each result."""

    def __init__(self, handler=None):
        self.calls: list[list[str]] = []
        self.handler = handler or (lambda cmd: _done(cmd))
        self._lock = threading.Lock()

    def __call__(self, cmd, **kwargs):
        with self._lock:
            self.calls.append(list(cmd))
        return self.handler(list(cmd))

    def helm_calls(self):
        return [c for c in self.calls if c[0] == "helm"]


@pytest.fixture
def no_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"subprocess must not run: {args!r}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def _fake(monkeypatch, handler=None) -> FakeRun:
    fake = FakeRun(handler)
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_helm_command_is_the_exact_upgrade_install():
    cmd = install.helm_install_command(
        "foss-mcp-pdf-net", CHART, "ns1", "rev-abc1234", Path("values/pdf_net.yaml"), "30m"
    )
    assert cmd == [
        "helm",
        "upgrade",
        "--install",
        "foss-mcp-pdf-net",
        str(CHART),
        "-n",
        "ns1",
        "--set",
        "image.tag=rev-abc1234",
        "-f",
        str(Path("values/pdf_net.yaml")),
        "--wait",
        "--timeout",
        "30m",
    ]


def test_defaults_are_parallel_two_and_timeout_thirty_minutes():
    args = install.parse_args(["--namespace", "ns", "--image-tag", "t"])
    assert args.parallel == 2
    assert args.timeout == "30m"
    assert args.only == []
    assert args.dry_run is False


def test_dry_run_prints_the_helm_command_for_one_pilot_and_runs_nothing(tmp_path, capsys, no_subprocess):
    values_dir = tmp_path / "values"
    rc = install.main(
        [
            "--namespace",
            "ns",
            "--image-tag",
            "t1",
            "--only",
            "pdf_net",
            "--dry-run",
            "--values-dir",
            str(values_dir),
        ]
    )
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0
    assert len(out) == 1
    assert out[0].startswith("helm upgrade --install foss-mcp-pdf-net ")
    assert "--set image.tag=t1" in out[0]
    assert not values_dir.exists()


def test_dry_run_covers_all_fourteen_pilots_and_never_builds_an_uninstall(tmp_path, capsys, no_subprocess):
    rc = install.main(["--namespace", "ns", "--image-tag", "t", "--dry-run", "--values-dir", str(tmp_path)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert rc == 0
    assert len(lines) == 14
    for line in lines:
        tokens = line.split()
        assert tokens[:3] == ["helm", "upgrade", "--install"]
        assert "uninstall" not in tokens
        assert "delete" not in tokens


def test_install_runs_one_helm_upgrade_per_pilot_and_writes_results(tmp_path, monkeypatch, capsys):
    fake = _fake(monkeypatch)
    rc = install.main(["--namespace", "ns", "--image-tag", "t", "--values-dir", str(tmp_path)])
    assert rc == 0
    assert len(fake.helm_calls()) == 14
    assert all(c[:3] == ["helm", "upgrade", "--install"] for c in fake.helm_calls())
    results = json.loads((tmp_path / install.RESULTS_FILE).read_text(encoding="utf-8"))
    assert len(results) == 14
    assert all(r["rc"] == 0 for r in results)
    assert len({r["release"] for r in results}) == 14
    assert "14 of 14 installed" in capsys.readouterr().out


def test_install_writes_one_values_file_per_selected_pilot(tmp_path, monkeypatch):
    _fake(monkeypatch)
    install.main(
        ["--namespace", "ns", "--image-tag", "t", "--only", "cells_go", "--values-dir", str(tmp_path)]
    )
    assert [p.name for p in tmp_path.glob("*.yaml")] == ["cells_go.yaml"]


def test_missing_namespace_is_created_before_any_install(tmp_path, monkeypatch):
    def handler(cmd):
        if cmd[:3] == ["kubectl", "get", "namespace"]:
            return _done(cmd, rc=1, stderr="not found")
        return _done(cmd)

    fake = _fake(monkeypatch, handler)
    rc = install.main(
        ["--namespace", "fresh", "--image-tag", "t", "--only", "pdf_net", "--values-dir", str(tmp_path)]
    )
    assert rc == 0
    kube = [c for c in fake.calls if c[0] == "kubectl"]
    assert kube == [["kubectl", "get", "namespace", "fresh"], ["kubectl", "create", "namespace", "fresh"]]
    assert fake.calls.index(["kubectl", "create", "namespace", "fresh"]) < fake.calls.index(
        fake.helm_calls()[0]
    )


def test_namespace_that_cannot_be_created_stops_before_helm(tmp_path, monkeypatch, capsys):
    def handler(cmd):
        if cmd[:2] == ["kubectl", "get"]:
            return _done(cmd, rc=1)
        if cmd[:2] == ["kubectl", "create"]:
            return _done(cmd, rc=1, stderr="forbidden")
        return _done(cmd)

    fake = _fake(monkeypatch, handler)
    rc = install.main(["--namespace", "ns", "--image-tag", "t", "--values-dir", str(tmp_path)])
    assert rc == 1
    assert fake.helm_calls() == []
    assert "could not create namespace ns: forbidden" in capsys.readouterr().err


def test_parallel_bound_is_never_exceeded(tmp_path, monkeypatch):
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def handler(cmd):
        if cmd[0] == "helm":
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
        return _done(cmd)

    _fake(monkeypatch, handler)
    rc = install.main(
        ["--namespace", "ns", "--image-tag", "t", "--parallel", "3", "--values-dir", str(tmp_path)]
    )
    assert rc == 0
    assert 2 <= state["peak"] <= 3


def test_parallel_below_one_is_refused():
    with pytest.raises(SystemExit):
        install.parse_args(["--namespace", "ns", "--image-tag", "t", "--parallel", "0"])


def test_failed_install_exits_nonzero_and_prints_pod_states_and_job_log(tmp_path, monkeypatch, capsys):
    def handler(cmd):
        if cmd[0] == "helm" and "foss-mcp-pdf-net" in cmd:
            return _done(cmd, rc=1, stderr="Error: timed out waiting for the condition")
        if cmd[:3] == ["kubectl", "get", "pods"]:
            return _done(cmd, stdout="NAME READY STATUS\nfoss-mcp-pdf-net-abc 0/1 Init:0/1\n")
        if cmd[:2] == ["kubectl", "logs"]:
            return _done(cmd, stdout="ingest line 7 of the log\n")
        return _done(cmd)

    _fake(monkeypatch, handler)
    rc = install.main(
        ["--namespace", "ns", "--image-tag", "t", "--only", "pdf_net", "--values-dir", str(tmp_path)]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAILED" in out
    assert "pod states" in out and "Init:0/1" in out
    assert "last 20 lines of the ingestion Job log" in out and "ingest line 7 of the log" in out
    assert "timed out waiting" in out
    results = json.loads((tmp_path / install.RESULTS_FILE).read_text(encoding="utf-8"))
    assert results[0]["rc"] == 1


def test_failed_install_asks_for_the_last_twenty_log_lines_of_its_own_release(tmp_path, monkeypatch):
    def handler(cmd):
        if cmd[0] == "helm" and "foss-mcp-pdf-net" in cmd:
            return _done(cmd, rc=1, stderr="boom")
        return _done(cmd)

    fake = _fake(monkeypatch, handler)
    install.main(
        ["--namespace", "ns", "--image-tag", "t", "--only", "pdf_net", "--values-dir", str(tmp_path)]
    )
    logs = [c for c in fake.calls if c[:2] == ["kubectl", "logs"]]
    assert len(logs) == 1
    assert "--tail=20" in logs[0]
    assert "app.kubernetes.io/instance=foss-mcp-pdf-net,app.kubernetes.io/component=ingestion" in logs[0]


def test_a_missing_helm_binary_is_reported_as_a_failed_install(tmp_path, monkeypatch, capsys):
    def handler(cmd):
        if cmd[0] == "helm":
            raise FileNotFoundError("helm")
        return _done(cmd)

    _fake(monkeypatch, handler)
    rc = install.main(
        ["--namespace", "ns", "--image-tag", "t", "--only", "pdf_net", "--values-dir", str(tmp_path)]
    )
    assert rc == 1
    assert "FileNotFoundError" in capsys.readouterr().out


def test_no_command_is_ever_an_uninstall_or_a_delete_even_when_installs_fail(tmp_path, monkeypatch):
    def handler(cmd):
        if cmd[0] == "helm":
            return _done(cmd, rc=1, stderr="failed")
        return _done(cmd)

    fake = _fake(monkeypatch, handler)
    install.main(["--namespace", "ns", "--image-tag", "t", "--values-dir", str(tmp_path)])
    assert fake.calls
    for cmd in fake.calls:
        assert "uninstall" not in cmd
        assert "delete" not in cmd
        assert not (cmd[0] == "helm" and cmd[1] not in ("upgrade",))


def test_the_installer_source_names_no_uninstall_or_delete_command():
    text = INSTALL_SOURCE.read_text(encoding="utf-8")
    assert '"uninstall"' not in text
    assert '"delete"' not in text
    assert "helm uninstall" not in text


def test_unknown_only_name_exits_two_without_running_anything(tmp_path, monkeypatch, capsys):
    fake = _fake(monkeypatch)
    rc = install.main(
        ["--namespace", "ns", "--image-tag", "t", "--only", "nope_x", "--values-dir", str(tmp_path)]
    )
    assert rc == 2
    assert fake.calls == []
    assert "unknown pilot" in capsys.readouterr().err


def test_results_and_selection_use_the_chart_pilots(tmp_path, monkeypatch):
    _fake(monkeypatch)
    install.main(["--namespace", "ns", "--image-tag", "t", "--values-dir", str(tmp_path)])
    results = json.loads((tmp_path / install.RESULTS_FILE).read_text(encoding="utf-8"))
    chart_keys = {pilots.pilot_key(p) for p in pilots.load_pilots(pilots.CHART_VALUES)}
    assert {r["pilot"] for r in results} == chart_keys
