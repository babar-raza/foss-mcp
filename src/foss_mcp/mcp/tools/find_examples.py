"""find_examples: exact-match first, then a lexical fallback bounded by a measured relevance
floor, then (TC-331) a strictly-additive vector-based fallback bounded by its own measured
cosine-similarity floor, over verified snippets only.

No snippet is ever executed inside the serving process: this module never calls ``eval``,
``exec``, ``compile``, or a subprocess on anything it returns - a snippet is text a human (or
another process entirely) chose to run, never code this tool runs itself.

A "verified snippet" is a chunk ``is_verified_example_text`` accepts (TC-465) - the one definition
every branch uses: the ``FQN: Example: <title>`` line, the verifier's own ``Kind: verified_example``
line, an ``Example:`` block line, and ``Verification: passed`` for an authored example (the
published lexical-index payload has no separate metadata field for any of it, same constraint
``get_symbol``'s ``FQN:`` line convention works around). A symbol chunk whose Javadoc merely says
``Example:`` is therefore never a verified example. Each match reports its ``provenance``.

The exact-FQN-match branch (a direct FQN lookup over the generation's documents) is already precise and reports
``coverage=1.0`` unconditionally - it is definitionally exact, never a guess. The semantic
(BM25) fallback is NOT definitionally exact: before TC-273, any chunk with a strictly-positive
BM25 score was treated as a confident match, so a single shared common word between the query
and an otherwise-unrelated chunk (e.g. both mention "page") could manufacture a full-confidence
``ExampleMatch`` for a query with no real relevant example in the corpus at all. The fallback
now ALSO requires ``coverage`` - the fraction of the query's own distinct (stemmed) terms present
in the candidate chunk's own tokens - to be at or above ``relevance.RELEVANCE_THRESHOLD`` (see
that module for how the number was measured against 3 real pilots; since TC-381 the scope's
product-family word is not one of the terms, so it can no longer fill a slot of the floor). A chunk that only shares a stray
common word with the query clears the old positive-BM25-score bar but not this floor, and the
fallback now honestly returns ``NoExampleFound`` instead of guessing.

TC-331 adds one more, strictly additive stage: when the lexical fallback above finds nothing at
all, one more attempt is made against the generation's vector index (if one was published for
it) before giving up. This can only ever turn an existing ``NoExampleFound`` into a real match -
it never runs when the lexical fallback already found something, so it can never regress an
existing hit. It is also fully exception-safe: a gateway outage, a missing gateway
configuration, or any other failure embedding the query degrades this stage back to exactly
today's ``NoExampleFound`` behavior, mirroring the mission plan's own named risk mitigation
("deterministic/lexical fallback always available").

TC-508 bounds that stage, because it runs on the request path: the query embedding is made through
``vector_guard`` with NO retries (one attempt), a hard overall deadline (2.0 s by default,
``FOSS_MCP_VECTOR_DEADLINE_SECONDS``) and a circuit breaker (3 consecutive gateway failures or
timeouts skip the gateway for 60 s, during which the raw query is never handed to the provider).
When the stage is skipped the tool returns the honest lexical miss it always returned, now with a
``vector_fallback`` note (``skipped_timeout``, ``skipped_circuit_open`` or ``skipped_error``) and a
reason saying the semantic fallback was skipped. A normal miss (the stage ran and found nothing)
carries no note.

TC-516 makes the lexical stage IDF-weighted (``relevance.term_weights``: a query word the whole
generation has never seen counts most), requires every identifier-like query word (``DataSource``) to be
present in the chunk, and answers a query with no distinctive term ("Example:", "pdf examples") with the
honest miss at once. The 0.6 threshold, the exact-title branch and the vector fallback are untouched.

TC-543 adds one rule to the lexical stage: when the query has at most two distinctive terms
(``relevance.TITLE_ANCHOR_MAX_TERMS``), an example may answer only if one of them is a word of its
TITLE (``relevance.title_anchored``). A short query is otherwise satisfied by any example whose code
calls ``Save`` or ``Open``. A longer query is judged by the coverage floor alone. Every
``ExampleMatch`` also carries ``match_basis``, which says what its ``coverage`` number is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from foss_mcp.indexing.gateway_embedding_provider import GatewayEmbeddingProvider, Vector
from foss_mcp.indexing.generation_manifest import GenerationManifest, GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index_scored
from foss_mcp.indexing.vector_index_writer import query_vector_index_scored
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.relevance import (
    RELEVANCE_THRESHOLD,
    TITLE_ANCHOR_MAX_TERMS,
    distinctive_terms,
    guarded_coverage,
    query_terms,
    required_terms,
    term_weights,
    title_anchored,
)
from foss_mcp.mcp.tools.search_symbols import SOURCE_KIND, scope_key
from foss_mcp.mcp.tools.vector_guard import (
    QUERY_TIME_MAX_ATTEMPTS,
    STATUS_OK,
    STATUS_SKIPPED_CIRCUIT_OPEN,
    STATUS_SKIPPED_ERROR,
    STATUS_SKIPPED_TIMEOUT,
    get_guard,
)

_EXAMPLE_MARKER = "Example:"
_EXAMPLE_FQN_PREFIX = "Example: "

# The lexical floor is ``relevance.RELEVANCE_THRESHOLD`` (TC-273's measured 0.6, unchanged; see
# that module). TC-381 moved it, with the query-term rule, into the one place documentation search,
# lookup and example search all share, and removed the product-family word from the terms it is
# computed over: "pdf" occurs in nearly every example, so it used to fill one slot of the floor.

# Empirically measured (TC-331), not guessed: built against 2 real, already-ingested pilots'
# own real example chunks (pdf/go, 4 examples; pdf/typescript, 4 examples) through the real,
# live internal Qwen embedding gateway (TC-330's ``GatewayEmbeddingProvider``) - 8
# true-positive probes (a natural query for each example's own real task) scored cosine
# 0.6446-0.8427; 4 false-positive probes (unrelated queries) scored, at best, 0.1782-0.2768
# against any example in the same pilot. This worker independently re-ran the same measurement
# against the same 2 real pilots' own committed demo-manifest generations before trusting these
# numbers: 8 true-positive probes scored 0.6675-0.8537; 3 false-positive probes scored, at
# best, 0.2701-0.3027 against any example. 0.45 sits with real margin on both sides of both
# measurements (0.3027 < 0.45 < 0.6675), not a round number picked without this data.
_VECTOR_SIMILARITY_THRESHOLD = 0.45


_VERIFIED_KIND_LINE = "Kind: verified_example"
_AUTHORED_PROVENANCE_LINE = "Provenance: authored"
_VERIFICATION_PASSED_LINE = "Verification: passed"

# TC-544: the language an example is written in, from the deployment's platform (unknown: empty).
_PLATFORM_LANGUAGE = {
    "net": "C#",
    "python": "Python",
    "java": "Java",
    "typescript": "TypeScript",
    "nodejs": "JavaScript (Node.js)",
    "go": "Go",
    "rust": "Rust",
    "cpp": "C++",
}

# TC-544: what "verified" means for an example, one fixed sentence per provenance. The chunk holds the
# snippet and its ``Source-Commit:`` only: the toolchain, the flags and the assembled source the
# ingestion verifier used live in its verification cache, not in the generation, so none is stated.
README_VERIFICATION_NOTE = (
    "Compiled or run by the ingestion verifier against the library at source_commit; the verifier may "
    "have added the README's own imports or an entry point around this snippet, which are not shown, so "
    "add the imports and entry point your language needs."
)
AUTHORED_VERIFICATION_NOTE = (
    "Authored for this index and compiled or run by the ingestion verifier against the library at "
    "source_commit; add the imports and entry point your language needs."
)

_SOURCE_COMMIT_PATTERN = r"^Source-Commit:[ \t]*(\S+)"

# TC-543: what an ``ExampleMatch.coverage`` number is, per answer (``ExampleMatch.match_basis``).
MATCH_BASIS_EXACT_TITLE = "exact_title"
MATCH_BASIS_TITLE_WORD = "title_word_and_example_text"
MATCH_BASIS_QUERY_TERMS = "query_terms_in_example_text"
MATCH_BASIS_EMBEDDING = "embedding_similarity"


def is_verified_example_text(text: str) -> bool:
    """The ONE definition of a verified example, shared by the exact, lexical and vector branches.

    True only when ALL hold: the chunk's ``FQN:`` line starts with ``Example: ``; some line is
    exactly ``Kind: verified_example`` (the verifier's own line - a symbol chunk whose Javadoc merely
    says ``Example:`` never has it); some line is exactly ``Example:``; and, when some line is
    exactly ``Provenance: authored``, some line is also exactly ``Verification: passed``.
    """
    fqn = extract_fqn(text)
    if fqn is None or not fqn.startswith(_EXAMPLE_FQN_PREFIX):
        return False
    lines = {line.rstrip("\r\n") for line in text.splitlines()}
    if _VERIFIED_KIND_LINE not in lines or _EXAMPLE_MARKER not in lines:
        return False
    if _AUTHORED_PROVENANCE_LINE in lines and _VERIFICATION_PASSED_LINE not in lines:
        return False
    return True


def _provenance(text: str) -> str:
    """``authored`` when the chunk has the line ``Provenance: authored``, ``readme`` otherwise."""
    lines = {line.rstrip("\r\n") for line in text.splitlines()}
    return "authored" if _AUTHORED_PROVENANCE_LINE in lines else "readme"


def _source_commit(text: str) -> str:
    """The value of the first line ``Source-Commit: <value>`` of the chunk, empty when there is none."""
    found = re.search(_SOURCE_COMMIT_PATTERN, text, re.MULTILINE)
    return found.group(1) if found else ""


def _verification_note(text: str) -> str:
    """The fixed sentence saying what ``verified`` means for the chunk's provenance."""
    return AUTHORED_VERIFICATION_NOTE if _provenance(text) == "authored" else README_VERIFICATION_NOTE


def _find_marker_line(lines: list[str]) -> int | None:
    """Index of the LAST line that is exactly ``Example:``, or ``None``."""
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].rstrip("\r\n") == _EXAMPLE_MARKER:
            return index
    return None


def _extract_example(text: str) -> str | None:
    """The code alone: the text after the LAST line that is exactly ``Example:``, with trailing
    ``Source-Commit:`` lines and trailing blank lines removed and nothing else changed.

    (The earlier substring split at the first ``Example:`` landed inside the ``FQN: Example: <title>``
    line, so the snippet began with the title, ``Kind:`` and prose and ended with ``Source-Commit:``.)
    """
    lines = text.splitlines(keepends=True)
    marker = _find_marker_line(lines)
    if marker is None:
        return None
    body = lines[marker + 1 :]
    while body and (not body[-1].strip() or body[-1].startswith("Source-Commit:")):
        body.pop()
    return "".join(body).rstrip("\r\n")


@dataclass(frozen=True)
class ExampleMatch:
    """``coverage`` means two different things depending on ``source`` (TC-331): for
    ``source="exact"`` it is unconditionally ``1.0`` (definitionally exact); for
    ``source="lexical"`` it is the lexical fallback's own token-overlap fraction (see
    ``relevance.RELEVANCE_THRESHOLD``); for ``source="vector"`` it is the vector fallback's own cosine
    similarity (see ``_VECTOR_SIMILARITY_THRESHOLD``). These two numbers are not
    interchangeable - always check ``source`` before comparing or thresholding ``coverage``
    across matches, rather than assuming it is one fixed kind of number.

    ``match_basis`` (TC-543, additive, last field) says plainly what ``coverage`` is for that answer:

    - ``exact_title``: the query was the example's own title (its ``FQN:`` line); ``coverage`` is 1.0.
    - ``title_word_and_example_text``: a lexical answer to a short query (at most two distinctive terms)
      that was anchored on a word of the example's title; ``coverage`` is the weighted share of the
      query's terms present in the example text, 1.0 meaning all are present.
    - ``query_terms_in_example_text``: a lexical answer to a longer query, judged by the coverage floor
      alone; ``coverage`` is the weighted share of the query's terms present in the example text.
    - ``embedding_similarity``: the vector fallback; ``coverage`` is a cosine similarity.

    A lexical ``coverage`` is not a relevance probability: 1.0 means every query term is present in the
    example, not that the example is about them. It defaults to ``""`` for a hand-built match.

    ``language``, ``source_commit`` and ``verification`` (TC-544, additive, last fields, all default ``""``)
    say what the generation really holds about the snippet: the language of the deployment's platform
    (``""`` for an unknown platform), the chunk's ``Source-Commit:`` value (``""`` when the line is
    absent) and one fixed sentence saying what ``verified`` means for this provenance. That sentence is
    deliberately short of a full record: the toolchain, flags and assembled source the ingestion verifier
    used are not in the generation, and the verifier may have added the README's imports or an entry
    point that the snippet does not show.
    """

    scope: Scope
    generation_id: str
    fqn: str
    snippet: str
    coverage: float
    source: str
    found: bool = True
    # TC-465: "authored" when the chunk has the line ``Provenance: authored`` (an example written
    # for the library and really verified), "readme" for a README example the verifier passed.
    provenance: str = "readme"
    # TC-543: what ``coverage`` is for this answer; one of the MATCH_BASIS_* strings, "" when hand-built.
    match_basis: str = ""
    # TC-544: language of the deployment's platform, the chunk's pinned source commit, and what verified means.
    language: str = ""
    source_commit: str = ""
    verification: str = ""


@dataclass(frozen=True)
class NoExampleFound:
    """An explicit miss. Never smoothed over into an unrelated snippet.

    ``found`` (always ``False``) and ``reason`` are additive (TC-381): the miss shape the tool
    description promises carries a found marker and the reason nothing was returned.

    ``vector_fallback`` (TC-508, additive) is ``None`` when the semantic stage ran normally or was
    never applicable, and otherwise says why it was skipped: ``skipped_timeout``,
    ``skipped_circuit_open`` or ``skipped_error``.
    """

    scope: Scope
    query: str
    found: bool = False
    reason: str = "no verified example matches this query"
    vector_fallback: str | None = None


_NO_MATCH_REASON = "no verified example matches this query"
_SKIP_WORDS = {
    STATUS_SKIPPED_TIMEOUT: "timeout",
    STATUS_SKIPPED_CIRCUIT_OPEN: "circuit open",
    STATUS_SKIPPED_ERROR: "error",
}


def find_examples(
    store: GenerationManifestStore, scope: Scope, query: str, *, top_k: int = 5
) -> list[ExampleMatch] | NoExampleFound:
    """A verified snippet for *query*: an exact FQN match first (a direct FQN lookup
    over the generation's documents), a semantic (lexical) search over every OTHER example-bearing chunk otherwise.

    Never executes a snippet, and never returns one when nothing - exact or semantic - has
    one to offer.
    """
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return NoExampleFound(scope, query, reason="no published generation for this scope")

    manifest = store.read_generation(key, active_generation_id)
    lexical_payload = manifest.payload.get("lexical_index")
    documents = (lexical_payload or {}).get("documents") or {}
    if not documents:
        return NoExampleFound(scope, query, reason="published generation has no document index")

    # Exact branch: the query IS an example's pseudo-FQN. Looked up directly over this generation's
    # documents (not through ``get_symbol``), so ``get_symbol`` can stop returning pseudo-symbols
    # without breaking this.
    for document in documents.values():
        text = document["text"]
        if is_verified_example_text(text) and extract_fqn(text) == query:
            example = _extract_example(text)
            if example is not None:
                return [
                    ExampleMatch(
                        scope,
                        active_generation_id,
                        query,
                        example,
                        coverage=1.0,
                        source="exact",
                        provenance=_provenance(text),
                        match_basis=MATCH_BASIS_EXACT_TITLE,
                        language=_PLATFORM_LANGUAGE.get(scope.platform, ""),
                        source_commit=_source_commit(text),
                        verification=_verification_note(text),
                    )
                ]

    example_doc_ids = [
        doc_id for doc_id, document in documents.items() if is_verified_example_text(document["text"])
    ]
    if not example_doc_ids:
        return NoExampleFound(scope, query, reason="no verified example exists in this generation")

    # TC-516: a query with no distinctive term ("Example:", "pdf examples") names no task, so no example
    # can answer it; and relevance is IDF-weighted with every identifier-like query word required.
    if not distinctive_terms(query, scope.family):
        return NoExampleFound(scope, query)
    terms = query_terms(query, scope.family)
    weights = term_weights(terms, lexical_payload)
    required = required_terms(query)
    bm25 = dict(query_lexical_index_scored(lexical_payload, query, top_k=len(documents)))
    scored: list[tuple[float, float, str]] = []
    for doc_id in example_doc_ids:
        doc_coverage = guarded_coverage(terms, documents[doc_id]["tokens"], required, weights)
        title = (extract_fqn(documents[doc_id]["text"]) or "")[len(_EXAMPLE_FQN_PREFIX) :]
        if doc_coverage >= RELEVANCE_THRESHOLD and title_anchored(query, scope.family, title):
            scored.append((doc_coverage, bm25.get(doc_id, 0.0), doc_id))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    hits = [(doc_id, doc_coverage) for doc_coverage, _score, doc_id in scored[:top_k]]
    if not hits:
        vector_matches, note = _vector_fallback(manifest, scope, active_generation_id, query, top_k)
        if vector_matches:
            return vector_matches
        reason = _NO_MATCH_REASON
        if note in _SKIP_WORDS:
            reason = f"{reason} (semantic fallback skipped: {_SKIP_WORDS[note]})"
        return NoExampleFound(scope, query, reason=reason, vector_fallback=note)

    lexical_basis = (
        MATCH_BASIS_TITLE_WORD
        if len(distinctive_terms(query, scope.family)) <= TITLE_ANCHOR_MAX_TERMS
        else MATCH_BASIS_QUERY_TERMS
    )
    matches = []
    for doc_id, doc_coverage in hits:
        text = documents[doc_id]["text"]
        matches.append(
            ExampleMatch(
                scope=scope,
                generation_id=active_generation_id,
                fqn=extract_fqn(text) or "",
                snippet=_extract_example(text) or "",
                coverage=doc_coverage,
                source="lexical",
                provenance=_provenance(text),
                match_basis=lexical_basis,
                language=_PLATFORM_LANGUAGE.get(scope.platform, ""),
                source_commit=_source_commit(text),
                verification=_verification_note(text),
            )
        )
    return matches


def _vector_fallback(
    manifest: GenerationManifest, scope: Scope, generation_id: str, query: str, top_k: int
) -> tuple[list[ExampleMatch], str | None]:
    """TC-331: one more, strictly additive attempt when the lexical fallback above found
    nothing at all - never called otherwise, so it can only ever turn an existing
    ``NoExampleFound`` into a real match, never regress an existing hit.

    Embeds *query* through the real embedding gateway and ranks the WHOLE vector-indexed
    corpus against it first (never cuts candidates by ``top_k`` before filtering - the same
    discipline ``search_symbols.py``'s own docstring establishes), filtered to verified-example
    points scoring at or above ``_VECTOR_SIMILARITY_THRESHOLD``, truncated to the caller's real
    ``top_k`` only at the end.

    Fully exception-safe end to end, not only around the embed call itself: a missing vector
    index (an older-shaped generation published before vector indexing existed), a missing
    gateway configuration, a network error, a budget-exceeded error, or any other failure
    anywhere in this stage returns no matches instead of raising - ``find_examples()`` then
    falls through to its existing ``NoExampleFound`` return, exactly mirroring the
    mission plan's own named risk mitigation ("deterministic/lexical fallback always
    available").

    Returns ``(matches, note)``. The query embedding runs through the process-wide
    ``VectorFallbackGuard`` (TC-508): one attempt, a hard deadline, and a circuit breaker. When it
    is skipped, *note* is the guard's status string (``skipped_timeout``, ``skipped_circuit_open``
    or ``skipped_error``); otherwise it is ``None``. The query text is only ever handed to the
    provider inside the guarded function, so with the circuit open the provider is not even
    constructed.
    """
    vector_payload = manifest.payload.get("vector_index")
    points = (vector_payload or {}).get("points") or []
    if not points:
        return [], None

    try:
        guard = get_guard()
    except Exception:
        # An unusable deadline setting must not take the tool down: degrade to the lexical miss.
        return [], STATUS_SKIPPED_ERROR

    def _embed_query() -> Vector:
        provider = GatewayEmbeddingProvider()
        # Query time, not ingestion: one attempt and no longer than the guard's deadline. A stand-in
        # provider that only has ``embed`` simply skips this.
        set_limits = getattr(provider, "set_query_time_limits", None)
        if set_limits is not None:
            set_limits(max_attempts=QUERY_TIME_MAX_ATTEMPTS, timeout_seconds=guard.deadline_seconds)
        return provider.embed([query])[0]

    status, query_vector = guard.call(_embed_query)
    if status != STATUS_OK:
        return [], status
    if query_vector is None:
        return [], STATUS_SKIPPED_ERROR

    try:
        point_by_id = {point["point_id"]: point for point in points}
        ranked = query_vector_index_scored(vector_payload, query_vector, top_k=len(points))

        matches: list[ExampleMatch] = []
        for point_id, score in ranked:
            point = point_by_id[point_id]
            text = point["text"]
            if not is_verified_example_text(text):
                continue
            if score < _VECTOR_SIMILARITY_THRESHOLD:
                continue
            matches.append(
                ExampleMatch(
                    scope=scope,
                    generation_id=generation_id,
                    fqn=extract_fqn(text) or "",
                    snippet=_extract_example(text) or "",
                    coverage=score,
                    source="vector",
                    provenance=_provenance(text),
                    match_basis=MATCH_BASIS_EMBEDDING,
                    language=_PLATFORM_LANGUAGE.get(scope.platform, ""),
                    source_commit=_source_commit(text),
                    verification=_verification_note(text),
                )
            )
            if len(matches) >= top_k:
                break
        return matches, None
    except Exception:
        return [], None
