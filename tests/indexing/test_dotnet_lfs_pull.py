"""Offline tests for TC-456 (REQ-G3-007): the .NET prepare step fetches Git LFS objects.

words/net's reference library ships its binary dependencies (packages/Aspose.JavaAttributes,
packages/Aspose.CodePorting) as Git LFS objects, declared by `filter=lfs` in the repository's
root .gitattributes. Without git-lfs - or when the LFS host is unreachable - a checkout leaves
129-byte pointer files where the real .dll belongs, and `dotnet build` then fails with thousands
of missing-type errors that say nothing about the real cause.

Everything here is offline and deterministic: ``example_verifier._run`` is replaced by a
recorder that never shells out to git, git-lfs, or dotnet, and the whole suite runs with no
network and no wall-clock thresholds.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier
from foss_mcp.indexing.example_verifier import (
    _LFS_INCLUDE_PATTERN,
    LFS_UNAVAILABLE_REASON,
    ExampleEnvironmentError,
    prepare_reference_library,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"
FURNISHED_PAGE = REPO_ROOT / "tests/fixtures/furnished/pdf_net/pages/_index.md"

REPOSITORY = "example-org/example-dotnet-library"
COMMIT = "1" * 40
CSPROJ_RELATIVE_PATH = "src/Library.csproj"

_LEGACY_SEQUENCE = ["git init", "git remote add", "git fetch --depth 1", "git checkout", "dotnet build"]
_LFS_SEQUENCE = [
    "git init",
    "git remote add",
    "git fetch --depth 1",
    "git checkout",
    "git lfs install --local",
    "git lfs pull --include",
    "dotnet build",
]
_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


@dataclass
class _Outcome:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    side_effect: Callable[[Path], None] | None = None


@dataclass
class _RunRecorder:
    """Stub for ``example_verifier._run``: records every call's argv and ``cwd``.

    Accepts any keyword arguments (only ``cwd`` is ever passed today). ``outcomes`` maps a
    0-based call index to an ``_Outcome``; an index with no entry succeeds with empty output.
    A ``side_effect`` runs before the ``CompletedProcess`` is returned, so a test can write
    files into the workdir exactly where a real git/git-lfs command would have.
    """

    outcomes: dict[int, _Outcome] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    cwds: list[Path] = field(default_factory=list)

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        index = len(self.calls)
        self.calls.append(list(args))
        cwd = kwargs.get("cwd")
        self.cwds.append(cwd)
        outcome = self.outcomes.get(index, _Outcome())
        if outcome.side_effect is not None:
            outcome.side_effect(cwd)
        return subprocess.CompletedProcess(
            args, outcome.returncode, stdout=outcome.stdout, stderr=outcome.stderr
        )


def _argv_prefixes(calls: list[list[str]]) -> list[str]:
    """Collapse each call's argv into the short label used in ``_LEGACY_SEQUENCE``/``_LFS_SEQUENCE``."""
    labels: list[str] = []
    for call in calls:
        joined = " ".join(call)
        if joined.startswith("git init"):
            labels.append("git init")
        elif joined.startswith("git remote add"):
            labels.append("git remote add")
        elif joined.startswith("git fetch --depth 1"):
            labels.append("git fetch --depth 1")
        elif joined.startswith("git lfs install --local"):
            labels.append("git lfs install --local")
        elif joined.startswith("git lfs pull --include"):
            labels.append("git lfs pull --include")
        elif joined.startswith("git checkout"):
            labels.append("git checkout")
        elif joined.startswith("dotnet build"):
            labels.append("dotnet build")
        else:
            labels.append(joined)
    return labels


def _write_gitattributes(workdir: Path, *, declares_lfs: bool) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    text = "packages/** filter=lfs diff=lfs merge=lfs -text\n" if declares_lfs else "* text=auto eol=lf\n"
    (workdir / ".gitattributes").write_text(text, encoding="utf-8")


def _write_pointer_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{_POINTER_PREFIX}\noid sha256:{'0' * 64}\nsize 12345\n", encoding="utf-8")


def _write_real_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x01\x02 not a pointer file, real binary content")


def test_lfs_repository_runs_the_full_seven_command_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    _write_gitattributes(workdir, declares_lfs=True)
    _write_real_file(workdir / "packages" / "Aspose.JavaAttributes" / "lib.dll")
    recorder = _RunRecorder()
    monkeypatch.setattr(example_verifier, "_run", recorder)

    prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    assert _argv_prefixes(recorder.calls) == _LFS_SEQUENCE


def test_lfs_sequence_commands_all_run_with_cwd_equal_to_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    _write_gitattributes(workdir, declares_lfs=True)
    _write_real_file(workdir / "packages" / "lib.dll")
    recorder = _RunRecorder()
    monkeypatch.setattr(example_verifier, "_run", recorder)

    prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    assert all(cwd == workdir for cwd in recorder.cwds)


def test_repository_without_gitattributes_runs_exactly_the_five_legacy_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    recorder = _RunRecorder()
    monkeypatch.setattr(example_verifier, "_run", recorder)

    prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    assert _argv_prefixes(recorder.calls) == _LEGACY_SEQUENCE
    assert all(cwd == workdir for cwd in recorder.cwds)


def test_gitattributes_without_lfs_filter_runs_exactly_the_five_legacy_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    _write_gitattributes(workdir, declares_lfs=False)
    recorder = _RunRecorder()
    monkeypatch.setattr(example_verifier, "_run", recorder)

    prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    assert _argv_prefixes(recorder.calls) == _LEGACY_SEQUENCE


def test_lfs_install_failure_raises_runtime_error_and_records_no_dotnet_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    _write_gitattributes(workdir, declares_lfs=True)
    recorder = _RunRecorder(outcomes={4: _Outcome(returncode=1, stderr="git: 'lfs' is not a git command")})
    monkeypatch.setattr(example_verifier, "_run", recorder)

    with pytest.raises(RuntimeError) as info:
        prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    assert str(info.value).startswith(LFS_UNAVAILABLE_REASON)
    assert "git: 'lfs' is not a git command" in str(info.value)
    assert "dotnet" not in _argv_prefixes(recorder.calls)
    assert not isinstance(info.value, ExampleEnvironmentError)


def test_lfs_pull_failure_raises_runtime_error_with_tail_of_output_and_no_dotnet_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    _write_gitattributes(workdir, declares_lfs=True)
    network_error_text = "Could not resolve hostname github.com; the LFS host is unreachable" * 20
    recorder = _RunRecorder(outcomes={5: _Outcome(returncode=1, stderr=network_error_text)})
    monkeypatch.setattr(example_verifier, "_run", recorder)

    with pytest.raises(RuntimeError) as info:
        prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    message = str(info.value)
    assert message.startswith(LFS_UNAVAILABLE_REASON)
    assert network_error_text[-200:] in message
    assert "dotnet" not in _argv_prefixes(recorder.calls)
    assert not isinstance(info.value, ExampleEnvironmentError)


def test_remaining_pointer_file_after_successful_pull_raises_runtime_error_naming_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    _write_gitattributes(workdir, declares_lfs=True)
    pointer_path = workdir / "packages" / "Aspose.JavaAttributes" / "lib.dll"

    def leave_pointer(cwd: Path | None) -> None:
        _write_pointer_file(pointer_path)

    recorder = _RunRecorder(outcomes={5: _Outcome(returncode=0, side_effect=leave_pointer)})
    monkeypatch.setattr(example_verifier, "_run", recorder)

    with pytest.raises(RuntimeError) as info:
        prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    message = str(info.value)
    assert message.startswith(LFS_UNAVAILABLE_REASON)
    assert "1" in message
    assert "dotnet" not in _argv_prefixes(recorder.calls)
    assert not isinstance(info.value, ExampleEnvironmentError)


def test_multiple_remaining_pointer_files_are_all_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    _write_gitattributes(workdir, declares_lfs=True)

    def leave_two_pointers(cwd: Path | None) -> None:
        _write_pointer_file(workdir / "packages" / "Aspose.JavaAttributes" / "lib.dll")
        _write_pointer_file(workdir / "packages" / "Aspose.CodePorting" / "lib.dll")

    recorder = _RunRecorder(outcomes={5: _Outcome(returncode=0, side_effect=leave_two_pointers)})
    monkeypatch.setattr(example_verifier, "_run", recorder)

    with pytest.raises(RuntimeError) as info:
        prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    assert "2" in str(info.value)


def test_real_non_pointer_files_proceed_to_dotnet_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "workdir"
    _write_gitattributes(workdir, declares_lfs=True)

    def leave_real_file(cwd: Path | None) -> None:
        _write_real_file(workdir / "packages" / "Aspose.JavaAttributes" / "lib.dll")

    recorder = _RunRecorder(outcomes={5: _Outcome(returncode=0, side_effect=leave_real_file)})
    monkeypatch.setattr(example_verifier, "_run", recorder)

    prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    assert _argv_prefixes(recorder.calls) == _LFS_SEQUENCE
    assert recorder.calls[-1][:2] == ["dotnet", "build"]


def test_constants_have_the_stated_values() -> None:
    assert LFS_UNAVAILABLE_REASON == "not verified (LFS unavailable)"
    assert _LFS_INCLUDE_PATTERN == "packages/**"


# End to end through the REAL infra/build_chunks.py dotnet dispatch entry, calling the real
# prepare_reference_library with the stubbed example_verifier._run.


def _dotnet_argv(tmp_path: Path) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Offline LFS degradation fixture",
        "--out",
        str(tmp_path / "chunks.json"),
        "--furnished-page",
        str(FURNISHED_PAGE),
        "--library-repository",
        REPOSITORY,
        "--library-commit",
        COMMIT,
        "--library-platform",
        "dotnet",
        "--library-csproj",
        CSPROJ_RELATIVE_PATH,
    ]


def _run_main(argv: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["build_chunks.py"] + argv
    try:
        build_chunks.main()
    finally:
        sys.argv = old_argv


def test_end_to_end_through_build_chunks_records_one_library_unavailable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _RunRecorder(
        outcomes={
            3: _Outcome(
                returncode=0,
                side_effect=lambda cwd: _write_gitattributes(cwd, declares_lfs=True),
            ),
            4: _Outcome(returncode=1, stderr="git: 'lfs' is not a git command"),
        }
    )
    monkeypatch.setattr(example_verifier, "_run", recorder)

    _run_main(_dotnet_argv(tmp_path))

    report = _report(tmp_path)
    assert len(report) == 1
    record = report[0]
    assert record["kind"] == "library_unavailable"
    assert record["verified"] is False
    assert record["output"].startswith(LFS_UNAVAILABLE_REASON)
    assert "dotnet" not in _argv_prefixes(recorder.calls)


def _report(tmp_path: Path) -> list[dict]:
    import json

    return json.loads((tmp_path / "chunks.verification.json").read_text(encoding="utf-8"))
