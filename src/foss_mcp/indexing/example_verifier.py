"""Real, isolated compile/execute-verification sandbox for candidate
examples (REQ-G2-048).

This module never trusts a candidate example's plausibility. It clones the
exact pinned upstream commit already indexed, builds (or, for Python,
installs) the reference library once, then compiles or runs each
candidate's real code in a throwaway, disposable project/environment that
references that library. The verdict is the real toolchain's exit code,
nothing else.

Originally proven for pdf/net's .NET toolchain
(``prepare_reference_library``/``verify_dotnet_example``, untouched below).
Extended here to three more pilots with the same git-fetch plumbing and the
same prepare/verify split: Python (slides/python), Rust (cells/rust), and
Go (pdf/go).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from foss_mcp.indexing.example_candidates import CandidateExample

__all__ = [
    "VerificationResult",
    "prepare_reference_library",
    "verify_dotnet_example",
    "prepare_python_library",
    "verify_python_example",
    "prepare_rust_library",
    "verify_rust_example",
    "prepare_go_library",
    "verify_go_example",
]

_OUTPUT_TRUNCATE_CHARS = 8000

_CANDIDATE_CSPROJ_TEMPLATE = """<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>

  <ItemGroup>
    <ProjectReference Include="{library_csproj}" />
  </ItemGroup>

</Project>
"""

_CANDIDATE_CARGO_TOML_TEMPLATE = """[package]
name = "candidate"
version = "0.0.0"
edition = "2021"

[dependencies]
{crate_name} = {{ path = "{library_dir}" }}
"""

_CANDIDATE_GO_MOD_TEMPLATE = """module candidate

go 1.24

require {module_path} v0.0.0

replace {module_path} => {library_dir}
"""


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _truncate(text: str) -> str:
    if len(text) <= _OUTPUT_TRUNCATE_CHARS:
        return text
    return text[:_OUTPUT_TRUNCATE_CHARS] + "\n...[truncated]"


@dataclass(frozen=True)
class VerificationResult:
    """The real, compiler-determined outcome of verifying one candidate."""

    candidate: CandidateExample
    verified: bool
    output: str


def prepare_reference_library(
    repository: str,
    commit: str,
    csproj_relative_path: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    build the reference library once, and return the built csproj's path.

    A plain shallow clone only gets the default branch tip, so the specific
    historical commit is fetched explicitly: ``git init`` + ``git remote
    add`` + ``git fetch --depth 1 origin <commit>`` + ``git checkout
    <commit>``.

    Raises ``RuntimeError`` on any real failure (clone, fetch, checkout, or
    build) - the reference library itself must always build clean, and a
    failure here is a real, reportable defect, never silently swallowed.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    repo_url = f"https://github.com/{repository}.git"

    init_result = _run(["git", "init"], cwd=workdir)
    if init_result.returncode != 0:
        raise RuntimeError(f"git init failed in {workdir}:\n{init_result.stdout}\n{init_result.stderr}")

    remote_result = _run(["git", "remote", "add", "origin", repo_url], cwd=workdir)
    if remote_result.returncode != 0:
        raise RuntimeError(
            f"git remote add failed for {repo_url}:\n{remote_result.stdout}\n{remote_result.stderr}"
        )

    fetch_result = _run(["git", "fetch", "--depth", "1", "origin", commit], cwd=workdir)
    if fetch_result.returncode != 0:
        raise RuntimeError(
            f"git fetch of commit {commit} from {repo_url} failed:\n"
            f"{fetch_result.stdout}\n{fetch_result.stderr}"
        )

    checkout_result = _run(["git", "checkout", commit], cwd=workdir)
    if checkout_result.returncode != 0:
        raise RuntimeError(
            f"git checkout of commit {commit} failed:\n{checkout_result.stdout}\n{checkout_result.stderr}"
        )

    csproj_path = workdir / csproj_relative_path
    build_result = _run(["dotnet", "build", str(csproj_path)], cwd=workdir)
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference library at {repository}@{commit} "
            f"({csproj_relative_path}) failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    return csproj_path


def verify_dotnet_example(
    candidate: CandidateExample,
    *,
    library_csproj: Path,
    workdir: Path,
) -> VerificationResult:
    """Compile ``candidate.code`` as top-level statements in a throwaway
    console project referencing ``library_csproj``, and report the real
    compiler outcome.

    The literal comparison determining ``verified`` is intentionally exact
    (``verified = result.returncode == 0``) so that forcing it to always be
    true is a detectable, meaningful negative control.
    """
    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    csproj_content = _CANDIDATE_CSPROJ_TEMPLATE.format(library_csproj=library_csproj)
    (project_dir / "candidate.csproj").write_text(csproj_content, encoding="utf-8")
    (project_dir / "Program.cs").write_text(candidate.code, encoding="utf-8")

    result = _run(["dotnet", "build"], cwd=project_dir)

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)


def _clone_pinned_commit(repository: str, commit: str, workdir: Path) -> None:
    """Shallow-fetch exactly one pinned commit of ``repository`` into
    ``workdir``.

    Mirrors ``prepare_reference_library``'s own git plumbing exactly
    (``git init`` + ``git remote add`` + ``git fetch --depth 1`` + ``git
    checkout``), factored out so the three new pilots below share one
    real, tested clone path instead of three subtly-diverging copies.
    ``prepare_reference_library`` itself is untouched and does not call
    this helper.

    Raises ``RuntimeError`` on any real failure (init, remote add, fetch,
    or checkout).
    """
    workdir.mkdir(parents=True, exist_ok=True)
    repo_url = f"https://github.com/{repository}.git"

    init_result = _run(["git", "init"], cwd=workdir)
    if init_result.returncode != 0:
        raise RuntimeError(f"git init failed in {workdir}:\n{init_result.stdout}\n{init_result.stderr}")

    remote_result = _run(["git", "remote", "add", "origin", repo_url], cwd=workdir)
    if remote_result.returncode != 0:
        raise RuntimeError(
            f"git remote add failed for {repo_url}:\n{remote_result.stdout}\n{remote_result.stderr}"
        )

    fetch_result = _run(["git", "fetch", "--depth", "1", "origin", commit], cwd=workdir)
    if fetch_result.returncode != 0:
        raise RuntimeError(
            f"git fetch of commit {commit} from {repo_url} failed:\n"
            f"{fetch_result.stdout}\n{fetch_result.stderr}"
        )

    checkout_result = _run(["git", "checkout", commit], cwd=workdir)
    if checkout_result.returncode != 0:
        raise RuntimeError(
            f"git checkout of commit {commit} failed:\n{checkout_result.stdout}\n{checkout_result.stderr}"
        )


def _venv_python_executable(venv_dir: Path) -> Path:
    """Return the real python executable inside ``venv_dir``, on whichever
    of the two standard venv layouts the current platform actually used
    (``Scripts/python.exe`` on Windows, ``bin/python`` elsewhere).
    """
    windows_python = venv_dir / "Scripts" / "python.exe"
    if windows_python.exists():
        return windows_python
    return venv_dir / "bin" / "python"


def prepare_python_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    create a fresh venv alongside it, and ``pip install -e`` the real
    library into that venv once. Returns the venv's own python executable,
    for ``verify_python_example`` to run candidates against.

    Python has no separate compile step, so this is the Python pilot's
    equivalent of ``prepare_reference_library`` building the reference
    library once: a real, disposable environment that genuinely has the
    real library installed and importable, never assumed.

    Raises ``RuntimeError`` on any real failure (clone, checkout, venv
    creation, or install).
    """
    repo_dir = workdir / "repo"
    _clone_pinned_commit(repository, commit, repo_dir)

    venv_dir = workdir / "venv"
    venv_result = _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=workdir)
    if venv_result.returncode != 0:
        raise RuntimeError(
            f"python -m venv failed for {venv_dir}:\n{venv_result.stdout}\n{venv_result.stderr}"
        )

    venv_python = _venv_python_executable(venv_dir)

    install_result = _run(
        [str(venv_python), "-m", "pip", "install", "-e", str(repo_dir)],
        cwd=workdir,
    )
    if install_result.returncode != 0:
        raise RuntimeError(
            f"pip install -e {repo_dir} into venv {venv_dir} failed:\n"
            f"{install_result.stdout}\n{install_result.stderr}"
        )

    return venv_python


def verify_python_example(
    candidate: CandidateExample,
    *,
    venv_python: Path,
    workdir: Path,
) -> VerificationResult:
    """Run ``candidate.code`` as a real, disposable script with
    ``venv_python`` (a venv that already has the reference library
    installed), and report the real interpreter outcome.

    Python has no separate compile step: the candidate is actually
    executed in a real, isolated subprocess, and a real exception during
    that execution means ``verified=False``, exactly like a real compiler
    error for the other languages. ``verified`` is the literal exit-code
    comparison (``verified = result.returncode == 0``), so forcing it to
    always be true is a detectable, meaningful negative control.
    """
    script_dir = workdir / "candidate_script"
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "candidate.py"
    script_path.write_text(candidate.code, encoding="utf-8")

    result = _run([str(venv_python), str(script_path)], cwd=script_dir)

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)


def prepare_rust_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``
    and build the reference crate once with ``cargo build``, proving the
    real library itself builds clean. Returns ``workdir`` - the cloned
    crate's own root, where its ``Cargo.toml`` lives - for
    ``verify_rust_example`` to depend on as a Cargo path dependency.

    Raises ``RuntimeError`` on any real failure (clone, checkout, or
    build).
    """
    _clone_pinned_commit(repository, commit, workdir)

    build_result = _run(["cargo", "build"], cwd=workdir)
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference crate at {repository}@{commit} failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    return workdir


def verify_rust_example(
    candidate: CandidateExample,
    *,
    library_crate_name: str,
    library_dir: Path,
    workdir: Path,
) -> VerificationResult:
    """Build ``candidate.code`` as ``src/main.rs`` in a throwaway crate
    that depends on ``library_dir`` (the built reference crate) via a
    Cargo path dependency named ``library_crate_name``, and report the
    real ``cargo build`` outcome.

    The literal comparison determining ``verified`` is intentionally exact
    (``verified = result.returncode == 0``), matching ``verify_dotnet_example``.
    """
    project_dir = workdir / "candidate_project"
    (project_dir / "src").mkdir(parents=True, exist_ok=True)

    cargo_toml_content = _CANDIDATE_CARGO_TOML_TEMPLATE.format(
        crate_name=library_crate_name,
        library_dir=library_dir.resolve().as_posix(),
    )
    (project_dir / "Cargo.toml").write_text(cargo_toml_content, encoding="utf-8")
    (project_dir / "src" / "main.rs").write_text(candidate.code, encoding="utf-8")

    result = _run(["cargo", "build"], cwd=project_dir)

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)


def prepare_go_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``
    and confirm the reference module builds clean with ``go build ./...``.
    Returns ``workdir`` - the cloned module's own root, where its
    ``go.mod`` lives - for ``verify_go_example`` to ``replace`` in a
    throwaway module.

    Go modules resolve directly with no separate restore/build-once step
    the way .NET/Rust need, but this keeps the same two-function shape as
    the other pilots for consistency.

    Raises ``RuntimeError`` on any real failure (clone, checkout, or
    build).
    """
    _clone_pinned_commit(repository, commit, workdir)

    build_result = _run(["go", "build", "./..."], cwd=workdir)
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference module at {repository}@{commit} failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    return workdir


def verify_go_example(
    candidate: CandidateExample,
    *,
    module_path: str,
    library_dir: Path,
    workdir: Path,
) -> VerificationResult:
    """Build ``candidate.code`` as ``main.go`` in a throwaway module that
    ``replace``s ``module_path`` with ``library_dir`` (the cloned
    reference module), and report the real ``go build`` outcome.

    The literal comparison determining ``verified`` is intentionally exact
    (``verified = result.returncode == 0``), matching ``verify_dotnet_example``.
    """
    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    go_mod_content = _CANDIDATE_GO_MOD_TEMPLATE.format(
        module_path=module_path,
        library_dir=library_dir.resolve().as_posix(),
    )
    (project_dir / "go.mod").write_text(go_mod_content, encoding="utf-8")
    (project_dir / "main.go").write_text(candidate.code, encoding="utf-8")

    result = _run(["go", "build", "./..."], cwd=project_dir)

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)
