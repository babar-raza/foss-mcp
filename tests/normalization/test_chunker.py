"""Section-granularity chunking, never one page-sized chunk (plan 11.3)."""

from __future__ import annotations

from foss_mcp.normalization.chunker import Chunk, chunk_document, with_validation
from foss_mcp.normalization.document_schema import (
    NOT_CHECKED,
    Provenance,
    SourceKind,
    ValidationResult,
    make_document,
)

PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)

# A real pdf/net release body (v26.9.0): genuine markdown headings, several real sections.
MULTI_SECTION_BODY = (
    "# Aspose.PDF FOSS for .NET v26.9.0\n\n"
    "A rendering and cross-platform release.\n\n"
    "## New APIs\n"
    "- **`PdfFileSanitization`** repairs files too damaged to open.\n\n"
    "## Rendering\n"
    "- Dash patterns on software strokes.\n\n"
    "## Text and fonts\n"
    "- Horizontal scaling stretches glyphs.\n"
)


def _document(body: str):
    return make_document(
        source_kind=SourceKind.REPO_NATIVE_RELEASE_NOTES,
        content_type="release_notes",
        provenance=PROVENANCE,
        evidence_refs=("aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET#v26.9.0",),
        title="v26.9.0",
        body=body,
        richness="detailed",
    )


def test_a_multi_heading_document_becomes_multiple_section_chunks() -> None:
    chunks = chunk_document(_document(MULTI_SECTION_BODY))
    assert len(chunks) > 1
    titles = [c.section_title for c in chunks]
    assert "New APIs" in titles and "Rendering" in titles and "Text and fonts" in titles


def test_page_level_chunking_is_rejected_a_fabricated_sentence_stays_localized() -> None:
    """The defect page-level chunking cannot catch: one bad sentence inside an otherwise
    correct page. Section chunking keeps it in its own chunk, not smeared across the page.
    """
    chunks = chunk_document(_document(MULTI_SECTION_BODY))
    rendering = next(c for c in chunks if c.section_title == "Rendering")
    assert "PdfFileSanitization" not in rendering.text
    assert "Dash patterns" in rendering.text


def test_every_chunk_carries_the_documents_provenance_and_trust_tier() -> None:
    chunks = chunk_document(_document(MULTI_SECTION_BODY))
    for chunk in chunks:
        assert chunk.provenance == PROVENANCE
        assert chunk.source_kind == "repo_native_release_notes"
        assert chunk.trust_tier == "high"  # richness="detailed"


def test_a_heading_free_body_still_splits_on_paragraphs_not_one_page_chunk() -> None:
    body = "First paragraph, one topic.\n\nSecond paragraph, a different topic entirely."
    chunks = chunk_document(_document(body))
    assert len(chunks) == 2


def test_an_empty_body_yields_no_chunks() -> None:
    assert chunk_document(_document("")) == []


def test_with_validation_returns_a_new_chunk_with_only_validation_updated() -> None:
    """with_validation has zero callers of its own (dedicated to citation.py's validate_chunk
    via TC-147) and zero direct test coverage until now - this is that coverage.
    """
    chunk = Chunk(
        section_title="Rendering",
        text="Dash patterns on software strokes.",
        source_kind="repo_native_release_notes",
        content_type="release_notes",
        provenance=PROVENANCE,
        trust_tier="high",
        evidence_refs=("aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET#v26.9.0",),
    )
    assert chunk.validation == NOT_CHECKED

    new_validation = ValidationResult(verdict="supported", detail="every claim resolves")
    updated = with_validation(chunk, new_validation)

    assert updated is not chunk
    assert updated.validation == new_validation
    assert updated.section_title == chunk.section_title
    assert updated.text == chunk.text
    assert updated.source_kind == chunk.source_kind
    assert updated.content_type == chunk.content_type
    assert updated.provenance == chunk.provenance
    assert updated.trust_tier == chunk.trust_tier
    assert updated.evidence_refs == chunk.evidence_refs
    # The original chunk itself is untouched - with_validation never mutates in place.
    assert chunk.validation == NOT_CHECKED
