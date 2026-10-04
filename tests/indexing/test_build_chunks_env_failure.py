"""Offline tests for infra/build_chunks.py's environment-failure exit (REQ-G2-048, TC-184).

An ExampleEnvironmentError from the verifier means a candidate's toolchain could not reach its
package registry. That is an environment failure, never a verdict on the candidate's code, so
main() must end with ENVIRONMENT_FAILURE_EXIT (75), write nothing, and never record the candidate
as not verified. A plain not-verified result must still take the normal path.

Everything here is offline. The rust prepare and verify functions are replaced, so there is no
network and no cargo toolchain. The verify function is replaced in _PLATFORM_DISPATCH itself,
because the dispatch table captures the function object at import time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_candidates import extract_candidate_examples
from foss_mcp.indexing.example_verifier import ExampleEnvironmentError, VerificationResult

REPO_ROOT = Path(__file__).resolve().parents[2]
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"
FURNISHED_PAGE = REPO_ROOT / "tests/fixtures/furnished/pdf_net/pages/_index.md"

LIBRARY_REPOSITORY = "example-org/example-rust-library"
LIBRARY_COMMIT = "0" * 40
LIBRARY_CRATE_NAME = "example_crate"
REGISTRY_MARKER = "spurious network error"


def _load_real_page() -> dict:
    text = FURNISHED_PAGE.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


def _fake_prepare_rust_library(repository: str, commit: str, workdir: Path) -> Path:
    # No clone and no cargo build. The returned path is only handed to the fake verify.
    return workdir


def _rust_argv(tmp_path: Path) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Offline environment failure fixture",
        "--out",
        str(tmp_path / "chunks.json"),
        "--furnished-page",
        str(FURNISHED_PAGE),
        "--library-repository",
        LIBRARY_REPOSITORY,
        "--library-commit",
        LIBRARY_COMMIT,
        "--library-platform",
        "rust",
        "--library-crate-name",
        LIBRARY_CRATE_NAME,
    ]


def _run_main(argv: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["build_chunks.py"] + argv
    try:
        build_chunks.main()
    finally:
        sys.argv = old_argv


def test_environment_failure_ends_build_with_exit_75_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _fake_prepare_rust_library)

    verify_calls: list[str] = []

    def fake_verify_rust_example(candidate, *, workdir, library_crate_name, library_dir):
        verify_calls.append(candidate.title)
        raise ExampleEnvironmentError("cargo", REGISTRY_MARKER, "registry unreachable")

    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", fake_verify_rust_example)

    # The page must yield candidates, or verify is never reached and the test proves nothing.
    assert extract_candidate_examples(_load_real_page())

    out_path = tmp_path / "chunks.json"
    with pytest.raises(SystemExit) as info:
        _run_main(_rust_argv(tmp_path))

    assert info.value.code == 75
    assert info.value.code == build_chunks.ENVIRONMENT_FAILURE_EXIT
    assert verify_calls, "verify must have been reached before the environment failure"
    assert not out_path.exists(), "no document or chunk may be written for an environment failure"

    err = capsys.readouterr().err
    assert "cargo" in err
    assert REGISTRY_MARKER in err
    assert err.count("\n") == 1, "exactly one stderr line"


def test_not_verified_candidate_does_not_exit_75(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _fake_prepare_rust_library)

    def fake_verify_rust_example(candidate, *, workdir, library_crate_name, library_dir):
        return VerificationResult(
            candidate=candidate,
            verified=False,
            output="error[E0599]: no method named `example` found",
        )

    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", fake_verify_rust_example)

    out_path = tmp_path / "chunks.json"
    # A real compile failure is the normal not-verified path: main returns, it does not
    # raise SystemExit(75).
    _run_main(_rust_argv(tmp_path))

    assert out_path.exists()
    captured = capsys.readouterr()
    assert "verified 0/" in captured.out
    assert captured.err == ""
