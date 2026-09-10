"""The normalized document envelope: every required field round-trips, and trust tier is
derived consistently from where a document came from.
"""

from __future__ import annotations

from foss_mcp.normalization.document_schema import (
    NormalizedDocument,
    Provenance,
    SourceKind,
    TrustTier,
    ValidationResult,
    from_dict,
    make_document,
    to_dict,
    trust_tier_for,
)


def test_every_required_field_round_trips_through_to_dict_and_from_dict() -> None:
    original = NormalizedDocument(
        source_kind="furnished",
        content_type="product_page",
        provenance=Provenance(repository="Aspose/aspose.org", commit="30b4199e" * 5, path="a/b.md"),
        evidence_refs=("Aspose/aspose.org@30b4199e:a/b.md",),
        validation=ValidationResult(verdict="supported", detail="every claim resolves"),
        trust_tier="medium",
        title="pdf/net",
        body="## Overview\n\ntext",
    )
    restored = from_dict(to_dict(original))
    assert restored == original
    # Named individually per closeout ("every schema field round-trips"), not only via equality.
    data = to_dict(original)
    assert data["source_kind"] == "furnished"
    assert data["content_type"] == "product_page"
    assert data["provenance"] == {"repository": "Aspose/aspose.org", "commit": "30b4199e" * 5, "path": "a/b.md"}
    assert data["evidence_refs"] == ["Aspose/aspose.org@30b4199e:a/b.md"]
    assert data["validation"] == {"verdict": "supported", "detail": "every claim resolves"}


def test_trust_tier_is_static_by_source_for_every_kind_but_release_notes() -> None:
    assert trust_tier_for(SourceKind.SELF_EXTRACTED) == TrustTier.HIGHEST
    assert trust_tier_for(SourceKind.REPO_NATIVE_MANIFEST) == TrustTier.HIGH
    assert trust_tier_for(SourceKind.FURNISHED) == TrustTier.MEDIUM


def test_release_note_trust_tier_varies_with_its_own_richness() -> None:
    """'Repo-native manifests are high but release notes are variable' - variable BY richness."""
    assert trust_tier_for(SourceKind.REPO_NATIVE_RELEASE_NOTES, richness="detailed") == TrustTier.HIGH
    assert trust_tier_for(SourceKind.REPO_NATIVE_RELEASE_NOTES, richness="templated") == TrustTier.VARIABLE
    assert trust_tier_for(SourceKind.REPO_NATIVE_RELEASE_NOTES, richness="none") == TrustTier.VARIABLE


def test_make_document_derives_trust_tier_so_a_caller_cannot_set_it_inconsistently() -> None:
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5),
        evidence_refs=(),
        title="Widget",
        body="kind: class_declaration",
    )
    assert doc.trust_tier == TrustTier.HIGHEST.value

    release_doc = make_document(
        source_kind=SourceKind.REPO_NATIVE_RELEASE_NOTES,
        content_type="release_notes",
        provenance=Provenance(repository="aspose-font-foss/Aspose.Font-FOSS-for-Python", commit="x"),
        evidence_refs=(),
        richness="templated",
    )
    assert release_doc.trust_tier == TrustTier.VARIABLE.value
