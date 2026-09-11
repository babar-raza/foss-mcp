"""``package_manifest.parse_manifest`` reads identity fields from a platform's own manifest."""

from __future__ import annotations

from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine import package_manifest


def test_python_manifest_reads_name_version_license_and_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "widget"\n'
        'version = "1.2.3"\n'
        'license = "MIT"\n'
        'dependencies = ["requests"]\n'
        'requires-python = ">=3.10"\n',
        encoding="utf-8",
    )
    info = package_manifest.parse_manifest(tmp_path, "python")
    assert info["name"] == "widget"
    assert info["version"] == "1.2.3"
    assert info["license"] == "MIT"
    assert info["dependencies"] == ["requests"]
    assert info["requires_python"] == ">=3.10"


def test_python_manifest_falls_back_to_setup_py_when_no_pyproject(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\nsetup(name='widget', version='0.1.0')\n",
        encoding="utf-8",
    )
    info = package_manifest.parse_manifest(tmp_path, "python")
    assert info["name"] == "widget"
    assert info["version"] == "0.1.0"


def test_rust_manifest_reads_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "widget"\nversion = "0.3.0"\nlicense = "MIT"\n[dependencies]\nserde = "1"\n',
        encoding="utf-8",
    )
    info = package_manifest.parse_manifest(tmp_path, "rust")
    assert info["name"] == "widget"
    assert info["version"] == "0.3.0"
    assert info["dependencies"] == ["serde"]
    assert info["canonical_package"] == "widget"


def test_an_unrecognized_platform_returns_an_empty_manifest(tmp_path: Path) -> None:
    assert package_manifest.parse_manifest(tmp_path, "cobol") == {}
