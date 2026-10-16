"""G3/TC-512: install_pilots.py --wait-for-jobs, offline. subprocess.run is replaced by a fake.

`helm --wait` does not wait for Jobs, so a pilot whose ingestion FAILED while its serving pod kept
running the previous generation used to be reported installed. These tests pin the opt-in flag, the
Job classification and the reporting. No helm or kubectl runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
sys.path.insert(0, str(DEPLOY_DIR))

import install_pilots as install  # noqa: E402

CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
BASE_ARGS = ["--namespace", "ns", "--image-tag", "t", "--only", "pdf_net"]


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

    def job_status_calls(self):
        return [c for c in self.calls if c[:3] == ["kubectl", "get", "job"]]


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


def _jobs(*statuses: dict) -> str:
    return json.dumps({"items": [{"status": status} for status in statuses]})


SUCCEEDED = _jobs({"succeeded": 1})
FAILED_BY_CONDITION = _jobs({"conditions": [{"type": "Failed", "status": "True"}]})
FAILED_BY_COUNT = _jobs({"failed": 3})
RUNNING = _jobs({"active": 1})


def _handler(job_status):
    """A fake where helm succeeds and `kubectl get job` answers job_status (a str, or a callable)."""

    def handler(cmd):
        if cmd[:3] == ["kubectl", "get", "job"]:
            return job_status(cmd) if callable(job_status) else _done(cmd, stdout=job_status)
        if cmd[:3] == ["kubectl", "get", "pods"]:
            return _done(cmd, stdout="NAME READY STATUS\nfoss-mcp-pdf-net-abc 1/1 Running\n")
        if cmd[:2] == ["kubectl", "logs"]:
            return _done(cmd, stdout="ingest failure line\n")
        return _done(cmd)

    return handler


def _run_main(tmp_path, *extra) -> int:
    return install.main([*BASE_ARGS, "--values-dir", str(tmp_path), *extra])


def _results(tmp_path) -> list[dict]:
    return json.loads((tmp_path / install.RESULTS_FILE).read_text(encoding="utf-8"))


def _helm_cmd(**kwargs) -> list[str]:
    return install.helm_install_command(
        "foss-mcp-pdf-net", CHART, "ns1", "rev-abc1234", Path("values/pdf_net.yaml"), "30m", **kwargs
    )


def test_default_helm_argv_has_no_wait_for_jobs():
    assert "--wait-for-jobs" not in _helm_cmd()
    assert _helm_cmd() == _helm_cmd(wait_for_jobs=False)
    assert _helm_cmd()[-3:] == ["--wait", "--timeout", "30m"]
    assert install.parse_args(BASE_ARGS).wait_for_jobs is False


def test_flag_inserts_wait_for_jobs_right_after_wait():
    default, flagged = _helm_cmd(), _helm_cmd(wait_for_jobs=True)
    assert flagged[flagged.index("--wait") + 1] == "--wait-for-jobs"
    assert [t for t in flagged if t != "--wait-for-jobs"] == default
    assert flagged[-2:] == ["--timeout", "30m"]
    assert install.parse_args([*BASE_ARGS, "--wait-for-jobs"]).wait_for_jobs is True


def test_dry_run_with_the_flag_shows_it(tmp_path, capsys, no_subprocess):
    assert _run_main(tmp_path, "--dry-run", "--wait-for-jobs") == 0
    helm_lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("helm ")]
    assert len(helm_lines) == 1
    assert " --wait --wait-for-jobs --timeout 30m" in helm_lines[0]
    assert _run_main(tmp_path, "--dry-run") == 0
    plain = [line for line in capsys.readouterr().out.splitlines() if line.startswith("helm ")]
    assert "--wait-for-jobs" not in plain[0]


def test_job_status_command_is_the_exact_scoped_get():
    assert install.job_status_command("ns1", "foss-mcp-pdf-net") == [
        "kubectl",
        "get",
        "job",
        "-n",
        "ns1",
        "-l",
        "app.kubernetes.io/instance=foss-mcp-pdf-net,app.kubernetes.io/component=ingestion",
        "-o",
        "json",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (SUCCEEDED, "succeeded"),
        (FAILED_BY_CONDITION, "failed"),
        (FAILED_BY_COUNT, "failed"),
        (RUNNING, "running"),
        (json.dumps({"items": []}), "missing"),
        (_jobs({"succeeded": 1}, {"failed": 2}), "failed"),
        (_jobs({"failed": 1, "succeeded": 1}), "succeeded"),
        ("this is not json", "unknown"),
        ("", "unknown"),
    ],
    ids=[
        "succeeded",
        "failed_by_condition",
        "failed_by_count",
        "running",
        "missing",
        "two_jobs_one_failed",
        "retried_then_succeeded",
        "garbage_text",
        "empty_text",
    ],
)
def test_classify_job_status(text, expected):
    assert install.classify_job_status(text) == expected


def test_helm_ok_but_failed_ingestion_is_reported_as_not_installed(tmp_path, monkeypatch, capsys):
    fake = _fake(monkeypatch, _handler(FAILED_BY_CONDITION))
    rc = _run_main(tmp_path, "--wait-for-jobs")
    out = capsys.readouterr().out
    assert rc == 1
    assert "INGESTION FAILED" in out
    assert "previous generation" in out
    assert "pod states" in out and "last 20 lines of the ingestion Job log" in out
    assert "ingest failure line" in out
    assert "0 of 1 installed" in out
    table = [line for line in out.splitlines() if line.startswith("foss-mcp-pdf-net")]
    assert table and "INGESTION FAILED" in table[0]
    assert _results(tmp_path)[0]["ingestion"] == "failed"
    assert _results(tmp_path)[0]["rc"] == 0
    assert len(fake.job_status_calls()) == 1


def test_helm_ok_and_succeeded_ingestion_is_installed(tmp_path, monkeypatch, capsys):
    _fake(monkeypatch, _handler(SUCCEEDED))
    rc = _run_main(tmp_path, "--wait-for-jobs")
    out = capsys.readouterr().out
    assert rc == 0
    assert "INGESTION FAILED" not in out and "unverified" not in out
    assert "1 of 1 installed" in out
    assert _results(tmp_path)[0]["ingestion"] == "succeeded"


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(_done(["kubectl"], stdout=RUNNING), id="running"),
        pytest.param(_done(["kubectl"], stdout="not json at all"), id="garbage"),
        pytest.param(_done(["kubectl"], rc=1, stderr="forbidden"), id="kubectl_exit_nonzero"),
    ],
)
def test_unknown_or_running_ingestion_is_installed_but_marked_unverified(
    tmp_path, monkeypatch, capsys, answer
):
    _fake(monkeypatch, _handler(lambda cmd: answer))
    rc = _run_main(tmp_path, "--wait-for-jobs")
    out = capsys.readouterr().out
    assert rc == 0
    assert "installed (ingestion unverified)" in out
    assert "1 of 1 installed" in out
    assert _results(tmp_path)[0]["ingestion"] in ("running", "unknown")


def test_a_missing_kubectl_makes_ingestion_unknown_not_failed(tmp_path, monkeypatch, capsys):
    def missing(cmd):
        raise FileNotFoundError("kubectl")

    _fake(monkeypatch, _handler(missing))
    rc = _run_main(tmp_path, "--wait-for-jobs")
    out = capsys.readouterr().out
    assert rc == 0
    assert "installed (ingestion unverified)" in out
    assert _results(tmp_path)[0]["ingestion"] == "unknown"


def test_helm_failure_with_the_flag_is_reported_exactly_as_before(tmp_path, monkeypatch, capsys):
    def handler(cmd):
        if cmd[0] == "helm":
            return _done(cmd, rc=1, stderr="Error: timed out waiting for the condition")
        return _handler(SUCCEEDED)(cmd)

    fake = _fake(monkeypatch, handler)
    rc = _run_main(tmp_path, "--wait-for-jobs")
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAILED" in out and "INGESTION FAILED" not in out
    assert "failed with rc 1: Error: timed out waiting for the condition" in out
    assert "pod states" in out
    assert "0 of 1 installed" in out
    assert _results(tmp_path)[0]["ingestion"] == ""
    assert fake.job_status_calls() == []
    assert "--wait-for-jobs" in _results(tmp_path)[0]["command"]


def test_without_the_flag_no_kubectl_get_job_is_run(tmp_path, monkeypatch, capsys):
    fake = _fake(monkeypatch, _handler(FAILED_BY_CONDITION))
    rc = _run_main(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert fake.job_status_calls() == []
    assert "INGESTION FAILED" not in out and "unverified" not in out
    assert "1 of 1 installed" in out
    assert _results(tmp_path)[0]["ingestion"] == ""
    assert "--wait-for-jobs" not in _results(tmp_path)[0]["command"]
