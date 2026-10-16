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

from collections.abc import Iterable
from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, RELEVANCE_THRESHOLD, chunk_kind, coverage, query_terms
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


def classify_content_type(text: str) -> str:
    """Which of the four documentation categories *text* belongs to, from its own content.

    'developer_guide' is the default: most product documentation IS developer-facing
    reference material, and the other three are specific, narrower categories a passage
    names explicitly rather than something every passage must declare.
    """
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


def _relevant_coverage(
    documents: dict[str, dict], doc_ids: Iterable[str], terms: frozenset[str]
) -> dict[str, float]:
    """doc_id -> query-term coverage, for the chunks of *doc_ids* at or above ``RELEVANCE_THRESHOLD``.
    The one relevance rule both the search and the miss hint use."""
    relevant: dict[str, float] = {}
    for doc_id in doc_ids:
        doc_coverage = coverage(terms, documents[doc_id]["tokens"])
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
) -> Miss:
    """A miss that also names the other content types holding a chunk relevant to the query."""
    terms = query_terms(query, scope.family)
    others = tuple(
        other
        for other in CONTENT_TYPES
        if other != content_type and _relevant_coverage(documents, sorted(grouped[other]), terms)
    )
    hint = (
        f"documentation for this query exists under content_type: {', '.join(others)}; "
        "call again with one of them, or use lookup, which tries all four"
        if others
        else None
    )
    return Miss(scope, query, content_type, reason, other_content_types=others, hint=hint)


def search_docs(
    store: GenerationManifestStore,
    scope: Scope,
    query: str,
    content_type: str,
    *,
    top_k: int = 10,
) -> list[DocMatch] | Miss:
    """Search only documents classified as *content_type*, only in ``scope``'s currently
    active generation.

    A query with no match - or a match that exists only under a DIFFERENT content_type -
    returns an explicit ``Miss``, never a widened search across every category.
    """
    if content_type not in CONTENT_TYPES:
        raise ValueError(f"content_type must be one of {CONTENT_TYPES}, got {content_type!r}")

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
    if not matching_doc_ids:
        return _miss_with_hint(
            scope,
            query,
            content_type,
            f"no documents classified content_type={content_type!r}",
            documents,
            grouped,
        )

    # Candidate selection never depends on BM25 having scored the document (a stemmed match such as
    # "install" vs "Installation" has no BM25 score); BM25 only breaks ties between equal coverage.
    terms = query_terms(query, scope.family)
    bm25 = dict(query_lexical_index_scored(lexical_payload, query, top_k=len(documents)))
    relevant = _relevant_coverage(documents, matching_doc_ids, terms)
    scored = [(doc_coverage, bm25.get(doc_id, 0.0), doc_id) for doc_id, doc_coverage in relevant.items()]
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    hits = [doc_id for _coverage, _score, doc_id in scored][:top_k]
    if not hits:
        return _miss_with_hint(
            scope, query, content_type, f"no {content_type} documents match {query!r}", documents, grouped
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
