"""G2/TC-248: a deterministic OFFLINE replica of ``lookup``'s doc-composition fallback
(``foss_mcp.mcp.tools.lookup._compose_from_docs``) against pdf/java's real, committed
fixtures - proving a specific replacement query is safe, after TC-238 broke the query the
live e2e test (``tests/e2e/test_pdf_java_live_content.py``,
``test_lookup_returns_real_doc_matches_for_an_api_naive_query``) used to rely on.

Why offline, not live: OWNER-10 (``ops/owner_items.yaml``) records that this environment's
own permission classifier denies ``docker compose ... up``/``down`` lifecycle actions against
this repository's e2e-test compose projects, confirmed independently twice (a worker attempt
and the supervisor). This card cannot bring up the real ``ingest-pdf-java``/``serving-pdf-java``
stack. ``_compose_from_docs``'s own decision mechanism - which content_type (getting_started,
developer_guide, troubleshooting, faq, tried IN THAT ORDER - ``search_docs.CONTENT_TYPES``) a
query's first non-empty ``search_docs`` result lands in - depends ONLY on two pure functions
over already-committed text: ``classify_content_type`` (buckets a chunk's own text) and
``query_lexical_index`` (BM25-ranks a query against a payload ``build_lexical_index`` built).
Neither reads anything a real container adds (container plumbing, a real ``javac`` compile):
both take already-published chunk text as their only input. So replaying the SAME real chunks
(``build_chunks_from_api_surface`` over the real, committed
``tests/fixtures/pdf_java/api_surface.json``, PLUS the real furnished-page doc chunks
``extract_doc_sections`` + ``chunk_document`` build from the real, committed
``tests/fixtures/furnished/pdf_java/pages/_index.md``) through these same two functions is
methodologically sound stand-in evidence for THIS class of change - never a weaker substitute,
and never claimed here as a live confirmation that did not happen.

What broke (confirmed below, not merely asserted): TC-238 raised pdf/java's live symbol-index
cap (``build_chunks_from_api_surface``'s ``max_types``) from 20 to 300. The real fixture has
150 real type entries - well under the new 300 cap, so EVERY real type chunk is now reachable,
including the real ``org.aspose.pdf.GenericAction`` entry ("Represents a PDF action of an
unknown or unsupported type"). ``classify_content_type`` buckets that chunk's text
"developer_guide" (the classifier's own default - it trips none of the getting_started/
troubleshooting/faq hint words), and it genuinely contains the word "unsupported". The
original live test's query, "what functionality is unsupported", now scores a nonzero BM25
match against that chunk, so ``_compose_from_docs``'s own ``CONTENT_TYPES``-ordered loop stops
at "developer_guide" and never reaches "troubleshooting" - where the real content this test
exists to prove reachable (the furnished page's own "Scope and Limitations" paragraph,
containing the literal substring "troubleshooting-relevant scope boundaries") actually lives.
This is a GENUINE IMPROVEMENT (more real content is now findable, exactly what TC-238 set out
to fix), not a regression - the old test's own hardcoded query/fragment pair is simply stale
against the fixed system.

The real furnished page's "Scope and Limitations" content block has NO markdown heading syntax
of its own, so ``chunk_document``'s paragraph-splitting path (not its heading-splitting path)
breaks it into four separate chunks - and only the FIRST of those four ("Known issues,
unsupported functionality, and other troubleshooting-relevant scope boundaries...") actually
trips a troubleshooting hint word ("troubleshoot", via "troubleshooting-relevant"; also "known
issue", via "Known issues"); its own three sibling paragraph chunks (the version/Java-11
paragraph, the bullet list of partially-implemented features, the "these limitations don't
apply to the commercial edition" paragraph) all default to "developer_guide" too. A query built
from words shared with those three siblings (e.g. "scope", "limitations", "unsupported") is
STILL unsafe even after avoiding GenericAction, because it collides with its own siblings
before reaching the one troubleshooting-classified chunk. ``OFFLINE_SAFE_QUERY`` below was
chosen, and is proven below, to share tokens ONLY with that one troubleshooting chunk, out of
every chunk in this fixture's full generation (type chunks and doc chunks alike).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.doc_candidates import extract_doc_sections
from foss_mcp.indexing.example_candidates import extract_candidate_examples
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.indexing.lexical_index_writer import doc_id as lexical_doc_id
from foss_mcp.indexing.lexical_index_writer import query_lexical_index, tokenize
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import _looks_like_a_task_question
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, classify_content_type
from foss_mcp.mcp.tools.search_symbols import is_exact_symbol_hit
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[2]
API_SURFACE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "pdf_java" / "api_surface.json"
FURNISHED_PAGE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "furnished" / "pdf_java" / "pages" / "_index.md"

# Matches production since TC-238 (``build_chunks_from_api_surface``'s own default).
MAX_TYPES = 300

GENERATION_ID = "tc248-offline-replica"

# The real, offline-confirmed replacement for the live e2e test's stale
# ``REAL_DOC_TASK_QUERY``. Every one of its non-stopword tokens ("documentation", "issues",
# "reviewed") appears in exactly ONE chunk anywhere in this generation - the real
# troubleshooting-classified "Scope and Limitations" paragraph - confirmed by
# ``test_every_offline_safe_query_token_is_unique_to_the_troubleshooting_chunk`` below.
#
# THE SUPERVISOR'S NEGATIVE CONTROL DEPENDS ON THIS EXACT NAME.
OFFLINE_SAFE_QUERY = "what documentation issues were reviewed"

# The live e2e test's own fragment (``REAL_DOC_FRAGMENT``), unchanged: OFFLINE_SAFE_QUERY
# surfaces this SAME real chunk, so there is no need to point the live test at a different
# sentence.
REAL_DOC_FRAGMENT = "troubleshooting-relevant scope boundaries"

# The live e2e test's OLD, now-stale query (TC-115), kept here under its own name - never
# named ``OFFLINE_SAFE_QUERY`` - so the tests below can honestly demonstrate the exact breakage
# mechanism without touching the supervisor's negative-control target.
STALE_PRE_TC238_QUERY = "what functionality is unsupported"


def _load_api_surface_fixture() -> dict:
    return json.loads(API_SURFACE_FIXTURE.read_text(encoding="utf-8"))


def _load_furnished_page() -> dict:
    """Parse the furnished page's YAML front matter - the same real convention
    ``infra/build_chunks.py``'s own ``_load_furnished_page`` and
    ``tests/indexing/test_doc_candidates.py``'s own ``_load_page`` use.
    """
    text = FURNISHED_PAGE_FIXTURE.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


def _doc_chunks_from_furnished_page(page: Mapping[str, object]) -> list[Chunk]:
    """Real doc chunks from the real furnished page's own overview/content/faq sections,
    built the same way the real production pipeline does (``infra/build_chunks.py``'s own
    ``_build_doc_chunks``, TC-112): ``extract_doc_sections`` for the candidates, then
    ``make_document`` + ``chunk_document`` (the SAME real primitives
    ``build_chunks_from_api_surface`` itself uses for type chunks) to split each candidate's
    body at its own real markdown structure, prefixing each resulting chunk's text with the
    real ``FQN: Doc: {title}`` marker so ``classify_content_type`` sees exactly the text a real
    published generation would carry.
    """
    candidates = extract_doc_sections(page)
    doc_chunks: list[Chunk] = []
    for candidate in candidates:
        doc = make_document(
            source_kind=SourceKind.FURNISHED,
            content_type="doc",
            provenance=Provenance(
                repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-Java",
                commit="db2d3f0622f035825419c6d46727022064f39f15",
                path=str(FURNISHED_PAGE_FIXTURE),
            ),
            evidence_refs=(
                "aspose-pdf-foss/Aspose.PDF-FOSS-for-Java@db2d3f0622f035825419c6d46727022064f39f15",
            ),
            title=candidate.title,
            body=candidate.body,
        )
        for chunk in chunk_document(doc):
            doc_chunks.append(dataclasses.replace(chunk, text=f"FQN: Doc: {candidate.title}\n{chunk.text}"))
    return doc_chunks


@dataclasses.dataclass(frozen=True)
class _OfflineReplica:
    """Everything ``_compose_from_docs`` needs, built once from real fixtures, real
    functions, no store, no container.
    """

    payload: dict
    doc_id_to_content_type: dict[str, str]
    doc_id_to_fqn: dict[str, str | None]
    example_tokens: frozenset[str]


@pytest.fixture(scope="module")
def offline_replica() -> _OfflineReplica:
    fixture = _load_api_surface_fixture()
    page = _load_furnished_page()

    type_chunks = build_chunks_from_api_surface(
        fixture, title="Aspose.PDF FOSS for Java", max_types=MAX_TYPES
    )
    doc_chunks = _doc_chunks_from_furnished_page(page)
    all_chunks = type_chunks + doc_chunks
    chunk_ids = [f"chunk-{index}" for index in range(len(all_chunks))]

    payload = build_lexical_index(all_chunks, chunk_ids, generation_id=GENERATION_ID)

    doc_id_to_content_type: dict[str, str] = {}
    doc_id_to_fqn: dict[str, str | None] = {}
    for chunk_id, chunk in zip(chunk_ids, all_chunks, strict=True):
        did = lexical_doc_id(GENERATION_ID, chunk_id)
        doc_id_to_content_type[did] = classify_content_type(chunk.text)
        doc_id_to_fqn[did] = extract_fqn(chunk.text)

    example_tokens: set[str] = set()
    for candidate in extract_candidate_examples(page):
        example_tokens |= set(tokenize(candidate.title))
        example_tokens |= set(tokenize(candidate.description))
        example_tokens |= set(tokenize(candidate.code))

    return _OfflineReplica(
        payload=payload,
        doc_id_to_content_type=doc_id_to_content_type,
        doc_id_to_fqn=doc_id_to_fqn,
        example_tokens=frozenset(example_tokens),
    )


def _search_docs_like(
    replica: _OfflineReplica, query: str, content_type: str, *, top_k: int = 10
) -> list[str]:
    """Replays ``search_docs``'s own real filtering rule exactly (TC-115's module docstring,
    ``foss_mcp.mcp.tools.search_docs.search_docs``): rank the WHOLE corpus first via the real
    ``query_lexical_index``, then keep only the doc_ids this replica's real
    ``classify_content_type`` call already bucketed as *content_type*, in ranked order,
    truncated to ``top_k`` - never a search scoped to one content_type up front.
    """
    documents = replica.payload["documents"]
    matching_doc_ids = {did for did, ct in replica.doc_id_to_content_type.items() if ct == content_type}
    ranked = query_lexical_index(replica.payload, query, top_k=len(documents))
    return [did for did in ranked if did in matching_doc_ids][:top_k]


def _compose_from_docs_like(replica: _OfflineReplica, query: str) -> tuple[str | None, list[str]]:
    """Replays ``lookup._compose_from_docs``'s own real decision loop exactly: try every
    ``search_docs.CONTENT_TYPES`` category IN ORDER, stop at the first non-empty result.
    Returns ``(None, [])`` when every category misses, mirroring the real function's own
    empty-``doc_matches`` outcome.
    """
    for candidate_type in CONTENT_TYPES:
        hits = _search_docs_like(replica, query, candidate_type)
        if hits:
            return candidate_type, hits
    return None, []


# ---------------------------------------------------------------------------
# The mechanism that broke the live test's old query - confirmed, not assumed.
# ---------------------------------------------------------------------------


def test_stale_pre_tc238_query_now_intercepts_at_developer_guide_via_genericaction(
    offline_replica: _OfflineReplica,
) -> None:
    """Confirms the exact breakage TC-238 introduced: the live e2e test's OLD query now stops
    at "developer_guide" (the real ``org.aspose.pdf.GenericAction`` type chunk, reachable now
    that ``max_types=300`` covers all 150 real types) before ever reaching "troubleshooting" -
    never a guess at what TC-238 changed, a direct replay of ``_compose_from_docs``'s own
    decision loop against the real fixtures.
    """
    content_type, hits = _compose_from_docs_like(offline_replica, STALE_PRE_TC238_QUERY)

    assert content_type == "developer_guide", (
        f"expected the stale query to now intercept at developer_guide (TC-238's own effect), got {content_type!r}"
    )
    documents = offline_replica.payload["documents"]
    assert any("GenericAction" in documents[doc_id]["text"] for doc_id in hits), [
        documents[doc_id]["text"][:120] for doc_id in hits
    ]
    # And troubleshooting was never reached for the stale query - confirming this is a real
    # ordering interception, not a coincidence of ranking.
    troubleshooting_hits = _search_docs_like(offline_replica, STALE_PRE_TC238_QUERY, "troubleshooting")
    assert troubleshooting_hits, "the real troubleshooting chunk must still match the stale query's own words"


# ---------------------------------------------------------------------------
# OFFLINE_SAFE_QUERY itself: the replacement this card picks.
# ---------------------------------------------------------------------------


def test_offline_safe_query_reads_as_a_task_question(offline_replica: _OfflineReplica) -> None:
    """``lookup`` only tries the doc-composition fallback FIRST for a query that
    ``_looks_like_a_task_question`` recognizes as a task question - confirming OFFLINE_SAFE_QUERY
    actually takes the code path this whole card is about, the same real function ``lookup.py``
    itself calls.
    """
    assert _looks_like_a_task_question(OFFLINE_SAFE_QUERY) is True


def test_offline_safe_query_reaches_troubleshooting_without_intercept(
    offline_replica: _OfflineReplica,
) -> None:
    """The concrete replacement for the live e2e test's own assertion: getting_started and
    developer_guide are both genuine, honest misses for OFFLINE_SAFE_QUERY (never intercepted,
    unlike the stale query above), so ``_compose_from_docs``'s own ``CONTENT_TYPES``-ordered
    loop genuinely reaches "troubleshooting" and surfaces the real chunk carrying
    REAL_DOC_FRAGMENT - the same real content the live test's REAL_DOC_FRAGMENT already names.
    """
    assert _search_docs_like(offline_replica, OFFLINE_SAFE_QUERY, "getting_started") == []
    assert _search_docs_like(offline_replica, OFFLINE_SAFE_QUERY, "developer_guide") == []

    content_type, hits = _compose_from_docs_like(offline_replica, OFFLINE_SAFE_QUERY)
    assert content_type == "troubleshooting", (
        f"expected OFFLINE_SAFE_QUERY to reach troubleshooting first, got {content_type!r}"
    )
    assert hits, "expected a real troubleshooting doc match, got none"

    documents = offline_replica.payload["documents"]
    assert any(REAL_DOC_FRAGMENT in documents[doc_id]["text"] for doc_id in hits), [
        documents[doc_id]["text"] for doc_id in hits
    ]


def test_every_offline_safe_query_token_is_unique_to_the_troubleshooting_chunk(
    offline_replica: _OfflineReplica,
) -> None:
    """The precise reason OFFLINE_SAFE_QUERY is safe, asserted directly rather than inferred
    from the composed result alone: every one of its own real BM25 tokens (via the real
    ``tokenize``) has a document frequency of exactly 1 across this ENTIRE real generation
    (150 real type chunks plus every real doc chunk) - and that one document is the real
    troubleshooting-classified "Scope and Limitations" paragraph. No other chunk in this
    fixture, of any content_type, can ever score a nonzero BM25 match for this query.
    """
    documents = offline_replica.payload["documents"]
    doc_freq = offline_replica.payload["doc_freq"]

    query_tokens = tokenize(OFFLINE_SAFE_QUERY)
    assert query_tokens, "OFFLINE_SAFE_QUERY must carry at least one real, non-stopword token"

    troubleshooting_doc_ids = {
        did for did, ct in offline_replica.doc_id_to_content_type.items() if ct == "troubleshooting"
    }
    assert len(troubleshooting_doc_ids) == 1, (
        f"expected exactly one real troubleshooting-classified chunk in this fixture, got {len(troubleshooting_doc_ids)}"
    )
    (troubleshooting_doc_id,) = troubleshooting_doc_ids
    assert REAL_DOC_FRAGMENT in documents[troubleshooting_doc_id]["text"]

    for token in query_tokens:
        assert doc_freq.get(token, 0) == 1, f"query token {token!r} must appear in exactly one real chunk"
        assert token in documents[troubleshooting_doc_id]["tokens"], (
            f"query token {token!r} must be a token of the real troubleshooting chunk"
        )


def test_offline_safe_query_has_no_exact_or_near_symbol_match(offline_replica: _OfflineReplica) -> None:
    """``search_symbols``'s own match rule (``is_exact_symbol_hit``) never fires for
    OFFLINE_SAFE_QUERY against any real FQN this fixture publishes - confirming a bare-query
    dispatch landing on ``search_symbols`` instead (not this card's concern directly, since
    ``lookup`` tries the doc-fallback path first for a task question, but asserted precisely
    because the card's own safety bar names it explicitly).
    """
    real_fqns = [
        fqn
        for fqn in offline_replica.doc_id_to_fqn.values()
        if fqn is not None and not fqn.startswith(("Example: ", "Doc: "))
    ]
    assert real_fqns, "expected real symbol FQNs from the real type fixture"

    assert not any(is_exact_symbol_hit(OFFLINE_SAFE_QUERY, fqn) for fqn in real_fqns)


def test_offline_safe_query_overlaps_no_candidate_example_text(offline_replica: _OfflineReplica) -> None:
    """No real candidate example (``extract_candidate_examples`` - pure, offline, over the
    same real furnished page; a candidate's compiled/verified status is the ONLY thing a real
    container adds) shares a single BM25 token with OFFLINE_SAFE_QUERY. Since every real,
    compile-verified example chunk ``find_examples`` could ever publish is drawn from this same
    candidate set, zero candidate-level overlap means zero possible verified-example overlap
    too - OFFLINE_SAFE_QUERY's composed ``TaskAnswer`` is doc-match-only, never accidentally
    carrying an unrelated example.
    """
    query_tokens = set(tokenize(OFFLINE_SAFE_QUERY))
    assert not (query_tokens & offline_replica.example_tokens)
