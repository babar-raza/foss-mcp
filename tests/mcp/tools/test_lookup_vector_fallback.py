"""G3/TC-634 (REQ-G3-011, QV3): ``lookup()`` gets its own bounded access to TC-633's doc-scoped
vector fallback inside ``search_docs`` - at most ONE extra ``search_docs`` call per ``lookup()``
invocation (never one per content-type category), reusing ``search_docs``'s own public combined
composition rather than importing the private ``_doc_vector_fallback`` helper directly. A second,
independent fix closes a real interaction bug found by code reading: ``_answer_quality``'s own
exactness recheck (``passage_coverage(rule, match.text) >= _FULL_COVERAGE``) is now skipped
entirely for any match whose ``match_basis`` is ``search_docs.MATCH_BASIS_EMBEDDING`` - a
cosine-similarity match must never be silently promoted to ``MATCH_EXACT`` by this independent,
purely-lexical recheck, whatever its raw text happens to score.

Offline and deterministic throughout: mechanism 1's tests (a)-(c) replace ``lookup.search_docs``
and ``lookup.find_examples`` with fully-controlled test doubles (never a real embedding gateway,
never real demo-manifests data) and prove call counts directly via a counting double, never by
inference from code structure. Mechanism 2's tests (d)-(e) call ``lookup._answer_quality``
directly against a real, measured ``_InMemoryStore``-backed lexical payload (the same pattern
``test_lookup_forgiveness.py`` and ``test_lookup_table_guard_and_exact_predicate.py`` already use),
each adversarial fixture's own full passage coverage independently confirmed via
``passage_coverage`` before the guard is exercised - never merely assumed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import foss_mcp.mcp.tools.lookup as lookup_module
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.search_docs import (
    CONTENT_TYPES,
    MATCH_BASIS_EMBEDDING,
    DocMatch,
)
from foss_mcp.mcp.tools.search_docs import Miss as DocMiss
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, MATCH_PARTIAL, Miss

_GENERATION_ID = "gen-tc634-lookup-vector-fallback"
SYNTHETIC_SCOPE = Scope(family="pdf", platform="net")


class _InMemoryStore:
    """One active generation whose chunks are exactly the given texts (a chunk is never split or
    merged) - the same pattern ``test_lookup_forgiveness.py`` and
    ``test_lookup_table_guard_and_exact_predicate.py`` already use. With no chunk texts at all, the
    generation is active but holds no documents - every one of ``lookup``'s own supporting calls
    (``search_symbols``, ``_named_symbols``, ``_limitation_docs``) sees a real, empty index and
    returns nothing, never raises."""

    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _doc_chunk(title: str, *body_lines: str) -> str:
    return "\n".join([f"FQN: Doc: {title}", *body_lines]) + "\n"


@pytest.fixture(autouse=True)
def _no_find_examples_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test below is about ``doc_matches``/``_answer_quality``, never about
    ``find_examples`` - a plain empty result, never a real gateway call, keeps ``example`` ``None``
    throughout unless a test overrides this itself."""
    monkeypatch.setattr(lookup_module, "find_examples", lambda *args, **kwargs: [])


# ---- mechanism 1: lookup() gets its own bounded, at-most-once combined search_docs call ----------

TASK_QUERY = "how do I configure the internationalization resource catalog"

VECTOR_MATCH_TEXT = _doc_chunk(
    "Internationalization", "Translation strings live in a separate resource catalog entirely."
)


def _vector_doc_match(scope: Scope) -> DocMatch:
    """A vector-sourced match of the exact shape TC-633's ``_doc_vector_fallback`` produces:
    ``match_quality=MATCH_PARTIAL``, ``match_basis=MATCH_BASIS_EMBEDDING``, NEVER ``MATCH_EXACT``."""
    return DocMatch(
        scope=scope,
        generation_id="gen-fake-vector",
        doc_id="doc-vector-1",
        chunk_id="chunk-vector-1",
        content_type="developer_guide",
        text=VECTOR_MATCH_TEXT,
        match_quality=MATCH_PARTIAL,
        match_basis=MATCH_BASIS_EMBEDDING,
    )


def _category_miss(scope: Scope, query: str, content_type: str) -> DocMiss:
    return DocMiss(scope, query, content_type, f"no {content_type} documents match {query!r}")


# ---- (a) + (b): the per-category loop finds nothing -> exactly one combined call surfaces the
# vector match, which lookup() carries through into its own TaskAnswer honestly (MATCH_PARTIAL,
# never MATCH_EXACT) --------------------------------------------------------------------------------


def test_combined_fallback_surfaces_a_vector_match_the_per_category_loop_missed_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _InMemoryStore()
    scope = SYNTHETIC_SCOPE
    calls: list[str | None] = []

    def fake_search_docs(
        store_arg: object,
        scope_arg: Scope,
        query: str,
        content_type: str | None = None,
        *,
        top_k: int = 10,
        chunk_id: str | None = None,
        offset: int = 0,
    ) -> list[DocMatch] | DocMiss:
        calls.append(content_type)
        if content_type is None:
            # The combined, all-categories call: the only call shape that ever reaches TC-633's
            # vector fallback for a lookup() invocation.
            return [_vector_doc_match(scope_arg)]
        return _category_miss(scope_arg, query, content_type)

    monkeypatch.setattr(lookup_module, "search_docs", fake_search_docs)

    result = lookup(store, scope, TASK_QUERY)

    assert isinstance(result, TaskAnswer), result
    assert len(result.doc_matches) == 1
    assert result.doc_matches[0].match_basis == MATCH_BASIS_EMBEDDING
    assert result.doc_matches[0].text == VECTOR_MATCH_TEXT
    # (a): lookup() surfaces the vector match, and its own overall quality is PARTIAL, never EXACT.
    assert result.match_quality == MATCH_PARTIAL
    assert result.match_quality != MATCH_EXACT
    # (b): exactly the per-category loop's own calls (one per CONTENT_TYPES entry, in order) plus
    # exactly ONE combined (content_type=None) call - proved by counting, not by inference.
    assert calls == [*CONTENT_TYPES, None]
    assert calls.count(None) == 1


def test_a_miss_from_the_combined_call_too_still_makes_only_that_one_extra_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When even the combined call finds nothing, lookup() still made only ONE extra call beyond
    the per-category loop - never retried, never widened further."""
    store = _InMemoryStore()
    scope = SYNTHETIC_SCOPE
    calls: list[str | None] = []

    def fake_search_docs(
        store_arg: object,
        scope_arg: Scope,
        query: str,
        content_type: str | None = None,
        *,
        top_k: int = 10,
        chunk_id: str | None = None,
        offset: int = 0,
    ) -> list[DocMatch] | DocMiss:
        calls.append(content_type)
        return _category_miss(scope_arg, query, content_type if content_type is not None else "all")

    monkeypatch.setattr(lookup_module, "search_docs", fake_search_docs)

    result = lookup(store, scope, TASK_QUERY)

    assert isinstance(result, Miss), result
    assert calls == [*CONTENT_TYPES, None]
    assert calls.count(None) == 1


# ---- (c): the per-category loop DOES find something -> the combined call never happens at all ----

LEXICAL_CATEGORY_TEXT = _doc_chunk("Developer Guide", "Call configure_catalog before anything else.")


def _lexical_doc_match(scope: Scope, content_type: str) -> DocMatch:
    return DocMatch(
        scope=scope,
        generation_id="gen-fake-lexical",
        doc_id="doc-lexical-1",
        chunk_id="chunk-lexical-1",
        content_type=content_type,
        text=LEXICAL_CATEGORY_TEXT,
    )


def test_per_category_hit_never_reaches_the_combined_fallback_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _InMemoryStore()
    scope = SYNTHETIC_SCOPE
    calls: list[str | None] = []
    hit_category = CONTENT_TYPES[1]

    def fake_search_docs(
        store_arg: object,
        scope_arg: Scope,
        query: str,
        content_type: str | None = None,
        *,
        top_k: int = 10,
        chunk_id: str | None = None,
        offset: int = 0,
    ) -> list[DocMatch] | DocMiss:
        calls.append(content_type)
        if content_type == hit_category:
            return [_lexical_doc_match(scope_arg, content_type)]
        if content_type is None:
            raise AssertionError(
                "must not be called: the per-category loop already found a documentation match"
            )
        return _category_miss(scope_arg, query, content_type)

    monkeypatch.setattr(lookup_module, "search_docs", fake_search_docs)

    result = lookup(store, scope, TASK_QUERY)

    assert isinstance(result, TaskAnswer), result
    assert result.doc_matches
    assert result.doc_matches[0].content_type == hit_category
    # the loop stopped at the hit (break) - its own call count exactly, and no combined call at all.
    assert calls == list(CONTENT_TYPES[: CONTENT_TYPES.index(hit_category) + 1])
    assert None not in calls


def test_the_combined_call_is_not_reached_when_the_loop_hits_on_the_very_first_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _InMemoryStore()
    scope = SYNTHETIC_SCOPE
    calls: list[str | None] = []
    hit_category = CONTENT_TYPES[0]

    def fake_search_docs(
        store_arg: object,
        scope_arg: Scope,
        query: str,
        content_type: str | None = None,
        *,
        top_k: int = 10,
        chunk_id: str | None = None,
        offset: int = 0,
    ) -> list[DocMatch] | DocMiss:
        calls.append(content_type)
        if content_type == hit_category:
            return [_lexical_doc_match(scope_arg, content_type)]
        raise AssertionError("must not be called: the first category already hit")

    monkeypatch.setattr(lookup_module, "search_docs", fake_search_docs)

    result = lookup(store, scope, TASK_QUERY)

    assert isinstance(result, TaskAnswer), result
    assert calls == [hit_category]


# ---- mechanism 2: a vector-sourced match is never promoted to MATCH_EXACT by _answer_quality's own
# independent lexical recheck, however it happens to score ------------------------------------------


def _measured_rule_and_coverage(store: _InMemoryStore, scope: Scope, query: str, text: str):
    _generation_id, documents = lookup_module._documents_of(store, scope)  # noqa: SLF001
    rule = lookup_module._rule_for(query, scope.family, {"documents": documents}, platform=scope.platform)  # noqa: SLF001
    assert rule is not None, f"query {query!r} names no topic at all - fixture construction error"
    coverage = lookup_module.passage_coverage(rule, text)  # noqa: SLF001
    return documents, coverage


ADVERSARIAL_QUERY = "zeta eta theta iota"
ADVERSARIAL_TEXT = _doc_chunk("Something", "zeta eta theta iota covers everything in this passage.")


def test_vector_sourced_match_is_never_promoted_to_exact_even_with_full_lexical_coverage() -> None:
    """The deliberately adversarial fixture the card's own ``purpose`` describes: a
    ``MATCH_BASIS_EMBEDDING`` match whose text WOULD score ``_FULL_COVERAGE`` under
    ``passage_coverage`` if the new guard did not exist - independently confirmed below, not
    merely assumed - must still yield ``MATCH_PARTIAL``, never ``MATCH_EXACT``. This is the test
    the card's negative control targets."""
    store = _InMemoryStore(ADVERSARIAL_TEXT)
    scope = SYNTHETIC_SCOPE
    documents, coverage = _measured_rule_and_coverage(store, scope, ADVERSARIAL_QUERY, ADVERSARIAL_TEXT)
    assert coverage >= lookup_module._FULL_COVERAGE, (  # noqa: SLF001
        f"test construction requires real, measured full coverage; measured {coverage}"
    )
    doc_id = next(iter(documents))
    match = DocMatch(
        scope=scope,
        generation_id=_GENERATION_ID,
        doc_id=doc_id,
        chunk_id=documents[doc_id]["chunk_id"],
        content_type="developer_guide",
        text=ADVERSARIAL_TEXT,
        match_quality=MATCH_PARTIAL,
        match_basis=MATCH_BASIS_EMBEDDING,
    )

    quality, _unmatched = lookup_module._answer_quality(store, scope, ADVERSARIAL_QUERY, (), None, (match,))  # noqa: SLF001

    assert quality == MATCH_PARTIAL
    assert quality != MATCH_EXACT


# ---- (e): a plain lexical MATCH_PARTIAL match with genuinely full passage coverage is UNAFFECTED
# by the new guard - still correctly promotes to MATCH_EXACT, proving the guard is specific to
# MATCH_BASIS_EMBEDDING, not a blanket disablement of this promotion path -----------------------

LEXICAL_QUERY = "kappa lambda mu nu"
LEXICAL_TEXT = _doc_chunk("Something Else", "kappa lambda mu nu covers everything in this passage too.")


def test_a_plain_lexical_partial_match_with_full_coverage_is_unaffected_and_still_promotes_to_exact() -> None:
    store = _InMemoryStore(LEXICAL_TEXT)
    scope = SYNTHETIC_SCOPE
    documents, coverage = _measured_rule_and_coverage(store, scope, LEXICAL_QUERY, LEXICAL_TEXT)
    assert coverage >= lookup_module._FULL_COVERAGE, (  # noqa: SLF001
        f"test construction requires real, measured full coverage; measured {coverage}"
    )
    doc_id = next(iter(documents))
    match = DocMatch(
        scope=scope,
        generation_id=_GENERATION_ID,
        doc_id=doc_id,
        chunk_id=documents[doc_id]["chunk_id"],
        content_type="developer_guide",
        text=LEXICAL_TEXT,
        match_quality=MATCH_PARTIAL,
        match_basis="lexical",
    )

    quality, unmatched = lookup_module._answer_quality(store, scope, LEXICAL_QUERY, (), None, (match,))  # noqa: SLF001

    assert quality == MATCH_EXACT
    assert unmatched == ()


def test_match_basis_embedding_guard_is_the_literal_this_cards_negative_control_anchors_on() -> None:
    """Documents, directly against the real committed source, the exact literal the taskcard's
    negative control mutates - ``gatectl``'s own anchor, not this test's invention."""
    import inspect

    source = inspect.getsource(lookup_module._answer_quality)  # noqa: SLF001
    assert source.count("match.match_basis == MATCH_BASIS_EMBEDDING") == 1
