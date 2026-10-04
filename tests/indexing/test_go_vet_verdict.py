"""Offline tests for the Go example verifier's build-and-vet verdict
(src/foss_mcp/indexing/example_verifier.py, verify_go_example; TC-187, REQ-G2-048).

No network and no Go toolchain: ``_run`` is replaced with a fake that returns
scripted results per argument list. Any argument list that was not scripted
raises, so a call the verifier makes that these tests did not anticipate
fails loudly instead of passing silently. The real go build and go vet runs
are covered by tests/indexing/test_example_verifier.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample

GO_MODULE_PATH = "github.com/example/fake-go-library"

BUILD_ARGS = ["go", "build", "./..."]
VET_ARGS = ["go", "vet", "./..."]

_CANDIDATE_CODE = 'fmt.Println("hello")\n'


def _candidate() -> CandidateExample:
    return CandidateExample(
        title="Verdict fixture",
        description="",
        language="go",
        code=_CANDIDATE_CODE,
    )


def _completed(
    args: list[str], returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout=stdout, stderr=stderr
    )


def _install_scripted_run(
    monkeypatch: pytest.MonkeyPatch,
    scripted: dict[tuple[str, ...], subprocess.CompletedProcess[str]],
) -> list[list[str]]:
    """Replace ``_run`` with a fake that answers each scripted argument list
    and records every call it receives, in order.
    """
    calls: list[list[str]] = []

    def fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        key = tuple(args)
        if key not in scripted:
            raise AssertionError(f"unscripted _run call: {args!r} in {cwd}")
        return scripted[key]

    monkeypatch.setattr(ev, "_run", fake_run)
    return calls


def test_build_and_vet_both_pass_verifies_the_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_scripted_run(
        monkeypatch,
        {
            tuple(BUILD_ARGS): _completed(BUILD_ARGS, 0, stdout="build ok\n"),
            tuple(VET_ARGS): _completed(VET_ARGS, 0),
        },
    )

    result = ev.verify_go_example(
        _candidate(),
        module_path=GO_MODULE_PATH,
        library_dir=tmp_path,
        workdir=tmp_path,
    )

    assert isinstance(result, ev.VerificationResult)
    assert result.verified is True, result.output
    assert calls == [BUILD_ARGS, VET_ARGS]


def test_build_passes_but_vet_fails_is_not_verified_and_reports_vet_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vet_message = "./main.go:5:2: fmt.Printf format %d reads arg #1, but call has 0 args"
    calls = _install_scripted_run(
        monkeypatch,
        {
            tuple(BUILD_ARGS): _completed(BUILD_ARGS, 0),
            tuple(VET_ARGS): _completed(VET_ARGS, 1, stderr=vet_message + "\n"),
        },
    )

    result = ev.verify_go_example(
        _candidate(),
        module_path=GO_MODULE_PATH,
        library_dir=tmp_path,
        workdir=tmp_path,
    )

    assert isinstance(result, ev.VerificationResult)
    assert result.verified is False
    assert vet_message in result.output
    assert calls == [BUILD_ARGS, VET_ARGS]


def test_build_failure_is_not_verified_and_vet_is_never_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    build_message = "./main.go:3:2: undefined: FrobnicateNonexistentMethodThatDoesNotExist"
    calls = _install_scripted_run(
        monkeypatch,
        {
            tuple(BUILD_ARGS): _completed(BUILD_ARGS, 1, stderr=build_message + "\n"),
        },
    )

    result = ev.verify_go_example(
        _candidate(),
        module_path=GO_MODULE_PATH,
        library_dir=tmp_path,
        workdir=tmp_path,
    )

    assert isinstance(result, ev.VerificationResult)
    assert result.verified is False
    assert build_message in result.output
    assert calls == [BUILD_ARGS]
    assert VET_ARGS not in calls
