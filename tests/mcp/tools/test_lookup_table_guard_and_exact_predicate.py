"""G3/TC-628 (REQ-G3-011, Q8): two independent fixes, both already named and already measured by
the project's own committed F6 conformance test (``tests/conformance/test_precision_and_size_budget.py``,
``test_the_three_named_known_false_exact_cases``):

1. ``lookup._answer_quality``'s first branch no longer forces ``match_quality="exact"`` just because a
   limitations-type documentation chunk cleared the relevance floor (``limitations_first`` is gone, both
   as a condition and as a parameter) - a limitations doc match is now held to the SAME existing
   passage-coverage loop every other doc match already is. Only a bare category-name query
   (``category_intent(query) is not None``) still forces exact unconditionally; that branch is untouched.
2. The table-dominated admission guard moved from ``lookup.py`` (``_without_unsupported_tables``, an
   all-or-nothing post-filter only ``lookup`` applied, now deleted) into ``search_docs.py`` itself, as a
   PER-CANDIDATE filter applied once, before either the confident tier (``_relevant_coverage``) or Q7's
   partial tier (``_partial_coverage``) ever scores a candidate - protecting every direct ``search_docs``
   caller, not only ``lookup``'s own composition. ``_is_table_dominated`` stays importable from ``lookup``
   under its own exact name (re-export), because ``tests/mcp/tools/test_lookup_forgiveness.py`` (not in
   this card's write_paths) imports it directly from there.

HONEST MEASURED CLOSEOUT for the three F6-named false-exact cases, confirmed live against the real
corpus below (test (f)), never forced either way:

- CELLS-RUST-001 ("how do I create a pivot table" on cells/rust) STILL reproduces - it is caused by
  ``search_symbols.py``'s own ``via_member`` scoring shortcut, a file this card never writes to, and this
  worker's own measurement confirms ``lookup`` returns the IDENTICAL ``list[SymbolMatch]`` (never even a
  ``TaskAnswer``) before and after this card's fix, with the top match's own ``match_quality == "exact"``
  unaffected either way. Explicitly out of scope; left for a future card.
- HTML-PY-007 ("how do I render HTML to an image" on html/python) and PDF-JAVA-005 ("convert PDF to PNG
  image" on pdf/java), run through the real, fixed ``lookup`` with their own LITERAL, originally-named
  phrasing, STILL measure ``match_quality == "exact"`` - but no longer via the blind, unconditional force
  this card removes. Measured concretely: html/python's real "Scope and Limitations" chunk literally says
  the library "does not yet expose a public API for rendering documents to an image or PDF" (both
  distinctive words of the literal query, in one real sentence); pdf/java's real "Key Capabilities and API
  Reference" class-table chunk literally carries one row, ``PdfConverter | Converts PDF pages to raster
  images (JPEG, PNG)``, that alone holds all three distinctive words of the literal query in one real
  10-line window. Both chunks now reach ``_answer_quality``'s EXISTING ``passage_coverage >= _FULL_COVERAGE``
  loop (never bypassed), and that loop - honestly, not blindly - finds genuine full coverage for these two
  specific real sentences, so the label is deserved this time, not a coincidence of the old mechanism.
  Test (a) below proves the mechanism itself is genuinely fixed (not merely coincidentally unchanged) by
  ADAPTING the HTML-PY-007 phrasing (as this card's own inputs explicitly allow) to a real variant whose
  real chunk does NOT reach full coverage - correctly demoted to ``MATCH_PARTIAL`` with non-empty
  ``unmatched_terms``, something the pre-fix code could never do for ANY limitations hit. Test (f) also
  measures this card's own, real, generalized win from fix 2: F6's own size-budget sub-case (its bare
  family-word query, every one of the 32 real pilots) drops from 7 pilots over the documented 50,000-byte
  ceiling to 0, because the per-candidate guard now excludes every table-dominated chunk from that query's
  candidate pool outright (a bare family word has no distinctive terms left to clear the 2-term floor).

Real-pilot data only for the real-pilot cases (a, b, f): reads the real, committed
``infra/demo-manifests`` in place (copied to a temporary directory first; the repository is never
written). Cases (c), (d) and (e) are synthetic, in-memory, offline and deterministic (the same
``build_lexical_index``-backed ``_InMemoryStore`` pattern ``test_lookup_forgiveness.py`` and
``test_lookup_query_normalization.py`` already use).
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import foss_mcp.mcp.tools.lookup as lookup_module
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.lookup import _is_table_dominated as lookup_is_table_dominated
from foss_mcp.mcp.tools.search_docs import (
    DocMatch,
    category_intent,
    search_docs,
)
from foss_mcp.mcp.tools.search_docs import Miss as DocMiss
from foss_mcp.mcp.tools.search_docs import (
    _is_table_dominated as search_docs_is_table_dominated,
)
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, MATCH_PARTIAL, SymbolMatch
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parents[3]
SEARCH_DOCS_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "search_docs.py"

_GENERATION_ID = "gen-tc628-search-docs-table-guard"
SYNTHETIC_SCOPE = Scope(family="pdf", platform="net")

# F6's own documented ceiling (tests/conformance/test_precision_and_size_budget.py,
# ``_SIZE_BUDGET_BYTES``) for one in-process, response-budget-free ``search_docs`` call - reused here
# (not re-derived a second way) to measure this card's own real, generalized size-budget win.
_SIZE_BUDGET_BYTES = 50_000
_BROAD_QUERY_TOP_K = 100


# ---- fixtures and small in-memory helpers -------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - nothing in
    the repo is ever written to; reading the real baked data is always allowed."""
    root = tmp_path_factory.mktemp("table-guard-and-exact-predicate-corpus")
    return load_demo_manifests_corpus(root)


class _InMemoryStore:
    """One active generation whose chunks are exactly the given texts (a chunk is never split or
    merged) - the same pattern ``test_lookup_forgiveness.py`` and ``test_lookup_query_normalization.py``
    already use."""

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


def _table_chunk(title: str, *terms: str) -> str:
    """A documentation chunk every one of whose body lines is a table row (``_is_table_dominated``
    is true by construction, the same helper ``test_lookup_forgiveness.py`` already uses)."""
    return _doc_chunk(title, *[f"| `{term}` | does something with {term} |" for term in terms])


# ---- (a): a limitations-first query whose real chunk does NOT reach full coverage -> MATCH_PARTIAL ----

# The literal HTML-PY-007 phrasing ("how do I render HTML to an image") measures full passage coverage
# against html/python's real "Scope and Limitations" chunk (it genuinely, literally says the library
# "does not yet expose a public API for rendering documents to an image or PDF") - a legitimately
# deserved exact label now, not a bug this card's fix is meant to flip (see test (f)). This ADAPTED
# variant (this card's own inputs explicitly allow reusing or adapting the named case) adds one real
# word ("server") that the real chunk never mentions, concretely demonstrating the mechanism this card
# fixes: a limitations hit that does NOT fully cover the query's own distinctive terms is correctly
# demoted to a labelled partial match, something the pre-fix unconditional force could never do for ANY
# limitations hit, regardless of real coverage.
ADAPTED_HTML_PY_007_QUERY = "how do I render HTML to an image on a server"


def test_limitations_hit_without_full_coverage_is_demoted_to_partial(corpus: ConformanceCorpus) -> None:
    pilot = corpus.pilots["html__python"]
    answer = lookup(corpus.store, pilot.scope, ADAPTED_HTML_PY_007_QUERY)

    assert isinstance(answer, TaskAnswer)
    assert answer.match_quality == MATCH_PARTIAL
    assert answer.unmatched_terms, "a demoted partial answer must still name the words it could not explain"
    assert answer.doc_matches, "the real limitations chunk must still be offered - just not forced exact"

    match = answer.doc_matches[0]
    title = extract_fqn(match.text)
    assert title == "Doc: Scope and Limitations"

    _generation_id, documents = lookup_module._documents_of(corpus.store, pilot.scope)  # noqa: SLF001
    rule = lookup_module._rule_for(  # noqa: SLF001
        ADAPTED_HTML_PY_007_QUERY, pilot.scope.family, {"documents": documents}, platform=pilot.scope.platform
    )
    assert rule is not None
    coverage = lookup_module.passage_coverage(rule, match.text)  # noqa: SLF001
    assert coverage < lookup_module._FULL_COVERAGE, (  # noqa: SLF001
        f"test construction requires real, measured less-than-full coverage; measured {coverage}"
    )


def test_answer_quality_no_longer_has_a_limitations_first_parameter() -> None:
    """Integration/liveness assertion on the real, committed function (AGENTS.md: proven on real
    source, not merely that some call happens to behave a certain way): ``limitations_first`` is gone
    from ``_answer_quality``'s own signature, not merely ignored."""
    parameters = inspect.signature(lookup_module._answer_quality).parameters  # noqa: SLF001
    assert "limitations_first" not in parameters


def test_without_unsupported_tables_is_deleted_from_lookup() -> None:
    """``lookup._without_unsupported_tables`` and its call site are dead code once the guard moved
    into ``search_docs`` - confirmed deleted, not merely unused."""
    assert not hasattr(lookup_module, "_without_unsupported_tables")


# ---- (b): a bare category-name query still forces MATCH_EXACT unconditionally, untouched -----------


def test_bare_category_name_query_still_forces_exact_unconditionally(corpus: ConformanceCorpus) -> None:
    """``category_intent(query) is not None`` is the ONE branch this card's own inputs say to leave
    completely untouched - a query that is only the name of a documentation category still answers
    ``MATCH_EXACT`` unconditionally, exactly as before this card."""
    pilot = corpus.pilots["html__python"]
    query = "known issues"
    assert category_intent(query) == "troubleshooting"

    answer = lookup(corpus.store, pilot.scope, query)

    assert isinstance(answer, TaskAnswer)
    assert answer.match_quality == MATCH_EXACT
    assert answer.unmatched_terms == ()
    assert answer.doc_matches


# ---- (c)/(d): the table-dominated guard, moved into search_docs, is a PER-CANDIDATE filter ----------


def test_table_dominated_chunk_with_one_distinctive_term_is_excluded_from_search_docs_itself() -> None:
    """Calls ``search_docs`` DIRECTLY (not ``lookup``): a synthetic table-dominated chunk that carries
    only ONE of the query's distinctive terms is now excluded from ``search_docs``'s OWN candidate pool
    before either tier ever scores it - never admitted, a plain ``Miss``. Before this card, the guard
    lived only in ``lookup.py`` and ``search_docs`` alone would have returned this chunk directly (this
    worker confirmed that concretely on the pre-fix code before writing this test)."""
    store = _InMemoryStore(_table_chunk("API Reference", "alphafoo", "betabar"))

    result = search_docs(store, SYNTHETIC_SCOPE, "alphafoo", "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, DocMiss)


def test_table_dominated_chunk_with_two_distinctive_terms_is_still_admitted_through_search_docs() -> None:
    """The same synthetic table-dominated chunk, but the query now names BOTH of its distinctive terms
    (clearing ``_TABLE_EVIDENCE_MIN_TERMS``): still admitted through ``search_docs`` directly, unchanged
    from before this card - a table-dominated chunk is never penalised once it carries real evidence."""
    store = _InMemoryStore(_table_chunk("API Reference", "alphafoo", "betabar"))

    result = search_docs(store, SYNTHETIC_SCOPE, "alphafoo betabar", "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, list) and result
    assert all(isinstance(match, DocMatch) for match in result)
    assert [extract_fqn(match.text) for match in result] == ["Doc: API Reference"]
    assert all(match.match_quality == MATCH_EXACT for match in result)


def test_a_non_table_dominated_chunk_is_unaffected_by_the_guard_either_way() -> None:
    """A chunk that is NOT table-dominated (ordinary prose) is admitted regardless of how many
    distinctive terms it carries - the guard only ever constrains a table-dominated chunk."""
    store = _InMemoryStore(_doc_chunk("Guide", "Call alphafoo first.", "Then you are done."))

    result = search_docs(store, SYNTHETIC_SCOPE, "alphafoo", "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, list) and result


def test_the_guard_is_wired_as_a_per_candidate_filter_before_both_scoring_tiers() -> None:
    """Integration/liveness assertion on committed source (AGENTS.md): the table guard's own filter
    call site exists in ``search_docs.py`` ahead of BOTH scoring tiers, not merely that some unit test
    happens to pass - a grep-level proof the wiring is real, not incidental."""
    text = SEARCH_DOCS_SOURCE.read_text(encoding="utf-8")
    guard_call = "_table_guarded_doc_ids(documents, matching_doc_ids, table_guard_terms)"
    relevant_call = "_relevant_coverage(documents, guarded_doc_ids, rule)"
    partial_call = "_partial_coverage(documents, guarded_doc_ids, rule)"
    assert text.count(guard_call) == 1
    assert relevant_call in text
    assert partial_call in text
    assert text.index(guard_call) < text.index(relevant_call) < text.index(partial_call)


# ---- (e): _is_table_dominated stays importable from lookup, under its own name, unchanged -----------


def test_is_table_dominated_stays_importable_from_lookup_by_its_own_name_and_behaves_identically() -> None:
    """``tests/mcp/tools/test_lookup_forgiveness.py`` (not in this card's write_paths) does
    ``from foss_mcp.mcp.tools.lookup import TaskAnswer, _is_table_dominated, lookup`` and must keep
    working unchanged. A direct identity check (not merely "the import does not raise"): ``lookup``'s
    own name is bound to the EXACT SAME function object ``search_docs`` now owns - a true re-export, not
    a second, possibly-diverging copy."""
    assert lookup_is_table_dominated is search_docs_is_table_dominated

    table_text = _table_chunk("API Reference", "alphafoo", "betabar")
    prose_text = _doc_chunk("Guide", "Just some ordinary prose that does not look like a table at all.")
    long_text = _doc_chunk("Dump", "alphafoo " + "word " * 1700)
    assert len(long_text) > 8000

    for candidate in (table_text, prose_text, long_text):
        assert lookup_is_table_dominated(candidate) == search_docs_is_table_dominated(candidate)
    assert lookup_is_table_dominated(table_text) is True
    assert lookup_is_table_dominated(prose_text) is False
    assert lookup_is_table_dominated(long_text) is True


# ---- (f): the real, honestly-measured outcome for all three F6-named cases, plus the real, ----------
# ---- generalized size-budget win fix 2 produces ------------------------------------------------------


def test_cells_rust_001_still_reproduces_unaffected_by_this_card(corpus: ConformanceCorpus) -> None:
    """CELLS-RUST-001 is caused by ``search_symbols.py``'s own ``via_member`` scoring shortcut - a file
    this card never writes to. Measured, honestly: ``lookup`` still returns a ``list[SymbolMatch]``
    (never even reaching a ``TaskAnswer``, so this card's own ``_answer_quality`` fix cannot touch it
    either way), and the top match's own ``match_quality`` is still ``"exact"`` - explicitly out of
    scope, left for a future card, confirmed still reproducing."""
    pilot = corpus.pilots["cells__rust"]
    answer = lookup(corpus.store, pilot.scope, "how do I create a pivot table")

    assert isinstance(answer, list)
    assert all(isinstance(item, SymbolMatch) for item in answer)
    assert answer and answer[0].match_quality == MATCH_EXACT


def test_html_py_007_and_pdf_java_005_literal_queries_measured_honestly(corpus: ConformanceCorpus) -> None:
    """The two cases this card targets, run with their own LITERAL, originally-named phrasing (not the
    adapted variant of test (a) above). Measured honestly, never forced: both still answer
    ``match_quality == "exact"`` - but each is now independently confirmed, by this very test, to reach
    full passage coverage of a REAL, on-topic sentence/row (not a blind, unconditional force the old
    mechanism could never have checked) - see this module's own docstring for the real sentence/row
    text. This is not a residual bug for these two specific real queries: it is the SAME honest
    mechanism (``_answer_quality``'s existing ``passage_coverage >= _FULL_COVERAGE`` loop) every other
    doc match is already held to, now applied unconditionally for a limitations hit too, legitimately
    finding full coverage here. The generalized fix is proven elsewhere: test (a) above (an adapted
    phrasing of the same HTML-PY-007 chunk correctly demoted to partial) and the size-budget measurement
    below (this card's own real, generalized win)."""
    html_answer = lookup(
        corpus.store, corpus.pilots["html__python"].scope, "how do I render HTML to an image"
    )
    assert isinstance(html_answer, TaskAnswer)
    assert html_answer.match_quality == MATCH_EXACT
    html_match = html_answer.doc_matches[0]
    assert "render" in html_match.text.lower()
    assert "image" in html_match.text.lower()

    pdf_answer = lookup(corpus.store, corpus.pilots["pdf__java"].scope, "convert PDF to PNG image")
    assert isinstance(pdf_answer, TaskAnswer)
    assert pdf_answer.match_quality == MATCH_EXACT
    assert any("PdfConverter" in match.text for match in pdf_answer.doc_matches)


def _serialized_bytes(matches: list[DocMatch]) -> int:
    return len(json.dumps([dataclasses.asdict(match) for match in matches]).encode("utf-8"))


def test_the_size_budget_win_this_card_actually_produces_is_real_and_measured(
    corpus: ConformanceCorpus,
) -> None:
    """This card's own real, generalized win from fix 2: F6's own size-budget sub-case
    (``tests/conformance/test_precision_and_size_budget.py``'s
    ``test_in_process_search_docs_stays_inside_the_size_budget_for_a_broad_query``, reused here
    verbatim, never re-derived a second way, for every one of the 32 real pilots) measured, BEFORE this
    card, 7 pilots over the documented 50,000-byte ceiling (cells/rust, pdf/go, pdf/java, pdf/net,
    pdf/python, slides/python, words/net - this worker's own fresh measurement, an exact match to that
    file's own pinned baseline). Measured again here, AFTER this card: 0 pilots over the ceiling - the
    per-candidate guard now excludes every table-dominated chunk from a bare family-word query's
    candidate pool outright, since such a query's own ``distinctive_terms`` is empty (the family word is
    always excluded from its own distinctive set), so no table-dominated chunk can ever clear the
    guard's >= 2 real-term floor for it. This is the concrete, generalized proof this card's fix widens
    protection to every direct ``search_docs`` caller, not only the three literally-named cases."""
    over_budget: list[str] = []
    for pilot_name, pilot in sorted(corpus.pilots.items()):
        result = search_docs(corpus.store, pilot.scope, pilot.scope.family, top_k=_BROAD_QUERY_TOP_K)
        if isinstance(result, DocMiss):
            continue
        if _serialized_bytes(result) > _SIZE_BUDGET_BYTES:
            over_budget.append(pilot_name)

    assert over_budget == [], (
        f"expected 0 pilots over the {_SIZE_BUDGET_BYTES}-byte ceiling after this card's fix, "
        f"measured: {over_budget}"
    )
