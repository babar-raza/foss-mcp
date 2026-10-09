"""Citation resolution proves traceability, not correctness (plan 8.10).

OQ-001: the furnished products.aspose.org page for pdf/net once asserted the library
"exposes 805 classes"; foss-mcp's own extraction found 899 TYPES (all kinds combined,
TC-011's original fixture), and the *reduced* fixture's own kind breakdown is a property of
an arbitrary alphabetic prefix, not of the real population, so it cannot corroborate a
narrower claim like "805 classes" either way. TC-119 later regenerated the real furnished
pdf/net fixture from repository-presenter's own sealed candidates, replacing the old
aspose.org-copied prose (including this sentence) with real README-derived content. These
tests use that exact original sentence, preserved as a hardcoded synthetic regression
fixture rather than read live off the fixture, and prove the system refuses to serve it as
a citable fact - it does not have to resolve whether 805 was ever right.

TC-263 (rework attempt 2): the api_surface.json fixture itself was later regenerated again,
with TC-252's centrality-ranked ``reduce_fixture()`` in place of the old alphabetical one.
The real upstream repository has also grown since TC-011's original onboarding, so the real
live ``type_count`` is now 971 (up from 899) - a real, independent fact about the real
repository, unrelated to the selection-algorithm fix.
"""

from __future__ import annotations

import json
from pathlib import Path

from foss_mcp.extraction.claim_id_bridge import (
    Declaration,
    Resolution,
    SemanticSupportVerdict,
    SymbolIndex,
)
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.citation import (
    INSUFFICIENT_EVIDENCE,
    SUPPORTED,
    UNSUPPORTED,
    citable_chunks,
    describe_unresolved_anchors,
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

    This once read the sentence live out of the real furnished pdf/net page's overview
    prose. TC-119 regenerated that fixture from repository-presenter's own sealed
    candidates, and the real overview no longer contains this sentence at all - it now
    holds real README-derived content instead of the old aspose.org copy. OQ-001's real
    historical scenario (a furnished page asserting a precise, uncorroborated numeric
    claim) is preserved here as a permanent, hardcoded synthetic regression fixture -
    mirroring test_the_805_classes_sentence_is_excluded_or_qualified_as_a_whole_chunk's
    own already-correct pattern - using the exact original sentence so the defect class
    keeps being guarded even though the live fixture that once exhibited it has moved on.

    TC-263 (rework attempt 2): ``known_counts["types"]`` is a live read off the committed
    ``api_surface.json`` fixture, which TC-263 regenerated for real against the pinned
    upstream repository - it now reports 971 (real upstream growth since TC-011's original
    899, unrelated to the TC-252 selection-algorithm fix this fixture regeneration proves).
    """
    known_counts = known_counts_from_fixture(_api_surface_fixture())
    assert "classes" not in known_counts
    assert known_counts["types"] == 971

    historical_805_classes_sentence = "The library exposes 805 classes."

    claims = find_numeric_claims(historical_805_classes_sentence)
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
        provenance=provenance
        or Provenance(repository="aspose-slides-foss/Aspose.Slides-FOSS-for-Python", commit="4e63447b" * 5),
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


def test_a_globally_unambiguous_bare_method_name_resolves_in_ordinary_prose() -> None:
    """TC-111: real furnished prose routinely cites a method conversationally by its bare name
    with no ``ClassName.`` prefix. This is an ordinary (non-``example``) chunk, so it has no
    independent compile-verification behind it - the anchor-resolution mechanism itself must
    recognize each bare name is globally unambiguous.

    TC-263 (rework attempt 2): the original names used here (`AddTextAnnotation` etc., all
    declared only by ``AnnotationCollection``) no longer survive the regenerated, centrality-
    ranked fixture's cut - confirmed absent entirely. Verified directly against the
    regenerated fixture: `AddFloatingBox` and `AddContentStream` are each declared by exactly
    one class (``Page``, itself the single highest-centrality type in the whole artifact), and
    `GetOrCreateMetadata` is declared by exactly one class (``Document``) - all three real and
    each globally unambiguous.
    """
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    chunk = _chunk(
        "Call `AddFloatingBox` and `AddContentStream` on a page, then call "
        "`GetOrCreateMetadata` on the document to record it."
    )
    validated = validate_chunk(chunk, index, {})
    assert validated.validation.verdict == SUPPORTED
    assert citable_chunks([validated]) == [validated]


def test_a_bare_method_name_shared_by_two_or_more_classes_still_stays_unresolved() -> None:
    """The generalization must never weaken the existing ambiguity rule: a bare name declared
    identically by two different classes is genuinely ambiguous - which one did the citation
    mean? - so it must correctly stay unresolved, exactly as a fully-qualified anchor with two
    or more overloads already does.
    """
    index = symbol_index_from_api_surface(
        [
            {"name": "AnnotationCollection", "methods": [{"name": "Remove"}]},
            {"name": "PageCollection", "methods": [{"name": "Remove"}]},
        ]
    )
    assert index.unambiguous_qualified_anchor_for_bare_member("Remove") is None
    chunk = validate_chunk(_chunk("Call `Remove` to delete it."), index, {})
    assert chunk.validation.verdict == UNSUPPORTED
    assert citable_chunks([chunk]) == []


def test_unambiguous_qualified_anchor_for_bare_member_returns_the_one_real_qualified_anchor() -> None:
    """Direct unit coverage of the new SymbolIndex method itself, using the real pdf/net
    fixture.

    TC-263 (rework attempt 2): ``AnnotationCollection``/``AddWatermarkAnnotation`` no longer
    survive the regenerated, centrality-ranked fixture's cut. Verified directly against the
    regenerated fixture: exactly one class (``Page``) declares ``AddFloatingBox``.
    """
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    assert index.unambiguous_qualified_anchor_for_bare_member("AddFloatingBox") == "Page.AddFloatingBox"
    assert index.unambiguous_qualified_anchor_for_bare_member("ThisMemberNameDoesNotExistAnywhere") is None


def test_a_fully_qualified_anchor_still_resolves_via_the_original_exact_match_not_the_fallback() -> None:
    """TC-111 must not change ``anchor_resolves``'s own existing exact-match behavior. Re-runs
    pdf/net's own real ``AddWatermarkAnnotation``-shaped chunk (already covered by
    ``test_pdf_net_addwatermarkannotation_example_stays_supported`` below) but with the
    qualified anchor's *own* class deleted from the index and replaced by two decoys that would
    make the new bare-member fallback ambiguous - proving this chunk resolves through the
    original qualified, exact-match path and never through the new bare-name fallback (which,
    if it were mistakenly firing here, would make this fail since the fallback is ambiguous).
    """
    index = symbol_index_from_api_surface(
        [
            {"name": "AnnotationCollection", "methods": [{"name": "AddWatermarkAnnotation"}]},
            {"name": "DecoyOne", "methods": [{"name": "AddWatermarkAnnotation"}]},
            {"name": "DecoyTwo", "methods": [{"name": "AddWatermarkAnnotation"}]},
        ]
    )
    # The bare-name fallback is genuinely ambiguous here (three classes), so it could never
    # resolve `AddWatermarkAnnotation` alone - only the qualified anchor can.
    assert index.unambiguous_qualified_anchor_for_bare_member("AddWatermarkAnnotation") is None
    chunk = validate_chunk(_chunk("Call `AnnotationCollection.AddWatermarkAnnotation` to add it."), index, {})
    assert chunk.validation.verdict == SUPPORTED
    assert citable_chunks([chunk]) == [chunk]


def test_describe_unresolved_anchors_reports_a_genuinely_absent_symbol() -> None:
    """TC-144: an anchor with zero matches anywhere is a genuinely absent symbol - renamed or
    removed, never a typo - reported as UNSUPPORTED with match_count=0, the same verdict the
    chunk itself already carries, now attributed to its actual failing anchor.
    """
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    chunk = validate_chunk(_chunk("See `TotallyMadeUpClassName` for details."), index, {})
    assert chunk.validation.verdict == UNSUPPORTED

    described = describe_unresolved_anchors([chunk], index)

    assert described == [
        ("Overview", Resolution("TotallyMadeUpClassName", SemanticSupportVerdict.UNSUPPORTED, match_count=0))
    ]


def test_describe_unresolved_anchors_reports_a_near_miss_as_partially_supported() -> None:
    """Two declarations registered under the identical anchor - as real overloads of one
    qualified member would be - make the anchor genuinely ambiguous: it still fails
    ``anchor_resolves`` (so the chunk is UNSUPPORTED, exactly like a genuinely absent symbol),
    but ``resolve_anchor`` distinguishes it as a near-miss, PARTIALLY_SUPPORTED with
    match_count=2 - a typo or stale doc is a different operator action than a renamed or
    removed symbol, and that distinction is the entire point of this diagnostic.
    """
    index = SymbolIndex(
        [
            Declaration(anchor="Shape.Remove"),
            Declaration(anchor="Shape.Remove", signature="(int index)"),
        ]
    )
    chunk = validate_chunk(_chunk("Call `Shape.Remove` to delete it."), index, {})
    assert chunk.validation.verdict == UNSUPPORTED

    described = describe_unresolved_anchors([chunk], index)

    assert described == [
        ("Overview", Resolution("Shape.Remove", SemanticSupportVerdict.PARTIALLY_SUPPORTED, match_count=2))
    ]


def test_describe_unresolved_anchors_never_reports_a_supported_or_insufficient_evidence_chunk() -> None:
    """A chunk whose verdict is SUPPORTED, or INSUFFICIENT_EVIDENCE (a numeric-only problem,
    with no failing anchor at all), must never appear in describe_unresolved_anchors' output -
    it exists to explain an UNSUPPORTED exclusion, nothing else.
    """
    fixture = _api_surface_fixture()
    index = symbol_index_from_api_surface(fixture["types"])
    known_counts = known_counts_from_fixture(fixture)
    supported = validate_chunk(_chunk(f"See `{fixture['types'][0]['name']}`."), index, {})
    insufficient = validate_chunk(_chunk("The library exposes 805 classes."), index, known_counts)
    assert supported.validation.verdict == SUPPORTED
    assert insufficient.validation.verdict == INSUFFICIENT_EVIDENCE

    assert describe_unresolved_anchors([supported, insufficient], index) == []


def test_describe_unresolved_anchors_skips_a_compile_verified_example_chunk_entirely() -> None:
    """Mirrors validate_chunk's own exemption exactly: a genuinely compile-verified example
    chunk never runs the anchor check at all, even if it happens to be UNSUPPORTED for an
    unrelated numeric-claim reason - its bare-name method citations are not a real anchor
    failure and must never be reported as one.
    """
    fixture = _api_surface_fixture()
    known_counts = known_counts_from_fixture(fixture)
    chunk = _example_chunk("This one example is one of the library's 900 classes.")
    validated = validate_chunk(chunk, symbol_index_from_api_surface([]), known_counts)
    assert validated.validation.verdict != SUPPORTED

    assert describe_unresolved_anchors([validated], symbol_index_from_api_surface([])) == []


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
