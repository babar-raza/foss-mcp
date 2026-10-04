"""Offline tests for the Rust reference build's infrastructure-vs-defect
classification (REQ-G2-048, TC-189).

``prepare_rust_library`` builds the real pinned reference crate with
``cargo build``. A registry or network outage during that build must raise
``ExampleEnvironmentError``, the same classification TC-181 gives the candidate
build. Any other failed reference build must keep raising ``RuntimeError``.

No network, no registry, no git clone and no Rust toolchain are needed:
``_clone_pinned_commit`` does nothing and ``_run_cargo`` returns a scripted
result.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_verifier import ExampleEnvironmentError

_REPOSITORY = "https://example.invalid/reference-crate.git"
_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _script_reference_build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int,
    output: str,
) -> list[list[str]]:
    """Make the reference clone a no-op and script the single cargo build.

    Returns the list that records each argv cargo was invoked with.
    """
    calls: list[list[str]] = []

    def fake_clone(repository: str, commit: str, workdir: Path) -> None:
        return None

    def fake_run_cargo(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=output)

    monkeypatch.setattr(ev, "_clone_pinned_commit", fake_clone)
    monkeypatch.setattr(ev, "_run_cargo", fake_run_cargo)
    return calls


def _build(tmp_path: Path) -> Path:
    return ev.prepare_rust_library(_REPOSITORY, _COMMIT, tmp_path / "reference")


@pytest.mark.parametrize("marker", ev._CARGO_NETWORK_FAILURE_MARKERS)
def test_reference_network_failure_without_diagnostic_is_environment_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, marker: str
) -> None:
    calls = _script_reference_build(
        monkeypatch,
        returncode=101,
        output=f"warning: {marker} (3 tries remaining)\nerror: failed to download from registry\n",
    )

    with pytest.raises(ExampleEnvironmentError) as excinfo:
        _build(tmp_path)

    assert calls == [["cargo", "build"]]
    assert excinfo.value.tool == "cargo"
    assert excinfo.value.marker == marker


def test_reference_build_with_rustc_diagnostic_is_a_defect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A rustc diagnostic is a real compile failure. The network marker printed
    # alongside it does not turn the failure into an environment error.
    _script_reference_build(
        monkeypatch,
        returncode=101,
        output=(
            "error[E0433]: failed to resolve: use of undeclared crate or module `missing`\n"
            "warning: spurious network error (2 tries remaining)\n"
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        _build(tmp_path)

    assert not isinstance(excinfo.value, ExampleEnvironmentError)
    assert "failed to build cleanly" in str(excinfo.value)


def test_reference_build_without_marker_or_diagnostic_is_a_defect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_reference_build(
        monkeypatch,
        returncode=101,
        output="error: could not compile `reference-crate` due to previous error\n",
    )

    with pytest.raises(RuntimeError) as excinfo:
        _build(tmp_path)

    assert not isinstance(excinfo.value, ExampleEnvironmentError)
    assert "failed to build cleanly" in str(excinfo.value)
