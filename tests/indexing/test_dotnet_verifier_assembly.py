"""Offline tests for TC-450: the .NET verifier compiles README blocks the way a reader would.

The candidate project targets the library's own target framework with implicit usings, the using
directives a page writes once are put in front of each block, a block that holds only using
directives is rejected without compiling, and every assembly step is written down as the first
line of the output. Nothing here runs a real toolchain: ``_run`` is replaced by a fake with the
real ``(args, *, cwd)`` signature that records what it was asked to run and reads the project files
at call time. The pages are the real committed furnished pages.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier
from foss_mcp.indexing.example_candidates import CandidateExample, extract_candidate_examples
from foss_mcp.indexing.example_verifier import (
    DOTNET_BUILD_PROPERTIES,
    VerificationResult,
    verify_dotnet_example,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FURNISHED = REPO_ROOT / "tests/fixtures/furnished"
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"

SLIDES_USINGS = (
    "using Aspose.Slides.Foss;",
    "using Aspose.Slides.Foss.Export;",
    "using Aspose.Slides.Foss.Drawing;",
)


def _candidates(pilot: str) -> list[CandidateExample]:
    page = build_chunks._load_furnished_page(FURNISHED / pilot / "pages" / "_index.md")
    return extract_candidate_examples(page)


def _by_title(candidates: list[CandidateExample], title: str) -> CandidateExample:
    matches = [candidate for candidate in candidates if candidate.title == title]
    assert len(matches) == 1, f"expected exactly one candidate titled {title!r}"
    return matches[0]


class FakeRun:
    """A replacement for ``example_verifier._run`` with the real ``(args, *, cwd)`` signature."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], Path]] = []
        self.program_cs: list[str] = []
        self.csproj: list[str] = []

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), cwd))
        self.program_cs.append((cwd / "Program.cs").read_text(encoding="utf-8"))
        self.csproj.append((cwd / "candidate.csproj").read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeRun) -> FakeRun:
    monkeypatch.setattr(example_verifier, "_run", fake)
    return fake


def _library(tmp_path: Path, csproj_body: str) -> Path:
    """A fake library clone: a ``.git`` directory and a csproj holding ``csproj_body``."""
    root = tmp_path / "clone"
    (root / ".git").mkdir(parents=True)
    project_dir = root / "src" / "Lib"
    project_dir.mkdir(parents=True)
    csproj = project_dir / "Lib.csproj"
    csproj.write_text(
        f'<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n{csproj_body}\n  </PropertyGroup>\n</Project>\n',
        encoding="utf-8",
    )
    return csproj


# --- _library_target_framework -----------------------------------------------------------------


def test_target_framework_single_net10(tmp_path: Path) -> None:
    csproj = _library(tmp_path, "    <TargetFramework>net10.0</TargetFramework>")
    assert example_verifier._library_target_framework(csproj) == ("net10.0", "library")


def test_target_framework_picks_highest_of_several_and_ignores_netstandard(tmp_path: Path) -> None:
    csproj = _library(tmp_path, "    <TargetFrameworks>netstandard2.0;net8.0;net10.0</TargetFrameworks>")
    assert example_verifier._library_target_framework(csproj) == ("net10.0", "library")


def test_target_framework_compares_numerically_not_textually(tmp_path: Path) -> None:
    csproj = _library(tmp_path, "    <TargetFrameworks>net10.0;net9.0</TargetFrameworks>")
    assert example_verifier._library_target_framework(csproj) == ("net10.0", "library")
    other = _library(tmp_path / "second", "    <TargetFrameworks>net9.0;net10.0</TargetFrameworks>")
    assert example_verifier._library_target_framework(other) == ("net10.0", "library")


def test_target_framework_read_from_directory_build_props_below_clone_root(tmp_path: Path) -> None:
    csproj = _library(tmp_path, "    <OutputType>Library</OutputType>")
    props = csproj.parent.parent / "Directory.Build.props"
    props.write_text(
        "<Project><PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )
    assert example_verifier._library_target_framework(csproj) == ("net9.0", "library")


def test_target_framework_ignores_directory_build_props_above_clone_root(tmp_path: Path) -> None:
    csproj = _library(tmp_path, "    <OutputType>Library</OutputType>")
    (tmp_path / "Directory.Build.props").write_text(
        "<Project><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )
    assert example_verifier._library_target_framework(csproj) == ("net8.0", "default")


def test_target_framework_without_usable_value_is_the_default(tmp_path: Path) -> None:
    csproj = _library(
        tmp_path,
        "    <TargetFramework>$(LibraryTfm)</TargetFramework>\n"
        "    <TargetFrameworks>netstandard2.0;netcoreapp3.1;net8.0-windows</TargetFrameworks>",
    )
    assert example_verifier._library_target_framework(csproj) == ("net8.0", "default")


def test_target_framework_ignores_commented_out_values(tmp_path: Path) -> None:
    csproj = _library(
        tmp_path,
        "    <!-- <TargetFramework>net99.0</TargetFramework> -->\n    <TargetFramework>net9.0</TargetFramework>",
    )
    assert example_verifier._library_target_framework(csproj) == ("net9.0", "library")


# --- the using-directive regex -----------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "using Aspose.Slides.Foss;",
        "using static System.Math;",
        "using Alias = A.B;",
        "using Aspose.Slides.Foss;   // Presentation, the I* interfaces",
        "    using System.IO;",
    ],
)
def test_directive_regex_matches_directives(line: str) -> None:
    assert example_verifier._DOTNET_USING_DIRECTIVE_RE.search(line) is not None


@pytest.mark.parametrize(
    "line",
    [
        "using var presentation = new Presentation();",
        "using (var p = new Presentation())",
        'using FileStream stream = File.OpenRead("photo.png");',
    ],
)
def test_directive_regex_does_not_match_using_statements(line: str) -> None:
    assert example_verifier._DOTNET_USING_DIRECTIVE_RE.search(line) is None


# --- _has_own_statements -----------------------------------------------------------------------


def test_slides_examples_block_has_no_statements_of_its_own() -> None:
    block = _by_title(_candidates("slides_net"), "Examples")
    assert block.code.count("using ") == 3
    assert not example_verifier._has_own_statements(
        block.code, preamble_line=example_verifier._DOTNET_USING_DIRECTIVE_RE
    )


@pytest.mark.parametrize("code", ["", "   \n\n  ", "// nothing here\n/* or\nhere */\n  // still nothing"])
def test_empty_and_comment_only_blocks_have_no_statements(code: str) -> None:
    assert not example_verifier._has_own_statements(
        code, preamble_line=example_verifier._DOTNET_USING_DIRECTIVE_RE
    )


def test_slides_quick_start_block_has_statements() -> None:
    block = _by_title(_candidates("slides_net"), "Quick start")
    assert example_verifier._has_own_statements(
        block.code, preamble_line=example_verifier._DOTNET_USING_DIRECTIVE_RE
    )


def test_lone_using_statement_is_a_statement() -> None:
    assert example_verifier._has_own_statements(
        "using var p = new Presentation();", preamble_line=example_verifier._DOTNET_USING_DIRECTIVE_RE
    )


def test_has_own_statements_without_a_preamble_pattern() -> None:
    assert example_verifier._has_own_statements("using Aspose.Slides.Foss;", preamble_line=None)


# --- extraction carries the page's code ---------------------------------------------------------


def test_extraction_of_the_real_slides_page_carries_page_code() -> None:
    candidates = _candidates("slides_net")
    assert len(candidates) == 9
    page_code = tuple(candidate.code for candidate in candidates)
    assert all(candidate.page_code == page_code for candidate in candidates)


def test_page_code_is_not_part_of_equality_or_repr() -> None:
    candidate = _candidates("slides_net")[0]
    other = dataclasses.replace(candidate, page_code=("something else",))
    assert other == candidate
    assert hash(other) == hash(candidate)
    assert "page_code" not in repr(candidate)


# --- page usings and assembly -------------------------------------------------------------------


def test_page_usings_of_the_real_slides_page() -> None:
    page_code = _candidates("slides_net")[0].page_code
    assert example_verifier._dotnet_page_usings(page_code) == SLIDES_USINGS


def test_assemble_text_and_formatting_adds_all_three_directives() -> None:
    candidate = _by_title(_candidates("slides_net"), "Text and formatting")
    source, added = example_verifier._assemble_dotnet_source(candidate.code, candidate.page_code)
    assert added == SLIDES_USINGS
    assert source == "\n".join(SLIDES_USINGS) + "\n" + candidate.code


def test_assemble_quick_start_adds_only_the_drawing_directive() -> None:
    candidate = _by_title(_candidates("slides_net"), "Quick start")
    source, added = example_verifier._assemble_dotnet_source(candidate.code, candidate.page_code)
    assert added == ("using Aspose.Slides.Foss.Drawing;",)
    assert source == "using Aspose.Slides.Foss.Drawing;\n" + candidate.code


def test_assemble_jmap_oauth_block_gains_the_jmap_directive() -> None:
    candidate = _by_title(_candidates("jmap_net"), "OAuth 2.0 bearer token authentication")
    source, added = example_verifier._assemble_dotnet_source(candidate.code, candidate.page_code)
    assert added == ("using Aspose.Jmap;",)
    assert source.startswith("using Aspose.Jmap;\n")


def test_assemble_compares_directives_with_whitespace_collapsed() -> None:
    code = "using   System.Text;\nConsole.WriteLine(1);"
    source, added = example_verifier._assemble_dotnet_source(code, ("using System.Text;", "using System.IO;"))
    assert added == ("using System.IO;",)
    assert source == "using System.IO;\n" + code


# --- verify_dotnet_example ----------------------------------------------------------------------


def test_verify_text_and_formatting_builds_against_the_library_framework(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _by_title(_candidates("slides_net"), "Text and formatting")
    csproj = _library(tmp_path, "    <TargetFramework>net10.0</TargetFramework>")
    fake = _install(monkeypatch, FakeRun(returncode=0, stdout="Build succeeded.", stderr=""))

    result = verify_dotnet_example(candidate, library_csproj=csproj, workdir=tmp_path / "work")

    assert len(fake.calls) == 1
    argv, cwd = fake.calls[0]
    assert argv == ["dotnet", "build", *DOTNET_BUILD_PROPERTIES]
    assert cwd == tmp_path / "work" / "candidate_project"
    assert "<TargetFramework>net10.0</TargetFramework>" in fake.csproj[0]
    assert "<ImplicitUsings>enable</ImplicitUsings>" in fake.csproj[0]
    assert f'<ProjectReference Include="{csproj}" />' in fake.csproj[0]
    assert fake.program_cs[0] == "\n".join(SLIDES_USINGS) + "\n" + candidate.code
    assert result.verified is True
    assert result.candidate is candidate
    first_line = result.output.splitlines()[0]
    assert first_line.startswith("[foss-mcp] assembly:")
    assert "net10.0" in first_line
    assert "(library)" in first_line
    for directive in SLIDES_USINGS:
        assert directive in first_line
    assert "Build succeeded." in result.output


def test_verify_failure_keeps_the_compiler_error_after_long_warning_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _by_title(_candidates("slides_net"), "Tables")
    csproj = _library(tmp_path, "    <TargetFramework>net10.0</TargetFramework>")
    warnings = "".join(f"Lib.cs({n},1): warning CS8618: warning number {n:05d}\n" for n in range(400))
    assert len(warnings) > 20_000 - 400
    final_line = "Program.cs(3,1): error CS0246: The type or namespace name 'Foo' could not be found"
    _install(monkeypatch, FakeRun(returncode=1, stdout=warnings[:20_000], stderr=final_line))

    result = verify_dotnet_example(candidate, library_csproj=csproj, workdir=tmp_path / "work")

    assert result.verified is False
    assert final_line in result.output
    assert "...[truncated " in result.output
    assert result.output.splitlines()[0].startswith("[foss-mcp] assembly:")


def test_verify_rejects_the_using_only_examples_block_without_running_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _by_title(_candidates("slides_net"), "Examples")
    csproj = _library(tmp_path, "    <TargetFramework>net10.0</TargetFramework>")
    fake = _install(monkeypatch, FakeRun())

    result = verify_dotnet_example(candidate, library_csproj=csproj, workdir=tmp_path / "work")

    assert fake.calls == []
    assert result.verified is False
    assert result.candidate is candidate
    assert result.output == example_verifier._NO_STATEMENTS_MESSAGE.format(kind="using directives")
    assert "has no statements of its own" in result.output
    assert "nothing was compiled or run" in result.output


def test_verify_candidate_without_page_code_compiles_exactly_its_own_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = 'Console.WriteLine("hello");'
    candidate = CandidateExample(title="Direct", description="", language="csharp", code=code)
    assert candidate.page_code == ()
    csproj = _library(tmp_path, "    <OutputType>Library</OutputType>")
    fake = _install(monkeypatch, FakeRun(returncode=0))

    result = verify_dotnet_example(candidate, library_csproj=csproj, workdir=tmp_path / "work")

    assert fake.program_cs == [code]
    assert "<TargetFramework>net8.0</TargetFramework>" in fake.csproj[0]
    first_line = result.output.splitlines()[0]
    assert first_line.endswith("page-usings-prepended=none")
    assert "(default)" in first_line
    assert result.candidate is candidate


# --- _truncate ----------------------------------------------------------------------------------


def test_truncate_returns_short_text_unchanged() -> None:
    text = "x" * example_verifier._OUTPUT_TRUNCATE_CHARS
    assert example_verifier._truncate(text) == text
    assert example_verifier._truncate("") == ""


def test_truncate_keeps_the_first_1500_and_the_last_6500_characters() -> None:
    assert example_verifier._OUTPUT_HEAD_CHARS == 1500
    text = "".join(chr(0x4E00 + n % 2000) for n in range(20_000))
    truncated = example_verifier._truncate(text)
    marker = "\n...[truncated 12000 characters]...\n"
    assert truncated == text[:1500] + marker + text[-6500:]


def test_truncate_states_the_exact_dropped_count() -> None:
    text = "a" * 8001
    truncated = example_verifier._truncate(text)
    assert "...[truncated 1 characters]..." in truncated
    assert truncated.startswith("a" * 1500 + "\n")
    assert truncated.endswith("\n" + "a" * 6500)


# --- wiring (AGENTS.md integration rule) --------------------------------------------------------


def test_build_chunks_main_hands_every_candidate_the_whole_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[CandidateExample] = []
    library_csproj = tmp_path / "Aspose.Slides.Foss.csproj"

    def fake_prepare(repository: str, commit: str, csproj_relative_path: str, workdir: Path) -> Path:
        return library_csproj

    def recorder(candidate: CandidateExample, *, workdir: Path, library_csproj: Path) -> VerificationResult:
        seen.append(candidate)
        return VerificationResult(candidate=candidate, verified=False, output="recorded")

    monkeypatch.setattr(build_chunks, "prepare_reference_library", fake_prepare)
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["dotnet"], "verify", recorder)
    argv = [
        "build_chunks.py",
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "TC-450 wiring",
        "--out",
        str(tmp_path / "chunks.json"),
        "--furnished-page",
        str(FURNISHED / "slides_net/pages/_index.md"),
        "--library-repository",
        "example/Aspose.Slides-FOSS-for-.NET",
        "--library-commit",
        "0" * 40,
        "--library-platform",
        "dotnet",
        "--library-csproj",
        "src/Aspose.Slides.Foss/Aspose.Slides.Foss.csproj",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    build_chunks.main()

    assert len(seen) == 9
    expected_page_code = tuple(candidate.code for candidate in _candidates("slides_net"))
    assert len(expected_page_code) == 9
    assert all(candidate.page_code == expected_page_code for candidate in seen)


def test_the_dotnet_dispatch_and_extraction_call_sites_exist_in_committed_source() -> None:
    assert build_chunks._PLATFORM_DISPATCH["dotnet"]["verify"] is verify_dotnet_example
    source = (REPO_ROOT / "infra/build_chunks.py").read_text(encoding="utf-8")
    assert "extract_candidate_examples(page)" in source
