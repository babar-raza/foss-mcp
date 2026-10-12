"""TC-455 (REQ-G3-007): per-pilot C++ compatibility switches and the page's own includes.

email/cpp, jmap/cpp and cells/cpp fail verification for verifier reasons (a GCC 14 warning, a
missing ``<limits>``, an include directory that is one level too deep, an ``#include`` that sits in
an earlier block of the page). ``--library-cpp-compat`` opts a pilot in to four named switches.
These tests are offline: every subprocess, tool lookup and clone is replaced, so no g++, cmake, git
or network is used, and nothing depends on wall-clock time.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import (
    CPP_COMPAT_NAMES,
    cpp_compat_compile_options,
    verify_cpp_example_linux,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
TEMPLATE = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "templates" / "ingestion-job.yaml"
BUILD_CHUNKS = REPO_ROOT / "infra" / "build_chunks.py"
JMAP_PAGE = REPO_ROOT / "tests" / "fixtures" / "furnished" / "jmap_cpp" / "pages" / "_index.md"
EMAIL_PAGE = REPO_ROOT / "tests" / "fixtures" / "furnished" / "email_cpp" / "pages" / "_index.md"

REPOSITORY = "aspose-email-foss/Aspose.JMAP-FOSS-for-Cpp"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
COMPAT_VALUE = "no-changes-meaning,include-limits,include-subdirs,page-includes"
COMPAT_PILOTS = [("cells", "cpp"), ("email", "cpp"), ("jmap", "cpp")]


def _candidate(code: str) -> CandidateExample:
    return CandidateExample(title="example", description="", language="cpp", code=code)


def _completed(
    args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _lines(consumer: Path) -> list[str]:
    return (consumer / "CMakeLists.txt").read_text(encoding="utf-8").splitlines()


def _write(tmp_path: Path, library: Path, **kwargs: object) -> list[str]:
    consumer = tmp_path / "consumer"
    ev._write_cpp_consumer_project(consumer, library, "tgt", False, **kwargs)  # type: ignore[arg-type]
    return _lines(consumer)


# --- the names and the compile options -----------------------------------------------------


def test_compat_names_are_exactly_the_four_switches() -> None:
    assert CPP_COMPAT_NAMES == ("no-changes-meaning", "include-limits", "include-subdirs", "page-includes")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("no-changes-meaning", ("-Wno-changes-meaning",)),
        ("include-limits", ("SHELL:-include limits",)),
        ("include-subdirs", ()),
        ("page-includes", ()),
    ],
)
def test_compile_options_per_name(name: str, expected: tuple[str, ...]) -> None:
    assert cpp_compat_compile_options([name]) == expected


def test_compile_options_follow_the_order_given() -> None:
    assert cpp_compat_compile_options(["include-limits", "page-includes", "no-changes-meaning"]) == (
        "SHELL:-include limits",
        "-Wno-changes-meaning",
    )
    assert cpp_compat_compile_options(CPP_COMPAT_NAMES) == ("-Wno-changes-meaning", "SHELL:-include limits")


def test_compile_options_reject_an_unknown_name_and_name_it() -> None:
    with pytest.raises(ValueError, match="bogus-switch"):
        cpp_compat_compile_options(["no-changes-meaning", "bogus-switch"])


# --- the generated consumer CMakeLists.txt -------------------------------------------------


def test_compile_options_line_sits_directly_before_add_subdirectory(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    library.mkdir()

    lines = _write(tmp_path, library, compile_options=("-Wno-changes-meaning", "SHELL:-include limits"))

    index = next(i for i, line in enumerate(lines) if line.startswith("add_subdirectory("))
    assert lines[index - 1] == 'add_compile_options("-Wno-changes-meaning" "SHELL:-include limits")'
    assert sum(line.startswith("add_compile_options(") for line in lines) == 1


def test_relaxed_warnings_with_compile_options_keep_wno_error_after_add_subdirectory(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    library.mkdir()
    consumer = tmp_path / "consumer"

    ev._write_cpp_consumer_project(consumer, library, "tgt", True, compile_options=("-Wno-changes-meaning",))

    lines = _lines(consumer)
    sub = next(i for i, line in enumerate(lines) if line.startswith("add_subdirectory("))
    opts = next(i for i, line in enumerate(lines) if line.startswith("add_compile_options("))
    relax = lines.index("target_compile_options(tgt PRIVATE -Wno-error)")
    assert opts == sub - 1
    assert relax == sub + 1


def test_default_consumer_project_equals_today_exactly(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    (library / "include" / "pkg").mkdir(parents=True)

    plain = _write(tmp_path, library)

    assert plain == [
        "cmake_minimum_required(VERSION 3.16)",
        "project(foss_mcp_consumer CXX)",
        "set(CMAKE_CXX_STANDARD 20)",
        "set(CMAKE_CXX_STANDARD_REQUIRED ON)",
        f'add_subdirectory("{library.as_posix()}" foss_mcp_library EXCLUDE_FROM_ALL)',
        "add_executable(foss_mcp_candidate candidate.cpp)",
        "target_link_libraries(foss_mcp_candidate PRIVATE tgt)",
    ]


def test_include_subdirs_writes_one_sorted_private_line_after_add_executable(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    (library / "include" / "other").mkdir(parents=True)
    (library / "include" / "aspose_jmap").mkdir(parents=True)
    (library / "include" / "loose.hpp").write_text("", encoding="utf-8")

    lines = _write(tmp_path, library, include_subdirs=True)

    exe = lines.index("add_executable(foss_mcp_candidate candidate.cpp)")
    expected = (
        "target_include_directories(foss_mcp_candidate PRIVATE "
        f'"{(library / "include" / "aspose_jmap").as_posix()}" "{(library / "include" / "other").as_posix()}")'
    )
    assert lines[exe + 1] == expected
    assert sum(line.startswith("target_include_directories(") for line in lines) == 1


def test_include_subdirs_writes_nothing_without_an_include_directory(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    library.mkdir()

    lines = _write(tmp_path, library, include_subdirs=True)

    assert not any(line.startswith("target_include_directories(") for line in lines)


def test_include_subdirs_false_writes_nothing_even_with_an_include_directory(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    (library / "include" / "aspose_jmap").mkdir(parents=True)

    lines = _write(tmp_path, library, include_subdirs=False)

    assert not any(line.startswith("target_include_directories(") for line in lines)


# --- verify with the page's includes -------------------------------------------------------


def _script_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_compile(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return _completed(args, 0)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, 0, stdout="ran\n")

    monkeypatch.setattr(ev, "_run", fake_compile)
    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    monkeypatch.setattr(ev.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")


def _verify(
    tmp_path: Path, candidate: CandidateExample, **kwargs: object
) -> tuple[ev.VerificationResult, str]:
    consumer = tmp_path / "reference" / "_foss_mcp_consumer"
    build_dir = consumer / "_build"
    build_dir.mkdir(parents=True)
    result = verify_cpp_example_linux(
        candidate,
        tmp_path / "cand",
        build_dir,
        consumer.parent / "include",
        consumer=True,
        **kwargs,  # type: ignore[arg-type]
    )
    return result, (consumer / "candidate.cpp").read_text(encoding="utf-8")


def test_a_missing_page_include_is_prefixed_to_the_written_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch)
    candidate = _candidate("#include <iostream>\nint main() { return 0; }\n")

    result, written = _verify(tmp_path, candidate, page_includes=('#include "JmapClient.hpp"',))

    assert written.splitlines()[0] == '#include "JmapClient.hpp"'
    assert "#include <iostream>" in written
    assert result.verified is True


def test_an_include_already_in_the_candidate_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch)
    code = '#include "JmapClient.hpp"\nint main() { return 0; }\n'

    result, written = _verify(tmp_path, _candidate(code), page_includes=('#include "JmapClient.hpp"',))

    assert written == code
    assert written.count('#include "JmapClient.hpp"') == 1
    assert not result.output.startswith("page includes prefixed:")


def test_prefixed_includes_sit_above_main_for_a_fragment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch)
    fragment = "auto client = aspose_jmap::JmapClient();\n"

    _, written = _verify(
        tmp_path, _candidate(fragment), page_includes=('#include "JmapClient.hpp"', "#include <string>")
    )

    assert written.index('#include "JmapClient.hpp"') < written.index("#include <string>")
    assert written.index("#include <string>") < written.index("int main()")
    assert written.index("int main()") < written.index("aspose_jmap::JmapClient")


def test_the_result_carries_the_original_candidate_and_names_the_prefixed_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch)
    candidate = _candidate("int main() { return 0; }\n")
    original_code = candidate.code

    result, _ = _verify(tmp_path, candidate, page_includes=('#include "JmapClient.hpp"', "#include <string>"))

    assert result.candidate is candidate
    assert result.candidate.code == original_code
    assert (
        result.output.splitlines()[0]
        == 'page includes prefixed: #include "JmapClient.hpp" | #include <string>'
    )


def test_verify_without_page_includes_writes_exactly_what_it_writes_today(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_verify(monkeypatch)
    fragment = "#include <iostream>\nstd::cout << 1;\n"

    result, written = _verify(tmp_path, _candidate(fragment))

    assert written == ev._wrap_cpp_fragment_if_needed(fragment)
    assert "page includes prefixed" not in result.output


# --- the page's includes -------------------------------------------------------------------


def test_page_cpp_includes_for_the_jmap_page() -> None:
    assert build_chunks._page_cpp_includes(JMAP_PAGE) == ('#include "JmapClient.hpp"',)


def test_page_cpp_includes_for_the_email_page_are_ordered_and_unique() -> None:
    includes = build_chunks._page_cpp_includes(EMAIL_PAGE)

    wanted = [
        "#include <fstream>",
        "#include <iostream>",
        '#include "aspose/email/foss/msg/mapi_message.hpp"',
    ]
    assert [line for line in includes if line in wanted] == wanted
    assert len(includes) == len(set(includes))


def test_page_cpp_includes_for_no_page_is_empty() -> None:
    assert build_chunks._page_cpp_includes(None) == ()


# --- build_chunks arguments and dispatch ---------------------------------------------------


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


def test_main_rejects_compat_without_a_target_with_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as info:
        _run_main(monkeypatch, tmp_path, "--library-cpp-compat", "include-limits")

    assert info.value.code == 2


@pytest.mark.parametrize("value", ["bogus", "include-limits,bogus", ""])
def test_main_rejects_an_unknown_or_empty_compat_list_with_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    with pytest.raises(SystemExit) as info:
        _run_main(monkeypatch, tmp_path, "--library-cpp-target", "t", "--library-cpp-compat", value)

    assert info.value.code == 2


def test_compat_argument_parses_a_comma_separated_list() -> None:
    assert build_chunks._cpp_compat_argument(COMPAT_VALUE) == CPP_COMPAT_NAMES


def _cpp_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "library_repository": REPOSITORY,
        "library_commit": COMMIT,
        "library_cpp_source_dir": None,
        "library_cpp_target": "tgt",
        "library_cpp_warnings": None,
        "library_cpp_verify_mode": None,
        "library_cpp_compat": None,
        "furnished_page": None,
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


def test_prepare_cpp_with_compat_passes_compile_options_and_include_subdirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _record_prepare(monkeypatch)

    build_chunks._prepare_cpp(_cpp_args(library_cpp_compat=CPP_COMPAT_NAMES), tmp_path)

    assert calls == [
        (
            (REPOSITORY, COMMIT, tmp_path),
            {
                "source_subdir": None,
                "target": "tgt",
                "relax_warnings": False,
                "compile_options": ("-Wno-changes-meaning", "SHELL:-include limits"),
                "include_subdirs": True,
            },
        )
    ]


def test_prepare_cpp_without_compat_passes_exactly_todays_keywords(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _record_prepare(monkeypatch)

    build_chunks._prepare_cpp(_cpp_args(), tmp_path)

    assert calls == [
        ((REPOSITORY, COMMIT, tmp_path), {"source_subdir": None, "target": "tgt", "relax_warnings": False})
    ]


def test_verify_kwargs_with_page_includes_carry_the_pages_includes() -> None:
    build_dir = Path("workdir") / "_build"
    args = _cpp_args(library_cpp_compat=("include-limits", "page-includes"), furnished_page=JMAP_PAGE)

    kwargs = build_chunks._PLATFORM_DISPATCH["cpp"]["verify_kwargs"](build_dir, args)

    assert kwargs == {
        "library_dir": build_dir,
        "include_dir": Path("workdir") / "include",
        "consumer": True,
        "page_includes": ('#include "JmapClient.hpp"',),
    }


def test_verify_kwargs_without_page_includes_are_todays_dict() -> None:
    build_dir = Path("workdir") / "_build"
    expected = {"library_dir": build_dir, "include_dir": Path("workdir") / "include", "consumer": True}

    without_compat = build_chunks._PLATFORM_DISPATCH["cpp"]["verify_kwargs"](build_dir, _cpp_args())
    other_compat = build_chunks._PLATFORM_DISPATCH["cpp"]["verify_kwargs"](
        build_dir, _cpp_args(library_cpp_compat=("include-limits",), furnished_page=JMAP_PAGE)
    )

    assert without_compat == expected
    assert other_compat == expected


# --- the wiring: values.yaml, docker-compose.yml, the committed source ---------------------


def _pilots() -> list[dict]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))["ingestion"]["pilots"]


def test_values_give_cpp_compat_to_exactly_cells_email_and_jmap_cpp() -> None:
    holders = {
        (pilot["family"], pilot["platform"]): pilot["library"]["cppCompat"]
        for pilot in _pilots()
        if "cppCompat" in (pilot.get("library") or {})
    }

    assert holders == {key: COMPAT_VALUE for key in COMPAT_PILOTS}
    for pilot in _pilots():
        if (pilot["family"], pilot["platform"]) in {("pdf", "cpp"), ("slides", "cpp")}:
            assert "cppCompat" not in pilot["library"]


@pytest.mark.parametrize(("family", "platform"), COMPAT_PILOTS)
def test_compose_passes_cpp_compat_before_out(family: str, platform: str) -> None:
    import shlex

    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    tokens = shlex.split(services[f"ingest-{family}-{platform}"]["command"][0])

    assert "--library-cpp-compat" in tokens
    position = tokens.index("--library-cpp-compat")
    assert tokens[position + 1] == COMPAT_VALUE
    assert position < tokens.index("--out")


def test_committed_entrypoint_and_template_wire_the_switches() -> None:
    source = BUILD_CHUNKS.read_text(encoding="utf-8")
    assert "compile_options=" in source
    assert "page_includes" in source
    assert "--library-cpp-compat" in TEMPLATE.read_text(encoding="utf-8")
