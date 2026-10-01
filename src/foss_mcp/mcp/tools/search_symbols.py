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

TC-068's example chunks are published into this exact same generation, honestly labeled with
a pseudo-FQN of the literal shape ``Example: <title>`` (never a real class/method FQN, by
design). Those chunks are not symbols and must never be reported as one, so this module
excludes them the same way ``find_examples.py`` already excludes real symbols from its own
matching: query the whole corpus first (never let ``top_k`` cut candidates before filtering),
drop anything whose FQN marks it as an example, THEN truncate to the caller's real ``top_k``.

TC-112's furnished documentation chunks are published into this exact same generation too,
honestly labeled with a parallel pseudo-FQN of the literal shape ``Doc: <title>`` (see
``infra/build_chunks.py``). Those chunks are not symbols either, so ``_NON_SYMBOL_FQN_PREFIXES``
below excludes both prefixes - this generalizes cleanly to a future non-symbol chunk kind
without editing the filter line itself again.

TC-143 (G2/REQ-G2-047): a genuine miss may still carry a purely ADDITIVE ``suggestions`` field -
a "did you mean" hint computed with the standard library's own ``difflib.get_close_matches``
against the real FQNs this exact same miss was computed against. This never widens the verdict
itself: a Miss stays a Miss, the ``reason`` field is untouched, and a suggestion is never
substituted for a real match. See ``suggest_similar_fqns`` below.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from difflib import get_close_matches

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index
from foss_mcp.mcp.routing import Scope

SOURCE_KIND = "self_extracted"

_NON_SYMBOL_FQN_PREFIXES = ("Example: ", "Doc: ")


def suggest_similar_fqns(known_fqns: Iterable[str], target: str, *, limit: int = 3) -> tuple[str, ...]:
    """Fuzzy "did you mean" suggestions for *target*, drawn only from *known_fqns*.

    *known_fqns* must be the real FQNs published in the exact generation a Miss/NotFound was
    just computed against - never a separate or stale data source. Deterministic (same inputs
    always produce the same output) and dependency-free: this is a thin wrapper over the
    standard library's own ``difflib.get_close_matches``, never a new dependency or a network
    call. Purely additive - callers attach the result to a Miss/NotFound's own ``suggestions``
    field; it never changes whether a query is a miss or a real match.
    """
    return tuple(get_close_matches(target, known_fqns, n=limit, cutoff=0.6))


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
    """An explicit miss. This IS the answer - never smoothed over into a widened result.

    ``suggestions`` (TC-143, G2/REQ-G2-047) is purely additive: a real "did you mean" hint
    computed from the FQNs actually published in this same generation, never a substitute for
    the miss itself and never present unless genuinely computed from real published content.
    """

    scope: Scope
    query: str
    reason: str
    suggestions: tuple[str, ...] = ()


def search_symbols(
    store: GenerationManifestStore, scope: Scope, query: str, *, top_k: int = 10
) -> list[SymbolMatch] | Miss:
    """Search only ``scope``'s currently active generation's self-extracted symbol index.

    TC-068's ``Example: <title>`` pseudo-symbol chunks and TC-112's ``Doc: <title>`` furnished
    documentation chunks live in this exact same generation but are never real symbols, so they
    are excluded from this tool's own notion of a match: the whole corpus is ranked first
    (mirroring ``find_examples.py``'s own ``query_lexical_index(..., top_k=len(documents))``
    pattern, so a real symbol is never lost to a premature cut), pseudo-symbols are dropped, and
    only then is the result truncated to the caller's real ``top_k``.

    A query with no match returns an explicit ``Miss`` - never a widened search, never a
    fallback to a different generation or scope.
    """
    from foss_mcp.mcp.tools.get_symbol import extract_fqn

    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return Miss(scope, query, "no published generation for this scope")

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    if not lexical_payload or not lexical_payload.get("documents"):
        return Miss(scope, query, "published generation has no symbol index")

    documents = lexical_payload["documents"]
    ranked = query_lexical_index(lexical_payload, query, top_k=len(documents))
    doc_ids = [
        doc_id
        for doc_id in ranked
        if not (extract_fqn(documents[doc_id]["text"]) or "").startswith(_NON_SYMBOL_FQN_PREFIXES)
    ][:top_k]
    if not doc_ids:
        known_fqns = []
        for doc_id in documents:
            fqn = extract_fqn(documents[doc_id]["text"])
            if fqn is not None and not fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
                known_fqns.append(fqn)
        return Miss(
            scope,
            query,
            f"no symbol matches {query!r}",
            suggestions=suggest_similar_fqns(known_fqns, query),
        )

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
