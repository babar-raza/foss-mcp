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

TC-516 (G3/REQ-G3-008) makes the QUERY side more forgiving where that is provably safe and stricter
where it is provably lax, without touching ``RELEVANCE_THRESHOLD``, ``tokenize``, any stored token or
the index: a plural acronym ("PDFs") is read as the acronym; a small synonym map lets "convert" find
"conversion"; ``term_weights`` makes coverage IDF-weighted (a term absent from the whole generation
counts most); ``required_terms`` makes an identifier-like query word ("DataSource") a must-match; and
``distinctive_terms`` says whether a query has any term that is neither the family word nor generic.

Pure functions only: no I/O, no clock, no randomness.
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Collection, Iterable, Mapping

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
# they appear in nearly every chunk of a pilot, so they must not count towards relevance. TC-516 adds
# "example", "sample", "use" and "how": they are as common in an example chunk as the family word.
_GENERIC_WORDS = frozenset(
    {"aspose", "foss", "library", "api", "example", "examples", "sample", "samples", "use", "how"}
)

# A possessive (ASCII apostrophe or U+2019 followed by "s") at a word end; removed from the QUERY
# before it is tokenised so it can never leave the one-letter fragment "s" that no chunk contains.
_POSSESSIVE_RE = re.compile(r"(?<=\w)['’]s\b", re.IGNORECASE)

# TC-516: a whole word of two or more capitals followed by a lower-case "s" ("PDFs") is the acronym's
# plural; the QUERY is rewritten to the capitals alone so the tokenizer does not split it into the
# junk terms "pd" and "fs". Document tokens are never touched.
_ACRONYM_PLURAL_RE = re.compile(r"\b([A-Z]{2,})s\b")

# TC-516: the clip of the IDF weight, every weight inside [WEIGHT_FLOOR, WEIGHT_CEILING] = [0.5, 2.0]. A term
# the generation knows is weighed ln(1 + N / (1 + df)) clipped to [WEIGHT_FLOOR, WEIGHT_KNOWN_CEILING]; a term
# absent from the whole generation weighs WEIGHT_CEILING. Measured on the baked pilots (the card's three
# tables): the single clip [0.5, 2.0] for every term gave 23/26 concise, 13/26 verbose and 12 irrelevant
# verified-absent answers, because with only 50 to 300 documents nearly every term saturates at 2.0 and the
# weights stop telling an absent term from a merely uncommon one; this split gives 23, 14 and 5.
WEIGHT_FLOOR = 0.5
WEIGHT_KNOWN_CEILING = 1.0
WEIGHT_CEILING = 2.0

_IDENTIFIER_RUN_RE = re.compile(r"[A-Za-z0-9_]+")
_LOWER_THEN_CAPITAL_RE = re.compile(r"[a-z][A-Z]")
_LOWER_RE = re.compile(r"[a-z]")
_DIGIT_RE = re.compile(r"[0-9]")


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


# TC-516: the closed set of query-side synonyms, as groups of STEMS. A query term counts as present
# when the document holds the term or any other member of its group; the document side is never altered.
_SYNONYM_WORD_GROUPS = (
    ("convert", "conversion"),
    ("colour", "color"),
    ("tab", "sheet", "worksheet"),
    ("export", "save"),
)
SYNONYM_GROUPS: tuple[frozenset[str], ...] = tuple(
    frozenset(stem_token(word) for word in group) for group in _SYNONYM_WORD_GROUPS
)
_SYNONYMS_OF: dict[str, frozenset[str]] = {member: group for group in SYNONYM_GROUPS for member in group}


def _normalised_query(query: str) -> str:
    """*query* with a plural acronym reduced to the acronym and every possessive suffix removed."""
    return _POSSESSIVE_RE.sub("", _ACRONYM_PLURAL_RE.sub(r"\1", query))


def _stemmed_query_terms(query: str) -> frozenset[str]:
    """The query's stemmed term set before the family word and the generic words are removed. A
    single alphabetic character (a possessive or case-split fragment such as the "x" of "TeX") cannot
    be matched meaningfully and only inflates the coverage denominator, so it is dropped unless that
    would empty the set (a one-letter query such as "x" still works).
    """
    stemmed = frozenset(stem_token(token) for token in tokenize(_normalised_query(query)))
    multi_character = frozenset(term for term in stemmed if not (len(term) == 1 and term.isalpha()))
    return multi_character if multi_character else stemmed


def _excluded_terms(family: str) -> set[str]:
    excluded = {stem_token(token) for token in tokenize(family)}
    excluded.add(stem_token(family))
    excluded.update(stem_token(word) for word in _GENERIC_WORDS)
    return excluded


def is_common_word(word: str, family: str) -> bool:
    """True when *word* is the scope's family word or one of the generic words (after stemming)."""
    return stem_token(word) in _excluded_terms(family)


def query_terms(query: str, family: str) -> frozenset[str]:
    """The query's stemmed term set, minus the scope's family word and the generic set; when that
    leaves nothing, the stemmed set before removal (so the bare query "pdf" still works). A
    possessive suffix is removed, a plural acronym ("PDFs") is read as the acronym, and one-letter
    alphabetic terms are dropped (unless nothing else remains), so the query side never carries
    terms no chunk can contain.
    """
    stemmed = _stemmed_query_terms(query)
    remaining = stemmed - _excluded_terms(family)
    return remaining if remaining else stemmed


def distinctive_terms(query: str, family: str) -> frozenset[str]:
    """``query_terms`` WITHOUT its "fall back to the full set when nothing remains" rule: the stemmed
    terms minus the family word and the generic words, so "Example:" and "pdf examples" have none.
    """
    return _stemmed_query_terms(query) - _excluded_terms(family)


def required_terms(query: str) -> frozenset[str]:
    """The identifier-like words of *query*, as the stemmed tokens a chunk MUST contain.

    A run of the ORIGINAL query is identifier-like when it holds a lower-case letter immediately
    followed by a capital (``DataSource``, ``getValue``), an underscore, or a lower-case letter
    together with a digit (``Pdf2Image``, ``set_password``). Its whole-run lower-case token is the
    required term; an underscore run contributes each piece between underscores, because the
    tokenizer never emits an underscore. An ALL-CAPS acronym (``PDF``, ``XLSX``, ``3MF``) and a plain
    word are not identifier-like and require nothing.
    """
    required: set[str] = set()
    for run in _IDENTIFIER_RUN_RE.findall(query):
        identifier_like = (
            _LOWER_THEN_CAPITAL_RE.search(run) is not None
            or "_" in run
            or (_LOWER_RE.search(run) is not None and _DIGIT_RE.search(run) is not None)
        )
        if not identifier_like:
            continue
        for piece in run.split("_"):
            if piece:
                required.add(stem_token(piece))
    return frozenset(required)


def _term_present(term: str, stemmed_document: set[str]) -> bool:
    if term in stemmed_document:
        return True
    return any(member in stemmed_document for member in _SYNONYMS_OF.get(term, ()))


def present_terms(terms: Collection[str], document_tokens: Iterable[str]) -> frozenset[str]:
    """The members of ``terms`` present among the stemmed ``document_tokens``, a synonym counting."""
    stemmed_document = {stem_token(token) for token in document_tokens}
    return frozenset(term for term in frozenset(terms) if _term_present(term, stemmed_document))


def _coverage_over(
    terms: Collection[str], stemmed_document: set[str], weights: Mapping[str, float] | None
) -> float:
    term_set = frozenset(terms)
    if not term_set:
        return 0.0
    present = [term for term in term_set if _term_present(term, stemmed_document)]
    if weights is None:
        return len(present) / len(term_set)
    total = sum(weights.get(term, 1.0) for term in term_set)
    if total <= 0.0:
        return 0.0
    return sum(weights.get(term, 1.0) for term in present) / total


def coverage(
    terms: Collection[str],
    document_tokens: Iterable[str],
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    """The fraction of ``terms`` present among the stemmed ``document_tokens`` (a term's synonyms
    count as the term). With ``weights`` (``term_weights``) it is the weight of the present terms
    over the weight of all terms; without it, the plain fraction every caller has always had.
    """
    return _coverage_over(terms, {stem_token(token) for token in document_tokens}, weights)


def guarded_coverage(
    terms: Collection[str],
    document_tokens: Iterable[str],
    required: Collection[str] = (),
    weights: Mapping[str, float] | None = None,
) -> float:
    """The one relevance score all three retrieval tools compare with ``RELEVANCE_THRESHOLD``:
    ``coverage`` (weighted when ``weights`` is given), but 0.0 when a ``required`` term (an
    identifier-like query word) is absent from the chunk.
    """
    tokens = document_tokens if isinstance(document_tokens, (list, tuple)) else list(document_tokens)
    if required:
        stemmed_document = {stem_token(token) for token in tokens}
        if any(term not in stemmed_document for term in required):
            return 0.0
    return coverage(terms, tokens, weights=weights)


def term_weights(terms: Collection[str], lexical_payload: Mapping[str, object]) -> dict[str, float]:
    """The IDF weight per term, every weight within [0.5, 2.0]. N is the number of documents of the
    generation and df the sum of ``doc_freq`` over the raw tokens whose stem is the term. A term the
    generation holds (df above 0) weighs ``ln(1 + N / (1 + df))`` clipped to [0.5, 1.0]; a term absent from
    the whole generation (df 0) weighs most, 2.0. So a
    missing word the corpus has never seen costs a candidate twice what a missing common word does.
    Deterministic: it reads only the payload.
    """
    documents = lexical_payload.get("documents") or {}
    doc_freq = lexical_payload.get("doc_freq") or {}
    document_count = len(documents)  # type: ignore[arg-type]
    frequency = dict.fromkeys(frozenset(terms), 0)
    for token, count in doc_freq.items():  # type: ignore[attr-defined]
        stem = stem_token(token)
        if stem in frequency:
            frequency[stem] += count
    return {
        term: (
            WEIGHT_CEILING
            if count == 0
            else min(WEIGHT_KNOWN_CEILING, max(WEIGHT_FLOOR, math.log(1 + document_count / (1 + count))))
        )
        for term, count in frequency.items()
    }
