"""TC-632 (QV1): an opt-in, additive documentation-chunk vector scope, mirroring exactly how
TC-331/TC-340 added the examples-only vector scope ``find_examples`` uses.

This card adds NO new query-time retrieval behaviour (that is QV2/TC-633's job) - it only makes
it possible for a generation to carry a second, separate set of embedded vectors for
documentation-type chunks, built the exact same way the existing example-chunk vectors are built
today. These tests pin, against the real ``build_vector_index``/``publish_generation`` and a
RECORDING embedding provider:

1. ``is_doc_chunk`` matches the exact ``"FQN: Doc: "`` prefix only.
2. ``build_vector_index(..., include=is_doc_chunk)`` selects FIRST and embeds only the selected
   texts (never embed-then-discard), same proof style as the example scope's own test.
3. ``publish_generation`` called with BOTH ``vector_chunk_filter`` and ``doc_vector_chunk_filter``
   on a mixed chunk list produces two independent, non-cross-contaminating vector indexes.
4. ``publish_generation`` called with ``doc_vector_chunk_filter=None`` (the default) produces a
   payload with no ``"vector_index_docs"`` key at all - zero behaviour change for every existing
   caller.
5. ``DOC_TITLE_PREFIX`` is pinned equal to ``search_docs``' own private copy.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import content_chunk_id, publish_generation
from foss_mcp.indexing.vector_index_writer import (
    DOC_TITLE_PREFIX,
    build_vector_index,
    is_doc_chunk,
    is_example_chunk,
    point_id,
)
from foss_mcp.mcp.tools import search_docs
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)

DOC_TEXT_ONE = "FQN: Doc: Getting Started\nInstall the package and open your first document."
DOC_TEXT_TWO = "FQN: Doc: Licensing\nApply a license before calling any API."
EXAMPLE_TEXT_ONE = "`Document.Open` opens a file.\nExample:\n```csharp\nvar d = Document.Open(path);\n```"
PLAIN_TEXT_ONE = "The Document class opens and saves PDF files."
PLAIN_TEXT_TWO = "This sentence mentions a Doc in passing, not at the start."


def _chunk(section_title: str, text: str) -> Chunk:
    return Chunk(section_title, text, "self_extracted", "api_surface", PROVENANCE, "highest", (), NOT_CHECKED)


class RecordingEmbeddingProvider:
    """A deterministic provider that records every ``embed`` call's exact input."""

    def __init__(self) -> None:
        self._inner = DeterministicEmbeddingProvider()
        self.dimension = self._inner.dimension
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        self.calls.append(list(texts))
        return self._inner.embed(texts)


def _mixed_chunks() -> list[Chunk]:
    return [
        _chunk("Document", PLAIN_TEXT_ONE),
        _chunk("Getting Started", DOC_TEXT_ONE),
        _chunk("Document.Open", EXAMPLE_TEXT_ONE),
        _chunk("Licensing", DOC_TEXT_TWO),
        _chunk("Misc", PLAIN_TEXT_TWO),
    ]


def test_is_doc_chunk_requires_the_exact_prefix_at_the_start() -> None:
    assert is_doc_chunk(_chunk("Getting Started", DOC_TEXT_ONE))
    assert is_doc_chunk(_chunk("Licensing", DOC_TEXT_TWO))
    # a mid-text occurrence of the word "Doc" is not the prefix.
    assert not is_doc_chunk(_chunk("A", PLAIN_TEXT_TWO))
    assert not is_doc_chunk(_chunk("B", "This mentions FQN: Doc: mid-sentence, not at the start."))
    # a different FQN kind (example marker) is not the doc prefix either.
    assert not is_doc_chunk(_chunk("C", EXAMPLE_TEXT_ONE))
    assert not is_doc_chunk(_chunk("D", "FQN: Symbol: Document.Open"))
    assert not is_doc_chunk(_chunk("E", ""))


def test_build_vector_index_with_doc_filter_selects_only_doc_chunks_and_embeds_only_their_texts() -> None:
    chunks = _mixed_chunks()
    chunk_ids = [f"c{i}" for i in range(len(chunks))]
    provider = RecordingEmbeddingProvider()

    payload = build_vector_index(chunks, chunk_ids, provider, "gen-doc-1", include=is_doc_chunk)

    assert payload["generation_id"] == "gen-doc-1"
    assert payload["dimension"] == provider.dimension
    assert [p["chunk_id"] for p in payload["points"]] == ["c1", "c3"]
    assert [p["point_id"] for p in payload["points"]] == [
        point_id("gen-doc-1", "c1"),
        point_id("gen-doc-1", "c3"),
    ]
    assert [p["text"] for p in payload["points"]] == [DOC_TEXT_ONE, DOC_TEXT_TWO]
    # select-then-embed: exactly ONE embed call, with ONLY the doc texts.
    assert provider.calls == [[DOC_TEXT_ONE, DOC_TEXT_TWO]]
    flat = [text for call in provider.calls for text in call]
    for non_doc in (PLAIN_TEXT_ONE, EXAMPLE_TEXT_ONE, PLAIN_TEXT_TWO):
        assert non_doc not in flat
    by_chunk_id = {p["chunk_id"]: tuple(p["vector"]) for p in payload["points"]}
    reference = DeterministicEmbeddingProvider()
    assert by_chunk_id["c1"] == reference.embed([DOC_TEXT_ONE])[0]
    assert by_chunk_id["c3"] == reference.embed([DOC_TEXT_TWO])[0]


def test_build_vector_index_with_no_doc_chunks_never_calls_embed_and_gives_empty_points() -> None:
    chunks = [_chunk("Document", PLAIN_TEXT_ONE), _chunk("Example", EXAMPLE_TEXT_ONE)]
    provider = RecordingEmbeddingProvider()

    payload = build_vector_index(chunks, ["c0", "c1"], provider, "gen-doc-2", include=is_doc_chunk)

    assert provider.calls == [], "embed must not be called at all when nothing is selected"
    assert payload["points"] == []
    assert payload["dimension"] == provider.dimension
    assert payload["generation_id"] == "gen-doc-2"


def test_publish_generation_with_both_filters_produces_two_non_cross_contaminating_indexes(
    tmp_path: Path,
) -> None:
    chunks = _mixed_chunks()
    store = GenerationManifestStore(tmp_path / "manifests")
    scope = "pdf::net::self_extracted"
    provider = RecordingEmbeddingProvider()
    lease = store.acquire_lease(scope, "test", "pending")

    generation_id = publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=store.read_active(scope),
        chunks=chunks,
        embedding_provider=provider,
        lease=lease,
        vector_chunk_filter=is_example_chunk,
        doc_vector_chunk_filter=is_doc_chunk,
    )

    manifest = store.read_generation(scope, generation_id)
    example_chunk_ids = {content_chunk_id(chunks[2])}
    doc_chunk_ids = {content_chunk_id(chunks[1]), content_chunk_id(chunks[3])}

    vector_index_chunk_ids = {p["chunk_id"] for p in manifest.payload["vector_index"]["points"]}
    vector_index_docs_chunk_ids = {p["chunk_id"] for p in manifest.payload["vector_index_docs"]["points"]}

    assert vector_index_chunk_ids == example_chunk_ids
    assert vector_index_docs_chunk_ids == doc_chunk_ids
    assert vector_index_chunk_ids.isdisjoint(vector_index_docs_chunk_ids)
    # the lexical index is untouched: it still holds every chunk.
    lexical_texts = {doc["text"] for doc in manifest.payload["lexical_index"]["documents"].values()}
    assert lexical_texts == {chunk.text for chunk in chunks}


def test_publish_generation_with_doc_filter_none_omits_the_vector_index_docs_key_entirely(
    tmp_path: Path,
) -> None:
    chunks = _mixed_chunks()
    store = GenerationManifestStore(tmp_path / "manifests")
    scope = "pdf::net::self_extracted"
    provider = RecordingEmbeddingProvider()
    lease = store.acquire_lease(scope, "test", "pending")

    generation_id = publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=store.read_active(scope),
        chunks=chunks,
        embedding_provider=provider,
        lease=lease,
        vector_chunk_filter=is_example_chunk,
    )

    manifest = store.read_generation(scope, generation_id)
    assert "vector_index_docs" not in manifest.payload
    assert "vector_index" in manifest.payload


def test_doc_title_prefix_is_pinned_to_search_docs_own_copy() -> None:
    assert DOC_TITLE_PREFIX == search_docs._DOC_TITLE_PREFIX == "FQN: Doc: "
