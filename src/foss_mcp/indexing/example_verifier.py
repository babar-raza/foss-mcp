"""Real, isolated .NET compile-verification sandbox for candidate examples
(REQ-G2-048).

This module never trusts a candidate example's plausibility. It clones the
exact pinned upstream commit already indexed, builds the reference library
once, then compiles each candidate's real code in a throwaway console
project that references that library. The verdict is the real compiler's
exit code, nothing else.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from foss_mcp.indexing.example_candidates import CandidateExample

__all__ = [
    "VerificationResult",
    "prepare_reference_library",
    "verify_dotnet_example",
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
