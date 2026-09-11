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
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance, ValidationResult


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
    args = parser.parse_args()

    store = GenerationManifestStore(args.manifest_store)
    scope = "::".join((args.family, args.platform, args.source_kind))
    expected_active = store.read_active(scope)
    lease = store.acquire_lease(scope, args.held_by, "pending")
    chunks = _load_chunks(args.chunks)
    embedding_provider = _load_embedding_provider(args.embedding_provider)

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
