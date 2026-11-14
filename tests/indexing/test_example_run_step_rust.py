"""G3/TC-806 (REQ-G3-014; XA5R): Rust examples are RUN, not only built.

``verify_rust_example`` runs ``cargo build``; it never started the program. The authored Rust examples are
SELF-CHECKING programs (``assert!`` / ``panic!`` / ``std::process::exit``) whose checks never ran, so
"Verification: passed" meant "it compiles". The pipeline now makes sure the candidate crate's binary exists and
starts THAT in a sandbox directory through the same ``execute_in_sandbox`` / ``RuntimeRecord`` machinery java,
dotnet and go use (not ``cargo run``: the sandbox working directory is where fixtures are staged).

Offline and deterministic: a FAKE runner stands in for every process, so no Rust toolchain and no network are
used. The real-toolchain proof (a deliberately wrong program, and the committed cells/rust examples, run for
real) is in the card's report. The wiring proof is the last section: the run step is reachable from the real
``build_chunks`` entrypoint.

Negative control: reverse-applying the production half of the commit (no rust branch in ``runtime_command`` /
``run_compiled_candidate``, rust out of ``_RUNNABLE_VERIFIERS``) fails every test below that expects a rust
example to be run, and a runner that ignored the exit code would fail the ``exit 101`` tests.
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
    LEVEL_BUILT,
    LEVEL_RAN_EXIT0,
    LEVEL_RAN_PRODUCED_OUTPUT,
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

LIB_REPO = "example-org/reference-rust"
LIB_COMMIT = "5" * 40
CRATE = "reference_rust"
TOOLCHAIN = "cargo 1.98.1 (test)"

# A complete, self-checking program: the verifier takes it as given (it has its own ``fn main``).
SELF_CHECKING_RUST = 'fn main() {\n    let sum = 1 + 1;\n    assert_eq!(sum, 2, "self-check");\n}\n'

PANIC_STDERR = (
    "\nthread 'main' (4242) panicked at src/main.rs:3:5:\n"
    "assertion `left == right` failed: self-check\n"
    "  left: 3\n"
    " right: 2\n"
    "note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace\n"
)

Runner = Callable[[Sequence[str], Path, float], RunOutcome]


def fake_runner(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    seen: list | None = None,
    build_exit_code: int = 0,
    raises: Exception | None = None,
) -> Runner:
    """A runner for BOTH steps: ``cargo build ...`` (``build_exit_code``) and the program (``exit_code``)."""

    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if seen is not None:
            seen.append((list(argv), cwd, timeout_seconds))
        if raises is not None:
            raise raises
        if list(argv[:2]) == ["cargo", "build"]:
            return RunOutcome(exit_code=build_exit_code, stderr="link failure" if build_exit_code else "")
        return RunOutcome(exit_code=exit_code, stdout=stdout, stderr=stderr)

    return runner


def candidate(code: str = SELF_CHECKING_RUST) -> CandidateExample:
    return CandidateExample(title="Self check", description="d", language="rust", code=code)


def run_rust(tmp_path: Path, runner: Runner, code: str = SELF_CHECKING_RUST) -> RuntimeRecord:
    return ev.run_compiled_candidate("rust", candidate(code), workdir=tmp_path, runner=runner)


# -- the run step, classified like java, dotnet and go ---------------------------------------------------------


def test_a_failing_assertion_is_a_failed_run_and_does_not_count_as_verified(tmp_path: Path) -> None:
    record = run_rust(tmp_path, fake_runner(exit_code=101, stderr=PANIC_STDERR))
    assert record.status == RUNTIME_FAILED
    assert record.ran is True and record.exit_code == 101
    assert ev.counts_as_verified(record.status) is False
    assert ev.resolve_verification_level(LEVEL_BUILT, record) == LEVEL_BUILT


def test_the_panic_message_lands_in_the_recorded_error(tmp_path: Path) -> None:
    """``thread 'main' panicked at <where>:`` names the place, the NEXT line says what failed: both are kept."""
    record = run_rust(tmp_path, fake_runner(exit_code=101, stderr=PANIC_STDERR))
    assert "panicked at src/main.rs:3:5:" in record.error
    assert "assertion `left == right` failed: self-check" in record.error
    assert "RUST_BACKTRACE" not in record.error, "the rustc note is boilerplate, not the reason"


def test_a_panic_header_with_no_following_line_is_still_recorded(tmp_path: Path) -> None:
    stderr = "thread 'main' panicked at src/main.rs:3:5:"
    record = run_rust(tmp_path, fake_runner(exit_code=101, stderr=stderr))
    assert record.status == RUNTIME_FAILED and record.error == stderr


def test_a_main_that_returns_err_is_a_failed_run(tmp_path: Path) -> None:
    record = run_rust(
        tmp_path, fake_runner(exit_code=1, stderr='Error: Custom { kind: NotFound, error: "x" }\n')
    )
    assert record.status == RUNTIME_FAILED and record.exit_code == 1
    assert record.error.startswith("Error: Custom")


def test_an_explicit_exit_one_with_no_message_is_a_failed_run(tmp_path: Path) -> None:
    record = run_rust(tmp_path, fake_runner(exit_code=1))
    assert record.status == RUNTIME_FAILED and record.exit_code == 1
    assert ev.counts_as_verified(record.status) is False


def test_exit_zero_with_output_is_recorded_as_ran_with_the_bytes_printed(tmp_path: Path) -> None:
    record = run_rust(tmp_path, fake_runner(exit_code=0, stdout="ok 42\n"))
    assert record.status == RUNTIME_RAN_OK
    assert record.ran is True and record.exit_code == 0 and record.produced_output == len("ok 42\n")
    assert ev.counts_as_verified(record.status) is True
    assert ev.resolve_verification_level(LEVEL_BUILT, record) == LEVEL_RAN_PRODUCED_OUTPUT


def test_exit_zero_with_no_output_is_recorded_as_ran_exit0_and_not_demoted(tmp_path: Path) -> None:
    """A self-checking program prints nothing when every check holds: that is a pass, level ``ran-exit0``."""
    record = run_rust(tmp_path, fake_runner(exit_code=0))
    assert record.ran is True and record.exit_code == 0 and record.produced_output == 0
    assert record.status == RUNTIME_RAN_OK != RUNTIME_NO_OUTPUT
    assert ev.counts_as_verified(record.status) is True
    assert ev.resolve_verification_level(LEVEL_BUILT, record) == LEVEL_RAN_EXIT0


def test_the_no_output_demotion_stays_java_and_dotnets_and_counts_as_verified_is_untouched() -> None:
    empty = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0)
    assert empty.status == RUNTIME_NO_OUTPUT and ev.counts_as_verified(empty.status) is False
    entry = {"platform": "java", "verified": True, "compiled": True, "ran": True, "exit_code": 0}
    assert ev.runtime_record_from_entry({**entry, "produced_output": 0}).status == RUNTIME_NO_OUTPUT
    rust_entry = {**entry, "platform": "rust", "produced_output": 0}
    assert ev.runtime_record_from_entry(rust_entry).status == RUNTIME_RAN_OK
    assert ev.entry_counts_as_verified(rust_entry) is True
    assert ev.runtime_record_from_entry({**rust_entry, "exit_code": 101}).status == RUNTIME_FAILED
    assert ev.entry_counts_as_verified({**rust_entry, "exit_code": 101}) is False


def test_a_timeout_is_a_failed_run_that_is_environmental_and_never_cached(tmp_path: Path) -> None:
    record = run_rust(tmp_path, fake_runner(exit_code=124, stderr="[foss-mcp] TIMEOUT: killed after 60s"))
    assert record.status == RUNTIME_FAILED
    assert record.error.startswith("timed out after")
    result = VerificationResult(candidate=candidate(), verified=True, output="built")
    assert ev.is_environmental_verdict(result, record) is True
    assert ev.is_environmental_verdict(result, run_rust(tmp_path, fake_runner(exit_code=101))) is False


def test_the_binary_is_ensured_first_then_started_in_a_sandbox_not_in_the_crate_directory(
    tmp_path: Path,
) -> None:
    seen: list = []
    run_rust(tmp_path, fake_runner(seen=seen))
    project_dir = tmp_path / "candidate_project"
    (build_argv, build_cwd, _), (run_argv, run_cwd, run_timeout) = seen
    assert build_argv[:2] == ["cargo", "build"] and "run" not in build_argv, "not `cargo run`"
    assert build_cwd == project_dir
    assert run_argv == runtime_module.rust_run_command(project_dir)
    assert Path(run_argv[0]).stem == "candidate" and "debug" in Path(run_argv[0]).parts
    assert run_cwd != project_dir and run_cwd.parent != project_dir, "the program runs in the fixture sandbox"
    assert not run_cwd.exists(), "the sandbox is removed once the run ends"
    assert run_timeout == ev._RUNTIME_TIMEOUT_SECONDS == 60


def test_the_runtime_command_for_rust_is_the_built_binary(tmp_path: Path) -> None:
    argv = ev.runtime_command("rust", candidate(), workdir=tmp_path, library=None)
    assert argv == runtime_module.rust_run_command(tmp_path / "candidate_project")
    assert argv is not None and tmp_path / "candidate_project" in Path(argv[0]).parents


def test_the_binary_is_where_cargo_puts_it_even_when_cargo_target_dir_moves_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    default = runtime_module.rust_binary_path(tmp_path)
    assert default.parent == tmp_path / "target" / "debug"
    monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path / "elsewhere"))
    assert runtime_module.rust_binary_path(tmp_path).parent == tmp_path / "elsewhere" / "debug"


def test_a_binary_that_cannot_be_built_is_the_machines_failure_and_nothing_is_run(tmp_path: Path) -> None:
    seen: list = []
    record = run_rust(tmp_path, fake_runner(seen=seen, build_exit_code=1))
    assert [call[0][:2] for call in seen] == [["cargo", "build"]], "the program is never started"
    assert record.ran is False and "link failure" in record.error
    result = VerificationResult(candidate=candidate(), verified=True, output="built")
    assert ev.is_environmental_verdict(result, record) is True


def test_a_runner_that_cannot_start_the_binary_is_environmental(tmp_path: Path) -> None:
    record = run_rust(tmp_path, fake_runner(raises=OSError("no such file")))
    assert record.ran is False and record.status == RUNTIME_FAILED
    result = VerificationResult(candidate=candidate(), verified=True, output="built")
    assert ev.is_environmental_verdict(result, record) is True


def test_the_switch_off_leaves_the_run_unattempted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "RUNTIME_RUN_REQUIRED_FOR_VERIFIED", False)
    seen: list = []
    assert run_rust(tmp_path, fake_runner(seen=seen)) == RuntimeRecord(compiled=True)
    assert seen == []


def test_legacy_rust_entry_levels_are_read_from_the_run_facts() -> None:
    built_only = RuntimeRecord(compiled=True)
    assert ev.legacy_verification_level("rust", built_only) == LEVEL_BUILT
    ran = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0, output_optional=True)
    assert ev.legacy_verification_level("rust", ran) == LEVEL_RAN_EXIT0


def test_the_panic_detail_helper_reads_only_a_panic_header() -> None:
    panic = ["thread 'main' panicked at src/main.rs:1:1:", "boom", "note: run with `RUST_BACKTRACE=1`"]
    assert runtime_module.panic_detail_line(panic, 0) == "boom"
    assert runtime_module.panic_detail_line(panic, 1) == ""
    assert runtime_module.panic_detail_line(["thread 'main' panicked at x:1:1:", "note: n"], 0) == ""
    assert runtime_module.panic_detail_line(["Error: boom", "more"], 0) == ""


# -- through the real build_chunks entrypoint, compile stubbed, the run going through the real chain -------------


def _rust_page(path: Path, blocks: list[tuple[str, str]]) -> Path:
    front = {
        "title": "Rust run fixture",
        "single": {
            "block": [
                {"title": title, "content": f"Prose for {title}.\n\n```rust\n{code}\n```\n"}
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
        "Rust run fixture",
        "--out",
        str(out_dir / "chunks.json"),
        "--furnished-page",
        str(page),
        "--library-repository",
        LIB_REPO,
        "--library-commit",
        LIB_COMMIT,
        "--library-platform",
        "rust",
        "--library-crate-name",
        CRATE,
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


class _RustPipeline:
    """Rust prepare and compile stubbed (every candidate compiles and leaves ``candidate_project``); the run goes
    through the real ``run_compiled_candidate`` with a fake runner whose outcome the test chooses."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.compiles: list[str] = []
        self.runs: list[list[str]] = []
        self.exit_code = 0
        self.stdout = ""
        self.stderr = ""
        monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
        monkeypatch.setattr(
            build_chunks, "prepare_rust_library", lambda repo, commit, workdir: workdir / "lib"
        )

        def fake_verify(cand, *, workdir, library_crate_name, library_dir, **_kw):
            self.compiles.append(cand.title)
            (workdir / "candidate_project").mkdir(parents=True, exist_ok=True)
            return VerificationResult(candidate=cand, verified=True, output="built", level=LEVEL_BUILT)

        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", fake_verify)
        # the stand-in plays the real cargo build, which leaves the built crate in the workdir
        monkeypatch.setitem(build_chunks._RUNNABLE_VERIFIERS, "rust", fake_verify)

        def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
            self.runs.append(list(argv))
            if list(argv[:2]) == ["cargo", "build"]:
                return RunOutcome(exit_code=0)
            return RunOutcome(exit_code=self.exit_code, stdout=self.stdout, stderr=self.stderr)

        monkeypatch.setattr(ev, "default_runtime_runner", runner)

    def build(self, tmp: Path, name: str, extra: list[str] | None = None) -> Path:
        out = tmp / name
        out.mkdir()
        page = _rust_page(tmp / f"{name}_index.md", [("Check the sum", SELF_CHECKING_RUST)])
        _main(_argv(out, page, extra))
        return out


def _report(out: Path) -> dict:
    return json.loads((out / "chunks.verification.json").read_text(encoding="utf-8"))[0]


def test_a_failing_self_check_is_not_published_as_a_verified_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rust = _RustPipeline(monkeypatch)
    rust.exit_code, rust.stderr = 101, PANIC_STDERR
    out = rust.build(tmp_path, "failing")
    assert "Check the sum" not in _example_texts(out)
    report = _report(out)
    assert report["verified"] is False and report["runtime_status"] == "failed"
    assert report["not_published"] == "the run failed" and report["exit_code"] == 101
    assert "assertion `left == right` failed" in report["runtime_error"]
    assert len(rust.runs) == 2 and rust.runs[0][:2] == ["cargo", "build"], "built, then started once"


def test_a_passing_silent_self_check_is_published_with_the_ran_exit0_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rust = _RustPipeline(monkeypatch)
    out = rust.build(tmp_path, "silent")
    text = _example_texts(out)["Check the sum"]
    assert "Verification-Level: ran-exit0\n" in text
    assert "Runtime-Status:" not in text, "ran-exit0 is the claim; no output means no 'produced N bytes' line"
    report = _report(out)
    assert report["verified"] is True and report["exit_code"] == 0 and report["ran"] is True
    assert report["verification_level"] == "ran-exit0" and "not_published" not in report


def test_a_passing_program_that_prints_is_published_with_its_output_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rust = _RustPipeline(monkeypatch)
    rust.stdout = "ok\n"
    out = rust.build(tmp_path, "printing")
    text = _example_texts(out)["Check the sum"]
    assert "Verification-Level: ran-produced-output\n" in text
    assert "Runtime-Status: ran\n" in text and "Runtime-Produced-Bytes: 3\n" in text


def test_a_timeout_is_not_published_and_is_not_written_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rust = _RustPipeline(monkeypatch)
    rust.exit_code, rust.stderr = 124, "[foss-mcp] TIMEOUT: run exceeded 60s and was stopped"
    cache_out = tmp_path / "cache.json"
    out = rust.build(tmp_path, "timeout", ["--verification-cache-out", str(cache_out)])
    assert "Check the sum" not in _example_texts(out)
    assert _report(out)["runtime_status"] == "failed"
    cached = json.loads(cache_out.read_text(encoding="utf-8"))["entries"] if cache_out.exists() else []
    assert cached == [], "a timeout is the machine's verdict, never cached (TC-801)"


def test_the_recorded_run_is_cached_and_a_hit_serves_it_without_running_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rust = _RustPipeline(monkeypatch)
    cache = tmp_path / "cache.json"
    first = rust.build(tmp_path, "first", ["--verification-cache-out", str(cache)])
    (entry,) = json.loads(cache.read_text(encoding="utf-8"))["entries"]
    assert entry["platform"] == "rust" and entry["ran"] is True and entry["exit_code"] == 0
    assert entry["produced_output"] == 0 and entry["verification_level"] == "ran-exit0"
    runs_before = len(rust.runs)
    second = rust.build(tmp_path, "second", ["--verification-cache", str(cache)])
    assert len(rust.runs) == runs_before and rust.compiles == ["Check the sum"], (
        "a hit neither builds nor runs"
    )
    assert _example_texts(second)["Check the sum"] == _example_texts(first)["Check the sum"]


def test_a_cached_rust_verdict_without_a_run_record_is_stale_and_is_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before TC-806 rust entries recorded the build only; serving one would claim a run that never happened."""
    rust = _RustPipeline(monkeypatch)
    cache = tmp_path / "cache.json"
    rust.build(tmp_path, "seed", ["--verification-cache-out", str(cache)])
    document = json.loads(cache.read_text(encoding="utf-8"))
    for entry in document["entries"]:
        for key in ("ran", "exit_code", "produced_output", "verification_level"):
            entry.pop(key, None)
    cache.write_text(json.dumps(document), encoding="utf-8")
    rust.build(tmp_path, "again", ["--verification-cache", str(cache)])
    assert rust.compiles == ["Check the sum", "Check the sum"]


# -- liveness: the run step is CALLED by the production entrypoint, not only correct in isolation ----------------


def test_the_real_rust_verifier_is_registered_as_runnable_in_the_production_entrypoint() -> None:
    assert "rust" in ev.RUNNABLE_PLATFORMS
    assert build_chunks._RUNNABLE_VERIFIERS["rust"] is ev.verify_rust_example
    assert build_chunks._PLATFORM_DISPATCH["rust"]["verify"] is build_chunks._RUNNABLE_VERIFIERS["rust"]
    assert set(build_chunks._RUNNABLE_VERIFIERS) == set(ev.RUNNABLE_PLATFORMS)
    source = BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    assert '"rust": verify_rust_example' in source
    assert "run_compiled_candidate(" in source and "_RUNNABLE_VERIFIERS.get(platform)" in source


def test_the_verifier_reaches_the_rust_run_builders_in_example_runtime() -> None:
    source = VERIFIER_SOURCE.read_text(encoding="utf-8")
    assert "from foss_mcp.indexing.example_runtime import" in source
    assert "rust_run_command(project_dir)" in source and "build_rust_run_binary(" in source
    called = {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"rust_run_command", "build_rust_run_binary", "panic_detail_line"} <= called


def test_example_runtime_is_a_leaf_module_with_no_import_of_the_verifier() -> None:
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding="utf-8"))
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    assert not [name for name in imported if "example_verifier" in name or "build_chunks" in name]


def test_every_other_platform_stays_unrun() -> None:
    for platform in set(build_chunks._PLATFORM_DISPATCH) - ev.RUNNABLE_PLATFORMS:
        assert ev.runtime_command(platform, candidate(), workdir=Path("w"), library=Path("l")) is None
