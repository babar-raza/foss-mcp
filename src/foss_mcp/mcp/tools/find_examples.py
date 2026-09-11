"""find_examples: exact-match first, then a semantic fallback, over verified snippets only.

No snippet is ever executed inside the serving process: this module never calls ``eval``,
``exec``, ``compile``, or a subprocess on anything it returns - a snippet is text a human (or
another process entirely) chose to run, never code this tool runs itself.

A "verified snippet" is a chunk whose self-extracted text carries an ``Example:`` block -
this project's own convention (the published lexical-index payload has no separate metadata
field for it, same constraint ``get_symbol``'s ``FQN:`` line convention works around).
"""

from __future__ import annotations

from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import NotFound, extract_fqn, get_symbol
from foss_mcp.mcp.tools.search_symbols import SOURCE_KIND, scope_key

_EXAMPLE_MARKER = "Example:"


def _extract_example(text: str) -> str | None:
    if _EXAMPLE_MARKER not in text:
        return None
    return text.split(_EXAMPLE_MARKER, 1)[1].strip()


@dataclass(frozen=True)
class ExampleMatch:
    scope: Scope
    generation_id: str
    fqn: str
    snippet: str


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
            return [ExampleMatch(scope, active_generation_id, exact.fqn, example)]

    example_doc_ids = {
        doc_id for doc_id, document in documents.items() if _EXAMPLE_MARKER in document["text"]
    }
    if not example_doc_ids:
        return NoExampleFound(scope, query)

    ranked = query_lexical_index(lexical_payload, query, top_k=len(documents))
    hits = [doc_id for doc_id in ranked if doc_id in example_doc_ids][:top_k]
    if not hits:
        return NoExampleFound(scope, query)

    matches = []
    for doc_id in hits:
        text = documents[doc_id]["text"]
        matches.append(
            ExampleMatch(
                scope=scope,
                generation_id=active_generation_id,
                fqn=extract_fqn(text) or "",
                snippet=_extract_example(text) or "",
            )
        )
    return matches
