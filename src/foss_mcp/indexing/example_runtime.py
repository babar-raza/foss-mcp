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
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

__all__ = [
    "GO_BINARY_STEM",
    "OUTPUT_OPTIONAL_PLATFORMS",
    "go_binary_path",
    "go_build_command",
    "go_run_command",
    "build_go_run_binary",
]

# Platforms whose programs signal success by their exit code alone: a self-checking program prints nothing when
# every check holds. An exit-0 run that wrote nothing is recorded (``Runtime-Produced-Bytes: 0`` is not written,
# the level is ``ran-exit0``) but is NOT demoted to ``no_output``: that demotion stays java/dotnet's, where an
# empty artefact is the defect it was built to catch (3d/java wrote a 35-byte STL with no facet).
OUTPUT_OPTIONAL_PLATFORMS = frozenset({"go"})

GO_BINARY_STEM = "candidate_run"


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
