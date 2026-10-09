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
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import get_close_matches

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored
from foss_mcp.mcp.routing import Scope

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


def suggest_similar_fqns(known_fqns: Iterable[str], target: str, *, limit: int = 3) -> tuple[str, ...]:
    """Fuzzy "did you mean" suggestions for *target*, drawn only from *known_fqns*.

    *known_fqns* must be the real FQNs published in the exact generation a Miss/NotFound was
    just computed against - never a separate or stale data source. Deterministic (same inputs
    always produce the same output) and dependency-free: this is a thin wrapper over the
    standard library's own ``difflib.get_close_matches``, never a new dependency or a network
    call. Purely additive - callers attach the result to a Miss/NotFound's own ``suggestions``
    field; it never changes whether a query is a miss or a real match.
    """
    return tuple(get_close_matches(target, known_fqns, n=limit, cutoff=0.6))


def scope_key(scope: Scope, source_kind: str) -> str:
    """The generation-manifest scope string for *scope* at *source_kind* - the same
    ``family::platform::source_kind`` format ``GenerationKey.scope`` uses.
    """
    return "::".join((scope.family, scope.platform, source_kind))


@dataclass(frozen=True)
class SymbolMatch:
    scope: Scope
    generation_id: str
    doc_id: str
    chunk_id: str
    text: str


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


def search_symbols(
    store: GenerationManifestStore, scope: Scope, query: str, *, top_k: int = 10
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

    A query with no match returns an explicit ``Miss`` - never a widened search, never a
    fallback to a different generation or scope.
    """
    from foss_mcp.mcp.tools.get_symbol import extract_fqn

    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return Miss(scope, query, "no published generation for this scope")

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    if not lexical_payload or not lexical_payload.get("documents"):
        return Miss(scope, query, "published generation has no symbol index")

    documents = lexical_payload["documents"]
    ranked = query_lexical_index_scored(lexical_payload, query, top_k=len(documents))
    admitted: list[tuple[int, float, str]] = []
    for doc_id, bm25_score in ranked:
        fqn = extract_fqn(documents[doc_id]["text"])
        if fqn is None or fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        tier = _match_tier(query, fqn)
        if tier is None:
            continue
        admitted.append((tier, bm25_score, doc_id))
    # Strongest evidence first (ascending tier); within a tier, the existing BM25-descending
    # order is still the right tiebreak - it is never discarded, only demoted to secondary.
    admitted.sort(key=lambda candidate: (candidate[0], -candidate[1]))
    doc_ids = [doc_id for _tier, _bm25_score, doc_id in admitted][:top_k]
    if not doc_ids:
        known_fqns = []
        for doc_id in documents:
            fqn = extract_fqn(documents[doc_id]["text"])
            if fqn is not None and not fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
                known_fqns.append(fqn)
        return Miss(
            scope,
            query,
            f"no symbol matches {query!r}",
            suggestions=suggest_similar_fqns(known_fqns, query),
        )

    return [
        SymbolMatch(
            scope=scope,
            generation_id=active_generation_id,
            doc_id=doc_id,
            chunk_id=documents[doc_id]["chunk_id"],
            text=documents[doc_id]["text"],
        )
        for doc_id in doc_ids
    ]
