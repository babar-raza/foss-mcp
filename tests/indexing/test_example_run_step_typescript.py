"""G3/TC-807 (REQ-G3-014; XA5T): TypeScript examples are RUN, not only type-checked.

``verify_typescript_example`` runs ``tsc --noEmit``; it never started the program. The authored TypeScript
examples are SELF-CHECKING programs (``throw new Error(...)``) whose checks never ran, so "Verification: passed"
meant "it type-checks". The pipeline now emits the candidate with the library's own ``tsc`` and starts the
JavaScript with ``node`` in a sandbox directory, through the same ``execute_in_sandbox`` / ``RuntimeRecord``
machinery java, dotnet, go and rust use.

Offline and deterministic: a FAKE runner stands in for every process, so no Node toolchain and no network are
used (the one test that writes files uses ``tmp_path``). The real-toolchain proof (a deliberately wrong program,
and the committed authored examples of three pilots, run for real) is in the card's report. The wiring proof is
the last section: the run step is reachable from the real ``build_chunks`` entrypoint.

Negative control: reverse-applying the production half of the commit (no typescript branch in
``runtime_command`` / ``run_compiled_candidate``, typescript out of ``_RUNNABLE_VERIFIERS``) fails every test
below that expects a TypeScript example to be run, and a runner that ignored the exit code would fail the
``exit 1`` tests.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
import yaml

import foss_mcp.indexing.example_runtime as runtime_module
import foss_mcp.indexing.example_verifier as ev
import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import (
    LEVEL_RAN_EXIT0,
    LEVEL_RAN_PRODUCED_OUTPUT,
    LEVEL_TYPE_CHECKED,
    RUNTIME_FAILED,
    RUNTIME_NO_OUTPUT,
    RUNTIME_RAN_OK,
    RunOutcome,
    RuntimeRecord,
    VerificationResult,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_CHUNKS_SOURCE = REPO_ROOT / "infra" / "build_chunks.py"
VERIFIER_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "indexing" / "example_verifier.py"
RUNTIME_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "indexing" / "example_runtime.py"
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"

LIB_REPO = "example-org/reference-typescript"
LIB_COMMIT = "7" * 40
PACKAGE = "@example/reference"
TOOLCHAIN = "v24.13.1 (test)"

# Imports the package by name: the verifier's paths-mode layout (ESM emitted into the candidate project).
PACKAGE_SELF_CHECK = (
    'import { Thing } from "@example/reference";\n'
    "const thing = new Thing();\n"
    'if (thing.size !== 1) throw new Error("self-check: expected size 1");\n'
)
# Imports by relative path from the repository root: the verifier's root-mode layout (emitted in the library).
ROOT_SELF_CHECK = (
    'import { Workbook } from "./aspose_cells";\n'
    "const workbook = new Workbook();\n"
    'if (!workbook) throw new Error("self-check");\n'
)

THROWN_STDERR = (
    "C:\\work\\ts_run\\out\\candidate.js:3\n"
    'if (thing.size !== 1) throw new Error("self-check: expected size 1");\n'
    "                      ^\n\n"
    "Error: self-check: expected size 1\n"
    "    at file:///C:/work/ts_run/out/candidate.js:3:29\n"
    "    at ModuleJob.run (node:internal/modules/esm/module_job:271:25)\n\n"
    "Node.js v24.13.1\n"
)

Runner = Callable[[Sequence[str], Path, float], RunOutcome]


def _is_tsc(argv: Sequence[str]) -> bool:
    return len(argv) > 1 and argv[1].replace("\\", "/").endswith("typescript/lib/tsc.js")


def fake_runner(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    seen: list | None = None,
    emit: bool = True,
    tsc_exit_code: int = 0,
    tsc_stdout: str = "",
    raises: Exception | None = None,
) -> Runner:
    """A runner for BOTH steps: ``node tsc.js ...`` (the emit; it writes the entry file the way tsc does when
    ``emit``) and ``node <entry>`` (the program, ``exit_code``)."""

    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if seen is not None:
            seen.append((list(argv), cwd, timeout_seconds))
        if raises is not None:
            raise raises
        if _is_tsc(argv):
            if emit:
                if "--outDir" in argv:
                    out = Path(argv[argv.index("--outDir") + 1])
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "candidate.js").write_text("// emitted\n", encoding="utf-8")
                else:
                    (cwd / runtime_module.TYPESCRIPT_ROOT_ENTRY).write_text("// emitted\n", encoding="utf-8")
            return RunOutcome(exit_code=tsc_exit_code, stdout=tsc_stdout)
        return RunOutcome(exit_code=exit_code, stdout=stdout, stderr=stderr)

    return runner


def candidate(code: str = PACKAGE_SELF_CHECK) -> CandidateExample:
    return CandidateExample(title="Self check", description="d", language="typescript", code=code)


def make_project(tmp_path: Path) -> tuple[Path, Path]:
    """A workdir whose ``candidate_project`` holds what ``verify_typescript_example`` leaves, and a library dir."""
    project = tmp_path / "work" / "candidate_project"
    project.mkdir(parents=True)
    (project / "candidate.ts").write_text(PACKAGE_SELF_CHECK, encoding="utf-8")
    (project / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    library = tmp_path / "library"
    library.mkdir()
    (library / "package.json").write_text(
        json.dumps({"name": PACKAGE, "main": "dist/index.js"}), encoding="utf-8"
    )
    return tmp_path / "work", library


def run_ts(
    tmp_path: Path, runner: Runner, code: str = PACKAGE_SELF_CHECK
) -> tuple[RuntimeRecord, Path, Path]:
    workdir, library = make_project(tmp_path)
    record = ev.run_compiled_candidate(
        "typescript",
        candidate(code),
        workdir=workdir,
        library=library,
        runner=runner,
        package_name=PACKAGE,
    )
    return record, workdir, library


# -- the run step, classified like java, dotnet, go and rust -----------------------------------------------------


def test_a_failing_self_check_is_a_failed_run_and_does_not_count_as_verified(tmp_path: Path) -> None:
    record, _, _ = run_ts(tmp_path, fake_runner(exit_code=1, stderr=THROWN_STDERR))
    assert record.status == RUNTIME_FAILED
    assert record.ran is True and record.exit_code == 1
    assert ev.counts_as_verified(record.status) is False
    assert ev.resolve_verification_level(LEVEL_TYPE_CHECKED, record) == LEVEL_TYPE_CHECKED


def test_the_thrown_error_lands_in_the_recorded_error(tmp_path: Path) -> None:
    record, _, _ = run_ts(tmp_path, fake_runner(exit_code=1, stderr=THROWN_STDERR))
    assert "self-check: expected size 1" in record.error
    assert not record.error.startswith("at ")


def test_an_uncaught_rejection_is_a_failed_run(tmp_path: Path) -> None:
    stderr = "node:internal/process/promises:394\n    triggerUncaughtException(err, true);\nError: boom\n"
    record, _, _ = run_ts(tmp_path, fake_runner(exit_code=1, stderr=stderr))
    assert record.status == RUNTIME_FAILED and record.exit_code == 1
    assert "boom" in record.error


def test_the_error_line_is_node_s_message_not_the_source_line_printed_above_it() -> None:
    find = runtime_module.node_error_line
    assert find(THROWN_STDERR, "") == "Error: self-check: expected size 1"
    assert find("TypeError: x is not a function\n    at f (a.js:1:1)\n") == "TypeError: x is not a function"
    assert find("Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'p'") == (
        "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'p'"
    )
    assert find("", "AssertionError: nope") == "AssertionError: nope", "stdout is read when stderr has none"
    assert find("throw new Error('x')\n    at y\n", "plain output") == ""


def test_the_node_error_line_is_used_for_typescript_only(tmp_path: Path) -> None:
    stderr = "triggerUncaughtException(err);\nError: from node\n"
    record = ev.execute_in_sandbox(
        ["node", "x"], runner=fake_runner(exit_code=1, stderr=stderr), platform="typescript"
    )
    assert record.error == "Error: from node"
    java = ev.execute_in_sandbox(
        ["java", "x"], runner=fake_runner(exit_code=1, stderr=stderr), platform="java"
    )
    assert java.error == "triggerUncaughtException(err);", "every other platform keeps the generic rule"


def test_an_explicit_exit_one_with_no_message_is_a_failed_run(tmp_path: Path) -> None:
    record, _, _ = run_ts(tmp_path, fake_runner(exit_code=1))
    assert record.status == RUNTIME_FAILED and record.exit_code == 1
    assert ev.counts_as_verified(record.status) is False


def test_exit_zero_with_output_is_recorded_as_ran_with_the_bytes_printed(tmp_path: Path) -> None:
    record, _, _ = run_ts(tmp_path, fake_runner(exit_code=0, stdout="cand1 OK\n"))
    assert record.status == RUNTIME_RAN_OK
    assert record.ran is True and record.exit_code == 0 and record.produced_output == len("cand1 OK\n")
    assert ev.counts_as_verified(record.status) is True
    assert ev.resolve_verification_level(LEVEL_TYPE_CHECKED, record) == LEVEL_RAN_PRODUCED_OUTPUT


def test_exit_zero_with_no_output_is_recorded_as_ran_exit0_and_not_demoted(tmp_path: Path) -> None:
    """A self-checking program prints nothing when every check holds: that is a pass, level ``ran-exit0``."""
    record, _, _ = run_ts(tmp_path, fake_runner(exit_code=0))
    assert record.ran is True and record.exit_code == 0 and record.produced_output == 0
    assert record.status == RUNTIME_RAN_OK != RUNTIME_NO_OUTPUT
    assert ev.counts_as_verified(record.status) is True
    assert ev.resolve_verification_level(LEVEL_TYPE_CHECKED, record) == LEVEL_RAN_EXIT0


def test_the_no_output_demotion_stays_java_and_dotnets_and_counts_as_verified_is_untouched() -> None:
    empty = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0)
    assert empty.status == RUNTIME_NO_OUTPUT and ev.counts_as_verified(empty.status) is False
    entry = {"platform": "java", "verified": True, "compiled": True, "ran": True, "exit_code": 0}
    assert ev.runtime_record_from_entry({**entry, "produced_output": 0}).status == RUNTIME_NO_OUTPUT
    ts_entry = {**entry, "platform": "typescript", "produced_output": 0}
    assert ev.runtime_record_from_entry(ts_entry).status == RUNTIME_RAN_OK
    assert ev.entry_counts_as_verified(ts_entry) is True
    assert ev.runtime_record_from_entry({**ts_entry, "exit_code": 1}).status == RUNTIME_FAILED
    assert ev.entry_counts_as_verified({**ts_entry, "exit_code": 1}) is False


def test_a_timeout_is_a_failed_run_that_is_environmental_and_never_cached(tmp_path: Path) -> None:
    record, _, _ = run_ts(tmp_path, fake_runner(exit_code=124, stderr="[foss-mcp] TIMEOUT: killed after 60s"))
    assert record.status == RUNTIME_FAILED
    assert record.error.startswith("timed out after")
    result = VerificationResult(candidate=candidate(), verified=True, output="type-checked")
    assert ev.is_environmental_verdict(result, record) is True
    other, _, _ = run_ts(tmp_path / "second", fake_runner(exit_code=1))
    assert ev.is_environmental_verdict(result, other) is False


def test_a_candidate_that_emitted_nothing_is_the_machines_failure_and_nothing_is_run(tmp_path: Path) -> None:
    seen: list = []
    record, _, _ = run_ts(tmp_path, fake_runner(seen=seen, emit=False, tsc_exit_code=2, tsc_stdout="TS6053"))
    assert [_is_tsc(call[0]) for call in seen] == [True], "the program is never started"
    assert record.ran is False and "wrote no JavaScript" in record.error and "TS6053" in record.error
    result = VerificationResult(candidate=candidate(), verified=True, output="type-checked")
    assert ev.is_environmental_verdict(result, record) is True


def test_a_tsc_exit_code_alone_is_not_a_verdict_the_entry_file_is(tmp_path: Path) -> None:
    """A library that does not type-check cleanly still emits: tsc exits 2 and the program runs."""
    seen: list = []
    record, _, _ = run_ts(tmp_path, fake_runner(seen=seen, tsc_exit_code=2, stdout="ok\n"))
    assert len(seen) == 2 and record.status == RUNTIME_RAN_OK


def test_a_runner_that_cannot_start_node_is_environmental(tmp_path: Path) -> None:
    record, _, _ = run_ts(tmp_path, fake_runner(raises=OSError("no such file")))
    assert record.ran is False and record.status == RUNTIME_FAILED
    result = VerificationResult(candidate=candidate(), verified=True, output="type-checked")
    assert ev.is_environmental_verdict(result, record) is True


def test_a_project_with_no_tsconfig_cannot_be_emitted(tmp_path: Path) -> None:
    workdir, library = make_project(tmp_path)
    (workdir / "candidate_project" / "tsconfig.json").unlink()
    seen: list = []
    record = ev.run_compiled_candidate(
        "typescript", candidate(), workdir=workdir, library=library, runner=fake_runner(seen=seen)
    )
    assert seen == [] and record.ran is False and "tsconfig.json" in record.error


def test_the_switch_off_leaves_the_run_unattempted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "RUNTIME_RUN_REQUIRED_FOR_VERIFIED", False)
    seen: list = []
    workdir, library = make_project(tmp_path)
    record = ev.run_compiled_candidate(
        "typescript", candidate(), workdir=workdir, library=library, runner=fake_runner(seen=seen)
    )
    assert record == RuntimeRecord(compiled=True)
    assert seen == []


def test_a_typescript_run_needs_the_library_directory(tmp_path: Path) -> None:
    assert ev.runtime_command("typescript", candidate(), workdir=tmp_path, library=None) is None
    seen: list = []
    record = ev.run_compiled_candidate(
        "typescript", candidate(), workdir=tmp_path, library=None, runner=fake_runner(seen=seen)
    )
    assert record == RuntimeRecord(compiled=True) and seen == []


def test_legacy_typescript_entry_levels_are_read_from_the_run_facts() -> None:
    type_checked_only = RuntimeRecord(compiled=True)
    assert ev.legacy_verification_level("typescript", type_checked_only) == LEVEL_TYPE_CHECKED
    ran = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0, output_optional=True)
    assert ev.legacy_verification_level("typescript", ran) == LEVEL_RAN_EXIT0


# -- what is emitted, where, and what is started ---------------------------------------------------------------


def test_a_package_import_is_emitted_as_esm_into_the_project_and_started_in_a_sandbox(tmp_path: Path) -> None:
    seen: list = []
    _, workdir, library = run_ts(tmp_path, fake_runner(seen=seen))
    project = workdir / "candidate_project"
    (emit_argv, emit_cwd, _), (run_argv, run_cwd, run_timeout) = seen
    out = project / "ts_run" / "out"
    assert emit_argv[0] == "node" and _is_tsc(emit_argv) and emit_cwd == project
    assert library.name in Path(emit_argv[1]).parts, "the library's own already-installed tsc"
    assert emit_argv[emit_argv.index("--noEmit") + 1] == "false", (
        "the verifier's tsconfig, emitting this time"
    )
    assert emit_argv[emit_argv.index("--outDir") + 1] == str(out)
    assert json.loads((out / "package.json").read_text(encoding="utf-8")) == {"type": "module"}
    assert run_argv == ["node", str(out / "candidate.js")]
    assert run_cwd != project and run_cwd.parent != project, "the program runs in the fixture sandbox"
    assert not run_cwd.exists(), "the sandbox is removed once the run ends"
    assert run_timeout == ev._RUNTIME_TIMEOUT_SECONDS == 60


def test_the_package_is_linked_into_the_emit_directory_by_the_verifiers_existing_link(tmp_path: Path) -> None:
    _, workdir, library = run_ts(tmp_path, fake_runner())
    link = workdir / "candidate_project" / "ts_run" / "out" / "node_modules" / PACKAGE
    assert link.exists(), "node resolves the package by name from the emitted candidate's directory"
    resolved = link.resolve() if link.is_symlink() else link
    assert resolved == library.resolve() or (link / "index.js").is_file()


def test_a_relative_import_is_emitted_in_place_in_the_library_and_cleaned_up_after_the_run(
    tmp_path: Path,
) -> None:
    seen: list = []
    workdir, library = make_project(tmp_path)
    during: list[bool] = []
    inner = fake_runner(seen=seen)

    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if not _is_tsc(argv):
            during.append((library / runtime_module.TYPESCRIPT_ROOT_ENTRY).is_file())
        return inner(argv, cwd, timeout_seconds)

    record = ev.run_compiled_candidate(
        "typescript", candidate(ROOT_SELF_CHECK), workdir=workdir, library=library, runner=runner
    )
    assert record.status == RUNTIME_RAN_OK
    (emit_argv, emit_cwd, _), (run_argv, _run_cwd, _) = seen
    assert emit_cwd == library and "--outDir" not in emit_argv and "--listEmittedFiles" in emit_argv
    assert run_argv == ["node", str(library / runtime_module.TYPESCRIPT_ROOT_ENTRY)]
    assert during == [True], "the emitted candidate exists while the program runs"
    leftovers = sorted(path.name for path in library.iterdir())
    assert leftovers == ["package.json"], f"the in-place emit leaves nothing behind, found {leftovers}"


def test_the_in_place_config_extends_the_librarys_tsconfig_and_emits_next_to_the_sources(
    tmp_path: Path,
) -> None:
    workdir, library = make_project(tmp_path)
    (library / "tsconfig.json").write_text('{"compilerOptions": {"module": "esnext"}}\n', encoding="utf-8")
    configs: list[dict] = []

    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if _is_tsc(argv):
            configs.append(json.loads((library / argv[argv.index("-p") + 1]).read_text(encoding="utf-8")))
            (library / runtime_module.TYPESCRIPT_ROOT_ENTRY).write_text("//\n", encoding="utf-8")
            return RunOutcome(exit_code=0)
        return RunOutcome(exit_code=0, stdout="x")

    ev.run_compiled_candidate(
        "typescript", candidate(ROOT_SELF_CHECK), workdir=workdir, library=library, runner=runner
    )
    (config,) = configs
    assert config["extends"] == "./tsconfig.json" and config["include"] == []
    options = config["compilerOptions"]
    assert options["noEmit"] is False and options["outDir"] == "." and options["module"] == "commonjs"
    assert options["moduleResolution"] == "node10", "a CommonJS emit cannot use bundler resolution"
    assert options["declaration"] is False


def test_every_file_the_emit_reports_is_removed_but_nothing_outside_the_library(tmp_path: Path) -> None:
    workdir, library = make_project(tmp_path)
    (library / "src").mkdir()
    outside = tmp_path / "keep.js"
    outside.write_text("keep", encoding="utf-8")

    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if not _is_tsc(argv):
            return RunOutcome(exit_code=0, stdout="x")
        (library / runtime_module.TYPESCRIPT_ROOT_ENTRY).write_text("//\n", encoding="utf-8")
        (library / "src" / "emitted.js").write_text("//\n", encoding="utf-8")
        listed = f"TSFILE: {(library / 'src' / 'emitted.js').as_posix()}\nTSFILE: {outside.as_posix()}\n"
        return RunOutcome(exit_code=0, stdout=listed)

    ev.run_compiled_candidate(
        "typescript", candidate(ROOT_SELF_CHECK), workdir=workdir, library=library, runner=runner
    )
    assert not (library / "src" / "emitted.js").exists()
    assert outside.read_text(encoding="utf-8") == "keep"


def test_an_esm_library_is_emitted_as_esm_in_place(tmp_path: Path) -> None:
    workdir, library = make_project(tmp_path)
    (library / "package.json").write_text(json.dumps({"type": "module"}), encoding="utf-8")
    seen_options: list[dict] = []

    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if _is_tsc(argv):
            document = json.loads((library / argv[argv.index("-p") + 1]).read_text(encoding="utf-8"))
            seen_options.append(document["compilerOptions"])
            (library / runtime_module.TYPESCRIPT_ROOT_ENTRY).write_text("//\n", encoding="utf-8")
        return RunOutcome(exit_code=0)

    ev.run_compiled_candidate(
        "typescript", candidate(ROOT_SELF_CHECK), workdir=workdir, library=library, runner=runner
    )
    assert seen_options[0]["module"] == "esnext" and seen_options[0]["moduleResolution"] == "bundler"
    assert seen_options[0]["target"] == "ES2022", "a library with no tsconfig gets a sane default"


def test_a_failed_in_place_emit_leaves_nothing_behind(tmp_path: Path) -> None:
    workdir, library = make_project(tmp_path)
    record = ev.run_compiled_candidate(
        "typescript",
        candidate(ROOT_SELF_CHECK),
        workdir=workdir,
        library=library,
        runner=fake_runner(emit=False, tsc_exit_code=2),
    )
    assert record.ran is False
    assert sorted(path.name for path in library.iterdir()) == ["package.json"]


def test_the_in_place_files_are_removed_even_when_the_run_raises(tmp_path: Path) -> None:
    workdir, library = make_project(tmp_path)

    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if _is_tsc(argv):
            (library / runtime_module.TYPESCRIPT_ROOT_ENTRY).write_text("//\n", encoding="utf-8")
            return RunOutcome(exit_code=0)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ev.run_compiled_candidate(
            "typescript", candidate(ROOT_SELF_CHECK), workdir=workdir, library=library, runner=runner
        )
    assert sorted(path.name for path in library.iterdir()) == ["package.json"]


def test_the_runtime_command_matches_the_layout_the_code_asks_for(tmp_path: Path) -> None:
    by_package = ev.runtime_command("typescript", candidate(), workdir=tmp_path, library=tmp_path / "lib")
    in_place = ev.runtime_command(
        "typescript", candidate(ROOT_SELF_CHECK), workdir=tmp_path, library=tmp_path / "lib"
    )
    assert by_package == ["node", str(tmp_path / "candidate_project" / "ts_run" / "out" / "candidate.js")]
    assert in_place == ["node", str(tmp_path / "lib" / runtime_module.TYPESCRIPT_ROOT_ENTRY)]


# -- the sandbox has the output directory the authored examples write into --------------------------------------


def test_the_sandbox_has_the_directory_an_example_saves_into(tmp_path: Path) -> None:
    code = 'workbook.save("outputfiles/cand1.xlsx");\nconst back = load(`outputfiles/other.pdf`);\n'
    runtime_module.ensure_output_directories(code, "typescript", tmp_path)
    assert (tmp_path / "outputfiles").is_dir()
    assert not (tmp_path / "outputfiles" / "cand1.xlsx").exists(), "directories only, never files"


def test_only_a_plain_relative_file_literal_makes_a_directory(tmp_path: Path) -> None:
    code = (
        'import { A } from "./dist/aspose/threed";\n'
        'import b from "@scope/pkg/sub.js";\n'
        'const u = "https://example.com/a/b.txt";\n'
        "const t = `${root}/x/y.txt`;\n"
        'const up = "../escape/file.txt";\n'
        'const abs = "/etc/passwd.txt";\n'
    )
    runtime_module.ensure_output_directories(code, "typescript", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_other_platforms_get_no_extra_directories(tmp_path: Path) -> None:
    runtime_module.ensure_output_directories('save("outputfiles/a.pdf")', "python", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_the_run_writes_into_the_sandbox_directory_not_the_project(tmp_path: Path) -> None:
    """The program's working directory has ``outputfiles/`` before it starts, and a file it writes counts."""
    code = PACKAGE_SELF_CHECK + 'save("outputfiles/report.bin");\n'
    seen_dirs: list[bool] = []
    inner = fake_runner()

    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if not _is_tsc(argv):
            seen_dirs.append((cwd / "outputfiles").is_dir())
            (cwd / "outputfiles" / "report.bin").write_bytes(b"12345")
            return RunOutcome(exit_code=0)
        return inner(argv, cwd, timeout_seconds)

    record, workdir, _ = run_ts(tmp_path, runner, code)
    assert seen_dirs == [True]
    assert record.produced_output == 5 and record.artefact_extensions == ("bin",)
    assert not (workdir / "candidate_project" / "outputfiles").exists()


# -- helper units -------------------------------------------------------------------------------------------------


def test_the_error_helpers_name_the_tsc_failure_and_read_only_inside_the_library(tmp_path: Path) -> None:
    outcome = RunOutcome(exit_code=5, stdout="out text", stderr="err text")
    message = runtime_module._tsc_failure(outcome)
    assert message.startswith("tsc emit exited 5") and "out text" in message and "err text" in message
    inside = tmp_path / "a.js"
    stdout = f"TSFILE: {inside.as_posix()}\nTSFILE: {(tmp_path.parent / 'outside.js').as_posix()}\nnoise\n"
    assert runtime_module._emitted_paths(stdout, tmp_path) == (inside,)


def test_remove_emitted_files_tolerates_a_file_that_is_already_gone(tmp_path: Path) -> None:
    there = tmp_path / "there.js"
    there.write_text("x", encoding="utf-8")
    runtime_module.remove_emitted_files([there, tmp_path / "gone.js"])
    assert not there.exists()


# -- through the real build_chunks entrypoint, compile stubbed, the run going through the real chain -------------


def _ts_page(path: Path, blocks: list[tuple[str, str]]) -> Path:
    front = {
        "title": "TypeScript run fixture",
        "single": {
            "block": [
                {"title": title, "content": f"Prose for {title}.\n\n```typescript\n{code}\n```\n"}
                for title, code in blocks
            ]
        },
    }
    path.write_text("---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n", encoding="utf-8")
    return path


def _argv(out_dir: Path, page: Path, extra: list[str] | None = None) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "TypeScript run fixture",
        "--out",
        str(out_dir / "chunks.json"),
        "--furnished-page",
        str(page),
        "--library-repository",
        LIB_REPO,
        "--library-commit",
        LIB_COMMIT,
        "--library-platform",
        "typescript",
        "--library-package-name",
        PACKAGE,
        *(extra or []),
    ]


def _main(argv: list[str]) -> None:
    old = build_chunks.sys.argv
    build_chunks.sys.argv = ["build_chunks.py", *argv]
    try:
        build_chunks.main()
    finally:
        build_chunks.sys.argv = old


def _example_texts(out_dir: Path) -> dict[str, str]:
    chunks = json.loads((out_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    texts = {}
    for chunk in chunks:
        match = re.search(r"^FQN: Example: (.+)$", chunk["text"], re.MULTILINE)
        if match and "Kind: verified_example" in chunk["text"]:
            texts[match.group(1)] = chunk["text"]
    return texts


class _TypeScriptPipeline:
    """TypeScript prepare and type-check stubbed (every candidate type-checks and leaves ``candidate_project``
    with a tsconfig); the run goes through the real ``run_compiled_candidate`` with a fake runner whose outcome
    the test chooses."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
        self.compiles: list[str] = []
        self.runs: list[list[str]] = []
        self.packages: list[str] = []
        self.exit_code = 0
        self.stdout = ""
        self.stderr = ""
        library = tmp / "reference_library"
        library.mkdir()
        monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
        monkeypatch.setattr(build_chunks, "prepare_typescript_library", lambda repo, commit, workdir: library)

        def fake_verify(cand, *, workdir, library_dir, package_name, **_kw):
            self.compiles.append(cand.title)
            self.packages.append(package_name)
            project = workdir / "candidate_project"
            project.mkdir(parents=True, exist_ok=True)
            (project / "tsconfig.json").write_text("{}\n", encoding="utf-8")
            return VerificationResult(
                candidate=cand, verified=True, output="type-checked", level=LEVEL_TYPE_CHECKED
            )

        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["typescript"], "verify", fake_verify)
        # the stand-in plays the real tsc --noEmit, which leaves the candidate project in the workdir
        monkeypatch.setitem(build_chunks._RUNNABLE_VERIFIERS, "typescript", fake_verify)

        def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
            self.runs.append(list(argv))
            if _is_tsc(argv) or (len(argv) > 1 and argv[1].endswith("tsc.js")):
                out = Path(argv[argv.index("--outDir") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / "candidate.js").write_text("//\n", encoding="utf-8")
                return RunOutcome(exit_code=0)
            return RunOutcome(exit_code=self.exit_code, stdout=self.stdout, stderr=self.stderr)

        monkeypatch.setattr(ev, "default_runtime_runner", runner)

    def build(self, tmp: Path, name: str, extra: list[str] | None = None) -> Path:
        out = tmp / name
        out.mkdir()
        page = _ts_page(tmp / f"{name}_index.md", [("Check the size", PACKAGE_SELF_CHECK)])
        _main(_argv(out, page, extra))
        return out


def _report(out: Path) -> dict:
    return json.loads((out / "chunks.verification.json").read_text(encoding="utf-8"))[0]


def test_a_failing_self_check_is_not_published_as_a_verified_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = _TypeScriptPipeline(monkeypatch, tmp_path)
    ts.exit_code, ts.stderr = 1, THROWN_STDERR
    out = ts.build(tmp_path, "failing")
    assert "Check the size" not in _example_texts(out)
    report = _report(out)
    assert report["verified"] is False and report["runtime_status"] == "failed"
    assert report["not_published"] == "the run failed" and report["exit_code"] == 1
    assert "self-check: expected size 1" in report["runtime_error"]
    assert len(ts.runs) == 2 and "--noEmit" in ts.runs[0], "emitted, then started once"


def test_the_package_name_reaches_the_run_step_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = _TypeScriptPipeline(monkeypatch, tmp_path)
    out = ts.build(tmp_path, "named")
    assert ts.packages == [PACKAGE]
    emitted_into = Path(ts.runs[0][ts.runs[0].index("--outDir") + 1])
    assert emitted_into.parts[-2:] == ("ts_run", "out")
    assert (out / "chunks.json").is_file()


def test_a_passing_silent_self_check_is_published_with_the_ran_exit0_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = _TypeScriptPipeline(monkeypatch, tmp_path)
    out = ts.build(tmp_path, "silent")
    text = _example_texts(out)["Check the size"]
    assert "Verification-Level: ran-exit0\n" in text
    assert "Runtime-Status:" not in text, "ran-exit0 is the claim; no output means no 'produced N bytes' line"
    report = _report(out)
    assert report["verified"] is True and report["exit_code"] == 0 and report["ran"] is True
    assert report["verification_level"] == "ran-exit0" and "not_published" not in report


def test_a_passing_program_that_prints_is_published_with_its_output_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = _TypeScriptPipeline(monkeypatch, tmp_path)
    ts.stdout = "ok\n"
    out = ts.build(tmp_path, "printing")
    text = _example_texts(out)["Check the size"]
    assert "Verification-Level: ran-produced-output\n" in text
    assert "Runtime-Status: ran\n" in text and "Runtime-Produced-Bytes: 3\n" in text


def test_a_timeout_is_not_published_and_is_not_written_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = _TypeScriptPipeline(monkeypatch, tmp_path)
    ts.exit_code, ts.stderr = 124, "[foss-mcp] TIMEOUT: run exceeded 60s and was stopped"
    cache_out = tmp_path / "cache.json"
    out = ts.build(tmp_path, "timeout", ["--verification-cache-out", str(cache_out)])
    assert "Check the size" not in _example_texts(out)
    assert _report(out)["runtime_status"] == "failed"
    cached = json.loads(cache_out.read_text(encoding="utf-8"))["entries"] if cache_out.exists() else []
    assert cached == [], "a timeout is the machine's verdict, never cached (TC-801)"


def test_the_recorded_run_is_cached_and_a_hit_serves_it_without_running_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = _TypeScriptPipeline(monkeypatch, tmp_path)
    cache = tmp_path / "cache.json"
    first = ts.build(tmp_path, "first", ["--verification-cache-out", str(cache)])
    (entry,) = json.loads(cache.read_text(encoding="utf-8"))["entries"]
    assert entry["platform"] == "typescript" and entry["ran"] is True and entry["exit_code"] == 0
    assert entry["produced_output"] == 0 and entry["verification_level"] == "ran-exit0"
    runs_before = len(ts.runs)
    second = ts.build(tmp_path, "second", ["--verification-cache", str(cache)])
    assert len(ts.runs) == runs_before and ts.compiles == ["Check the size"], "a hit neither emits nor runs"
    assert _example_texts(second)["Check the size"] == _example_texts(first)["Check the size"]


def test_a_cached_typescript_verdict_without_a_run_record_is_stale_and_is_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before TC-807 typescript entries recorded the type-check only; serving one would claim a run that never
    happened."""
    ts = _TypeScriptPipeline(monkeypatch, tmp_path)
    cache = tmp_path / "cache.json"
    ts.build(tmp_path, "seed", ["--verification-cache-out", str(cache)])
    document = json.loads(cache.read_text(encoding="utf-8"))
    for entry in document["entries"]:
        for key in ("ran", "exit_code", "produced_output", "verification_level"):
            entry.pop(key, None)
    cache.write_text(json.dumps(document), encoding="utf-8")
    ts.build(tmp_path, "again", ["--verification-cache", str(cache)])
    assert ts.compiles == ["Check the size", "Check the size"]


# -- liveness: the run step is CALLED by the production entrypoint, not only correct in isolation ----------------


def test_the_real_typescript_verifier_is_registered_as_runnable_in_the_production_entrypoint() -> None:
    assert "typescript" in ev.RUNNABLE_PLATFORMS
    assert build_chunks._RUNNABLE_VERIFIERS["typescript"] is ev.verify_typescript_example
    assert (
        build_chunks._PLATFORM_DISPATCH["typescript"]["verify"]
        is build_chunks._RUNNABLE_VERIFIERS["typescript"]
    )
    assert set(build_chunks._RUNNABLE_VERIFIERS) == set(ev.RUNNABLE_PLATFORMS)
    source = BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    assert '"typescript": verify_typescript_example' in source
    assert "run_compiled_candidate(" in source and "_RUNNABLE_VERIFIERS.get(platform)" in source
    assert 'package_name=getattr(args, "library_package_name", None)' in source


def test_the_verifier_reaches_the_typescript_run_builders_in_example_runtime() -> None:
    source = VERIFIER_SOURCE.read_text(encoding="utf-8")
    assert "from foss_mcp.indexing.example_runtime import" in source
    called = {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {
        "typescript_run_command",
        "build_typescript_run_files",
        "remove_emitted_files",
        "ensure_output_directories",
    } <= called


def test_example_runtime_is_a_leaf_module_with_no_import_of_the_verifier() -> None:
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding="utf-8"))
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    assert not [name for name in imported if "example_verifier" in name or "build_chunks" in name]


def test_every_other_platform_stays_unrun() -> None:
    for platform in set(build_chunks._PLATFORM_DISPATCH) - ev.RUNNABLE_PLATFORMS:
        assert ev.runtime_command(platform, candidate(), workdir=Path("w"), library=Path("l")) is None
