"""Offline tests for the Rust verifier's infrastructure-vs-compile
classification (REQ-G2-048, TC-181).

A registry or network outage during ``cargo build`` must raise
``ExampleEnvironmentError`` and never be recorded as a not-verified compile
verdict. A real compile failure must still return ``verified=False``.

No network, no registry and no Rust toolchain are needed: ``_run_cargo`` is
the only thing replaced. ``verify_rust_example`` itself runs for real and
writes its throwaway crate into ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import ExampleEnvironmentError, VerificationResult

_CRATE_NAME = "aspose-cells-foss-rust"
_CODE = "fn main() {}\n"

_NETWORK_MARKERS = [
    "spurious network error",
    "Could not resolve hostname",
    "SSL connect error",
    "download of config.json failed",
    "transfer too slow",
]


def _candidate() -> CandidateExample:
    return CandidateExample(title="Trivial", description="", language="rust", code=_CODE)


def _fake_cargo(monkeypatch: pytest.MonkeyPatch, *, returncode: int, output: str) -> list[list[str]]:
    """Replace the cargo subprocess with one that returns a fixed outcome.

    Returns the list that records each argv cargo was invoked with.
    """
    calls: list[list[str]] = []

    def fake_run_cargo(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=output)

    monkeypatch.setattr(ev, "_run_cargo", fake_run_cargo)
    return calls


def _verify(tmp_path: Path) -> VerificationResult:
    return ev.verify_rust_example(
        _candidate(),
        library_crate_name=_CRATE_NAME,
        library_dir=tmp_path / "reference_library",
        workdir=tmp_path,
    )


def test_compile_failure_returns_not_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _fake_cargo(
        monkeypatch,
        returncode=101,
        output="error[E0599]: no method named `frobnicate` found for struct `Workbook`\n",
    )

    result = _verify(tmp_path)

    assert calls == [["cargo", "build"]]
    assert result.verified is False
    assert "E0599" in result.output


def test_successful_build_is_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_cargo(monkeypatch, returncode=0, output="   Finished `dev` profile\n")

    result = _verify(tmp_path)

    assert result.verified is True
    assert result.candidate == _candidate()


@pytest.mark.parametrize("marker", _NETWORK_MARKERS)
def test_network_marker_raises_environment_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, marker: str
) -> None:
    _fake_cargo(
        monkeypatch,
        returncode=101,
        output=f"error: failed to get `aspose` as a dependency\n\nCaused by:\n  {marker}\n",
    )

    with pytest.raises(ExampleEnvironmentError) as info:
        _verify(tmp_path)

    assert info.value.tool == "cargo"
    assert info.value.marker == marker


def test_network_marker_in_a_passing_run_is_still_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Classification applies only to a failed run. A successful build that
    happens to print a marker-like line is still a real success.
    """
    _fake_cargo(monkeypatch, returncode=0, output="warning: spurious network error retried\n")

    result = _verify(tmp_path)

    assert result.verified is True


@pytest.mark.parametrize("marker", _NETWORK_MARKERS)
def test_compile_failure_with_a_network_marker_is_still_not_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, marker: str
) -> None:
    """Cargo prints its retry warning on real compile runs too. A failed run
    whose output has a network marker AND a rustc diagnostic is a compile
    failure: it returns not-verified and raises nothing.
    """
    _fake_cargo(
        monkeypatch,
        returncode=101,
        output=(
            f"warning: {marker} (2 tries remaining)\n"
            "error[E0599]: no method named `frobnicate` found for struct `Workbook`\n"
        ),
    )

    result = _verify(tmp_path)

    assert result.verified is False
    assert "E0599" in result.output


def test_marker_list_is_exactly_the_five_documented_markers() -> None:
    assert set(ev._CARGO_NETWORK_FAILURE_MARKERS) == set(_NETWORK_MARKERS)
    assert len(ev._CARGO_NETWORK_FAILURE_MARKERS) == len(_NETWORK_MARKERS)
