"""Publish pdf/net's first generation through the real CAS/leasing authority (TC-005), over
real content from TC-011's fixture - not a mock of either.
"""

from __future__ import annotations

import json
from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation, rollback_generation
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_net" / "api_surface.json"
SCOPE = "pdf::net::self_extracted"


def _pdf_net_chunks() -> list[Chunk]:
    """A real, bounded slice of TC-011's actual pdf/net extraction, normalized and chunked
    through TC-014's own pipeline - the first real content this generation publishes.
    """
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sections = []
    for entry in fixture["types"][:20]:
        name = entry.get("class_import") or entry.get("name", "")
        methods = ", ".join(m.get("name", "") for m in entry.get("methods") or [])
        text = f"{name} is a {entry.get('kind', '')}."
        if methods:
            text += f" Methods: {methods}."
        sections.append(f"## {name}\n\n{text}")
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(
            repository=fixture["source_repository"], commit=fixture["source_commit"], path="api_surface.json"
        ),
        evidence_refs=(f"{fixture['source_repository']}@{fixture['source_commit']}:api_surface.json",),
        title="pdf/net API surface",
        body="\n\n".join(sections),
    )
    return chunk_document(doc)


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def _publish(store: GenerationManifestStore, chunks, expected_active, provider) -> str:
    lease = store.acquire_lease(SCOPE, "worker-1", "pending")
    return publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=expected_active,
        chunks=chunks,
        embedding_provider=provider,
        lease=lease,
    )


def test_publishing_pdf_nets_first_generation_through_the_cas_path(tmp_path: Path) -> None:
    """A real generation, from TC-011's real fixture, through the actual CAS/leasing
    authority - not a mock of either."""
    store = _store(tmp_path)
    chunks = _pdf_net_chunks()
    assert len(chunks) > 1

    generation_id = _publish(store, chunks, None, DeterministicEmbeddingProvider())

    assert store.read_active(SCOPE) == generation_id
    manifest = store.read_generation(SCOPE, generation_id)
    assert manifest.payload["vector_index"]["points"]
    assert manifest.payload["lexical_index"]["documents"]
    assert len(manifest.payload["vector_index"]["points"]) == len(chunks)


def test_a_republish_of_unchanged_content_still_yields_a_new_generation_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    chunks = _pdf_net_chunks()
    provider = DeterministicEmbeddingProvider()

    gen1 = _publish(store, chunks, None, provider)
    gen2 = _publish(store, chunks, gen1, provider)

    assert gen1 != gen2
    assert store.read_active(SCOPE) == gen2


def test_point_ids_differ_across_generations_for_identical_content(tmp_path: Path) -> None:
    """The exact regression the plan names: same chunk content, but the two generations'
    points must never collide - this is what makes rollback safe.
    """
    store = _store(tmp_path)
    chunks = _pdf_net_chunks()
    provider = DeterministicEmbeddingProvider()

    gen1 = _publish(store, chunks, None, provider)
    gen2 = _publish(store, chunks, gen1, provider)

    points1 = {p["chunk_id"]: p["point_id"] for p in store.read_generation(SCOPE, gen1).payload["vector_index"]["points"]}
    points2 = {p["chunk_id"]: p["point_id"] for p in store.read_generation(SCOPE, gen2).payload["vector_index"]["points"]}

    assert points1.keys() == points2.keys(), "identical content must yield the identical chunk_ids"
    for chunk_id in points1:
        assert points1[chunk_id] != points2[chunk_id], "but generation-qualified point ids must differ"


def test_rollback_restores_the_prior_generation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    chunks = _pdf_net_chunks()
    provider = DeterministicEmbeddingProvider()

    gen1 = _publish(store, chunks, None, provider)
    gen2 = _publish(store, chunks, gen1, provider)
    assert store.read_active(SCOPE) == gen2

    rollback_lease = store.acquire_lease(SCOPE, "worker-ops", gen1)
    rollback_generation(store, SCOPE, gen2, gen1, rollback_lease)

    assert store.read_active(SCOPE) == gen1
