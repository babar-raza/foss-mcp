"""infra.rollback's first real test coverage (TC-139).

Before this card, ``foss_mcp.indexing.publisher.rollback_generation`` had no caller anywhere
that would ever trigger it in production - TC-130's own import-graph walk confirmed this.
This test publishes TWO real generations to a REAL ``GenerationManifestStore`` (never a mock),
confirms the second is active, invokes ``infra/rollback.py``'s own ``main()`` targeting the
first generation, and confirms the store's active pointer is genuinely back to the first
generation afterward - a real rollback through the real CLI, not merely a direct call to
``rollback_generation`` with asserted arguments.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import infra.rollback as rollback
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

SCOPE = "pdf::net::self_extracted"

_PROVENANCE = Provenance(
    repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
    commit="b717287" * 5,
    path="api_surface.json",
)


def _chunk(section_title: str, text: str) -> Chunk:
    return Chunk(
        section_title=section_title,
        text=text,
        source_kind="self_extracted",
        content_type="api_surface",
        provenance=_PROVENANCE,
        trust_tier="highest",
        evidence_refs=(f"{_PROVENANCE.repository}@{_PROVENANCE.commit}:api_surface.json",),
        validation=NOT_CHECKED,
    )


def _publish_real_generation(store: GenerationManifestStore, held_by: str, chunks: list[Chunk]) -> str:
    """Publish one real generation directly through ``publish_generation`` (the same
    authority path ``infra/ingest.py`` uses) and return its generation_id."""
    expected_active = store.read_active(SCOPE)
    lease = store.acquire_lease(SCOPE, held_by, "pending")
    return publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=expected_active,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )


def _rollback_argv(manifest_store_path: Path, target_generation: str) -> list[str]:
    return [
        "rollback.py",
        "--family",
        "pdf",
        "--platform",
        "net",
        "--source-kind",
        "self_extracted",
        "--manifest-store",
        str(manifest_store_path),
        "--target-generation",
        target_generation,
    ]


def test_main_rolls_a_real_scope_back_to_a_real_previously_published_generation(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_store_path = tmp_path / "manifests"
    store = GenerationManifestStore(manifest_store_path)

    first_generation_id = _publish_real_generation(
        store, "ingestion", [_chunk("First", "The first generation's document.")]
    )
    second_generation_id = _publish_real_generation(
        store, "ingestion", [_chunk("Second", "The second generation's document.")]
    )

    assert store.read_active(SCOPE) == second_generation_id
    assert first_generation_id != second_generation_id

    monkeypatch.setattr(sys, "argv", _rollback_argv(manifest_store_path, first_generation_id))
    rollback.main()

    # Re-read through a FRESH store instance pointed at the same on-disk root, so the
    # assertion genuinely proves the CLI persisted the rollback to the real filesystem
    # authority rather than merely mutating some in-memory object the test already holds.
    reread_store = GenerationManifestStore(manifest_store_path)
    assert reread_store.read_active(SCOPE) == first_generation_id, (
        "the active pointer must genuinely be back on the first generation after rollback"
    )

    rolled_back_manifest = reread_store.read_generation(SCOPE, first_generation_id)
    published_texts = {
        doc["text"] for doc in rolled_back_manifest.payload["lexical_index"]["documents"].values()
    }
    assert published_texts == {"The first generation's document."}


def test_main_prints_the_rolled_back_generation_id(tmp_path: Path, monkeypatch, capsys) -> None:
    manifest_store_path = tmp_path / "manifests"
    store = GenerationManifestStore(manifest_store_path)

    first_generation_id = _publish_real_generation(
        store, "ingestion", [_chunk("First", "The first generation's document.")]
    )
    _publish_real_generation(store, "ingestion", [_chunk("Second", "The second generation's document.")])

    monkeypatch.setattr(sys, "argv", _rollback_argv(manifest_store_path, first_generation_id))
    rollback.main()

    out = capsys.readouterr().out
    assert out.strip() == f"rolled back to {first_generation_id}"


def test_main_defaults_held_by_to_rollback(tmp_path: Path, monkeypatch) -> None:
    """``--held-by`` defaults to ``'rollback'`` per the card's required exact shape - a
    distinct lease holder identity from ``infra/ingest.py``'s own ``'ingestion'`` default, so a
    rollback's lease is never mistaken for an ingestion run's in any shared lease state."""
    manifest_store_path = tmp_path / "manifests"
    store = GenerationManifestStore(manifest_store_path)

    first_generation_id = _publish_real_generation(
        store, "ingestion", [_chunk("First", "The first generation's document.")]
    )
    _publish_real_generation(store, "ingestion", [_chunk("Second", "The second generation's document.")])

    argv = _rollback_argv(manifest_store_path, first_generation_id)
    monkeypatch.setattr(sys, "argv", argv)
    rollback.main()

    lease_state = store._read_json(store._lease_state_path(SCOPE))
    assert lease_state is not None
    assert lease_state["held_by"] == "rollback"


def test_main_raises_unknown_generation_error_for_a_target_never_published(
    tmp_path: Path, monkeypatch
) -> None:
    """Rollback's own sanity check (``generation_exists``) refuses a target that was never
    really published - the CLI must let that error propagate rather than silently activating
    a bogus pointer."""
    from foss_mcp.indexing.generation_manifest import UnknownGenerationError

    manifest_store_path = tmp_path / "manifests"
    store = GenerationManifestStore(manifest_store_path)
    _publish_real_generation(store, "ingestion", [_chunk("First", "The first generation's document.")])

    bogus_target = "pdf::net::self_extracted::never-published"
    monkeypatch.setattr(sys, "argv", _rollback_argv(manifest_store_path, bogus_target))

    with pytest.raises(UnknownGenerationError):
        rollback.main()
