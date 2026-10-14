"""Offline tests for TC-502 (REQ-G3-007): .NET builds turn XML documentation generation off.

The upstream words/net library documents its API with ``<include file="..\\Docs\\Text.xml" />``
(a Windows backslash path) while its csproj sets ``GenerateDocumentationFile`` and
``TreatWarningsAsErrors``, so on Linux the unresolved include is a build error (CS1589). The verifier
passes ``-p:GenerateDocumentationFile=false`` to both of its ``dotnet build`` calls.

Everything here is offline and deterministic: ``example_verifier._run`` is replaced by a recorder, so
no git, git-lfs or dotnet process ever starts and nothing touches the network.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import (
    DOTNET_BUILD_PROPERTIES,
    prepare_reference_library,
    verify_dotnet_example,
)

SOURCE_FILE = Path(example_verifier.__file__)
PROPERTY = "-p:GenerateDocumentationFile=false"
REPOSITORY = "example-org/example-dotnet-library"
COMMIT = "1" * 40
CSPROJ_RELATIVE_PATH = "src/Library.csproj"


class _Recorder:
    """Stub for ``example_verifier._run``: records every argv and succeeds with empty output."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def _dotnet_builds(recorder: _Recorder) -> list[list[str]]:
    return [call for call in recorder.calls if call[:2] == ["dotnet", "build"]]


def _library_csproj(tmp_path: Path) -> Path:
    root = tmp_path / "clone"
    (root / ".git").mkdir(parents=True)
    csproj = root / "src" / "Lib" / "Lib.csproj"
    csproj.parent.mkdir(parents=True)
    csproj.write_text(
        '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
        "    <TargetFramework>net8.0</TargetFramework>\n  </PropertyGroup>\n</Project>\n",
        encoding="utf-8",
    )
    return csproj


def test_constant_has_exactly_the_one_documented_entry() -> None:
    assert DOTNET_BUILD_PROPERTIES == (PROPERTY,)
    assert len(DOTNET_BUILD_PROPERTIES) == 1


def test_prepare_reference_library_build_turns_documentation_generation_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(example_verifier, "_run", recorder)
    workdir = tmp_path / "workdir"

    csproj_path = prepare_reference_library(REPOSITORY, COMMIT, CSPROJ_RELATIVE_PATH, workdir)

    builds = _dotnet_builds(recorder)
    assert len(builds) == 1
    assert builds[0] == ["dotnet", "build", str(csproj_path), PROPERTY]
    assert PROPERTY in builds[0]


def test_verify_dotnet_example_build_turns_documentation_generation_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(example_verifier, "_run", recorder)
    candidate = CandidateExample(
        title="Hello", description="", language="csharp", code='Console.WriteLine("hello");'
    )

    result = verify_dotnet_example(
        candidate, library_csproj=_library_csproj(tmp_path), workdir=tmp_path / "work"
    )

    assert recorder.calls == [["dotnet", "build", PROPERTY]]
    assert result.verified is True


def test_the_property_is_not_added_to_the_candidate_csproj_template() -> None:
    template = example_verifier._CANDIDATE_CSPROJ_TEMPLATE
    assert "GenerateDocumentationFile" not in template
    assert PROPERTY not in template


def test_generated_candidate_csproj_does_not_carry_the_property(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(example_verifier, "_run", _Recorder())
    candidate = CandidateExample(
        title="Hello", description="", language="csharp", code="Console.WriteLine(1);"
    )
    workdir = tmp_path / "work"

    verify_dotnet_example(candidate, library_csproj=_library_csproj(tmp_path), workdir=workdir)

    written = (workdir / "candidate_project" / "candidate.csproj").read_text(encoding="utf-8")
    assert "GenerateDocumentationFile" not in written


def test_committed_source_uses_the_constant_at_both_call_sites() -> None:
    text = SOURCE_FILE.read_text(encoding="utf-8")
    assert text.count("DOTNET_BUILD_PROPERTIES") >= 3
    assert '["dotnet", "build", str(csproj_path), *DOTNET_BUILD_PROPERTIES]' in text
    assert '["dotnet", "build", *DOTNET_BUILD_PROPERTIES]' in text


def test_the_property_literal_is_defined_once_in_the_committed_source() -> None:
    text = SOURCE_FILE.read_text(encoding="utf-8")
    assert text.count(PROPERTY) == 1


def test_only_dotnet_build_argv_lists_use_the_constant() -> None:
    """Non-.NET verifiers keep their argv lists: no other `_run`-style call mentions the constant."""
    lines = SOURCE_FILE.read_text(encoding="utf-8").splitlines()
    users = [
        line.strip()
        for line in lines
        if "DOTNET_BUILD_PROPERTIES" in line and not line.lstrip().startswith("#")
    ]
    call_sites = [line for line in users if re.search(r"\*DOTNET_BUILD_PROPERTIES", line)]
    assert len(call_sites) == 2
    assert all('"dotnet", "build"' in line for line in call_sites)
