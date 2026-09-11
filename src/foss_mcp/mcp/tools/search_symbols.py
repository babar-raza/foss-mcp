"""search_symbols: search foss-mcp's own self-extracted API surface for a symbol name.

CRITICAL, do not inherit this defect: the reference system silently retried without an
explicit filter, and substituted the active generation for a requested one, whenever the
strict search came up empty. This project does the opposite - a miss returns a miss. This
tool never widens its search, never falls back to a different scope, and never reads a
generation other than the one currently active for the scope routing fixed
(``foss_mcp.mcp.routing.resolve_scope``).

``Scope`` (from ``foss_mcp.mcp.routing``) names the PRODUCT a deployment serves
(family, platform) - not which data source within it a particular tool needs.
Self-extracted symbols and furnished documentation are two different sources for the SAME
product, published as two independent generations (different ``source_kind``, hence a
different generation-manifest scope key each); this module fixes ``SOURCE_KIND`` itself
rather than reading ``scope.source_kind``, so a caller cannot point ``search_symbols`` at the
wrong source by passing a ``Scope`` built for something else.
"""

from __future__ import annotations

from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index
from foss_mcp.mcp.routing import Scope

SOURCE_KIND = "self_extracted"


def scope_key(scope: Scope, source_kind: str) -> str:
    """The generation-manifest scope string for *scope* at *source_kind* - the same
    ``family::platform::source_kind`` format ``GenerationKey.scope`` uses.
    """
    return "::".join((scope.family, scope.platform, source_kind))


@dataclass(frozen=True)
class SymbolMatch:
    scope: Scope
    generation_id: str
    doc_id: str
    chunk_id: str
    text: str


@dataclass(frozen=True)
class Miss:
    """An explicit miss. This IS the answer - never smoothed over into a widened result."""

    scope: Scope
    query: str
    reason: str


def search_symbols(
    store: GenerationManifestStore, scope: Scope, query: str, *, top_k: int = 10
) -> list[SymbolMatch] | Miss:
    """Search only ``scope``'s currently active generation's self-extracted symbol index.

    A query with no match returns an explicit ``Miss`` - never a widened search, never a
    fallback to a different generation or scope.
    """
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return Miss(scope, query, "no published generation for this scope")

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    if not lexical_payload or not lexical_payload.get("documents"):
        return Miss(scope, query, "published generation has no symbol index")

    doc_ids = query_lexical_index(lexical_payload, query, top_k=top_k)
    if not doc_ids:
        return Miss(scope, query, f"no symbol matches {query!r}")

    documents = lexical_payload["documents"]
    return [
        SymbolMatch(
            scope=scope,
            generation_id=active_generation_id,
            doc_id=doc_id,
            chunk_id=documents[doc_id]["chunk_id"],
            text=documents[doc_id]["text"],
        )
        for doc_id in doc_ids
    ]
