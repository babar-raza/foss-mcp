"""G3/TC-633 (REQ-G3-011, QV2): the doc-scoped vector-similarity fallback in ``search_docs`` - a
strictly additive last-resort stage that fires ONLY when every lexical stage (``_relevant_coverage``'s
confident tier AND Q7's ``_partial_coverage`` partial tier) has already found nothing for a
long-enough query, over TC-632's ``vector_index_docs`` payload, reusing ``find_examples``'s own
already-hardened ``GatewayEmbeddingProvider``/``VectorFallbackGuard`` machinery exactly.

Offline and deterministic except for the one test explicitly marked otherwise (the real-gateway
proof, ``test_a_real_longq_001_style_query_gets_a_real_vector_match_against_the_real_gateway``,
skipped when the two real credential env vars are absent). Every other test here either monkeypatches
``search_docs.GatewayEmbeddingProvider`` with a fully-controlled fake, or never reaches it at all
(proved directly via a counting double, never merely assumed).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from foss_mcp.indexing.gateway_embedding_provider import (
    _API_KEY_ENV,
    _ENDPOINT_ENV,
    GatewayEmbeddingError,
)
from foss_mcp.indexing.gateway_embedding_provider import (
    GatewayEmbeddingProvider as RealGatewayEmbeddingProvider,
)
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.indexing.publisher import content_chunk_id, publish_generation
from foss_mcp.indexing.vector_index_writer import is_doc_chunk
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools import search_docs as search_docs_module
from foss_mcp.mcp.tools.search_docs import (
    DOC_VECTOR_SIMILARITY_THRESHOLD,
    MATCH_BASIS_EMBEDDING,
    PARTIAL_LONG_QUERY_MIN_TERMS,
    DocMatch,
    Miss,
    _partial_coverage,
    _relevant_coverage,
    _rule_for,
    search_docs,
)
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, MATCH_PARTIAL, scope_key
from foss_mcp.mcp.tools.vector_guard import VectorFallbackGuard, _set_guard, get_guard
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance
from tests.conformance.corpus import load_demo_manifests_corpus
from tests.conformance.test_sentence_wrapped_retrieval import LONGQ_001_PILOT, LONGQ_001_QUERY
from tests.mcp.tools.test_retrieval_tools import (
    PDF_NET_SCOPE,
    _FakeQueryEmbeddingProvider,
    _FixedVectorEmbeddingProvider,
    _store,
)

SOURCE_KIND = "self_extracted"

# ---- fixtures -----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _gateway_variables_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test except the real-gateway one runs with no gateway configured at all; the real-
    gateway test re-adds its own two variables after this fixture clears them (``monkeypatch`` is
    the same instance for the whole test, so a later ``monkeypatch.setenv`` in the test body wins)."""
    for name in (_ENDPOINT_ENV, _API_KEY_ENV, "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fresh_guard() -> Iterator[VectorFallbackGuard]:
    """A fresh, closed-circuit guard for every test - the real one is process-wide, so without this
    a circuit opened by one test could silently skip the gateway in a later, unrelated one."""
    installed = VectorFallbackGuard()
    previous = get_guard()
    _set_guard(installed)
    try:
        yield installed
    finally:
        _set_guard(previous)


def _doc_chunk(section_title: str, text: str) -> Chunk:
    provenance = Provenance(repository="Aspose/Aspose.PDF-FOSS-for-.NET", commit="z")
    return Chunk(section_title, text, SOURCE_KIND, "api_surface", provenance, "highest", (), NOT_CHECKED)


def _publish_docs(
    store: GenerationManifestStore, scope: Scope, chunks: list[Chunk], embedding_provider
) -> str:
    """Publishes *chunks* through the real ``publish_generation`` authority path with a doc-scoped
    vector index (TC-632's ``vector_index_docs``, built via ``is_doc_chunk``) and NO default
    ``vector_index`` (``vector_chunk_filter`` always rejects, so that index stays empty and the fake
    provider below is never asked to embed the same texts twice)."""
    scope_str = scope_key(scope, SOURCE_KIND)
    lease = store.acquire_lease(scope_str, "worker-1", "pending")
    return publish_generation(
        store,
        family=scope.family,
        platform=scope.platform,
        source_kind=SOURCE_KIND,
        expected_active=store.read_active(scope_str),
        chunks=chunks,
        embedding_provider=embedding_provider,
        lease=lease,
        vector_chunk_filter=lambda _chunk: False,
        doc_vector_chunk_filter=is_doc_chunk,
    )


# ---- a long query, independently confirmed (below) to share ZERO tokens with every doc chunk this
# file constructs - the ONLY way the vector stage, never the lexical ones, can ever produce a match
# (or, for the negative tests, the only thing that could wrongly produce one). 7 distinctive terms,
# above PARTIAL_LONG_QUERY_MIN_TERMS (6).
NONSENSE_LONG_QUERY = "zyzzyx quproc negfoo wibblerox trantuple flombard gribnax"

VECTOR_TARGET_TEXT = (
    "FQN: Doc: Internationalization\n"
    "Translation strings live in a separate resource catalog, unrelated to any word used in this "
    "test's own queries."
)

# A real confident-hit fixture (REQ-G3-011, test (c) part 1): every one of the query's own terms is
# present in the chunk, so ``_relevant_coverage`` finds it at coverage 1.0 - independently confirmed
# by ``test_fixture_confident_query_really_is_a_confident_hit`` below, not merely assumed.
CONFIDENT_TEXT = (
    "FQN: Doc: Getting Started\nInstall the toolkit and import the sdk before calling any feature."
)
CONFIDENT_QUERY = "install toolkit import sdk"

# A real Q7 partial-tier fixture (REQ-G3-011, test (c) part 2): an 8-distinctive-term query whose
# best-passage coverage against this chunk, scored with the REAL IDF weights the published 3-chunk
# generation itself produces (never the unweighted/None-payload numbers, which give a different
# figure), is 0.432970157678998 (5 of 8 terms present) - inside PARTIAL_COVERAGE_THRESHOLD's own
# band, independently confirmed below against that same real published payload, not merely assumed.
PARTIAL_TEXT = (
    "FQN: Doc: Scope and Limitations\n"
    "This module supports monochrome palettes, legacy encodings, vector gradients and raster "
    "animations but omits modern compression and audio playback entirely."
)
PARTIAL_QUERY = "What is the way to handle palette encoding gradient animation compression watermark issue?"


def _three_chunk_fixture() -> list[Chunk]:
    return [
        _doc_chunk("Getting Started", CONFIDENT_TEXT),
        _doc_chunk("Scope and Limitations", PARTIAL_TEXT),
        _doc_chunk("Internationalization", VECTOR_TARGET_TEXT),
    ]


def _vector_query_provider(cosine: float):
    """A fake query-time ``GatewayEmbeddingProvider`` factory whose one query embeds to a vector
    with EXACTLY *cosine* similarity against ``(1.0, 0.0)`` - the vector this file always assigns to
    ``VECTOR_TARGET_TEXT`` below."""
    vector = (cosine, (1.0 - cosine**2) ** 0.5)
    return lambda: _FakeQueryEmbeddingProvider(vector)


def _fixed_vectors_provider() -> _FixedVectorEmbeddingProvider:
    """``VECTOR_TARGET_TEXT`` gets ``(1.0, 0.0)``; the other two fixture chunks get the ZERO vector -
    ``_cosine_scores`` (``vector_index_writer``) scores ANY query against a zero-norm point as
    exactly 0.0, by construction, so no choice of fake query vector can ever make the
    confident/partial fixture chunks look like a vector match (unlike two merely-orthogonal unit
    vectors, whose similarity to a THIRD, non-orthogonal query vector is not independently
    controllable - confirmed the hard way while first drafting this fixture)."""
    zero = (0.0, 0.0)
    return _FixedVectorEmbeddingProvider(
        {
            CONFIDENT_TEXT: zero,
            PARTIAL_TEXT: zero,
            VECTOR_TARGET_TEXT: (1.0, 0.0),
        }
    )


class _CountingProvider:
    """Stands in for ``GatewayEmbeddingProvider`` in ``search_docs`` module: counts constructions and
    refuses to actually embed anything, so a test can prove the stage was NEVER reached (asserting a
    counter stayed at 0) rather than merely hoping an exception would have propagated - the guard
    below swallows any exception a provider itself might raise, so a raise-on-construct double would
    prove nothing here."""

    def __init__(self) -> None:
        self.constructed = 0

    def __call__(self) -> _CountingProvider:
        self.constructed += 1
        return self

    def embed(self, texts):
        raise AssertionError("must not be called: the lexical stage already produced a result")


# ---- fixture self-checks: every scenario below is independently confirmed against the SAME real,
# IDF-weighted lexical payload ``search_docs`` itself builds from this file's 3-chunk fixture (never
# an unweighted/``lexical_payload=None`` stand-in, which gives DIFFERENT numbers - the doc-scope IDF
# weights in ``doc_scope_term_weights`` depend on the whole published doc/example corpus, not merely
# on the rule's own terms) - not merely assumed.


def _fixture_lexical_payload() -> dict:
    chunks = _three_chunk_fixture()
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    return build_lexical_index(chunks, chunk_ids, "fixture-gen")


def test_fixture_nonsense_query_shares_no_terms_with_any_fixture_chunk() -> None:
    payload = _fixture_lexical_payload()
    documents = payload["documents"]
    rule = _rule_for(NONSENSE_LONG_QUERY, "pdf", payload, platform="net")
    assert rule is not None
    assert len(rule.terms) >= PARTIAL_LONG_QUERY_MIN_TERMS, sorted(rule.terms)
    assert _relevant_coverage(documents, documents.keys(), rule) == {}
    assert _partial_coverage(documents, documents.keys(), rule) == {}


def test_fixture_confident_query_really_is_a_confident_hit() -> None:
    payload = _fixture_lexical_payload()
    documents = payload["documents"]
    rule = _rule_for(CONFIDENT_QUERY, "pdf", payload, platform="net")
    assert rule is not None
    relevant = _relevant_coverage(documents, documents.keys(), rule)
    assert len(relevant) == 1
    (coverage,) = relevant.values()
    assert coverage == pytest.approx(1.0)


def test_fixture_partial_query_really_is_a_q7_partial_hit_not_confident() -> None:
    payload = _fixture_lexical_payload()
    documents = payload["documents"]
    partial_doc_id = next(
        doc_id for doc_id, document in documents.items() if document["text"] == PARTIAL_TEXT
    )
    rule = _rule_for(PARTIAL_QUERY, "pdf", payload, platform="net")
    assert rule is not None
    assert len(rule.terms) >= PARTIAL_LONG_QUERY_MIN_TERMS, sorted(rule.terms)
    assert _relevant_coverage(documents, documents.keys(), rule) == {}, "must NOT be a confident hit"
    partial = _partial_coverage(documents, documents.keys(), rule)
    assert partial_doc_id in partial, partial
    score, matched = partial[partial_doc_id]
    assert score == pytest.approx(0.432970157678998)
    assert len(matched) >= 2


# ---- (a): no vector_index_docs payload at all -> byte-for-byte today's behaviour, gateway untouched


def test_no_vector_index_docs_payload_falls_through_to_miss_exactly_as_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    chunks = _three_chunk_fixture()
    scope_str = scope_key(PDF_NET_SCOPE, SOURCE_KIND)
    lease = store.acquire_lease(scope_str, "worker-1", "pending")
    # No doc_vector_chunk_filter at all - the default, today's behaviour: the payload carries no
    # "vector_index_docs" key (TC-632's own pinned contract).
    publish_generation(
        store,
        family=PDF_NET_SCOPE.family,
        platform=PDF_NET_SCOPE.platform,
        source_kind=SOURCE_KIND,
        expected_active=store.read_active(scope_str),
        chunks=chunks,
        embedding_provider=_fixed_vectors_provider(),
        lease=lease,
        vector_chunk_filter=lambda _chunk: False,
    )

    counting = _CountingProvider()
    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", counting)

    result = search_docs(store, PDF_NET_SCOPE, NONSENSE_LONG_QUERY)

    assert isinstance(result, Miss), result
    assert counting.constructed == 0, (
        "the gateway must never be touched when there is no vector_index_docs at all"
    )


# ---- (b): a vector_index_docs payload present + a deterministic fake provider -> a labelled PARTIAL
# match, NEVER MATCH_EXACT, whatever the fake similarity score ----------------------------------------


def test_vector_fallback_finds_a_match_the_lexical_stages_missed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    chunks = _three_chunk_fixture()
    generation_id = _publish_docs(store, PDF_NET_SCOPE, chunks, _fixed_vectors_provider())
    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", _vector_query_provider(0.9))

    result = search_docs(store, PDF_NET_SCOPE, NONSENSE_LONG_QUERY)

    assert isinstance(result, list) and result, result
    assert all(isinstance(match, DocMatch) for match in result)
    assert all(match.match_quality == MATCH_PARTIAL for match in result)
    assert all(match.match_quality != MATCH_EXACT for match in result)
    assert all(match.match_basis == MATCH_BASIS_EMBEDDING for match in result)
    target_chunk_id = content_chunk_id(chunks[2])
    assert any(match.chunk_id == target_chunk_id for match in result)
    for match in result:
        assert match.generation_id == generation_id
        assert match.unmatched_terms == tuple(
            sorted(_rule_for(NONSENSE_LONG_QUERY, "pdf", None, platform="net").terms)
        )


def test_vector_fallback_is_an_honest_miss_below_the_measured_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as above, but the fake query vector's cosine similarity against the target chunk
    is only 0.3 - below ``DOC_VECTOR_SIMILARITY_THRESHOLD`` (0.55) - so the stage must still return a
    plain ``Miss``, never a weak guess."""
    store = _store(tmp_path)
    chunks = _three_chunk_fixture()
    _publish_docs(store, PDF_NET_SCOPE, chunks, _fixed_vectors_provider())
    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", _vector_query_provider(0.3))

    result = search_docs(store, PDF_NET_SCOPE, NONSENSE_LONG_QUERY)

    assert isinstance(result, Miss), result


# ---- (c): a query that already gets a confident OR a Q7-partial lexical hit never reaches this new
# stage at all - proved via a counting double, not merely assumed from code structure --------------


def test_confident_hit_never_reaches_the_vector_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    chunks = _three_chunk_fixture()
    _publish_docs(store, PDF_NET_SCOPE, chunks, _fixed_vectors_provider())
    counting = _CountingProvider()
    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", counting)

    result = search_docs(store, PDF_NET_SCOPE, CONFIDENT_QUERY)

    assert isinstance(result, list) and result
    assert all(match.match_quality == MATCH_EXACT for match in result)
    assert counting.constructed == 0, "a confident lexical hit must never touch the gateway"


def test_q7_partial_hit_never_reaches_the_vector_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    chunks = _three_chunk_fixture()
    _publish_docs(store, PDF_NET_SCOPE, chunks, _fixed_vectors_provider())
    counting = _CountingProvider()
    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", counting)

    result = search_docs(store, PDF_NET_SCOPE, PARTIAL_QUERY)

    assert isinstance(result, list) and result
    assert all(match.match_quality == MATCH_PARTIAL for match in result)
    assert all(match.match_basis == "lexical" for match in result)
    assert counting.constructed == 0, "an existing Q7 partial hit must never touch the gateway"


# ---- (d): any exception anywhere in the new stage degrades to the existing Miss, never propagates -


def test_a_gateway_failure_during_the_query_embed_degrades_to_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    chunks = _three_chunk_fixture()
    _publish_docs(store, PDF_NET_SCOPE, chunks, _fixed_vectors_provider())

    class _Down:
        def embed(self, texts):
            raise GatewayEmbeddingError("gateway down")

    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", _Down)

    result = search_docs(store, PDF_NET_SCOPE, NONSENSE_LONG_QUERY)

    assert isinstance(result, Miss), result


def test_a_malformed_vector_shape_inside_the_ranking_step_degrades_to_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The query embed itself succeeds, but the fake provider returns a vector of the WRONG
    dimension relative to the stored points (3 components vs the stored 2) - ``query_vector_index_scored``
    raises inside the ranking step's own ``try`` block, which must also degrade to ``Miss``, never
    propagate (the outer ``except Exception`` ``_doc_vector_fallback`` wraps around the ranking code,
    not only around the embed call)."""
    store = _store(tmp_path)
    chunks = _three_chunk_fixture()
    _publish_docs(store, PDF_NET_SCOPE, chunks, _fixed_vectors_provider())

    class _WrongShape:
        def embed(self, texts):
            return [(1.0, 0.0, 0.0) for _ in texts]

    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", _WrongShape)

    result = search_docs(store, PDF_NET_SCOPE, NONSENSE_LONG_QUERY)

    assert isinstance(result, Miss), result


# ---- (e): MATCH_BASIS_EMBEDDING is pinned equal to find_examples.MATCH_BASIS_EMBEDDING -----------


def test_match_basis_embedding_is_pinned_equal_to_find_examples_own_copy() -> None:
    assert MATCH_BASIS_EMBEDDING == find_examples_module.MATCH_BASIS_EMBEDDING == "embedding_similarity"


# ---- (f): THE CONTENT_TYPE GUARD - both directions, same query/fixture, one test -------------------


def test_content_type_guard_blocks_the_gateway_per_category_but_not_for_the_combined_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``lookup.py``'s ``_compose_from_docs`` calls ``search_docs`` once PER category, always with an
    explicit ``candidate_type``, never ``content_type=None`` - so this new stage must NEVER fire for
    an explicit ``content_type=`` call (else one ``lookup()`` could trigger several real
    embedding-gateway calls, one per category tried before any hit), and MUST fire for the combined,
    ``content_type=None`` search. Both directions, same query and fixture, in one test."""
    store = _store(tmp_path)
    chunks = _three_chunk_fixture()
    _publish_docs(store, PDF_NET_SCOPE, chunks, _fixed_vectors_provider())

    # VECTOR_TARGET_TEXT's title ("Internationalization") classifies as "developer_guide" - a real,
    # non-empty category this query's own lexical stages still miss inside.
    counting = _CountingProvider()
    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", counting)

    per_category_result = search_docs(store, PDF_NET_SCOPE, NONSENSE_LONG_QUERY, "developer_guide")

    assert isinstance(per_category_result, Miss), per_category_result
    assert counting.constructed == 0, "an explicit content_type= call must never reach the gateway"

    monkeypatch.setattr(search_docs_module, "GatewayEmbeddingProvider", _vector_query_provider(0.9))

    combined_result = search_docs(store, PDF_NET_SCOPE, NONSENSE_LONG_QUERY, content_type=None)

    assert isinstance(combined_result, list) and combined_result, combined_result
    assert all(match.match_basis == MATCH_BASIS_EMBEDDING for match in combined_result)


# ---- the real-gateway proof: a real F5-style long query, lexically missing, gets a real, non-exact
# vector-based result against the real demo-manifests corpus -----------------------------------------

_GPT_OSS_ENDPOINT = "GPT_OSS_ENDPOINT"
_GPT_OSS_API_KEY = "GPT_OSS_API_KEY"
_REAL_CREDS_PRESENT = bool(os.environ.get(_GPT_OSS_ENDPOINT)) and bool(os.environ.get(_GPT_OSS_API_KEY))


@pytest.mark.skipif(
    not _REAL_CREDS_PRESENT,
    reason="GPT_OSS_ENDPOINT/GPT_OSS_API_KEY not set in this environment - real-gateway proof skipped",
)
def test_a_real_longq_001_style_query_gets_a_real_vector_match_against_the_real_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The literal LONGQ-001 query against a generation built from pdf/net's REAL, committed doc
    chunks (``infra/demo-manifests``), with a REAL ``vector_index_docs`` payload built via
    ``publish_generation`` and the REAL ``GatewayEmbeddingProvider`` (TC-632's own test pattern) - not
    a hand-picked fake vector. This worker's own fresh measurement (closeout report) found this exact
    query's own real "Installation, Dependencies, and Quick Start" chunk scores 0.7075 and a
    topically-adjacent chunk scores 0.7125 - both comfortably above ``DOC_VECTOR_SIMILARITY_THRESHOLD``
    (0.55), so this must return a real, non-``MATCH_EXACT`` vector match.
    """
    monkeypatch.setenv(_ENDPOINT_ENV, os.environ[_GPT_OSS_ENDPOINT])
    monkeypatch.setenv(_API_KEY_ENV, os.environ[_GPT_OSS_API_KEY])

    corpus_root = tmp_path / "corpus"
    corpus = load_demo_manifests_corpus(corpus_root)
    pilot = corpus.pilots[LONGQ_001_PILOT]
    real_doc_chunks = pilot.doc_chunks()
    assert real_doc_chunks, "the real pdf/net pilot must carry at least one real documentation chunk"

    chunks = [_doc_chunk(f"real-doc-{i}", document["text"]) for i, document in enumerate(real_doc_chunks)]

    store = GenerationManifestStore(tmp_path / "manifests")
    generation_id = _publish_docs(store, PDF_NET_SCOPE, chunks, RealGatewayEmbeddingProvider())

    manifest = store.read_generation(scope_key(PDF_NET_SCOPE, SOURCE_KIND), generation_id)
    lexical_payload = manifest.payload["lexical_index"]
    documents = lexical_payload["documents"]
    rule = _rule_for(LONGQ_001_QUERY, PDF_NET_SCOPE.family, lexical_payload, platform=PDF_NET_SCOPE.platform)
    assert rule is not None
    assert len(rule.terms) >= PARTIAL_LONG_QUERY_MIN_TERMS, sorted(rule.terms)
    # Confirms, directly, that this is a genuine lexical miss on BOTH tiers before the real vector
    # stage is ever asked to do anything - never merely assumed.
    assert _relevant_coverage(documents, documents.keys(), rule) == {}
    assert _partial_coverage(documents, documents.keys(), rule) == {}

    result = search_docs(store, PDF_NET_SCOPE, LONGQ_001_QUERY)

    assert isinstance(result, list) and result, f"expected a real vector match, got {result!r}"
    assert all(isinstance(match, DocMatch) for match in result)
    assert all(match.match_quality == MATCH_PARTIAL for match in result)
    assert all(match.match_quality != MATCH_EXACT for match in result)
    assert all(match.match_basis == MATCH_BASIS_EMBEDDING for match in result)


# ---- a frozen-value sanity check, the same discipline PARTIAL_COVERAGE_THRESHOLD's own test uses --


def test_doc_vector_similarity_threshold_is_the_measured_value_documented_in_its_own_comment() -> None:
    assert DOC_VECTOR_SIMILARITY_THRESHOLD == 0.55
