"""G3/TC-606 (REQ-G3-010): F6, the precision, exact-label and size-budget conformance invariant.

Measures three related honesty properties of ``lookup``/``search_docs`` against all 32 real baked
pilots, pinning today's real numbers as a committed baseline (Q7/Q8, later cards, improve against it;
this card never fixes anything):

1. Precision: an absent-feature or nonsense sentence must miss - never a confident (non-``Miss``)
   answer. Reuses the nonsense vocabulary ``tests/mcp/tools/test_nonsense_queries_miss.py`` already
   has (``NONSENSE_QUERIES``).
2. Exact-label honesty: ``TaskAnswer.match_quality == "exact"`` must mean one real piece of the
   answer - a symbol, the verified example, or one documentation chunk - genuinely, INDIVIDUALLY
   explains every distinctive word of the query, never a keyword coincidence. Three already-known,
   already-reproduced false-exact cases (``docs/round2/issues.json``: ``CELLS-RUST-001``,
   ``HTML-PY-007``'s sibling render-to-image phrasing, ``PDF-JAVA-005``) are reproduced and pinned
   directly, by domain knowledge the audit already established; a broader seeded sample of real-task
   queries is checked by this test's OWN independent coverage recomputation (``relevance.py``'s own
   ``distinctive_terms``/``present_terms``/``tokenize``, never by trusting ``match_quality`` or
   ``unmatched_terms`` on the answer itself) - a mismatch there is recorded as its own failure.
3. Size budget: an in-process ``search_docs`` call (bypassing the server's response-budget wrapper
   entirely - this sub-case exists to see the tool's own raw, unbudgeted output) for a query broad
   enough to match many chunks must stay under a generous, documented byte ceiling. Several real
   pilots are expected to fail this today (``PDF-JAVA-005`` measured a 23,541-character ``lookup``
   answer built largely from multi-KB alphabetical class tables, and the wider plan cites a 76 KB
   single-table case) - this sub-case exists to prove the later table-dominated guard (Q7/Q8) has
   something concrete to fix and to verify against, not to fix it here.

Every sub-case's failures are recorded via ``assert_no_new_failures(load_baseline(...), observed)`` -
a failure outside the committed baseline fails the check; the baseline is ``"strict": false`` (it may
always be ahead of what is currently observed), so a case that stops reproducing never fails this file,
it is simply not in ``observed`` for that run.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable

import pytest

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.relevance import distinctive_terms, present_terms
from foss_mcp.mcp.tools.search_docs import DocMatch, search_docs
from foss_mcp.mcp.tools.search_docs import Miss as DocSearchMiss
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, Miss, SymbolMatch
from tests.conformance.baseline import KnownFailure, assert_no_new_failures, load_baseline
from tests.conformance.corpus import (
    REPO_ROOT,
    ConformanceCorpus,
    exhaustive_enabled,
    load_demo_manifests_corpus,
    sample_items,
)
from tests.mcp.tools.test_nonsense_queries_miss import NONSENSE_QUERIES

BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "conformance" / "baseline_precision_and_size_budget.json"

EXPECTED_PILOT_COUNT = 32

# Default (non-exhaustive) sample size: how many real symbols per pilot the broader exact-label
# sample draws its "how do I use X" queries from. Kept small so this file's default run stays fast;
# FOSS_MCP_CONFORMANCE_EXHAUSTIVE=1 runs every symbol of every pilot instead.
_BROAD_SAMPLE_SEED = 606
_BROAD_SAMPLE_PER_PILOT = 3

# G3/TC-606 size-budget sub-case: the tool's own unbudgeted top_k, large enough that the ranking's
# real candidate count decides how many matches come back, never this cap - the point is to see what
# a query that genuinely matches many chunks does to an in-process, response-budget-free call.
_BROAD_QUERY_TOP_K = 100

# G3/TC-606: the byte ceiling for one in-process search_docs response (raw list[DocMatch], JSON-
# serialized, never passed through the server's 24,576-byte response-budget wrapper this sub-case
# deliberately bypasses). PDF-JAVA-005 (docs/round2/issues.json) measured a 23,541-character *lookup*
# answer (lookup composes search_docs with symbols and an example on top) built mostly from multi-KB
# alphabetical class-table doc chunks, and the wider plan separately cites a single ~76 KB alphabetical
# table case for the same pilot/query family. This card's own real, measured run of this exact
# sub-case (the family word alone, top_k=100, all 32 pilots) clusters clearly in two bands: 25 of 32
# pilots stay under 41,000 bytes (most well under half that), while 7 - cells/rust, pdf/go, pdf/java,
# pdf/net, pdf/python, slides/python, words/net - land between 53,927 and 125,890 bytes, well over
# double the next-highest pilot. 50,000 bytes sits in the real gap between those two bands: generous
# enough that no normal, single- or few-chunk answer ever trips it, small enough that the pilots
# genuinely dominated by several large table-style chunks for one broad query - this sub-case's whole
# reason to exist - still get caught, matching the magnitude of both real numbers research already
# found rather than a number picked without looking at this card's own measurements.
_SIZE_BUDGET_BYTES = 50_000


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - nothing in
    the repo is ever written to; reading the real baked data is always allowed. One store covers every
    pilot's scope, so every sub-case below reuses it directly."""
    root = tmp_path_factory.mktemp("precision-size-budget-corpus")
    return load_demo_manifests_corpus(root)


def _write_known_failure(pilot: str, case_id: str, reason: str) -> KnownFailure:
    return KnownFailure(pilot=pilot, case_id=case_id, reason=reason)


def test_the_real_demo_manifests_directory_has_the_expected_pilot_count(corpus: ConformanceCorpus) -> None:
    """The card's own recovery clause: name the real count found, never hardcode 32 as a silent pass."""
    assert len(corpus.pilots) == EXPECTED_PILOT_COUNT, sorted(corpus.pilots)


# ---- (1) precision: a nonsense sentence must always miss, on every real pilot -------------------------


def test_nonsense_queries_are_honest_misses_on_every_real_pilot(corpus: ConformanceCorpus) -> None:
    """``lookup`` on every one of the 32 real pilots, for every one of the reused ``NONSENSE_QUERIES`` -
    any confident (non-``Miss``) hit is a precision failure, recorded against the committed baseline."""
    observed: list[tuple[str, str]] = []
    for pilot_name in sorted(corpus.pilots):
        scope = corpus.pilots[pilot_name].scope
        for index, query in enumerate(NONSENSE_QUERIES):
            answer = lookup(corpus.store, scope, query)
            if not isinstance(answer, Miss):
                observed.append((pilot_name, f"nonsense-miss:{pilot_name}:{index}"))
    assert_no_new_failures(load_baseline(BASELINE_PATH), observed)


# ---- (2a) exact-label honesty: the three named, already-known false-exact cases -----------------------


def test_the_three_named_known_false_exact_cases(corpus: ConformanceCorpus) -> None:
    """Attempts to REPRODUCE, against the real current baked data, the three concrete cases this
    card's own purpose names (each already confirmed false-exact by the round-2 audit's own domain
    reasoning - ``docs/round2/issues.json`` CELLS-RUST-001, HTML-PY-007's render-to-image sibling
    phrasing, PDF-JAVA-005): if ``lookup`` still answers ``match_quality == "exact"``, the case still
    reproduces and is recorded; if it no longer does, this test leaves it out of ``observed`` and the
    non-strict baseline never fails on its absence (and this docstring, not a silent pass, is why: see
    the closeout report for which of the three reproduced on this run)."""
    observed: list[tuple[str, str]] = []

    def _check(pilot: str, scope: Scope, query: str, case_id: str) -> None:
        answer = lookup(corpus.store, scope, query)
        if isinstance(answer, TaskAnswer) and answer.match_quality == MATCH_EXACT:
            observed.append((pilot, case_id))

    # CELLS-RUST-001: "how do I create a pivot table" on cells/rust - pivot tables are not one of the
    # 122 indexed types and are not in the README; a member-name keyword match (WorksheetProtection /
    # WorkbookProperties, fit~0.0 per the audit) forces match_quality="exact" anyway.
    _check(
        "cells__rust",
        corpus.pilots["cells__rust"].scope,
        "how do I create a pivot table",
        "false-exact:cells__rust:pivot-table",
    )

    # HTML-PY-007's sibling phrasing: html/python genuinely has no public API to render HTML to an
    # image (get_product_reference(section="limitations") says so explicitly) - the limitations
    # documentation chunk is found, limitations_first fires, and TC-562's condition 1 forces
    # match_quality="exact" unconditionally once ANY limitations paragraph clears the relevance floor,
    # whether or not it was really asked for this specific capability's own words.
    _check(
        "html__python",
        corpus.pilots["html__python"].scope,
        "how do I render HTML to an image",
        "false-exact:html__python:render-to-image",
    )

    # PDF-JAVA-005: "convert PDF to PNG image" on pdf/java - the real answering class (devices.PngDevice)
    # exists and is indexed, but the composed answer is still "exact" even though several of its own
    # doc_matches are multi-KB alphabetically-sorted A-Z class-table dumps (hundreds of unrelated
    # classes) rather than a targeted answer - exactly the class-table shape this card's purpose names.
    _check(
        "pdf__java",
        corpus.pilots["pdf__java"].scope,
        "convert PDF to PNG image",
        "false-exact:pdf__java:png-class-table",
    )

    assert_no_new_failures(load_baseline(BASELINE_PATH), observed)


# ---- (2b) exact-label honesty: a broader seeded sample, independently re-checked ----------------------


def _real_fqns(corpus: ConformanceCorpus, pilot_name: str) -> list[str]:
    """Every real, distinct FQN this pilot's own symbol chunks declare, sorted (deterministic input to
    the seeded sampler below) - the same ``extract_fqn`` parse the product code itself uses."""
    fqns = {
        extract_fqn(document["text"])
        for document in corpus.pilots[pilot_name].symbol_chunks()
        if extract_fqn(document["text"]) is not None
    }
    return sorted(fqns)  # type: ignore[arg-type]


def _short_name(fqn: str) -> str:
    """The final dotted/``::``-scoped segment of *fqn* - the same final-segment reading
    ``search_symbols``'s own tier-1 match and ``lookup._named_symbols`` both already use."""
    return fqn.replace("::", ".").rsplit(".", 1)[-1]


def _piece_texts(answer: TaskAnswer) -> list[str]:
    """Every piece of *answer* that COULD individually be the reason ``_answer_quality`` called it
    exact: the top-ranked symbol's own text (``_answer_quality`` only ever looks at ``symbols[:1]``),
    the verified example's snippet, and each documentation match's own text - one per piece, never
    merged, because the real product mechanism this is re-checking is "does ONE piece, on its own,
    explain everything" - never a union of several partial pieces added together."""
    pieces: list[str] = []
    if answer.symbols:
        pieces.append(answer.symbols[0].text)
    if answer.example is not None:
        pieces.append(answer.example.snippet)
    pieces.extend(match.text for match in answer.doc_matches)
    return pieces


def _independently_exact(scope: Scope, query: str, pieces: Iterable[str]) -> bool:
    """TC-606's own, separate re-check of what "exact" is supposed to mean - computed directly from
    ``relevance.py``'s own ``distinctive_terms``/``present_terms``/``tokenize``, never by reading
    ``match_quality`` or ``unmatched_terms`` off the answer being checked. True when at least one of
    *pieces*, on its own, literally contains (stemmed, symmetric-synonym counting only - never the
    product's own one-way ``USER_TERM_SYNONYMS``, ``fit`` score or ``via_member`` flag) every
    distinctive word of *query*. A query with no distinctive word (bare family/generic words only)
    cannot be mismatched this way, since nothing concrete was asked."""
    terms = distinctive_terms(query, scope.family)
    if not terms:
        return True
    return any(present_terms(terms, tokenize(piece)) == terms for piece in pieces)


def test_a_broader_sample_of_real_task_queries_for_false_exact_labels(corpus: ConformanceCorpus) -> None:
    """Beyond the three named cases: a seeded sample of "how do I use <real symbol's short name>"
    queries (reusing each pilot's own real FQNs, the same way F4/F5 reuse real titles/symbols) per
    pilot, independently re-checked as described in ``_independently_exact`` above - any answer this
    test's own, separate recomputation says should NOT have been "exact" is recorded as a mismatch."""
    observed: list[tuple[str, str]] = []
    for pilot_name in sorted(corpus.pilots):
        scope = corpus.pilots[pilot_name].scope
        fqns = _real_fqns(corpus, pilot_name)
        if not fqns:
            continue
        sample = (
            fqns if exhaustive_enabled() else sample_items(fqns, _BROAD_SAMPLE_SEED, _BROAD_SAMPLE_PER_PILOT)
        )
        for fqn in sample:
            query = f"how do I use {_short_name(fqn)}"
            answer = lookup(corpus.store, scope, query)
            case_id = f"false-exact:{pilot_name}:{query}"
            if isinstance(answer, TaskAnswer):
                if answer.match_quality == MATCH_EXACT and not _independently_exact(
                    scope, query, _piece_texts(answer)
                ):
                    observed.append((pilot_name, case_id))
            elif isinstance(answer, list) and answer and isinstance(answer[0], SymbolMatch):
                top = answer[0]
                if top.match_quality == MATCH_EXACT and not _independently_exact(scope, query, [top.text]):
                    observed.append((pilot_name, case_id))
    assert_no_new_failures(load_baseline(BASELINE_PATH), observed)


# ---- (3) size budget: an in-process, response-budget-free search_docs call for a broad query ----------


def _serialized_bytes(matches: list[DocMatch]) -> int:
    """The raw JSON-serialized byte size of *matches* exactly as returned by ``search_docs`` - never
    passed through the server's response-budget wrapper (``DOC_PART_BYTES``/the 24,576-byte envelope
    cap), which is precisely the mechanism this sub-case bypasses on purpose."""
    return len(json.dumps([dataclasses.asdict(match) for match in matches]).encode("utf-8"))


def test_in_process_search_docs_stays_inside_the_size_budget_for_a_broad_query(
    corpus: ConformanceCorpus,
) -> None:
    """For every real pilot, a deliberately broad query (the pilot's own family word alone, which
    ``search_docs._rule_for`` keeps as a valid rule even after the usual family-word exclusion empties
    the term set) run through ``search_docs`` directly, in-process, with a generous ``top_k`` so the
    tool's own, real, unbudgeted candidate count decides the response size - never this test's cap.
    Any pilot whose raw serialized response exceeds the documented byte ceiling is recorded as a
    failure; several real pilots are EXPECTED to fail this today (that is this sub-case's whole
    purpose: pinning today's real numbers for the later table-dominated guard, Q7/Q8, to fix and verify
    against), so a baseline entry reproducing here is not itself news - a NEW one, outside the
    baseline, is."""
    observed: list[tuple[str, str]] = []
    for pilot_name in sorted(corpus.pilots):
        scope = corpus.pilots[pilot_name].scope
        result = search_docs(corpus.store, scope, scope.family, top_k=_BROAD_QUERY_TOP_K)
        if isinstance(result, DocSearchMiss):
            continue
        size = _serialized_bytes(result)
        if size > _SIZE_BUDGET_BYTES:
            observed.append((pilot_name, f"size-budget:{pilot_name}"))
    assert_no_new_failures(load_baseline(BASELINE_PATH), observed)
