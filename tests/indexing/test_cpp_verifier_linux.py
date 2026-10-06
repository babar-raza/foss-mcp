"""TC-222 (REQ-G2-043): the Linux C++ verifier for pdf/cpp, its dispatch entry and the ingestion image.

The offline tests replace every subprocess and every git call, so they need no network, no g++,
no cmake and no Docker. They prove the command lines, the result shape, the environment-error
paths, the dispatch entry, and that Dockerfile.ingestion installs g++ and cmake.

``test_real_pinned_pdf_cpp_library_builds_and_verifies_a_candidate`` is marked ``live``. It clones
the real pinned pdf/cpp commit, builds it with g++ and cmake, and verifies one real candidate. It is
deselected unless FOSS_MCP_NETWORK_TESTS=1 (see tests/conftest.py).
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import ExampleEnvironmentError, verify_cpp_example_linux

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile.ingestion"

CPP_REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-Cpp"
CPP_COMMIT = "4b83c9fec1e37fd205156770161f6843bac00ceb"
ARTIFACT = "libaspose_pdf_foss.a"

GOOD_CODE = """#include <aspose/pdf/document.hpp>
#include <aspose/pdf/page_collection.hpp>
#include <iostream>

int main() {
    Aspose::Pdf::Document doc;
    doc.Pages().Add();
    std::cout << static_cast<int>(doc.Pages().Count()) << "\\n";
    return 0;
}
"""


def _candidate(code: str = GOOD_CODE) -> CandidateExample:
    return CandidateExample(title="Create a document", description="", language="cpp", code=code)


def _completed(
    args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _script_tools(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> None:
    """Make shutil.which report only the named tools as installed."""

    def fake_which(name: str, *args: object, **kwargs: object) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    monkeypatch.setattr(ev.shutil, "which", fake_which)


def _script_prepare(
    monkeypatch: pytest.MonkeyPatch,
    *,
    configure_code: int = 0,
    build_code: int = 0,
    produce_artifact: bool = True,
) -> list[list[str]]:
    """Replace the clone with a no-op and every cmake call with a scripted result.

    Returns the list of argv lists that subprocess.run was called with, in order.
    """
    calls: list[list[str]] = []

    def fake_clone(repository: str, commit: str, workdir: Path) -> None:
        return None

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if "--build" in args:
            if build_code == 0 and produce_artifact:
                build_dir = Path(args[args.index("--build") + 1])
                build_dir.mkdir(parents=True, exist_ok=True)
                (build_dir / ARTIFACT).write_bytes(b"!<arch>\n")
            return _completed(args, build_code, stderr="build output")
        return _completed(args, configure_code, stderr="configure output")

    monkeypatch.setattr(ev, "_clone_pinned_commit", fake_clone)
    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    _script_tools(monkeypatch, {"g++", "cmake"})
    return calls


def test_dispatch_cpp_entry_names_the_linux_pair_and_its_verifier_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cpp entry of _PLATFORM_DISPATCH names verify_cpp_example_linux and the label cpp_verifier_linux."""
    entry = build_chunks._PLATFORM_DISPATCH["cpp"]

    assert set(entry) == {"prepare", "verify", "verify_kwargs", "shared_page_workdir", "verifier_name"}
    assert entry["verify"] is verify_cpp_example_linux
    assert entry["shared_page_workdir"] is False
    assert entry["verifier_name"] == "cpp_verifier_linux"


def test_dispatch_cpp_prepare_calls_prepare_cpp_library_linux_with_repository_commit_and_workdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple] = []
    sentinel = object()

    def recorder(repository: str, commit: str, workdir: Path) -> object:
        calls.append((repository, commit, workdir))
        return sentinel

    monkeypatch.setattr(build_chunks, "prepare_cpp_library_linux", recorder)
    args = argparse.Namespace(library_repository=CPP_REPOSITORY, library_commit=CPP_COMMIT)

    assert build_chunks._PLATFORM_DISPATCH["cpp"]["prepare"](args, tmp_path) is sentinel
    assert calls == [(CPP_REPOSITORY, CPP_COMMIT, tmp_path)]


def test_dispatch_cpp_verify_kwargs_takes_the_build_dir_and_its_sibling_include_dir() -> None:
    build_dir = Path("workdir") / "_build"

    kwargs = build_chunks._PLATFORM_DISPATCH["cpp"]["verify_kwargs"](build_dir, argparse.Namespace())

    assert kwargs == {"library_dir": build_dir, "include_dir": Path("workdir") / "include"}


def test_prepare_configures_with_cmake_release_and_gpp_then_builds_only_the_library_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_prepare(monkeypatch)

    build_dir = ev.prepare_cpp_library_linux(CPP_REPOSITORY, CPP_COMMIT, tmp_path / "reference")

    assert build_dir == tmp_path / "reference" / "_build"
    assert (build_dir / ARTIFACT).is_file()
    configure, build = calls
    assert configure[0] == "cmake"
    assert "-DCMAKE_BUILD_TYPE=Release" in configure
    assert "-DCMAKE_CXX_COMPILER=g++" in configure
    assert configure[configure.index("-S") + 1] == str(tmp_path / "reference")
    assert configure[configure.index("-B") + 1] == str(build_dir)
    assert build == ["cmake", "--build", str(build_dir), "--target", "aspose_pdf_foss", "--parallel"]


def test_prepare_raises_environment_error_when_g_plus_plus_is_missing_and_clones_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cloned: list[str] = []
    monkeypatch.setattr(ev, "_clone_pinned_commit", lambda *a: cloned.append("clone"))
    _script_tools(monkeypatch, {"cmake"})

    with pytest.raises(ExampleEnvironmentError) as info:
        ev.prepare_cpp_library_linux(CPP_REPOSITORY, CPP_COMMIT, tmp_path / "reference")

    assert info.value.tool == "g++"
    assert cloned == []


def test_prepare_raises_environment_error_when_cmake_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_tools(monkeypatch, {"g++"})

    with pytest.raises(ExampleEnvironmentError) as info:
        ev.prepare_cpp_library_linux(CPP_REPOSITORY, CPP_COMMIT, tmp_path / "reference")

    assert info.value.tool == "cmake"


def test_prepare_raises_environment_error_when_the_pinned_commit_cannot_be_fetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failing_clone(repository: str, commit: str, workdir: Path) -> None:
        raise RuntimeError("git fetch of commit failed")

    monkeypatch.setattr(ev, "_clone_pinned_commit", failing_clone)
    _script_tools(monkeypatch, {"g++", "cmake"})

    with pytest.raises(ExampleEnvironmentError) as info:
        ev.prepare_cpp_library_linux(CPP_REPOSITORY, CPP_COMMIT, tmp_path / "reference")

    assert info.value.tool == "git"


def test_prepare_configure_failure_is_a_runtime_error_not_an_environment_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_prepare(monkeypatch, configure_code=1)

    with pytest.raises(RuntimeError) as info:
        ev.prepare_cpp_library_linux(CPP_REPOSITORY, CPP_COMMIT, tmp_path / "reference")

    assert not isinstance(info.value, ExampleEnvironmentError)
    assert "cmake configure" in str(info.value)


def test_prepare_build_failure_is_a_runtime_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _script_prepare(monkeypatch, build_code=2)

    with pytest.raises(RuntimeError, match="build of aspose_pdf_foss"):
        ev.prepare_cpp_library_linux(CPP_REPOSITORY, CPP_COMMIT, tmp_path / "reference")


def test_prepare_reports_a_successful_build_that_produced_no_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_prepare(monkeypatch, produce_artifact=False)

    with pytest.raises(RuntimeError, match="was not produced"):
        ev.prepare_cpp_library_linux(CPP_REPOSITORY, CPP_COMMIT, tmp_path / "reference")


def _script_verify(
    monkeypatch: pytest.MonkeyPatch,
    *,
    compile_code: int = 0,
    run_code: int = 0,
    run_timeout: bool = False,
) -> list[list[str]]:
    """Script the compile (through ev._run) and the run (through subprocess.run) of one candidate."""
    calls: list[list[str]] = []

    def fake_compile(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return _completed(args, compile_code, stdout="", stderr="g++ diagnostic" if compile_code else "")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if run_timeout:
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 0), output="partial", stderr="")
        return _completed(args, run_code, stdout="2\n", stderr="")

    monkeypatch.setattr(ev, "_run", fake_compile)
    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    _script_tools(monkeypatch, {"g++"})
    return calls


def test_verify_compiles_links_and_runs_the_candidate_against_the_library_and_include_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_verify(monkeypatch)
    library_dir = tmp_path / "reference" / "_build"
    include_dir = tmp_path / "reference" / "include"

    result = verify_cpp_example_linux(_candidate(), tmp_path / "cand", library_dir, include_dir)

    assert result.verified is True
    compile_argv, run_argv = calls
    assert compile_argv[0] == "g++"
    assert "-std=c++20" in compile_argv
    assert f"-I{include_dir}" in compile_argv
    assert str(library_dir / ARTIFACT) in compile_argv
    assert compile_argv[compile_argv.index("-o") + 1] == str(
        tmp_path / "cand" / "candidate_project" / "candidate"
    )
    assert run_argv == [str(tmp_path / "cand" / "candidate_project" / "candidate")]
    assert (tmp_path / "cand" / "candidate_project" / "candidate.cpp").read_text(
        encoding="utf-8"
    ) == GOOD_CODE


def test_verify_compile_error_is_a_candidate_failure_and_the_program_is_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_verify(monkeypatch, compile_code=1)

    result = verify_cpp_example_linux(
        _candidate("int main() { broken }"), tmp_path / "cand", tmp_path, tmp_path
    )

    assert result.verified is False
    assert "g++ diagnostic" in result.output
    assert len(calls) == 1


def test_verify_non_zero_exit_of_the_candidate_is_not_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch, run_code=3)

    result = verify_cpp_example_linux(_candidate(), tmp_path / "cand", tmp_path, tmp_path)

    assert result.verified is False
    assert "2" in result.output


def test_verify_a_run_that_exceeds_the_timeout_is_not_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch, run_timeout=True)

    result = verify_cpp_example_linux(_candidate(), tmp_path / "cand", tmp_path, tmp_path)

    assert result.verified is False
    assert "stopped" in result.output


def test_verify_raises_environment_error_when_g_plus_plus_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_tools(monkeypatch, set())

    with pytest.raises(ExampleEnvironmentError) as info:
        verify_cpp_example_linux(_candidate(), tmp_path / "cand", tmp_path, tmp_path)

    assert info.value.tool == "g++"


def test_dockerfile_ingestion_installs_g_plus_plus_and_cmake_through_apt() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    install_lines = [line for line in re.sub(r"\\\n", " ", text).splitlines() if "apt-get install" in line]

    assert any(
        re.search(r"(?:^|\s)g\+\+(?=\s|$)", line) and re.search(r"\bcmake\b", line) for line in install_lines
    ), install_lines
    assert "--no-install-recommends" in next(
        line for line in install_lines if re.search(r"(?:^|\s)g\+\+(?=\s|$)", line)
    )


@pytest.mark.live
def test_real_pinned_pdf_cpp_library_builds_and_verifies_a_candidate(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Builds the real pinned pdf/cpp library with g++ and cmake and verifies one real candidate."""
    workdir = tmp_path_factory.mktemp("pdf_cpp_reference")
    build_dir = ev.prepare_cpp_library_linux(CPP_REPOSITORY, CPP_COMMIT, workdir)

    assert (build_dir / ARTIFACT).is_file()

    result = verify_cpp_example_linux(
        _candidate(),
        tmp_path_factory.mktemp("pdf_cpp_candidate"),
        build_dir,
        build_dir.parent / "include",
    )

    assert result.verified is True, result.output
