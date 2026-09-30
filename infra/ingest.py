"""Ingestion entrypoint: a plain CLI, no message broker.

Publishes one generation from pre-built chunks (JSON) through the CAS manifest path
(``foss_mcp.indexing.publisher``, ``foss_mcp.indexing.generation_manifest``) - the same
authority path TC-015 built. "Ingestion" is deliberately not called "worker": there is no
queue here to work against, just a command that runs, publishes, and exits.

No production ``EmbeddingProvider`` ships anywhere in ``src/`` yet (see
``foss_mcp.indexing.embedding_provider`` - the deterministic one exists for tests only, and
is never importable from a production path). ``--embedding-provider`` names one by import
path (``module.path:ClassName``) so this entry point is genuinely complete now and needs no
further change once a real provider is added by a later card.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

from foss_mcp.indexing.embedding_provider import EmbeddingProvider
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.citation import (
    citable_chunks,
    known_counts_from_fixture,
    symbol_index_from_api_surface,
    validate_document,
)
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance, ValidationResult


class PublishSafetyError(Exception):
    """Raised by :func:`_assert_publish_is_safe` to refuse a publish that would either
    ship an empty generation or drastically regress against what is already live. Never
    caught inside this module: ``main()`` lets it propagate and exit non-zero rather than
    publish anyway."""


def _assert_publish_is_safe(new_chunk_count: int, active_document_count: int | None) -> None:
    """Guard called immediately before :func:`publish_generation`.

    Refuses the publish when either is true:

    1. ``new_chunk_count == 0`` - an empty generation is never a valid publish, regardless
       of whether anything is currently active. A tree-sitter regression, a broken
       extraction, or a citation-validation bug that silently drops every chunk must not
       replace a good, currently-serving generation with nothing.
    2. There IS a currently active generation (``active_document_count`` is not ``None``
       and greater than zero) and the new chunk count is less than half its document
       count - a drastic regression against what is already live.
    """
    if new_chunk_count == 0:
        raise PublishSafetyError("refusing to publish an empty generation (0 chunks)")
    if active_document_count is not None and active_document_count > 0:
        if new_chunk_count < active_document_count * 0.5:
            raise PublishSafetyError(
                f"refusing to publish {new_chunk_count} chunk(s): this is more than a 50% "
                f"regression against the currently active generation's {active_document_count} "
                "document(s)"
            )


def _load_embedding_provider(spec: str) -> EmbeddingProvider:
    module_name, _, class_name = spec.partition(":")
    if not module_name or not class_name:
        raise ValueError(f"--embedding-provider must be 'module.path:ClassName', got {spec!r}")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)()


def _load_chunks(path: Path) -> list[Chunk]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Chunk(
            section_title=entry["section_title"],
            text=entry["text"],
            source_kind=entry["source_kind"],
            content_type=entry["content_type"],
            provenance=Provenance(**entry["provenance"]),
            trust_tier=entry["trust_tier"],
            evidence_refs=tuple(entry.get("evidence_refs", ())),
            validation=ValidationResult(**entry["validation"]) if "validation" in entry else NOT_CHECKED,
        )
        for entry in data["chunks"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--source-kind", required=True)
    parser.add_argument("--chunks", type=Path, required=True, help="pre-built chunks, as JSON")
    parser.add_argument("--manifest-store", type=Path, required=True)
    parser.add_argument("--embedding-provider", required=True, help="module.path:ClassName")
    parser.add_argument("--held-by", default="ingestion")
    parser.add_argument(
        "--api-surface",
        type=Path,
        default=None,
        help=(
            "self-extracted api_surface.json to validate chunks' citations against "
            "(TC-014's validate_document/citable_chunks). Omit to publish all loaded "
            "chunks unchanged, as before this card - not every source_kind has a "
            "self-extracted symbol index to validate against."
        ),
    )
    args = parser.parse_args()

    store = GenerationManifestStore(args.manifest_store)
    scope = "::".join((args.family, args.platform, args.source_kind))
    expected_active = store.read_active(scope)
    lease = store.acquire_lease(scope, args.held_by, "pending")
    chunks = _load_chunks(args.chunks)
    if args.api_surface is not None:
        fixture = json.loads(args.api_surface.read_text(encoding="utf-8"))
        symbol_index = symbol_index_from_api_surface(fixture["types"])
        known_counts = known_counts_from_fixture(fixture)
        chunks = citable_chunks(validate_document(chunks, symbol_index, known_counts))
    embedding_provider = _load_embedding_provider(args.embedding_provider)

    active_document_count: int | None = None
    if expected_active is not None:
        active_manifest = store.read_generation(scope, expected_active)
        active_documents = (active_manifest.payload.get("lexical_index") or {}).get("documents") or {}
        active_document_count = len(active_documents)
    _assert_publish_is_safe(len(chunks), active_document_count)

    generation_id = publish_generation(
        store,
        family=args.family,
        platform=args.platform,
        source_kind=args.source_kind,
        expected_active=expected_active,
        chunks=chunks,
        embedding_provider=embedding_provider,
        lease=lease,
    )
    print(f"published {generation_id}")


if __name__ == "__main__":
    main()
