"""Offline tests for TC-867: the .NET verifier adds SkiaSharp's Linux native asset when the library uses SkiaSharp.

A library that draws through SkiaSharp (Aspose.Cells.FOSS saving a workbook to PDF) references only the managed
SkiaSharp package; the native Linux binary comes from the separate package ``SkiaSharp.NativeAssets.Linux`` and is
not pulled in transitively, so a built candidate failed at run time with ``DllNotFoundException ... libSkiaSharp``.
The generated ``candidate.csproj`` now gets one extra, Linux-conditioned ItemGroup at the library's SkiaSharp
version, and the assembly line says so. A library that declares no SkiaSharp is built byte-identically to before.

Nothing here runs a real toolchain: ``_run`` is replaced by a fake with the real ``(args, *, cwd)`` signature that
reads the project files at call time, exactly as tests/indexing/test_dotnet_verifier_assembly.py does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import verify_dotnet_example

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILE = REPO_ROOT / "src/foss_mcp/indexing/example_verifier.py"

NATIVE_PACKAGE = "SkiaSharp.NativeAssets.Linux"
CODE = 'Console.WriteLine("hello");'
NO_SKIA_ASSEMBLY_LINE = (
    "[foss-mcp] assembly: dotnet target-framework=net8.0 (default); "
    "implicit-usings=enable; page-usings-prepended=none"
)


class FakeRun:
    """A replacement for ``example_verifier._run`` with the real ``(args, *, cwd)`` signature."""

    def __init__(self) -> None:
        self.csproj: list[str] = []

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.csproj.append((cwd / "candidate.csproj").read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(args, 0, "Build succeeded.", "")


def _csproj_text(body: str) -> str:
    return f'<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n  </PropertyGroup>\n{body}\n</Project>\n'


def _library(tmp_path: Path, body: str = "") -> Path:
    """A fake clone (a ``.git`` directory, then ``src/Lib/Lib.csproj`` holding ``body`` under the project)."""
    root = tmp_path / "clone"
    (root / ".git").mkdir(parents=True)
    project_dir = root / "src" / "Lib"
    project_dir.mkdir(parents=True)
    csproj = project_dir / "Lib.csproj"
    csproj.write_text(_csproj_text(body), encoding="utf-8")
    return csproj


def _skia_reference(version: str) -> str:
    return f'  <ItemGroup>\n    <PackageReference Include="SkiaSharp" Version="{version}" />\n  </ItemGroup>'


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, csproj: Path) -> tuple[str, str]:
    """Verify one candidate against ``csproj``; return ``(candidate.csproj text, first output line)``."""
    fake = FakeRun()
    monkeypatch.setattr(example_verifier, "_run", fake)
    candidate = CandidateExample(title="Hello", description="", language="csharp", code=CODE)
    result = verify_dotnet_example(candidate, library_csproj=csproj, workdir=tmp_path / "work")
    assert result.verified is True
    assert len(fake.csproj) == 1
    return fake.csproj[0], result.output.splitlines()[0]


def _template(csproj: Path, target_framework: str = "net8.0") -> str:
    return example_verifier._CANDIDATE_CSPROJ_TEMPLATE.format(
        library_csproj=csproj, target_framework=target_framework
    )


def _native_group(version: str) -> str:
    return (
        "  <ItemGroup Condition=\"$([MSBuild]::IsOSPlatform('Linux'))\">\n"
        f'    <PackageReference Include="{NATIVE_PACKAGE}" Version="{version}" />\n'
        "  </ItemGroup>\n"
    )


def _assert_native_asset(written: str, line: str, csproj: Path, version: str) -> None:
    assert written == _template(csproj).replace("</Project>", _native_group(version) + "\n</Project>")
    assert f'<PackageReference Include="{NATIVE_PACKAGE}" Version="{version}" />' in written
    assert written.count(NATIVE_PACKAGE) == 1
    assert line.endswith(f"; native-assets={NATIVE_PACKAGE}@{version}")
    assert line == f"{NO_SKIA_ASSEMBLY_LINE}; native-assets={NATIVE_PACKAGE}@{version}"


# --- SkiaSharp found, from each place a version can be declared -----------------------------------------------


def test_skiasharp_in_the_library_csproj_adds_the_linux_native_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csproj = _library(tmp_path, _skia_reference("2.88.8"))
    written, line = _build(tmp_path, monkeypatch, csproj)
    _assert_native_asset(written, line, csproj, "2.88.8")


def test_skiasharp_in_a_directory_build_props_two_levels_up_adds_the_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csproj = _library(tmp_path)
    (csproj.parent.parent.parent / "Directory.Build.props").write_text(
        f"<Project>\n{_skia_reference('3.119.0')}\n</Project>\n", encoding="utf-8"
    )
    written, line = _build(tmp_path, monkeypatch, csproj)
    _assert_native_asset(written, line, csproj, "3.119.0")


def test_skiasharp_package_version_in_directory_packages_props_adds_the_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csproj = _library(tmp_path)
    (csproj.parent.parent / "Directory.Packages.props").write_text(
        "<Project>\n  <ItemGroup>\n"
        '    <PackageVersion Include="SkiaSharp" Version="2.88.9" />\n'
        "  </ItemGroup>\n</Project>\n",
        encoding="utf-8",
    )
    written, line = _build(tmp_path, monkeypatch, csproj)
    _assert_native_asset(written, line, csproj, "2.88.9")


def test_the_highest_numeric_version_of_several_declarations_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 2.88.8 is numerically higher than 2.9.0 although it sorts lower as text.
    csproj = _library(tmp_path, _skia_reference("2.9.0"))
    (csproj.parent.parent / "Directory.Packages.props").write_text(
        '<Project><ItemGroup><PackageVersion Include="SkiaSharp" Version="2.88.8" /></ItemGroup></Project>',
        encoding="utf-8",
    )
    written, line = _build(tmp_path, monkeypatch, csproj)
    _assert_native_asset(written, line, csproj, "2.88.8")


def test_a_version_given_as_a_child_element_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        '  <ItemGroup>\n    <PackageReference Include="SkiaSharp">\n'
        "      <Version>2.88.8</Version>\n    </PackageReference>\n  </ItemGroup>"
    )
    csproj = _library(tmp_path, body)
    written, line = _build(tmp_path, monkeypatch, csproj)
    _assert_native_asset(written, line, csproj, "2.88.8")


def test_attribute_order_and_a_condition_do_not_hide_the_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "  <ItemGroup Condition=\"'$(TargetFramework)' != 'net462'\">\n"
        '    <PackageReference Version="3.119.0" Condition="\'$(X)\' == \'y\'" Include="SkiaSharp" />\n'
        "  </ItemGroup>"
    )
    csproj = _library(tmp_path, body)
    written, line = _build(tmp_path, monkeypatch, csproj)
    _assert_native_asset(written, line, csproj, "3.119.0")


# --- SkiaSharp absent: byte-identical to today ----------------------------------------------------------------


def test_a_commented_out_reference_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = '  <!-- <PackageReference Include="SkiaSharp" Version="2.88.8" /> -->'
    csproj = _library(tmp_path, body)
    written, line = _build(tmp_path, monkeypatch, csproj)
    assert written == _template(csproj)
    assert line == NO_SKIA_ASSEMBLY_LINE
    assert NATIVE_PACKAGE not in written


def test_no_skiasharp_leaves_the_csproj_and_the_assembly_line_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        '  <ItemGroup>\n    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />\n  </ItemGroup>'
    )
    csproj = _library(tmp_path, body)
    written, line = _build(tmp_path, monkeypatch, csproj)
    assert written == _template(csproj)
    assert line == NO_SKIA_ASSEMBLY_LINE
    assert "native-assets" not in line
    assert "IsOSPlatform" not in written


@pytest.mark.parametrize(
    "reference",
    [
        '<PackageReference Include="SkiaSharp.Views" Version="2.88.8" />',
        '<PackageReference Include="SkiaSharp.NativeAssets.Linux" Version="2.88.8" />',
        '<PackageReference Include="SkiaSharp" Version="$(SkiaVersion)" />',
        '<PackageReference Include="SkiaSharp" />',
    ],
)
def test_other_packages_and_unresolvable_versions_add_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    csproj = _library(tmp_path, f"  <ItemGroup>\n    {reference}\n  </ItemGroup>")
    written, line = _build(tmp_path, monkeypatch, csproj)
    assert written == _template(csproj)
    assert line == NO_SKIA_ASSEMBLY_LINE


def test_a_props_file_above_the_clone_root_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csproj = _library(tmp_path)
    (tmp_path / "Directory.Build.props").write_text(
        f"<Project>\n{_skia_reference('2.88.8')}\n</Project>\n", encoding="utf-8"
    )
    written, line = _build(tmp_path, monkeypatch, csproj)
    assert written == _template(csproj)
    assert line == NO_SKIA_ASSEMBLY_LINE


# --- the condition and the template ---------------------------------------------------------------------------


def test_the_linux_condition_text_is_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    csproj = _library(tmp_path, _skia_reference("2.88.8"))
    written, _ = _build(tmp_path, monkeypatch, csproj)
    assert "IsOSPlatform('Linux')" in written
    assert "<ItemGroup Condition=\"$([MSBuild]::IsOSPlatform('Linux'))\">" in written
    assert (
        written.index("IsOSPlatform('Linux')") < written.index(NATIVE_PACKAGE) < written.index("</Project>")
    )
    assert written.endswith("</ItemGroup>\n\n</Project>\n")


def test_the_candidate_template_itself_is_not_changed() -> None:
    template = example_verifier._CANDIDATE_CSPROJ_TEMPLATE
    assert "SkiaSharp" not in template
    assert "IsOSPlatform" not in template


# --- a sibling project that carries SkiaSharp (one level of ProjectReference) ---------------------------------


def _with_sibling(tmp_path: Path, library_body: str, sibling_body: str) -> Path:
    """A clone whose library references ``../Sibling/Sibling.csproj`` (Windows separators, as upstream writes them)."""
    csproj = _library(tmp_path, library_body)
    sibling_dir = csproj.parent.parent / "Sibling"
    sibling_dir.mkdir()
    (sibling_dir / "Sibling.csproj").write_text(_csproj_text(sibling_body), encoding="utf-8")
    return csproj


def test_skiasharp_one_projectreference_away_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = (
        '  <ItemGroup>\n    <ProjectReference Include="..\\Sibling\\Sibling.csproj" />\n  </ItemGroup>'
    )
    csproj = _with_sibling(tmp_path, reference, _skia_reference("3.119.0"))
    written, line = _build(tmp_path, monkeypatch, csproj)
    _assert_native_asset(written, line, csproj, "3.119.0")


def test_a_second_level_of_projectreference_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = '  <ItemGroup>\n    <ProjectReference Include="../Sibling/Sibling.csproj" />\n  </ItemGroup>'
    csproj = _with_sibling(
        tmp_path,
        reference,
        '  <ItemGroup>\n    <ProjectReference Include="../Deep/Deep.csproj" />\n  </ItemGroup>',
    )
    deep_dir = csproj.parent.parent / "Deep"
    deep_dir.mkdir()
    (deep_dir / "Deep.csproj").write_text(_csproj_text(_skia_reference("2.88.8")), encoding="utf-8")
    written, line = _build(tmp_path, monkeypatch, csproj)
    assert written == _template(csproj)
    assert line == NO_SKIA_ASSEMBLY_LINE


def test_a_projectreference_outside_the_clone_root_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Outside.csproj").write_text(_csproj_text(_skia_reference("2.88.8")), encoding="utf-8")
    reference = (
        '  <ItemGroup>\n    <ProjectReference Include="../../../outside/Outside.csproj" />\n  </ItemGroup>'
    )
    csproj = _library(tmp_path, reference)
    written, line = _build(tmp_path, monkeypatch, csproj)
    assert written == _template(csproj)
    assert line == NO_SKIA_ASSEMBLY_LINE


def test_a_missing_projectreference_target_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = '  <ItemGroup>\n    <ProjectReference Include="..\\Gone\\Gone.csproj" />\n  </ItemGroup>'
    csproj = _library(tmp_path, reference)
    written, _ = _build(tmp_path, monkeypatch, csproj)
    assert written == _template(csproj)


# --- the helper and the call site -----------------------------------------------------------------------------


def test_the_version_helper_reports_none_without_skiasharp(tmp_path: Path) -> None:
    assert example_verifier._library_skiasharp_version(_library(tmp_path)) is None


def test_the_version_helper_reports_the_highest_version(tmp_path: Path) -> None:
    csproj = _library(tmp_path, _skia_reference("2.88.8"))
    (csproj.parent / "Directory.Build.props").write_text(
        f"<Project>\n{_skia_reference('2.88.10')}\n</Project>\n", encoding="utf-8"
    )
    assert example_verifier._library_skiasharp_version(csproj) == "2.88.10"


def test_the_helper_is_called_from_verify_dotnet_example_in_committed_source() -> None:
    text = SOURCE_FILE.read_text(encoding="utf-8")
    body = text[text.index("def verify_dotnet_example(") :]
    body = body[: body.index("\n# --- cells/cpp")]
    assert "_library_skiasharp_version(library_csproj)" in body
    assert "_native_assets_item_group(" in body
    assert "native-assets=" in body
