"""G3/TC-598 (REQ-G3-010): tests of THIS card's own mechanism (the loader, the sampler, and the
baseline contract) - never a product invariant. F4-F9 and the later program-closure cards own the
product invariants and build their own tests on top of ``tests/conformance/corpus.py`` and
``tests/conformance/baseline.py``; nothing here asserts anything about retrieval quality, index
honesty, or any other served behaviour.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from tests.conformance.baseline import (
    Baseline,
    KnownFailure,
    assert_no_new_failures,
    load_baseline,
)
from tests.conformance.corpus import (
    DEMO_MANIFESTS,
    ConformanceCorpus,
    exhaustive_enabled,
    load_demo_manifests_corpus,
    no_embedding_gateway_env,
    sample_items,
    sample_pilots,
)

EXPECTED_PILOT_COUNT = 32


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - nothing in
    the repo is ever written to; reading the real baked data is always allowed."""
    root = tmp_path_factory.mktemp("conformance-core-corpus")
    return load_demo_manifests_corpus(root)


# ---- (a) the loader works against all 32 real baked pilots -------------------------------------------


def test_the_real_demo_manifests_directory_has_the_expected_pilot_count() -> None:
    """The card's own ``recovery`` clause: don't hardcode 32 as a pass condition blindly - name the
    real count found, so a drift is reported rather than silently masked by a hardcoded assertion."""
    names = sorted(p.name for p in DEMO_MANIFESTS.glob("*__self_extracted") if p.is_dir())
    assert len(names) == EXPECTED_PILOT_COUNT, (len(names), names)


def test_corpus_loads_all_32_real_baked_pilots(corpus: ConformanceCorpus) -> None:
    assert len(corpus.pilots) == EXPECTED_PILOT_COUNT, sorted(corpus.pilots)


def test_every_real_pilot_has_a_nonzero_document_count(corpus: ConformanceCorpus) -> None:
    empty = [name for name, pilot in corpus.pilots.items() if not pilot.documents]
    assert not empty, empty


def test_pilot_chunk_helpers_classify_the_real_documents(corpus: ConformanceCorpus) -> None:
    """A real, non-synthetic smoke test of ``ConformancePilot``'s own kind-filtering helpers: every
    pilot's three kind buckets, summed, account for every loaded document (``chunk_kind`` is total)."""
    for pilot in corpus.pilots.values():
        total = len(pilot.doc_chunks()) + len(pilot.example_chunks()) + len(pilot.symbol_chunks())
        assert total == len(pilot.documents), pilot.name
    # At least one pilot (this corpus's own cells__net, used elsewhere as a worked example) has symbol
    # chunks - the classifier is exercised on real data, not merely called on an always-empty bucket.
    assert corpus.pilots["cells__net"].symbol_chunks()


# ---- (b) the sampler is deterministic given a seed, and seeds can differ -----------------------------


def test_sample_pilots_is_deterministic_for_the_same_seed(corpus: ConformanceCorpus) -> None:
    first = sample_pilots(corpus, seed=7, fraction_or_count=0.5)
    second = sample_pilots(corpus, seed=7, fraction_or_count=0.5)
    assert first == second
    assert len(first) == 16


def test_sample_pilots_can_differ_across_seeds(corpus: ConformanceCorpus) -> None:
    samples = {tuple(sample_pilots(corpus, seed=seed, fraction_or_count=0.5)) for seed in range(10)}
    assert len(samples) > 1, samples


def test_sample_pilots_accepts_an_absolute_count(corpus: ConformanceCorpus) -> None:
    sample = sample_pilots(corpus, seed=1, fraction_or_count=5)
    assert len(sample) == 5
    assert len(set(sample)) == 5
    assert set(sample) <= set(corpus.pilots)


def test_sample_items_is_deterministic_for_the_same_seed() -> None:
    items = list(range(100))
    first = sample_items(items, seed=3, n=10)
    second = sample_items(items, seed=3, n=10)
    assert first == second
    assert len(first) == 10


def test_sample_items_can_differ_across_seeds() -> None:
    items = list(range(100))
    samples = {tuple(sample_items(items, seed=seed, n=10)) for seed in range(10)}
    assert len(samples) > 1, samples


def test_sample_items_clamps_n_to_the_pool_size() -> None:
    assert sample_items([1, 2, 3], seed=0, n=100) and len(sample_items([1, 2, 3], seed=0, n=100)) == 3


def test_exhaustive_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOSS_MCP_CONFORMANCE_EXHAUSTIVE", raising=False)
    assert exhaustive_enabled() is False


def test_exhaustive_env_var_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOSS_MCP_CONFORMANCE_EXHAUSTIVE", "1")
    assert exhaustive_enabled() is True


def test_no_embedding_gateway_env_scrubs_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENDPOINT_ENV, "https://example.invalid")
    monkeypatch.setenv(_API_KEY_ENV, "secret")
    with no_embedding_gateway_env():
        assert _ENDPOINT_ENV not in os.environ
        assert _API_KEY_ENV not in os.environ
    assert os.environ[_ENDPOINT_ENV] == "https://example.invalid"
    assert os.environ[_API_KEY_ENV] == "secret"


# ---- (c) load_baseline round-trips a small synthetic JSON fixture, incl. strict-mode tightening ------


def _write_baseline(path: Path, *, strict: bool) -> None:
    payload = {
        "strict": strict,
        "known_failures": [
            {"pilot": "cells__net", "case_id": "c05", "reason": "measured, not yet fixed"},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_baseline_round_trips_the_synthetic_fixture(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    _write_baseline(path, strict=False)
    baseline = load_baseline(path)
    assert baseline.strict is False
    assert baseline.known_failures == (KnownFailure("cells__net", "c05", "measured, not yet fixed"),)


def test_a_case_present_in_both_observed_and_baseline_passes(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    _write_baseline(path, strict=False)
    baseline = load_baseline(path)
    observed = [{"pilot": "cells__net", "case_id": "c05", "reason": "still reproduces"}]
    assert assert_no_new_failures(baseline, observed) is None


def test_a_case_in_observed_but_not_in_baseline_fails_naming_it(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    _write_baseline(path, strict=False)
    baseline = load_baseline(path)
    observed = [
        {"pilot": "cells__net", "case_id": "c05", "reason": "still reproduces"},
        {"pilot": "pdf__net", "case_id": "p02", "reason": "a brand new regression"},
    ]
    with pytest.raises(AssertionError, match="pdf__net:p02"):
        assert_no_new_failures(baseline, observed)


def test_strict_baseline_fails_when_a_known_failure_no_longer_reproduces(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    _write_baseline(path, strict=True)
    baseline = load_baseline(path)
    # cells__net:c05 is known but absent from the observed failures below - it now passes, so the
    # strict baseline must be tightened, and that absence must fail the check.
    with pytest.raises(AssertionError, match="cells__net:c05"):
        assert_no_new_failures(baseline, observed_failures=[])


def test_non_strict_baseline_does_not_fail_when_a_known_failure_no_longer_reproduces(
    tmp_path: Path,
) -> None:
    path = tmp_path / "baseline.json"
    _write_baseline(path, strict=False)
    baseline = load_baseline(path)
    # The same absence as the strict test above, but strict=False (the default): never a failure.
    assert assert_no_new_failures(baseline, observed_failures=[]) is None


def test_assert_no_new_failures_accepts_tuple_and_knownfailure_shaped_observed(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    _write_baseline(path, strict=False)
    baseline = load_baseline(path)
    as_tuple = [("cells__net", "c05")]
    as_known_failure = [KnownFailure("cells__net", "c05", "still reproduces")]
    assert assert_no_new_failures(baseline, as_tuple) is None
    assert assert_no_new_failures(baseline, as_known_failure) is None


def test_baseline_defaults_are_permissive_for_a_minimal_file(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("{}", encoding="utf-8")
    baseline = load_baseline(path)
    assert baseline == Baseline(strict=False, known_failures=())
    assert assert_no_new_failures(baseline, observed_failures=[]) is None
