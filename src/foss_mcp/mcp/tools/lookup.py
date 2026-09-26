"""lookup: a forgiving entry point that dispatches to search_symbols and search_docs.

"Forgiving" is about WHICH underlying index gets tried, never about WHERE it searches:
every dispatch below passes the identical ``scope`` straight through, so a result can never
carry a different product, platform, or generation than the one routing fixed. A caller that
does not know whether a term is a symbol or a documentation topic can still ask once; a
caller that gets nothing back gets an honest miss, never a widened one.

REQ-G2-049: a task-oriented query with no prior API knowledge (e.g. "how do I add a watermark
to a PDF") should be able to get back BOTH the relevant documentation guidance AND a verified
example in one answer, when both exist. This applies ONLY to the doc-fallback path
(``content_type`` omitted, no symbol match, a doc match found) - the exact-symbol-match path
and the explicit-``content_type`` path are unchanged, since either one is already a complete,
specific answer on its own terms. In that doc-fallback path, once a doc match is found, a
``TaskAnswer`` composes it with whatever ``find_examples`` can honestly offer for the same
query - ``example`` stays ``None``, never fabricated, when no verified example exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import ExampleMatch, find_examples
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, DocMatch, search_docs
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols


@dataclass(frozen=True)
class TaskAnswer:
    """A composed answer to a task-oriented query: documentation guidance plus whatever
    verified example genuinely exists for the same query - never a fabricated one.
    """

    scope: Scope
    doc_matches: tuple[DocMatch, ...]
    example: ExampleMatch | None


def lookup(
    store: GenerationManifestStore,
    scope: Scope,
    query: str,
    *,
    content_type: str | None = None,
    top_k: int = 10,
) -> list[SymbolMatch] | list[DocMatch] | TaskAnswer | Miss:
    """Try the most specific search first, then the others, all within ``scope`` alone.

    - ``content_type`` given: search only that documentation category (delegates entirely to
      ``search_docs``, so a caller who already knows what they want gets exactly that tool's
      behaviour, honest miss included).
    - ``content_type`` omitted: try ``search_symbols`` first (a bare name most often names a
      symbol), then every documentation category in turn. The first non-empty result composes
      with ``find_examples`` into a ``TaskAnswer``; if nothing matches anywhere, the miss from
      the symbol search is returned, since that is the search a bare query most specifically
      asked for.
    """
    if content_type is not None:
        return search_docs(store, scope, query, content_type, top_k=top_k)

    symbol_result = search_symbols(store, scope, query, top_k=top_k)
    if isinstance(symbol_result, list) and symbol_result:
        return symbol_result

    for candidate_type in CONTENT_TYPES:
        doc_result = search_docs(store, scope, query, candidate_type, top_k=top_k)
        if isinstance(doc_result, list) and doc_result:
            example_result = find_examples(store, scope, query, top_k=1)
            example = (
                example_result[0] if isinstance(example_result, list) and example_result else None
            )
            return TaskAnswer(scope=scope, doc_matches=tuple(doc_result), example=example)

    return symbol_result
