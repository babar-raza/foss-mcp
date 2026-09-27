"""Tests for src/foss_mcp/indexing/example_verifier.py (REQ-G2-048).

Real network, real git clone, real dotnet build - twice, once for the
reference library and once per candidate. Nothing here is mocked: the whole
point of this card is that a candidate example's verdict comes from a real
compiler, not from a guess about what it should do.

This is genuinely slow (shallow clone + NuGet restore + two dotnet builds)
and requires network access and a .NET 8 SDK.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    extract_candidate_examples,
)
from foss_mcp.indexing.example_verifier import (
    VerificationResult,
    prepare_reference_library,
    verify_dotnet_example,
)

# Pinned exactly as indexed - matches config/products/pdf/net.yaml's
# `repository` and tests/fixtures/pdf_net/api_surface.json's own
# `source_commit`. Never "latest".
REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"
COMMIT = "b7172877651413cff57a8bfe41fb8a8befb2406b"
CSPROJ_RELATIVE_PATH = "src/Aspose.Pdf.Foss.csproj"

REAL_FIXTURE = Path("tests/fixtures/furnished/pdf_net/pages/_index.md")

LINK_ANNOTATION_TITLE = "Open a PDF, Add a Link Annotation, and Save"


def _load_real_page() -> dict:
    text = REAL_FIXTURE.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


def _link_annotation_candidate() -> CandidateExample:
    """The real, already-known-broken candidate from TC-066's real
    extraction of the real furnished fixture: it uses `PdfAction` without
    importing `Aspose.Pdf.Annotations`.
    """
    page = _load_real_page()
    candidates = extract_candidate_examples(page)
    return next(c for c in candidates if c.title == LINK_ANNOTATION_TITLE)


@pytest.fixture(scope="module")
def reference_library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workdir = tmp_path_factory.mktemp("pdf_net_reference")
    return prepare_reference_library(
        repository=REPOSITORY,
        commit=COMMIT,
        csproj_relative_path=CSPROJ_RELATIVE_PATH,
        workdir=workdir,
    )


def test_prepare_reference_library_builds_the_real_pinned_commit(
    reference_library: Path,
) -> None:
    assert reference_library.exists()
    assert reference_library.name == "Aspose.Pdf.Foss.csproj"


def test_known_broken_candidate_fails_real_compile(reference_library: Path, tmp_path: Path) -> None:
    candidate = _link_annotation_candidate()
    assert "PdfAction" in candidate.code
    assert "using Aspose.Pdf.Annotations;" not in candidate.code

    result = verify_dotnet_example(candidate, library_csproj=reference_library, workdir=tmp_path)

    assert isinstance(result, VerificationResult)
    assert result.candidate == candidate
    assert result.verified is False
    assert "PdfAction" in result.output or "CS0103" in result.output


def test_known_broken_candidate_compiles_once_missing_using_is_added(
    reference_library: Path, tmp_path: Path
) -> None:
    candidate = _link_annotation_candidate()
    # The real extracted candidate's code, plus the one missing line -
    # never a hand-written, different snippet.
    corrected_code = "using Aspose.Pdf.Annotations;\n" + candidate.code
    corrected_candidate = dataclasses.replace(candidate, code=corrected_code)

    result = verify_dotnet_example(corrected_candidate, library_csproj=reference_library, workdir=tmp_path)

    assert result.verified is True, result.output
