"""Offline tests for TC-190: a page's Python examples run in document order in one sandbox.

Real API docs are sequential tutorials: example 2 opens output.pptx, which example 1 saves.
``infra/build_chunks.py`` therefore gives the python platform one shared candidate workdir per
page (``"shared_page_workdir": True`` in ``_PLATFORM_DISPATCH``), and every other platform keeps a
fresh, isolated workdir per candidate.

Nothing here touches git, a venv, or a real interpreter: the python prepare function is replaced
by a stub that returns a dummy venv path, and the python verify function is replaced by a fake
that models the one fact that matters - whether the file an earlier candidate wrote is still
visible in the workdir the later candidate receives.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_verifier import VerificationResult

PAGE_TITLE = "Create and reopen a deck"
FIRST_TITLE = "Create the deck"
SECOND_TITLE = "Open the saved deck"


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


def _args(page_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        furnished_page=page_path,
        library_repository="example/Aspose.Slides-FOSS-for-Python",
        library_commit="0" * 40,
        library_platform="python",
        library_csproj=None,
        library_crate_name=None,
        library_module_path=None,
        library_package_name=None,
    )


@pytest.fixture
def python_sandbox_fakes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace python prepare with a dummy venv and python verify with a sandbox-aware fake.

    Returns the list of workdirs the fake verify received, one per call, in call order.
    """
    received_workdirs: list[Path] = []

    def fake_prepare_python_library(repository: str, commit: str, workdir: Path) -> Path:
        return Path("dummy-venv") / "python"

    def fake_verify_python_example(
        candidate: object,
        *,
        venv_python: Path,
        workdir: Path,
    ) -> VerificationResult:
        script_dir = workdir / "candidate_script"
        script_dir.mkdir(parents=True, exist_ok=True)
        is_first_candidate = not received_workdirs
        received_workdirs.append(workdir)
        if is_first_candidate:
            # Example 1 saves the deck, exactly as the real tutorial does.
            (script_dir / "output.pptx").write_bytes(b"saved by the first example")
            verified = True
        else:
            # Example 2 can only open the deck if example 1 wrote it into the same folder.
            verified = (script_dir / "output.pptx").exists()
        return VerificationResult(candidate=candidate, verified=verified, output="")  # type: ignore[arg-type]

    monkeypatch.setattr(build_chunks, "prepare_python_library", fake_prepare_python_library)
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", fake_verify_python_example)
    return received_workdirs


def test_two_python_candidates_on_one_page_are_both_verified_in_one_sandbox(
    tmp_path: Path,
    python_sandbox_fakes: list[Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    page_path = _write_page(tmp_path / "_index.md")

    chunks = build_chunks._build_verified_example_chunks(_args(page_path))

    # Both candidates were run, and both verified: example 2 saw the file example 1 wrote.
    assert len(python_sandbox_fakes) == 2
    assert "verified 2/2 candidate examples" in capsys.readouterr().out
    assert chunks, "expected verified example chunks for both candidates"

    # Both candidates were given the very same workdir, so the file really was shared.
    assert python_sandbox_fakes[0] == python_sandbox_fakes[1]

    all_text = "\n".join(chunk.text for chunk in chunks)
    assert SECOND_TITLE in all_text


def test_python_platform_entry_has_shared_page_workdir_true() -> None:
    assert build_chunks._PLATFORM_DISPATCH["python"]["shared_page_workdir"] is True


def test_java_and_every_other_platform_keep_shared_page_workdir_false() -> None:
    assert build_chunks._PLATFORM_DISPATCH["java"]["shared_page_workdir"] is False
    for platform, entry in build_chunks._PLATFORM_DISPATCH.items():
        if platform == "python":
            continue
        assert entry["shared_page_workdir"] is False, platform
