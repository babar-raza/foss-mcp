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
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

__all__ = [
    "GO_BINARY_STEM",
    "OUTPUT_OPTIONAL_PLATFORMS",
    "RUST_BINARY_STEM",
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
OUTPUT_OPTIONAL_PLATFORMS = frozenset({"go", "rust"})

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
