"""Citation resolution proves traceability, not correctness (plan 8.10).

OQ-001: the furnished products.aspose.org page for pdf/net asserts the library "exposes 805
classes"; foss-mcp's own extraction found 899 TYPES (all kinds combined, TC-011's fixture), and
the *reduced* fixture's own kind breakdown is a property of an arbitrary alphabetic prefix, not
of the real population, so it cannot corroborate a narrower claim like "805 classes" either way.
These tests use that exact real sentence, not a synthetic stand-in, and prove the system refuses
to serve it as a citable fact - it does not have to resolve whether 805 is right.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.citation import (
    INSUFFICIENT_EVIDENCE,
    SUPPORTED,
    UNSUPPORTED,
    citable_chunks,
    find_numeric_claims,
    find_symbol_anchors,
    known_counts_from_fixture,
    symbol_index_from_api_surface,
    validate_chunk,
    validate_document,
    validate_numeric_claim,
)
from foss_mcp.normalization.document_schema import Provenance

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"


def _api_surface_fixture() -> dict:
    return json.loads((FIXTURE_ROOT / "pdf_net" / "api_surface.json").read_text(encoding="utf-8"))


def _furnished_overview_text() -> str:
    """The real furnished pdf/net page's overview prose - contains the real "805 classes"
    sentence OQ-001 names. Parsed with ``safe_load_all`` because the Hugo page bundle's
    trailing ``---`` starts a second, empty YAML document.
    """
    text = (FIXTURE_ROOT / "furnished" / "pdf_net" / "pages" / "_index.md").read_text(encoding="utf-8")
    page = next(yaml.safe_load_all(text))
    return page["overview"]["content"]


def _chunk(text: str, provenance: Provenance | None = None) -> Chunk:
    return Chunk(
        section_title="Overview",
        text=text,
        source_kind="furnished",
        content_type="product_page",
        provenance=provenance or Provenance(repository="Aspose/aspose.org", commit="30b4199e" * 5),
        trust_tier="medium",
    )


def test_a_resolving_anchor_is_supported() -> None:
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    resolvable = fixture["types"][0]["name"]
    chunk = validate_chunk(_chunk(f"See `{resolvable}` for details."), index, {})
    assert chunk.validation.verdict == SUPPORTED


def test_a_non_resolving_anchor_is_excluded_or_qualified() -> None:
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    chunk = validate_chunk(_chunk("See `TotallyMadeUpClassName` for details."), index, {})
    assert chunk.validation.verdict == UNSUPPORTED
    assert citable_chunks([chunk]) == []


def test_a_numeric_claim_matching_the_known_count_is_supported() -> None:
    result = validate_numeric_claim(899, "types", {"types": 899})
    assert result.verdict == SUPPORTED


def test_a_numeric_claim_contradicting_the_known_count_is_unsupported() -> None:
    result = validate_numeric_claim(900, "types", {"types": 899})
    assert result.verdict == UNSUPPORTED


def test_oq_001_the_real_805_classes_claim_has_no_confident_corroborating_count() -> None:
    """The exact defect OQ-001 names: 'classes' is not among what a reduced fixture can
    confidently vouch for (see known_counts_from_fixture), so the real claim is
    insufficient_evidence - never silently treated as either confirmed or refuted.
    """
    known_counts = known_counts_from_fixture(_api_surface_fixture())
    assert "classes" not in known_counts
    assert known_counts["types"] == 899

    claims = find_numeric_claims(_furnished_overview_text())
    assert (805, "classes") in claims

    result = validate_numeric_claim(805, "classes", known_counts)
    assert result.verdict == INSUFFICIENT_EVIDENCE


def test_the_805_classes_sentence_is_excluded_or_qualified_as_a_whole_chunk() -> None:
    """Isolated to the numeric claim alone (no symbol anchors), so this is unambiguously the
    numeric-claim rule doing the work, not an unrelated anchor failure.
    """
    known_counts = known_counts_from_fixture(_api_surface_fixture())
    chunk = validate_chunk(
        _chunk("The library exposes 805 classes."),
        symbol_index_from_api_surface([]),
        known_counts,
    )
    assert chunk.validation.verdict == INSUFFICIENT_EVIDENCE
    assert citable_chunks([chunk]) == []


def test_a_complete_untruncated_fixture_would_let_classes_be_corroborated() -> None:
    """known_counts_from_fixture is forward-compatible: once a fixture is not reduced, its own
    kind breakdown becomes a confident count - the gap is the reduction, not the mechanism.
    """
    complete_fixture = {
        "truncated": False,
        "type_count": 2,
        "types": [{"kind": "class_declaration"}, {"kind": "class_declaration"}],
    }
    assert known_counts_from_fixture(complete_fixture) == {"classes": 2, "types": 2}


def test_find_symbol_anchors_strips_a_call_suffix_and_skips_non_identifier_spans() -> None:
    text = "Use `Document.Open(path)` or `Document()`, not a shell span like `pip install x`."
    assert find_symbol_anchors(text) == ["Document.Open", "Document"]


def test_validate_document_sets_every_chunk_and_citable_chunks_drops_the_unsupported() -> None:
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    good = _chunk(f"See `{fixture['types'][0]['name']}`.")
    bad = _chunk("See `NotARealClass`.")
    validated = validate_document([good, bad], index, {})
    assert [c.validation.verdict for c in validated] == [SUPPORTED, UNSUPPORTED]
    assert citable_chunks(validated) == [validated[0]]
