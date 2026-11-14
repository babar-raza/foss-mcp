"""Per-language run steps for the example verifier (TC-805, REQ-G3-014).

``example_verifier.runtime_command`` / ``run_compiled_candidate`` own the generic machinery (the sandbox, the
``RuntimeRecord``, the classification); the code that is specific to ONE language's run step lives here, so a
new language does not grow the 3,900-line verifier. This module imports nothing from the verifier: it only builds
argument vectors and reads the outcome a caller's runner returns, which keeps every function here testable with a
fake runner and no toolchain.

Go. ``verify_go_example`` assembles ``main.go`` in ``<workdir>/candidate_project`` and runs ``go build`` and
``go vet``; it never started the program, so the self-checking examples (``log.Fatal`` / ``panic`` on a failed
check) were reported as verified because they compiled. The run step builds the program into a binary
(``go build -o``) and starts THAT in the sandbox directory, rather than ``go run .``: ``go run`` only works with
the module directory as its working directory, and the sandbox working directory is where the input fixtures are
staged and where a written file is looked for.

Rust (TC-806). ``verify_rust_example`` assembles ``src/main.rs`` in ``<workdir>/candidate_project`` and runs
``cargo build``; the binary was never started, so a self-checking program (``assert!`` / ``panic!`` /
``std::process::exit(1)``) was reported as verified because it compiled. The run step re-runs ``cargo build``
(a no-op out of the target directory the verifier just filled) and starts the resulting binary in the sandbox
directory, not ``cargo run``: ``cargo run`` executes in the caller's working directory, and the sandbox working
directory is where the input fixtures are staged and where a written file is looked for. A Rust panic exits 101
and prints ``thread 'main' panicked at <file>:<l>:<c>:`` with the message on the NEXT line; ``panic_detail_line``
lets the recorded error carry both.

TypeScript (TC-807). ``verify_typescript_example`` only type-checks (``tsc --noEmit``); the authored examples are
self-checking programs (``throw``), so "Verification: passed" meant "it compiles". The run step EMITS the
candidate with the library's own already-installed ``tsc`` and starts the JavaScript with ``node``, in the sandbox
directory (where input fixtures are staged and written files are looked for). Two layouts, the two the verifier
has: a candidate that imports the package by name is emitted as ESM into ``<project>/ts_run/out`` with the package
linked into that directory's ``node_modules`` (``link_package``, the verifier's existing package link); a candidate
that imports by relative path from the repository root (cells, 3d) is emitted IN PLACE in the library directory,
as CommonJS, because the modules it names live there, and every file the emit wrote is removed after the run
(``TypeScriptBuild.emitted``). The emit's own exit code is not a verdict - a library that does not type-check
cleanly still emits - so success is the entry file existing.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = [
    "GO_BINARY_STEM",
    "OUTPUT_OPTIONAL_PLATFORMS",
    "OUTPUT_DIRECTORY_PLATFORMS",
    "RUST_BINARY_STEM",
    "TYPESCRIPT_ROOT_ENTRY",
    "TypeScriptBuild",
    "typescript_run_entry",
    "typescript_run_command",
    "build_typescript_run_files",
    "remove_emitted_files",
    "ensure_output_directories",
    "node_error_line",
    "go_binary_path",
    "go_build_command",
    "go_run_command",
    "build_go_run_binary",
    "rust_binary_path",
    "rust_build_command",
    "rust_run_command",
    "build_rust_run_binary",
    "panic_detail_line",
]

# Platforms whose programs signal success by their exit code alone: a self-checking program prints nothing when
# every check holds. An exit-0 run that wrote nothing is recorded (``Runtime-Produced-Bytes: 0`` is not written,
# the level is ``ran-exit0``) but is NOT demoted to ``no_output``: that demotion stays java/dotnet's, where an
# empty artefact is the defect it was built to catch (3d/java wrote a 35-byte STL with no facet).
OUTPUT_OPTIONAL_PLATFORMS = frozenset({"go", "rust", "typescript"})
# Platforms whose examples write to a relative ``dir/file.ext`` path without creating ``dir`` (the authored
# TypeScript examples save to ``outputfiles/<name>``): the sandbox gets those directories, nothing else.
OUTPUT_DIRECTORY_PLATFORMS = frozenset({"typescript"})

GO_BINARY_STEM = "candidate_run"
# The verifier's throwaway crate is named ``candidate`` (``_CANDIDATE_CARGO_TOML_TEMPLATE``): its binary is too.
RUST_BINARY_STEM = "candidate"


class _Outcome(Protocol):
    @property
    def exit_code(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


Runner = Callable[[Sequence[str], Path, float], _Outcome]


def go_binary_path(project_dir: Path) -> Path:
    """Where the built candidate program is written: next to its ``main.go``, ``.exe`` on Windows."""
    return project_dir / (GO_BINARY_STEM + (".exe" if os.name == "nt" else ""))


def go_build_command(project_dir: Path) -> list[str]:
    """The argv, run with ``project_dir`` as working directory, that links the candidate into its binary."""
    return ["go", "build", "-o", str(go_binary_path(project_dir)), "."]


def go_run_command(project_dir: Path) -> list[str]:
    """The argv that starts the built candidate (its own binary; the caller supplies the working directory)."""
    return [str(go_binary_path(project_dir))]


def build_go_run_binary(project_dir: Path, *, runner: Runner, timeout_seconds: float) -> str:
    """Build the candidate's binary through ``runner`` and return ``""`` on success, else why it did not build.

    The candidate was just built by ``verify_go_example`` (same module, same ``go.sum``), so this is a link step
    out of the build cache. A failure here is the machine's (a full disk, a killed process), not the example's.
    """
    try:
        outcome = runner(go_build_command(project_dir), project_dir, timeout_seconds)
    except OSError as exc:
        return f"could not start go build: {exc}"
    if outcome.exit_code != 0:
        text = " ".join(f"{outcome.stderr} {outcome.stdout}".split())
        return f"go build -o exited {outcome.exit_code}: {text}"
    return ""


def rust_binary_path(project_dir: Path) -> Path:
    """Where the built candidate crate's binary lives: ``<target dir>/debug/candidate``, ``.exe`` on Windows.

    The target directory is the crate's own ``target`` unless ``CARGO_TARGET_DIR`` moves it (the verifier's
    cargo inherits that variable, so the binary is wherever it put it).
    """
    target = project_dir / (os.environ.get("CARGO_TARGET_DIR") or "target")
    return target / "debug" / (RUST_BINARY_STEM + (".exe" if os.name == "nt" else ""))


def rust_build_command(project_dir: Path) -> list[str]:
    """The argv, run with ``project_dir`` as working directory, that makes sure the candidate's binary exists.

    ``--offline``: the verifier's own ``cargo build`` just resolved and fetched everything, so a network
    failure here is never the example's.
    """
    return ["cargo", "build", "--quiet", "--offline"]


def rust_run_command(project_dir: Path) -> list[str]:
    """The argv that starts the built candidate (its own binary; the caller supplies the working directory)."""
    return [str(rust_binary_path(project_dir))]


def build_rust_run_binary(project_dir: Path, *, runner: Runner, timeout_seconds: float) -> str:
    """Make sure the candidate's binary exists, through ``runner``: ``""`` on success, else why it does not.

    The candidate was just built by ``verify_rust_example`` (same crate, same ``Cargo.lock``), so this is a
    no-op out of cargo's own fingerprints. A failure here is the machine's (a full disk, a killed process),
    not the example's; a timeout (exit 124) lands here too and is environmental like any other.
    """
    try:
        outcome = runner(rust_build_command(project_dir), project_dir, timeout_seconds)
    except OSError as exc:
        return f"could not start cargo build: {exc}"
    if outcome.exit_code != 0:
        text = " ".join(f"{outcome.stderr} {outcome.stdout}".split())
        return f"cargo build exited {outcome.exit_code}: {text}"
    return ""


def panic_detail_line(lines: Sequence[str], index: int) -> str:
    """The line after a Rust ``thread '...' panicked at <where>:`` line, which holds the panic message.

    ``lines`` are the stripped non-empty output lines and ``index`` the panic line's position. Returns ``""``
    when the line is not a panic header, is the last one, or the next line is rustc's ``note:`` boilerplate.
    """
    if "panicked at" not in lines[index] or index + 1 >= len(lines):
        return ""
    following = lines[index + 1]
    return "" if following.startswith("note:") else following


# -- TypeScript (TC-807) ----------------------------------------------------------------------------------

_TS_ROOT_CANDIDATE_STEM = "__foss_mcp_run_candidate__"
TYPESCRIPT_ROOT_ENTRY = _TS_ROOT_CANDIDATE_STEM + ".js"
_TS_ROOT_TSCONFIG = "__foss_mcp_run_tsconfig__.json"
_TS_RUN_DIRNAME = "ts_run"
_TS_PATHS_ENTRY = "candidate.js"
_TSFILE_RE = re.compile(r"^TSFILE: (?P<path>.+)$", re.MULTILINE)
# A relative ``dir/file.ext`` string literal: the file's directory is where a program writes (or reads) it.
# ``./x``, ``../x``, ``@scope/pkg``, URLs and template strings never match (a segment starts with a letter,
# a digit or an underscore, and the literal holds no ``:``, ``$`` or ``{``).
_RELATIVE_FILE_LITERAL_RE = re.compile(
    r"""["'`]((?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)+[A-Za-z0-9_][A-Za-z0-9_.-]*\.[A-Za-z0-9]{1,8})["'`]"""
)

# The line node prints for an uncaught error: ``Error: msg``, ``TypeError: msg``, ``Error [ERR_X]: msg``. The
# lines above it are the source line and a caret, which can mention ``Exception`` without saying what failed.
_NODE_ERROR_LINE_RE = re.compile(r"^(?:[A-Za-z_$][\w$]*)?(?:Error|Exception)(?: \[[A-Z0-9_]+\])?: .+$")

LinkPackage = Callable[[Path, Path, str], None]


@dataclass(frozen=True)
class TypeScriptBuild:
    """The outcome of emitting a candidate: ``error`` is empty on success; ``emitted`` are the files an in-place
    emit wrote (a caller removes them with ``remove_emitted_files`` once the run is over)."""

    error: str = ""
    emitted: tuple[Path, ...] = ()


def typescript_run_entry(project_dir: Path, library_dir: Path, *, relative_imports: bool) -> Path:
    """Where the emitted candidate's JavaScript lives: next to the library for a candidate that imports by
    relative path from the repository root, else in the candidate project's own ``ts_run/out`` directory."""
    if relative_imports:
        return library_dir / TYPESCRIPT_ROOT_ENTRY
    return project_dir / _TS_RUN_DIRNAME / "out" / _TS_PATHS_ENTRY


def typescript_run_command(project_dir: Path, library_dir: Path, *, relative_imports: bool) -> list[str]:
    """The argv that starts the emitted candidate (the caller supplies the working directory)."""
    return ["node", str(typescript_run_entry(project_dir, library_dir, relative_imports=relative_imports))]


def _library_is_esm(library_dir: Path) -> bool:
    try:
        package = json.loads((library_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(package, dict) and package.get("type") == "module"


def _tsc_failure(outcome: _Outcome) -> str:
    text = " ".join(f"{outcome.stdout} {outcome.stderr}".split())
    return f"tsc emit exited {outcome.exit_code} and wrote no JavaScript: {text[:300]}"


def _emitted_paths(stdout: str, library_dir: Path) -> tuple[Path, ...]:
    """The files ``tsc --listEmittedFiles`` says it wrote, restricted to the library directory."""
    root = library_dir.resolve()
    found: list[Path] = []
    for match in _TSFILE_RE.finditer(stdout):
        path = Path(match.group("path").strip())
        if not path.is_absolute():
            path = library_dir / path
        if root in path.resolve().parents:
            found.append(path)
    return tuple(found)


def remove_emitted_files(paths: Iterable[Path]) -> None:
    """Remove the files an in-place emit wrote; a file that is already gone is fine."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _emit_by_package_import(
    project_dir: Path,
    library_dir: Path,
    package_name: str,
    *,
    runner: Runner,
    timeout_seconds: float,
    link_package: LinkPackage,
) -> TypeScriptBuild:
    """Emit ``candidate.ts`` with the verifier's own paths-mode tsconfig (``--noEmit false``) as ESM into
    ``ts_run/out`` and link the package into that directory, so ``node`` resolves the import by name."""
    if not (project_dir / "tsconfig.json").is_file():
        return TypeScriptBuild(error="the candidate project has no tsconfig.json to emit from")
    out_dir = project_dir / _TS_RUN_DIRNAME / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "package.json").write_text(json.dumps({"type": "module"}) + "\n", encoding="utf-8")
    entry = out_dir / _TS_PATHS_ENTRY
    entry.unlink(missing_ok=True)
    tsc = library_dir / "node_modules" / "typescript" / "lib" / "tsc.js"
    argv = ["node", str(tsc), "-p", "tsconfig.json", "--noEmit", "false", "--outDir", str(out_dir)]
    try:
        outcome = runner(argv, project_dir, timeout_seconds)
    except OSError as exc:
        return TypeScriptBuild(error=f"could not start tsc: {exc}")
    if not entry.is_file():
        return TypeScriptBuild(error=_tsc_failure(outcome))
    if package_name:
        try:
            link_package(out_dir, library_dir, package_name)
        except OSError as exc:
            return TypeScriptBuild(error=f"could not link {package_name} for the run: {exc}")
    return TypeScriptBuild()


def _emit_in_library(
    library_dir: Path, code: str, *, runner: Runner, timeout_seconds: float
) -> TypeScriptBuild:
    """Emit a candidate that imports by relative path from the repository root, next to the library's own
    modules (the paths it names live there): the candidate and its tsconfig are written into ``library_dir``
    exactly like the verifier's own repository-root check, and the config EXTENDS the library's tsconfig."""
    candidate_file = library_dir / (_TS_ROOT_CANDIDATE_STEM + ".ts")
    config_file = library_dir / _TS_ROOT_TSCONFIG
    esm = _library_is_esm(library_dir)
    options: dict[str, object] = {
        "noEmit": False,
        "outDir": ".",
        "rootDir": ".",
        "module": "esnext" if esm else "commonjs",
        "moduleResolution": "bundler" if esm else "node10",
        "skipLibCheck": True,
        "noUnusedLocals": False,
        "noUnusedParameters": False,
        "declaration": False,
        "declarationMap": False,
        "sourceMap": False,
        "incremental": False,
        "composite": False,
    }
    config: dict[str, object] = {}
    if (library_dir / "tsconfig.json").is_file():
        config["extends"] = "./tsconfig.json"
    else:
        options.update({"target": "ES2022", "strict": True, "esModuleInterop": True})
    config["compilerOptions"] = options
    config["files"] = [candidate_file.name]
    # The library's own tsconfig may include its whole tree; only the candidate (and what it imports) is emitted.
    config["include"] = []
    entry = library_dir / TYPESCRIPT_ROOT_ENTRY
    tsc = library_dir / "node_modules" / "typescript" / "lib" / "tsc.js"
    try:
        candidate_file.write_text(code, encoding="utf-8")
        config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        entry.unlink(missing_ok=True)
        try:
            outcome = runner(
                ["node", str(tsc), "-p", config_file.name, "--listEmittedFiles"], library_dir, timeout_seconds
            )
        except OSError as exc:
            return TypeScriptBuild(error=f"could not start tsc: {exc}")
        emitted = (*_emitted_paths(outcome.stdout, library_dir), entry)
        if not entry.is_file():
            remove_emitted_files(emitted)
            return TypeScriptBuild(error=_tsc_failure(outcome))
        return TypeScriptBuild(emitted=emitted)
    finally:
        candidate_file.unlink(missing_ok=True)
        config_file.unlink(missing_ok=True)


def build_typescript_run_files(
    project_dir: Path,
    library_dir: Path,
    code: str,
    *,
    package_name: str,
    relative_imports: bool,
    runner: Runner,
    timeout_seconds: float,
    link_package: LinkPackage,
) -> TypeScriptBuild:
    """Emit the type-checked candidate to JavaScript through ``runner``, so ``typescript_run_command`` can start it.

    ``relative_imports`` is the verifier's own repository-root-mode test (a static import or export-from with a
    ``./`` or ``../`` specifier). A failure here is the machine's (the candidate type-checked a moment ago), so the
    caller records it as a run that could not be attempted, never as the example failing.
    """
    if relative_imports:
        return _emit_in_library(library_dir, code, runner=runner, timeout_seconds=timeout_seconds)
    return _emit_by_package_import(
        project_dir,
        library_dir,
        package_name,
        runner=runner,
        timeout_seconds=timeout_seconds,
        link_package=link_package,
    )


def ensure_output_directories(code: str, platform: str, run_dir: Path) -> None:
    """Create the directories of the relative ``dir/file.ext`` literals ``code`` names, under ``run_dir``.

    A program that saves to ``outputfiles/report.pdf`` assumes ``outputfiles`` exists (the authors ran their
    examples in a directory that had it); in an empty sandbox the save fails with ENOENT, which says nothing
    about the example. Only directories are created, never files. Only for ``OUTPUT_DIRECTORY_PLATFORMS``.
    """
    if platform not in OUTPUT_DIRECTORY_PLATFORMS:
        return
    for match in _RELATIVE_FILE_LITERAL_RE.finditer(code):
        (run_dir / match.group(1).rsplit("/", 1)[0]).mkdir(parents=True, exist_ok=True)


def node_error_line(*streams: str) -> str:
    """The first ``Error: message`` line node printed in ``streams`` (stderr first), whitespace collapsed; ``""``
    when there is none (the caller falls back to the generic first-error-looking line)."""
    for stream in streams:
        for raw in stream.splitlines():
            line = raw.strip()
            if _NODE_ERROR_LINE_RE.match(line):
                return " ".join(line.split())
    return ""
