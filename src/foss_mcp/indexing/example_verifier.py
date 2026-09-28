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
Extended to three more pilots with the same git-fetch plumbing and the same
prepare/verify split: Python (slides/python), Rust (cells/rust), and Go
(pdf/go). Extended again here to the last two pilots with a real extraction
fixture: Java (pdf/java) and TypeScript (pdf/typescript) - completing 6 of 7.
Extended a final time to the 7th and last pilot, C++ (cells/cpp) - the only
pilot with no prior verifier at all, since it needs a real C/C++ compiler
toolchain rather than a language-native package manager. A sibling project
on this same machine (repository-presenter) already installed and verified
a working MSVC toolchain for this exact repository
(``C:\\tools\\rp-toolchains\\TOOLCHAIN_PATHS.txt``); GCC and Clang (also
available via that same registry) both FAIL on this repo's own source (a
trigraph literal in ``NumberFormat.cpp`` trips ``-Werror=trigraphs`` under
their non-MSVC compile flags), confirmed real and not a preference.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
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
    "prepare_java_library",
    "verify_java_example",
    "prepare_typescript_library",
    "verify_typescript_example",
    "prepare_cpp_library",
    "verify_cpp_example",
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


# --- cells/cpp ---------------------------------------------------------------
#
# Real, hands-on toolchain facts confirmed today by cloning
# aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp at its pinned commit and
# actually running cmake -G Ninja + ninja against it with MSVC:
#
#   - The pinned repo has NO top-level CMakeLists.txt; the library's own
#     CMakeLists.txt (project `aspose_cells_foss_cpp`, library target
#     `aspose_cells_foss`, a STATIC library) lives in the
#     ``Aspose.Cells.Foss.Cpp`` subdirectory. Its own public headers live
#     under ``Aspose.Cells.Foss.Cpp/include/aspose/cells_foss/*.h`` - the
#     real, nested include layout TC-094's extraction run had already
#     confirmed.
#   - Configuring with no CMAKE_BUILD_TYPE set (Ninja's single-config
#     default) makes the project's own toolchain-detect.cmake select MSVC's
#     debug, dynamic runtime (``/MDd``). A candidate compiled with cl.exe's
#     own bare default (``/MT``, static release) then fails to LINK against
#     it with LNK2038 runtime-library mismatches - not a candidate code
#     defect. Building with ``-DCMAKE_BUILD_TYPE=Release`` (MSVC's dynamic
#     release runtime, ``/MD``) and compiling every candidate with ``/MD``
#     keeps both sides in agreement; confirmed by a real link that only
#     succeeds once both are ``/MD``.
#   - The real output artifact is a plain ``.lib`` file at the build
#     directory's own root (Ninja's single-config generator does not nest
#     output under a per-configuration subdirectory the way the Visual
#     Studio generator would): ``aspose_cells_foss.lib``.
#   - Building the bare ``aspose_cells_foss`` target (rather than Ninja's
#     default "all") builds only the library's own ~190 object/link steps,
#     skipping the project's own ``tests`` subdirectory the top-level
#     CMakeLists.txt also enables - keeping this fast.
#   - The real furnished content at
#     ``tests/fixtures/furnished/cells_cpp/pages/_index.md`` is a complete,
#     self-contained snippet with its own ``#include``s and ``int main()``
#     - not a bare statement block needing a wrapper, unlike pdf/java's own
#     furnished content. No wrapping is done here; a candidate is compiled
#     exactly as given. (That real snippet is itself missing a needed
#     ``#include "aspose/cells_foss/WorksheetCollection.h"`` - ``Workbook.h``
#     only forward-declares that class, so ``WorksheetCollection::operator[]``
#     is not visible from it alone - which a real MSVC compile of it
#     genuinely catches; this module does not paper over that, since a
#     candidate that fails to compile as furnished is exactly what
#     verification exists to catch.)

_CPP_VCVARSALL = Path(r"C:\tools\rp-toolchains\vs-buildtools\VC\Auxiliary\Build\vcvarsall.bat")
_CPP_VSWHERE_DIR = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer")
_CPP_NINJA_EXECUTABLE = Path(r"C:\tools\rp-toolchains\ninja\ninja.exe")
_CPP_CMAKE_EXECUTABLE = Path(r"C:\tools\rp-toolchains\winlibs\mingw64\bin\cmake.exe")

_CPP_LIBRARY_SUBDIR = "Aspose.Cells.Foss.Cpp"
_CPP_LIBRARY_TARGET = "aspose_cells_foss"
_CPP_LIBRARY_ARTIFACT_NAME = "aspose_cells_foss.lib"
_CPP_BUILD_DIRNAME = "_build"

_CL_EXE_PATH_RE = re.compile(r"^(.*\bcl\.exe)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class _MsvcEnvironment:
    """The real, subprocess-scoped MSVC x64 environment - never written to
    this machine's actual PATH/environment.
    """

    env: dict[str, str]
    cl_exe: Path


def _msvc_environment(workdir: Path) -> _MsvcEnvironment:
    """Activate the real MSVC x64 developer environment by running
    ``vcvarsall.bat x64`` in a disposable ``cmd.exe`` subprocess and parsing
    its own ``set`` output, exactly as
    ``C:\\tools\\rp-toolchains\\TOOLCHAIN_PATHS.txt`` documents.

    Returns a full environment dict (a copy of this process's own
    ``os.environ``, with ``INCLUDE``/``LIB``/``LIBPATH`` set from
    vcvarsall's own output and cl.exe's real resolved directory plus the
    real Windows SDK ``bin`` directory - needed for ``rc.exe``/``mt.exe``
    during linking, confirmed necessary by a real failed link without it -
    prepended to ``PATH``) for the caller to pass as
    ``subprocess.run(..., env=...)``. This machine's own PATH/environment is
    never modified; everything here is scoped to the returned dict.

    Raises ``RuntimeError`` if vcvarsall.bat fails, or if
    INCLUDE/LIB/LIBPATH/the Windows SDK bin directory/cl.exe's own path
    cannot be resolved from its real output.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    script_path = workdir / "_activate_msvc.bat"
    script_path.write_text(
        "@echo off\r\n"
        f'call "{_CPP_VCVARSALL}" x64\r\n'
        "echo ---MSVC-ENV-START---\r\n"
        "set\r\n"
        "echo ---MSVC-ENV-END---\r\n"
        "where cl.exe\r\n",
        encoding="utf-8",
    )

    launch_env = dict(os.environ)
    launch_env["PATH"] = f"{_CPP_VSWHERE_DIR};{launch_env.get('PATH', '')}"

    result = subprocess.run(
        ["cmd.exe", "/c", str(script_path)],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=launch_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"vcvarsall.bat x64 failed:\n{result.stdout}\n{result.stderr}")

    output = result.stdout
    if "---MSVC-ENV-START---" not in output or "---MSVC-ENV-END---" not in output:
        raise RuntimeError(f"vcvarsall.bat x64 produced unexpected output:\n{output}\n{result.stderr}")

    env_block = output.split("---MSVC-ENV-START---", 1)[1].split("---MSVC-ENV-END---", 1)[0]
    captured: dict[str, str] = {}
    for line in env_block.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            captured[key.strip()] = value

    for required_key in ("INCLUDE", "LIB", "LIBPATH", "WindowsSdkVerBinPath"):
        if not captured.get(required_key):
            raise RuntimeError(f"vcvarsall.bat x64 did not set {required_key}:\n{output}")

    after_env_block = output.split("---MSVC-ENV-END---", 1)[1]
    cl_match = _CL_EXE_PATH_RE.search(after_env_block)
    if cl_match is None:
        raise RuntimeError(f"could not resolve cl.exe from `where cl.exe`:\n{after_env_block}")
    cl_exe = Path(cl_match.group(1).strip())
    if not cl_exe.exists():
        raise RuntimeError(f"resolved cl.exe path does not exist: {cl_exe}")

    sdk_bin_dir = Path(captured["WindowsSdkVerBinPath"].strip().rstrip("\\")) / "x64"

    env = dict(os.environ)
    env["INCLUDE"] = captured["INCLUDE"]
    env["LIB"] = captured["LIB"]
    env["LIBPATH"] = captured["LIBPATH"]
    env["PATH"] = f"{cl_exe.parent};{sdk_bin_dir};{env.get('PATH', '')}"

    return _MsvcEnvironment(env=env, cl_exe=cl_exe)


def prepare_cpp_library(repository: str, commit: str, workdir: Path) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    activate a real MSVC x64 environment, and build the real
    ``aspose_cells_foss`` static-library CMake target once with
    ``cmake -G Ninja`` + ``ninja`` (MSVC specifically - GCC/Clang both fail
    on this repo's own trigraph literal under their non-MSVC compile
    flags, confirmed real by
    ``C:\\tools\\rp-toolchains\\TOOLCHAIN_PATHS.txt``).

    Returns ``workdir`` - the cloned repo's own root, matching
    ``prepare_rust_library``'s/``prepare_go_library``'s own convention of
    returning the prepared root directly. Under it,
    ``Aspose.Cells.Foss.Cpp/include`` is the real header root a candidate's
    ``#include`` directives resolve against, and
    ``Aspose.Cells.Foss.Cpp/_build/aspose_cells_foss.lib`` is the real
    built static library - both real, hands-on-confirmed paths
    ``verify_cpp_example`` derives from ``library_dir``/``include_dir``.

    Raises ``RuntimeError`` on any real failure (clone, checkout, MSVC
    environment activation, cmake configure, or ninja build).
    """
    _clone_pinned_commit(repository, commit, workdir)

    library_source_dir = workdir / _CPP_LIBRARY_SUBDIR
    build_dir = library_source_dir / _CPP_BUILD_DIRNAME
    build_dir.mkdir(parents=True, exist_ok=True)

    msvc = _msvc_environment(workdir / "_msvc_env_prepare")
    build_env = dict(msvc.env)
    build_env["PATH"] = f"{_CPP_NINJA_EXECUTABLE.parent};{build_env.get('PATH', '')}"

    configure_result = subprocess.run(
        [
            str(_CPP_CMAKE_EXECUTABLE),
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_CXX_COMPILER={msvc.cl_exe}",
            str(library_source_dir),
        ],
        cwd=build_dir,
        capture_output=True,
        text=True,
        env=build_env,
    )
    if configure_result.returncode != 0:
        raise RuntimeError(
            f"cmake configure of {repository}@{commit} ({_CPP_LIBRARY_SUBDIR}) failed:\n"
            f"{configure_result.stdout}\n{configure_result.stderr}"
        )

    build_result = subprocess.run(
        [str(_CPP_NINJA_EXECUTABLE), _CPP_LIBRARY_TARGET],
        cwd=build_dir,
        capture_output=True,
        text=True,
        env=build_env,
    )
    if build_result.returncode != 0:
        raise RuntimeError(
            f"ninja build of {_CPP_LIBRARY_TARGET} for {repository}@{commit} failed:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    library_artifact = build_dir / _CPP_LIBRARY_ARTIFACT_NAME
    if not library_artifact.exists():
        raise RuntimeError(
            f"ninja reported success building {_CPP_LIBRARY_TARGET} but {library_artifact} was not produced"
        )

    return workdir


def verify_cpp_example(
    candidate: CandidateExample,
    *,
    library_dir: Path,
    include_dir: Path,
    workdir: Path,
) -> VerificationResult:
    """Compile and link ``candidate.code`` as a real, throwaway ``.cpp``
    file against the pre-built static library under ``library_dir`` (a
    ``prepare_cpp_library`` root -
    ``Aspose.Cells.Foss.Cpp/_build/aspose_cells_foss.lib`` under it) and
    ``include_dir`` (the header root, e.g. ``Aspose.Cells.Foss.Cpp/include``
    under that same root), using the same real MSVC toolchain, and report
    the real ``cl.exe`` outcome.

    Compiled with ``/MD`` to match the reference library's own ``Release``
    build (dynamic, non-debug runtime) - confirmed by a real, hands-on
    reproduction that a mismatched runtime (the library's own CMake default
    of ``/MDd`` vs. cl.exe's own bare default of ``/MT``) fails at LINK time
    with LNK2038 errors, not at candidate compile time, so both sides must
    agree.

    The literal comparison determining ``verified`` is intentionally exact
    (``verified = result.returncode == 0``), matching every other pilot.
    """
    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    source_path = project_dir / "candidate.cpp"
    source_path.write_text(candidate.code, encoding="utf-8")

    library_artifact = library_dir / _CPP_LIBRARY_SUBDIR / _CPP_BUILD_DIRNAME / _CPP_LIBRARY_ARTIFACT_NAME
    output_exe = project_dir / "candidate.exe"

    msvc = _msvc_environment(workdir / "_msvc_env_verify")

    result = subprocess.run(
        [
            str(msvc.cl_exe),
            "/nologo",
            "/EHsc",
            "/std:c++17",
            "/MD",
            f"/I{include_dir}",
            str(source_path),
            str(library_artifact),
            f"/Fe:{output_exe}",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        env=msvc.env,
    )

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


_GO_PACKAGE_DECLARATION_RE = re.compile(r"(?m)^\s*package\s+\w+")


def _wrap_go_fragment_if_needed(code: str, module_path: str) -> str:
    """Wrap a bare Go statement fragment in a minimal compilable
    ``package main`` + ``func main()`` shell; compile a complete file
    exactly as given.

    Every one of pdf/go's real furnished-content candidates (confirmed by
    reading ``tests/fixtures/furnished/pdf_go/pages/_index.md``) is a bare
    statement fragment like ``doc, _ := pdf.Open("input.pdf")`` with no
    ``package``, ``import``, or ``func main`` of its own - the same shape
    pdf/java's own furnished content has, which ``verify_java_example``
    already handles by wrapping only when no ``public class`` is present.
    This mirrors that pattern for Go: a fragment already containing a
    top-level ``package`` declaration is a complete file and is compiled
    as-is, unwrapped; anything else is treated as a bare fragment and
    wrapped.

    The real published Go package at ``module_path`` is named
    ``asposepdf``, not ``pdf`` (confirmed by cloning
    ``aspose-pdf-foss/Aspose-PDF-FOSS-for-Go`` at its pinned commit
    ``286484d235196d65c9a458c5eff3d3d6539216dc`` and reading its own
    top-level ``.go`` files' ``package`` declarations directly) - but every
    real furnished candidate uses the qualifier ``pdf.`` throughout,
    matching this same repository's own documented install instructions
    (``tests/fixtures/furnished/pdf_go/pages/_index.md``'s own FAQ:
    "Import it with an alias in your source files using ``import pdf
    "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"``"). The import
    below therefore always aliases the import as ``pdf`` regardless of the
    package's own real declared name, which Go's import aliasing supports
    unconditionally - confirmed by actually compiling a hand-built wrapped
    example against the real cloned module before this was written.

    Go fails to compile on an unused import, so ``"fmt"`` is included only
    if the fragment text actually contains ``fmt.`` (a simple substring
    check, matching this project's own pragmatic style elsewhere - no full
    Go parse needed - and confirmed real: an unconditional ``"fmt"``
    import against a fragment that never uses it fails with `"fmt"
    imported and not used`), and the pdf import only if the fragment
    contains its ``pdf.`` qualifier (always true for pdf/go's real content
    today, but kept honest rather than unconditional).
    """
    if _GO_PACKAGE_DECLARATION_RE.search(code) is not None:
        return code

    import_lines = []
    if "fmt." in code:
        import_lines.append('\t"fmt"')
    if "pdf." in code:
        import_lines.append(f'\tpdf "{module_path}"')

    import_block = ""
    if import_lines:
        import_block = "import (\n" + "\n".join(import_lines) + "\n)\n\n"

    indented = "\n".join(f"\t{line}" if line.strip() else line for line in code.splitlines())

    return f"package main\n\n{import_block}func main() {{\n{indented}\n}}\n"


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

    Real furnished pdf/go content is a bare statement fragment with no
    ``package``/``import``/``func main`` of its own (confirmed by reading
    the real ``tests/fixtures/furnished/pdf_go/pages/_index.md`` content) -
    not a full compilable Go file. A candidate with no top-level ``package``
    declaration of its own is therefore wrapped by
    ``_wrap_go_fragment_if_needed`` in a minimal ``package main`` +
    ``func main()`` shell; a candidate that already declares its own
    ``package`` is compiled as-is.

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
    wrapped_code = _wrap_go_fragment_if_needed(candidate.code, module_path)
    (project_dir / "main.go").write_text(wrapped_code, encoding="utf-8")

    result = _run(["go", "build", "./..."], cwd=project_dir)

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)


# --- pdf/java --------------------------------------------------------------

_MAVEN_VERSION = "3.9.9"
_MAVEN_DOWNLOAD_URL = (
    "https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip"
)

_JAVA_PUBLIC_CLASS_RE = re.compile(r"public\s+(?:final\s+|abstract\s+)?class\s+(\w+)")


def _find_or_download_maven(workdir: Path) -> Path:
    """Return a real, invocable ``mvn`` executable: the one already on
    PATH if present, else a freshly downloaded-and-unzipped Maven
    distribution cached under ``workdir``.

    This machine has no ``mvn`` on PATH at all, confirmed by a real,
    hands-on check (``shutil.which("mvn")`` returns ``None``), so the
    download path is real, not theoretical. The versioned
    ``dlcdn.apache.org`` URL for this release 404s; only the
    ``archive.apache.org`` URL above was confirmed to actually serve the
    3.9.9 binary distribution.

    Raises ``RuntimeError`` if the download does not produce the expected
    executable.
    """
    on_path = shutil.which("mvn")
    if on_path is not None:
        return Path(on_path)

    maven_home = workdir / f"apache-maven-{_MAVEN_VERSION}"
    mvn_executable = maven_home / "bin" / ("mvn.cmd" if sys.platform == "win32" else "mvn")
    if mvn_executable.exists():
        return mvn_executable

    workdir.mkdir(parents=True, exist_ok=True)
    archive_path = workdir / "apache-maven.zip"
    with urllib.request.urlopen(_MAVEN_DOWNLOAD_URL) as response:
        archive_path.write_bytes(response.read())

    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(workdir)

    if not mvn_executable.exists():
        raise RuntimeError(
            f"Maven download from {_MAVEN_DOWNLOAD_URL} into {workdir} did not "
            f"produce the expected executable at {mvn_executable}"
        )

    return mvn_executable


def _select_built_jar(target_dir: Path) -> Path:
    """Return the real packaged jar in ``target_dir``, excluding the
    ``-sources.jar`` and ``-javadoc.jar`` side artifacts Maven also
    produces alongside it.
    """
    candidates = sorted(
        path
        for path in target_dir.glob("*.jar")
        if not path.name.endswith("-sources.jar") and not path.name.endswith("-javadoc.jar")
    )
    if not candidates:
        raise RuntimeError(f"no packaged jar (excluding sources/javadoc) found in {target_dir}")
    return candidates[0]


def prepare_java_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    ensure a real, invocable Maven is available (checking PATH first,
    downloading only if absent), build the reference library once, and
    return the real packaged jar's path.

    ``mvn package -DskipTests`` FAILS on this real repository: it skips
    test *execution* but not test *compilation*, and 283 test-source
    files fail to compile against the 943 main classes. This uses
    ``mvn package -Dmaven.test.skip=true`` instead, which skips compiling
    tests too and produces a real jar - confirmed working today by a real,
    hands-on build. The packaged jar (not the raw ``target/classes``
    directory) is what gets returned: referencing ``target/classes``
    directly as a classpath entry was found to fail from Git Bash on this
    machine even with a correct path.

    Raises ``RuntimeError`` on any real failure (clone, checkout, Maven
    acquisition, or build).
    """
    repo_dir = workdir / "repo"
    _clone_pinned_commit(repository, commit, repo_dir)

    mvn_executable = _find_or_download_maven(workdir / "maven")

    build_result = _run(
        [str(mvn_executable), "package", "-Dmaven.test.skip=true"],
        cwd=repo_dir,
    )
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference library at {repository}@{commit} failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    return _select_built_jar(repo_dir / "target")


def verify_java_example(
    candidate: CandidateExample,
    *,
    library_jar: Path,
    workdir: Path,
) -> VerificationResult:
    """Compile ``candidate.code`` against ``library_jar`` (the real
    packaged reference jar) with ``javac``, and report the real compiler
    outcome.

    Real furnished pdf/java content is a complete, self-contained
    statement block already wrapped in a ``try (Document doc = ...)``
    block - not a full compilable Java file with its own class
    declaration (confirmed by reading the real
    ``tests/fixtures/furnished/pdf_java/pages/`` content). A candidate
    with no ``public class`` of its own is therefore wrapped here in a
    minimal ``Candidate`` class with a ``main`` method and a wildcard
    ``org.aspose.pdf`` import; a candidate that already declares its own
    public class is compiled as-is, named after that class.

    The literal comparison determining ``verified`` is intentionally
    exact (``verified = result.returncode == 0``), matching
    ``verify_dotnet_example``.
    """
    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    match = _JAVA_PUBLIC_CLASS_RE.search(candidate.code)
    if match is not None:
        class_name = match.group(1)
        source = candidate.code
    else:
        class_name = "Candidate"
        indented = "\n".join(
            f"        {line}" if line.strip() else line for line in candidate.code.splitlines()
        )
        source = (
            "import org.aspose.pdf.*;\n\n"
            f"public class {class_name} {{\n"
            "    public static void main(String[] args) throws Exception {\n"
            f"{indented}\n"
            "    }\n"
            "}\n"
        )

    source_path = project_dir / f"{class_name}.java"
    source_path.write_text(source, encoding="utf-8")

    result = _run(
        ["javac", "-cp", str(library_jar), source_path.name],
        cwd=project_dir,
    )

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)


# --- pdf/typescript ----------------------------------------------------------

_TS_TSCONFIG_TEMPLATE = """{{
  "compilerOptions": {{
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "noEmit": true,
    "baseUrl": ".",
    "paths": {{
      "{package_name}": ["{dist_index}"]
    }}
  }},
  "include": ["candidate.ts"]
}}
"""


def prepare_typescript_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    ``npm install`` its own dependencies, and build it once with
    ``npx tsc -p tsconfig.build.json`` (confirmed working today), producing
    real ``dist/*.js`` + ``*.d.ts`` output. Returns the cloned repo's own
    root - where ``dist/`` and its already-installed
    ``node_modules/typescript`` live - for ``verify_typescript_example`` to
    depend on and type-check against.

    ``npm``/``npx`` are resolved via ``shutil.which`` rather than invoked
    by bare name: on this machine they are ``.CMD`` wrapper scripts, not
    ``.exe`` files, and a bare name in an argv list with ``shell=False``
    is not found by Windows' ``CreateProcess`` the way a real ``.exe`` on
    PATH is - confirmed by a real, hands-on reproduction.

    Raises ``RuntimeError`` on any real failure (clone, checkout, install,
    or build).
    """
    repo_dir = workdir / "repo"
    _clone_pinned_commit(repository, commit, repo_dir)

    npm_executable = shutil.which("npm")
    if npm_executable is None:
        raise RuntimeError("npm is not available on PATH")
    npx_executable = shutil.which("npx")
    if npx_executable is None:
        raise RuntimeError("npx is not available on PATH")

    install_result = _run([npm_executable, "install"], cwd=repo_dir)
    if install_result.returncode != 0:
        raise RuntimeError(
            f"npm install for {repository}@{commit} failed:\n{install_result.stdout}\n{install_result.stderr}"
        )

    build_result = _run([npx_executable, "tsc", "-p", "tsconfig.build.json"], cwd=repo_dir)
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference library at {repository}@{commit} failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    dist_dir = repo_dir / "dist"
    if not dist_dir.is_dir() or not any(dist_dir.iterdir()):
        raise RuntimeError(f"npm run build did not produce dist/ output in {dist_dir}")

    return repo_dir


def verify_typescript_example(
    candidate: CandidateExample,
    *,
    library_dir: Path,
    package_name: str,
    workdir: Path,
) -> VerificationResult:
    """Type-check ``candidate.code`` as a real ``.ts`` file importing
    ``package_name`` from ``library_dir``'s built ``dist/`` output, and
    report the real outcome.

    A minimal throwaway ``tsconfig.json`` maps ``package_name`` straight
    at ``dist/index`` via ``paths`` (``moduleResolution: "bundler"``), so
    no second ``npm install`` is needed per candidate. Compiling is done
    with the reference library's own already-installed TypeScript
    compiler (``library_dir/node_modules/typescript``), invoked via
    ``node`` directly rather than through the ``.CMD`` wrapper, run with
    ``--noEmit`` so this is real type-checking against the real built
    ``.d.ts`` output, not code generation.

    The literal comparison determining ``verified`` is intentionally
    exact (``verified = result.returncode == 0``), matching
    ``verify_dotnet_example``.
    """
    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    (project_dir / "candidate.ts").write_text(candidate.code, encoding="utf-8")

    dist_index = (library_dir / "dist" / "index").resolve().as_posix()
    tsconfig_content = _TS_TSCONFIG_TEMPLATE.format(
        package_name=package_name,
        dist_index=dist_index,
    )
    (project_dir / "tsconfig.json").write_text(tsconfig_content, encoding="utf-8")

    tsc_entrypoint = library_dir / "node_modules" / "typescript" / "lib" / "tsc.js"

    result = _run(
        ["node", str(tsc_entrypoint), "-p", "tsconfig.json"],
        cwd=project_dir,
    )

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)
