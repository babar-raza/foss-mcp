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

TC-493 (REQ-G3-005, honest answers): when lookup finds nothing it has searched the documentation,
the verified examples AND the symbols, so its own miss says that (``NOTHING_MATCHED_REASON``)
instead of repeating the symbol search's narrower "no symbol matches ..." wording. The ``Miss``
type, its scope, query and did-you-mean suggestions are kept; ``search_symbols`` itself is untouched.

TC-515 (REQ-G3-008): the miss now says "nothing in the INDEXED documentation, verified examples or
symbols of this pilot matches ...", because a miss is a verdict about the index, not about the
repository (12 pilots index at most 300 types). The miss keeps the ``coverage_note`` that
``search_symbols`` sets from the generation's ``API-Coverage:`` line, which says how much of the API
the index holds.

TC-516 (REQ-G3-008): lookup includes the symbol(s) a query NAMES. "how do I use Workbook" used to answer
with documentation (or a miss) although ``lookup("Workbook")`` returns the symbol: the composed
``TaskAnswer`` now carries ``symbols`` (at most three), and when there is neither documentation nor an
example but the query names a type, lookup returns that symbol list instead of a miss. A query names a type
when ONE of its distinctive words is a type name, or when at least 60 percent (and at least two) are, so a
generic lowercase word such as "table" only counts when it is (nearly) the whole question and "create a
pivot table" adds nothing. A documentation answer whose only evidence is table-dominated chunks (an API
table, a long reference dump) needs at least two distinctive query terms in one such chunk; otherwise those
chunks are dropped.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, replace

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import ExampleMatch, find_examples
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, SYMBOL, chunk_kind, distinctive_terms, present_terms
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, DocMatch, search_docs
from foss_mcp.mcp.tools.search_symbols import (
    SOURCE_KIND,
    Miss,
    SymbolMatch,
    naming_tokens,
    scope_key,
    search_symbols,
    token_forms,
)

# What lookup's own honest miss says: lookup searched documentation, verified examples and symbols.
NOTHING_MATCHED_REASON = (
    "nothing in the indexed documentation, verified examples or symbols of this pilot matches {query!r}"
)

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

# TC-516: how a query is read as naming a type, and what counts as a table-dominated documentation chunk.
_MAX_NAMED_SYMBOLS = 3
_NAMED_SHARE = 0.6
_TABLE_LINE = re.compile(r"^\s*(?:\||- +`[^`]+`\s*[-:\u2013\u2014])")
_TABLE_DOMINATED_MIN_SHARE = 0.5
_TABLE_DOMINATED_MIN_LENGTH = 8000
_TABLE_EVIDENCE_MIN_TERMS = 2
_FINAL_SEGMENT = re.compile(r"\.|::")

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


def _lookup_miss(symbol_result: list[SymbolMatch] | Miss, query: str) -> list[SymbolMatch] | Miss:
    """Re-word the symbol search's plain "no symbol matches" miss into lookup's broader miss.

    Only that exact miss is re-worded (scope, query and suggestions unchanged); a symbol list and
    the other misses ("no published generation for this scope", "published generation has no
    symbol index") are returned unchanged.
    """
    if isinstance(symbol_result, Miss) and symbol_result.reason == f"no symbol matches {query!r}":
        return replace(symbol_result, reason=NOTHING_MATCHED_REASON.format(query=query))
    return symbol_result


@dataclass(frozen=True)
class TaskAnswer:
    """A composed answer to a task-oriented query: documentation guidance plus whatever
    verified example genuinely exists for the same query - never a fabricated one.
    """

    scope: Scope
    doc_matches: tuple[DocMatch, ...]
    example: ExampleMatch | None
    # TC-516: the symbol(s) the query names (at most three), ``()`` when it names none.
    symbols: tuple[SymbolMatch, ...] = ()


def _is_table_dominated(text: str) -> bool:
    """True when a documentation chunk is a table or reference dump rather than prose: at least half of its
    non-empty body lines (after the ``FQN:`` line) start with ``|`` or with ``- `` and a backticked
    identifier and a dash or colon, or the chunk is longer than 8,000 characters."""
    if len(text) > _TABLE_DOMINATED_MIN_LENGTH:
        return True
    lines = text.splitlines()
    body_start = next((index + 1 for index, line in enumerate(lines) if line.startswith("FQN:")), 0)
    body = [line for line in lines[body_start:] if line.strip()]
    if not body:
        return False
    table_lines = sum(1 for line in body if _TABLE_LINE.match(line))
    return table_lines >= _TABLE_DOMINATED_MIN_SHARE * len(body)


def _named_symbols(store: GenerationManifestStore, scope: Scope, query: str) -> tuple[SymbolMatch, ...]:
    """The symbol chunks *query* names, at most three, in query-word order (TC-516).

    A distinctive word names a type when its casefolded, plural-stripped form equals the casefolded final
    segment (split on "." and "::") of an indexed symbol FQN. The symbols are admitted when exactly ONE
    distinctive word remains and it names a type, or when at least 60 percent of the distinctive words (and
    at least two) each name one.
    """
    tokens = naming_tokens(query, scope.family)
    if not tokens:
        return ()
    key = scope_key(scope, SOURCE_KIND)
    generation_id = store.read_active(key)
    if generation_id is None:
        return ()
    payload = store.read_generation(key, generation_id).payload.get("lexical_index") or {}
    documents = payload.get("documents") or {}
    by_segment: dict[str, list[str]] = {}
    for doc_id, document in documents.items():
        text = document["text"]
        if chunk_kind(text) != SYMBOL:
            continue
        fqn = extract_fqn(text)
        if fqn is not None:
            by_segment.setdefault(_FINAL_SEGMENT.split(fqn.strip())[-1].casefold(), []).append(doc_id)
    named: list[list[str]] = []
    for token in tokens:
        doc_ids = next((by_segment[form] for form in sorted(token_forms(token)) if form in by_segment), None)
        if doc_ids is not None:
            named.append(doc_ids)
    if len(tokens) == 1:
        admitted = bool(named)
    else:
        admitted = len(named) >= 2 and len(named) >= _NAMED_SHARE * len(tokens)
    if not admitted:
        return ()
    chosen: list[str] = []
    for doc_ids in named:
        for doc_id in doc_ids:
            if doc_id not in chosen and len(chosen) < _MAX_NAMED_SYMBOLS:
                chosen.append(doc_id)
    return tuple(
        SymbolMatch(
            scope=scope,
            generation_id=generation_id,
            doc_id=doc_id,
            chunk_id=documents[doc_id]["chunk_id"],
            text=documents[doc_id]["text"],
        )
        for doc_id in chosen
    )


def _without_unsupported_tables(
    matches: tuple[DocMatch, ...], query: str, family: str
) -> tuple[DocMatch, ...]:
    """TC-516: documentation whose only evidence is table-dominated chunks stands only when one such chunk
    holds at least two distinctive query terms; otherwise those chunks are dropped (``()``)."""
    if not all(_is_table_dominated(match.text) for match in matches):
        return matches
    terms = distinctive_terms(query, family)
    for match in matches:
        if len(present_terms(terms, tokenize(match.text))) >= _TABLE_EVIDENCE_MIN_TERMS:
            return matches
    return ()


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
      returned as soon as either one is non-empty; if BOTH are empty (and no symbol matched
      either), lookup's own miss is returned: the symbol search's miss (scope, query and
      did-you-mean suggestions kept) re-worded to say documentation, verified examples and
      symbols were all searched (``NOTHING_MATCHED_REASON``, TC-493).
    """
    if content_type is not None:
        return search_docs(store, scope, query, content_type, top_k=top_k)

    def _compose_from_docs() -> TaskAnswer | None:
        doc_matches: tuple[DocMatch, ...] = ()
        for candidate_type in CONTENT_TYPES:
            doc_result = search_docs(store, scope, query, candidate_type, top_k=top_k)
            if isinstance(doc_result, list) and doc_result:
                # TC-381: doc_matches are documentation chunks only - a symbol or example chunk
                # is never offered as documentation (search_docs already guarantees it; this
                # keeps lookup's own composition honest even if that ever regresses).
                documentation = tuple(m for m in doc_result if chunk_kind(m.text) == DOCUMENTATION)
                documentation = _without_unsupported_tables(documentation, query, scope.family)
                if documentation:
                    doc_matches = documentation
                    break

        example_result = find_examples(store, scope, query, top_k=1)
        example = example_result[0] if isinstance(example_result, list) and example_result else None

        if not doc_matches and example is None:
            return None
        return TaskAnswer(scope=scope, doc_matches=doc_matches, example=example, symbols=named_symbols())

    named_cache: list[tuple[SymbolMatch, ...]] = []

    def named_symbols() -> tuple[SymbolMatch, ...]:
        if not named_cache:
            named_cache.append(_named_symbols(store, scope, query))
        return named_cache[0]

    def _answer_naming_a_type() -> list[SymbolMatch] | None:
        symbols = named_symbols()
        return list(symbols) if symbols else None

    if _looks_like_a_task_question(query):
        task_answer = _compose_from_docs()
        if task_answer is not None:
            return task_answer
        symbol_result = search_symbols(store, scope, query, top_k=top_k)
        if isinstance(symbol_result, list) and symbol_result:
            return symbol_result
        return _answer_naming_a_type() or _lookup_miss(symbol_result, query)

    symbol_result = search_symbols(store, scope, query, top_k=top_k)
    if isinstance(symbol_result, list) and symbol_result:
        return symbol_result

    task_answer = _compose_from_docs()
    if task_answer is not None:
        return task_answer

    return _answer_naming_a_type() or _lookup_miss(symbol_result, query)
