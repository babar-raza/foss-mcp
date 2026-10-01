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

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import rollback_generation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--source-kind", required=True)
    parser.add_argument("--manifest-store", type=Path, required=True)
    parser.add_argument("--held-by", default="rollback")
    parser.add_argument(
        "--target-generation",
        required=True,
        help="the generation id to roll back to (must already have been published)",
    )
    args = parser.parse_args()

    store = GenerationManifestStore(args.manifest_store)
    scope = "::".join((args.family, args.platform, args.source_kind))
    expected_active = store.read_active(scope)
    lease = store.acquire_lease(scope, args.held_by, "pending")

    generation_id = rollback_generation(
        store,
        scope,
        expected_active,
        args.target_generation,
        lease,
    )
    print(f"rolled back to {generation_id}")


if __name__ == "__main__":
    main()
