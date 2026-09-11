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
from collections.abc import Sequence
from datetime import UTC, datetime

from foss_mcp.indexing.embedding_provider import EmbeddingProvider
from foss_mcp.indexing.generation_manifest import (
    GenerationKey,
    GenerationManifest,
    GenerationManifestStore,
    Lease,
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
) -> str:
    """Build the vector and lexical index payloads over ``chunks`` and publish them as one new
    generation for ``(family, platform, source_kind)``. Returns the newly active generation_id.
    """
    key = GenerationKey(
        family=family, platform=platform, source_kind=source_kind, version=version or new_version()
    )
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    payload = {
        "vector_index": build_vector_index(chunks, chunk_ids, embedding_provider, key.generation_id),
        "lexical_index": build_lexical_index(chunks, chunk_ids, key.generation_id),
    }
    manifest = GenerationManifest(key=key, payload=payload)
    return manifest_publish(store, key.scope, expected_active, manifest, lease)


def rollback_generation(
    store: GenerationManifestStore,
    scope: str,
    expected_active: str | None,
    target_generation_id: str,
    lease: Lease,
) -> str:
    """Roll ``scope`` back to a previously-published generation - the identical authority
    path ``publish_generation`` uses, per ``generation_manifest.rollback``.
    """
    return manifest_rollback(store, scope, expected_active, target_generation_id, lease)
