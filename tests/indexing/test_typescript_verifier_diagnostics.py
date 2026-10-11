"""TC-452 (REQ-G3-007): the TypeScript verifier judges a candidate on its own diagnostics.

Two real failures of the 2026-10-11 diagnosis are pinned here. 3d/typescript declares
``types: dist/index.d.ts`` but its build writes ``dist/aspose/threed/index.d.ts``, so the library
preparation must tolerate the missing declared file when the build produced declaration files.
cells/typescript fails strict tsc only inside the LIBRARY's own sources, so a run whose every
diagnostic is parsed, attributed to a library file and absent from the candidate verifies, with the
deciding rule written into the output. A candidate's own diagnostic, an unparsed error mention and an
import-only block never verify.

These tests are offline: ``ev._run`` and ``ev.shutil.which`` are replaced, so no node, npm, npx or
tsc ever runs. Synthetic package.json and source trees live in tmp_path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample, extract_candidate_examples

REPOSITORY = "example-org/reference-ts"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PACKAGE_NAME = "aspose-reference-foss"
CELLS_PAGE = Path("tests/fixtures/furnished/cells_typescript/pages/_index.md")

# The exact cells/typescript tsc output of the 2026-10-11 diagnosis: three library diagnostics, one
# with an indented continuation line.
CELLS_OUTPUT = (
    "aspose_cells/chartLoader.ts(210,11): error TS2345: Argument of type 'string | null' is not "
    "assignable to parameter of type 'string | undefined'.\n"
    "  Type 'null' is not assignable to type 'string | undefined'.\n"
    "aspose_cells/html/htmlDocument.ts(92,54): error TS7006: Parameter 'node' implicitly has an "
    "'any' type.\n"
    "aspose_cells/workbook.ts(482,63): error TS2345: Argument of type 'string | null' is not "
    "assignable to parameter of type 'string'.\n"
    "  Type 'null' is not assignable to type 'string'.\n"
)
# The exact 3d/typescript tsc output for its rejected "Additional Examples" block.
THREE_D_OUTPUT = (
    "__foss_mcp_candidate__.ts(9,54): error TS2339: Property 'controlPoints' does not exist on "
    "type 'Entity'.\n"
)


def _completed(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> object:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


class FakeToolchain:
    """Replaces ``ev._run``: every argv starting with ``git`` succeeds; the rest are recorded and
    answered by ``responder`` (default: success)."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path]] = []
        self.responder: Callable[[list[str], Path], object] | None = None

    def __call__(self, args: list[str], *, cwd: Path) -> object:
        if args[0] == "git":
            return _completed(args)
        self.calls.append((list(args), cwd))
        if self.responder is not None:
            return self.responder(list(args), cwd)
        return _completed(args)


@pytest.fixture
def toolchain(monkeypatch: pytest.MonkeyPatch) -> FakeToolchain:
    fake = FakeToolchain()
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    return fake


def _candidate(code: str) -> CandidateExample:
    return CandidateExample(title="example", description="", language="typescript", code=code)


def _write_repo(root: Path, package: dict, files: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
    for relative in files:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}" if relative.endswith(".json") else "export {};\n", encoding="utf-8")


def _tsc_writes(outputs: list[str]) -> Callable[[list[str], Path], object]:
    def respond(args: list[str], cwd: Path) -> object:
        if "tsc" in args:
            for relative in outputs:
                target = cwd / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("// built\n", encoding="utf-8")
        return _completed(args)

    return respond


# --- prepare_typescript_library: a missing declared types file ----------------------------------


def test_prepare_tolerates_a_missing_declared_types_file_when_declarations_were_built(
    toolchain: FakeToolchain, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """3d/typescript: package.json declares dist/index.d.ts, the build writes
    dist/aspose/threed/index.d.ts. Prepare returns the repo and says so on stderr."""
    repo = tmp_path / "work" / "repo"
    _write_repo(
        repo,
        {"main": "dist/index.js", "types": "dist/index.d.ts"},
        ["tsconfig.json", "src/index.ts"],
    )
    toolchain.responder = _tsc_writes(["dist/aspose/threed/index.js", "dist/aspose/threed/index.d.ts"])

    result = ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work")

    assert result == repo
    stderr_lines = [line for line in capsys.readouterr().err.splitlines() if "[foss-mcp]" in line]
    assert len(stderr_lines) == 1
    line = stderr_lines[0]
    assert str(repo / "dist" / "index.d.ts") in line
    assert "dist/aspose/threed/index.d.ts" in line
    assert "holds 1 declaration files" in line
    assert "package-name imports will not resolve" in line


def test_prepare_counts_every_declaration_file_and_names_the_first(
    toolchain: FakeToolchain, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "work" / "repo"
    _write_repo(repo, {"types": "dist/index.d.ts"}, ["tsconfig.json"])
    toolchain.responder = _tsc_writes(["dist/b/index.d.ts", "dist/a/deep/x.d.ts", "dist/a/y.js"])

    assert ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work") == repo
    err = capsys.readouterr().err
    assert "holds 2 declaration files (first: dist/a/deep/x.d.ts)" in err


def test_prepare_still_raises_when_dist_holds_no_declaration_file(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    repo = tmp_path / "work" / "repo"
    _write_repo(
        repo,
        {"main": "dist/src/index.js", "types": "dist/src/index.d.ts"},
        ["tsconfig.json", "src/index.ts"],
    )
    (repo / "dist").mkdir()
    (repo / "dist" / "unrelated.js").write_text("//\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="declared types file"):
        ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work")


# --- _typescript_verdict: the table ---------------------------------------------------------------


def test_verdict_exit_zero_is_verified() -> None:
    assert ev._typescript_verdict(0, "") == (True, "tsc exit 0")


def test_verdict_cells_library_only_diagnostics_verify_and_name_the_files() -> None:
    verified, note = ev._typescript_verdict(2, CELLS_OUTPUT)

    assert verified is True
    assert note == (
        "tsc exit 2; all 3 diagnostics are in library files (aspose_cells/chartLoader.ts, "
        "aspose_cells/html/htmlDocument.ts, aspose_cells/workbook.ts) and none in the candidate; "
        "the candidate's own use of the library type-checks"
    )


def test_verdict_3d_candidate_diagnostic_is_not_verified() -> None:
    verified, note = ev._typescript_verdict(2, THREE_D_OUTPUT)

    assert verified is False
    assert "candidate" in note


def test_verdict_library_plus_candidate_diagnostics_is_not_verified() -> None:
    verified, _note = ev._typescript_verdict(2, CELLS_OUTPUT + THREE_D_OUTPUT)

    assert verified is False


def test_verdict_paths_mode_candidate_file_is_not_verified() -> None:
    output = "candidate.ts(1,22): error TS2307: Cannot find module 'aspose-reference-foss'.\n"

    assert ev._typescript_verdict(2, output)[0] is False


def test_verdict_windows_library_path_verifies_and_windows_candidate_path_does_not() -> None:
    library = "C:\\work\\repo\\aspose_cells\\workbook.ts(482,63): error TS2345: Argument is wrong.\n"
    candidate = "C:\\work\\repo\\__foss_mcp_candidate__.ts(3,5): error TS2339: Property is missing.\n"

    assert ev._typescript_verdict(2, library)[0] is True
    assert ev._typescript_verdict(2, candidate)[0] is False
    assert ev._typescript_verdict(2, library + candidate)[0] is False


def test_verdict_file_less_config_error_is_not_verified() -> None:
    config_error = "error TS5083: Cannot read file 'tsconfig.json'.\n"
    library = "aspose_cells/workbook.ts(482,63): error TS2345: Argument is wrong.\n"

    assert ev._typescript_verdict(1, config_error)[0] is False
    assert ev._typescript_verdict(1, library + config_error)[0] is False


def test_verdict_diagnostic_without_a_message_is_not_a_parsed_diagnostic() -> None:
    """``aspose_cells.ts(1,1): error TS2305`` has no ``: message`` part, so it does not parse."""
    assert ev._typescript_verdict(2, "aspose_cells.ts(1,1): error TS2305\n")[0] is False
    library = "aspose_cells/workbook.ts(482,63): error TS2345: Argument is wrong.\n"
    assert ev._typescript_verdict(2, library + "aspose_cells.ts(1,1): error TS2305\n")[0] is False


def test_verdict_non_zero_exit_without_diagnostics_is_not_verified() -> None:
    assert ev._typescript_verdict(1, "")[0] is False
    assert ev._typescript_verdict(2, "node: command crashed\n")[0] is False


# --- verify_typescript_example end to end -----------------------------------------------------------


def _cells_quick_start() -> CandidateExample:
    text = CELLS_PAGE.read_text(encoding="utf-8")
    page = yaml.safe_load(text.split("---", 2)[1])
    candidates = extract_candidate_examples(page)
    return next(c for c in candidates if c.title == "Quick Start")


def _library_dir(tmp_path: Path) -> Path:
    library = tmp_path / "repo"
    _write_repo(library, {"name": "aspose-cells-foss", "main": "index.ts"}, ["index.ts", "tsconfig.json"])
    return library


def test_cells_quick_start_with_library_only_diagnostics_is_verified_with_a_verdict_line(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = _library_dir(tmp_path)
    candidate = _cells_quick_start()
    seen_files: list[bool] = []

    def respond(args: list[str], cwd: Path) -> object:
        seen_files.append((library / ev._TS_CANDIDATE_FILENAME).is_file())
        return _completed(args, 2, stdout=CELLS_OUTPUT)

    toolchain.responder = respond

    result = ev.verify_typescript_example(
        candidate, library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "work"
    )

    assert result.verified is True
    assert result.candidate is candidate
    assert result.output.startswith("[foss-mcp] verdict: tsc exit 2; all 3 diagnostics are in library files")
    assert "aspose_cells/workbook.ts(482,63)" in result.output
    assert seen_files == [True]
    assert not (library / ev._TS_CANDIDATE_FILENAME).exists()
    assert not (library / ev._TS_ROOT_TSCONFIG_FILENAME).exists()
    assert len(toolchain.calls) == 1
    assert toolchain.calls[0][0][-2:] == ["-p", ev._TS_ROOT_TSCONFIG_FILENAME]
    assert toolchain.calls[0][1] == library


def test_candidate_diagnostic_is_not_verified_and_temporary_files_are_removed(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = _library_dir(tmp_path)
    candidate = _candidate(
        'import { Workbook } from "./aspose_cells";\nconst w = new Workbook();\nw.nope();\n'
    )
    toolchain.responder = lambda args, cwd: _completed(args, 2, stdout=THREE_D_OUTPUT)

    result = ev.verify_typescript_example(
        candidate, library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "work"
    )

    assert result.verified is False
    assert result.candidate is candidate
    assert result.output.startswith("[foss-mcp] verdict: tsc exit 2;")
    assert "controlPoints" in result.output
    assert not (library / ev._TS_CANDIDATE_FILENAME).exists()
    assert not (library / ev._TS_ROOT_TSCONFIG_FILENAME).exists()


def test_clean_run_in_paths_mode_is_verified_with_the_exit_zero_rule(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = tmp_path / "repo"
    _write_repo(library, {"main": "dist/index.js", "types": "dist/index.d.ts"}, ["dist/index.d.ts"])
    candidate = _candidate('import { Thing } from "aspose-reference-foss";\nconst t = new Thing();\n')

    result = ev.verify_typescript_example(
        candidate, library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "work"
    )

    assert result.verified is True
    assert result.output.startswith("[foss-mcp] verdict: tsc exit 0\n")
    assert toolchain.calls[0][0][-2:] == ["-p", "tsconfig.json"]


def test_import_only_block_is_rejected_without_running_anything(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = _library_dir(tmp_path)
    candidate = _candidate('import { Workbook } from "./aspose_cells";')

    result = ev.verify_typescript_example(
        candidate, library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "work"
    )

    assert result.verified is False
    assert result.candidate is candidate
    assert result.output == ev._NO_STATEMENTS_MESSAGE.format(kind="import and export-from declarations")
    assert toolchain.calls == []


def test_multi_line_import_only_block_is_rejected_without_running_anything(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = _library_dir(tmp_path)
    code = (
        "// the public surface\n"
        "import {\n  Workbook,\n  Style,\n} from './aspose_cells';\n"
        'export { Workbook } from "./aspose_cells";\n'
    )

    result = ev.verify_typescript_example(
        _candidate(code), library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "work"
    )

    assert result.verified is False
    assert "no statements of its own" in result.output
    assert toolchain.calls == []


def test_block_with_a_statement_after_its_imports_still_runs_tsc(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = _library_dir(tmp_path)
    code = 'import { Workbook } from "./aspose_cells";\nconst workbook = new Workbook();\n'

    result = ev.verify_typescript_example(
        _candidate(code), library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "work"
    )

    assert result.verified is True
    assert len(toolchain.calls) == 1


# --- wiring ------------------------------------------------------------------------------------------


def test_build_chunks_dispatches_typescript_to_the_real_prepare_and_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = build_chunks._PLATFORM_DISPATCH["typescript"]
    assert entry["verify"] is ev.verify_typescript_example

    sentinel = tmp_path / "prepared"
    calls: list[tuple] = []

    def fake_prepare(repository: str, commit: str, workdir: Path) -> Path:
        calls.append((repository, commit, workdir))
        return sentinel

    monkeypatch.setattr(build_chunks, "prepare_typescript_library", fake_prepare)
    args = argparse.Namespace(library_repository=REPOSITORY, library_commit=COMMIT)

    assert entry["prepare"](args, tmp_path) is sentinel
    assert calls == [(REPOSITORY, COMMIT, tmp_path)]
