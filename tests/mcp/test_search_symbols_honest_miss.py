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
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols
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


class _InMemoryGenerationStore:
    """Stands in for GenerationManifestStore: one active generation, held entirely in memory."""

    def __init__(self) -> None:
        chunks = []
        for fqn, kind, prose in _KNOWN_SYMBOLS:
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
    # A single dropped trailing character of a real FQN: a miss, with that FQN as the hint.
    result = search_symbols(_InMemoryGenerationStore(), PDF_NET_SCOPE, "PdfDocument.AddWatermarkAnnotatio")

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


def test_a_partial_name_is_not_a_match() -> None:
    store = _InMemoryGenerationStore()

    # "Watermar" and "Annotat" are truncated words: each is a prefix of a whole word of a real FQN,
    # never a whole word itself, so neither is an exact symbol hit and both must be misses.
    # ("PdfDocument" and "AddWatermark" are whole words of a real FQN, so they are matches.)
    for partial in ("Watermar", "Annotat"):
        result = search_symbols(store, PDF_NET_SCOPE, partial)
        assert isinstance(result, Miss), f"partial name {partial!r} must not be a match"
