"""search_symbols: search foss-mcp's own self-extracted API surface for a symbol name.

CRITICAL, do not inherit this defect: the reference system silently retried without an
explicit filter, and substituted the active generation for a requested one, whenever the
strict search came up empty. This project does the opposite - a miss returns a miss. This
tool never widens its search, never falls back to a different scope, and never reads a
generation other than the one currently active for the scope routing fixed
(``foss_mcp.mcp.routing.resolve_scope``).

``Scope`` (from ``foss_mcp.mcp.routing``) names the PRODUCT a deployment serves
(family, platform) - not which data source within it a particular tool needs.
Self-extracted symbols and furnished documentation are two different sources for the SAME
product, published as two independent generations (different ``source_kind``, hence a
different generation-manifest scope key each); this module fixes ``SOURCE_KIND`` itself
rather than reading ``scope.source_kind``, so a caller cannot point ``search_symbols`` at the
wrong source by passing a ``Scope`` built for something else.

TC-068's example chunks are published into this exact same generation, honestly labeled with
a pseudo-FQN of the literal shape ``Example: <title>`` (never a real class/method FQN, by
design). Those chunks are not symbols and must never be reported as one, so this module
excludes them the same way ``find_examples.py`` already excludes real symbols from its own
matching: query the whole corpus first (never let ``top_k`` cut candidates before filtering),
drop anything whose FQN marks it as an example, THEN truncate to the caller's real ``top_k``.

TC-112's furnished documentation chunks are published into this exact same generation too,
honestly labeled with a parallel pseudo-FQN of the literal shape ``Doc: <title>`` (see
``infra/build_chunks.py``). Those chunks are not symbols either, so ``_NON_SYMBOL_FQN_PREFIXES``
below excludes both prefixes - this generalizes cleanly to a future non-symbol chunk kind
without editing the filter line itself again.

TC-143 (G2/REQ-G2-047): a genuine miss may still carry a purely ADDITIVE ``suggestions`` field -
a "did you mean" hint computed with the standard library's own ``difflib.get_close_matches``
against the real FQNs this exact same miss was computed against. This never widens the verdict
itself: a Miss stays a Miss, the ``reason`` field is untouched, and a suggestion is never
substituted for a real match. See ``suggest_similar_fqns`` below.

TC-278 (G2/REQ-G2-043, C7 of the third independent audit): ``_match_tier`` replaces the old
boolean-only ``is_exact_symbol_hit`` check INSIDE ``search_symbols`` with the strength of
evidence that admitted a candidate - 0 (full-FQN equality), 1 (final-segment equality) or
2 (bag-of-words containment) - and ``search_symbols`` now sorts its admitted candidates by
that tier first, raw BM25 score only as the tiebreak within a tier (FIX A). Before this, a
real base class (a tier-1 hit) could be buried behind its own subclasses (each only a
tier-2 hit) purely because the subclasses' shorter chunks scored a higher raw BM25 term
density - live-confirmed against pdf/net: querying "Annotation" ranked the real base class
``Aspose.Pdf.Annotations.Annotation`` 9th of 14 admitted candidates, behind 8 subclasses.

REWORK (attempt 2): the old ``is_exact_symbol_hit`` boolean wrapper - kept, at first, only
for its one external test caller - is now DELETED entirely rather than left in place. Once
``search_symbols`` itself called ``_match_tier`` directly (to get the tier, not just a bool),
``is_exact_symbol_hit`` had no production caller left anywhere, and this project's own
``tests/test_unwired_modules.py`` correctly flagged it as newly unwired. Its one remaining
caller (``tests/indexing/test_lookup_doc_fallback_query_safety.py``) now calls
``_match_tier(query, fqn) is not None`` directly instead - the exact same boolean meaning,
with no redundant wrapper left unreferenced by anything.

Tier 2 (bag-of-words) also gained a real, measured precision floor (FIX B): the query's own
words, in the query's own order, must now appear as one unbroken, correctly-ordered run
inside the FQN's own word sequence - not merely be present somewhere in the FQN's word SET,
which is all the old check required. Measured directly against 3 real pilots' own lexical
data (pdf/net, cells/cpp, slides/python), mirroring TC-273's own 3-pilot measurement
protocol: a symmetric overlap ratio (the fraction of the FQN's own word count the query's
words cover) does NOT discriminate the real false positive from an already-required true
positive - "set pattern color"/"set color pattern" against the real, unrelated
``Aspose.Pdf.Operators.BasicSetColorAndPatternOperator`` scored 0.333, HIGHER than the
already-required true positive "annotation" against ``Aspose.Pdf.Annotations.Annotation``
(0.250) - so a threshold on that ratio alone cannot reject the false positive without also
rejecting the true positive. The contiguous-run/order constraint, by contrast, correctly
separated every case measured: both real false positives (their 3 query words are scattered
and reordered across the FQN's 9 words) failed it, while every already-required true
positive and every natural-order real multi-word probe tried across all 3 pilots passed it.
This module's own write_paths do not include ``docs/DECISION_LOG.md``; the full measurement
record (exact numbers, the real symbols/queries probed in all 3 pilots) is this card's own
worker report, for the supervisor to fold into the decision log at the gate boundary.

TC-516 (G3/REQ-G3-008): two additions, neither of which touches a tier number, ``_match_tier``, the
Miss, its ``reason``, ``suggestions`` or ``coverage_note``. (a) Within the member-name tier a type that
DECLARES the member ranks before the types that merely INHERIT it (``pdf/go`` ``SetPassword``: the six
field types that inherit it from ``TextBoxField`` no longer outrank ``Document``). (b) A gated per-token
FALLBACK stage runs only when every stage above admitted nothing AND the query contains whitespace: the
distinctive tokens of the query (``naming_tokens``) are matched against each type's own name and the
members it declares, so "Workbook methods", "workbooks" and "send email" find their type while
"PdfDocument.Rotate" (no whitespace), "Watermar" and "AFRelation" stay honest misses. There is no
prefix tier: ``tests/mcp/test_search_symbols_honest_miss.py`` pins those partial words as misses.

TC-279 (G2/REQ-G2-043): a SEPARATE defect from TC-278's ranking/threshold fixes above - a false
NEGATIVE from extraction/matching granularity, not a ranking problem. ``chunk_builder.py``'s
``build_chunks_from_api_surface`` publishes one chunk per TYPE, with a single ``FQN:`` line
naming only the class; every method/property is rendered as prose inside that same chunk's
``Methods:``/``Properties:`` block, with no FQN of its own. A query naming a real METHOD or
PROPERTY (e.g. ``SetRotation`` for the real ``Aspose.Pdf.Page.SetRotation``) therefore never
passed ``_match_tier`` - a tier-0/1 FQN-equality check and a tier-2 bag-of-words check both
compare only against the type's own FQN - however highly BM25 ranked the containing chunk,
producing a wrong, misleading Miss for a symbol that genuinely exists. Live-confirmed: querying
"SetRotation" ranked the real ``Aspose.Pdf.Page`` chunk #1 by a wide BM25 margin, yet returned
an honest-LOOKING but WRONG Miss.

The architecture decision (the supervisor's, recorded on this card): do not change chunk
granularity - get_symbol/list_members already depend on one-chunk-per-type, and one chunk per
method would be a much larger, riskier change to the whole ingestion/indexing/storage pipeline.
Instead, widen ``search_symbols``'s own matching step: ``_member_name`` parses a rendered
Methods:/Properties: block item's leading identifier (reusing ``get_symbol.py``'s own block-
parsing logic rather than reimplementing it), and a query that exactly names that member - by
its own bare name, or as "Type.Member"/"Type::Member" using the chunk's own FQN's final segment
- admits the chunk as a hit. Returning the TYPE's full chunk is correct: it already contains the
matched method's real signature in context, and there is no separate method-level chunk to
return instead.

This new member-match tier sits between TC-278's tier 1 (final-segment class equality) and its
tier 2 (bag-of-words), which is renumbered to tier 3 here: a bare, whole-member-name match is
strong, unambiguous evidence - similar strength to a final-segment class match, and never weaker
than bag-of-words, which has much weaker evidence per match. The real ambiguity case - the same
bare member name declared on multiple distinct types - was verified directly against the real
pdf/net fixture rather than assumed: "Dispose" is genuinely declared on 9 distinct real types
(``Aspose.Pdf.Page``, ``Aspose.Pdf.Document``, ``Aspose.Pdf.XForm`` and 6 others). This tier
admits ALL of them, exactly like TC-278's own bag-of-words tier already admits every one of
"Annotation"'s 8 real subclasses side by side - BM25 sorts among tied-tier candidates, same as
every other tier here, so no special-casing was needed or added.

TC-541 (G3/REQ-G3-008): within one match tier a chunk that lists at least one method, property or
enum member ranks before a chunk that lists none (``_has_listed_members``), so an empty alias of a
Python class (a re-export shell the data refresh has not yet paired with its definition) never answers
before the real class. The key sits second, behind the tier and ahead of declaring-before-inheriting and
BM25, which are unchanged; no tier number, ``_match_tier``, the Miss or the fallback stage changes.

TC-585 (G3/REQ-G3-009): ``_evidence_ranking`` and ``_fallback_doc_ids`` (via ``naming_tokens``) now also
exclude a word measured ubiquitous within the SCOPE's own data (``relevance.family_generic_stems``,
additive to the existing global ``_GENERIC_WORDS``) - independent reproduction found "file" on pdf,
"color" on slides and "cell" on cells, each a literal type/member-name match common enough across that
one family's own API surface to bury a correct, differently-named answer that does not happen to share
the word, exactly the kind of defect ``_match_tier``'s own tiers above are immune to (they require the
WHOLE query, not one common word of it) but the sentence-level evidence stage was not.
"""

from __future__ import annotations

import math
import re
import string
from collections import OrderedDict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from typing import TYPE_CHECKING

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored, tokenize
from foss_mcp.mcp.routing import Scope

if TYPE_CHECKING:  # relevance imports get_symbol, which imports this module: only the annotation needs it
    from foss_mcp.mcp.tools.relevance import EvidenceTerm

SOURCE_KIND = "self_extracted"

_NON_SYMBOL_FQN_PREFIXES = ("Example: ", "Doc: ")


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD_SEPARATORS = re.compile(r"[\s._:]+")
_FINAL_SEGMENT = re.compile(r"\.|::")


def _symbol_words(text: str) -> list[str]:
    """The lowercased words of *text*, split at camel-case boundaries and at whitespace, dots,
    colons and underscores (``PdfDocument.AddWatermarkAnnotation`` -> pdf, document, add, watermark,
    annotation; ``Aspose::Pdf::AFRelationship`` -> aspose, pdf, af, relationship).
    """
    spaced = _CAMEL_BOUNDARY.sub(" ", text)
    return [word.casefold() for word in _WORD_SEPARATORS.split(spaced) if word]


def _match_tier(query: str, fqn: str | None) -> int | None:
    """The strength of evidence that *query* names *fqn*, as a tier - 0 (strongest) to 2
    (weakest) - or ``None`` for no match at all. The three tiers, in admission order:

    0. Full-FQN equality: *query* equals the whole FQN, case-insensitively after stripping.
    1. Final-segment equality: *query* equals the FQN's final dotted (or ``::``-scoped)
       segment, same comparison.
    2. Bag-of-words containment with a contiguous-run precision floor (TC-278, G2/REQ-G2-043):
       every word of *query* is a whole word among the words of *fqn* (words split at
       camel-case boundaries, dots, ``::`` and underscores) AND, additionally, the query's
       own words - in the query's own order - appear as one unbroken, correctly-ordered run
       inside the FQN's own word sequence. ``watermark`` is a contiguous one-word run of
       ``PdfDocument.AddWatermarkAnnotation``, so it is a tier-2 hit. A scattered, reordered
       multi-word query (e.g. "set pattern color" against an FQN whose own words are
       "...set color ... pattern...") satisfies the bag-of-words containment alone but fails
       this additional floor, so it is not a hit at any tier - measured empirically against 3
       real pilots (see this module's own docstring) rather than guessed; a plain
       containment-SET check alone cannot tell this case apart from a real
       match, and a symmetric overlap-ratio threshold cannot either (the measured false
       positive scores higher on that signal than an already-required true positive).

    A partial word such as ``Watermar`` is never a whole word of anything, so it never reaches
    tier 2 regardless of this floor. A chunk with no FQN line (*fqn* is ``None``) can never
    match at any tier.

    This is CLASS-level evidence only - it never looks inside the chunk's own rendered
    Methods:/Properties: blocks. ``search_symbols`` (TC-279, G2/REQ-G2-043) combines this
    result with ``_matches_a_rendered_member`` below to place a member-name match between this
    function's own tier 1 and tier 2, remapping this function's tier 2 (bag-of-words) to overall
    tier 3 - see ``search_symbols``'s own docstring for the combined ordering.
    """
    if fqn is None:
        return None
    needle = query.strip().casefold()
    if not needle:
        return None
    full = fqn.strip().casefold()
    if needle == full:
        return 0
    if needle == _FINAL_SEGMENT.split(full)[-1]:
        return 1
    query_words = _symbol_words(query)
    if not query_words:
        return None
    fqn_words = _symbol_words(fqn)
    fqn_word_set = set(fqn_words)
    if not all(word in fqn_word_set for word in query_words):
        return None
    run_length = len(query_words)
    window_count = len(fqn_words) - run_length + 1
    if window_count < 1:
        return None
    for start in range(window_count):
        if fqn_words[start : start + run_length] == query_words:
            return 2
    return None


_MEMBER_NAME_END = re.compile(r"\(|:| \((?:inherited|promoted) from")


def _member_name(item: str) -> str:
    """The member's own leading identifier, parsed off one already-stripped item of a rendered
    Methods:/Properties: block (as returned by get_symbol.py's block-parsing helper) - the text
    before its first ``(`` (a method's own parameter list opening), ``:`` (a property's own
    type), or `` (inherited from``/`` (promoted from`` suffix, whichever comes first.
    ``chunk_builder.py``'s ``_method_line``/``_property_line`` always render one of ``(`` or
    ``:`` immediately after the name itself (``"SetRotation(degrees: int) -> void"``,
    ``"Name: Type (writable)"``), so the `` (inherited from``/`` (promoted from`` alternative is
    a defensive fallback rather than one these two real conventions currently need.
    """
    match = _MEMBER_NAME_END.search(item)
    return (item[: match.start()] if match is not None else item).strip()


# TC-571 (REQ-G3-009): a Go struct-embedding member chunk_builder.py renders as
# "(promoted from X)" never DECLARES the member either - it is PROMOTED through an embedded
# field, not inherited through a base class, but for ranking purposes it is exactly as
# non-declaring as the original "(inherited from X)" marker.
_NON_DECLARING_MARKERS = ("(inherited from ", "(promoted from ")


def _is_non_declaring_item(item: str) -> bool:
    """True when *item* (one already-stripped line of a rendered Methods:/Properties: block)
    carries either non-declaring marker - the type listing *item* does not itself declare this
    member; it only inherits or promotes it."""
    return any(marker in item for marker in _NON_DECLARING_MARKERS)


def _member_declaration(query: str, fqn: str | None, text: str) -> bool | None:
    """``None`` when *query* names no method or property this chunk's own already-rendered
    Methods:/Properties: blocks list; otherwise whether the type DECLARES a member that *query* names
    (``True``) or only INHERITS it (``False``: every matching item carries ``(inherited from ...)``).

    A match is the member's own bare name, or "Type.Member"/"Type::Member" using *fqn*'s own final
    segment, case-insensitively (TC-279, G2/REQ-G2-043). Parses the blocks with get_symbol.py's own
    existing block-parsing helper rather than reimplementing it - the same convention
    ``get_symbol``/``list_members`` already parse this exact text with. A partial/truncated member name
    (e.g. ``SetRotatio``) is never equal to a real member's own full name, so it is never a hit,
    honestly mirroring ``_match_tier``'s own partial-word floor.
    """
    from foss_mcp.mcp.tools.get_symbol import _extract_block

    needle = query.strip().casefold()
    if not needle:
        return None
    final_segment = _FINAL_SEGMENT.split(fqn.strip())[-1].casefold() if fqn else None
    declared: bool | None = None
    for header in ("Methods:", "Properties:"):
        for item in _extract_block(text, header):
            member_name = _member_name(item).casefold()
            if not member_name:
                continue
            named = needle == member_name or (
                final_segment is not None
                and needle in (f"{final_segment}.{member_name}", f"{final_segment}::{member_name}")
            )
            if not named:
                continue
            if not _is_non_declaring_item(item):
                return True
            declared = False
    return declared


def _has_listed_members(text: str) -> bool:
    """True when the chunk *text* lists at least one item under ``Methods:``, ``Properties:`` or ``Members:``
    (TC-541)."""
    from foss_mcp.mcp.tools.get_symbol import _extract_block

    return any(_extract_block(text, header) for header in ("Methods:", "Properties:", "Members:"))


def _matches_a_rendered_member(query: str, fqn: str | None, text: str) -> bool:
    """True when *query* exactly names (case-insensitively) a method or property this chunk's
    own already-rendered Methods:/Properties: blocks list (see ``_member_declaration``)."""
    return _member_declaration(query, fqn, text) is not None


# TC-516: words that carry no name of their own when a query is read as "which type is meant".
_NAMING_NOISE_WORDS = frozenset(
    {"method", "methods", "class", "classes", "property", "properties", "api", "using", "what", "does"}
)
_NAMING_MIN_LENGTH = 3
_FALLBACK_MIN_SHARE = 0.6
_QUERY_POSSESSIVE = re.compile(r"['\u2019]s$")


def naming_tokens(query: str, family: str, *, extra_generic: Collection[str] = ()) -> list[str]:
    """The casefolded words of *query* that could name a type or a member, in query order.

    Whitespace-split; surrounding punctuation and a possessive suffix removed; a word is dropped when it
    is shorter than three characters, a stopword ("how", "do", "I"), one of the noise words ("method",
    "methods", "class", "classes", "property", "properties", "api", "using", "what", "does"), a generic
    word ("use", "example"), one of *extra_generic*'s stems (TC-585: ubiquitous within this one family's
    own data - see ``relevance.family_generic_stems``) or the scope's own family word. A dotted or scoped
    word ("PdfDocument.Rotate") stays one token, which names no type, so it never matches by its parts.
    """
    from foss_mcp.mcp.tools.relevance import is_common_word  # lazy: get_symbol imports this module

    tokens: list[str] = []
    for raw in query.split():
        word = _QUERY_POSSESSIVE.sub("", raw.strip(string.punctuation + "`").casefold())
        if len(word) < _NAMING_MIN_LENGTH or word in _NAMING_NOISE_WORDS:
            continue
        if not tokenize(word) or is_common_word(word, family, extra_generic=extra_generic):
            continue
        tokens.append(word)
    return tokens


def token_forms(token: str) -> frozenset[str]:
    """*token* and its plural-stripped forms ("workbooks" -> "workbook", "classes" -> "class")."""
    forms = {token}
    if token.endswith("es") and len(token) > 3:
        forms.add(token[:-2])
    if token.endswith("s") and len(token) > 3:
        forms.add(token[:-1])
    return frozenset(forms)


def _declared_member_names(text: str) -> set[str]:
    """The casefolded names of the members *text* renders WITHOUT an ``(inherited from ...)`` or
    ``(promoted from ...)`` marker."""
    from foss_mcp.mcp.tools.get_symbol import _extract_block

    names: set[str] = set()
    for header in ("Methods:", "Properties:"):
        for item in _extract_block(text, header):
            if _is_non_declaring_item(item):
                continue
            name = _member_name(item).casefold()
            if name:
                names.add(name)
    return names


def suggest_similar_fqns(known_fqns: Iterable[str], target: str, *, limit: int = 3) -> tuple[str, ...]:
    """Fuzzy "did you mean" suggestions for *target*, drawn only from *known_fqns*.

    *known_fqns* must be the real FQNs published in the exact generation a Miss/NotFound was
    just computed against - never a separate or stale data source. Deterministic (same inputs
    always produce the same output) and dependency-free: this is a thin wrapper over the
    standard library's own ``difflib.get_close_matches``, never a new dependency or a network
    call. Purely additive - callers attach the result to a Miss/NotFound's own ``suggestions``
    field; it never changes whether a query is a miss or a real match.

    The full-FQN comparison (cutoff 0.6) is tried first and its result is returned whenever it is
    non-empty. Only when it is empty (TC-386) is *target*'s final segment (split on ``.`` and
    ``::``) compared against each known FQN's final segment at cutoff 0.75, so a misspelt bare
    type name such as ``Workbok`` still finds ``Workbook`` although every known FQN is
    namespaced; the matching full FQNs are returned deduplicated, at most *limit* of them.
    """
    known = list(known_fqns)
    by_full = tuple(get_close_matches(target, known, n=limit, cutoff=0.6))
    if by_full:
        return by_full
    target_segment = _FINAL_SEGMENT.split(target.strip())[-1]
    if not target_segment:
        return ()
    by_segment: dict[str, list[str]] = {}
    for fqn in known:
        by_segment.setdefault(_FINAL_SEGMENT.split(fqn.strip())[-1], []).append(fqn)
    suggestions: list[str] = []
    for segment in get_close_matches(target_segment, sorted(by_segment), n=limit, cutoff=0.75):
        for fqn in sorted(set(by_segment[segment])):
            if fqn not in suggestions:
                suggestions.append(fqn)
    return tuple(suggestions[:limit])


def scope_key(scope: Scope, source_kind: str) -> str:
    """The generation-manifest scope string for *scope* at *source_kind* - the same
    ``family::platform::source_kind`` format ``GenerationKey.scope`` uses.
    """
    return "::".join((scope.family, scope.platform, source_kind))


MATCH_EXACT = "exact"
MATCH_PARTIAL = "partial"

# TC-559's fixed phrase: a symbol chunk that carries it documents something the FOSS build does not implement.
NOT_IMPLEMENTED_PHRASE = "not implemented in this build"


@dataclass(frozen=True)
class SymbolMatch:
    """One symbol chunk. Everything after ``text`` is additive (TC-562) and defaults to "nothing said":

    - ``match_quality``: ``"exact"`` when the query is explained by this type (its name, or the member it
      declares), ``"partial"`` when only part of the query is (``unmatched_terms`` names the rest), ``""`` for a
      hand-built match;
    - ``total_matches`` / ``next_offset``: how many symbols answered the query in all and the ``offset`` that
      returns the next page (``None`` on the last page; ``total_matches`` 0 when unknown);
    - ``aliases``: the other FQNs whose chunk is the same type under another name (a re-export), not repeated;
    - ``limitation``: the line of the chunk that says the symbol is "not implemented in this build", when it does.
    """

    scope: Scope
    generation_id: str
    doc_id: str
    chunk_id: str
    text: str
    match_quality: str = ""
    unmatched_terms: tuple[str, ...] = ()
    total_matches: int = 0
    next_offset: int | None = None
    aliases: tuple[str, ...] = ()
    limitation: str = ""


def limitation_line(text: str) -> str:
    """The first line of a chunk that carries TC-559's phrase ``not implemented in this build``, else ``""``."""
    for line in text.splitlines():
        if NOT_IMPLEMENTED_PHRASE in line.lower():
            return line.strip()
    return ""


@dataclass(frozen=True)
class Miss:
    """An explicit miss. This IS the answer - never smoothed over into a widened result.

    ``suggestions`` (TC-143, G2/REQ-G2-047) is purely additive: a real "did you mean" hint
    computed from the FQNs actually published in this same generation, never a substitute for
    the miss itself and never present unless genuinely computed from real published content.
    """

    scope: Scope
    query: str
    reason: str
    suggestions: tuple[str, ...] = ()
    # TC-515: the API-coverage caveat, set on a miss from a generation that exists when its symbol
    # chunks state that the index holds fewer types than the library has (``None`` otherwise).
    coverage_note: str | None = None


def _safe_extra_generic(
    words: list[str], family: str, lexical_payload: Mapping[str, object] | None
) -> frozenset[str]:
    """TC-585: ``relevance.family_generic_stems``'s candidates for *words*, restricted to the largest
    most-ubiquitous-first subset that does not empty *words* of every STRONG term (``evidence_terms``'s
    own baseline classification, extra_generic-free: not a light/action verb, not a single letter or
    stopword, not already weak via the family-stem rescue) when at least one such term existed before any
    per-family exclusion.

    "change a workbook cell value" (cells) has three candidates over ``FAMILY_UBIQUITY_FRACTION``
    ("cell", "workbook", "value"); excluding all three from the fallback stage's own ``naming_tokens``
    would leave only the light verb "change" there and turn an already-correct answer into a Miss. "cell"
    is never itself a STRONG baseline term here (it is the family's own stem, already weak via the
    family-stem rescue), so excluding it costs nothing; excluding "workbook" and "value" too would cost
    everything, so the greedy selection stops before the last real one - but only because this query
    started with TWO strong terms to protect. A query whose only strong term is the lone per-family-generic
    candidate ("open a pdf file", pdf: "file" is the only one) has nothing left to protect either way, so
    the full candidate set is returned unrestricted.

    A candidate that is ALSO the bare, whole name of a real type in this exact generation ("Document",
    "Page", "Shape" are all real pdf/slides types, as common in prose as "file" ever is on pdf) is never
    excluded regardless of its measured fraction: measured directly against the full retrieval corpus,
    treating such a word as generic broke "create a new word document" -> ``Document`` and "give a shape
    a name" -> ``Shape`` on multiple platforms (a real, central type is not the kind of accidental
    literal-name collision this card's purpose describes), while "file" and "color" are never themselves
    a bare type's whole name on the pilots this card's own reproduction measured.
    """
    from foss_mcp.mcp.tools.get_symbol import extract_fqn
    from foss_mcp.mcp.tools.relevance import (
        evidence_terms,
        family_generic_fraction,
        family_generic_stems,
        stem_token,
    )

    stems = {stem_token(word) for word in words}
    candidates = family_generic_stems(stems, family, lexical_payload)
    if not candidates:
        return frozenset()
    documents = (lexical_payload or {}).get("documents") or {}
    real_type_stems: set[str] = set()
    for document in documents.values():
        fqn = extract_fqn(document["text"])
        if fqn is None or fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        real_type_stems.add(stem_token(_FINAL_SEGMENT.split(fqn.strip())[-1]))
    candidates = candidates - real_type_stems
    if not candidates:
        return frozenset()
    baseline_terms = evidence_terms(" ".join(words), family)
    strong_stems = {stem_token(term.word) for term in baseline_terms if not term.weak}
    if len(strong_stems) < 2:
        # Nothing to protect (no strong term at all), or only the one the bug itself turns on - either
        # way there was never a second, independent strong term whose loss would be a real regression.
        return candidates
    ranked = sorted(candidates, key=lambda stem: -family_generic_fraction(stem, lexical_payload))
    chosen: set[str] = set()
    for stem in ranked:
        if strong_stems - (chosen | {stem}):
            chosen.add(stem)
    return frozenset(chosen)


def _fallback_doc_ids(
    documents: dict[str, dict],
    ranked: list[tuple[str, float]],
    query: str,
    family: str,
    top_k: int,
    lexical_payload: Mapping[str, object] | None = None,
) -> list[str]:
    """TC-516: the per-token fallback, for a query that every stage above left without a symbol.

    Runs only when *query* contains whitespace (a dotted or scoped name such as ``PdfDocument.Rotate``
    therefore stays a miss). Each ``naming_tokens`` token is matched against a symbol chunk's own FQN
    final segment (case-insensitively, plural-stripped) or a member the type DECLARES; a chunk is admitted
    when at least 60 percent of the tokens (and at least one) match. Ranked by the number of matched
    tokens, then by the tokens that match the type's own name (a declaring type before one that merely
    lists the member by name), then by BM25. There is no prefix matching.

    TC-585: *lexical_payload*, when given, measures which of the query's own words are ubiquitous within
    this one family's own data (``_safe_extra_generic``) and drops them from ``naming_tokens`` too, so a
    word excluded upstream (``_evidence_ranking``) cannot still gate this later stage's own 60-percent
    floor on its own account - the same safe subset, never emptying every strong word of the query.
    """
    from foss_mcp.mcp.tools.get_symbol import extract_fqn

    if not any(character.isspace() for character in query.strip()):
        return []
    extra_generic = _safe_extra_generic(query.split(), family, lexical_payload)
    tokens = naming_tokens(query, family, extra_generic=extra_generic)
    if not tokens:
        return []
    needed = max(1, math.ceil(_FALLBACK_MIN_SHARE * len(tokens)))
    bm25 = dict(ranked)
    candidates: list[tuple[int, int, float, str]] = []
    for doc_id, document in documents.items():
        text = document["text"]
        fqn = extract_fqn(text)
        if fqn is None or fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        segment = _FINAL_SEGMENT.split(fqn.strip())[-1].casefold()
        members = _declared_member_names(text)
        by_name = 0
        by_member = 0
        for token in tokens:
            forms = token_forms(token)
            if segment in forms:
                by_name += 1
            elif not forms.isdisjoint(members):
                by_member += 1
        if by_name + by_member >= needed:
            candidates.append((by_name + by_member, by_name, bm25.get(doc_id, 0.0), doc_id))
    candidates.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    return [doc_id for _matched, _named, _score, doc_id in candidates[:top_k]]


# ---------------------------------------------------------------------------------------------------------
# TC-562 (G3/REQ-G3-009): the evidence stage - a task phrased in the user's words finds the type that does it.
# ---------------------------------------------------------------------------------------------------------

_CAMEL_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_FIRST_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOWER_THEN_CAPITAL = re.compile(r"[a-z][A-Z]")
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]+")
_BODY_SKIPPED_PREFIXES = ("FQN:", "Defined-In:", "Exported-As:", "API-Coverage:", "Source-Commit:", "Part:")

TYPE_WORD_WEIGHT = 2.0
MEMBER_WORD_WEIGHT = 1.5
SYNONYM_FACTOR = 0.4  # a word the user did not type, only a synonym of it, counts this much of an own word
BOTH_PLACES_BONUS = 0.5  # the word is in the type's name AND in a member it declares
LIGHT_TERM_BONUS = 0.5
TEXT_RELEVANCE_WEIGHT = 2.0  # the chunk's own prose (BM25 over the user's words), scaled to 0..1
NAME_TYPE_WEIGHT = 3.5
NAME_MEMBER_WEIGHT = 2.5
# Words a user adds to say what kind of thing they want ("Workbook methods"), not what it is called.
_EVIDENCE_NOISE_WORDS = frozenset(
    {
        "method",
        "methods",
        "class",
        "classes",
        "function",
        "functions",
        "type",
        "types",
        "api",
        "member",
        "members",
    }
)
_ENTRY_CACHE_SIZE = 6


@dataclass(frozen=True)
class _Entry:
    """One symbol chunk as the evidence stage reads it: the canonical words of its type name, of the members
    it DECLARES (an inherited member is the base type's evidence, not this one's) and of its whole names."""

    doc_id: str
    fqn: str
    short: str
    type_keys: frozenset[str]
    whole_key: str
    member_keys: frozenset[str]
    member_wholes: frozenset[str]
    has_members: bool
    body_hash: int


# A generation is read once per process; the cache holds the generation's own documents object so an id can never
# be mistaken for another generation's.
_entry_cache: OrderedDict[int, tuple[Mapping[str, Mapping[str, str]], list[_Entry]]] = OrderedDict()


def _words_of(name: str) -> list[str]:
    parts: list[str] = []
    for piece in re.split(r"[_\W]+", name):
        parts.extend(_CAMEL_PARTS.findall(piece))
    return [part.lower() for part in parts]


def _whole_key(name: str) -> str:
    from foss_mcp.mcp.tools.relevance import canon

    return canon(re.sub(r"[^a-z0-9]", "", name.lower()))


def _entries(documents: Mapping[str, Mapping[str, str]]) -> list[_Entry]:
    from foss_mcp.mcp.tools.get_symbol import _extract_block, extract_fqn
    from foss_mcp.mcp.tools.relevance import canon

    cached = _entry_cache.get(id(documents))
    if cached is not None and cached[0] is documents:
        _entry_cache.move_to_end(id(documents))
        return cached[1]
    entries: list[_Entry] = []
    for doc_id, document in documents.items():
        text = document["text"]
        fqn = extract_fqn(text)
        if fqn is None or fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        short = _FINAL_SEGMENT.split(fqn.strip())[-1]
        if not short:
            continue
        names: list[str] = []
        for header in ("Methods:", "Properties:", "Members:"):
            for item in _extract_block(text, header):
                if _is_non_declaring_item(item):
                    continue
                found = _FIRST_IDENTIFIER.match(item.strip())
                if found is not None and found.group(0) != "this":
                    names.append(found.group(0))
        body = "\n".join(line for line in text.splitlines() if not line.startswith(_BODY_SKIPPED_PREFIXES))
        entries.append(
            _Entry(
                doc_id=doc_id,
                fqn=fqn.strip(),
                short=short,
                type_keys=frozenset(canon(word) for word in _words_of(short)),
                whole_key=_whole_key(short),
                member_keys=frozenset(canon(word) for name in names for word in _words_of(name)),
                member_wholes=frozenset(_whole_key(name) for name in names),
                has_members=bool(names),
                body_hash=hash(body),
            )
        )
    _entry_cache[id(documents)] = (documents, entries)
    while len(_entry_cache) > _ENTRY_CACHE_SIZE:
        _entry_cache.popitem(last=False)
    return entries


def _plain_words(query: str) -> list[str] | None:
    """The whitespace-separated words of *query* with surrounding punctuation removed, or ``None`` when any word is
    dotted, scoped or underscored (an exact-name query: ``PdfDocument.Rotate``, ``Widget_methods``), which the
    evidence stage never reads as a sentence."""
    words = [word.strip(string.punctuation + "`") for word in query.split()]
    words = [word for word in words if word]
    if not words or any(re.search(r"[._:/\\]", word) for word in words):
        return None
    return words


def _identifier_names(words: list[str]) -> tuple[list[str], list[str]]:
    """``(names, rest)``: the identifier-like words (a lower-case letter then a capital: ``PdfDocument``) and the
    other words. A name is matched whole, never by its parts."""
    names = [word for word in words if _LOWER_THEN_CAPITAL.search(word) and _ALNUM_RUN.fullmatch(word)]
    return names, [word for word in words if word not in names]


def _inflections(token: str) -> set[str]:
    """*token* and its plain English inflections stripped (plural, -ing, -ed), for comparison with a whole name."""
    forms = {token}
    if len(token) > 3 and token.endswith("ies"):
        forms.add(token[:-3] + "y")
    if len(token) > 3 and token.endswith("es"):
        forms.add(token[:-2])
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        forms.add(token[:-1])
    if len(token) > 5 and token.endswith("ing"):
        base = token[:-3]
        forms.update({base, base + "e"})
        if len(base) > 2 and base[-1] == base[-2]:
            forms.add(base[:-1])
    if len(token) > 4 and token.endswith("ed"):
        base = token[:-2]
        forms.update({base, base + "e", token[:-1]})
    return forms


def _inflected_name_ids(documents: Mapping[str, Mapping[str, str]], query: str) -> list[str]:
    """TC-562: the symbols whose whole name is *query* up to an English inflection (``Widgets`` -> ``Widget``;
    ``Do``, a word the index tokenizer drops, is found here too). One plain word only: a dotted, scoped or
    underscored query, a partial word and a sentence never reach it."""
    words = _plain_words(query)
    if words is None or len(words) != 1 or not words[0].isalnum():
        return []
    wanted = {form.casefold() for form in _inflections(words[0])}
    return [entry.doc_id for entry in _entries(documents) if entry.short.casefold() in wanted]


@dataclass(frozen=True)
class _Scored:
    entry: _Entry
    score: float
    explained: int
    unexplained: tuple[str, ...]
    # How much of the type's own name the question accounts for, whether a member the type declares carried part of
    # the explanation, and whether the type lives in an internal namespace: they decide ``exact`` or ``partial``.
    fit: float = 0.0
    via_member: bool = False
    internal: bool = False
    first_asked: int = (
        0  # position in the question of the first word this type explains (query order on a tie)
    )

    @property
    def exact(self) -> bool:
        """Every strong word explained AND the type is plausibly what was asked for: its name is at least half
        made of the words asked, or a member it declares explains one, and it is not an internal helper."""
        return not self.unexplained and (self.fit >= 0.5 or self.via_member) and not self.internal


@dataclass(frozen=True)
class _Concept:
    """One thing the user asked about: the words they typed for it (``keys``) and every key that counts as it."""

    word: str
    keys: frozenset[str]
    forms: frozenset[str]
    surface: tuple[str, ...]


def _concepts(terms: list[EvidenceTerm]) -> list[_Concept]:
    """Merges terms that are synonyms of each other ("protect" and "password") into one concept, so a type that
    carries both words is not paid twice for the same idea."""
    concepts: list[_Concept] = []
    for term in terms:
        for index, concept in enumerate(concepts):
            if term.key in concept.forms or concept.keys & term.forms:
                concepts[index] = _Concept(
                    concept.word,
                    concept.keys | {term.key},
                    concept.forms | term.forms,
                    concept.surface + term.surface,
                )
                break
        else:
            concepts.append(_Concept(term.word, frozenset({term.key}), term.forms, term.surface))
    return concepts


_INTERNAL_NAMESPACE = re.compile(
    r"[.:]internal[.:]|[.:]core[.:]|[.:]xml[.:]|[.:]engine[.:]|[.:]impl[.:]", re.IGNORECASE
)
INTERNAL_PENALTY = 2.0
INTERFACE_PENALTY = 1.0
NAME_FIT_WEIGHT = 4.0
PARTIAL_MIN_FIT = 0.5


def _evidence_ranking(
    documents: Mapping[str, Mapping[str, str]], query: str, family: str, lexical_payload: Mapping[str, object]
) -> list[_Scored]:
    """TC-562: the symbol chunks that explain a task query, best first; ``[]`` when none does.

    The query's words become terms (``relevance.evidence_terms``: stop words and function words dropped, each
    word widened one way to the words an API uses; terms that are synonyms of each other are one concept). A
    STRONG concept is explained by a type when it is a word of the type's name (3 points) or of a member the type
    declares (1.5), each scaled by how few types carry the word (IDF) and by 0.6 when only a synonym of the user's
    own word matched; an identifier-like word of the query (``PdfDocument``) is explained only by that whole name.
    A light verb ("add", "create") never has to be explained and earns half a point when it is. A type is admitted
    when it explains at least 60 percent of the strong concepts, or, when every strong concept is known somewhere
    in the generation (no foreign word such as "pivot" or "teleport"), at least half of them. The score also
    rewards a type whose name is made of the words asked for (``Workbook`` over ``XlsxWorkbookStylesXml``), the
    chunk's own prose (BM25 over the words and their synonyms), and demotes an internal namespace and an
    ``I``-interface that has a class beside it. Ranked by score, then the shorter name, then a type that lists
    members.

    TC-585: a type whose own NAME merely stems like the family's own word ("Cell"/"Cells" for family
    "cells") is rescued into the fit score below by ``evidence_terms``'s family-stem light term - see
    ``own_explained`` and the family-stem rescue site inside the per-entry loop for the measured gate on
    that rescue (a type must explain at least one STRONG concept LITERALLY, own not synonym, before it
    collects this additional credit). Trying the broader fix this card's purpose first suggested - also
    excluding a per-family-ubiquitous word (``relevance.family_generic_stems``) from being a REQUIRED
    concept here at all - regressed the full retrieval corpus every time it was tried, because many
    families' own most common words ("document", "page", "shape", "text") are also their most frequent,
    genuinely correct answers; this module's worker report records the measured attempts and numbers.
    """
    from foss_mcp.mcp.tools.relevance import RELEVANCE_THRESHOLD, evidence_terms

    words = _plain_words(query)
    if words is None:
        return []
    names, rest = _identifier_names(words)
    terms = [
        term for term in evidence_terms(" ".join(rest), family) if term.word not in _EVIDENCE_NOISE_WORDS
    ]
    concepts = _concepts([term for term in terms if not term.weak])
    light = [term for term in terms if term.weak]
    name_keys = [(name, _whole_key(name)) for name in names]
    entries = _entries(documents)
    if not entries:
        return []
    vocabulary_words: set[str] = set()
    vocabulary_wholes: set[str] = set()
    shorts = {entry.short for entry in entries}
    for entry in entries:
        vocabulary_words |= entry.type_keys | entry.member_keys
        vocabulary_wholes |= {entry.whole_key} | entry.member_wholes
    total = len(concepts) + len(name_keys)
    if total == 0:
        return []
    foreign = sum(1 for concept in concepts if not (concept.forms & vocabulary_words)) + sum(
        1 for _name, key in name_keys if key not in vocabulary_wholes
    )
    needed = max(1, math.ceil(RELEVANCE_THRESHOLD * total))
    # A word that names half the generation (page, document) says little; a word few types carry says a lot.
    weights: dict[str, float] = {}
    for concept in concepts:
        carrying = sum(1 for entry in entries if concept.keys & (entry.type_keys | entry.member_keys))
        if carrying == 0:
            carrying = sum(1 for entry in entries if concept.forms & (entry.type_keys | entry.member_keys))
        weights[concept.word] = math.log(1 + len(entries) / (1 + carrying)) / math.log(1 + len(entries))
    expanded = " ".join([*words, *(word for concept in concepts for word in concept.surface)])
    bm25 = dict(query_lexical_index_scored(lexical_payload, expanded, top_k=len(documents)))  # type: ignore[arg-type]
    top_bm25 = max(bm25.values(), default=0.0) or 1.0
    scored: list[_Scored] = []
    for entry in entries:
        score = 0.0
        explained = 0
        via_member = False
        first_asked = len(concepts) + len(name_keys)
        matched_type_words: set[str] = set()
        missing: list[str] = []
        own_explained = False
        for position, concept in enumerate(concepts):
            weight = weights[concept.word]
            if concept.forms & entry.type_keys:
                own = bool(concept.keys & entry.type_keys)
                score += weight * TYPE_WORD_WEIGHT * (1.0 if own else SYNONYM_FACTOR)
                if concept.forms & entry.member_keys:
                    score += weight * BOTH_PLACES_BONUS
                matched_type_words |= concept.forms & entry.type_keys
                explained += 1
                own_explained = own_explained or own
                first_asked = min(first_asked, position)
            elif concept.forms & entry.member_keys:
                own = bool(concept.keys & entry.member_keys)
                score += weight * MEMBER_WORD_WEIGHT * (1.0 if own else SYNONYM_FACTOR)
                explained += 1
                own_explained = own_explained or own
                via_member = True
                first_asked = min(first_asked, position)
            else:
                missing.append(concept.word)
        for name, key in name_keys:
            if key == entry.whole_key:
                score += NAME_TYPE_WEIGHT
                explained += 1
                matched_type_words |= entry.type_keys
                own_explained = True
            elif key in entry.member_wholes:
                score += NAME_MEMBER_WEIGHT
                explained += 1
                own_explained = True
                via_member = True
            else:
                missing.append(name)
        if explained == 0:
            continue
        for term in light:
            if term.forms & entry.member_keys:
                score += LIGHT_TERM_BONUS
            # TC-585: the family-stem rescue below credits ANY type merely NAMED after the family's own
            # stem ("Cell", "Cells" on the cells pilots) with a full NAME_FIT_WEIGHT bonus - reproduced:
            # for "wrap text in a cell", that bonus alone (``fit`` 0 -> 1.0) was enough to admit ``Cells``
            # (which explains "text" only through the "string" synonym) as a PARTIAL match ahead of
            # ``Style``/``Alignment``, the types that genuinely declare ``WrapText`` and explain the whole
            # question. Gated on ``own_explained``: a type that explains at least one STRONG concept
            # LITERALLY (own, type or member) earns the rescue same as before ("add a slide" on the
            # slides pilots: the promotion rule above turns "slide" into a real, non-weak concept there,
            # explaining ``Slide`` via its own name directly); one whose only explanation is itself a
            # synonym no longer gets the family name for free on top of it.
            if term.family_stem and term.forms & entry.type_keys and own_explained:
                # only a light NOUN ("cell" on the cells pilots) counts towards the name; a verb does not
                matched_type_words |= term.forms & entry.type_keys
        fit = len(matched_type_words) / len(entry.type_keys) if entry.type_keys else 0.0
        # Admitted when it explains enough of the question - or, with no foreign word in it, half of it AND the
        # type's name is mostly made of the words asked ("workbook" and "cell value" each name a type; a pivot
        # table does not become TableStyleType).
        if explained < needed and not (foreign == 0 and fit >= PARTIAL_MIN_FIT and explained * 2 >= total):
            continue
        score += NAME_FIT_WEIGHT * fit
        internal = _INTERNAL_NAMESPACE.search(entry.fqn) is not None
        if internal:
            score -= INTERNAL_PENALTY
        if re.match(r"I[A-Z]", entry.short) and entry.short[1:] in shorts:
            score -= INTERFACE_PENALTY
        score += TEXT_RELEVANCE_WEIGHT * bm25.get(entry.doc_id, 0.0) / top_bm25
        scored.append(
            _Scored(entry, score, explained, tuple(missing), fit, via_member, internal, first_asked)
        )
    scored.sort(
        key=lambda item: (
            -round(item.score, 6),
            item.first_asked,
            len(item.entry.type_keys),
            0 if item.entry.has_members else 1,
            -bm25.get(item.entry.doc_id, 0.0),
            item.entry.fqn,
        )
    )
    return scored


def _collapse_aliases(
    entries_by_doc: Mapping[str, _Entry], ordered: list[str]
) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    """Drops the later chunks that are the same type under another name (the same short name and body, such as a
    Python re-export), keeping the first; returns the kept ids and each kept id's alias FQNs."""
    kept: list[str] = []
    seen: dict[tuple[str, int], str] = {}
    aliases: dict[str, list[str]] = {}
    for doc_id in ordered:
        entry = entries_by_doc.get(doc_id)
        if entry is None:
            kept.append(doc_id)
            continue
        key = (entry.short.casefold(), entry.body_hash)
        first = seen.get(key)
        if first is None:
            seen[key] = doc_id
            kept.append(doc_id)
        else:
            aliases.setdefault(first, []).append(entry.fqn)
    return kept, {doc_id: tuple(names) for doc_id, names in aliases.items()}


def search_symbols(
    store: GenerationManifestStore, scope: Scope, query: str, *, top_k: int = 10, offset: int = 0
) -> list[SymbolMatch] | Miss:
    """Search only ``scope``'s currently active generation's self-extracted symbol index.

    TC-068's ``Example: <title>`` pseudo-symbol chunks and TC-112's ``Doc: <title>`` furnished
    documentation chunks live in this exact same generation but are never real symbols, so they
    are excluded from this tool's own notion of a match: the whole corpus is ranked first
    (mirroring ``find_examples.py``'s own ``query_lexical_index_scored(..., top_k=len(documents))``
    pattern, so a real symbol is never lost to a premature cut), pseudo-symbols are dropped, and
    only then is the result truncated to the caller's real ``top_k``.

    TC-278 (G2/REQ-G2-043): admitted candidates are sorted by the STRENGTH of the evidence that
    admitted them first (tier 0 - full-FQN equality - ranks ahead of tier 1 - final-segment
    equality - ranks ahead of tier 2 - bag-of-words containment), raw BM25 score only as the
    tiebreak within a tier. Before this, a real base class (a tier-1 hit) could rank behind its
    own subclasses (each only a tier-2 hit) purely because the subclasses' shorter chunks scored
    a higher raw BM25 term density - never because they were stronger evidence of a real match.

    TC-279 (G2/REQ-G2-043): a FOURTH tier recognizes a query that exactly names a method or
    property listed inside a candidate chunk's own already-rendered Methods:/Properties: blocks
    (``_matches_a_rendered_member``) - a real member genuinely exists but, unlike a type, never
    gets its own ``FQN:`` line, so ``_match_tier`` alone can never see it no matter how highly
    BM25 ranks the containing chunk. This member-name evidence is placed between TC-278's tier 1
    (final-segment class equality) and its tier 2 (bag-of-words), which is renumbered to overall
    tier 3 here: a bare, whole-member-name match is strong, unambiguous evidence, similar in
    strength to a final-segment class match, and never weaker than bag-of-words. When the SAME
    bare member name is genuinely declared on several distinct types (confirmed against the real
    pdf/net fixture - "Dispose" on 9 distinct real types), this tier admits ALL of them, exactly
    like every other tier already lets BM25 sort among several same-tier candidates - there is
    no special-casing for this case because none was needed.

    TC-562 (G3/REQ-G3-009): the query is NFKC-folded first (a full-width ``Ｗｏｒｋｂｏｏｋ`` is ``Workbook``). When
    no tier above admits anything and the per-token fallback finds nothing, two more stages run, in order: (a) a
    single plain word that is a whole type name up to an English inflection (``Widgets``) or a name the index
    tokenizer cannot see (``Do``); (b) the evidence stage (``_evidence_ranking``) for a sentence in the user's own
    words ("merge cells together", "add a slide"). Each result carries ``match_quality`` (``exact``/``partial``);
    a partial answer names the words it could not explain. ``offset`` pages through the ranked symbols:
    ``total_matches`` is how many there are and ``next_offset`` the offset of the next page. The chunks that are
    the same type under another name are collapsed into one result whose ``aliases`` names the rest.

    A query with no match returns an explicit ``Miss`` - never a widened search, never a
    fallback to a different generation or scope.
    """
    from foss_mcp.mcp.tools.get_symbol import extract_fqn, generation_coverage_note
    from foss_mcp.mcp.tools.relevance import fold_query

    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return Miss(scope, query, "no published generation for this scope")

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    if not lexical_payload or not lexical_payload.get("documents"):
        return Miss(
            scope,
            query,
            "published generation has no symbol index",
            coverage_note=generation_coverage_note((lexical_payload or {}).get("documents") or {}),
        )

    original_query = query
    query = fold_query(query)
    documents = lexical_payload["documents"]
    ranked = query_lexical_index_scored(lexical_payload, query, top_k=len(documents))
    admitted: list[tuple[int, int, int, float, str]] = []
    for doc_id, bm25_score in ranked:
        text = documents[doc_id]["text"]
        fqn = extract_fqn(text)
        if fqn is None or fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        class_tier = _match_tier(query, fqn)
        member_declaration = _member_declaration(query, fqn, text)
        if class_tier is None and member_declaration is None:
            continue
        inherits_only = 0
        if class_tier is not None and class_tier < 2:
            tier = class_tier
        elif member_declaration is not None:
            tier = 2
            # TC-516: a type that DECLARES the member ranks before the types that only INHERIT it.
            inherits_only = 0 if member_declaration else 1
        else:
            tier = 3
        # TC-541: a chunk that lists members (0) before one that lists none (1) within the tier.
        memberless = 0 if _has_listed_members(text) else 1
        admitted.append((tier, memberless, inherits_only, bm25_score, doc_id))
    # Strongest evidence first (ascending tier); within a tier, a chunk that lists members before an empty
    # one (TC-541), declaring types before inheriting ones, then the existing BM25-descending order - it
    # is never discarded, only demoted to secondary.
    admitted.sort(key=lambda candidate: (candidate[0], candidate[1], candidate[2], -candidate[3]))
    ordered = [doc_id for _tier, _memberless, _inherits, _bm25_score, doc_id in admitted]
    qualities: dict[str, str] = dict.fromkeys(ordered, MATCH_EXACT)
    unmatched: dict[str, tuple[str, ...]] = {}
    if not ordered:
        ordered = _inflected_name_ids(documents, query)
        qualities = dict.fromkeys(ordered, MATCH_EXACT)
    if not ordered:
        scored = _evidence_ranking(documents, query, scope.family, lexical_payload)
        ordered = [item.entry.doc_id for item in scored]
        for item in scored:
            qualities[item.entry.doc_id] = MATCH_EXACT if item.exact else MATCH_PARTIAL
            unmatched[item.entry.doc_id] = item.unexplained
    if not ordered:
        ordered = _fallback_doc_ids(documents, ranked, query, scope.family, len(documents), lexical_payload)
        qualities = dict.fromkeys(ordered, MATCH_EXACT)
    if not ordered:
        known_fqns = []
        for doc_id in documents:
            fqn = extract_fqn(documents[doc_id]["text"])
            if fqn is not None and not fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
                known_fqns.append(fqn)
        return Miss(
            scope,
            original_query,
            f"no symbol matches {original_query!r}",
            suggestions=suggest_similar_fqns(known_fqns, original_query),
            coverage_note=generation_coverage_note(documents),
        )

    entries_by_doc = {entry.doc_id: entry for entry in _entries(documents)}
    ordered, aliases = _collapse_aliases(entries_by_doc, ordered)
    total = len(ordered)
    page = ordered[offset : offset + top_k]
    if not page:
        return Miss(
            scope,
            original_query,
            f"offset {offset} is past the last of the {total} symbols that match {original_query!r}",
        )
    next_offset = offset + top_k if offset + top_k < total else None
    return [
        SymbolMatch(
            scope=scope,
            generation_id=active_generation_id,
            doc_id=doc_id,
            chunk_id=documents[doc_id]["chunk_id"],
            text=documents[doc_id]["text"],
            match_quality=qualities.get(doc_id, MATCH_EXACT),
            unmatched_terms=unmatched.get(doc_id, ()),
            total_matches=total,
            next_offset=next_offset,
            aliases=aliases.get(doc_id, ()),
            limitation=limitation_line(documents[doc_id]["text"]),
        )
        for doc_id in page
    ]
