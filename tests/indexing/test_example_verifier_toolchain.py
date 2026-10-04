"""Offline unit tests for the cells/cpp toolchain resolver in
src/foss_mcp/indexing/example_verifier.py (TC-175, REQ-G2-048).

No network, no compiler, no real Visual Studio: every external lookup is
either a real file created under pytest's tmp_path, or a faked vswhere
output. These tests prove the resolution rules themselves - the real
end-to-end C++ build is covered by tests/indexing/test_example_verifier.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier as ev


def _write_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_toolchain_root_defaults_to_dev_tools_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOSS_MCP_TOOLS", raising=False)

    assert ev._toolchain_root() == Path(r"C:\dev-tools\foss-mcp")


def test_toolchain_root_honours_foss_mcp_tools_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FOSS_MCP_TOOLS", str(tmp_path))

    assert ev._toolchain_root() == tmp_path


def test_resolve_ninja_finds_real_file_under_override_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FOSS_MCP_TOOLS", str(tmp_path))
    ninja = _write_file(tmp_path / "rp-toolchains" / "ninja" / "ninja.exe")

    assert ev._resolve_ninja() == ninja


def test_resolve_cmake_finds_real_file_under_override_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FOSS_MCP_TOOLS", str(tmp_path))
    cmake = _write_file(tmp_path / "cmake" / "bin" / "cmake.exe")

    assert ev._resolve_cmake() == cmake


def test_missing_ninja_error_names_tool_place_and_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FOSS_MCP_TOOLS", str(tmp_path))

    with pytest.raises(RuntimeError) as excinfo:
        ev._resolve_ninja()

    message = str(excinfo.value)
    assert "ninja" in message
    assert "ninja.exe" in message
    assert str(tmp_path) in message
    assert "FOSS_MCP_TOOLS" in message


def test_missing_cmake_error_names_tool_place_and_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FOSS_MCP_TOOLS", str(tmp_path))

    with pytest.raises(RuntimeError) as excinfo:
        ev._resolve_cmake()

    message = str(excinfo.value)
    assert "cmake.exe" in message
    assert str(tmp_path) in message
    assert "FOSS_MCP_TOOLS" in message


def test_vcvarsall_resolved_from_vswhere_reported_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vswhere_dir = tmp_path / "Installer"
    _write_file(vswhere_dir / "vswhere.exe")
    install_dir = tmp_path / "BuildTools"
    vcvarsall = _write_file(install_dir / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat")
    monkeypatch.setattr(ev, "_CPP_VSWHERE_DIR", vswhere_dir)

    seen_args: list[list[str]] = []

    def fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        seen_args.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=f"{install_dir}\r\n", stderr="")

    monkeypatch.setattr(ev, "_run", fake_run)

    assert ev._resolve_vcvarsall() == vcvarsall
    assert "installationPath" in seen_args[0]
    assert seen_args[0][0] == str(vswhere_dir / "vswhere.exe")


def test_missing_vswhere_error_names_vswhere(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ev, "_CPP_VSWHERE_DIR", tmp_path)

    with pytest.raises(RuntimeError) as excinfo:
        ev._resolve_vcvarsall()

    assert "vswhere.exe" in str(excinfo.value)
    assert str(tmp_path) in str(excinfo.value)


def test_vswhere_reporting_no_installation_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_file(tmp_path / "vswhere.exe")
    monkeypatch.setattr(ev, "_CPP_VSWHERE_DIR", tmp_path)
    monkeypatch.setattr(
        ev,
        "_run",
        lambda args, *, cwd: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError) as excinfo:
        ev._resolve_vcvarsall()

    assert "Microsoft.VisualStudio.Component.VC.Tools.x86.x64" in str(excinfo.value)


def test_vcvarsall_missing_under_reported_installation_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_file(tmp_path / "vswhere.exe")
    install_dir = tmp_path / "EmptyInstall"
    install_dir.mkdir()
    monkeypatch.setattr(ev, "_CPP_VSWHERE_DIR", tmp_path)
    monkeypatch.setattr(
        ev,
        "_run",
        lambda args, *, cwd: subprocess.CompletedProcess(args, 0, stdout=f"{install_dir}\n", stderr=""),
    )

    with pytest.raises(RuntimeError) as excinfo:
        ev._resolve_vcvarsall()

    assert "vcvarsall.bat" in str(excinfo.value)
    assert str(install_dir) in str(excinfo.value)
