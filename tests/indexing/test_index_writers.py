"""Vector and lexical index writers over the embedded topology TC-004 chose.

``DeterministicEmbeddingProvider`` is the offline, deterministic embedding provider the test
suite uses. It is defined ONLY here, per TC-015's inputs: never importable from a ``src/``
production path (``foss_mcp.indexing.embedding_provider`` ships the interface only, no
concrete provider) - a test double in a production import path is how it quietly becomes the
production behavior.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from foss_mcp.indexing.lexical_index_writer import build_lexical_index, doc_id, query_lexical_index, tokenize
from foss_mcp.indexing.vector_index_writer import build_vector_index, point_id, query_vector_index
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance


class DeterministicEmbeddingProvider:
    """Offline and deterministic: the same text always maps to the same vector, no randomness,
    no model, no network - enough to test indexing and point-id behavior, nothing about
    embedding quality.
    """

    dimension = 8

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append(tuple(byte / 255.0 for byte in digest[: self.dimension]))
        return vectors


PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)


def _chunks() -> list[Chunk]:
    return [
        Chunk(
            "Document",
            "The Document class opens and saves PDF files.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        ),
        Chunk(
            "Page",
            "A Page belongs to a PageCollection.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        ),
    ]


def test_point_id_is_generation_qualified_not_content_only() -> None:
    assert point_id("gen-a", "chunk-1") != point_id("gen-b", "chunk-1")
    assert point_id("gen-a", "chunk-1") == point_id("gen-a", "chunk-1")


def test_doc_id_is_generation_qualified_not_content_only() -> None:
    assert doc_id("gen-a", "chunk-1") != doc_id("gen-b", "chunk-1")


def test_the_same_text_always_embeds_to_the_same_vector() -> None:
    provider = DeterministicEmbeddingProvider()
    assert provider.embed(["hello world"]) == provider.embed(["hello world"])


def test_build_vector_index_produces_one_point_per_chunk_with_a_real_vector() -> None:
    chunks = _chunks()
    provider = DeterministicEmbeddingProvider()
    payload = build_vector_index(chunks, ["c1", "c2"], provider, "gen-1")
    assert payload["dimension"] == provider.dimension
    assert [p["point_id"] for p in payload["points"]] == [point_id("gen-1", "c1"), point_id("gen-1", "c2")]
    assert len(payload["points"][0]["vector"]) == provider.dimension


def test_query_vector_index_ranks_the_closest_point_first() -> None:
    chunks = _chunks()
    provider = DeterministicEmbeddingProvider()
    payload = build_vector_index(chunks, ["c1", "c2"], provider, "gen-1")
    query_vector = provider.embed([chunks[0].text])[0]
    assert query_vector_index(payload, query_vector, top_k=1) == [point_id("gen-1", "c1")]


def test_build_lexical_index_produces_one_document_per_chunk() -> None:
    payload = build_lexical_index(_chunks(), ["c1", "c2"], "gen-1")
    assert set(payload["documents"]) == {doc_id("gen-1", "c1"), doc_id("gen-1", "c2")}


def test_query_lexical_index_finds_the_matching_document() -> None:
    payload = build_lexical_index(_chunks(), ["c1", "c2"], "gen-1")
    assert query_lexical_index(payload, "PageCollection", top_k=1) == [doc_id("gen-1", "c2")]


def test_tokenize_drops_stopwords_but_keeps_distinguishing_terms() -> None:
    tokens = tokenize("how do I add a watermark to a PDF")
    for stopword in ("how", "do", "i", "a", "to"):
        assert stopword not in tokens
    for real_term in ("add", "watermark", "pdf"):
        assert real_term in tokens


def test_natural_language_query_matches_its_real_symbol_but_not_a_stopword_only_sentence() -> None:
    """A real build/query round trip, no mocking: a natural-language question whose only
    distinguishing term is the chunk's real symbol name must match it, while a different
    sentence sharing only stopwords with that chunk's text must return no match at all -
    proving stopword filtering lets an honest empty Miss stay reachable.
    """
    chunks = [
        Chunk(
            "AddWatermarkAnnotation",
            "AddWatermarkAnnotation lets you add a watermark to a PDF document.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        ),
        Chunk(
            "Page",
            "A Page belongs to a PageCollection.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        ),
    ]
    payload = build_lexical_index(chunks, ["c1", "c2"], "gen-1")

    matches = query_lexical_index(payload, "how do I add a watermark to a PDF", top_k=5)
    assert matches
    assert matches[0] == doc_id("gen-1", "c1")

    # Shares only stopwords ("a", "to", "the") with either chunk's text - no distinguishing
    # term in common with anything indexed, so this must score exactly 0.0 for every document
    # and return no match at all.
    no_match = query_lexical_index(payload, "how do I get to the store", top_k=5)
    assert no_match == []
