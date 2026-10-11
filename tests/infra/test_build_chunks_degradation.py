"""Offline tests for infra/build_chunks.py's honest degradation (REQ-G3-005, TC-379).

When a pilot's reference library cannot be prepared (a missing build backend, a toolchain we do
not carry, a broken upstream commit), the platform's ``prepare`` raises a RuntimeError. The build
must still publish the API-surface chunks and the documentation chunks, publish no example as
verified, print one stderr line, and record the reason in the verification report. An
environment failure (ExampleEnvironmentError) and a configuration mistake stay fatal, and the
degradation covers preparation only.

Everything is offline: the rust prepare and verify are replaced (the dispatch table captures the
verify function object at import time, so it is replaced in the table itself).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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
UNAVAILABLE_PREFIX = "reference library unavailable: "
UNAVAILABLE_SUFFIX = "; publishing documentation without verified examples"
PREPARATION_TITLE = "(reference library preparation)"


def _argv(tmp_path: Path, *, with_crate_name: bool = True) -> list[str]:
    argv = [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Offline degradation fixture",
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
    ]
    if with_crate_name:
        argv += ["--library-crate-name", LIBRARY_CRATE_NAME]
    return argv


def _run_main(argv: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["build_chunks.py"] + argv
    try:
        build_chunks.main()
    finally:
        sys.argv = old_argv


def _raising_prepare(exc: BaseException):
    def prepare(repository: str, commit: str, workdir: Path) -> Path:
        raise exc

    return prepare


def _working_prepare(repository: str, commit: str, workdir: Path) -> Path:
    return workdir


def _install_verify(monkeypatch: pytest.MonkeyPatch, *, verified: bool) -> list[str]:
    calls: list[str] = []

    def fake_verify(candidate, *, workdir, library_crate_name, library_dir):
        calls.append(candidate.title)
        return VerificationResult(candidate=candidate, verified=verified, output="fake output")

    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", fake_verify)
    return calls


def _chunks(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / "chunks.json").read_text(encoding="utf-8"))["chunks"]


def _report(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / "chunks.verification.json").read_text(encoding="utf-8"))


def _example_chunks(chunks: list[dict]) -> list[dict]:
    return [chunk for chunk in chunks if "FQN: Example: " in chunk["text"]]


def _doc_chunks(chunks: list[dict]) -> list[dict]:
    return [chunk for chunk in chunks if "FQN: Doc: " in chunk["text"]]


def _api_chunks(chunks: list[dict]) -> list[dict]:
    return [
        chunk for chunk in chunks if chunk not in _example_chunks(chunks) and chunk not in _doc_chunks(chunks)
    ]


def _degraded_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
) -> tuple[list[dict], list[dict], str]:
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _raising_prepare(RuntimeError(message)))
    verify_calls = _install_verify(monkeypatch, verified=True)
    _run_main(_argv(tmp_path))
    assert verify_calls == [], "no candidate may be verified when the library is unavailable"
    return _chunks(tmp_path), _report(tmp_path), ""


# (1) a RuntimeError from prepare degrades honestly


def test_failed_preparation_still_publishes_api_and_doc_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The page must yield candidates, or "zero example chunks" proves nothing.
    assert FURNISHED_PAGE.exists() and API_SURFACE.exists()
    chunks, _report_records, _ = _degraded_run(tmp_path, monkeypatch, "failed to build cleanly: boom")
    assert _api_chunks(chunks), "API-surface chunks must still be published"
    assert _doc_chunks(chunks), "documentation chunks must still be published"
    assert _example_chunks(chunks) == [], "no example may be published as verified"
    assert "wrote" in capsys.readouterr().out


def test_failed_preparation_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # main() returns normally (no SystemExit); the output file exists.
    _degraded_run(tmp_path, monkeypatch, "failed to build cleanly: boom")
    assert (tmp_path / "chunks.json").exists()


def test_failed_preparation_prints_exactly_one_stderr_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = "failed to build cleanly: boom"
    _degraded_run(tmp_path, monkeypatch, message)
    err = capsys.readouterr().err
    assert err == f"{UNAVAILABLE_PREFIX}{message}{UNAVAILABLE_SUFFIX}\n"


def test_failed_preparation_records_exactly_one_library_unavailable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = "failed to build cleanly: boom"
    _chunks_out, report, _ = _degraded_run(tmp_path, monkeypatch, message)
    assert len(report) == 1
    record = report[0]
    assert record["kind"] == "library_unavailable"
    assert record["verified"] is False
    assert record["index"] == -1
    assert record["title"] == PREPARATION_TITLE
    assert record["platform"] == "rust"
    assert record["output"] == message


def test_failed_preparation_chunks_equal_an_all_unverified_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    degraded_dir = tmp_path / "degraded"
    degraded_dir.mkdir()
    chunks, _report_records, _ = _degraded_run(degraded_dir, monkeypatch, "boom")

    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _working_prepare)
    _install_verify(monkeypatch, verified=False)
    _run_main(_argv(baseline_dir))

    assert chunks == _chunks(baseline_dir)


# (2) an environment failure stays fatal


def test_environment_failure_in_prepare_exits_75_and_writes_no_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exc = ExampleEnvironmentError("cargo", REGISTRY_MARKER, "registry unreachable")
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _raising_prepare(exc))
    _install_verify(monkeypatch, verified=True)

    with pytest.raises(SystemExit) as info:
        _run_main(_argv(tmp_path))

    assert info.value.code == build_chunks.ENVIRONMENT_FAILURE_EXIT
    assert not (tmp_path / "chunks.json").exists()
    err = capsys.readouterr().err
    assert "cargo" in err
    assert REGISTRY_MARKER in err
    assert UNAVAILABLE_PREFIX not in err


def test_environment_failure_in_prepare_records_no_library_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exc = ExampleEnvironmentError("cargo", REGISTRY_MARKER, "registry unreachable")
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _raising_prepare(exc))
    _install_verify(monkeypatch, verified=True)

    with pytest.raises(SystemExit):
        _run_main(_argv(tmp_path))

    assert _report(tmp_path) == []


# (3) other exceptions from prepare propagate


def test_value_error_from_prepare_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _raising_prepare(ValueError("bad value")))
    _install_verify(monkeypatch, verified=True)

    with pytest.raises(ValueError, match="bad value"):
        _run_main(_argv(tmp_path))

    assert not (tmp_path / "chunks.json").exists()
    assert _report(tmp_path) == []


def test_missing_required_flag_is_still_a_fatal_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _raising_prepare(RuntimeError("boom")))

    with pytest.raises(SystemExit) as info:
        _run_main(_argv(tmp_path, with_crate_name=False))

    assert info.value.code == 2
    assert "--library-crate-name" in capsys.readouterr().err
    assert not (tmp_path / "chunks.json").exists()


# (4) the degradation covers preparation only


def test_runtime_error_from_verify_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _working_prepare)

    def raising_verify(candidate, *, workdir, library_crate_name, library_dir):
        raise RuntimeError("verifier exploded")

    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", raising_verify)

    with pytest.raises(RuntimeError, match="verifier exploded"):
        _run_main(_argv(tmp_path))

    assert not (tmp_path / "chunks.json").exists()
    assert all(record.get("kind") != "library_unavailable" for record in _report(tmp_path))


# (5) the happy path is unchanged


def test_happy_path_publishes_verified_examples_and_candidate_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _working_prepare)
    verify_calls = _install_verify(monkeypatch, verified=True)

    _run_main(_argv(tmp_path))

    page_candidates = extract_candidate_examples(_load_page())
    assert page_candidates, "the page must yield candidates, or this test proves nothing"
    assert len(verify_calls) == len(page_candidates)

    chunks = _chunks(tmp_path)
    assert _example_chunks(chunks), "verified examples must be published"
    assert _doc_chunks(chunks)
    assert _api_chunks(chunks)

    report = _report(tmp_path)
    assert len(report) == len(page_candidates)
    assert [record["index"] for record in report] == list(range(len(page_candidates)))
    assert all(record["verified"] is True for record in report)
    assert all(record["kind"] == "candidate" for record in report)
    assert all(record["platform"] == "rust" for record in report)
    assert capsys.readouterr().err == ""


def test_happy_path_unverified_candidates_are_recorded_and_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_chunks, "prepare_rust_library", _working_prepare)
    _install_verify(monkeypatch, verified=False)

    _run_main(_argv(tmp_path))

    assert _example_chunks(_chunks(tmp_path)) == []
    report = _report(tmp_path)
    assert report
    assert all(record["verified"] is False and record["kind"] == "candidate" for record in report)
    assert capsys.readouterr().err == ""


# (6) the stderr message is truncated to 600 characters, the report keeps it in full


def test_long_message_is_truncated_on_stderr_but_complete_in_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = "".join(chr(ord("a") + (i % 26)) for i in range(2000))
    _chunks_out, report, _ = _degraded_run(tmp_path, monkeypatch, message)

    err = capsys.readouterr().err
    assert err == f"{UNAVAILABLE_PREFIX}{message[:600]}{UNAVAILABLE_SUFFIX}\n"
    assert message[:601] not in err
    assert report[0]["output"] == message
    assert len(report[0]["output"]) == 2000


def test_message_of_exactly_600_characters_is_not_altered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = "x" * 600
    _degraded_run(tmp_path, monkeypatch, message)
    assert capsys.readouterr().err == f"{UNAVAILABLE_PREFIX}{message}{UNAVAILABLE_SUFFIX}\n"


def _load_page() -> dict:
    import yaml

    text = FURNISHED_PAGE.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text.split("---", 2)[1])
    assert isinstance(parsed, dict)
    return parsed
