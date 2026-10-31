"""search_docs: ONE tool with a content_type filter, not four separate tools.

CRITICAL, do not inherit this defect: the reference system silently retried without an
explicit filter, and substituted the active generation for a requested one, whenever the
strict search came up empty. This project does the opposite - a miss returns a miss, and a
document tagged with a DIFFERENT content_type than requested is never returned as if it
matched.

The published generation's lexical-index payload (``foss_mcp.indexing.lexical_index_writer``)
carries each chunk's ``text`` but no separate content-type metadata - that writer is out of
this card's write_paths, and belongs to a different card. ``classify_content_type`` derives
the category from the chunk's own text instead of requiring metadata the current index shape
does not carry, the same way ``foss_mcp.extraction.github_release_reader.classify_richness``
derives richness from a release body's own text rather than a metadata field nobody set.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored, tokenize
from foss_mcp.mcp.response_budget import fence_blocks
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.relevance import (
    DOCUMENTATION,
    RELEVANCE_THRESHOLD,
    chunk_kind,
    distinctive_terms,
    doc_scope_term_weights,
    fold_query,
    guarded_coverage,
    has_minimum_evidence,
    names_language_letter,
    normalize_query,
    query_terms,
    required_terms,
    stem_token,
)
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, MATCH_PARTIAL, scope_key

SOURCE_KIND = "self_extracted"

CONTENT_TYPES = ("getting_started", "developer_guide", "troubleshooting", "faq")

# TC-562: a query that is only the NAME of a documentation category ("getting started", "known issues", "faq")
# asks for that category. Its words never need to occur in the chunks (a quick-start chunk says "install" and
# "quick start", not "getting started"), so the category's own documentation answers it.
_CATEGORY_PHRASES: dict[str, str] = {
    "getting started": "getting_started",
    "get started": "getting_started",
    "quick start": "getting_started",
    "quickstart": "getting_started",
    "install": "getting_started",
    "installing": "getting_started",
    "installation": "getting_started",
    "setup": "getting_started",
    "set up": "getting_started",
    "prerequisites": "getting_started",
    "requirements": "getting_started",
    "troubleshooting": "troubleshooting",
    "known issues": "troubleshooting",
    "known issue": "troubleshooting",
    "limitations": "troubleshooting",
    "scope and limitations": "troubleshooting",
    "faq": "faq",
    "faqs": "faq",
    "frequently asked questions": "faq",
}
_CATEGORY_FILLER = frozenset(
    {
        "a",
        "an",
        "the",
        "how",
        "to",
        "do",
        "i",
        "me",
        "my",
        "please",
        "show",
        "guide",
        "docs",
        "doc",
        "documentation",
        "page",
        "section",
        "tutorial",
        "for",
        "of",
        "on",
        "about",
        "this",
        "library",
        "and",
    }
)

_CONTENT_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "getting_started": (
        "getting started",
        "quickstart",
        "quick start",
        "installation",
        "install ",
        "prerequisites",
    ),
    "troubleshooting": ("troubleshoot", "known issue", "common error", "debugging"),
    "faq": ("faq", "frequently asked"),
}


_DOC_TITLE_PREFIX = "FQN: Doc: "
_FAQ_BODY_PREFIX = "FAQ:"
# TC-517: the title rules for a chunk that carries a ``FQN: Doc: <title>`` line, checked in this order.
_TROUBLESHOOTING_TITLE = re.compile(
    r"troubleshoot|known issue|limitation|unsupported|error|problem", re.IGNORECASE
)
_GETTING_STARTED_TITLE = re.compile(
    r"install|quick ?start|getting started|prerequisite|setup|dependenc|requirement", re.IGNORECASE
)


def _classify_by_title(text: str) -> str | None:
    """TC-517: the category of a documentation chunk from its section title, or ``None`` when *text*
    has no ``FQN: Doc: <title>`` first line (the keyword rule then applies)."""
    first_line, _, rest = text.partition("\n")
    if not first_line.startswith(_DOC_TITLE_PREFIX):
        return None
    title = first_line[len(_DOC_TITLE_PREFIX) :]
    first_body_line = rest.split("\n", 1)[0]
    if first_body_line.startswith(_FAQ_BODY_PREFIX):
        return "faq"
    if _TROUBLESHOOTING_TITLE.search(title):
        return "troubleshooting"
    if _GETTING_STARTED_TITLE.search(title):
        return "getting_started"
    return "developer_guide"


def classify_content_type(text: str) -> str:
    """Which of the four documentation categories *text* belongs to.

    A chunk with a ``FQN: Doc: <title>`` first line is categorised by its section title (TC-517; a
    first body line starting ``FAQ:`` is a faq). Any other text - hand-written chunks - is
    categorised from its own words. 'developer_guide' is the default: most product documentation
    IS developer-facing reference material, and the other three are specific, narrower categories.
    """
    by_title = _classify_by_title(text)
    if by_title is not None:
        return by_title
    lowered = text.lower()
    for content_type in ("getting_started", "troubleshooting", "faq"):
        if any(hint in lowered for hint in _CONTENT_TYPE_HINTS[content_type]):
            return content_type
    return "developer_guide"


def category_intent(query: str) -> str | None:
    """The documentation category *query* names and nothing else ("getting started", "how to install", "known
    issues", "FAQ"), or ``None``. A query with any other word ("install the pdf driver") names a task, not a
    category."""
    words = [
        word for word in re.findall(r"[a-z0-9]+", fold_query(query).lower()) if word not in _CATEGORY_FILLER
    ]
    return _CATEGORY_PHRASES.get(" ".join(words)) if words else None


@dataclass(frozen=True)
class DocMatch:
    scope: Scope
    generation_id: str
    doc_id: str
    chunk_id: str
    content_type: str
    text: str
    # G3/TC-627 (REQ-G3-011): the symbol path's own shape (``search_symbols.SymbolMatch``) -
    # ``MATCH_EXACT`` for today's confident hit (every existing construction, in this file and in
    # ``lookup.py``'s ``_limitation_docs``, needs no change), ``MATCH_PARTIAL`` for the new labelled
    # partial tier below. ``kw_only=True``: ``DocPart`` (below) subclasses ``DocMatch`` and adds its
    # OWN non-default fields (``offset`` etc.) - without ``kw_only`` a dataclass refuses to combine a
    # parent's defaulted field with a child's non-defaulted one ("non-default argument follows
    # default argument"), confirmed by actually constructing the two classes before relying on it.
    match_quality: str = field(default=MATCH_EXACT, kw_only=True)
    unmatched_terms: tuple[str, ...] = field(default=(), kw_only=True)


# G3/TC-563: the most JSON bytes of chunk text one continuation part carries. The response budget is
# 24,576 bytes in all; the rest is the part's own fields, the scope and the envelope.
DOC_PART_BYTES = 20480


@dataclass(frozen=True)
class DocPart(DocMatch):
    """One contiguous, line-aligned part of a documentation chunk (``search_docs`` with ``chunk_id``).

    ``text`` is exactly ``chunk[offset:offset + len(text)]``. ``next_offset`` is where the next part
    starts, ``None`` when this part reaches the end of the chunk; ``part_note`` says so in words and,
    when more follows, names the call that returns it."""

    offset: int
    total_chars: int
    next_offset: int | None
    part_note: str


@dataclass(frozen=True)
class Miss:
    """An explicit miss. This IS the answer - never smoothed over into a widened result."""

    scope: Scope
    query: str
    content_type: str
    reason: str
    # TC-515: on a miss from a readable generation, the OTHER content types that hold a documentation
    # chunk clearing the same relevance rule, and a sentence naming them (``()`` / ``None`` otherwise).
    other_content_types: tuple[str, ...] = ()
    hint: str | None = None


def _doc_ids_by_content_type(documents: dict[str, dict]) -> dict[str, set[str]]:
    """The documentation chunks of *documents* (never a symbol or example chunk, TC-381) grouped by
    the content type ``classify_content_type`` derives from their own text."""
    grouped: dict[str, set[str]] = {content_type: set() for content_type in CONTENT_TYPES}
    for doc_id, document in documents.items():
        if chunk_kind(document["text"]) == DOCUMENTATION:
            grouped[classify_content_type(document["text"])].add(doc_id)
    return grouped


@dataclass(frozen=True)
class _Rule:
    """The relevance rule of one query over one generation (TC-516): its terms, their IDF weights and
    the identifier-like words every relevant chunk must contain."""

    terms: frozenset[str]
    weights: dict[str, float]
    required: frozenset[str]


def _rule_for(
    query: str, family: str, lexical_payload: dict | None, *, platform: str | None = None
) -> _Rule | None:
    """The rule for *query*, or ``None`` when the query has no distinctive term (it names no topic, so
    no documentation chunk can answer it). The bare query that IS only the family word keeps
    ``query_terms``' fallback, as it always has. A query that names the language C++ or C# (a single
    letter directly followed by ``++`` or ``#``) names a topic even though that letter is not a term. A query
    with no word of two characters (``a.b.c::d``) names nothing (TC-562). *platform* (TC-610, G3/REQ-G3-011),
    when given, additionally excludes the scope's own platform/runtime word from ``query_terms``/
    ``distinctive_terms``, exactly as those functions already document; ``platform=None`` (the default)
    leaves every existing call byte-for-byte unaffected.

    REWORK (attempt 2, G3/TC-617): excluding the platform word can empty ``distinctive_terms`` even
    though the query names a real topic - a bare product-title query ("Aspose.<Family> FOSS for
    <Platform>") whose only non-generic, non-family content IS its own platform word. Before platform
    exclusion existed, such a query fell through to ``query_terms``' own fallback (the full stemmed
    set) via the bare-family-word special case below. When that is exactly what happened - the
    platform exclusion alone is what emptied ``distinctive_terms`` - this rebuilds ``terms`` without
    the platform exclusion, preserving that pre-existing single-term rule byte-for-byte, while a query
    with real additional distinctive content beyond its own platform word still gets that word
    correctly excluded."""
    if not has_minimum_evidence(query):
        return None
    terms = query_terms(query, family, platform=platform)
    names_topic = bool(distinctive_terms(query, family, platform=platform)) or names_language_letter(query)
    if not names_topic:
        if platform is not None and distinctive_terms(query, family) == frozenset({stem_token(platform)}):
            terms = query_terms(query, family)
        elif terms != frozenset({stem_token(family)}):
            return None
    return _Rule(terms, doc_scope_term_weights(terms, lexical_payload or {}), required_terms(query))


# TC-562: a passage is this many consecutive lines of a chunk.
PASSAGE_LINES = 10

# G3/TC-627 (REQ-G3-011, Q7): a query needs at least this many content terms (``rule.terms`` -
# ``query_terms``'s own stemmed set) to be eligible for the labelled partial tier at all. Below this,
# a query is "short" and a plain ``Miss`` is the only outcome, however high a chunk's own passage
# coverage happens to be - independently re-measured safe: the 4 short nonsense queries of
# ``test_nonsense_queries_miss.py`` top out at 3 content terms across all 32 real pilots, so they can
# never reach this gate by construction, at any threshold.
PARTIAL_LONG_QUERY_MIN_TERMS = 6

# G3/TC-627 (REQ-G3-011, Q7): the frozen admission floor for the partial tier's own best-passage
# coverage (plan section 2.2 item 3's allowed band is 0.34-0.50). Independently re-measured, fresh,
# against the real 32-pilot infra/demo-manifests corpus before this card was written: F5's 5 longer
# nonsense sentences (test_sentence_wrapped_retrieval.NONSENSE_SENTENCES) measure a maximum
# best-passage coverage of 0.2500 across every pilot and every documentation chunk - a comfortable
# 0.09-0.25 margin below every value in the allowed band. The literal LONGQ-001 query's own real
# install chunk measures 0.2984, still below even the loosest allowed value, so this card alone does
# NOT flip LONGQ-001 via its OWN chunk (flipping it is Q8/Q9's job).
#
# 0.41, not the card's own suggested 0.40 default: at 0.40, several real pdf/net "Key Capabilities
# and API Reference" chunks (the card's own named structural precision risk) measure EXACTLY
# 0.403166800478466 best-passage coverage against the literal LONGQ-001 query itself, with 2-3 of
# its terms ("convert"/"file"/"reference") present via this file's own title-folding
# (``_passage_match``'s ``title_tokens``) - at 0.40 these clear the gate and flip the pinned
# LONGQ-001 case from Miss to a partial hit on the WRONG chunk, which this card's own required test
# (f) forbids. 0.41 is the smallest round value still inside the allowed band that clears this
# measured collision (0.403166800478466 < 0.41) while keeping real recall headroom: of the 81
# "long and currently-failing" cases in F5's 96-case default sample, 17 (21%) still clear 0.41's own
# chunk floor, against 19 (23%) at the exact collision value and 24 (30%) at 0.40 - a small, honest
# reduction, not a collapse.
PARTIAL_COVERAGE_THRESHOLD = 0.41

# G3/TC-627 (REQ-G3-011, Q7): the partial tier never claims one single "best" answer - a structural
# precision risk (a different, more lexically generic chunk, overwhelmingly one titled "Key
# Capabilities and API Reference", can out-score the true chunk) means up to this many labelled,
# honestly-uncertain candidates are returned instead.
_MAX_PARTIAL_DOCS = 3


def _passage_match(rule: _Rule, text: str) -> tuple[float, frozenset[str]]:
    """``passage_coverage``'s own best-window coverage, together with which of ``rule.terms``
    (stemmed) were present in that SAME best-scoring window (G3/TC-627, REQ-G3-011): the plain
    intersection of ``rule.terms`` with the window's own stemmed tokens - the same stemming
    ``guarded_coverage`` already does internally for its ``required`` check, reused here rather than
    re-stemmed a second way. A required identifier-like term's absence already zeroes a window's
    score via ``guarded_coverage``, so the partial tier's own "at least 2 matched terms" gate never
    needs a separate check for it.

    G3/TC-572: a chunk's own ``FQN: Doc: <title>`` first line (TC-517) declares that title as the topic
    of the WHOLE chunk, not just the physical lines near line 0 - so the title's own tokens are folded
    into every window. A chunk with no such title line is unaffected (``title_tokens`` is empty)."""
    lines = text.splitlines()
    line_tokens = [tokenize(line) for line in lines]
    title_tokens = tokenize(lines[0]) if lines and lines[0].startswith(_DOC_TITLE_PREFIX) else []
    best = 0.0
    best_matched: frozenset[str] = frozenset()
    for start in range(max(1, len(lines) - PASSAGE_LINES + 1)):
        tokens = title_tokens + [token for found in line_tokens[start : start + PASSAGE_LINES] for token in found]  # fmt: skip
        score = guarded_coverage(rule.terms, tokens, rule.required, rule.weights)
        if score > best:
            best = score
            best_matched = frozenset(rule.terms) & {stem_token(token) for token in tokens}
        if best >= 1.0:
            break
    return best, best_matched


def passage_coverage(rule: _Rule, text: str) -> float:
    """The best coverage of the rule's terms (IDF-weighted, identifier-like words required) by any window of
    ``PASSAGE_LINES`` consecutive lines of *text*; the whole chunk when it is no longer than a window.

    A thin wrapper around ``_passage_match`` (G3/TC-627): this function's own two existing call sites
    (``_relevant_coverage``, ``_miss_with_hint``) and their behaviour stay byte-for-byte unchanged."""
    return _passage_match(rule, text)[0]


def _relevant_coverage(
    documents: dict[str, dict], doc_ids: Iterable[str], rule: _Rule | None
) -> dict[str, float]:
    """doc_id -> query-term coverage, for the chunks of *doc_ids* at or above ``RELEVANCE_THRESHOLD``.
    The one relevance rule both the search and the miss hint use."""
    relevant: dict[str, float] = {}
    if rule is None:
        return relevant
    for doc_id in doc_ids:
        doc_coverage = guarded_coverage(rule.terms, documents[doc_id]["tokens"], rule.required, rule.weights)
        if doc_coverage < RELEVANCE_THRESHOLD:
            continue
        # TC-562: a long chunk holds many topics, so the terms must also meet in one passage of it.
        passage = passage_coverage(rule, documents[doc_id]["text"])
        if passage >= RELEVANCE_THRESHOLD:
            relevant[doc_id] = passage
    return relevant


def _partial_coverage(
    documents: dict[str, dict], doc_ids: Iterable[str], rule: _Rule
) -> dict[str, tuple[float, frozenset[str]]]:
    """doc_id -> (best-passage coverage, the rule terms matched in that SAME window), for the chunks
    of *doc_ids* whose best-passage coverage clears ``PARTIAL_COVERAGE_THRESHOLD`` with at least 2
    distinct rule terms actually present in that window (G3/TC-627, REQ-G3-011). This is the labelled
    partial tier's own, weaker admission - tried only when ``_relevant_coverage``'s confident gate
    found nothing, and only for a long-enough query (``search_docs`` checks that before calling this)."""
    partial: dict[str, tuple[float, frozenset[str]]] = {}
    for doc_id in doc_ids:
        score, matched = _passage_match(rule, documents[doc_id]["text"])
        if score >= PARTIAL_COVERAGE_THRESHOLD and len(matched) >= 2:
            partial[doc_id] = (score, matched)
    return partial


def _miss_with_hint(
    scope: Scope,
    query: str,
    content_type: str,
    reason: str,
    documents: dict[str, dict],
    grouped: dict[str, set[str]],
    rule: _Rule | None,
) -> Miss:
    """A miss that also names the other content types holding a chunk relevant to the query."""
    others = tuple(
        other
        for other in CONTENT_TYPES
        if other != content_type and _relevant_coverage(documents, sorted(grouped[other]), rule)
    )
    hint = (
        f"documentation for this query exists under content_type: {', '.join(others)}; "
        "call again with one of them, or use lookup, which tries all four"
        if others
        else None
    )
    return Miss(scope, query, content_type, reason, other_content_types=others, hint=hint)


def _part_end(text: str, offset: int) -> int:
    """Where the part that starts at *offset* ends: the last line boundary at which the part still fits
    ``DOC_PART_BYTES`` of JSON text and, when it can, outside a fenced code block. A line too long to fit
    is cut mid-line, the only cut that is not on a line boundary."""
    lines = text.split("\n")
    blocks = fence_blocks(lines)
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    first = max(index for index, start in enumerate(starts) if start <= offset)
    used = 0
    end = offset
    last_outside = 0
    for index in range(first, len(lines)):
        line_end = starts[index] + len(lines[index]) + (1 if index < len(lines) - 1 else 0)
        segment = text[max(starts[index], offset) : line_end]
        cost = len(json.dumps(segment).encode("utf-8")) - 2
        if used + cost > DOC_PART_BYTES:
            if end == offset:  # not even one line fits: cut inside it
                for position in range(max(starts[index], offset), line_end):
                    used += len(json.dumps(text[position]).encode("utf-8")) - 2
                    if used > DOC_PART_BYTES:
                        break
                    end = position + 1
            elif last_outside > offset:
                end = last_outside
            return end
        used += cost
        end = line_end
        block = blocks[index]
        if block is None or block[1] == index:
            last_outside = line_end
    return len(text)


ALL_CONTENT_TYPES = "all"


def _fetch_part(
    store: GenerationManifestStore,
    scope: Scope,
    query: str,
    content_type: str | None,
    chunk_id: str,
    offset: int,
) -> list[DocMatch] | Miss:
    """The part of documentation chunk *chunk_id* that starts at character *offset* of its text (the
    way to read the rest of an excerpt the response budget cut). The chunk must be a documentation chunk
    classified *content_type* (any documentation chunk when *content_type* is ``None``, TC-562); anything else
    is a miss, never a different chunk."""
    label = content_type or ALL_CONTENT_TYPES
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return Miss(scope, query, label, "no published generation for this scope")
    manifest = store.read_generation(key, active_generation_id)
    documents = (manifest.payload.get("lexical_index") or {}).get("documents") or {}
    if not documents:
        return Miss(scope, query, label, "published generation has no document index")
    for doc_id in sorted(documents):
        document = documents[doc_id]
        if document["chunk_id"] != chunk_id:
            continue
        text = document["text"]
        if chunk_kind(text) != DOCUMENTATION or (
            content_type is not None and classify_content_type(text) != content_type
        ):
            break
        content_type = classify_content_type(text)
        if offset >= len(text):
            raise ValueError(f"offset must be less than the chunk length of {len(text)} characters")
        end = _part_end(text, offset)
        more = end < len(text)
        note = f"characters {offset}-{end} of {len(text)}; " + (
            f"call search_docs with query=<the same query>, content_type='{content_type}', "
            f"chunk_id='{chunk_id}' and offset={end} for the next part"
            if more
            else "this is the end of the chunk"
        )
        return [
            DocPart(
                scope=scope,
                generation_id=active_generation_id,
                doc_id=doc_id,
                chunk_id=chunk_id,
                content_type=content_type,
                text=text[offset:end],
                offset=offset,
                total_chars=len(text),
                next_offset=end if more else None,
                part_note=note,
            )
        ]
    return Miss(scope, query, label, f"no {label} documentation chunk with chunk_id {chunk_id!r}")


def search_docs(
    store: GenerationManifestStore,
    scope: Scope,
    query: str,
    content_type: str | None = None,
    *,
    top_k: int = 10,
    chunk_id: str | None = None,
    offset: int = 0,
) -> list[DocMatch] | Miss:
    """Search only documents classified as *content_type*, only in ``scope``'s currently
    active generation.

    A query with no match - or a match that exists only under a DIFFERENT content_type -
    returns an explicit ``Miss``, never a widened search across every category.

    With *chunk_id* the call does not search: it returns the part of that one documentation chunk
    that starts at character *offset* (TC-563), which is how the rest of a chunk the response budget
    cut is read. *offset* is ignored without a *chunk_id*.

    TC-562: *content_type* is optional. Without it every documentation category is searched together (the same
    relevance rule, each match labelled with its own category) - the choice ``lookup`` already offered. A query
    that is only the name of a category ("getting started", "known issues", "faq") returns that category's
    documentation whatever words the chunks use; the query is NFKC-folded first.
    """
    if content_type is not None and content_type not in CONTENT_TYPES:
        raise ValueError(f"content_type must be one of {CONTENT_TYPES}, got {content_type!r}")
    if chunk_id is not None:
        return _fetch_part(store, scope, query, content_type, chunk_id, offset)

    label = content_type or ALL_CONTENT_TYPES
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return Miss(scope, query, label, "no published generation for this scope")

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    documents = (lexical_payload or {}).get("documents") or {}
    if not documents:
        return Miss(scope, query, label, "published generation has no document index")

    original_query = query
    query = fold_query(query)
    query = normalize_query(query, scope.family)
    # TC-381: only ``documentation`` chunks are candidates - an API-symbol or example chunk is never
    # documentation, whatever its words - and the category is derived for those alone.
    grouped = _doc_ids_by_content_type(documents)
    matching_doc_ids = set().union(*grouped.values()) if content_type is None else grouped[content_type]
    rule = _rule_for(query, scope.family, lexical_payload, platform=scope.platform)
    if not matching_doc_ids:
        return _miss_with_hint(
            scope,
            original_query,
            label,
            f"no documents classified content_type={label!r}",
            documents,
            grouped,
            rule,
        )

    intent = category_intent(query)
    if intent is not None and (content_type is None or content_type == intent) and grouped[intent]:
        # The query names a category: its documentation answers, most specific title first.
        phrase = set(re.findall(r"[a-z0-9]+", query.lower())) - _CATEGORY_FILLER

        def title_words(doc_id: str) -> set[str]:
            return set(re.findall(r"[a-z0-9]+", documents[doc_id]["text"].split("\n", 1)[0].lower()))

        ordered = sorted(
            grouped[intent], key=lambda doc_id: (0 if phrase & title_words(doc_id) else 1, doc_id)
        )
        return [
            DocMatch(
                scope=scope,
                generation_id=active_generation_id,
                doc_id=doc_id,
                chunk_id=documents[doc_id]["chunk_id"],
                content_type=intent,
                text=documents[doc_id]["text"],
            )
            for doc_id in ordered[:top_k]
        ]

    # Candidate selection never depends on BM25 having scored the document (a stemmed match such as
    # "install" vs "Installation" has no BM25 score); BM25 only breaks ties between equal coverage.
    bm25 = dict(query_lexical_index_scored(lexical_payload, query, top_k=len(documents)))
    relevant = _relevant_coverage(documents, matching_doc_ids, rule)
    scored = [(doc_coverage, bm25.get(doc_id, 0.0), doc_id) for doc_id, doc_coverage in relevant.items()]
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    hits = [doc_id for _coverage, _score, doc_id in scored][:top_k]
    if not hits:
        # G3/TC-627 (REQ-G3-011, Q7): the confident gate found nothing. For a long-enough query
        # (>= PARTIAL_LONG_QUERY_MIN_TERMS content terms), try the SAME candidate pool once more
        # under the weaker, labelled partial admission before giving up to a plain Miss.
        if rule is not None and len(rule.terms) >= PARTIAL_LONG_QUERY_MIN_TERMS:
            partial = _partial_coverage(documents, matching_doc_ids, rule)
            if partial:
                ordered_partial = sorted(
                    partial.items(), key=lambda item: (-item[1][0], -bm25.get(item[0], 0.0), item[0])
                )[: min(top_k, _MAX_PARTIAL_DOCS)]
                return [
                    DocMatch(
                        scope=scope,
                        generation_id=active_generation_id,
                        doc_id=doc_id,
                        chunk_id=documents[doc_id]["chunk_id"],
                        content_type=content_type or classify_content_type(documents[doc_id]["text"]),
                        text=documents[doc_id]["text"],
                        match_quality=MATCH_PARTIAL,
                        unmatched_terms=tuple(sorted(rule.terms - matched)),
                    )
                    for doc_id, (_score, matched) in ordered_partial
                ]
        return _miss_with_hint(
            scope,
            original_query,
            label,
            f"no {label} documents match {original_query!r}"
            if content_type is not None
            else f"no documents match {original_query!r}",
            documents,
            grouped,
            rule,
        )

    return [
        DocMatch(
            scope=scope,
            generation_id=active_generation_id,
            doc_id=doc_id,
            chunk_id=documents[doc_id]["chunk_id"],
            content_type=content_type or classify_content_type(documents[doc_id]["text"]),
            text=documents[doc_id]["text"],
        )
        for doc_id in hits
    ]
