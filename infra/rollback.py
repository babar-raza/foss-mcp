"""Rollback entrypoint: a plain CLI, no message broker.

TC-130's own import-graph walk confirmed ``foss_mcp.indexing.publisher.rollback_generation``
has no caller anywhere that would ever trigger it in production - there was presently no way
to actually invoke a rollback outside a test. This module wraps that already-implemented,
already-tested function in a one-shot command, mirroring ``infra/ingest.py``'s own real CLI
shape exactly (argparse, ``GenerationManifestStore`` construction, lease acquisition pattern)
rather than inventing a different convention. "Rollback" is deliberately not a worker or a
queue consumer either: it runs once, rolls the scope's active pointer back to a previously
published generation, prints the result, and exits.

Rollback travels the identical authority path a forward publish does (see
``foss_mcp.indexing.generation_manifest``'s module docstring): it is a fenced CAS through
``GenerationManifestStore.cas_activate``, targeting a generation_id that was written earlier.
This module adds no logic of its own beyond parsing arguments and calling
``foss_mcp.indexing.publisher.rollback_generation`` with them.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from foss_mcp.indexing.generation_manifest import (
    GenerationManifestStore,
    UnknownGenerationError,
    payload_stats,
)
from foss_mcp.indexing.publisher import rollback_generation


def _list_generations(store: GenerationManifestStore, scope: str) -> None:
    """Print one line per stored generation, newest first. Takes no lease and writes nothing."""
    active = store.read_active(scope)
    for generation_id in store.list_generations(scope):
        manifest = store.read_generation(scope, generation_id)
        stats = manifest.payload.get("stats")
        if not (
            isinstance(stats, dict)
            and all(kind in stats for kind in ("symbols", "docs", "examples", "total"))
        ):
            stats = payload_stats(manifest.payload)
        marker = "ACTIVE" if generation_id == active else "      "
        print(
            f"{generation_id} {marker} symbols={stats['symbols']} docs={stats['docs']} "
            f"examples={stats['examples']} total={stats['total']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--source-kind", required=True)
    parser.add_argument("--manifest-store", type=Path, required=True)
    parser.add_argument("--held-by", default="rollback")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--target-generation",
        help="the generation id to roll back to (must already have been published)",
    )
    mode.add_argument(
        "--list",
        action="store_true",
        help="list the scope's stored generations, newest first, marking the active one",
    )
    mode.add_argument(
        "--previous",
        action="store_true",
        help="roll back to the generation the last publish or rollback replaced",
    )
    args = parser.parse_args()

    store = GenerationManifestStore(args.manifest_store)
    scope = "::".join((args.family, args.platform, args.source_kind))
    if args.list:
        _list_generations(store, scope)
        return

    target = args.target_generation
    if args.previous:
        target = store.previous_generation(scope)
        if target is None:
            raise UnknownGenerationError(f"scope {scope!r} has no previous generation to roll back to")
    expected_active = store.read_active(scope)
    lease = store.acquire_lease(scope, args.held_by, "pending")

    generation_id = rollback_generation(
        store,
        scope,
        expected_active,
        target,
        lease,
        validate=True,
    )
    print(f"rolled back to {generation_id}")


if __name__ == "__main__":
    main()
