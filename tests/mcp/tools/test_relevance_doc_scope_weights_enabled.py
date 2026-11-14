"""G3/TC-626 (Q6, REQ-G3-011): the measurement-gated decision to enable Q5's
``relevance.doc_scope_term_weights`` (built dormant by TC-625) for ``search_docs`` and ``lookup``
only - ``find_examples`` stays on ``term_weights``, after a real, reproduced regression.

WORKER'S OWN PRE-COMMIT MEASUREMENT (performed by hand, one call site at a time, by temporarily
patching the committed source with ``git`` and reverting before this commit - not re-executed by
this test file, because mutating a committed production file transiently inside a permanent test
is not a safe pattern to leave in the suite): with ``term_weights`` replaced by
``doc_scope_term_weights`` at exactly one of the three real call sites at a time and nothing else
changed,

- ``search_docs._rule_for`` ALONE: the full 361-test bundle below (``test_round3_retrieval_corpus.py``,
  ``test_forgiveness_acceptance.py``, ``test_nonsense_queries_miss.py``,
  ``test_find_examples_title_anchor.py``, ``test_relevance_idf_synonyms.py``,
  ``test_lookup_forgiveness.py``, ``test_lookup_miss_reason.py``,
  ``tests/conformance/test_sentence_wrapped_retrieval.py``) passed completely unchanged (361
  passed, 0 failed, exit 0) - identical to the measured baseline before any change.
- ``lookup._limitation_docs`` ALONE: the same 361-test bundle also passed completely unchanged
  (361 passed, 0 failed, exit 0).
- ``find_examples.find_examples`` ALONE: a real, reproduced regression -
  ``test_forgiveness_acceptance.py::test_verbose_example_queries_do_not_regress`` and
  ``test_find_examples_title_anchor.py::test_the_tc516_example_tables_are_unchanged`` both failed,
  dropping from the pinned floor ``MIN_VERBOSE_HITS = 14`` to a measured ``{'hit': 12, 'miss': 14,
  'wrong': 0}`` - exactly the numbers the card's own ``purpose`` field states. This is why
  ``find_examples.py`` is excluded from this card's wiring: it is untouched by this diff (confirmed
  below by ``git diff`` producing no hunk for that file, and by the liveness checks in this file).

All three isolated results matched the supervisor's own pre-authoring measurement exactly, so this
worker proceeded to wire ``search_docs`` and ``lookup`` together (the real end state the checks
below re-confirm fresh on every run) rather than stopping with ``FAILED_INTERNAL``.

This file's own tests re-confirm, fresh, on every run:

(a)/(b) the full ratchet bundle (both call sites wired together, the real committed end state)
    passes completely, by literally re-invoking it as a subprocess against the real, current
    source tree - not a restatement of a number, a live re-run.
(b) ``search_docs._rule_for`` and ``lookup._limitation_docs`` genuinely call
    ``doc_scope_term_weights``, not ``term_weights`` - a liveness check by direct inspection of
    each function's own code object and of its defining module's namespace, not merely an
    output-shape assertion.
(c) ``find_examples.py`` is genuinely UNCHANGED (still calls ``term_weights``, never
    ``doc_scope_term_weights``) and its own pinned verbose-example ratchet still holds at its
    floor.
(d) the literal LONGQ-001 sentence's real weighted coverage for the real, committed pdf/net
    "Installation, Dependencies, and Quick Start" chunk, measured fresh through the real,
    now-wired ``search_docs._rule_for``/``passage_coverage`` with the SAME ``platform="net"``
    argument the real ``search_docs``/``lookup`` call sites actually pass (TC-610, already
    shipped before this card) - reported honestly, whichever way it falls.

    This worker's own fresh number through that real, platform-aware call path is LOWER than the
    card's own stated figures (0.4286 -> 0.3895): this worker measures 0.3333 (``term_weights``,
    pre-card) -> 0.2984 (``doc_scope_term_weights``, this card). The discrepancy is explained, not
    ignored: LONGQ-001 has 7 distinctive terms before TC-610's platform exclusion and 6 after it
    (``tests/mcp/tools/test_find_examples_query_normalization.py::
    test_the_longq_001_sentence_also_drops_by_one_through_the_wired_module`` already pins this -
    "net" drops out). The card's own stated 0.4286/0.3895 are reproduced bit-for-bit by this same
    test below ONLY when ``platform=None`` is passed instead of the real ``"net"`` - i.e. the
    supervisor's own pre-authoring measurement did not account for TC-610's platform exclusion,
    which every real production call site (``search_docs.search_docs``, ``lookup._limitation_docs``)
    already applies. The qualitative conclusion the card draws - doc-scope weighting is a small
    DECREASE for this one passage, not an increase, and it does not cross ``RELEVANCE_THRESHOLD``
    either way - holds under both figures; only the absolute numbers differ. Per this card's own
    instructions this is reported honestly rather than assumed or silently corrected, and is not a
    ratchet-bundle regression (the bundle re-confirmation in (a)/(b) above is what gates the
    enable decision, and it matches the card exactly).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools import lookup as lookup_module
from foss_mcp.mcp.tools import relevance
from foss_mcp.mcp.tools import search_docs as search_docs_module
from foss_mcp.mcp.tools.get_symbol import extract_fqn

REPO_ROOT = Path(__file__).resolve().parents[3]
PDF_NET_MANIFEST_DIR = REPO_ROOT / "infra" / "demo-manifests" / "pdf__net__self_extracted"

# The exact 8-file bundle this card's own second check command names, reused verbatim here so a
# fresh re-run of it is literally the same bundle the card and this worker both measured against.
RATCHET_BUNDLE: tuple[str, ...] = (
    "tests/mcp/tools/test_round3_retrieval_corpus.py",
    "tests/mcp/tools/test_forgiveness_acceptance.py",
    "tests/mcp/tools/test_nonsense_queries_miss.py",
    "tests/mcp/tools/test_find_examples_title_anchor.py",
    "tests/mcp/tools/test_relevance_idf_synonyms.py",
    "tests/mcp/tools/test_lookup_forgiveness.py",
    "tests/mcp/tools/test_lookup_miss_reason.py",
    "tests/conformance/test_sentence_wrapped_retrieval.py",
)

LONGQ_001_QUERY = (
    "how do I install and reference the Aspose.PDF FOSS library in my .NET project so I can start "
    "converting files"
)
LONGQ_001_TITLE = "Installation, Dependencies, and Quick Start"


def _run_pytest(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---- (a)/(b): the full ratchet bundle, re-run fresh, against the real committed end state ------


def test_the_full_ratchet_bundle_passes_completely_through_the_real_wired_search_docs_and_lookup() -> None:
    """A LIVE re-run (not a restatement) of the exact bundle named in this card's own second check
    command, against the real, current, committed source tree where ``search_docs._rule_for`` and
    ``lookup._limitation_docs`` are both genuinely wired to ``doc_scope_term_weights``. Must exit 0
    with zero failures and zero errors - the end-state confirmation of the isolated, by-call-site
    measurements this worker performed by hand before committing (see module docstring)."""
    # TC-671: 1200s (20 min) replaces the prior 300s budget, which was too tight on this host -
    # the supervisor measured 464s for a subset and 736s for the full bundle, both passing cleanly.
    result = _run_pytest(list(RATCHET_BUNDLE), timeout=1200)
    assert result.returncode == 0, (
        f"the ratchet bundle regressed with doc-scope weights wired into search_docs/lookup "
        f"(exit {result.returncode}):\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
    )


# ---- (b): liveness - which function the real closures actually call, not just output shape -----


def test_search_docs_rule_for_genuinely_calls_doc_scope_term_weights() -> None:
    assert relevance.DOC_SCOPE_WEIGHTS_ENABLED is True
    assert search_docs_module.doc_scope_term_weights is relevance.doc_scope_term_weights
    assert not hasattr(search_docs_module, "term_weights"), (
        "search_docs.py must no longer import term_weights at all once doc_scope_term_weights "
        "replaces its one and only call site"
    )
    code_names = search_docs_module._rule_for.__code__.co_names
    assert "doc_scope_term_weights" in code_names
    assert "term_weights" not in code_names


def test_lookup_limitation_docs_genuinely_calls_doc_scope_term_weights() -> None:
    assert lookup_module.doc_scope_term_weights is relevance.doc_scope_term_weights
    assert not hasattr(lookup_module, "term_weights"), (
        "lookup.py must no longer import term_weights at all once doc_scope_term_weights "
        "replaces its one and only call site"
    )
    code_names = lookup_module._limitation_docs.__code__.co_names
    assert "doc_scope_term_weights" in code_names
    assert "term_weights" not in code_names


# ---- (c): find_examples is genuinely untouched, and its own ratchet still holds -----------------


def test_find_examples_is_genuinely_unchanged_and_still_calls_term_weights() -> None:
    """The deliberate, measured exclusion: find_examples.py keeps calling term_weights, never
    doc_scope_term_weights - confirmed by direct inspection of its own function's code object and
    its defining module's namespace, exactly like the two positive wiring checks above."""
    assert find_examples_module.term_weights is relevance.term_weights
    assert not hasattr(find_examples_module, "doc_scope_term_weights")
    code_names = find_examples_module.find_examples.__code__.co_names
    assert "term_weights" in code_names
    assert "doc_scope_term_weights" not in code_names


def test_find_examples_own_verbose_example_ratchet_still_holds_at_its_pinned_floor() -> None:
    """The two rows this card's own isolated find_examples-alone measurement found regressed below
    MIN_VERBOSE_HITS = 14 (to 12 hits / 14 misses) when doc_scope_term_weights was patched into
    find_examples ALONE - confirmed here, fresh, that with find_examples left untouched (the real
    committed state), both still pass at the pinned floor."""
    result = _run_pytest(
        [
            "tests/mcp/tools/test_forgiveness_acceptance.py::test_verbose_example_queries_do_not_regress",
            "tests/mcp/tools/test_find_examples_title_anchor.py::test_the_tc516_example_tables_are_unchanged",
        ],
        timeout=120,
    )
    assert result.returncode == 0, (
        f"find_examples' own verbose-example ratchet regressed even though find_examples.py was "
        f"not touched by this card (exit {result.returncode}):\n{result.stdout[-4000:]}"
    )


# ---- (d): the literal LONGQ-001 sentence, measured fresh, reported honestly ---------------------


def _real_pdf_net_lexical_payload() -> dict[str, object]:
    """The real, committed pdf/net pilot's own lexical-index payload, read straight from
    infra/demo-manifests - no ingestion, no mocks, exactly as build_lexical_index wrote it (the
    same real fixture tests/mcp/tools/test_relevance_doc_scope_weights.py's own TC-625 test reads)."""
    pointer = json.loads((PDF_NET_MANIFEST_DIR / "active_pointer.json").read_text(encoding="utf-8"))
    suffix = pointer["generation_id"].rsplit("::", 1)[-1]
    generation_file = PDF_NET_MANIFEST_DIR / f"generation__{suffix}.json"
    manifest = json.loads(generation_file.read_text(encoding="utf-8"))
    return manifest["payload"]["lexical_index"]


def _installation_chunk_text(payload: dict[str, object]) -> str:
    documents: dict[str, dict] = payload["documents"]  # type: ignore[assignment]
    for document in documents.values():
        if extract_fqn(document["text"]) == f"Doc: {LONGQ_001_TITLE}":
            return document["text"]
    raise AssertionError("could not find the real 'Installation, Dependencies, and Quick Start' chunk")


def test_longq_001_real_weighted_coverage_through_the_real_wired_search_docs() -> None:
    """Measured fresh, through the REAL search_docs._rule_for/passage_coverage, with the same
    platform="net" argument the real search_docs/lookup call sites actually pass. Reports the real
    number honestly: it does not cross RELEVANCE_THRESHOLD either before or after this card, and
    doc-scope weighting is a small DECREASE for this one specific passage (consistent with the
    card's own finding that not every term's doc-scope weight shifts the same direction as the two
    illustrative terms "install"/"file")."""
    payload = _real_pdf_net_lexical_payload()
    chunk_text = _installation_chunk_text(payload)

    # The real, current, platform-aware call path (exactly what search_docs.search_docs and
    # lookup._limitation_docs both actually pass: platform=scope.platform="net" for pdf/net).
    rule_scoped = search_docs_module._rule_for(LONGQ_001_QUERY, "pdf", payload, platform="net")
    assert rule_scoped is not None
    scoped_coverage = search_docs_module.passage_coverage(rule_scoped, chunk_text)

    terms = relevance.query_terms(LONGQ_001_QUERY, "pdf", platform="net")
    required = relevance.required_terms(LONGQ_001_QUERY)
    pre_card_rule = search_docs_module._Rule(terms, relevance.term_weights(terms, payload), required)
    pre_card_coverage = search_docs_module.passage_coverage(pre_card_rule, chunk_text)

    # This worker's own real, fresh numbers (see module docstring for why they differ from the
    # card's stated 0.4286/0.3895).
    assert pre_card_coverage == pytest.approx(0.3333333333333333, abs=1e-9)
    # TC-835 re-pin: 0.29841659976076695 -> 0.30119742228547147. Mechanism change, not a drift: the
    # doc-scope weights now count DOCUMENTATION chunks only (25 of 948), no longer documentation + example
    # chunks (33 of 948). Authored ``verified_example`` growth (TC-747: 943 -> 948 chunks) had raised the IDF of
    # "file"/"reference" for search_docs and flipped LONGQ-001 onto generic chunks; with examples excluded the
    # weights depend on the documentation alone (file 0.9045, reference 0.7357) and this passage scores
    # 0.3012 while the generic 'Key Capabilities' chunks score 0.3976, both below PARTIAL_COVERAGE_THRESHOLD.
    assert scoped_coverage == pytest.approx(0.30119742228547147, abs=1e-9)
    assert scoped_coverage < pre_card_coverage
    assert scoped_coverage < relevance.RELEVANCE_THRESHOLD
    assert pre_card_coverage < relevance.RELEVANCE_THRESHOLD

    # Reproduce the card's OWN stated figures bit-for-bit, to pin down exactly why they differ:
    # only when platform exclusion is NOT applied (platform=None, i.e. "net" still counted as a
    # distinctive term) does the measurement match 0.4286 -> 0.3895 - proving the discrepancy is
    # TC-610's already-shipped platform exclusion, not a different computation or a mistake in
    # this worker's own measurement.
    rule_scoped_no_platform = search_docs_module._rule_for(LONGQ_001_QUERY, "pdf", payload, platform=None)
    assert rule_scoped_no_platform is not None
    scoped_coverage_no_platform = search_docs_module.passage_coverage(rule_scoped_no_platform, chunk_text)

    terms_no_platform = relevance.query_terms(LONGQ_001_QUERY, "pdf", platform=None)
    pre_card_rule_no_platform = search_docs_module._Rule(
        terms_no_platform, relevance.term_weights(terms_no_platform, payload), required
    )
    pre_card_coverage_no_platform = search_docs_module.passage_coverage(pre_card_rule_no_platform, chunk_text)

    assert pre_card_coverage_no_platform == pytest.approx(0.42857142857142855, abs=1e-9)
    # TC-835 re-pin (0.3895071935068185 -> 0.39266177604136077): same mechanism change as above (documentation-only
    # scope); the unscoped platform=None figure moves with the weights too.
    assert scoped_coverage_no_platform == pytest.approx(0.39266177604136077, abs=1e-9)
    assert math.isclose(pre_card_coverage_no_platform, 0.4286, abs_tol=5e-4)
    assert math.isclose(scoped_coverage_no_platform, 0.3927, abs_tol=5e-4)  # TC-835: was 0.3895
