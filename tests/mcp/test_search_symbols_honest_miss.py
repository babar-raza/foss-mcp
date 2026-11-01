"""search_symbols is an honest miss for an absent symbol (TC-199, G2/REQ-G2-047).

A match must be an exact symbol hit: the query equals a result's full FQN or its final dotted
segment, compared case-insensitively. A lexical rank alone is never a match. An absent symbol
that merely shares a token with a real one must come back as a Miss, with ``suggestions`` as a
"did you mean" hint. Offline: the index is built in memory from real chunks, never from disk.
"""

from __future__ import annotations

from types import SimpleNamespace

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.search_symbols import MATCH_PARTIAL, Miss, SymbolMatch, search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

PDF_NET_SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc199-honest-miss"

# A tiny, known symbol set. Every real FQN here is referenced by the tests below.
_KNOWN_SYMBOLS = (
    ("PdfDocument.AddWatermarkAnnotation", "Method", "Adds a watermark annotation to the page."),
    ("PdfDocument.Merge", "Method", "Merges several documents into one."),
    ("Annotation", "Class", "Serves as the abstract base for every markup element."),
    ("Aspose.Pdf.AFRelationship", "Enum", "Describes how an embedded file relates to the document."),
)

# A C++ product writes its FQNs with the scope operator, not dots (TC-210).
_CPP_SYMBOLS = (
    ("Aspose::Pdf::AFRelationship", "Enum", "Describes how an embedded file relates to the document."),
)


class _InMemoryGenerationStore:
    """Stands in for GenerationManifestStore: one active generation, held entirely in memory."""

    def __init__(self, symbols: tuple[tuple[str, str, str], ...] = _KNOWN_SYMBOLS) -> None:
        chunks = []
        for fqn, kind, prose in symbols:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="tc199"),
                evidence_refs=(),
                title="pdf/net API surface",
                body=f"## {fqn}\n\nFQN: {fqn}\nKind: {kind}\n{prose}\n",
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, _GENERATION_ID)
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _matched_fqns(result: object) -> list[str]:
    assert isinstance(result, list), f"expected a list of matches, got {result!r}"
    assert all(isinstance(match, SymbolMatch) for match in result)
    return [match.text for match in result]


def test_an_exact_fqn_returns_a_match() -> None:
    result = search_symbols(_InMemoryGenerationStore(), PDF_NET_SCOPE, "PdfDocument.AddWatermarkAnnotation")

    texts = _matched_fqns(result)
    assert any("FQN: PdfDocument.AddWatermarkAnnotation" in text for text in texts)


def test_an_exact_final_segment_in_another_case_returns_a_match() -> None:
    result = search_symbols(_InMemoryGenerationStore(), PDF_NET_SCOPE, "addwatermarkannotation")

    texts = _matched_fqns(result)
    assert any("FQN: PdfDocument.AddWatermarkAnnotation" in text for text in texts)


def test_an_absent_symbol_sharing_a_token_with_a_real_one_is_a_miss_not_a_match() -> None:
    # "PdfDocument" is a real token in two indexed FQNs, but "PdfDocument.Rotate" is not one of them.
    result = search_symbols(_InMemoryGenerationStore(), PDF_NET_SCOPE, "PdfDocument.Rotate")

    assert isinstance(result, Miss)
    assert result.scope == PDF_NET_SCOPE
    assert result.query == "PdfDocument.Rotate"
    assert result.reason == "no symbol matches 'PdfDocument.Rotate'"


def test_the_miss_for_an_absent_symbol_suggests_a_close_real_name() -> None:
    # A single CHANGED (never dropped) trailing character of a real FQN: a miss, with that FQN as
    # the hint. TC-654 (G3/REQ-G3-011): deliberately a same-length substitution, not a truncation -
    # a truncated last word is always a genuine prefix of the real word, which TC-654's own new
    # tier-4 now admits as a (weak) match, not a miss (see test_a_partial_name_is_not_a_match
    # below); a substituted last character is never a prefix of the real word (same length, one
    # character wrong), so this probe still stays an honest miss with a "did you mean" hint.
    result = search_symbols(_InMemoryGenerationStore(), PDF_NET_SCOPE, "PdfDocument.AddWatermarkAnnotatiom")

    assert isinstance(result, Miss)
    assert result.suggestions == ("PdfDocument.AddWatermarkAnnotation",)


def test_a_whole_word_query_finds_the_symbol_that_contains_it() -> None:
    result = search_symbols(_InMemoryGenerationStore(), PDF_NET_SCOPE, "watermark")

    texts = _matched_fqns(result)
    assert any("FQN: PdfDocument.AddWatermarkAnnotation" in text for text in texts)


def test_an_absent_name_sharing_no_word_with_a_real_one_is_a_miss_never_a_match() -> None:
    result = search_symbols(_InMemoryGenerationStore(), PDF_NET_SCOPE, "Tc197AbsentSymbolProbe")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'Tc197AbsentSymbolProbe'"
    # Any suggestion must be a real FQN published in this same generation, never a fabricated one.
    assert isinstance(result.suggestions, tuple)
    assert set(result.suggestions) <= {fqn for fqn, _, _ in _KNOWN_SYMBOLS}


def test_a_partial_name_now_finds_its_real_word_at_the_new_weakest_tier() -> None:
    # TC-654 (G3/REQ-G3-011): Q12's deliberate, reviewed OVERTURNING of the previous "no prefix
    # tier" design - a RAISED floor, recall went up, nothing that worked before stopped working.
    # "Watermar" and "Annotat" are truncated words: each is a genuine (>= 3 char) prefix of a real
    # word of a real FQN, never a whole word itself - they used to be honest misses (TC-199/TC-516)
    # because this tool had no tier weak enough to admit a fragment; they are now tier-4
    # (prefix) hits, with the weakest, most honestly-labelled match_quality, MATCH_PARTIAL, never
    # MATCH_EXACT. ("PdfDocument" and "AddWatermark" are already whole words of a real FQN, so they
    # stay stronger, pre-existing matches - unaffected by this card.)
    store = _InMemoryGenerationStore()

    watermar = search_symbols(store, PDF_NET_SCOPE, "Watermar")
    texts = _matched_fqns(watermar)
    assert any("FQN: PdfDocument.AddWatermarkAnnotation" in text for text in texts)
    assert all(match.match_quality == MATCH_PARTIAL for match in watermar)  # type: ignore[union-attr]

    annotat = search_symbols(store, PDF_NET_SCOPE, "Annotat")
    texts = _matched_fqns(annotat)
    # "annotat" is a genuine prefix of "annotation", a real word of BOTH remaining FQNs.
    assert any("FQN: Annotation" in text for text in texts)
    assert any("FQN: PdfDocument.AddWatermarkAnnotation" in text for text in texts)
    assert all(match.match_quality == MATCH_PARTIAL for match in annotat)  # type: ignore[union-attr]


def _cpp_result(query: str) -> object:
    return search_symbols(_InMemoryGenerationStore(_CPP_SYMBOLS), PDF_NET_SCOPE, query)


def _assert_cpp_hit(query: str) -> None:
    texts = _matched_fqns(_cpp_result(query))
    assert any("FQN: Aspose::Pdf::AFRelationship" in text for text in texts)


def test_a_cpp_symbol_is_found_by_its_short_name() -> None:
    _assert_cpp_hit("AFRelationship")


def test_a_cpp_short_name_is_case_insensitive() -> None:
    _assert_cpp_hit("afrelationship")


def test_a_cpp_symbol_is_found_by_its_full_scoped_name() -> None:
    _assert_cpp_hit("Aspose::Pdf::AFRelationship")


def test_a_cpp_whole_word_of_the_short_name_is_a_hit() -> None:
    _assert_cpp_hit("Relationship")


def test_a_cpp_partial_word_now_finds_its_real_word_at_the_new_weakest_tier() -> None:
    # TC-654 (G3/REQ-G3-011): the same RAISED floor as test_a_partial_name_now_finds_its_real_word
    # above, for a ``::``-scoped C++ FQN. "AFRelation" is "af" (a real whole word of
    # ``Aspose::Pdf::AFRelationship``) plus "relation" - not itself a whole word, but a genuine
    # >= 3 char prefix of the real word "relationship" - so every word of the query is now
    # explained (whole or prefix) and this is a tier-4 hit, MATCH_PARTIAL.
    result = _cpp_result("AFRelation")

    texts = _matched_fqns(result)
    assert any("FQN: Aspose::Pdf::AFRelationship" in text for text in texts)
    assert all(match.match_quality == MATCH_PARTIAL for match in result)  # type: ignore[union-attr]


def test_an_absent_cpp_scoped_name_is_a_miss() -> None:
    result = _cpp_result("Aspose::Pdf::Nonexistent")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'Aspose::Pdf::Nonexistent'"


def test_a_dotted_fqn_matches_a_whole_word_exactly_and_a_prefix_only_partially() -> None:
    # TC-654 (G3/REQ-G3-011): "Watermar" is no longer a miss (see
    # test_a_partial_name_now_finds_its_real_word_at_the_new_weakest_tier) - it is now this tool's
    # own weakest, most honestly-labelled tier-4 hit, MATCH_PARTIAL. The whole word "watermark"
    # is untouched: still a stronger, pre-existing bag-of-words hit, MATCH_EXACT.
    store = _InMemoryGenerationStore()

    prefix_hit = search_symbols(store, PDF_NET_SCOPE, "Watermar")
    assert all(match.match_quality == MATCH_PARTIAL for match in prefix_hit)  # type: ignore[union-attr]
    texts = _matched_fqns(prefix_hit)
    assert any("FQN: PdfDocument.AddWatermarkAnnotation" in text for text in texts)

    whole_word_hit = search_symbols(store, PDF_NET_SCOPE, "watermark")
    texts = _matched_fqns(whole_word_hit)
    assert any("FQN: PdfDocument.AddWatermarkAnnotation" in text for text in texts)
    assert all(match.match_quality != MATCH_PARTIAL for match in whole_word_hit)  # type: ignore[union-attr]
