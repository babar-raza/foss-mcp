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
from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored
from foss_mcp.mcp.response_budget import fence_blocks
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.relevance import (
    DOCUMENTATION,
    RELEVANCE_THRESHOLD,
    chunk_kind,
    distinctive_terms,
    guarded_coverage,
    names_language_letter,
    query_terms,
    required_terms,
    stem_token,
    term_weights,
)
from foss_mcp.mcp.tools.search_symbols import scope_key

SOURCE_KIND = "self_extracted"

CONTENT_TYPES = ("getting_started", "developer_guide", "troubleshooting", "faq")

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


@dataclass(frozen=True)
class DocMatch:
    scope: Scope
    generation_id: str
    doc_id: str
    chunk_id: str
    content_type: str
    text: str


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


def _rule_for(query: str, family: str, lexical_payload: dict | None) -> _Rule | None:
    """The rule for *query*, or ``None`` when the query has no distinctive term (it names no topic, so
    no documentation chunk can answer it). The bare query that IS only the family word keeps
    ``query_terms``' fallback, as it always has. A query that names the language C++ or C# (a single
    letter directly followed by ``++`` or ``#``) names a topic even though that letter is not a term."""
    terms = query_terms(query, family)
    names_topic = bool(distinctive_terms(query, family)) or names_language_letter(query)
    if not names_topic and terms != frozenset({stem_token(family)}):
        return None
    return _Rule(terms, term_weights(terms, lexical_payload or {}), required_terms(query))


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
        if doc_coverage >= RELEVANCE_THRESHOLD:
            relevant[doc_id] = doc_coverage
    return relevant


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


def _fetch_part(
    store: GenerationManifestStore, scope: Scope, query: str, content_type: str, chunk_id: str, offset: int
) -> list[DocMatch] | Miss:
    """The part of documentation chunk *chunk_id* that starts at character *offset* of its text (the
    way to read the rest of an excerpt the response budget cut). The chunk must be a documentation chunk
    classified *content_type*; anything else is a miss, never a different chunk."""
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return Miss(scope, query, content_type, "no published generation for this scope")
    manifest = store.read_generation(key, active_generation_id)
    documents = (manifest.payload.get("lexical_index") or {}).get("documents") or {}
    if not documents:
        return Miss(scope, query, content_type, "published generation has no document index")
    for doc_id in sorted(documents):
        document = documents[doc_id]
        if document["chunk_id"] != chunk_id:
            continue
        text = document["text"]
        if chunk_kind(text) != DOCUMENTATION or classify_content_type(text) != content_type:
            break
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
    return Miss(
        scope, query, content_type, f"no {content_type} documentation chunk with chunk_id {chunk_id!r}"
    )


def search_docs(
    store: GenerationManifestStore,
    scope: Scope,
    query: str,
    content_type: str,
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
    """
    if content_type not in CONTENT_TYPES:
        raise ValueError(f"content_type must be one of {CONTENT_TYPES}, got {content_type!r}")
    if chunk_id is not None:
        return _fetch_part(store, scope, query, content_type, chunk_id, offset)

    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return Miss(scope, query, content_type, "no published generation for this scope")

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    documents = (lexical_payload or {}).get("documents") or {}
    if not documents:
        return Miss(scope, query, content_type, "published generation has no document index")

    # TC-381: only ``documentation`` chunks are candidates - an API-symbol or example chunk is never
    # documentation, whatever its words - and the category is derived for those alone.
    grouped = _doc_ids_by_content_type(documents)
    matching_doc_ids = grouped[content_type]
    rule = _rule_for(query, scope.family, lexical_payload)
    if not matching_doc_ids:
        return _miss_with_hint(
            scope,
            query,
            content_type,
            f"no documents classified content_type={content_type!r}",
            documents,
            grouped,
            rule,
        )

    # Candidate selection never depends on BM25 having scored the document (a stemmed match such as
    # "install" vs "Installation" has no BM25 score); BM25 only breaks ties between equal coverage.
    bm25 = dict(query_lexical_index_scored(lexical_payload, query, top_k=len(documents)))
    relevant = _relevant_coverage(documents, matching_doc_ids, rule)
    scored = [(doc_coverage, bm25.get(doc_id, 0.0), doc_id) for doc_id, doc_coverage in relevant.items()]
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    hits = [doc_id for _coverage, _score, doc_id in scored][:top_k]
    if not hits:
        return _miss_with_hint(
            scope,
            query,
            content_type,
            f"no {content_type} documents match {query!r}",
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
            content_type=content_type,
            text=documents[doc_id]["text"],
        )
        for doc_id in hits
    ]
