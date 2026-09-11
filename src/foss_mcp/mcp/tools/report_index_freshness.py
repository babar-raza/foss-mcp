"""report_index_freshness: the indexed generation's source revision against the current one.

The published generation's lexical-index payload (``foss_mcp.indexing.lexical_index_writer``,
out of this card's write_paths) persists each chunk's ``text`` but not the ``Provenance``
(repository/commit) the chunk was built from, so the source commit a generation was indexed
from has to be recoverable from the text itself - this project's own convention (a
``Source-Commit:`` line), the same pattern ``get_symbol``'s ``FQN:`` line already established
for the same underlying constraint. A generation whose chunks carry no such line is honestly
reported as unknown, never assumed fresh.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.search_symbols import scope_key

_SOURCE_COMMIT_LINE = re.compile(r"^Source-Commit:\s*(\S+)$", re.MULTILINE)


@dataclass(frozen=True)
class FreshnessReport:
    scope: Scope
    source_kind: str
    indexed_generation_id: str | None
    indexed_source_commit: str | None
    current_source_commit: str | None
    stale: bool
    reason: str


def _indexed_source_commit(manifest_payload: dict) -> str | None:
    documents = (manifest_payload.get("lexical_index") or {}).get("documents") or {}
    for document in documents.values():
        match = _SOURCE_COMMIT_LINE.search(document["text"])
        if match is not None:
            return match.group(1)
    return None


def report_index_freshness(
    store: GenerationManifestStore,
    scope: Scope,
    source_kind: str,
    current_source_commit: str | None,
) -> FreshnessReport:
    """Compare the commit ``scope``'s active generation (for *source_kind*) was indexed from
    against *current_source_commit*. Never publishes a generation, never guesses freshness
    when the information needed to know is simply absent.
    """
    key = scope_key(scope, source_kind)
    generation_id = store.read_active(key)
    if generation_id is None:
        return FreshnessReport(scope, source_kind, None, None, current_source_commit, True, "never published")

    manifest = store.read_generation(key, generation_id)
    indexed_commit = _indexed_source_commit(manifest.payload)

    if indexed_commit is None:
        return FreshnessReport(
            scope, source_kind, generation_id, None, current_source_commit, True,
            "indexed generation does not record its source commit",
        )
    if current_source_commit is None:
        return FreshnessReport(
            scope, source_kind, generation_id, indexed_commit, None, True,
            "current source commit is unknown; staleness cannot be ruled out",
        )
    if indexed_commit != current_source_commit:
        return FreshnessReport(
            scope, source_kind, generation_id, indexed_commit, current_source_commit, True,
            f"indexed {indexed_commit}, current source is {current_source_commit}",
        )
    return FreshnessReport(
        scope, source_kind, generation_id, indexed_commit, current_source_commit, False,
        "index matches the current source commit",
    )
