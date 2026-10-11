"""TC-340: an opt-in vector scope - embed and store only example-bearing chunks.

The vector index has exactly one consumer, ``find_examples``' fallback, and that keeps only
points whose text contains the ``Example:`` marker. Embedding every chunk with the 4096-dim
gateway made a pilot's generation file 7x larger and its probes time out, so ``build_vector_index``
gained an optional ``include`` predicate. These tests pin, against the real
``build_vector_index`` and a RECORDING embedding provider:

1. ``include=None`` is exactly today's behavior (one point per chunk).
2. A predicate selects FIRST and embeds only the selected texts (never embed-then-discard).
3. Nothing selected means ``embed`` is never called and ``points`` is ``[]``.
4. ``EXAMPLE_MARKER`` is pinned equal to ``find_examples``' own private copy.
5. ``is_example_chunk`` matches the exact marker only.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import content_chunk_id, publish_generation
from foss_mcp.indexing.vector_index_writer import (
    EXAMPLE_MARKER,
    build_vector_index,
    is_example_chunk,
    point_id,
)
from foss_mcp.mcp.tools import find_examples
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)

EXAMPLE_TEXT_ONE = "`Document.Open` opens a file.\nExample:\n```csharp\nvar d = Document.Open(path);\n```"
EXAMPLE_TEXT_TWO = "`Page.Rotate` rotates a page.\nExample:\n```csharp\npage.Rotate(90);\n```"
PLAIN_TEXT_ONE = "The Document class opens and saves PDF files."
PLAIN_TEXT_TWO = "A Page belongs to a PageCollection."
PLAIN_TEXT_THREE = "Nothing here has a sample block."


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
        _chunk("Document.Open", EXAMPLE_TEXT_ONE),
        _chunk("Page", PLAIN_TEXT_TWO),
        _chunk("Page.Rotate", EXAMPLE_TEXT_TWO),
        _chunk("Misc", PLAIN_TEXT_THREE),
    ]


def test_default_scope_yields_exactly_one_point_per_chunk_identical_to_today() -> None:
    chunks = _mixed_chunks()
    chunk_ids = [f"c{i}" for i in range(len(chunks))]
    default_provider = RecordingEmbeddingProvider()
    explicit_none_provider = RecordingEmbeddingProvider()

    default_payload = build_vector_index(chunks, chunk_ids, default_provider, "gen-1")
    explicit_none_payload = build_vector_index(
        chunks, chunk_ids, explicit_none_provider, "gen-1", include=None
    )

    assert default_payload == explicit_none_payload
    assert default_payload["generation_id"] == "gen-1"
    assert default_payload["dimension"] == default_provider.dimension
    assert [p["chunk_id"] for p in default_payload["points"]] == chunk_ids
    assert [p["point_id"] for p in default_payload["points"]] == [point_id("gen-1", c) for c in chunk_ids]
    assert [p["text"] for p in default_payload["points"]] == [chunk.text for chunk in chunks]
    # the unfiltered path embeds every chunk in ONE call, in chunk order - exactly as before.
    assert default_provider.calls == [[chunk.text for chunk in chunks]]
    expected_vectors = DeterministicEmbeddingProvider().embed([chunk.text for chunk in chunks])
    assert [tuple(p["vector"]) for p in default_payload["points"]] == expected_vectors


def test_example_scope_keeps_only_marked_chunks_and_embeds_only_their_texts() -> None:
    chunks = _mixed_chunks()
    chunk_ids = [f"c{i}" for i in range(len(chunks))]
    provider = RecordingEmbeddingProvider()

    payload = build_vector_index(chunks, chunk_ids, provider, "gen-2", include=is_example_chunk)

    assert payload["generation_id"] == "gen-2"
    assert payload["dimension"] == provider.dimension
    assert [p["chunk_id"] for p in payload["points"]] == ["c1", "c3"]
    assert [p["point_id"] for p in payload["points"]] == [point_id("gen-2", "c1"), point_id("gen-2", "c3")]
    assert [p["text"] for p in payload["points"]] == [EXAMPLE_TEXT_ONE, EXAMPLE_TEXT_TWO]
    # the recording provider proves select-then-embed: exactly ONE embed call, with ONLY the marked
    # texts - the unmarked ones were never sent to the gateway, not embedded and then discarded.
    assert provider.calls == [[EXAMPLE_TEXT_ONE, EXAMPLE_TEXT_TWO]]
    flat = [text for call in provider.calls for text in call]
    for unmarked in (PLAIN_TEXT_ONE, PLAIN_TEXT_TWO, PLAIN_TEXT_THREE):
        assert unmarked not in flat
    # each kept point's vector belongs to its OWN chunk, not a neighbor's.
    by_chunk_id = {p["chunk_id"]: tuple(p["vector"]) for p in payload["points"]}
    reference = DeterministicEmbeddingProvider()
    assert by_chunk_id["c1"] == reference.embed([EXAMPLE_TEXT_ONE])[0]
    assert by_chunk_id["c3"] == reference.embed([EXAMPLE_TEXT_TWO])[0]


def test_a_custom_predicate_is_honoured_and_pairs_chunk_ids_correctly() -> None:
    chunks = _mixed_chunks()
    chunk_ids = ["id-a", "id-b", "id-c", "id-d", "id-e"]
    provider = RecordingEmbeddingProvider()

    payload = build_vector_index(
        chunks, chunk_ids, provider, "gen-3", include=lambda chunk: chunk.section_title.startswith("Page")
    )

    assert [p["chunk_id"] for p in payload["points"]] == ["id-c", "id-d"]
    assert [p["text"] for p in payload["points"]] == [PLAIN_TEXT_TWO, EXAMPLE_TEXT_TWO]
    assert provider.calls == [[PLAIN_TEXT_TWO, EXAMPLE_TEXT_TWO]]


def test_a_list_with_no_marked_chunks_never_calls_embed_and_gives_empty_points() -> None:
    chunks = [_chunk("Document", PLAIN_TEXT_ONE), _chunk("Page", PLAIN_TEXT_TWO)]
    provider = RecordingEmbeddingProvider()

    payload = build_vector_index(chunks, ["c0", "c1"], provider, "gen-4", include=is_example_chunk)

    assert provider.calls == [], "embed must not be called at all when nothing is selected"
    assert payload["points"] == []
    assert payload["dimension"] == provider.dimension
    assert payload["generation_id"] == "gen-4"


def test_an_empty_chunk_list_with_a_predicate_never_calls_embed() -> None:
    provider = RecordingEmbeddingProvider()

    payload = build_vector_index([], [], provider, "gen-5", include=is_example_chunk)

    assert provider.calls == []
    assert payload == {"generation_id": "gen-5", "dimension": provider.dimension, "points": []}


def test_example_marker_is_pinned_to_find_examples_own_copy() -> None:
    assert EXAMPLE_MARKER == find_examples._EXAMPLE_MARKER == "Example:"


def test_is_example_chunk_requires_the_exact_marker() -> None:
    assert is_example_chunk(_chunk("Document.Open", EXAMPLE_TEXT_ONE))
    # the word "example" in lower case, mid-sentence, is not the marker.
    assert not is_example_chunk(_chunk("A", "This is an example of a plain sentence."))
    assert not is_example_chunk(_chunk("B", "For example: see the other page."))
    # mid-word and wrong-case occurrences are not the marker either.
    assert not is_example_chunk(_chunk("C", "The counterexample: is not a marker; neither is EXAMPLE:"))
    assert not is_example_chunk(_chunk("D", "Examples: plural is not the marker."))
    assert not is_example_chunk(_chunk("E", "Example without a colon"))
    assert not is_example_chunk(_chunk("F", ""))


def test_publish_generation_threads_vector_chunk_filter_into_the_stored_generation(tmp_path: Path) -> None:
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
    vector_points = manifest.payload["vector_index"]["points"]
    assert [p["chunk_id"] for p in vector_points] == [
        content_chunk_id(chunks[1]),
        content_chunk_id(chunks[3]),
    ]
    assert provider.calls == [[EXAMPLE_TEXT_ONE, EXAMPLE_TEXT_TWO]]
    # the lexical index is untouched by the vector scope: it still holds every chunk.
    lexical_texts = {doc["text"] for doc in manifest.payload["lexical_index"]["documents"].values()}
    assert lexical_texts == {chunk.text for chunk in chunks}
