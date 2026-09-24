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

FIXTURE_SLIDES_PYTHON = Path(__file__).parents[1] / "fixtures" / "slides_python" / "api_surface.json"
SCOPE_SLIDES_PYTHON = "slides::python::self_extracted"

FIXTURE_CELLS_RUST = Path(__file__).parents[1] / "fixtures" / "cells_rust" / "api_surface.json"
SCOPE_CELLS_RUST = "cells::rust::self_extracted"


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


def _slides_python_chunks() -> list[Chunk]:
    """A real, bounded slice of TC-025's actual slides/python extraction, normalized and
    chunked through TC-014's own pipeline - the second pilot's first real content, over its
    own independent scope. The fixture's entries carry the same class_import/name/kind/methods
    shape _pdf_net_chunks() already relies on (plus fields it doesn't need, like enum_members
    on the 36 enum entries, whose methods list is simply empty).
    """
    fixture = json.loads(FIXTURE_SLIDES_PYTHON.read_text(encoding="utf-8"))
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
        title="slides/python API surface",
        body="\n\n".join(sections),
    )
    return chunk_document(doc)


def _cells_rust_chunks() -> list[Chunk]:
    """A real, bounded slice of TC-032's actual cells/rust extraction, normalized and
    chunked through TC-014's own pipeline - the third pilot's first real content, over its
    own independent scope. Rust's tree-sitter-derived kind vocabulary (struct_item/enum_item/
    trait_item/function) differs from pdf/net's C# kinds and slides/python's python-ast kinds,
    but the entries still carry class_import/name/kind/methods for struct_item, enum_item and
    trait_item; the small number of standalone trait-impl "function" entries (e.g. default/eq)
    have no class_import at all, only their own "name", which the existing
    class_import-or-name fallback already handles.
    """
    fixture = json.loads(FIXTURE_CELLS_RUST.read_text(encoding="utf-8"))
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
        title="cells/rust API surface",
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


def _publish_slides_python(store: GenerationManifestStore, chunks, expected_active, provider) -> str:
    lease = store.acquire_lease(SCOPE_SLIDES_PYTHON, "worker-1", "pending")
    return publish_generation(
        store,
        family="slides",
        platform="python",
        source_kind="self_extracted",
        expected_active=expected_active,
        chunks=chunks,
        embedding_provider=provider,
        lease=lease,
    )


def _publish_cells_rust(store: GenerationManifestStore, chunks, expected_active, provider) -> str:
    lease = store.acquire_lease(SCOPE_CELLS_RUST, "worker-1", "pending")
    return publish_generation(
        store,
        family="cells",
        platform="rust",
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

    points1 = {
        p["chunk_id"]: p["point_id"]
        for p in store.read_generation(SCOPE, gen1).payload["vector_index"]["points"]
    }
    points2 = {
        p["chunk_id"]: p["point_id"]
        for p in store.read_generation(SCOPE, gen2).payload["vector_index"]["points"]
    }

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


def test_publishing_slides_pythons_first_generation_through_the_cas_path(tmp_path: Path) -> None:
    """A real generation, from TC-025's real fixture, through the same CAS/leasing authority
    pdf/net's own first-generation test proves - the second pilot, its own independent scope."""
    store = _store(tmp_path)
    chunks = _slides_python_chunks()
    assert len(chunks) > 1

    generation_id = _publish_slides_python(store, chunks, None, DeterministicEmbeddingProvider())

    assert store.read_active(SCOPE_SLIDES_PYTHON) == generation_id
    manifest = store.read_generation(SCOPE_SLIDES_PYTHON, generation_id)
    assert manifest.payload["vector_index"]["points"]
    assert manifest.payload["lexical_index"]["documents"]
    assert len(manifest.payload["vector_index"]["points"]) == len(chunks)


def test_publishing_cells_rusts_first_generation_through_the_cas_path(tmp_path: Path) -> None:
    """A real generation, from TC-032's real fixture, through the same CAS/leasing authority
    pdf/net's and slides/python's own first-generation tests prove - the third pilot, its own
    independent scope."""
    store = _store(tmp_path)
    chunks = _cells_rust_chunks()
    assert len(chunks) > 1

    generation_id = _publish_cells_rust(store, chunks, None, DeterministicEmbeddingProvider())

    assert store.read_active(SCOPE_CELLS_RUST) == generation_id
    manifest = store.read_generation(SCOPE_CELLS_RUST, generation_id)
    assert manifest.payload["vector_index"]["points"]
    assert manifest.payload["lexical_index"]["documents"]
    assert len(manifest.payload["vector_index"]["points"]) == len(chunks)


def test_two_pilots_sharing_one_manifest_store_never_leak_into_each_others_active_generation(
    tmp_path: Path,
) -> None:
    """pdf/net and slides/python publish their first generations into the SAME store instance -
    the real-world deployment shape TC-023's multi-instance docker-compose assumes. TC-015's own
    suite never exercised two scopes together; this proves neither pilot's active generation ever
    leaks into, or is shadowed by, the other's.
    """
    store = _store(tmp_path)
    provider = DeterministicEmbeddingProvider()

    pdf_net_generation = _publish(store, _pdf_net_chunks(), None, provider)
    slides_python_generation = _publish_slides_python(store, _slides_python_chunks(), None, provider)

    assert store.read_active(SCOPE) == pdf_net_generation
    assert store.read_active(SCOPE_SLIDES_PYTHON) == slides_python_generation
    assert store.read_active(SCOPE) != store.read_active(SCOPE_SLIDES_PYTHON)


def test_three_pilots_sharing_one_manifest_store_never_leak_into_each_others_active_generation(
    tmp_path: Path,
) -> None:
    """pdf/net, slides/python AND cells/rust publish their first generations into the SAME store
    instance - extending the two-pilot isolation proof (above) to three concurrent pilots sharing
    one manifest store. Each scope's read_active() must return only its own generation; none may
    leak into or be shadowed by either of the other two.
    """
    store = _store(tmp_path)
    provider = DeterministicEmbeddingProvider()

    pdf_net_generation = _publish(store, _pdf_net_chunks(), None, provider)
    slides_python_generation = _publish_slides_python(store, _slides_python_chunks(), None, provider)
    cells_rust_generation = _publish_cells_rust(store, _cells_rust_chunks(), None, provider)

    assert store.read_active(SCOPE) == pdf_net_generation
    assert store.read_active(SCOPE_SLIDES_PYTHON) == slides_python_generation
    assert store.read_active(SCOPE_CELLS_RUST) == cells_rust_generation

    generations = {pdf_net_generation, slides_python_generation, cells_rust_generation}
    assert len(generations) == 3, "each pilot's generation id must be distinct"

    actives = {
        SCOPE: store.read_active(SCOPE),
        SCOPE_SLIDES_PYTHON: store.read_active(SCOPE_SLIDES_PYTHON),
        SCOPE_CELLS_RUST: store.read_active(SCOPE_CELLS_RUST),
    }
    assert len(set(actives.values())) == 3, "no scope's active generation may equal another's"
