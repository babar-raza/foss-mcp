"""G3/TC-513: scripts/deploy/prove_pilots.py forwards the proof's timeout and minimum-content flags and prints its warnings. Offline.

subprocess.Popen and subprocess.run are replaced by fakes, as in tests/deploy/test_prove_pilots.py; the proof report each fake
proof writes holds the summary the real proof script would write, and optionally its warnings.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"

sys.path.insert(0, str(DEPLOY_DIR))

import prove_pilots as prove  # noqa: E402

PASSING = {"applicable": 11, "pass": 11, "fail": 0, "not_applicable": 0}


class FakePopen:
    """A port-forward that stays up until it is stopped."""

    def __init__(self, args: Any, **kwargs: Any) -> None:
        self.args = list(args)

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


class Answer:
    status = 200

    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture(autouse=True)
def fast_and_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(prove, "HEALTH_POLL_S", 0)
    monkeypatch.setattr(prove, "HEALTH_TIMEOUT_S", 0.05)
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: Answer())


def _proof(monkeypatch: pytest.MonkeyPatch, warnings: dict[str, list[str]] | None = None) -> list[list[str]]:
    """A subprocess.run fake that writes a passing proof report for each pilot; returns the recorded argv list."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        report_path = Path(cmd[cmd.index("--report") + 1])
        pilot = cmd[cmd.index("--pilot") + 1]
        report: dict[str, Any] = {"summary": PASSING, "all_applicable_passed": True}
        if warnings is not None:
            report["warnings"] = warnings.get(pilot, [])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _prove(tmp_path: Path, *extra: str) -> int:
    return int(
        prove.main(["--namespace", "ns", "--only", "cells_rust", "--report-dir", str(tmp_path), *extra])
    )


def test_default_proof_argv_is_unchanged_and_has_no_new_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _proof(monkeypatch)
    assert _prove(tmp_path) == 0
    assert calls == [
        [
            sys.executable,
            str(prove.PROOF_SCRIPT),
            "--pilot",
            "cells_rust",
            "--url",
            "http://127.0.0.1:8401",
            "--fixtures",
            str(prove.FIXTURES),
            "--report",
            str(tmp_path / "cells_rust.json"),
        ]
    ]
    for flag in ("--call-timeout", "--find-examples-timeout", "--min-content"):
        assert flag not in calls[0]


def test_timeout_and_min_content_flags_are_forwarded_to_the_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _proof(monkeypatch)
    assert _prove(tmp_path, "--call-timeout", "30", "--find-examples-timeout", "90", "--min-content") == 0
    cmd = calls[0]
    assert cmd[-5:] == ["--call-timeout", "30.0", "--find-examples-timeout", "90.0", "--min-content"]
    only_one = _proof(monkeypatch)
    _prove(tmp_path, "--find-examples-timeout", "45")
    assert only_one[0][-2:] == ["--find-examples-timeout", "45.0"]
    assert "--call-timeout" not in only_one[0]
    assert "--min-content" not in only_one[0]
    flag_only = _proof(monkeypatch)
    _prove(tmp_path, "--min-content")
    assert flag_only[0][-1] == "--min-content"
    assert flag_only[0].count("--min-content") == 1


def test_warnings_in_the_proof_report_are_printed_after_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    text = "cells_rust: the extraction left 2 unresolved symbol(s) (see the fixture's unresolved list)"
    _proof(monkeypatch, {"cells_rust": [text]})
    assert _prove(tmp_path) == 0
    lines = capsys.readouterr().out.splitlines()
    wanted = f"WARNING foss-mcp-cells-rust: {text}"
    assert wanted in lines
    table_end = max(index for index, line in enumerate(lines) if line.rstrip().endswith("PASS"))
    assert lines.index(wanted) > table_end


def test_a_report_without_warnings_prints_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _proof(monkeypatch)  # the report has no "warnings" key at all
    assert _prove(tmp_path) == 0
    assert "WARNING" not in capsys.readouterr().out
    _proof(monkeypatch, {})  # an empty warnings list
    assert _prove(tmp_path) == 0
    assert "WARNING" not in capsys.readouterr().out
    assert prove.report_warnings(tmp_path / "absent.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert prove.report_warnings(broken) == []


def test_no_stale_fourteen_in_the_deploy_tool_docstrings() -> None:
    for name in ("prove_pilots.py", "pilots.py"):
        text = (DEPLOY_DIR / name).read_text(encoding="utf-8")
        assert re.search("fourteen", text, re.IGNORECASE) is None, name
