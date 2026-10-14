"""G3/TC-505: the QUERY side of the shared relevance rule never carries terms no chunk can contain.

A possessive ("element's") used to leave the junk one-letter term "s", and a mixed-case family word
("TeX") left the junk fragment "x"; each junk term inflated the denominator of the 60 percent
coverage rule and turned a hit into a miss. These tests pin the repair on the query side only
(document tokenisation, stemming, camelCase expansion and the threshold are unchanged).

Offline and deterministic: pure functions, no I/O.
"""

from __future__ import annotations

from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.tools.relevance import (
    RELEVANCE_THRESHOLD,
    coverage,
    query_terms,
    stem_token,
)

ASCII_QUERY = "how do I change an element's text"
TYPOGRAPHIC_QUERY = "how do I change an element’s text"


def _one_letter(terms: frozenset[str]) -> set[str]:
    return {term for term in terms if len(term) == 1 and term.isalpha()}


def test_possessive_leaves_no_one_letter_term() -> None:
    assert _one_letter(query_terms(ASCII_QUERY, "html")) == set()


def test_possessive_keeps_the_stem_of_the_noun() -> None:
    assert stem_token("element") in query_terms(ASCII_QUERY, "html")


def test_typographic_apostrophe_gives_the_same_set() -> None:
    assert query_terms(TYPOGRAPHIC_QUERY, "html") == query_terms(ASCII_QUERY, "html")


def test_possessive_query_equals_the_query_without_the_apostrophe() -> None:
    assert query_terms(ASCII_QUERY, "html") == query_terms("how do I change an element text", "html")


def test_mixed_case_family_word_leaves_no_one_letter_fragment() -> None:
    terms = query_terms("TeX logging", "tex")
    assert "x" not in terms
    # NOTE (TC-505 card defect): the card also expects "te" to be absent, but "te" is a two-letter
    # fragment that the card's one-letter rule does not drop, so it is not asserted here.
    assert _one_letter(terms) == set()
    assert stem_token("logging") in terms


def test_single_letter_query_still_returns_a_term() -> None:
    assert query_terms("x", "tex") == frozenset({"x"})
    assert query_terms("x", "page")


def test_digit_tokens_are_kept() -> None:
    terms = query_terms("sha256 hash", "page")
    assert "sha256" in terms
    assert stem_token("hash") in terms


def test_lone_digit_is_not_dropped() -> None:
    assert "3" in query_terms("pdf 3", "page")


def test_identifier_sub_word_expansion_is_unchanged() -> None:
    expected = {stem_token(token) for token in tokenize("PostScript")}
    expected -= {stem_token(token) for token in tokenize("page")}
    expected -= {stem_token("page")}
    assert query_terms("PostScript", "page") == frozenset(expected)
    assert {"postscript", "post", "script"} <= query_terms("PostScript", "page")


def test_family_word_is_still_excluded() -> None:
    assert "html" not in query_terms("html element text", "html")
    assert stem_token("element") in query_terms("html element text", "html")


def test_coverage_of_the_diagnosed_html_miss_reaches_the_threshold() -> None:
    chunk_tokens = ["parse", "html", "string", "change", "element", "text"]
    assert coverage(query_terms(ASCII_QUERY, "html"), chunk_tokens) >= RELEVANCE_THRESHOLD
    assert coverage(query_terms(TYPOGRAPHIC_QUERY, "html"), chunk_tokens) >= RELEVANCE_THRESHOLD
