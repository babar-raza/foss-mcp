"""find_examples: exact-match first, then a semantic fallback bounded by a measured relevance
floor, over verified snippets only.

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
"""

from __future__ import annotations

from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored, tokenize
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
    scope: Scope
    generation_id: str
    fqn: str
    snippet: str
    coverage: float


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
            return [ExampleMatch(scope, active_generation_id, exact.fqn, example, coverage=1.0)]

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
            )
        )
    return matches
