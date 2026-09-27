"""lookup: a forgiving entry point that dispatches to search_symbols and search_docs.

"Forgiving" is about WHICH underlying index gets tried, never about WHERE it searches:
every dispatch below passes the identical ``scope`` straight through, so a result can never
carry a different product, platform, or generation than the one routing fixed. A caller that
does not know whether a term is a symbol or a documentation topic can still ask once; a
caller that gets nothing back gets an honest miss, never a widened one.

REQ-G2-049: a task-oriented query with no prior API knowledge (e.g. "how do I add a watermark
to a PDF") should be able to get back BOTH the relevant documentation guidance AND a verified
example in one answer, when either or both exist. This applies ONLY to the doc-fallback path
(``content_type`` omitted, no symbol match) - the exact-symbol-match path and the
explicit-``content_type`` path are unchanged, since either one is already a complete, specific
answer on its own terms. In that doc-fallback path, ``find_examples`` is tried independently of
whether a doc match was found - not gated behind it, since no getting_started/developer_guide/
troubleshooting/faq content has ever been built or published for any pilot, so a doc match is
often genuinely absent while a real, verified example still exists for the same query. A
``TaskAnswer`` is composed whenever EITHER a doc match OR an example is found - ``doc_matches``
stays ``()`` and ``example`` stays ``None``, never fabricated, exactly when each is genuinely
absent. Only when BOTH are empty does lookup fall through to ``search_symbols``.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import ExampleMatch, find_examples
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, DocMatch, search_docs
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols

# A leading question/auxiliary word that marks a query as a natural-language question rather
# than a bare identifier, even when it is short (e.g. "is AFRelationship a class?" - though in
# practice a real question this short is rare; the word-count threshold below is what
# classifies the overwhelming majority of real task questions).
_QUESTION_OR_AUXILIARY_WORDS = frozenset(
    {
        "how",
        "what",
        "why",
        "can",
        "could",
        "does",
        "do",
        "did",
        "is",
        "are",
        "was",
        "were",
        "should",
        "would",
        "will",
        "where",
        "when",
        "who",
        "which",
        "shall",
    }
)

# A query at or above this many words reads as a sentence, not an identifier - real symbol
# names (including a namespaced FQN like "PdfDocument.AddWatermarkAnnotation") are always a
# single whitespace-delimited token.
_TASK_QUESTION_MIN_WORDS = 3


def _looks_like_a_task_question(query: str) -> bool:
    """Does *query* read as a natural-language task question rather than a bare
    identifier/symbol lookup?

    A short, single-token query (e.g. ``"AddWatermarkAnnotation"``, ``"Document"``,
    ``"AFRelationship"``) is genuinely ambiguous and stays on today's existing
    symbol-first dispatch order. A multi-word query, or one that opens with a
    question/auxiliary word (``"how"``, ``"what"``, ``"can"``, ...), reads as an actual
    question about how to accomplish a task, and tries the doc-fallback+composition path
    first instead - because for a sentence like "how do I add a watermark to a PDF", a
    non-empty ``search_symbols`` result is very often just an incidental lexical
    collision (nearly every real symbol's FQN shares a common word, like "pdf", with the
    question itself), never genuine evidence the caller wanted a symbol lookup.
    """
    words = query.split()
    if len(words) >= _TASK_QUESTION_MIN_WORDS:
        return True
    if not words:
        return False
    first_word = words[0].strip(string.punctuation).lower()
    return first_word in _QUESTION_OR_AUXILIARY_WORDS


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
    - ``content_type`` omitted: dispatch order depends on the query's own shape
      (``_looks_like_a_task_question``). A short, bare, identifier-like query (e.g.
      ``"AFRelationship"``) tries ``search_symbols`` first (existing, unchanged behaviour -
      a bare name most often names a symbol), then every documentation category in turn. A
      query that reads as a natural-language task question (several words, and/or opens with
      a question/auxiliary word like "how"/"what"/"can") tries the doc-fallback+composition
      path FIRST instead, falling back to ``search_symbols`` only if no documentation matches
      anywhere - because for a real sentence, a non-empty symbol-search result is very often
      just an incidental lexical collision (e.g. nearly every symbol's FQN in a product shares
      a common word, like "pdf", with the question itself), never genuine evidence of an
      intentional symbol lookup. Either way, the first non-empty doc-category match (if any)
      composes with whatever ``find_examples`` can independently offer for the same query into
      a ``TaskAnswer`` - a doc match and an example are each optional, and a ``TaskAnswer`` is
      returned as soon as either one is non-empty; if BOTH are empty, the miss from the symbol
      search is returned, since that is the search a bare query most specifically asked for.
    """
    if content_type is not None:
        return search_docs(store, scope, query, content_type, top_k=top_k)

    def _compose_from_docs() -> TaskAnswer | None:
        doc_matches: tuple[DocMatch, ...] = ()
        for candidate_type in CONTENT_TYPES:
            doc_result = search_docs(store, scope, query, candidate_type, top_k=top_k)
            if isinstance(doc_result, list) and doc_result:
                doc_matches = tuple(doc_result)
                break

        example_result = find_examples(store, scope, query, top_k=1)
        example = example_result[0] if isinstance(example_result, list) and example_result else None

        if not doc_matches and example is None:
            return None
        return TaskAnswer(scope=scope, doc_matches=doc_matches, example=example)

    if _looks_like_a_task_question(query):
        task_answer = _compose_from_docs()
        if task_answer is not None:
            return task_answer
        return search_symbols(store, scope, query, top_k=top_k)

    symbol_result = search_symbols(store, scope, query, top_k=top_k)
    if isinstance(symbol_result, list) and symbol_result:
        return symbol_result

    task_answer = _compose_from_docs()
    if task_answer is not None:
        return task_answer

    return symbol_result
