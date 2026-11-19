"""G3/TC-609 (REQ-G3-010): F9, the example-integrity conformance invariant.

Measures, against EVERY real example chunk in all 32 real baked pilots (no sampling: example
counts per pilot are small - see ``test_every_pilots_example_chunk_count_is_small_enough_for_an_
exhaustive_sweep`` below, which confirms that assumption against the real counts rather than
asserting it blindly), two already-confirmed defects:

1. Orphan fragments. ``chunker.py``'s ``_HEADING`` regex (``^#{1,6}[ \\t]+``, ``re.MULTILINE``)
   reads ANY line that starts a column with 1-6 ``#`` characters as a markdown heading - including
   a column-0 Python/shell-style ``#`` comment line sitting inside an example's own code block. When
   an example's code contains such a line, ``chunk_document`` splits the example there: the comment
   text becomes the new "section"'s title, and the code that follows it becomes a second chunk with
   NO ``FQN:``/``Kind:`` header at all (only the first, pre-split chunk keeps those). This module
   detects that SYMPTOM directly on the already-baked, already-split generation (the pre-split full
   document text is not recoverable from a baked generation): a chunk carrying a ``Source-Commit:``
   line - the verifier's own marker, written only on example-derived chunks - but no ``FQN:`` and no
   ``Kind:`` line at all is exactly the orphaned continuation this defect leaves behind.

2. Mislabelled-verified chunks. A chunk's ``Kind: verified_example`` line (written by the real
   ``is_verified_example_text`` contract - ``find_examples.py``) is set purely from whether the
   candidate COMPILED, independent of whether its recorded run actually succeeded. A chunk can
   therefore carry ``Kind: verified_example`` while its own ``Runtime-Status:`` line reads ``failed``
   or ``no_output`` (the only two failure values ``find_examples.py`` itself recognizes - see its
   ``_STATUS_FAILED``/``_STATUS_NO_OUTPUT`` constants; ``ran`` is the only success value).

This card only measures and pins today's real numbers into the committed baseline
(``tests/fixtures/conformance/baseline_example_integrity.json``, ``"strict": false``); XA1-XA19
(later cards) do the fixing. Nothing here asserts anything about retrieval quality or any other
served behaviour - see ``test_conformance_core.py`` for the loader/sampler/baseline-contract tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import _extract_example, _runtime_fields, is_verified_example_text
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from tests.conformance.baseline import assert_no_new_failures, load_baseline
from tests.conformance.corpus import ConformanceCorpus, ConformancePilot, load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "conformance" / "baseline_example_integrity.json"

# find_examples.py's own real success/failure vocabulary (its private ``_STATUS_FAILED``/
# ``_STATUS_NO_OUTPUT`` constants) - not re-guessed here, read from that module's real source.
_FAILED_RUNTIME_STATUSES = frozenset({"failed", "no_output"})

ORPHAN_CASE_PREFIX = "orphan_fragment"
MISLABELLED_VERIFIED_CASE_PREFIX = "mislabelled_verified"

# Already confirmed by this plan's prior research, before this card re-measured them for real.
# TC-848: cohort C1 was regenerated with one chunk per example (a column-0 ``#`` comment inside an example's code no longer
# splits it), so the three pilots that exhibited the orphan-fragment defect (3d/python 8 orphan chunks, pdf/python 2,
# slides/python 2, confirmed against the previously committed generations) now have none: the defect is FIXED there and no
# pilot is known to exhibit it any more.
PREVIOUSLY_KNOWN_ORPHAN_PILOTS = frozenset({"3d__python", "pdf__python", "slides__python"})
KNOWN_ORPHAN_PILOTS: frozenset[str] = frozenset()
# TC-849: cohort C2 regenerated 3d/net, and TC-802 (publish no example whose run failed or produced nothing) removed its one
# ``Kind: verified_example`` chunk with ``Runtime-Status: failed`` ("Additional Examples", a README example that read a file the
# sandbox does not have): the defect is FIXED there. Every example chunk 3d/net now holds is authored and ran with output.
PREVIOUSLY_KNOWN_MISLABELLED_VERIFIED_PILOTS = frozenset({"3d__net"})
KNOWN_MISLABELLED_VERIFIED_PILOTS = frozenset({"3d__java", "pdf__cpp"})


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - nothing in
    the repo is ever written to; reading the real baked data is always allowed."""
    root = tmp_path_factory.mktemp("example-integrity-corpus")
    return load_demo_manifests_corpus(root)


def _orphan_fragment_findings(pilot: ConformancePilot) -> list[dict[str, str]]:
    """Every chunk in *pilot* that is evidence of the chunker's heading-split defect (see this
    module's own docstring for the exact mechanism): a chunk with a ``Source-Commit:`` line but no
    ``FQN:``/``Kind:`` header at all. Scans EVERY document in the pilot, not only its EXAMPLE-kind
    chunks: an orphaned, headerless chunk has no ``FQN:`` line, so ``relevance.chunk_kind`` itself
    classifies it as ``documentation`` (its own documented default for "no FQN line at all"), never
    ``example`` - this is exactly why the orphan sub-case cannot be found by looking at
    ``example_chunks()`` alone.
    """
    findings: list[dict[str, str]] = []
    for document in pilot.documents:
        text = document["text"]
        if "FQN:" in text or "Kind:" in text:
            continue
        if "Source-Commit:" not in text:
            continue
        first_line = text.splitlines()[0] if text.splitlines() else ""
        findings.append(
            {
                "pilot": pilot.name,
                "case_id": f"{ORPHAN_CASE_PREFIX}:{document['chunk_id']}",
                "reason": (
                    "orphaned code fragment: Source-Commit: present but no FQN:/Kind: header - "
                    f"first line {first_line!r}"
                ),
            }
        )
    return findings


def _mislabelled_verified_findings(pilot: ConformancePilot) -> list[dict[str, str]]:
    """Every example chunk in *pilot* that is labelled ``Kind: verified_example``
    (``is_verified_example_text``) while its own ``Runtime-Status:`` line (read via the real
    ``_runtime_fields``, never re-parsed here) says the run did not succeed."""
    findings: list[dict[str, str]] = []
    for document in pilot.example_chunks():
        text = document["text"]
        if not is_verified_example_text(text):
            continue
        status, _produced, _error = _runtime_fields(text)
        if status not in _FAILED_RUNTIME_STATUSES:
            continue
        fqn = extract_fqn(text) or document["chunk_id"]
        findings.append(
            {
                "pilot": pilot.name,
                "case_id": f"{MISLABELLED_VERIFIED_CASE_PREFIX}:{fqn}",
                "reason": f"Kind: verified_example with Runtime-Status: {status}",
            }
        )
    return findings


def _all_findings(corpus: ConformanceCorpus) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for pilot in corpus.pilots.values():
        findings.extend(_orphan_fragment_findings(pilot))
        findings.extend(_mislabelled_verified_findings(pilot))
    return findings


# ---- exhaustiveness sanity: confirm the card's no-sampling-needed assumption against real counts -----


def test_every_pilots_example_chunk_count_is_small_enough_for_an_exhaustive_sweep(
    corpus: ConformanceCorpus,
) -> None:
    """The card's own assumption ("example counts per pilot are small... no exhaustive_enabled()
    gating needed") checked against the real counts, not assumed: the real measured maximum across
    all 32 pilots is 14 example chunks in one pilot (164 total), both comfortably low enough to
    check in full on every run with no sampling."""
    counts = {name: len(pilot.example_chunks()) for name, pilot in corpus.pilots.items()}
    assert max(counts.values()) < 50, counts


def test_every_real_pilot_has_at_least_one_example_chunk_to_check(corpus: ConformanceCorpus) -> None:
    empty = [name for name, pilot in corpus.pilots.items() if not pilot.example_chunks()]
    assert not empty, empty


# ---- orphan-fragment sub-case (3d/python, pdf/python, slides/python) ---------------------------------


def test_the_three_previously_known_orphan_pilots_no_longer_exhibit_the_defect(
    corpus: ConformanceCorpus,
) -> None:
    """TC-848: 3d/python, pdf/python and slides/python were regenerated with one chunk per example; each still
    has real example chunks (so this is not vacuous) and none has a headerless orphan fragment any more."""
    for name in sorted(PREVIOUSLY_KNOWN_ORPHAN_PILOTS):
        pilot = corpus.pilots[name]
        assert pilot.example_chunks(), name
        assert _orphan_fragment_findings(pilot) == [], name


def test_the_orphan_fragment_detector_still_flags_a_headerless_chunk_carrying_a_source_commit() -> None:
    """The detector itself keeps its coverage now that no real pilot exhibits the defect."""
    commit = "0123456789abcdef0123456789abcdef01234567"
    pilot = ConformancePilot(
        name="synthetic__python",
        scope=Scope(family="synthetic", platform="python"),
        documents=[
            {
                "chunk_id": "whole",
                "text": f"FQN: Example: Whole\nKind: verified_example\nx = 1\nSource-Commit: {commit}",
            },
            {"chunk_id": "orphan", "text": f"y = 2\nSource-Commit: {commit}"},
            {"chunk_id": "prose", "text": "Some documentation with no commit line."},
        ],
    )
    findings = _orphan_fragment_findings(pilot)
    assert [finding["case_id"] for finding in findings] == [f"{ORPHAN_CASE_PREFIX}:orphan"]


def test_no_other_pilot_newly_exhibits_the_orphan_fragment_defect(corpus: ConformanceCorpus) -> None:
    found = {
        finding["pilot"] for pilot in corpus.pilots.values() for finding in _orphan_fragment_findings(pilot)
    }
    new = found - KNOWN_ORPHAN_PILOTS
    assert not new, f"newly found orphan-fragment pilot(s), not previously known: {sorted(new)}"


# ---- mislabelled-verified sub-case (3d/java, pdf/cpp; 3d/net fixed by TC-849) -----------------------------------------


def test_the_previously_known_3d_net_pilot_no_longer_exhibits_the_mislabelled_verified_defect(
    corpus: ConformanceCorpus,
) -> None:
    """TC-849: 3d/net was regenerated; it still has real example chunks (so this is not vacuous) and none is
    labelled verified while its own run failed or produced nothing."""
    for name in sorted(PREVIOUSLY_KNOWN_MISLABELLED_VERIFIED_PILOTS):
        pilot = corpus.pilots[name]
        assert pilot.example_chunks(), name
        assert _mislabelled_verified_findings(pilot) == [], name


def test_the_two_already_known_mislabelled_verified_pilots_still_exhibit_the_defect(
    corpus: ConformanceCorpus,
) -> None:
    found = {
        finding["pilot"]
        for pilot in corpus.pilots.values()
        for finding in _mislabelled_verified_findings(pilot)
    }
    missing = KNOWN_MISLABELLED_VERIFIED_PILOTS - found
    assert not missing, (
        f"previously-confirmed mislabelled-verified pilot(s) no longer reproduce: {sorted(missing)}"
    )


def test_no_other_pilot_newly_exhibits_the_mislabelled_verified_defect(corpus: ConformanceCorpus) -> None:
    found = {
        finding["pilot"]
        for pilot in corpus.pilots.values()
        for finding in _mislabelled_verified_findings(pilot)
    }
    new = found - KNOWN_MISLABELLED_VERIFIED_PILOTS
    assert not new, f"newly found mislabelled-verified pilot(s), not previously known: {sorted(new)}"


def test_extract_example_still_recovers_a_code_body_from_every_mislabelled_verified_chunk(
    corpus: ConformanceCorpus,
) -> None:
    """Reuses the real ``_extract_example`` (never a second extraction, per this card's own
    instruction): every mislabelled-verified chunk still has a recoverable code body, so this
    sub-case's finding is about a REAL example chunk, not a malformed one that merely looks like one."""
    for pilot in corpus.pilots.values():
        for document in pilot.example_chunks():
            text = document["text"]
            if not is_verified_example_text(text):
                continue
            status, _produced, _error = _runtime_fields(text)
            if status in _FAILED_RUNTIME_STATUSES:
                assert _extract_example(text) is not None, (pilot.name, document["chunk_id"])


# ---- the one combined baseline check ------------------------------------------------------------------


def test_example_integrity_failures_match_the_committed_baseline(corpus: ConformanceCorpus) -> None:
    """Every real measured orphan-fragment or mislabelled-verified failure, across all 32 pilots,
    recorded against the committed, non-strict baseline: a FAILURE not already known is new and
    fails loudly (``assert_no_new_failures``); a baseline entry no longer observed is fine while
    ``"strict": false`` (XA1-XA19's own closure card flips that, not this one)."""
    baseline = load_baseline(BASELINE_PATH)
    assert_no_new_failures(baseline, _all_findings(corpus))
