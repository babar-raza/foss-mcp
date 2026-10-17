"""TC-362 (REQ-G3-005): the TypeScript verifier takes its build and entry point from the library.

pdf/typescript builds with tsconfig.build.json into dist/ and exports dist/index.d.ts. A dist/src layout
builds with tsconfig.json into dist/src/, 3d/typescript builds with tsconfig.json into dist/, and
cells/typescript has no build at all (package.json main is index.ts). README blocks for cells and 3d
import by a relative path from the repository root, which a ``paths`` mapping can never resolve, so
those candidates are type-checked in a repository-root mode.

These tests are offline: ``ev._run`` and ``ev.shutil.which`` are replaced, so no git, npm, npx, node
or tsc ever runs. Synthetic package.json, tsconfig and source trees live in tmp_path.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample

REPOSITORY = "example-org/reference-ts"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PACKAGE_NAME = "aspose-reference-foss"

# (layout name, package.json or None, files by relative path, expected types entry relative to the
# library root, the build the layout must get: None means no build)
LAYOUTS: dict[str, dict] = {
    "pdf": {
        "package": {"main": "dist/index.js", "types": "dist/index.d.ts"},
        "files": ["tsconfig.build.json", "tsconfig.json", "src/index.ts"],
        "entry": "dist/index",
        "build": ["tsc", "-p", "tsconfig.build.json"],
        "outputs": ["dist/index.js", "dist/index.d.ts"],
    },
    "dist-src": {
        "package": {"main": "dist/src/index.js", "types": "dist/src/index.d.ts"},
        "files": ["tsconfig.json", "src/index.ts"],
        "entry": "dist/src/index",
        "build": ["tsc", "-p", "tsconfig.json"],
        "outputs": ["dist/src/index.js", "dist/src/index.d.ts"],
    },
    "3d": {
        "package": {"main": "dist/index.js", "types": "dist/index.d.ts"},
        "files": ["tsconfig.json", "src/index.ts"],
        "entry": "dist/index",
        "build": ["tsc", "-p", "tsconfig.json"],
        "outputs": ["dist/index.js", "dist/index.d.ts"],
    },
    "cells": {
        "package": {"name": "aspose-cells-foss", "main": "index.ts"},
        "files": ["index.ts", "tsconfig.json"],
        "entry": "index",
        "build": None,
        "outputs": [],
    },
}


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


def _write_tree(root: Path, package: dict | None, files: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if package is not None:
        (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
    for relative in files:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}" if relative.endswith(".json") else "export {};\n", encoding="utf-8")


def _candidate(code: str) -> CandidateExample:
    return CandidateExample(title="example", description="", language="typescript", code=code)


def _tsc_writes(outputs: list[str]) -> Callable[[list[str], Path], object]:
    def respond(args: list[str], cwd: Path) -> object:
        if "tsc" in args:
            for relative in outputs:
                target = cwd / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("// built\n", encoding="utf-8")
        return _completed(args)

    return respond


# --- _typescript_types_entry ---------------------------------------------------------------


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_types_entry_for_each_pilot_layout(tmp_path: Path, layout: str) -> None:
    """pdf (dist/index.d.ts), dist-src (dist/src/index.d.ts), 3d (dist/index.d.ts) and cells (main
    index.ts, no types key) each resolve to the extensionless entry their own package.json names."""
    spec = LAYOUTS[layout]
    _write_tree(tmp_path, spec["package"], [])

    assert ev._typescript_types_entry(tmp_path) == tmp_path / spec["entry"]


def test_types_entry_without_package_json_is_dist_index(tmp_path: Path) -> None:
    assert ev._typescript_types_entry(tmp_path) == tmp_path / "dist" / "index"


def test_types_entry_with_package_json_lacking_all_keys_is_dist_index(tmp_path: Path) -> None:
    _write_tree(tmp_path, {"name": "x", "version": "1.0.0"}, [])

    assert ev._typescript_types_entry(tmp_path) == tmp_path / "dist" / "index"


def test_types_entry_with_unreadable_package_json_is_dist_index(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")

    assert ev._typescript_types_entry(tmp_path) == tmp_path / "dist" / "index"


def test_types_entry_prefers_types_then_typings_then_main(tmp_path: Path) -> None:
    _write_tree(tmp_path, {"main": "m.js", "typings": "t/typings.d.ts", "types": "t/types.d.ts"}, [])
    assert ev._typescript_types_entry(tmp_path) == tmp_path / "t" / "types"

    _write_tree(tmp_path, {"main": "m.js", "typings": "t/typings.d.ts"}, [])
    assert ev._typescript_types_entry(tmp_path) == tmp_path / "t" / "typings"

    _write_tree(tmp_path, {"main": "m.js"}, [])
    assert ev._typescript_types_entry(tmp_path) == tmp_path / "m"


@pytest.mark.parametrize(
    ("declared", "stripped"),
    [
        ("./dist/index.d.ts", "dist/index"),
        ("lib/entry.ts", "lib/entry"),
        ("lib/entry.js", "lib/entry"),
        ("lib/entry.mjs", "lib/entry"),
        ("lib/entry.cjs", "lib/entry"),
        ("lib/entry", "lib/entry"),
        ("lib/entry.min.js", "lib/entry.min"),
    ],
)
def test_types_entry_strips_one_known_suffix(tmp_path: Path, declared: str, stripped: str) -> None:
    _write_tree(tmp_path, {"main": declared}, [])

    assert ev._typescript_types_entry(tmp_path) == tmp_path / stripped


# --- prepare_typescript_library --------------------------------------------------------------


def _prepare(toolchain: FakeToolchain, tmp_path: Path, layout: str) -> tuple[Path, list[list[str]]]:
    spec = LAYOUTS[layout]
    repo = tmp_path / "work" / "repo"
    _write_tree(repo, spec["package"], spec["files"])
    toolchain.responder = _tsc_writes(spec["outputs"])
    result = ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work")
    return result, [call[0] for call in toolchain.calls]


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_prepare_chooses_the_build_from_the_librarys_own_files(
    toolchain: FakeToolchain, tmp_path: Path, layout: str
) -> None:
    """Each layout runs npm install, then exactly its own npx tsc build - or, for cells, nothing."""
    result, argvs = _prepare(toolchain, tmp_path, layout)

    expected = [["/usr/bin/npm", "install"]]
    if LAYOUTS[layout]["build"] is not None:
        expected.append(["/usr/bin/npx", *LAYOUTS[layout]["build"]])
    assert argvs == expected
    assert result == tmp_path / "work" / "repo"
    assert all(cwd == result for _, cwd in toolchain.calls)


def test_prepare_pdf_layout_is_unchanged_install_then_tsconfig_build(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    """tsconfig.build.json wins over tsconfig.json, with the exact argv pdf/typescript always had."""
    _, argvs = _prepare(toolchain, tmp_path, "pdf")

    assert argvs == [
        ["/usr/bin/npm", "install"],
        ["/usr/bin/npx", "tsc", "-p", "tsconfig.build.json"],
    ]


def test_prepare_cells_with_a_tsconfig_still_does_no_build(toolchain: FakeToolchain, tmp_path: Path) -> None:
    """cells/typescript's main is a TypeScript source file, so even with a tsconfig.json nothing is built."""
    _, argvs = _prepare(toolchain, tmp_path, "cells")

    assert ["/usr/bin/npx", "tsc", "-p", "tsconfig.json"] not in argvs
    assert not any("tsc" in argv for argv in argvs)


def test_prepare_raises_when_the_build_leaves_the_declared_types_file_missing(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    spec = LAYOUTS["dist-src"]
    repo = tmp_path / "work" / "repo"
    _write_tree(repo, spec["package"], spec["files"])
    (repo / "dist").mkdir()
    (repo / "dist" / "unrelated.js").write_text("//\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="declared types file"):
        ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work")


def test_prepare_without_declared_types_keeps_the_nonempty_dist_check(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    repo = tmp_path / "work" / "repo"
    _write_tree(repo, {"main": "dist/index.js"}, ["tsconfig.build.json"])

    with pytest.raises(RuntimeError, match="did not produce dist/"):
        ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work")


def test_prepare_without_package_json_builds_with_the_build_config_and_checks_dist(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    repo = tmp_path / "work" / "repo"
    _write_tree(repo, None, ["tsconfig.build.json"])
    toolchain.responder = _tsc_writes(["dist/index.js"])

    assert ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work") == repo
    assert [c[0] for c in toolchain.calls] == [
        ["/usr/bin/npm", "install"],
        ["/usr/bin/npx", "tsc", "-p", "tsconfig.build.json"],
    ]


def test_prepare_raises_naming_the_repository_when_there_is_nothing_to_build(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    repo = tmp_path / "work" / "repo"
    _write_tree(repo, {"main": "dist/index.js"}, [])

    with pytest.raises(RuntimeError, match=REPOSITORY):
        ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work")


def test_prepare_raises_when_the_build_fails(toolchain: FakeToolchain, tmp_path: Path) -> None:
    spec = LAYOUTS["3d"]
    repo = tmp_path / "work" / "repo"
    _write_tree(repo, spec["package"], spec["files"])
    toolchain.responder = lambda args, cwd: (
        _completed(args, 2, stderr="error TS2307") if "tsc" in args else _completed(args)
    )

    with pytest.raises(RuntimeError, match="failed to build cleanly"):
        ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work")


def test_prepare_raises_when_npm_install_fails(toolchain: FakeToolchain, tmp_path: Path) -> None:
    spec = LAYOUTS["pdf"]
    _write_tree(tmp_path / "work" / "repo", spec["package"], spec["files"])
    toolchain.responder = lambda args, cwd: _completed(args, 1, stderr="ERESOLVE")

    with pytest.raises(RuntimeError, match="npm install"):
        ev.prepare_typescript_library(REPOSITORY, COMMIT, tmp_path / "work")
    assert len(toolchain.calls) == 1


# --- verify_typescript_example: paths mode ---------------------------------------------------


PACKAGE_CANDIDATE = f'import {{ Thing }} from "{PACKAGE_NAME}";\nconst t = new Thing();\nconsole.log(t);\n'


def test_verify_pdf_shaped_candidate_writes_todays_tsconfig_and_argv(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    _write_tree(library, LAYOUTS["pdf"]["package"], LAYOUTS["pdf"]["files"])
    workdir = tmp_path / "work"

    result = ev.verify_typescript_example(
        _candidate(PACKAGE_CANDIDATE), library_dir=library, package_name=PACKAGE_NAME, workdir=workdir
    )

    expected = ev._TS_TSCONFIG_TEMPLATE.format(
        package_name=PACKAGE_NAME, dist_index=(library / "dist" / "index").resolve().as_posix()
    )
    project = workdir / "candidate_project"
    assert (project / "tsconfig.json").read_text(encoding="utf-8") == expected
    assert (project / "candidate.ts").read_text(encoding="utf-8") == PACKAGE_CANDIDATE
    assert toolchain.calls == [
        (
            ["node", str(library / "node_modules" / "typescript" / "lib" / "tsc.js"), "-p", "tsconfig.json"],
            project,
        )
    ]
    assert result.verified is True


def test_verify_without_package_json_keeps_the_dist_index_path(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()

    ev.verify_typescript_example(
        _candidate(PACKAGE_CANDIDATE), library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "w"
    )

    text = (tmp_path / "w" / "candidate_project" / "tsconfig.json").read_text(encoding="utf-8")
    assert f'"{(library / "dist" / "index").resolve().as_posix()}"' in text


@pytest.mark.parametrize(
    ("layout", "entry"), [("dist-src", "dist/src/index"), ("3d", "dist/index"), ("cells", "index")]
)
def test_verify_maps_the_package_to_each_librarys_own_entry(
    toolchain: FakeToolchain, tmp_path: Path, layout: str, entry: str
) -> None:
    library = tmp_path / "library"
    _write_tree(library, LAYOUTS[layout]["package"], LAYOUTS[layout]["files"])

    ev.verify_typescript_example(
        _candidate(PACKAGE_CANDIDATE), library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "w"
    )

    config = json.loads((tmp_path / "w" / "candidate_project" / "tsconfig.json").read_text(encoding="utf-8"))
    assert config["compilerOptions"]["paths"] == {PACKAGE_NAME: [(library / entry).resolve().as_posix()]}


@pytest.mark.parametrize(("returncode", "verified"), [(0, True), (2, False)])
def test_verify_verdict_is_the_tsc_exit_code(
    toolchain: FakeToolchain, tmp_path: Path, returncode: int, verified: bool
) -> None:
    toolchain.responder = lambda args, cwd: _completed(args, returncode, stdout="out", stderr="TS2339")
    library = tmp_path / "library"
    library.mkdir()

    result = ev.verify_typescript_example(
        _candidate(PACKAGE_CANDIDATE), library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "w"
    )

    assert result.verified is verified
    assert "TS2339" in result.output


# --- relative-import detection ----------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        'import { Workbook } from "./aspose_cells";',
        "import { Workbook } from '../aspose_cells';",
        'import * as threed from "./dist/aspose/threed";',
        'import "./side-effect";',
        'export * from "./other";',
        'export { A } from "../a";',
        'import {\n  Scene,\n  Mesh,\n} from "./dist/aspose/threed";\nconst s = new Scene();',
        'import a from "pkg"; import b from "./local";',
        'import type { T } from "./types";',
    ],
)
def test_relative_specifier_detection_true(code: str) -> None:
    assert ev._has_relative_module_specifier(code) is True


@pytest.mark.parametrize(
    "code",
    [
        f'import {{ Thing }} from "{PACKAGE_NAME}";',
        'import fs from "node:fs";\nconst x = "./not-an-import";',
        'const m = await import("./dynamic");',
        'export const path = "./value";',
        "const a = 1;",
    ],
)
def test_relative_specifier_detection_false(code: str) -> None:
    assert ev._has_relative_module_specifier(code) is False


# --- verify_typescript_example: repository-root mode ----------------------------------------


CELLS_CANDIDATE = 'import { Workbook } from "./aspose_cells";\nconst wb = new Workbook();\nconsole.log(wb);\n'


def test_cells_relative_import_runs_in_repository_root_mode(toolchain: FakeToolchain, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _write_tree(library, LAYOUTS["cells"]["package"], LAYOUTS["cells"]["files"])
    seen: dict[str, object] = {}

    def respond(args: list[str], cwd: Path) -> object:
        seen["config"] = json.loads((library / "__foss_mcp_tsconfig__.json").read_text(encoding="utf-8"))
        seen["candidate"] = (library / "__foss_mcp_candidate__.ts").read_text(encoding="utf-8")
        return _completed(args)

    toolchain.responder = respond
    workdir = tmp_path / "work"

    result = ev.verify_typescript_example(
        _candidate(CELLS_CANDIDATE), library_dir=library, package_name=PACKAGE_NAME, workdir=workdir
    )

    config = seen["config"]
    assert config["extends"] == "./tsconfig.json"
    assert config["files"] == ["__foss_mcp_candidate__.ts"]
    assert config["compilerOptions"]["noEmit"] is True
    assert config["compilerOptions"]["rootDir"] == "."
    assert config["compilerOptions"]["noUnusedLocals"] is False
    assert config["compilerOptions"]["noUnusedParameters"] is False
    assert config["compilerOptions"]["skipLibCheck"] is True
    assert config["include"] == []
    assert seen["candidate"] == CELLS_CANDIDATE
    assert toolchain.calls == [
        (
            [
                "node",
                str(library / "node_modules" / "typescript" / "lib" / "tsc.js"),
                "-p",
                "__foss_mcp_tsconfig__.json",
            ],
            library,
        )
    ]
    assert result.verified is True
    assert not (library / "__foss_mcp_candidate__.ts").exists()
    assert not (library / "__foss_mcp_tsconfig__.json").exists()
    # The candidate is also kept in the workdir for the record, and no paths-mode tsconfig is written.
    assert (workdir / "candidate_project" / "candidate.ts").read_text(encoding="utf-8") == CELLS_CANDIDATE
    assert not (workdir / "candidate_project" / "tsconfig.json").exists()


def test_root_mode_removes_both_files_when_tsc_fails(toolchain: FakeToolchain, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _write_tree(library, LAYOUTS["cells"]["package"], LAYOUTS["cells"]["files"])
    toolchain.responder = lambda args, cwd: _completed(args, 2, stdout="aspose_cells.ts(1,1): error TS2305")

    result = ev.verify_typescript_example(
        _candidate(CELLS_CANDIDATE), library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "w"
    )

    assert result.verified is False
    assert "TS2305" in result.output
    assert not (library / "__foss_mcp_candidate__.ts").exists()
    assert not (library / "__foss_mcp_tsconfig__.json").exists()


def test_root_mode_removes_both_files_when_the_run_raises(toolchain: FakeToolchain, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _write_tree(library, LAYOUTS["cells"]["package"], LAYOUTS["cells"]["files"])

    def explode(args: list[str], cwd: Path) -> object:
        raise OSError("node vanished")

    toolchain.responder = explode

    with pytest.raises(OSError, match="node vanished"):
        ev.verify_typescript_example(
            _candidate(CELLS_CANDIDATE),
            library_dir=library,
            package_name=PACKAGE_NAME,
            workdir=tmp_path / "w",
        )
    assert not (library / "__foss_mcp_candidate__.ts").exists()
    assert not (library / "__foss_mcp_tsconfig__.json").exists()


def test_root_mode_without_a_library_tsconfig_does_not_extend_and_sets_its_own_options(
    toolchain: FakeToolchain, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    _write_tree(library, {"main": "index.ts"}, ["index.ts"])
    seen: dict[str, object] = {}

    def respond(args: list[str], cwd: Path) -> object:
        seen["config"] = json.loads((library / "__foss_mcp_tsconfig__.json").read_text(encoding="utf-8"))
        return _completed(args)

    toolchain.responder = respond

    ev.verify_typescript_example(
        _candidate(CELLS_CANDIDATE), library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "w"
    )

    options = seen["config"]["compilerOptions"]
    assert "extends" not in seen["config"]
    assert options["moduleResolution"] == "bundler"
    assert options["strict"] is True
    assert options["noEmit"] is True


def test_root_mode_leaves_other_library_files_alone(toolchain: FakeToolchain, tmp_path: Path) -> None:
    library = tmp_path / "library"
    _write_tree(library, LAYOUTS["cells"]["package"], LAYOUTS["cells"]["files"])

    ev.verify_typescript_example(
        _candidate(CELLS_CANDIDATE), library_dir=library, package_name=PACKAGE_NAME, workdir=tmp_path / "w"
    )

    assert sorted(p.name for p in library.iterdir()) == ["index.ts", "package.json", "tsconfig.json"]


def test_module_exports_the_new_names() -> None:
    assert "prepare_nodejs_library" in ev.__all__
    assert "verify_nodejs_example" in ev.__all__
    assert "prepare_typescript_library" in ev.__all__
