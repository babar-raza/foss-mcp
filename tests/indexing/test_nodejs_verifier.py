"""TC-362 (REQ-G3-005): the Node.js (plain ESM JavaScript) verifier and its nodejs dispatch entry.

sample/nodejs is plain ESM JavaScript (package aspose-sample-foss, entry src/index.js, no dependencies),
so there is nothing to compile. The verifier is a weaker bar than compilation: ``node --check`` for
syntax, then a generated module holding only the candidate's imports of the package, run so that a
named import the library does not export fails at link time without running the candidate.

These tests are offline: ``ev._run``, ``ev.shutil.which`` and (where used) ``ev.os.symlink`` are
replaced, so no git, npm or node ever runs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import ExampleEnvironmentError, verify_nodejs_example

REPOSITORY = "example-org/reference-js"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PACKAGE_NAME = "aspose-sample-foss"


def _completed(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> object:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


class FakeToolchain:
    """Replaces ``ev._run``: any argv starting with ``git`` succeeds; the rest are recorded."""

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


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    (root / "src").mkdir(parents=True)
    (root / "src" / "index.js").write_text("export const SampleClient = class {};\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": PACKAGE_NAME, "type": "module", "main": "src/index.js"}), encoding="utf-8"
    )
    return root


def _candidate(code: str) -> CandidateExample:
    return CandidateExample(title="example", description="", language="javascript", code=code)


def _sym_ok(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, Path, bool]]:
    """Replace os.symlink with a recorder that succeeds and creates nothing."""
    made: list[tuple[Path, Path, bool]] = []

    def fake_symlink(src: object, dst: object, target_is_directory: bool = False) -> None:
        made.append((Path(str(src)), Path(str(dst)), target_is_directory))

    monkeypatch.setattr(ev.os, "symlink", fake_symlink)
    return made


# --- prepare_nodejs_library -------------------------------------------------------------------


def _seed_repo(workdir: Path, package: dict | None) -> Path:
    repo = workdir / "repo"
    repo.mkdir(parents=True)
    if package is not None:
        (repo / "package.json").write_text(json.dumps(package), encoding="utf-8")
    return repo


def test_prepare_runs_no_npm_for_a_dependency_free_package(toolchain: FakeToolchain, tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path / "work", {"name": PACKAGE_NAME, "main": "src/index.js"})

    assert ev.prepare_nodejs_library(REPOSITORY, COMMIT, tmp_path / "work") == repo
    assert toolchain.calls == []


@pytest.mark.parametrize(
    "package", [None, {"dependencies": {}}, {"dependencies": None}, {"devDependencies": {"x": "1"}}]
)
def test_prepare_runs_no_npm_without_runtime_dependencies(
    toolchain: FakeToolchain, tmp_path: Path, package: dict | None
) -> None:
    _seed_repo(tmp_path / "work", package)

    ev.prepare_nodejs_library(REPOSITORY, COMMIT, tmp_path / "work")

    assert toolchain.calls == []


def test_prepare_runs_npm_install_omit_dev_for_a_package_with_dependencies(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    repo = _seed_repo(tmp_path / "work", {"dependencies": {"left-pad": "^1.0.0"}})

    assert ev.prepare_nodejs_library(REPOSITORY, COMMIT, tmp_path / "work") == repo
    assert toolchain.calls == [(["/usr/bin/npm", "install", "--omit=dev"], repo)]


def test_prepare_raises_when_npm_install_fails(toolchain: FakeToolchain, tmp_path: Path) -> None:
    _seed_repo(tmp_path / "work", {"dependencies": {"left-pad": "^1.0.0"}})
    toolchain.responder = lambda args, cwd: _completed(args, 1, stderr="E404")

    with pytest.raises(RuntimeError, match="npm install"):
        ev.prepare_nodejs_library(REPOSITORY, COMMIT, tmp_path / "work")


def test_prepare_raises_when_npm_is_missing_but_needed(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_repo(tmp_path / "work", {"dependencies": {"left-pad": "^1.0.0"}})
    monkeypatch.setattr(ev.shutil, "which", lambda name, *a, **k: "/usr/bin/node" if name == "node" else None)

    with pytest.raises(RuntimeError, match="npm"):
        ev.prepare_nodejs_library(REPOSITORY, COMMIT, tmp_path / "work")


def test_prepare_raises_an_environment_error_when_node_is_missing(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ev.shutil, "which", lambda name, *a, **k: None)

    with pytest.raises(ExampleEnvironmentError) as excinfo:
        ev.prepare_nodejs_library(REPOSITORY, COMMIT, tmp_path / "work")

    assert excinfo.value.tool == "node"
    assert not (tmp_path / "work").exists()


# --- verify_nodejs_example --------------------------------------------------------------------


def _verify(
    toolchain: FakeToolchain,
    tmp_path: Path,
    library: Path,
    code: str,
    *,
    codes: tuple[int, int] = (0, 0),
    package_name: str = PACKAGE_NAME,
) -> tuple[object, dict[str, str], Path]:
    """Run the verifier with scripted node exit codes; returns (result, link-check text, project dir)."""
    seen: dict[str, str] = {}
    outcomes = list(codes)

    def respond(args: list[str], cwd: Path) -> object:
        link_check = cwd / "__foss_mcp_link_check__.mjs"
        if link_check.exists():
            seen["link_check"] = link_check.read_text(encoding="utf-8")
        returncode = outcomes.pop(0)
        return _completed(args, returncode, stdout=f"stdout-{returncode}", stderr=f"stderr-{returncode}")

    toolchain.responder = respond
    workdir = tmp_path / "work"
    result = verify_nodejs_example(
        _candidate(code), library_dir=library, package_name=package_name, workdir=workdir
    )
    return result, seen, workdir / "candidate_project"


GOOD_CANDIDATE = (
    f'import {{ SampleClient }} from "{PACKAGE_NAME}";\nconst c = new SampleClient();\nconsole.log(c);\n'
)


def test_verify_runs_a_syntax_check_then_the_link_check_in_the_project_dir(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _sym_ok(monkeypatch)

    result, seen, project = _verify(toolchain, tmp_path, library, GOOD_CANDIDATE)

    assert toolchain.calls == [
        (["node", "--check", "candidate.mjs"], project),
        (["node", "__foss_mcp_link_check__.mjs"], project),
    ]
    assert (project / "candidate.mjs").read_text(encoding="utf-8") == GOOD_CANDIDATE
    assert result.verified is True
    assert "stdout-0" in result.output


def test_link_check_holds_only_the_package_import_single_line(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _sym_ok(monkeypatch)
    code = (
        'import fs from "node:fs";\n'
        f'import {{ SampleClient, Mailbox }} from "{PACKAGE_NAME}";\n'
        'import other from "other-package";\n'
        "const c = new SampleClient(fs, other, Mailbox);\n"
        "c.connect();\n"
    )

    _, seen, _ = _verify(toolchain, tmp_path, library, code)

    assert seen["link_check"] == f'import {{ SampleClient, Mailbox }} from "{PACKAGE_NAME}";\n'


def test_link_check_holds_only_the_package_import_multi_line(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _sym_ok(monkeypatch)
    code = (
        "import {\n"
        "  SampleClient,\n"
        "  Mailbox,\n"
        f'}} from "{PACKAGE_NAME}";\n'
        "const c = new SampleClient();\n"
        "console.log(c, Mailbox);\n"
    )

    _, seen, _ = _verify(toolchain, tmp_path, library, code)

    assert seen["link_check"].startswith("import {")
    assert "SampleClient," in seen["link_check"]
    assert "Mailbox," in seen["link_check"]
    assert seen["link_check"].rstrip().endswith(f'}} from "{PACKAGE_NAME}";')
    assert "const c" not in seen["link_check"]
    assert "console" not in seen["link_check"]


def test_link_check_keeps_each_package_import_and_subpath_import(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _sym_ok(monkeypatch)
    code = (
        f'import {{ A }} from "{PACKAGE_NAME}";\n'
        f"import B from '{PACKAGE_NAME}/sub/path.js'\n"
        f'import * as ns from "{PACKAGE_NAME}-extra";\n'
        f'export {{ C }} from "{PACKAGE_NAME}";\n'
        "console.log(A, B, ns);\n"
    )

    _, seen, _ = _verify(toolchain, tmp_path, library, code)

    assert seen["link_check"] == (
        f"import {{ A }} from \"{PACKAGE_NAME}\";\nimport B from '{PACKAGE_NAME}/sub/path.js';\n"
    )


@pytest.mark.parametrize(
    ("codes", "verified"),
    [((0, 0), True), ((0, 1), False)],
)
def test_verified_needs_both_node_calls_to_succeed(
    toolchain: FakeToolchain,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    library: Path,
    codes: tuple[int, int],
    verified: bool,
) -> None:
    _sym_ok(monkeypatch)

    result, _, _ = _verify(toolchain, tmp_path, library, GOOD_CANDIDATE, codes=codes)

    assert result.verified is verified
    assert len(toolchain.calls) == 2
    if not verified:
        assert "stderr-1" in result.output


def test_a_syntax_failure_skips_the_link_check(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _sym_ok(monkeypatch)

    result, seen, project = _verify(toolchain, tmp_path, library, GOOD_CANDIDATE, codes=(1,))

    assert result.verified is False
    assert toolchain.calls == [(["node", "--check", "candidate.mjs"], project)]
    assert "link_check" not in seen
    assert not (project / "__foss_mcp_link_check__.mjs").exists()
    assert "stderr-1" in result.output


def test_a_candidate_with_no_package_import_is_not_verified_and_says_so(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _sym_ok(monkeypatch)
    code = 'import fs from "node:fs";\nconst c = new SampleClient();\nconsole.log(c, fs);\n'

    result, seen, project = _verify(toolchain, tmp_path, library, code, codes=(0,))

    assert result.verified is False
    assert "no static import" in result.output
    assert PACKAGE_NAME in result.output
    assert "link_check" not in seen
    assert [argv for argv, _ in toolchain.calls] == [["node", "--check", "candidate.mjs"]]


def test_a_dynamic_import_does_not_count_as_a_package_import(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _sym_ok(monkeypatch)
    code = f'const m = await import("{PACKAGE_NAME}");\nconsole.log(m);\n'

    result, _, _ = _verify(toolchain, tmp_path, library, code, codes=(0,))

    assert result.verified is False
    assert "no static import" in result.output


def test_node_modules_entry_is_a_directory_symlink_to_the_library(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    made = _sym_ok(monkeypatch)

    _, _, project = _verify(toolchain, tmp_path, library, GOOD_CANDIDATE)

    assert made == [(library.resolve(), project / "node_modules" / PACKAGE_NAME, True)]


def test_a_scoped_package_name_links_below_its_scope_directory(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    made = _sym_ok(monkeypatch)
    code = 'import { SampleClient } from "@aspose/sample";\nconsole.log(SampleClient);\n'

    result, seen, project = _verify(toolchain, tmp_path, library, code, package_name="@aspose/sample")

    assert made[0][1] == project / "node_modules" / "@aspose" / "sample"
    assert (project / "node_modules" / "@aspose").is_dir()
    assert seen["link_check"] == 'import { SampleClient } from "@aspose/sample";\n'
    assert result.verified is True


def _refuse_symlink(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("symlinks not permitted")

    monkeypatch.setattr(ev.os, "symlink", refuse)


def test_the_shim_package_is_written_when_symlinks_are_refused(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _refuse_symlink(monkeypatch)

    result, _, project = _verify(toolchain, tmp_path, library, GOOD_CANDIDATE)

    shim = project / "node_modules" / PACKAGE_NAME
    assert json.loads((shim / "package.json").read_text(encoding="utf-8")) == {"type": "module"}
    index = (shim / "index.js").read_text(encoding="utf-8")
    assert index == f'export * from "{(library / "src" / "index.js").resolve().as_uri()}";\n'
    assert result.verified is True


def test_the_shim_follows_exports_dot_before_main(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _refuse_symlink(monkeypatch)
    (library / "package.json").write_text(
        json.dumps(
            {"main": "src/index.js", "exports": {".": {"import": "./lib/entry.js", "default": "./x.js"}}}
        ),
        encoding="utf-8",
    )

    _, _, project = _verify(toolchain, tmp_path, library, GOOD_CANDIDATE)

    index = (project / "node_modules" / PACKAGE_NAME / "index.js").read_text(encoding="utf-8")
    assert (library / "lib" / "entry.js").resolve().as_uri() in index


def test_the_shim_accepts_a_string_exports_and_falls_back_to_index_js(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _refuse_symlink(monkeypatch)
    (library / "package.json").write_text(json.dumps({"exports": "./main.js"}), encoding="utf-8")
    assert ev._nodejs_package_entry(library) == library / "main.js"

    (library / "package.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    assert ev._nodejs_package_entry(library) == library / "index.js"


def test_a_second_run_in_the_same_workdir_replaces_the_previous_shim(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _refuse_symlink(monkeypatch)
    _verify(toolchain, tmp_path, library, GOOD_CANDIDATE)
    toolchain.calls.clear()

    result, _, project = _verify(toolchain, tmp_path, library, GOOD_CANDIDATE)

    assert result.verified is True
    assert (project / "node_modules" / PACKAGE_NAME / "index.js").is_file()


def test_the_verifier_never_executes_the_candidate_itself(
    toolchain: FakeToolchain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: Path
) -> None:
    _sym_ok(monkeypatch)

    _verify(toolchain, tmp_path, library, GOOD_CANDIDATE)

    assert ["node", "candidate.mjs"] not in [argv for argv, _ in toolchain.calls]


def test_the_docstring_states_the_weaker_bar() -> None:
    assert "WEAKER" in (ev.verify_nodejs_example.__doc__ or "")


# --- the nodejs dispatch entry -----------------------------------------------------------------


def test_nodejs_entry_has_the_documented_keys_and_functions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = build_chunks._PLATFORM_DISPATCH["nodejs"]

    assert set(entry) == {"prepare", "verify", "verify_kwargs", "shared_page_workdir"}
    assert entry["verify"] is verify_nodejs_example
    assert entry["shared_page_workdir"] is False

    sentinel = object()
    calls: list[tuple] = []

    def recorder(repository: str, commit: str, workdir: Path) -> object:
        calls.append((repository, commit, workdir))
        return sentinel

    monkeypatch.setattr(build_chunks, "prepare_nodejs_library", recorder)
    args = argparse.Namespace(library_repository=REPOSITORY, library_commit=COMMIT)

    assert entry["prepare"](args, tmp_path) is sentinel
    assert calls == [(REPOSITORY, COMMIT, tmp_path)]


def test_nodejs_verify_kwargs_gives_library_dir_and_package_name() -> None:
    prepared = Path("workdir") / "repo"
    args = argparse.Namespace(library_package_name=PACKAGE_NAME)

    kwargs = build_chunks._PLATFORM_DISPATCH["nodejs"]["verify_kwargs"](prepared, args)

    assert kwargs == {"library_dir": prepared, "package_name": PACKAGE_NAME}


def test_typescript_entry_is_unchanged() -> None:
    entry = build_chunks._PLATFORM_DISPATCH["typescript"]

    assert entry["verify"] is ev.verify_typescript_example
    assert entry["shared_page_workdir"] is False
    assert entry["verify_kwargs"](Path("w"), argparse.Namespace(library_package_name="p")) == {
        "library_dir": Path("w"),
        "package_name": "p",
    }


def test_no_dispatch_entry_other_than_python_shares_a_page_workdir() -> None:
    for name, entry in build_chunks._PLATFORM_DISPATCH.items():
        assert entry["shared_page_workdir"] is (name == "python"), name


def test_nodejs_requires_the_package_name_flag() -> None:
    assert build_chunks._PLATFORM_REQUIRED_FLAGS["nodejs"] == (
        "library_package_name",
        "--library-package-name",
    )


def test_library_platform_nodejs_without_package_name_exits_2_naming_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "chunks.json"
    old_argv = sys.argv
    sys.argv = [
        "build_chunks.py",
        "--api-surface",
        "tests/fixtures/pdf_net/api_surface.json",
        "--title",
        "Reference JS",
        "--out",
        str(out_path),
        "--furnished-page",
        "tests/fixtures/furnished/pdf_net/pages/_index.md",
        "--library-repository",
        REPOSITORY,
        "--library-commit",
        COMMIT,
        "--library-platform",
        "nodejs",
        # --library-package-name deliberately omitted
    ]
    try:
        with pytest.raises(SystemExit) as excinfo:
            build_chunks.main()
    finally:
        sys.argv = old_argv

    assert excinfo.value.code == 2
    assert "--library-platform nodejs requires --library-package-name" in capsys.readouterr().err
    assert not out_path.exists()
