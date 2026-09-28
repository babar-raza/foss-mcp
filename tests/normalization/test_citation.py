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


def _example_chunk(text: str, provenance: Provenance | None = None) -> Chunk:
    """Shaped exactly like infra/build_chunks.py's ``_build_verified_example_chunks`` output:
    ``content_type="example"``, the real, structured signal the fix keys off - never a
    body carrying the ``Kind: verified_example`` string as a substitute.
    """
    return Chunk(
        section_title="Create a Presentation and Add a Shape",
        text=text,
        source_kind="furnished",
        content_type="example",
        provenance=provenance or Provenance(repository="aspose-slides-foss/Aspose.Slides-FOSS-for-Python", commit="4e63447b" * 5),
        trust_tier="medium",
    )


def test_a_compile_verified_example_with_a_bare_method_citation_is_supported() -> None:
    """The real defect TC-101's worker found: slides/python's real 'Create a Presentation and
    Add a Shape' example genuinely compiles, but its own furnished-content description cites
    `add_auto_shape()` and `add_text_frame()` as bare method names (no `ClassName.` prefix),
    which symbol_index_from_api_surface never registers. This chunk's code was already
    independently, more strongly verified by real compilation, so its description must not be
    held to the citation-anchor bar meant for raw, unverified API documentation prose.
    """
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    chunk = _example_chunk(
        "Use the context manager to ensure the PPTX is always closed. `add_auto_shape()` "
        "takes a `ShapeType` enum, then x/y position and width/height in points — call "
        "`add_text_frame()` on the shape to attach a text frame."
    )
    validated = validate_chunk(chunk, index, {})
    assert validated.validation.verdict == SUPPORTED
    assert citable_chunks([validated]) == [validated]


def test_a_non_example_chunk_with_the_same_bare_method_citation_is_still_unsupported() -> None:
    """The fix must not weaken citation validation for anything other than a genuinely
    compile-verified example chunk - the same bare-name citation in an ordinary
    (non-``example``) chunk is still correctly excluded.
    """
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    chunk = _chunk("Call `add_auto_shape()` to insert a shape.")
    validated = validate_chunk(chunk, index, {})
    assert validated.validation.verdict == UNSUPPORTED
    assert citable_chunks([validated]) == []


def test_renaming_the_example_guard_breaks_the_example_exemption() -> None:
    """Guards the exact, load-bearing shape the negative control mutates: a chunk shaped
    exactly like the compile-verified example must resolve through
    ``_is_real_compile_verified_example`` by name, not by some other proxy - if that
    function's real name is ever renamed away, this chunk's citation-anchor check regresses
    to running unconditionally and the chunk goes back to UNSUPPORTED.
    """
    from foss_mcp.normalization import citation as citation_module

    assert hasattr(citation_module, "_is_real_compile_verified_example")

    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    chunk = _example_chunk("Call `add_auto_shape()` to insert a shape.")
    assert citation_module._is_real_compile_verified_example(chunk) is True
    validated = validate_chunk(chunk, index, {})
    assert validated.validation.verdict == SUPPORTED


def test_an_example_chunk_with_a_contradicting_numeric_claim_is_still_unsupported() -> None:
    """The numeric-claim check is orthogonal to compile-verification (it guards a
    library-wide surface count, not anything about one example's code) and stays on for
    every chunk kind, including ``example`` - compiling one example says nothing about
    whether an unrelated count in its prose is accurate.
    """
    fixture = _api_surface_fixture()
    known_counts = known_counts_from_fixture(fixture)
    chunk = _example_chunk("This one example is one of the library's 900 classes.")
    validated = validate_chunk(chunk, symbol_index_from_api_surface([]), known_counts)
    assert validated.validation.verdict != SUPPORTED
    assert citable_chunks([validated]) == []


def test_pdf_net_addwatermarkannotation_example_stays_supported() -> None:
    """A general fix, not a slides/python-specific patch: pdf/net's own real, already-accepted
    AddWatermarkAnnotation example description happens not to cite any bare method name, so it
    was SUPPORTED before this fix and must stay SUPPORTED after it, for the same reason
    (every claim resolves) rather than because of the new exemption.
    """
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    chunk = _example_chunk(
        "Open the source PDF with `Document`, then call `Document.Pages` to reach the first "
        "page before adding the watermark annotation."
    )
    validated = validate_chunk(chunk, index, {})
    assert validated.validation.verdict == SUPPORTED
    assert citable_chunks([validated]) == [validated]
