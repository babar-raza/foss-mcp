"""Offline tests for TC-191: infra/build_chunks.py records every candidate's verification result.

Before this card, the candidate loop kept only ``verification_result.verified``, so a rejected
candidate left no evidence of why it was rejected. main() now writes one UTF-8 JSON array with one
object per candidate (title, platform, verified, output in full, index on its page) to
``--verification-report``, or to ``--out`` with the suffix ``.verification.json`` when that flag is
not given.

Nothing here touches git, a venv, a network, or a real interpreter. The python prepare function is
replaced by a stub, and the python verify function is replaced in ``_PLATFORM_DISPATCH`` itself,
because the dispatch table captures the function object at import time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_verifier import VerificationResult

REPO_ROOT = Path(__file__).resolve().parents[2]
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"

PAGE_TITLE = "Create and reopen a deck"
FIRST_TITLE = "Create the deck"
SECOND_TITLE = "Open the saved deck"
LIBRARY_REPOSITORY = "example/Aspose.Slides-FOSS-for-Python"
LIBRARY_COMMIT = "0" * 40

VERIFIED_OUTPUT = "compiled and ran cleanly\n"
# Deliberately long, multi-line and non-ASCII, so that any truncation of the recorded output shows.
REJECTED_OUTPUT = (
    "".join(f"line {n}: error at offset {n}\n" for n in range(300))
    + "Traceback (most recent call last):\n"
    + "ModuleNotFoundError: No module named 'aspose' (\u00e9\u4e2d)\n"
)


def _write_page(path: Path) -> Path:
    """Write a furnished-style page with two python candidates, in document order."""
    front_matter = {
        "title": PAGE_TITLE,
        "single": {
            "block": [
                {
                    "title": FIRST_TITLE,
                    "content": (
                        "Create a presentation and save it.\n\n"
                        "```python\n"
                        "import aspose.slides as slides\n"
                        'with slides.Presentation() as p:\n    p.save("output.pptx", slides.export.SaveFormat.PPTX)\n'
                        "```\n"
                    ),
                },
                {
                    "title": SECOND_TITLE,
                    "content": (
                        "Open the deck that the previous example saved.\n\n"
                        "```python\n"
                        "import aspose.slides as slides\n"
                        'with slides.Presentation("output.pptx") as p:\n    pass\n'
                        "```\n"
                    ),
                },
            ]
        },
    }
    path.write_text("---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8")
    return path


def _install_fake_python_verifier(monkeypatch: pytest.MonkeyPatch, verified_titles: set[str]) -> None:
    """Replace the python verifier with one whose result is fixed by candidate title.

    A candidate whose title is in ``verified_titles`` verifies with VERIFIED_OUTPUT. Every other
    candidate is rejected with REJECTED_OUTPUT.
    """

    def fake_prepare_python_library(repository: str, commit: str, workdir: Path) -> Path:
        return Path("dummy-venv") / "python"

    def fake_verify_python_example(candidate, *, venv_python: Path, workdir: Path) -> VerificationResult:
        if candidate.title in verified_titles:
            return VerificationResult(candidate=candidate, verified=True, output=VERIFIED_OUTPUT)
        return VerificationResult(candidate=candidate, verified=False, output=REJECTED_OUTPUT)

    monkeypatch.setattr(build_chunks, "prepare_python_library", fake_prepare_python_library)
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", fake_verify_python_example)


def _argv(tmp_path: Path, page_path: Path, extra: list[str] | None = None) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Offline verification report fixture",
        "--out",
        str(tmp_path / "chunks.json"),
        "--furnished-page",
        str(page_path),
        "--library-repository",
        LIBRARY_REPOSITORY,
        "--library-commit",
        LIBRARY_COMMIT,
        "--library-platform",
        "python",
    ] + (extra or [])


def _run_main(argv: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["build_chunks.py"] + argv
    try:
        build_chunks.main()
    finally:
        sys.argv = old_argv


def _read_report(path: Path) -> list[dict]:
    assert path.exists(), f"verification report was not written to {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_report_is_json_array_of_two_candidate_objects_with_required_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_python_verifier(monkeypatch, verified_titles={FIRST_TITLE})
    page_path = _write_page(tmp_path / "_index.md")

    # No --verification-report flag: the report must sit next to --out with .verification.json.
    _run_main(_argv(tmp_path, page_path))

    report = _read_report(tmp_path / "chunks.verification.json")
    assert isinstance(report, list)
    assert len(report) == 2
    for record in report:
        assert isinstance(record, dict)
        assert {"title", "platform", "verified", "output", "index"} <= set(record)

    assert [record["title"] for record in report] == [FIRST_TITLE, SECOND_TITLE]
    assert [record["platform"] for record in report] == ["python", "python"]
    assert [record["verified"] for record in report] == [True, False]
    assert [record["index"] for record in report] == [0, 1]


def test_not_verified_record_carries_the_full_output_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_python_verifier(monkeypatch, verified_titles={FIRST_TITLE})
    page_path = _write_page(tmp_path / "_index.md")

    _run_main(_argv(tmp_path, page_path))

    report = _read_report(tmp_path / "chunks.verification.json")
    rejected = report[1]
    assert rejected["verified"] is False
    assert rejected["output"] == REJECTED_OUTPUT
    assert len(rejected["output"]) > 5000, "the output must not be truncated"

    verified = report[0]
    assert verified["verified"] is True
    assert verified["output"] == VERIFIED_OUTPUT


def test_report_is_written_even_when_no_candidate_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_python_verifier(monkeypatch, verified_titles=set())
    page_path = _write_page(tmp_path / "_index.md")

    # A build with zero verified candidates still completes normally and still writes the report.
    _run_main(_argv(tmp_path, page_path))

    assert "verified 0/2 candidate examples" in capsys.readouterr().out
    report = _read_report(tmp_path / "chunks.verification.json")
    assert len(report) == 2
    assert all(record["verified"] is False for record in report)
    assert all(record["output"] == REJECTED_OUTPUT for record in report)


def test_explicit_verification_report_flag_overrides_the_default_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_python_verifier(monkeypatch, verified_titles={FIRST_TITLE})
    page_path = _write_page(tmp_path / "_index.md")
    explicit_path = tmp_path / "reports" / "evidence.json"

    _run_main(_argv(tmp_path, page_path, ["--verification-report", str(explicit_path)]))

    assert _read_report(explicit_path)[1]["output"] == REJECTED_OUTPUT
    assert not (tmp_path / "chunks.verification.json").exists()
