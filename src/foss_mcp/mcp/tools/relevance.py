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

TC-543 (G3/REQ-G3-008) adds ``title_anchored`` for ``find_examples``: for a query of at most
``TITLE_ANCHOR_MAX_TERMS`` (2) distinctive terms, an example may answer only if one of those terms (a
synonym counting) is a word of the example's TITLE. A one- or two-word query is satisfied by any example
whose CODE merely calls ``Save`` or ``Open``, which is nearly every example, so ``coverage`` of 1.0 said
nothing; a longer query is judged by the coverage floor alone, as before. ``RELEVANCE_THRESHOLD``, the IDF
weights and the required identifier terms are untouched.

TC-562 (G3/REQ-G3-009) makes the QUERY side understand how people phrase a task, still without embeddings:
``STOP_WORDS`` (function words and conversational filler, never an API noun) are dropped from a query behind
the switch ``STOP_WORDS_ENABLED``; ``USER_TERM_SYNONYMS`` maps the words a user types to the words an API uses
("merge" -> merge/concat/append, "link" -> hyperlink) one way, from the query to the index; ``fold_query``
NFKC-folds a query so a full-width ``Workbook`` is the word ``Workbook``; ``has_minimum_evidence`` refuses a
query with no word of two characters (``a.b.c::d``); and ``evidence_terms`` turns a task query into the terms
``search_symbols`` scores a type against (``strong`` terms must be explained, ``weak`` light verbs such as
"add" and "create" only help). ``RELEVANCE_THRESHOLD``, ``tokenize``, the stored tokens and the index are untouched.

TC-585 (G3/REQ-G3-009): ``_GENERIC_WORDS`` is hand-maintained and global, so a word ubiquitous in ONE
family's own API surface but meaningful in another ("file" on pdf, "color" on slides, "value"/"workbook"
on cells) was never excluded there, and as a literal match it could crowd out a weaker, token-based
fallback answer. ``family_generic_stems`` measures this per SCOPE from the same doc_freq table
``term_weights`` already reads (reused, not duplicated): a word present in at least
``FAMILY_UBIQUITY_FRACTION`` of that scope's own indexed chunks is a candidate for exclusion from THAT
family's own ``is_common_word``/``evidence_terms`` ``extra_generic`` - in addition to, never instead of,
the existing global list. ``search_symbols.py`` owns where this is actually wired in (its own
``_safe_extra_generic``, and the SEPARATE ``own_explained`` gate on the family-stem rescue), after
measuring that feeding it into THIS module's own concept admission regressed the retrieval corpus.

Pure functions only: no I/O, no clock, no randomness.
"""

from __future__ import annotations

import functools
import math
import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, replace

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

# ---------------------------------------------------------------------------------------------------
# TC-562 (G3/REQ-G3-009): how people phrase a task.
# ---------------------------------------------------------------------------------------------------

# A real switch. True: function words and conversational filler (``STOP_WORDS``) are not query terms. False:
# the query is tokenised exactly as it was before this card (only the index tokenizer's own stop list applies).
STOP_WORDS_ENABLED = True

# Function words and filler only. Never a word that is a product or API noun ("page", "document", "table",
# "image", "save", "open"): those are what the question is about. The index tokenizer already drops its own
# list of articles, prepositions, pronouns, auxiliaries and question words; these are the ones it lacks.
STOP_WORDS: frozenset[str] = frozenset(
    {
        "actually",
        "also",
        "always",
        "another",
        "anyone",
        "anything",
        "around",
        "automatically",
        "basically",
        "correctly",
        "easily",
        "entire",
        "etc",
        "even",
        "ever",
        "every",
        "everyone",
        "everything",
        "exactly",
        "help",
        "helps",
        "just",
        "kindly",
        "let",
        "lets",
        "like",
        "likes",
        "maybe",
        "need",
        "needed",
        "needs",
        "never",
        "now",
        "often",
        "onto",
        "perhaps",
        "please",
        "possible",
        "possibly",
        "programmatically",
        "properly",
        "quickly",
        "really",
        "simply",
        "someone",
        "something",
        "sometimes",
        "somehow",
        "sure",
        "thing",
        "things",
        "together",
        "tries",
        "try",
        "trying",
        "upon",
        "via",
        "want",
        "wanted",
        "wants",
        "way",
        "ways",
        "whole",
        "wish",
        "within",
        "would",
        "yet",
        "two",
        "three",
        "four",
        "five",
        "several",
        "many",
        "multiple",
        "single",
        "couple",
    }
)

# Filler of a task sentence that is not a function word, so it stays a term for documentation and example
# search (an example may really say "write to disk") but never has to be explained by a symbol.
_EVIDENCE_FILLER: frozenset[str] = frozenset({"disk", "scratch"})

# Light verbs: they say what kind of request it is, not which API. They only ever help (a member called
# ``AddEmptySlide`` earns a little for "add"); they are never required to be explained.
WEAK_TERMS: frozenset[str] = frozenset(
    {
        "add",
        "apply",
        "build",
        "call",
        "change",
        "create",
        "edit",
        "generate",
        "get",
        "give",
        "insert",
        "make",
        "modify",
        "new",
        "perform",
        "put",
        "run",
        "set",
        "update",
        "use",
    }
)

# User words -> the words an API uses. ONE WAY (from the query to the index; a document word is never widened)
# and separate from ``SYNONYM_GROUPS``, which is symmetric and pinned. A key is the word as typed; each value is a
# word an API member or type name would carry.
USER_TERM_SYNONYMS: dict[str, tuple[str, ...]] = {
    # edit / set a value
    "change": ("put", "set", "value"),
    "set": ("put", "set", "value"),
    "edit": ("put", "set", "value"),
    "modify": ("put", "set", "value"),
    "update": ("put", "set", "value"),
    "write": ("write", "save"),
    "store": ("save", "write"),
    # combine / split
    "merge": ("merge", "concat", "concatenate", "append"),
    "combine": ("merge", "concat", "concatenate", "append"),
    "join": ("merge", "concat", "join"),
    "concatenate": ("concat", "merge", "append"),
    "split": ("split",),
    "separate": ("split",),
    "divide": ("split",),
    # remove
    "remove": ("remove", "delete"),
    "delete": ("remove", "delete"),
    "erase": ("remove", "delete", "clear"),
    # text
    "text": ("text", "string"),
    "string": ("text", "string"),
    # links
    "link": ("hyperlink", "link"),
    "hyperlink": ("hyperlink", "link", "url"),
    "url": ("hyperlink", "url", "link"),
    "website": ("hyperlink", "url", "web"),
    "web": ("hyperlink", "url", "web"),
    # appearance
    "bold": ("font", "bold"),
    "italic": ("font", "italic"),
    "underline": ("font", "underline"),
    "strikethrough": ("font", "strikeout"),
    "typeface": ("font",),
    "colour": ("color",),
    "highlight": ("conditional", "format", "condition", "highlight"),
    "rule": ("condition", "conditional", "format"),
    "fill": ("fill", "pattern", "color"),
    "background": ("background", "fill", "pattern", "color"),
    "shading": ("fill", "pattern"),
    "center": ("alignment", "horizontal", "center"),
    "centre": ("alignment", "horizontal", "center"),
    "align": ("alignment", "horizontal"),
    "wrap": ("wrap",),
    "border": ("border", "borders"),
    "size": ("size", "width", "height"),
    "resize": ("size", "width", "height", "resize"),
    # spreadsheet words
    "spreadsheet": ("workbook", "worksheet"),
    "excel": ("workbook",),
    "xlsx": ("workbook",),
    "dropdown": ("validation", "list"),
    "validate": ("validation",),
    "limit": ("validation",),
    "restrict": ("validation", "protect"),
    "freeze": ("pane", "freeze"),
    "formula": ("formula", "calculation"),
    "calculate": ("formula", "calculation", "calculate"),
    "calculation": ("formula", "calculation"),
    "landscape": ("orientation", "landscape"),
    "portrait": ("orientation", "portrait"),
    "orientation": ("orientation",),
    "paper": ("paper", "papersize", "size"),
    "a4": ("paper", "size"),
    "print": ("print", "page", "setup"),
    "margin": ("margin",),
    # protection
    "password": ("password", "protect", "protection", "encrypt", "security"),
    "protect": ("protect", "protection", "password", "encrypt"),
    "encrypt": ("encrypt", "password", "security"),
    "lock": ("protect", "protection", "lock"),
    "secure": ("protect", "security", "encrypt"),
    "sign": ("sign", "signature", "certificate"),
    "signature": ("sign", "signature", "certificate"),
    "digital": ("sign", "signature", "certificate"),
    # visibility
    "hide": ("hidden", "hide", "visible", "visibility"),
    "hidden": ("hidden", "hide", "visible", "visibility"),
    "unhide": ("hidden", "visible", "visibility"),
    "visible": ("visibility", "hidden", "visible"),
    # images and drawing
    "picture": ("picture", "image"),
    "photo": ("picture", "image"),
    "image": ("image", "picture"),
    "graphic": ("image", "picture", "graphic"),
    "chart": ("chart",),
    "graph": ("chart", "graph"),
    "plot": ("chart",),
    "thumbnail": ("thumbnail", "image", "render"),
    "render": ("render", "convert", "save", "draw"),
    # annotations
    "comment": ("comment", "note", "annotation"),
    "note": ("note", "comment"),
    "sticky": ("annotation", "text"),
    "watermark": ("watermark", "stamp"),
    "stamp": ("stamp", "watermark"),
    # open / save / convert
    "open": ("open", "load", "read"),
    "load": ("load", "open", "read"),
    "read": ("read", "load", "open"),
    "import": ("load", "open", "import"),
    "convert": ("convert", "conversion", "save", "export"),
    "extract": ("extract", "absorber"),
    # documents
    "deck": ("presentation",),
    "speaker": ("notes",),
    "bookmark": ("bookmark", "outline"),
    "outline": ("outline", "bookmark"),
    "transition": ("transition", "animation", "effect"),
    "animate": ("animation", "effect", "animate"),
    "track": ("revision", "track"),
    "tracked": ("revision", "track"),
    "bulleted": ("list",),
    "bullet": ("list",),
    "numbered": ("list",),
    "heading": ("heading", "style"),
    "spacing": ("spacing", "line"),
    "info": ("info", "property", "metadata"),
    "metadata": ("metadata", "info", "property"),
    "properties": ("properties", "property", "metadata", "info"),
    "author": ("author", "creator"),
    # 3d
    "cube": ("box", "mesh"),
    "model": ("scene", "mesh", "model"),
    "move": ("translate", "translation", "transform", "position"),
    "rotate": ("rotate", "rotation", "transform"),
    "scale": ("scale", "scaling", "transform"),
    "position": ("position", "transform", "translation"),
    "lamp": ("light",),
    "triangulate": ("triangulate", "polygon", "modifier"),
    # mail
    "email": ("mapi", "message", "msg"),
    "mail": ("mapi", "message", "msg"),
    "message": ("mapi", "message", "msg"),
    "outlook": ("mapi", "message", "msg"),
    "attach": ("attachment", "attach"),
    "attachment": ("attachment", "attach"),
    "sender": ("sender", "from"),
    "sent": ("delivery", "time", "date"),
    "received": ("delivery", "time", "date"),
    "date": ("time", "date", "delivery"),
    "broken": ("exception", "error", "invalid", "corrupt"),
    "corrupt": ("exception", "error", "repair", "corrupt"),
    "corrupted": ("exception", "error", "repair", "corrupt"),
    "damaged": ("exception", "error", "repair", "corrupt"),
    "recover": ("repair", "recover", "diagnostics"),
    "error": ("exception", "error"),
    "fail": ("exception", "error"),
    "problem": ("issue", "error", "exception", "validation"),
    "issue": ("issue", "error", "exception", "validation"),
    # walking a structure
    "walk": ("visitor", "iterate", "enumerate"),
    "traverse": ("visitor", "iterate", "enumerate"),
    "visit": ("visitor", "iterate"),
    "iterate": ("iterate", "enumerate", "visitor"),
    "loop": ("iterate", "enumerate"),
    "compile": ("compile", "run", "job"),
    # making a file smaller
    "compress": ("compress", "optimize", "size"),
    "shrink": ("compress", "optimize", "size"),
    "smaller": ("compress", "optimize", "size"),
    "reduce": ("compress", "optimize", "size"),
}

_ZERO_WIDTH_RE = re.compile("[​-‏⁠﻿]")
_CAMEL_PARTS_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_ALNUM_RUN_RE = re.compile(r"[A-Za-z0-9]+")
_TWO_CHARACTER_RUN_RE = re.compile(r"[A-Za-z0-9]{2,}")


def fold_query(query: str) -> str:
    """*query* in Unicode compatibility form NFKC with the zero-width characters removed, so a full-width
    ``Ｗｏｒｋｂｏｏｋ`` is the word ``Workbook``. Anything NFKC cannot map to ASCII (a CJK word) is left as
    it is: it matches nothing, honestly."""
    return _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", query))


_IRREGULAR_STEMS = {"children": "child", "indices": "index", "vertices": "vertex", "matrices": "matrix"}


def canon(word: str) -> str:
    """A key under which a word and its inflections meet: ``stem_token``, then a trailing ``e`` removed, so
    merge/merging/merges and hide/hiding agree. Both sides of every comparison use it; it is never written to an
    index."""
    stem = _IRREGULAR_STEMS.get(word.lower()) or stem_token(word)
    if len(stem) > 5 and stem.endswith("ly"):
        stem = stem[:-2]
    if len(stem) > 3 and stem.endswith("e"):
        stem = stem[:-1]
    return stem


_WEAK_KEYS = frozenset(canon(word) for word in WEAK_TERMS)

# Other names people use for the product itself ("notebook" for OneNote, "latex" for TeX): like the family word
# they say which library, not which API, so they are not query terms.
FAMILY_ALIASES: dict[str, frozenset[str]] = {
    "note": frozenset({"notebook", "notebooks", "onenote"}),
    "tex": frozenset({"latex"}),
    "pdf": frozenset({"acrobat"}),
}

# Verbs for what a user wants done ("save", "export", "convert"). When NO type or member of a generation carries
# the verb at all, the user is paraphrasing (the library calls it something else) and it is not held against an
# answer; an unknown NOUN ("pivot") still is.
ACTION_TERMS: frozenset[str] = frozenset(
    {
        "check",
        "compile",
        "convert",
        "display",
        "download",
        "export",
        "fetch",
        "find",
        "generate",
        "handle",
        "import",
        "inspect",
        "list",
        "load",
        "open",
        "output",
        "parse",
        "print",
        "read",
        "render",
        "retrieve",
        "save",
        "search",
        "show",
        "store",
        "view",
        "write",
    }
)
ACTION_KEYS: frozenset[str] = frozenset(canon(word) for word in ACTION_TERMS)


def _build_user_synonyms() -> dict[str, frozenset[str]]:
    built: dict[str, set[str]] = {}
    for key, values in USER_TERM_SYNONYMS.items():
        built.setdefault(canon(key), set()).update(canon(value) for value in values)
    for group in SYNONYM_GROUPS:
        for member in group:
            built.setdefault(canon(member), set()).update(canon(other) for other in group)
    return {key: frozenset(values) for key, values in built.items()}


_USER_SYNONYMS_OF: dict[str, frozenset[str]] = _build_user_synonyms()


def _normalised_query(query: str) -> str:
    """*query* in NFKC form (TC-562) with a plural acronym reduced to the acronym and every possessive
    suffix removed."""
    return _POSSESSIVE_RE.sub("", _ACRONYM_PLURAL_RE.sub(r"\1", fold_query(query)))


def _content_tokens(query: str) -> list[str]:
    """The index tokenizer's tokens of the normalised *query*, minus ``STOP_WORDS`` while
    ``STOP_WORDS_ENABLED`` (TC-562). A query made only of such words keeps them: nothing is ever emptied."""
    tokens = tokenize(_normalised_query(query))
    if not STOP_WORDS_ENABLED:
        return tokens
    content = [token for token in tokens if token not in STOP_WORDS]
    return content if content else tokens


def _stemmed_query_terms(query: str) -> frozenset[str]:
    """The query's stemmed term set before the family word and the generic words are removed. A
    single alphabetic character (a possessive or case-split fragment such as the "x" of "TeX") cannot
    be matched meaningfully and only inflates the coverage denominator, so it is dropped unless that
    would empty the set (a one-letter query such as "x" still works).
    """
    stemmed = frozenset(stem_token(token) for token in _content_tokens(query))
    multi_character = frozenset(term for term in stemmed if not (len(term) == 1 and term.isalpha()))
    return multi_character if multi_character else stemmed


def _excluded_terms(family: str, extra_generic: Collection[str] = ()) -> set[str]:
    excluded = {stem_token(token) for token in tokenize(family)}
    excluded.add(stem_token(family))
    excluded.update(stem_token(word) for word in _GENERIC_WORDS)
    excluded.update(extra_generic)
    return excluded


def is_common_word(word: str, family: str, *, extra_generic: Collection[str] = ()) -> bool:
    """True when *word* is the scope's family word, one of the global generic words, or one of
    *extra_generic*'s stems (TC-585: a word measured ubiquitous within one family's own data; see
    ``family_generic_stems``), after stemming.
    """
    return stem_token(word) in _excluded_terms(family, extra_generic)


# TC-585 (G3/REQ-G3-009, RST round-2 RD-5): a word that is merely ubiquitous within ONE family's own
# API surface ("file" on pdf, "color" on slides, "cell"/"value"/"workbook" on cells) is not in the
# hand-maintained global ``_GENERIC_WORDS`` above (it is genuinely distinctive in most OTHER families),
# yet it is also a literal type-name/member-name match, so a confident but wrong candidate that merely
# shares the word can crowd out the fallback stage's own weaker, token-based evidence for the real answer.
# ``family_generic_stems`` below measures, from the SAME doc_freq table ``term_weights`` already reads
# (reused, not duplicated), the plain fraction of a SCOPE's own indexed chunks that carry each candidate
# word; a word at or above ``FAMILY_UBIQUITY_FRACTION`` is a candidate for exclusion from that scope's
# ``search_symbols._fallback_doc_ids`` token-matching stage - never from a different family's, since the
# same word may be that family's single most distinctive term there.
#
# A SEPARATE, independent fix (``search_symbols._evidence_ranking``'s ``own_explained`` gate on the
# family-stem light-term rescue) addresses the other half of this card's reproduction directly: a type
# whose name merely STEMS like the family's own word ("Cell"/"Cells" for family "cells") was credited a
# full NAME_FIT_WEIGHT bonus regardless of whether it explained anything else, letting it outscore a type
# that genuinely explains the whole question ("Style"/"Alignment" for "wrap text in a cell"). Measuring
# this card's own fix against the full retrieval corpus (every attempt that let a per-family-generic word
# ALSO exclude itself as a REQUIRED concept in ``_evidence_ranking``, changing ``needed``/``total``/
# ``foreign``, regressed unrelated families whose own common, central nouns - "document" on pdf, "page"
# on pdf, "shape" on slides, "text" on all three - are also their most frequent words) is why the fraction
# below feeds only the fallback stage and the ``own_explained`` gate, never ``_evidence_ranking``'s own
# concept admission; this module's worker report records the measured attempts and the full before/after
# corpus numbers.
#
# Measured directly (doc-level fraction) on the real baked ``infra/demo-manifests`` generations the card's
# own 3 reproduced cases named, every platform of each family: pdf's "file" 0.066-0.248 (6 platforms),
# slides' "color" 0.106-0.155 (4 platforms), cells' "value" 0.167-0.636 and "workbook" 0.071-0.323 (7
# platforms). 0.12 sits below "color"'s lowest measured platform and below "file" on net/python/cpp -
# the platforms this card's own reproduction used - while a genuinely rare, distinctive word on the SAME
# pilots stays far under it ("watermark" 0.002-0.026, "hyperlink" 0.000-0.192, "stamp" 0.020-0.076 on pdf;
# "chart" 0.000-0.021 on 3 of slides' 4 platforms; "range" 0.031-0.177 on cells).
FAMILY_UBIQUITY_FRACTION = 0.12

# Below this many documents, a fraction measurement is too noisy to trust (2 documents: one occurrence
# is already 50 percent) - see ``family_generic_stems``. Every real baked pilot holds at least 49.
MIN_SCOPE_DOCUMENTS_FOR_UBIQUITY = 20


def _scope_document_fraction(stem: str, lexical_payload: Mapping[str, object]) -> float:
    """The fraction of *lexical_payload*'s own documents carrying a raw token whose stem is *stem* -
    the plain (unclipped) fraction behind ``term_weights``'s own doc_freq table, summed over every raw
    token that stems to it exactly as ``term_weights`` already sums one."""
    documents = lexical_payload.get("documents") or {}
    doc_freq = lexical_payload.get("doc_freq") or {}
    total_documents = len(documents)  # type: ignore[arg-type]
    if total_documents == 0:
        return 0.0
    carrying = sum(
        count
        for token, count in doc_freq.items()
        if stem_token(token) == stem  # type: ignore[attr-defined]
    )
    return carrying / total_documents


def family_generic_stems(
    stems: Collection[str], family: str, lexical_payload: Mapping[str, object] | None
) -> frozenset[str]:
    """The members of *stems* (already ``stem_token``-stemmed) that are ubiquitous within
    *lexical_payload*'s own scope (TC-585): present in at least ``FAMILY_UBIQUITY_FRACTION`` of that
    scope's own indexed chunks. ``frozenset()`` when *lexical_payload* carries no document-frequency
    table (``None``, or a synthetic fixture built without one) - no scope data means no claim is made.
    Additive to the existing global ``_GENERIC_WORDS``, never a replacement: pass the result as
    ``is_common_word``'s or ``evidence_terms``'s ``extra_generic`` for THIS scope only.

    *family*'s OWN stem is never a member of the result, however high its measured fraction (it is
    always the highest, since the namespace prefix of every type's FQN carries it): that word already
    has its own, DIFFERENT special treatment (the family-stem light-term rescue in ``evidence_terms`` -
    "add a slide" on the slides pilots, where the family-stem word IS the thing asked about), which many
    real queries across the whole corpus depend on; routing it through the plain generic-discard path
    instead broke far more than the one case ("cell" on the cells pilots) this card's own reproduction
    targeted, confirmed by running the full retrieval-corpus ratchet (see this card's own worker report).

    ``frozenset()`` for a scope of fewer than ``MIN_SCOPE_DOCUMENTS_FOR_UBIQUITY`` documents: with only a
    handful of chunks, any word appearing in even one or two of them is already "ubiquitous" by fraction
    alone (2 documents: one occurrence is 50 percent) without that meaning anything about real ubiquity -
    found directly by a synthetic 2-chunk fixture ("create a pivot table" wrongly started matching once
    "create" and "pivot" were each swept up as "ubiquitous" for a corpus of 2). Every real baked pilot
    this card measured holds at least 49 documents, comfortably above the floor.
    """
    if not lexical_payload:
        return frozenset()
    documents = lexical_payload.get("documents") or {}
    if not documents or not lexical_payload.get("doc_freq"):
        return frozenset()
    if len(documents) < MIN_SCOPE_DOCUMENTS_FOR_UBIQUITY:  # type: ignore[arg-type]
        return frozenset()
    family_stem = stem_token(family)
    return frozenset(
        stem
        for stem in stems
        if stem != family_stem and _scope_document_fraction(stem, lexical_payload) >= FAMILY_UBIQUITY_FRACTION
    )


def family_generic_fraction(stem: str, lexical_payload: Mapping[str, object] | None) -> float:
    """The public form of the measurement ``family_generic_stems`` itself uses: ``0.0`` when
    *lexical_payload* carries no document-frequency table, else *stem*'s real fraction of this scope's
    own documents (TC-585). A caller that must exclude several ubiquitous words from ONE query but keep
    at least one real concept alive (``search_symbols._safe_extra_generic``) ranks its candidates by this
    value, most-ubiquitous-first, rather than guessing an order.
    """
    if not lexical_payload or not lexical_payload.get("documents") or not lexical_payload.get("doc_freq"):
        return 0.0
    return _scope_document_fraction(stem, lexical_payload)


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


_LANGUAGE_LETTER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z](?=\+\+|#)")


def names_language_letter(query: str) -> bool:
    """True only when *query* contains a single letter directly followed by ``++`` or ``#`` ("C++", "C#"),
    the one place a one-letter term carries the topic. TC-505 drops one-letter terms and TC-516 turns "no
    distinctive term" into a miss, which together made the C++ pilots' product title unanswerable.
    """
    return _LANGUAGE_LETTER_RE.search(_normalised_query(query)) is not None


# TC-543: a query with at most this many distinctive terms must find one of them in an example's title.
TITLE_ANCHOR_MAX_TERMS = 2


def title_anchored(query: str, family: str, title: str) -> bool:
    """Whether an example titled *title* may answer *query* (TC-543).

    A query with more than ``TITLE_ANCHOR_MAX_TERMS`` distinctive terms is judged by the coverage floor
    alone, so this is True. Otherwise it is True exactly when at least one distinctive term (stemmed, a
    synonym counting, as in ``present_terms``) is a word of *title*; the family word and the generic words
    are not distinctive, so ``pdf`` in a title never anchors ``pdf save``. A query with no distinctive term
    is answered with the honest miss before this is reached.
    """
    terms = distinctive_terms(query, family)
    if len(terms) > TITLE_ANCHOR_MAX_TERMS:
        return True
    return bool(present_terms(terms, tokenize(title)))


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


def has_minimum_evidence(query: str) -> bool:
    """False for a query that cannot name anything (TC-562): no run of two or more letters or digits once
    folded (``a.b.c::d``, ``???``, ``x``). ``C++`` and ``C#`` are the one place a single letter carries the
    topic. Such a query is an honest miss for documentation and examples; it cannot be explained by the one-letter
    fragments of its own punctuation.
    """
    folded = fold_query(query)
    return _TWO_CHARACTER_RUN_RE.search(folded) is not None or names_language_letter(folded)


@dataclass(frozen=True)
class EvidenceTerm:
    """One word of a task query as ``search_symbols`` scores a type against it (TC-562): its canonical key,
    every key that counts as the same word (itself, the user-to-API synonyms, the symmetric groups) and whether
    it is a light term (``weak``: it may help a candidate but never has to be explained)."""

    key: str
    forms: frozenset[str]
    weak: bool
    word: str = ""
    family_stem: bool = False
    surface: tuple[str, ...] = ()


def evidence_terms(query: str, family: str) -> tuple[EvidenceTerm, ...]:
    """The terms of *query* for symbol scoring, in query order, without repeats.

    A word is dropped when it is a single letter, one of the index tokenizer's function words, a ``STOP_WORDS``
    word (while ``STOP_WORDS_ENABLED``), or exactly the scope's family word or a generic word ("use", "example").
    A word that only STEMS like the family word ("cell" for the family "cells") stays as a light term, because it
    may be the type's name. When nothing else is left the family and generic words are put back, so "pdf" still
    works. A camel-case run ("PdfDocument") is read as its words.

    TC-585: a word merely ubiquitous within one family's own data (``relevance.family_generic_stems``) is
    deliberately NOT excluded here - ``search_symbols._evidence_ranking`` instead gates the family-stem
    rescue below on ``own_explained`` (a type must explain some STRONG concept literally before collecting
    it), after measuring that excluding such a word from this function's own concept set - and so from
    ``needed``/``total``/``foreign`` - regressed the retrieval corpus (many families' own most common words
    are also their most frequent, genuinely correct answers; see that module's own worker report).
    """
    excluded = _excluded_terms(family)
    family_words = (
        {token for token in tokenize(family)}
        | {family.casefold()}
        | FAMILY_ALIASES.get(family.casefold(), frozenset())
    )
    generic_stems = {stem_token(word) for word in _GENERIC_WORDS}
    kept: list[EvidenceTerm] = []
    common: list[EvidenceTerm] = []
    seen: set[str] = set()
    for run in _ALNUM_RUN_RE.findall(_normalised_query(query)):
        parts = _CAMEL_PARTS_RE.findall(run)
        for word in (part.lower() for part in (parts if len(parts) > 1 else [run])):
            if len(word) == 1 and word.isalpha():
                continue
            if not tokenize(word):
                continue
            if STOP_WORDS_ENABLED and (word in STOP_WORDS or word in _EVIDENCE_FILLER):
                continue
            key = canon(word)
            if key in seen:
                continue
            seen.add(key)
            forms = {key}
            forms |= _USER_SYNONYMS_OF.get(key, frozenset())
            forms |= {canon(value) for value in USER_TERM_SYNONYMS.get(word, ())}
            surface = (word, *USER_TERM_SYNONYMS.get(word, ()))
            term = EvidenceTerm(
                key, frozenset(forms), key in _WEAK_KEYS or key in ACTION_KEYS, word, False, surface
            )
            if word in family_words:
                common.append(term)
                continue
            if stem_token(word) in excluded:
                if stem_token(word) in generic_stems:
                    common.append(term)
                    continue
                term = EvidenceTerm(key, frozenset(forms), True, word, True, surface)
            kept.append(term)
    if kept and all(term.weak for term in kept) and any(term.family_stem for term in kept):
        # "add a slide" on the slides pilots: the family-stem word IS the thing asked about, so it counts.
        kept = [replace(term, weak=False) if term.family_stem else term for term in kept]
    return tuple(kept) if kept else tuple(common)
