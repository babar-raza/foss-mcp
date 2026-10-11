"""TC-361 (REQ-G3-005): the opt-in consumer-project mode of the Linux C++ verifier.

cells/cpp, slides/cpp, email/cpp and jmap/cpp each keep their CMake project and target under
different names (jmap/cpp is a header-only INTERFACE target with no archive at all), so the
verifier can opt in to a tiny generated CMake project that add_subdirectory()s the library and
links a candidate executable to the pilot's target. These tests are offline: every subprocess,
every tool lookup and the git clone are replaced, so no g++, cmake, git or network is used.
pdf/cpp's own pinned tests live in test_cpp_verifier_linux.py and stay unmodified.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import ExampleEnvironmentError, verify_cpp_example_linux

REPOSITORY = "aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp"
COMMIT = "0123456789abcdef0123456789abcdef01234567"

# (pilot, source subdirectory or None for the repository root, CMake target)
PILOTS = [
    ("cells/cpp", "Aspose.Cells.Foss.Cpp", "aspose_cells_foss"),
    ("slides/cpp", None, "AsposeSlidesFoss"),
    ("email/cpp", None, "AsposeEmailFoss"),
    ("jmap/cpp", None, "aspose-jmap-foss-cpp"),
]

SLIDES_FRAGMENT = """#include <Aspose/Slides/Foss/presentation.hpp>
#include <iostream>

Presentation prs;
prs.AddSlide();
std::cout << prs.SlideCount() << "\\n";
"""


def _candidate(code: str) -> CandidateExample:
    return CandidateExample(title="example", description="", language="cpp", code=code)


def _completed(
    args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _script_tools(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> None:
    def fake_which(name: str, *args: object, **kwargs: object) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    monkeypatch.setattr(ev.shutil, "which", fake_which)


def _script_prepare(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_subdir: str | None,
    create_source: bool = True,
    configure_code: int = 0,
    build_code: int = 0,
) -> list[list[str]]:
    """Replace the clone (it creates the source directory) and every cmake call."""
    calls: list[list[str]] = []

    def fake_clone(repository: str, commit: str, workdir: Path) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        if create_source and source_subdir:
            (workdir / source_subdir).mkdir(parents=True, exist_ok=True)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if "--build" in args:
            return _completed(args, build_code, stderr="build output")
        return _completed(args, configure_code, stderr="configure output")

    monkeypatch.setattr(ev, "_clone_pinned_commit", fake_clone)
    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    _script_tools(monkeypatch, {"g++", "cmake"})
    return calls


def _prepare(
    workdir: Path,
    source_subdir: str | None,
    target: str,
    *,
    relax: bool = False,
) -> Path:
    return ev.prepare_cpp_library_linux(
        REPOSITORY, COMMIT, workdir, source_subdir=source_subdir, target=target, relax_warnings=relax
    )


# --- the generated consumer CMakeLists.txt -------------------------------------------------


def test_consumer_cmakelists_for_cells_cpp_relaxed_has_wno_error_after_add_subdirectory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "lib" / "Aspose.Cells.Foss.Cpp"
    source.mkdir(parents=True)
    consumer = tmp_path / "consumer"

    ev._write_cpp_consumer_project(consumer, source, "aspose_cells_foss", True)

    text = (consumer / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "cmake_minimum_required(VERSION 3.16)" in text
    assert "project(foss_mcp_consumer CXX)" in text
    assert "set(CMAKE_CXX_STANDARD 20)" in text
    assert "set(CMAKE_CXX_STANDARD_REQUIRED ON)" in text
    add_sub = f'add_subdirectory("{source.as_posix()}" foss_mcp_library EXCLUDE_FROM_ALL)'
    assert add_sub in text
    assert "target_compile_options(aspose_cells_foss PRIVATE -Wno-error)" in text
    assert text.index("-Wno-error") > text.index("add_subdirectory")
    assert "add_executable(foss_mcp_candidate candidate.cpp)" in text
    assert "target_link_libraries(foss_mcp_candidate PRIVATE aspose_cells_foss)" in text
    assert (consumer / "candidate.cpp").read_text(encoding="utf-8").strip() == "int main() { return 0; }"


def test_consumer_cmakelists_strict_warnings_carry_no_wno_error(tmp_path: Path) -> None:
    source = tmp_path / "lib"
    source.mkdir()

    ev._write_cpp_consumer_project(tmp_path / "consumer", source, "AsposeSlidesFoss", False)

    text = (tmp_path / "consumer" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "-Wno-error" not in text
    assert "target_link_libraries(foss_mcp_candidate PRIVATE AsposeSlidesFoss)" in text


def test_consumer_cmakelists_for_slides_cpp_names_its_target(tmp_path: Path) -> None:
    source = tmp_path / "lib"
    source.mkdir()

    ev._write_cpp_consumer_project(tmp_path / "consumer", source, "AsposeSlidesFoss", False)

    text = (tmp_path / "consumer" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert f'add_subdirectory("{source.as_posix()}" foss_mcp_library EXCLUDE_FROM_ALL)' in text
    assert "AsposeSlidesFoss" in text


def test_consumer_cmakelists_for_email_cpp_names_its_target(tmp_path: Path) -> None:
    source = tmp_path / "lib"
    source.mkdir()

    ev._write_cpp_consumer_project(tmp_path / "consumer", source, "AsposeEmailFoss", False)

    text = (tmp_path / "consumer" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "target_link_libraries(foss_mcp_candidate PRIVATE AsposeEmailFoss)" in text


def test_consumer_cmakelists_for_jmap_cpp_links_the_interface_target_and_expects_no_archive(
    tmp_path: Path,
) -> None:
    source = tmp_path / "lib"
    source.mkdir()

    ev._write_cpp_consumer_project(tmp_path / "consumer", source, "aspose-jmap-foss-cpp", False)

    text = (tmp_path / "consumer" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "target_link_libraries(foss_mcp_candidate PRIVATE aspose-jmap-foss-cpp)" in text
    assert "libaspose" not in text
    assert "link_directories" not in text
    assert "find_library" not in text


@pytest.mark.parametrize("target", ["", "a b", "x;rm", "x$(id)", "a/b", '"q"', "-flag one"])
def test_writer_rejects_an_invalid_target(tmp_path: Path, target: str) -> None:
    source = tmp_path / "lib"
    source.mkdir()

    with pytest.raises(ValueError):
        ev._write_cpp_consumer_project(tmp_path / "consumer", source, target, False)


def test_writer_rejects_a_source_directory_that_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ev._write_cpp_consumer_project(tmp_path / "consumer", tmp_path / "missing", "t", False)


# --- prepare in consumer mode --------------------------------------------------------------


@pytest.mark.parametrize(("pilot", "source_subdir", "target"), PILOTS)
def test_prepare_consumer_mode_configures_and_builds_the_consumer_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pilot: str,
    source_subdir: str | None,
    target: str,
) -> None:
    calls = _script_prepare(monkeypatch, source_subdir=source_subdir)
    workdir = tmp_path / "reference"

    build_dir = _prepare(workdir, source_subdir, target)

    consumer = workdir / "_foss_mcp_consumer"
    assert build_dir == consumer / "_build"
    configure, build = calls
    assert configure == [
        "cmake",
        "-S",
        str(consumer),
        "-B",
        str(consumer / "_build"),
        "-G",
        "Unix Makefiles",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_CXX_COMPILER=g++",
        "-DBUILD_TESTING=OFF",
    ]
    assert build == [
        "cmake",
        "--build",
        str(consumer / "_build"),
        "--target",
        "foss_mcp_candidate",
        "--parallel",
    ]
    text = (consumer / "CMakeLists.txt").read_text(encoding="utf-8")
    library_dir = workdir / source_subdir if source_subdir else workdir
    assert f'add_subdirectory("{library_dir.as_posix()}" foss_mcp_library' in text
    assert f"PRIVATE {target})" in text


def test_prepare_consumer_mode_for_the_header_only_target_demands_no_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_prepare(monkeypatch, source_subdir=None)

    build_dir = _prepare(tmp_path / "reference", None, "aspose-jmap-foss-cpp")

    assert build_dir.name == "_build"
    assert list((tmp_path / "reference").rglob("*.a")) == []


def test_prepare_consumer_mode_relaxed_writes_wno_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_prepare(monkeypatch, source_subdir="Aspose.Cells.Foss.Cpp")

    _prepare(tmp_path / "reference", "Aspose.Cells.Foss.Cpp", "aspose_cells_foss", relax=True)

    text = (tmp_path / "reference" / "_foss_mcp_consumer" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "target_compile_options(aspose_cells_foss PRIVATE -Wno-error)" in text


def test_prepare_consumer_mode_never_issues_the_pdf_cpp_library_target_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_prepare(monkeypatch, source_subdir=None)

    _prepare(tmp_path / "reference", None, "AsposeEmailFoss")

    assert all("aspose_pdf_foss" not in argv for argv in calls)


@pytest.mark.parametrize("target", ["bad target", "a;b", ""])
def test_prepare_rejects_an_invalid_target_with_value_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str
) -> None:
    _script_prepare(monkeypatch, source_subdir=None)

    with pytest.raises(ValueError):
        _prepare(tmp_path / "reference", None, target)


@pytest.mark.parametrize("subdir", ["/etc", "C:/x", "\\abs", "../outside", "a/../../b", "a\\..\\b"])
def test_prepare_rejects_an_absolute_or_dotdot_source_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subdir: str
) -> None:
    calls = _script_prepare(monkeypatch, source_subdir=None)

    with pytest.raises(ValueError):
        _prepare(tmp_path / "reference", subdir, "t")

    assert calls == []


def test_prepare_rejects_a_source_dir_missing_after_the_clone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_prepare(monkeypatch, source_subdir="Aspose.Cells.Foss.Cpp", create_source=False)

    with pytest.raises(ValueError):
        _prepare(tmp_path / "reference", "Aspose.Cells.Foss.Cpp", "aspose_cells_foss")

    assert calls == []


def test_prepare_consumer_configure_failure_is_a_runtime_error_not_an_environment_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_prepare(monkeypatch, source_subdir=None, configure_code=1)

    with pytest.raises(RuntimeError) as info:
        _prepare(tmp_path / "reference", None, "AsposeSlidesFoss")

    assert not isinstance(info.value, ExampleEnvironmentError)
    assert "cmake configure" in str(info.value)


def test_prepare_consumer_build_failure_is_a_runtime_error_not_an_environment_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_prepare(monkeypatch, source_subdir=None, build_code=2)

    with pytest.raises(RuntimeError) as info:
        _prepare(tmp_path / "reference", None, "AsposeSlidesFoss")

    assert not isinstance(info.value, ExampleEnvironmentError)
    assert "AsposeSlidesFoss" in str(info.value)


def test_prepare_consumer_mode_keeps_the_environment_error_split(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_tools(monkeypatch, {"cmake"})
    with pytest.raises(ExampleEnvironmentError) as missing:
        _prepare(tmp_path / "reference", None, "AsposeSlidesFoss")
    assert missing.value.tool == "g++"

    _script_tools(monkeypatch, {"g++", "cmake"})

    def failing_clone(repository: str, commit: str, workdir: Path) -> None:
        raise RuntimeError("git fetch of commit failed")

    monkeypatch.setattr(ev, "_clone_pinned_commit", failing_clone)
    with pytest.raises(ExampleEnvironmentError) as fetch:
        _prepare(tmp_path / "reference", None, "AsposeSlidesFoss")
    assert fetch.value.tool == "git"


def test_prepare_without_a_target_keeps_the_legacy_command_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if "--build" in args:
            build_dir = Path(args[args.index("--build") + 1])
            build_dir.mkdir(parents=True, exist_ok=True)
            (build_dir / "libaspose_pdf_foss.a").write_bytes(b"!<arch>\n")
        return _completed(args)

    monkeypatch.setattr(ev, "_clone_pinned_commit", lambda *a: None)
    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    _script_tools(monkeypatch, {"g++", "cmake"})

    build_dir = ev.prepare_cpp_library_linux("o/r", COMMIT, tmp_path / "reference")

    assert build_dir == tmp_path / "reference" / "_build"
    assert calls[1] == ["cmake", "--build", str(build_dir), "--target", "aspose_pdf_foss", "--parallel"]
    assert not (tmp_path / "reference" / "_foss_mcp_consumer").exists()


# --- verify in consumer mode ---------------------------------------------------------------


def _script_verify(
    monkeypatch: pytest.MonkeyPatch, *, compile_code: int = 0, run_code: int = 0
) -> list[tuple[str, list[str], object]]:
    """Script the build (through ev._run) and the run (through subprocess.run)."""
    calls: list[tuple[str, list[str], object]] = []

    def fake_compile(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(("compile", list(args), cwd))
        return _completed(args, compile_code, stderr="cmake diagnostic" if compile_code else "")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(("run", list(args), kwargs.get("cwd")))
        return _completed(args, run_code, stdout="3\n")

    monkeypatch.setattr(ev, "_run", fake_compile)
    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    _script_tools(monkeypatch, {"g++", "cmake"})
    return calls


def test_verify_consumer_writes_the_wrapped_candidate_builds_the_target_and_runs_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_verify(monkeypatch)
    consumer = tmp_path / "reference" / "_foss_mcp_consumer"
    build_dir = consumer / "_build"
    build_dir.mkdir(parents=True)
    workdir = tmp_path / "cand"

    result = verify_cpp_example_linux(
        _candidate(SLIDES_FRAGMENT), workdir, build_dir, consumer.parent / "include", consumer=True
    )

    assert result.verified is True
    written = (consumer / "candidate.cpp").read_text(encoding="utf-8")
    assert written == ev._wrap_cpp_fragment_if_needed(SLIDES_FRAGMENT)
    assert "int main()" in written
    (kind_build, build_argv, build_cwd), (kind_run, run_argv, run_cwd) = calls
    assert kind_build == "compile"
    assert build_argv == ["cmake", "--build", str(build_dir), "--target", "foss_mcp_candidate"]
    assert build_cwd == workdir / "candidate_project"
    assert kind_run == "run"
    assert run_argv == [str(build_dir / "foss_mcp_candidate")]
    assert run_cwd == workdir / "candidate_project"
    assert (workdir / "candidate_project").is_dir()
    assert not (workdir / "candidate_project" / "candidate.cpp").exists()


def test_verify_consumer_compile_failure_is_reported_without_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_verify(monkeypatch, compile_code=2)
    consumer = tmp_path / "consumer"
    (consumer / "_build").mkdir(parents=True)

    result = verify_cpp_example_linux(
        _candidate("int main() { broken }"), tmp_path / "cand", consumer / "_build", tmp_path, consumer=True
    )

    assert result.verified is False
    assert "cmake diagnostic" in result.output
    assert [kind for kind, _, _ in calls] == ["compile"]


def test_verify_consumer_non_zero_exit_is_not_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch, run_code=1)
    consumer = tmp_path / "consumer"
    (consumer / "_build").mkdir(parents=True)

    result = verify_cpp_example_linux(
        _candidate(SLIDES_FRAGMENT), tmp_path / "cand", consumer / "_build", tmp_path, consumer=True
    )

    assert result.verified is False


@pytest.mark.parametrize("consumer", [True, False])
def test_verify_run_false_makes_compile_and_link_the_verdict_and_never_executes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, consumer: bool
) -> None:
    calls = _script_verify(monkeypatch, run_code=9)
    build_dir = tmp_path / "consumer" / "_build"
    build_dir.mkdir(parents=True)

    result = verify_cpp_example_linux(
        _candidate("int main() { return 1; }"),
        tmp_path / "cand",
        build_dir,
        tmp_path,
        consumer=consumer,
        run=False,
    )

    assert result.verified is True
    assert [kind for kind, _, _ in calls] == ["compile"]


def test_verify_run_false_still_reports_a_compile_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _script_verify(monkeypatch, compile_code=1)

    result = verify_cpp_example_linux(
        _candidate("int main() { broken }"), tmp_path / "cand", tmp_path, tmp_path, run=False
    )

    assert result.verified is False
    assert [kind for kind, _, _ in calls] == ["compile"]


def test_verify_consumer_mode_needs_cmake_as_an_environment_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_tools(monkeypatch, {"g++"})

    with pytest.raises(ExampleEnvironmentError) as info:
        verify_cpp_example_linux(
            _candidate("int main() {}"), tmp_path / "cand", tmp_path, tmp_path, consumer=True
        )

    assert info.value.tool == "cmake"


def test_verify_legacy_mode_wraps_a_fragment_but_passes_a_full_program_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch)
    _script_tools(monkeypatch, {"g++"})

    verify_cpp_example_linux(_candidate(SLIDES_FRAGMENT), tmp_path / "a", tmp_path, tmp_path)
    full = "#include <iostream>\nint main() { return 0; }\n"
    verify_cpp_example_linux(_candidate(full), tmp_path / "b", tmp_path, tmp_path)

    assert "int main() {" in (tmp_path / "a" / "candidate_project" / "candidate.cpp").read_text(
        encoding="utf-8"
    )
    assert (tmp_path / "b" / "candidate_project" / "candidate.cpp").read_text(encoding="utf-8") == full


# --- the fragment wrapper ------------------------------------------------------------------


def test_wrapper_keeps_includes_on_top_and_wraps_the_statements() -> None:
    wrapped = ev._wrap_cpp_fragment_if_needed(SLIDES_FRAGMENT)

    lines = wrapped.splitlines()
    assert lines[0] == "#include <Aspose/Slides/Foss/presentation.hpp>"
    assert lines[1] == "#include <iostream>"
    assert wrapped.index("int main() {") > wrapped.index("#include <iostream>")
    assert wrapped.index("Presentation prs;") > wrapped.index("int main() {")
    assert wrapped.rstrip().endswith("return 0;\n}")
    assert wrapped.count("#include") == 2


def test_wrapper_keeps_using_namespace_lines_outside_main() -> None:
    code = "#include <vector>\nusing namespace std;\nusing Aspose::Pdf::Document;\nvector<int> v;\nv.push_back(1);\n"

    wrapped = ev._wrap_cpp_fragment_if_needed(code)

    head, _, body = wrapped.partition("int main() {")
    assert "using namespace std;" in head
    assert "using Aspose::Pdf::Document;" in head
    assert "vector<int> v;" in body
    assert "using namespace" not in body


def test_wrapper_returns_a_full_program_byte_identical() -> None:
    code = "#include <iostream>\r\n\r\nint  main ( int argc, char** argv ) {\r\n  return 0;\r\n}\r\n"

    assert ev._wrap_cpp_fragment_if_needed(code) == code


def test_wrapper_wraps_a_fragment_that_only_mentions_main_without_calling_syntax() -> None:
    wrapped = ev._wrap_cpp_fragment_if_needed("int domain = 1;\nint remaining = domain;\n")

    assert "int main() {" in wrapped


def test_wrapper_does_not_move_includes_that_come_after_a_statement() -> None:
    wrapped = ev._wrap_cpp_fragment_if_needed("int a = 1;\n#include <iostream>\n")

    assert wrapped.index("int main() {") < wrapped.index("int a = 1;")


# --- the build_chunks dispatch -------------------------------------------------------------


def _cpp_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "library_repository": REPOSITORY,
        "library_commit": COMMIT,
        "library_cpp_source_dir": None,
        "library_cpp_target": None,
        "library_cpp_warnings": None,
        "library_cpp_verify_mode": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _record_prepare(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple, dict]]:
    calls: list[tuple[tuple, dict]] = []

    def recorder(*args: object, **kwargs: object) -> str:
        calls.append((args, kwargs))
        return "prepared"

    monkeypatch.setattr(build_chunks, "prepare_cpp_library_linux", recorder)
    return calls


def test_dispatch_prepare_with_no_cpp_flag_passes_exactly_three_positional_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _record_prepare(monkeypatch)

    build_chunks._PLATFORM_DISPATCH["cpp"]["prepare"](_cpp_args(), tmp_path)

    assert calls == [((REPOSITORY, COMMIT, tmp_path), {})]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"library_cpp_target": "aspose_cells_foss", "library_cpp_source_dir": "Aspose.Cells.Foss.Cpp"},
            {
                "source_subdir": "Aspose.Cells.Foss.Cpp",
                "target": "aspose_cells_foss",
                "relax_warnings": False,
            },
        ),
        (
            {"library_cpp_target": "aspose_cells_foss", "library_cpp_warnings": "relaxed"},
            {"source_subdir": None, "target": "aspose_cells_foss", "relax_warnings": True},
        ),
        (
            {"library_cpp_target": "AsposeEmailFoss", "library_cpp_warnings": "strict"},
            {"source_subdir": None, "target": "AsposeEmailFoss", "relax_warnings": False},
        ),
    ],
)
def test_dispatch_prepare_adds_the_keywords_when_a_cpp_flag_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, overrides: dict, expected: dict
) -> None:
    calls = _record_prepare(monkeypatch)

    build_chunks._PLATFORM_DISPATCH["cpp"]["prepare"](_cpp_args(**overrides), tmp_path)

    assert calls == [((REPOSITORY, COMMIT, tmp_path), expected)]


def test_dispatch_verify_kwargs_for_an_empty_namespace_is_unchanged() -> None:
    build_dir = Path("workdir") / "_build"

    kwargs = build_chunks._PLATFORM_DISPATCH["cpp"]["verify_kwargs"](build_dir, argparse.Namespace())

    assert kwargs == {"library_dir": build_dir, "include_dir": Path("workdir") / "include"}


@pytest.mark.parametrize(
    ("overrides", "extra"),
    [
        ({}, {}),
        ({"library_cpp_verify_mode": "run"}, {}),
        ({"library_cpp_target": "t"}, {"consumer": True}),
        ({"library_cpp_verify_mode": "compile"}, {"run": False}),
        ({"library_cpp_target": "t", "library_cpp_verify_mode": "compile"}, {"consumer": True, "run": False}),
        ({"library_cpp_target": "t", "library_cpp_warnings": "relaxed"}, {"consumer": True}),
    ],
)
def test_dispatch_verify_kwargs_per_flag_combination(overrides: dict, extra: dict) -> None:
    build_dir = Path("workdir") / "_build"

    kwargs = build_chunks._PLATFORM_DISPATCH["cpp"]["verify_kwargs"](build_dir, _cpp_args(**overrides))

    assert kwargs == {"library_dir": build_dir, "include_dir": Path("workdir") / "include", **extra}


def _run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str) -> None:
    argv = [
        "build_chunks.py",
        "--api-surface",
        str(tmp_path / "surface.json"),
        "--title",
        "T",
        "--out",
        str(tmp_path / "out.json"),
        *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    build_chunks.main()


@pytest.mark.parametrize(
    "flags",
    [
        ("--library-cpp-source-dir", "Aspose.Cells.Foss.Cpp"),
        ("--library-cpp-warnings", "relaxed"),
        ("--library-cpp-warnings", "strict"),
        ("--library-cpp-source-dir", "sub", "--library-cpp-warnings", "relaxed"),
    ],
)
def test_main_rejects_source_dir_or_warnings_without_a_target_with_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flags: tuple[str, ...]
) -> None:
    with pytest.raises(SystemExit) as info:
        _run_main(monkeypatch, tmp_path, *flags)

    assert info.value.code == 2


def test_main_rejects_an_unknown_warnings_value_with_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as info:
        _run_main(monkeypatch, tmp_path, "--library-cpp-target", "t", "--library-cpp-warnings", "loud")

    assert info.value.code == 2


def test_main_rejects_an_unknown_verify_mode_with_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as info:
        _run_main(monkeypatch, tmp_path, "--library-cpp-verify-mode", "skip")

    assert info.value.code == 2


def test_main_accepts_the_cpp_flags_with_a_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "surface.json").write_text('{"types": []}', encoding="utf-8")

    def stop(fixture: object, **kwargs: object) -> list:
        raise RuntimeError("past-argument-parsing")

    monkeypatch.setattr(build_chunks, "build_chunks_from_api_surface", stop)

    with pytest.raises(RuntimeError, match="past-argument-parsing"):
        _run_main(
            monkeypatch,
            tmp_path,
            "--library-cpp-target",
            "t",
            "--library-cpp-source-dir",
            "sub",
            "--library-cpp-warnings",
            "relaxed",
            "--library-cpp-verify-mode",
            "compile",
        )
