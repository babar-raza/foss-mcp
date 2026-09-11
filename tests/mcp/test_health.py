"""Health probes that prove usefulness, not process reachability.

Readiness reads the ACTUAL active generation pointer - the confirmed reference-system
defect this replaces reported ready while serving nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.health import DeploymentGenerationStore, is_alive, is_ready, round_trip_check
from foss_mcp.mcp.routing import Scope
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_net" / "api_surface.json"
PDF_NET_SCOPE = Scope(family="pdf", platform="net")


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def _publish_real_generation(store: GenerationManifestStore) -> str:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sections = [f"## {t['name']}\n\n{t['name']} is a {t.get('kind', '')}." for t in fixture["types"][:5]]
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository=fixture["source_repository"], commit=fixture["source_commit"]),
        evidence_refs=(),
        title="pdf/net API surface",
        body="\n\n".join(sections),
    )
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
    return publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunk_document(doc),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )


def test_liveness_is_always_true_and_needs_no_store() -> None:
    assert is_alive() is True


def test_readiness_is_false_with_no_active_generation(tmp_path: Path) -> None:
    store = DeploymentGenerationStore.for_scope(_store(tmp_path), PDF_NET_SCOPE, "self_extracted")
    assert is_ready(store) is False


def test_readiness_is_true_with_a_real_published_generation(tmp_path: Path) -> None:
    manifest_store = _store(tmp_path)
    _publish_real_generation(manifest_store)
    store = DeploymentGenerationStore.for_scope(manifest_store, PDF_NET_SCOPE, "self_extracted")
    assert is_ready(store) is True


def test_readiness_does_not_leak_across_scopes(tmp_path: Path) -> None:
    """Publishing pdf/net must never make an unrelated scope report ready."""
    manifest_store = _store(tmp_path)
    _publish_real_generation(manifest_store)
    other = DeploymentGenerationStore.for_scope(
        manifest_store, Scope(family="cells", platform="python"), "self_extracted"
    )
    assert is_ready(other) is False


def test_round_trip_check_reads_the_real_published_content(tmp_path: Path) -> None:
    manifest_store = _store(tmp_path)
    _publish_real_generation(manifest_store)
    store = DeploymentGenerationStore.for_scope(manifest_store, PDF_NET_SCOPE, "self_extracted")
    assert round_trip_check(store) is True


def test_round_trip_check_is_false_with_nothing_published(tmp_path: Path) -> None:
    store = DeploymentGenerationStore.for_scope(_store(tmp_path), PDF_NET_SCOPE, "self_extracted")
    assert round_trip_check(store) is False
