"""find_examples: exact-match first, then a lexical fallback bounded by a measured relevance
floor, then (TC-331) a strictly-additive vector-based fallback bounded by its own measured
cosine-similarity floor, over verified snippets only.

No snippet is ever executed inside the serving process: this module never calls ``eval``,
``exec``, ``compile``, or a subprocess on anything it returns - a snippet is text a human (or
another process entirely) chose to run, never code this tool runs itself.

A "verified snippet" is a chunk whose self-extracted text carries an ``Example:`` block -
this project's own convention (the published lexical-index payload has no separate metadata
field for it, same constraint ``get_symbol``'s ``FQN:`` line convention works around).

The exact-FQN-match branch (``get_symbol``-backed) is already precise and reports
``coverage=1.0`` unconditionally - it is definitionally exact, never a guess. The semantic
(BM25) fallback is NOT definitionally exact: before TC-273, any chunk with a strictly-positive
BM25 score was treated as a confident match, so a single shared common word between the query
and an otherwise-unrelated chunk (e.g. both mention "page") could manufacture a full-confidence
``ExampleMatch`` for a query with no real relevant example in the corpus at all. The fallback
now ALSO requires ``coverage`` - the fraction of the query's own distinct tokens present in the
candidate chunk's own tokens - to be at or above ``_COVERAGE_THRESHOLD`` (see its own comment
for how that number was measured against 3 real pilots). A chunk that only shares a stray
common word with the query clears the old positive-BM25-score bar but not this floor, and the
fallback now honestly returns ``NoExampleFound`` instead of guessing.

TC-331 adds one more, strictly additive stage: when the lexical fallback above finds nothing at
all, one more attempt is made against the generation's vector index (if one was published for
it) before giving up. This can only ever turn an existing ``NoExampleFound`` into a real match -
it never runs when the lexical fallback already found something, so it can never regress an
existing hit. It is also fully exception-safe: a gateway outage, a missing gateway
configuration, or any other failure embedding the query degrades this stage back to exactly
today's ``NoExampleFound`` behavior, mirroring the mission plan's own named risk mitigation
("deterministic/lexical fallback always available").
"""

from __future__ import annotations

from dataclasses import dataclass

from foss_mcp.indexing.gateway_embedding_provider import GatewayEmbeddingProvider
from foss_mcp.indexing.generation_manifest import GenerationManifest, GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored, tokenize
from foss_mcp.indexing.vector_index_writer import query_vector_index_scored
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import NotFound, extract_fqn, get_symbol
from foss_mcp.mcp.tools.search_symbols import SOURCE_KIND, scope_key

_EXAMPLE_MARKER = "Example:"

# Empirically measured (TC-273), not guessed: built against 3 real, already-ingested pilots'
# own lexical indexes (pdf/net, cells/cpp, slides/python - picked from this project's own
# committed fixtures/generations) across 7 distinct query phrasings per category, split into
# two measured bounds:
#   - A query sharing only one or two common words with an otherwise-unrelated verified
#     example chunk (e.g. "page rotation" against a PDF example that never discusses
#     rotation, "style guide" against a styled-workbook example that never discusses a
#     guide, "text alignment" against a slides example that never discusses alignment) -
#     the exact "page"-style false-confidence regression this floor exists to close - never
#     reached coverage above 0.500 against ANY example chunk, in ANY of the 3 pilots.
#   - A query that actually names the real task a verified example answers reached coverage
#     of at least 0.667, and usually 1.000, against that example, in every one of the 3
#     pilots, across every phrasing tried (exact title wording and more natural paraphrases
#     alike).
# 0.6 sits strictly between both measured bounds (0.500 < 0.6 <= 0.667), with margin on both
# sides - not a round number picked without this data.
_COVERAGE_THRESHOLD = 0.6

# Empirically measured (TC-331), not guessed: built against 2 real, already-ingested pilots'
# own real example chunks (pdf/go, 4 examples; pdf/typescript, 4 examples) through the real,
# live internal Qwen embedding gateway (TC-330's ``GatewayEmbeddingProvider``) - 8
# true-positive probes (a natural query for each example's own real task) scored cosine
# 0.6446-0.8427; 4 false-positive probes (unrelated queries) scored, at best, 0.1782-0.2768
# against any example in the same pilot. This worker independently re-ran the same measurement
# against the same 2 real pilots' own committed demo-manifest generations before trusting these
# numbers: 8 true-positive probes scored 0.6675-0.8537; 3 false-positive probes scored, at
# best, 0.2701-0.3027 against any example. 0.45 sits with real margin on both sides of both
# measurements (0.3027 < 0.45 < 0.6675), not a round number picked without this data.
_VECTOR_SIMILARITY_THRESHOLD = 0.45


def _extract_example(text: str) -> str | None:
    if _EXAMPLE_MARKER not in text:
        return None
    return text.split(_EXAMPLE_MARKER, 1)[1].strip()


def _coverage(query_tokens: frozenset[str], document_tokens: list[str]) -> float:
    """The fraction of ``query_tokens`` (the query's own DISTINCT tokens) that appear among
    ``document_tokens`` - the relevance signal the semantic fallback's floor is built on.
    """
    if not query_tokens:
        return 0.0
    return len(query_tokens & set(document_tokens)) / len(query_tokens)


@dataclass(frozen=True)
class ExampleMatch:
    """``coverage`` means two different things depending on ``source`` (TC-331): for
    ``source="exact"`` it is unconditionally ``1.0`` (definitionally exact); for
    ``source="lexical"`` it is the lexical fallback's own token-overlap fraction (see
    ``_COVERAGE_THRESHOLD``); for ``source="vector"`` it is the vector fallback's own cosine
    similarity (see ``_VECTOR_SIMILARITY_THRESHOLD``). These two numbers are not
    interchangeable - always check ``source`` before comparing or thresholding ``coverage``
    across matches, rather than assuming it is one fixed kind of number.
    """

    scope: Scope
    generation_id: str
    fqn: str
    snippet: str
    coverage: float
    source: str


@dataclass(frozen=True)
class NoExampleFound:
    """An explicit miss. Never smoothed over into an unrelated snippet."""

    scope: Scope
    query: str


def find_examples(
    store: GenerationManifestStore, scope: Scope, query: str, *, top_k: int = 5
) -> list[ExampleMatch] | NoExampleFound:
    """A verified snippet for *query*: an exact FQN match first (``get_symbol`` under the
    hood), a semantic (lexical) search over every OTHER example-bearing chunk otherwise.

    Never executes a snippet, and never returns one when nothing - exact or semantic - has
    one to offer.
    """
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return NoExampleFound(scope, query)

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    documents = (lexical_payload or {}).get("documents") or {}
    if not documents:
        return NoExampleFound(scope, query)

    exact = get_symbol(store, scope, query)
    if not isinstance(exact, NotFound):
        example = _extract_example(exact.raw_text)
        if example is not None:
            return [
                ExampleMatch(scope, active_generation_id, exact.fqn, example, coverage=1.0, source="exact")
            ]

    example_doc_ids = {
        doc_id for doc_id, document in documents.items() if _EXAMPLE_MARKER in document["text"]
    }
    if not example_doc_ids:
        return NoExampleFound(scope, query)

    query_tokens = frozenset(tokenize(query))
    ranked = query_lexical_index_scored(lexical_payload, query, top_k=len(documents))
    hits: list[tuple[str, float]] = []
    for doc_id, _score in ranked:
        if doc_id not in example_doc_ids:
            continue
        coverage = _coverage(query_tokens, documents[doc_id]["tokens"])
        if coverage < _COVERAGE_THRESHOLD:
            continue
        hits.append((doc_id, coverage))
        if len(hits) >= top_k:
            break
    if not hits:
        vector_matches = _vector_fallback(manifest, scope, active_generation_id, query, top_k)
        if vector_matches:
            return vector_matches
        return NoExampleFound(scope, query)

    matches = []
    for doc_id, coverage in hits:
        text = documents[doc_id]["text"]
        matches.append(
            ExampleMatch(
                scope=scope,
                generation_id=active_generation_id,
                fqn=extract_fqn(text) or "",
                snippet=_extract_example(text) or "",
                coverage=coverage,
                source="lexical",
            )
        )
    return matches


def _vector_fallback(
    manifest: GenerationManifest, scope: Scope, generation_id: str, query: str, top_k: int
) -> list[ExampleMatch]:
    """TC-331: one more, strictly additive attempt when the lexical fallback above found
    nothing at all - never called otherwise, so it can only ever turn an existing
    ``NoExampleFound`` into a real match, never regress an existing hit.

    Embeds *query* through the real embedding gateway and ranks the WHOLE vector-indexed
    corpus against it first (never cuts candidates by ``top_k`` before filtering - the same
    discipline ``search_symbols.py``'s own docstring establishes), filtered to verified-example
    points scoring at or above ``_VECTOR_SIMILARITY_THRESHOLD``, truncated to the caller's real
    ``top_k`` only at the end.

    Fully exception-safe end to end, not only around the embed call itself: a missing vector
    index (an older-shaped generation published before vector indexing existed), a missing
    gateway configuration, a network error, a budget-exceeded error, or any other failure
    anywhere in this stage returns an empty list instead of raising - ``find_examples()`` then
    falls through to its existing, unchanged ``NoExampleFound`` return, exactly mirroring the
    mission plan's own named risk mitigation ("deterministic/lexical fallback always
    available").
    """
    vector_payload = manifest.payload.get("vector_index")
    points = (vector_payload or {}).get("points") or []
    if not points:
        return []

    try:
        provider = GatewayEmbeddingProvider()
        query_vector = provider.embed([query])[0]

        point_by_id = {point["point_id"]: point for point in points}
        ranked = query_vector_index_scored(vector_payload, query_vector, top_k=len(points))

        matches: list[ExampleMatch] = []
        for point_id, score in ranked:
            point = point_by_id[point_id]
            text = point["text"]
            if _EXAMPLE_MARKER not in text:
                continue
            if score < _VECTOR_SIMILARITY_THRESHOLD:
                continue
            matches.append(
                ExampleMatch(
                    scope=scope,
                    generation_id=generation_id,
                    fqn=extract_fqn(text) or "",
                    snippet=_extract_example(text) or "",
                    coverage=score,
                    source="vector",
                )
            )
            if len(matches) >= top_k:
                break
        return matches
    except Exception:
        return []
