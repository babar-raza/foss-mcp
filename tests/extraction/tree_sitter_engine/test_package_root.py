"""``package_root.detect_package_root`` locates a platform's source root inside a clone."""

from __future__ import annotations

from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine import package_root


def test_python_src_layout_root_is_the_package_under_src(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'widget'\n", encoding="utf-8")
    pkg = tmp_path / "src" / "widget"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    assert package_root.detect_package_root(tmp_path, "python") == pkg


def test_python_flat_layout_root_is_the_first_package_dir(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text("", encoding="utf-8")
    pkg = tmp_path / "widget"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    assert package_root.detect_package_root(tmp_path, "python") == pkg


def test_dotnet_root_excludes_test_and_exe_projects(tmp_path: Path) -> None:
    lib = tmp_path / "src" / "Widget"
    lib.mkdir(parents=True)
    (lib / "Widget.csproj").write_text("<Project></Project>", encoding="utf-8")
    tests = tmp_path / "tests" / "Widget.Tests"
    tests.mkdir(parents=True)
    (tests / "Widget.Tests.csproj").write_text("<Project></Project>", encoding="utf-8")
    assert package_root.detect_package_root(tmp_path, "dotnet") == lib


def test_go_nested_layout_root_is_the_directory_with_go_mod(tmp_path: Path) -> None:
    nested = tmp_path / "widget"
    nested.mkdir()
    (nested / "go.mod").write_text("module example.com/widget\n", encoding="utf-8")
    assert package_root.detect_package_root(tmp_path, "go") == nested


def test_falls_back_to_the_repo_root_when_no_marker_is_found(tmp_path: Path) -> None:
    assert package_root.detect_package_root(tmp_path, "python") == tmp_path
    assert package_root.detect_package_root(tmp_path, "unknown-platform") == tmp_path


def test_python_namespace_package_root_is_the_top_level_namespace_dir(tmp_path: Path) -> None:
    """Mirrors the real aspose-slides-foss/Aspose.Slides-FOSS-for-Python layout:

    `aspose/` has no __init__.py (a PEP 420 implicit namespace package) but
    contains `aspose/slides_foss/__init__.py`. detect_package_root must return
    the top-level namespace directory itself, not the nested subpackage and
    not the repo root.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'aspose-slides-foss'\n", encoding="utf-8"
    )
    namespace_dir = tmp_path / "aspose"
    namespace_dir.mkdir()
    nested_pkg = namespace_dir / "slides_foss"
    nested_pkg.mkdir()
    (nested_pkg / "__init__.py").write_text("", encoding="utf-8")
    assert package_root.detect_package_root(tmp_path, "python") == namespace_dir
