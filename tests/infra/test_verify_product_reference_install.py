"""infra.verify_product_reference_install's first test coverage (G2/TC-275, C2 part 2).

Before this card, infra/verify_package_registry.py (TC-260) was a real, already-implemented
registry-verification sidecar fetcher with zero production callers - its own module docstring
said as much. This module is that first real caller for a single pilot: it reads the pilot's
already-written product_reference_<family>_<platform>.json sidecar, derives its "install"
coordinate the same way get_product_reference.py's own install branch does
(install_coordinate_for_verification), and - when there is one - calls the matching checker from
verify_package_registry.py's own _CHECKERS dict, writing the result through that module's own
sidecar shape.

Fully offline per this card's own ``network: false``: every checker call here is mocked through
verify_package_registry.py's own _CHECKERS dict (monkeypatch.setitem), never the real network -
the same convention tests/infra/test_verify_package_registry.py itself already uses.

infra/ is not a package (no __init__.py); every module under test is imported bare, after infra/
is inserted at the front of sys.path - the same convention tests/infra/test_serve_http.py already
uses for serve_http.py, and the one infra/verify_product_reference_install.py's own bare
``from fetch_product_reference import ...``/``from verify_package_registry import ...`` imports
require in order to resolve to the SAME module objects this file monkeypatches.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra"))
import fetch_product_reference  # noqa: E402
import verify_package_registry  # noqa: E402
import verify_product_reference_install  # noqa: E402

PYPROJECT_TEXT = """
[project]
name = "aspose-jmap-foss"
version = "26.9.0"
"""

PYPROJECT_WITH_NO_NAME_TEXT = """
[project]
version = "26.9.0"
"""

CMAKE_LISTS_TEXT = "cmake_minimum_required(VERSION 3.20)\nproject(AsposePdfFoss)\n"


def _write_manifest_sidecar(manifests_dir: Path, family: str, platform: str, *, manifest_text: str) -> Path:
    path = manifests_dir / fetch_product_reference.sidecar_name(family, platform)
    sidecar = {
        "manifest_text": manifest_text,
        "platform": platform,
        "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
        "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
    }
    path.write_text(json.dumps(sidecar), encoding="utf-8")
    return path


def _set_argv(monkeypatch, manifests_dir: Path, *, family: str = "jmap", platform: str = "python") -> None:
    argv = [
        "verify_product_reference_install.py",
        "--family",
        family,
        "--platform",
        platform,
        "--manifests-dir",
        str(manifests_dir),
    ]
    monkeypatch.setattr(sys, "argv", argv)


def _registry_output_path(manifests_dir: Path, family: str, platform: str) -> Path:
    return manifests_dir / verify_package_registry.verify_package_registry_sidecar_name(family, platform)


# --- writes the expected sidecar shape when a real coordinate exists ----------------------------


def test_main_writes_the_expected_sidecar_shape_when_a_coordinate_exists(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_manifest_sidecar(tmp_path, "jmap", "python", manifest_text=PYPROJECT_TEXT)
    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", lambda coordinate: True)
    _set_argv(monkeypatch, tmp_path)

    verify_product_reference_install.main()

    written = json.loads(_registry_output_path(tmp_path, "jmap", "python").read_text(encoding="utf-8"))
    assert written["ecosystem"] == "pypi"
    assert written["coordinate"] == "aspose-jmap-foss"
    assert written["verified"] is True
    assert isinstance(written["checked_at"], str) and written["checked_at"]


def test_main_writes_verified_false_when_the_checker_confirms_absence(tmp_path: Path, monkeypatch) -> None:
    _write_manifest_sidecar(tmp_path, "jmap", "python", manifest_text=PYPROJECT_TEXT)
    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", lambda coordinate: False)
    _set_argv(monkeypatch, tmp_path)

    verify_product_reference_install.main()

    written = json.loads(_registry_output_path(tmp_path, "jmap", "python").read_text(encoding="utf-8"))
    assert written["verified"] is False


def test_main_round_trips_through_load_package_registry_sidecar(tmp_path: Path, monkeypatch) -> None:
    _write_manifest_sidecar(tmp_path, "jmap", "python", manifest_text=PYPROJECT_TEXT)
    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", lambda coordinate: True)
    _set_argv(monkeypatch, tmp_path)

    verify_product_reference_install.main()

    loaded = verify_package_registry.load_package_registry_sidecar(_registry_output_path(tmp_path, "jmap", "python"))
    assert loaded is not None
    assert loaded["coordinate"] == "aspose-jmap-foss"


# --- writes nothing and exits 0 when there is nothing to verify ---------------------------------


def test_main_writes_nothing_when_no_manifest_sidecar_exists_yet(tmp_path: Path, monkeypatch, capsys) -> None:
    _set_argv(monkeypatch, tmp_path)

    verify_product_reference_install.main()  # must not raise

    out = capsys.readouterr().out
    assert "nothing to verify" in out
    assert list(tmp_path.iterdir()) == []


def test_main_writes_nothing_when_the_manifest_has_no_install_coordinate_cpp(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_manifest_sidecar(tmp_path, "pdf", "cpp", manifest_text=CMAKE_LISTS_TEXT)
    _set_argv(monkeypatch, tmp_path, family="pdf", platform="cpp")

    verify_product_reference_install.main()

    out = capsys.readouterr().out
    assert "nothing to verify" in out
    assert not _registry_output_path(tmp_path, "pdf", "cpp").exists()


def test_main_writes_nothing_when_the_manifest_is_missing_the_install_field(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_manifest_sidecar(tmp_path, "jmap", "python", manifest_text=PYPROJECT_WITH_NO_NAME_TEXT)
    _set_argv(monkeypatch, tmp_path)

    verify_product_reference_install.main()

    out = capsys.readouterr().out
    assert "nothing to verify" in out
    assert not _registry_output_path(tmp_path, "jmap", "python").exists()


# --- respects verify_package_registry.py's own freshness cache ----------------------------------


def test_main_skips_the_check_when_the_existing_registry_sidecar_is_within_the_freshness_window(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_manifest_sidecar(tmp_path, "jmap", "python", manifest_text=PYPROJECT_TEXT)
    output_path = _registry_output_path(tmp_path, "jmap", "python")
    existing = {
        "ecosystem": "pypi",
        "coordinate": "aspose-jmap-foss",
        "verified": False,
        "checked_at": "2026-01-01T00:00:00+00:00",
    }
    output_path.write_text(json.dumps(existing), encoding="utf-8")

    def _must_not_be_called(coordinate: str) -> bool:
        raise AssertionError("the checker must not be called")

    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", _must_not_be_called)
    _set_argv(monkeypatch, tmp_path)

    verify_product_reference_install.main()

    out = capsys.readouterr().out
    assert "skip" in out.lower()
    assert json.loads(output_path.read_text(encoding="utf-8")) == existing


def test_main_checks_again_when_the_existing_registry_sidecar_is_older_than_the_freshness_window(
    tmp_path: Path, monkeypatch
) -> None:
    _write_manifest_sidecar(tmp_path, "jmap", "python", manifest_text=PYPROJECT_TEXT)
    output_path = _registry_output_path(tmp_path, "jmap", "python")
    output_path.write_text(
        json.dumps(
            {
                "ecosystem": "pypi",
                "coordinate": "aspose-jmap-foss",
                "verified": False,
                "checked_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    stale_time = time.time() - verify_package_registry._SIDECAR_FRESHNESS_SECONDS - 1
    os.utime(output_path, (stale_time, stale_time))

    calls: list[str] = []

    def _checker(coordinate: str) -> bool:
        calls.append(coordinate)
        return True

    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", _checker)
    _set_argv(monkeypatch, tmp_path)

    verify_product_reference_install.main()

    assert calls == ["aspose-jmap-foss"]
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["verified"] is True


# --- a genuine transport/registry failure propagates, never silently guessed --------------------


def test_main_lets_a_checker_transport_failure_propagate_without_writing_a_sidecar(
    tmp_path: Path, monkeypatch
) -> None:
    _write_manifest_sidecar(tmp_path, "jmap", "python", manifest_text=PYPROJECT_TEXT)

    def _checker_that_fails(coordinate: str) -> bool:
        raise urllib.error.URLError("boom: simulated registry outage")

    monkeypatch.setitem(verify_package_registry._CHECKERS, "pypi", _checker_that_fails)
    _set_argv(monkeypatch, tmp_path)

    with pytest.raises(urllib.error.URLError):
        verify_product_reference_install.main()

    assert not _registry_output_path(tmp_path, "jmap", "python").exists()
