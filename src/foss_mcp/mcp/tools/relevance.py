"""One shared, deterministic relevance rule for documentation search, lookup and example search.

TC-381 (G3/REQ-G3-005). Before this module each retrieval tool had its own notion of "a hit":
``search_docs`` treated every chunk that is not an example as documentation (API-symbol chunks
included, defaulted to ``developer_guide``), accepted any positive BM25 score, and had no
plural/stem tolerance; ``find_examples`` had a coverage floor, but the product-family word ("pdf"
occurs in nearly every example) filled one slot of it, so one more shared common word reached the
floor. This module is the single place all three tools take their rule from:

- ``chunk_kind`` says what a chunk IS (``symbol``, ``example`` or ``documentation``), from its own
  ``FQN:`` line, so a symbol or example chunk can never be offered as documentation whatever words
  it contains;
- ``stem_token`` is a light, deterministic, stdlib-only stemmer applied to both sides of every
  comparison, so "install" finds "Installation" and "pages" matches "page";
- ``query_terms`` is the query's stemmed term set minus the scope's own product-family word and a
  small generic set, so the word every chunk of a product shares can no longer manufacture a match;
- ``coverage`` / ``RELEVANCE_THRESHOLD`` are the measured (TC-273) floor over those terms.

Pure functions only: no I/O, no clock, no randomness.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Collection, Iterable

from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.tools.get_symbol import extract_fqn

SYMBOL = "symbol"
EXAMPLE = "example"
DOCUMENTATION = "documentation"

_EXAMPLE_FQN_PREFIX = "Example: "
_DOC_FQN_PREFIX = "Doc: "

# TC-273's measured value, unchanged: sits strictly between "one or two stray shared common
# words" (never above 0.500) and "the query names the task the chunk answers" (at least 0.667).
# With two terms both must be present; with three at least two.
RELEVANCE_THRESHOLD = 0.6

# Words that name the vendor/product line rather than a task; like the scope's own family word
# they appear in nearly every chunk of a pilot, so they must not count towards relevance.
_GENERIC_WORDS = frozenset({"aspose", "foss", "library", "api"})

# A possessive (ASCII apostrophe or U+2019 followed by "s") at a word end; removed from the QUERY
# before it is tokenised so it can never leave the one-letter fragment "s" that no chunk contains.
_POSSESSIVE_RE = re.compile(r"(?<=\w)['\u2019]s\b", re.IGNORECASE)


def chunk_kind(text: str) -> str:
    """``example`` when the chunk's FQN line starts with ``Example: ``; ``documentation`` when it
    starts with ``Doc: `` or the chunk has no FQN line at all (hand-written documentation chunks);
    ``symbol`` otherwise.
    """
    fqn = extract_fqn(text)
    if fqn is None:
        return DOCUMENTATION
    if fqn.startswith(_EXAMPLE_FQN_PREFIX):
        return EXAMPLE
    if fqn.startswith(_DOC_FQN_PREFIX):
        return DOCUMENTATION
    return SYMBOL


@functools.cache
def stem_token(token: str) -> str:
    """A light stemmer: lowercases and strips, then applies the FIRST matching rule of, in order,
    ``ations``/``ation``, ``ings``/``ing`` (3+ characters must remain), ``ies`` -> ``y``, ``es``
    after s/x/z/ch/sh, a final ``s`` not preceded by ``s`` (word longer than 3 characters), ``ed``
    (word longer than 4 characters). install, installs, installing and installation all give
    ``install``; pages gives ``page``; images gives ``image``. A rule never empties a word.
    """
    word = token.lower().strip()
    for suffix in ("ations", "ation"):
        if word.endswith(suffix) and len(word) > len(suffix):
            return word[: -len(suffix)]
    for suffix in ("ings", "ing"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    return word


def query_terms(query: str, family: str) -> frozenset[str]:
    """The query's stemmed term set, minus the scope's family word and the generic set; when that
    leaves nothing, the stemmed set before removal (so the bare query "pdf" still works). A
    possessive suffix is removed and one-letter alphabetic terms are dropped (unless nothing else
    remains), so the query side never carries terms no chunk can contain.
    """
    stemmed = frozenset(stem_token(token) for token in tokenize(_POSSESSIVE_RE.sub("", query)))
    # A single alphabetic character (a possessive or case-split fragment such as the "x" of "TeX")
    # cannot be matched meaningfully and only inflates the coverage denominator; drop it unless that
    # would empty the set (a one-letter query such as "x" still works).
    multi_character = frozenset(term for term in stemmed if not (len(term) == 1 and term.isalpha()))
    stemmed = multi_character if multi_character else stemmed
    excluded = {stem_token(token) for token in tokenize(family)}
    excluded.add(stem_token(family))
    excluded.update(stem_token(word) for word in _GENERIC_WORDS)
    remaining = stemmed - excluded
    return remaining if remaining else stemmed


def coverage(terms: Collection[str], document_tokens: Iterable[str]) -> float:
    """The fraction of ``terms`` present among the stemmed ``document_tokens``."""
    term_set = frozenset(terms)
    if not term_set:
        return 0.0
    stemmed_document = {stem_token(token) for token in document_tokens}
    return len(term_set & stemmed_document) / len(term_set)
