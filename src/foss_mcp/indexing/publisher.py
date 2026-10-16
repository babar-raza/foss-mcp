"""Publish a product's generation through the CAS manifest path (TC-005), over the embedded
topology TC-004 decided.

Builds on ``generation_manifest.py`` rather than reimplementing CAS or leasing: every
activation - forward publish or rollback - is delegated to its module-level ``publish``/
``rollback`` functions, the one authority path TC-005 built. This module's own job is narrower:
turn a document's chunks (``foss_mcp.normalization.chunker``) into the vector- and
lexical-index payloads that become one generation's content, and mint a version that is never
content-derived, so a republish of byte-identical content still gets a new generation identity.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from foss_mcp.indexing.embedding_provider import EmbeddingProvider
from foss_mcp.indexing.generation_manifest import (
    GenerationManifestStore,
    Lease,
    PublishPolicy,
    build_manifest,
    integrity_refusal_message,
    payload_stats,
    refuse_publish,
)
from foss_mcp.indexing.generation_manifest import publish as manifest_publish
from foss_mcp.indexing.generation_manifest import rollback as manifest_rollback
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.indexing.vector_index_writer import build_vector_index
from foss_mcp.normalization.chunker import Chunk


def content_chunk_id(chunk: Chunk) -> str:
    """A chunk's identity from its own content - stable across generations built from
    identical text, so the SAME chunk gets the SAME chunk_id everywhere. Only the
    generation-qualified point_id/doc_id built from it differs between generations.
    """
    digest = hashlib.sha256(f"{chunk.section_title}\x1f{chunk.text}".encode()).hexdigest()
    return digest[:16]


def new_version() -> str:
    """A version guaranteed unique per call - never derived from content.

    A content hash alone was the confirmed defect that collapsed a byte-identical republish
    into the SAME generation: ``generation_manifest.write_generation`` treats repeated
    identical content under one ``generation_id`` as a harmless no-op (by design, for
    rollback), so a content-only version could never produce the new generation identity a
    republish is supposed to get. Timestamp-plus-uuid needs no shared counter state and is
    still sortable by when it was minted.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def build_generation_stats(chunks: Sequence[Chunk], chunk_ids: Sequence[str]) -> dict[str, Any]:
    """The per-generation statistics persisted as ``payload.stats`` (TC-510): the per-kind counts
    of the lexical index this generation carries (one document per distinct chunk id), plus the
    duplicate-chunk-id count, the count of chunks whose text is not a string or is blank, and the
    sorted provenance repositories."""
    documents = {chunk_id: {"text": chunk.text} for chunk_id, chunk in zip(chunk_ids, chunks, strict=True)}
    return {
        **payload_stats({"lexical_index": {"documents": documents}}),
        "duplicate_chunk_ids": len(chunk_ids) - len(set(chunk_ids)),
        "empty_text_chunks": _empty_text_count(chunks),
        "repositories": sorted({chunk.provenance.repository for chunk in chunks}),
    }


def _empty_text_count(chunks: Sequence[Chunk]) -> int:
    return sum(1 for chunk in chunks if not isinstance(chunk.text, str) or not chunk.text.strip())


def publish_generation(
    store: GenerationManifestStore,
    *,
    family: str,
    platform: str,
    source_kind: str,
    expected_active: str | None,
    chunks: Sequence[Chunk],
    embedding_provider: EmbeddingProvider,
    lease: Lease,
    version: str | None = None,
    vector_chunk_filter: Callable[[Chunk], bool] | None = None,
    vector_decimals: int | None = None,
    policy: PublishPolicy | None = None,
) -> str:
    """Build the vector and lexical index payloads over ``chunks`` and publish them as one new
    generation for ``(family, platform, source_kind)``. Returns the newly active generation_id.

    ``vector_chunk_filter`` is an opt-in scope for the VECTOR index only, passed straight to
    ``build_vector_index`` as ``include``: ``None`` (the default) embeds and stores every chunk;
    a predicate embeds and stores only the chunks it accepts. The lexical index always holds
    every chunk either way.

    ``vector_decimals`` is passed straight to ``build_vector_index`` as ``decimals``: ``None`` (the
    default) stores vectors unrounded, an integer rounds every stored component to that many places.

    ``policy`` (TC-510) is opt-in: ``None`` changes nothing. A :class:`PublishPolicy` makes the
    payload carry ``stats``, refuses duplicate or empty chunks before any index is built, and is
    handed to the manifest ``publish`` that enforces floors, limits and identity.
    """
    version = version or new_version()
    # The generation_id is needed to build the payload itself (it is folded into every
    # point_id/doc_id below), before the payload exists to hand to build_manifest - so it is
    # derived from a throwaway build_manifest call here, then the real manifest is built once
    # the payload is ready. Both calls go through the one real constructor; neither
    # re-implements GenerationKey/GenerationManifest construction locally.
    generation_id = build_manifest(family, platform, source_kind, version).generation_id
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    if policy is not None:
        integrity = integrity_refusal_message(len(chunk_ids) - len(set(chunk_ids)), _empty_text_count(chunks))
        if integrity is not None:
            refuse_publish(
                store,
                build_manifest(family, platform, source_kind, version).key.scope,
                generation_id,
                integrity,
                expected_active=expected_active,
                held_by=policy.held_by,
                stats={
                    "duplicate_chunk_ids": len(chunk_ids) - len(set(chunk_ids)),
                    "empty_text_chunks": _empty_text_count(chunks),
                },
            )
    payload: dict[str, Any] = {
        "vector_index": build_vector_index(
            chunks,
            chunk_ids,
            embedding_provider,
            generation_id,
            include=vector_chunk_filter,
            decimals=vector_decimals,
        ),
        "lexical_index": build_lexical_index(chunks, chunk_ids, generation_id),
    }
    if policy is not None:
        payload["stats"] = build_generation_stats(chunks, chunk_ids)
    manifest = build_manifest(family, platform, source_kind, version, payload)
    if policy is None:
        return manifest_publish(store, manifest.key.scope, expected_active, manifest, lease)
    return manifest_publish(store, manifest.key.scope, expected_active, manifest, lease, policy)


def rollback_generation(
    store: GenerationManifestStore,
    scope: str,
    expected_active: str | None,
    target_generation_id: str,
    lease: Lease,
    *,
    validate: bool = False,
) -> str:
    """Roll ``scope`` back to a previously-published generation - the identical authority
    path ``publish_generation`` uses, per ``generation_manifest.rollback``. ``validate`` (TC-529)
    additionally requires the target's content to pass ``check_generation_shape``.
    """
    return manifest_rollback(store, scope, expected_active, target_generation_id, lease, validate=validate)
