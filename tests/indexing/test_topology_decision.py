"""Tests for the TC-004 storage-topology spike (plan 18.1 / REQ-G0-006).

Builds a small fixture corpus and at least 20 hand-labelled queries, runs
both candidate topology profiles from ``foss_mcp.indexing.topology_spike``
over them through the profiles' one shared interface, and checks that:

* both profiles answer the identical query set with identical results (they
  share one interface and one scoring core, so the only thing a measured
  difference between them can reflect is topology overhead, not algorithm
  quality);
* the measurement itself produces sane, non-degenerate numbers;
* docs/DECISION_LOG.md records the storage-topology decision with the
  measured numbers and the rule that was applied;
* config/products/pdf/net.yaml's storage_topology has actually been written
  with the decision, not left at the 'undecided_pending_TC-004' placeholder.

That last point is load-bearing: reverting the manifest to the placeholder
must break this suite, otherwise nothing here would actually prove the
measured decision was written back rather than the spike having changed
nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from foss_mcp.indexing import topology_spike
from foss_mcp.indexing.topology_spike import (
    EmbeddedProfile,
    LabelledQuery,
    Measurement,
    ServiceShapedProfile,
    decide,
    measure,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DECISION_LOG_PATH = REPO_ROOT / "docs" / "DECISION_LOG.md"
MANIFEST_PATH = REPO_ROOT / "config" / "products" / "pdf" / "net.yaml"
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "product-manifest.schema.json"

PLACEHOLDER = "undecided_pending_TC-004"
TOP_K = 5

# --------------------------------------------------------------------------
# Fixture corpus: 15 short documents, each about a distinct topic drawn from
# this repository's own domain, so hand-labelling "the one correct doc" per
# query is unambiguous. Built here in code, entirely offline -- nothing is
# fetched.
# --------------------------------------------------------------------------
CORPUS: dict[str, str] = {
    "pdf_extract": (
        "The PDF extraction pipeline parses page trees and content streams "
        "to recover raw text, raster images, and vector graphics from a "
        "PDF document."
    ),
    "tree_sitter": (
        "Tree-sitter grammars build incremental parse trees for source "
        "code, letting the parsing engine query syntax nodes without "
        "reparsing an entire file after a small edit."
    ),
    "normalization": (
        "Normalization converts facts extracted from many source formats "
        "into one canonical shape, so indexing and retrieval never need to "
        "know which extraction backend originally produced a fact."
    ),
    "sharded_index": (
        "Sharded indexing splits a large corpus across several index "
        "partitions keyed by document id, trading query fan-out for "
        "horizontal write throughput as the corpus grows."
    ),
    "retrieval_rank": (
        "The retrieval layer ranks candidate passages with a scoring "
        "function and assembles the final ranked result list handed back "
        "to a calling tool."
    ),
    "mcp_server": (
        "The MCP server exposes registered tools over a protocol "
        "transport, routing each incoming client request to the handler "
        "implementation registered for it."
    ),
    "mcp_tools": (
        "Individual MCP tool handlers translate one protocol request into "
        "calls against extraction, normalization, indexing, or retrieval, "
        "then shape the reply payload."
    ),
    "furnished_content": (
        "Furnished content assembly decorates normalized facts with the "
        "surrounding context, cross references, and worked examples a "
        "downstream reader needs."
    ),
    "telemetry": (
        "Telemetry instrumentation records structured logs, counters, and "
        "distributed traces for every pipeline stage, so a slow or "
        "failing ingestion run can be diagnosed afterward."
    ),
    "config_loader": (
        "Configuration loading validates every product manifest against "
        "its JSON schema before exposing typed settings to the rest of "
        "the product code."
    ),
    "embedded_profile": (
        "An embedded retrieval index keeps its data structures entirely "
        "inside the host process's own memory, with no network hop and no "
        "separate service to deploy or operate."
    ),
    "service_profile": (
        "A service-shaped retrieval index lives behind a client and "
        "server boundary, paying a request serialization and network "
        "round trip cost on every query it answers."
    ),
    "git_worktree": (
        "A throwaway git worktree checks out one pinned revision in "
        "isolation, so automated verification only ever sees committed "
        "content, never an author's uncommitted local changes."
    ),
    "schema_validation": (
        "Schema validation checks a manifest's shape and required fields "
        "against its JSON Schema document before the manifest is trusted "
        "anywhere else downstream."
    ),
    "decision_log": (
        "A decision log records why a choice was made, the numbers "
        "measured to support it, and the rule that was applied, so the "
        "reasoning outlives the person who made the call."
    ),
}

# --------------------------------------------------------------------------
# 30 hand-labelled queries (plan 18.1 requires at least 20): free-form query
# text paired with the doc_id a human labeller says is the correct answer.
# --------------------------------------------------------------------------
QUERIES: list[LabelledQuery] = [
    LabelledQuery("how does the extraction pipeline recover raster images from a PDF", "pdf_extract"),
    LabelledQuery("what parses PDF page trees and content streams", "pdf_extract"),
    LabelledQuery("incremental parse trees for source code without reparsing the whole file", "tree_sitter"),
    LabelledQuery("which grammars let the engine query syntax nodes", "tree_sitter"),
    LabelledQuery("converting extracted facts into one canonical shape", "normalization"),
    LabelledQuery("why doesn't indexing need to know the original extraction backend", "normalization"),
    LabelledQuery("splitting a large corpus across index partitions by document id", "sharded_index"),
    LabelledQuery("trading query fan-out for horizontal write throughput", "sharded_index"),
    LabelledQuery("ranking candidate passages with a scoring function", "retrieval_rank"),
    LabelledQuery("assembling the final ranked result list for a calling tool", "retrieval_rank"),
    LabelledQuery("routing an incoming client request to a registered handler", "mcp_server"),
    LabelledQuery("exposing registered tools over a protocol transport", "mcp_server"),
    LabelledQuery("translating a protocol request into extraction and retrieval calls", "mcp_tools"),
    LabelledQuery("shaping the reply payload of an MCP tool handler", "mcp_tools"),
    LabelledQuery(
        "decorating normalized facts with cross references and worked examples", "furnished_content"
    ),
    LabelledQuery("assembling context a downstream reader needs from normalized facts", "furnished_content"),
    LabelledQuery("recording structured logs counters and distributed traces", "telemetry"),
    LabelledQuery("diagnosing a slow or failing ingestion run afterward", "telemetry"),
    LabelledQuery(
        "validating a product manifest against its json schema before exposing settings", "config_loader"
    ),
    LabelledQuery("loading typed configuration settings for product code", "config_loader"),
    LabelledQuery(
        "keeping data structures inside the host process memory with no network hop", "embedded_profile"
    ),
    LabelledQuery("an index with no separate service to deploy or operate", "embedded_profile"),
    LabelledQuery("a client and server boundary paying a request serialization cost", "service_profile"),
    LabelledQuery("network round trip cost on every single query answered", "service_profile"),
    LabelledQuery("checking out one pinned revision in an isolated throwaway worktree", "git_worktree"),
    LabelledQuery("verification only ever seeing committed content not local changes", "git_worktree"),
    LabelledQuery(
        "checking a manifest shape and required fields against its schema document", "schema_validation"
    ),
    LabelledQuery("why the manifest must be trusted only after schema validation", "schema_validation"),
    LabelledQuery("recording why a choice was made and the numbers that supported it", "decision_log"),
    LabelledQuery("a record of the rule applied so reasoning outlives its author", "decision_log"),
]


def _run_both_profiles() -> tuple[EmbeddedProfile, ServiceShapedProfile]:
    embedded = EmbeddedProfile()
    embedded.build(CORPUS)
    service = ServiceShapedProfile()
    service.build(CORPUS)
    return embedded, service


def _load_manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_fixture_has_at_least_twenty_labelled_queries_over_a_real_corpus():
    """Plan 18.1 input: 'at least 20 hand-labelled queries.'"""
    assert len(CORPUS) >= 10
    assert len(QUERIES) >= 20
    # Every labelled query must point at a doc that actually exists.
    assert {q.correct_doc_id for q in QUERIES} <= set(CORPUS)


def test_both_profiles_answer_the_same_query_set_with_identical_results():
    """Both candidates sit behind ONE common interface and share the same
    scoring core, so for every query in the shared query set they must
    return the exact same ranked doc_ids -- a measured difference between
    them can only be topology overhead, never retrieval quality."""
    embedded, service = _run_both_profiles()
    for labelled in QUERIES:
        embedded_result = embedded.query(labelled.text, top_k=TOP_K)
        service_result = service.query(labelled.text, top_k=TOP_K)
        assert embedded_result == service_result, labelled.text
        assert len(embedded_result) > 0, f"no results at all for: {labelled.text!r}"


def test_measure_reports_sane_relevance_and_latency_for_both_profiles():
    embedded_measurement = measure(EmbeddedProfile(), CORPUS, QUERIES, top_k=TOP_K)
    service_measurement = measure(ServiceShapedProfile(), CORPUS, QUERIES, top_k=TOP_K)

    for m in (embedded_measurement, service_measurement):
        assert m.n_queries == len(QUERIES)
        assert 0.0 <= m.top5_relevance <= 1.0
        assert m.p95_latency_seconds >= 0.0

    # The fixture corpus is small and each query was labelled from its
    # source doc's own vocabulary, so both profiles (same scoring core)
    # should find the correct doc in the top 5 for the large majority of
    # queries. This is a sanity check on the fixture, not a demand for 100%.
    assert embedded_measurement.top5_relevance >= 0.7
    assert service_measurement.top5_relevance == embedded_measurement.top5_relevance

    # The service-shaped profile pays an explicit serialize/deserialize
    # round trip on every query on top of the identical scoring work, so it
    # must never be measured as faster than the embedded profile.
    assert service_measurement.p95_latency_seconds >= embedded_measurement.p95_latency_seconds


def test_decide_picks_a_valid_profile_from_the_measured_numbers():
    """Exercises the plan 18.1 decision rule directly against this
    fixture's own measurements (a separate test below cross-checks this
    against what actually ended up written to the manifest)."""
    embedded_measurement = measure(EmbeddedProfile(), CORPUS, QUERIES, top_k=TOP_K)
    service_measurement = measure(ServiceShapedProfile(), CORPUS, QUERIES, top_k=TOP_K)
    decision = decide(embedded_measurement, service_measurement)
    assert decision in {EmbeddedProfile.name, ServiceShapedProfile.name}


def test_decision_log_has_one_storage_topology_entry_with_numbers_and_rule():
    assert DECISION_LOG_PATH.is_file(), f"{DECISION_LOG_PATH} does not exist"
    text = DECISION_LOG_PATH.read_text(encoding="utf-8")

    headings = list(re.finditer(r"(?im)^##.*storage topology.*$", text))
    assert len(headings) == 1, f"expected exactly one storage-topology entry, found {len(headings)}"

    entry_start = headings[0].start()
    rest = text[entry_start:]
    next_heading = re.search(r"\n##", rest[1:])
    entry_block = rest[: next_heading.start() + 1] if next_heading else rest

    assert "2026-09-10" in entry_block
    assert "TC-004" in entry_block
    assert re.search(r"p95", entry_block, re.IGNORECASE)
    assert re.search(r"relevance", entry_block, re.IGNORECASE)
    assert re.search(r"\d", entry_block), "entry must contain measured numbers"
    assert re.search(r"rule", entry_block, re.IGNORECASE)
    non_blank_lines = [line for line in entry_block.strip().splitlines() if line.strip()]
    assert len(non_blank_lines) <= 6, "decision entry must be at most 6 lines"


# --------------------------------------------------------------------------
# Fixed, synthetic measurements for the decision tests below. ``measure()``
# derives p95 latency from wall-clock time, so a live re-measurement flips on
# a busy host (observed: recorded ``embedded``, re-derived ``service_shaped``).
# These objects are built exactly as ``measure()`` returns them
# (``Measurement(profile_name, p95_latency_seconds, top5_relevance, n_queries)``)
# but with fixed numbers, so the decision under test never depends on the clock.
# --------------------------------------------------------------------------

# The numbers recorded for the TC-004 spike in docs/DECISION_LOG.md
# (2026-09-10 entry): embedded p95=0.058ms, service_shaped p95=0.692ms,
# relevance 30/30 for both. That entry is the measurement the manifest's
# recorded decision was derived from.
_RECORDED_EMBEDDED = Measurement(
    profile_name=EmbeddedProfile.name,
    p95_latency_seconds=0.058e-3,
    top5_relevance=30 / 30,
    n_queries=30,
)
_RECORDED_SERVICE = Measurement(
    profile_name=ServiceShapedProfile.name,
    p95_latency_seconds=0.692e-3,
    top5_relevance=30 / 30,
    n_queries=30,
)

# Clearly synthetic measurements (not from the repository's history) that
# drive ``decide`` down each of its other branches.
_SYNTHETIC_EMBEDDED_LOW_RELEVANCE = Measurement(
    profile_name=EmbeddedProfile.name,
    p95_latency_seconds=0.058e-3,
    top5_relevance=0.80,  # 0.80 / 1.00 = 0.80 < 0.90: more than 10% worse
    n_queries=30,
)
_SYNTHETIC_EMBEDDED_HIGH_LATENCY = Measurement(
    profile_name=EmbeddedProfile.name,
    p95_latency_seconds=2.0e-3,  # 2.0ms / 0.692ms is about 2.9x: above 2x
    top5_relevance=30 / 30,
    n_queries=30,
)


def test_manifest_storage_topology_matches_the_measured_decision_and_is_not_the_placeholder():
    """Load-bearing test for TC-004's negative control: if the manifest's
    storage_topology is ever reverted to the 'undecided_pending_TC-004'
    placeholder, this must fail, because that would mean the measured
    decision was never actually written back.

    Deterministic by construction (TC-430): the decision is re-derived from
    the FIXED measurement recorded in docs/DECISION_LOG.md, never by timing
    the profiles again, so host load cannot flip it."""
    manifest = _load_manifest()
    recorded = manifest["storage_topology"]

    assert recorded != PLACEHOLDER
    assert recorded, "storage_topology must be non-empty"

    # (a) the recorded decision is what the decision rule yields for the
    # measurement the spike recorded.
    assert decide(_RECORDED_EMBEDDED, _RECORDED_SERVICE) == recorded

    # (b) the recorded value is one of the values decide() can return. Every
    # branch of the rule is exercised with fixed measurements.
    reachable = {
        decide(_RECORDED_EMBEDDED, _RECORDED_SERVICE),
        decide(_SYNTHETIC_EMBEDDED_LOW_RELEVANCE, _RECORDED_SERVICE),
        decide(_SYNTHETIC_EMBEDDED_HIGH_LATENCY, _RECORDED_SERVICE),
    }
    assert reachable == {EmbeddedProfile.name, ServiceShapedProfile.name}
    assert decide(_SYNTHETIC_EMBEDDED_LOW_RELEVANCE, _RECORDED_SERVICE) == ServiceShapedProfile.name
    assert decide(_SYNTHETIC_EMBEDDED_HIGH_LATENCY, _RECORDED_SERVICE) == ServiceShapedProfile.name
    assert recorded in reachable


def test_live_relevance_matches_the_recorded_measurement_without_timing_fields():
    """The non-timing part of the recorded measurement stays live: retrieval
    quality depends on the corpus and the scoring core, not on the clock.
    Only ``top5_relevance`` and ``n_queries`` are asserted -- never latency."""
    embedded_measurement = measure(EmbeddedProfile(), CORPUS, QUERIES, top_k=TOP_K)
    service_measurement = measure(ServiceShapedProfile(), CORPUS, QUERIES, top_k=TOP_K)

    assert embedded_measurement.n_queries == len(QUERIES) == _RECORDED_EMBEDDED.n_queries
    assert service_measurement.n_queries == len(QUERIES) == _RECORDED_SERVICE.n_queries
    assert embedded_measurement.top5_relevance == _RECORDED_EMBEDDED.top5_relevance
    assert service_measurement.top5_relevance == _RECORDED_SERVICE.top5_relevance


def test_decision_is_pure_and_the_decision_test_never_calls_measure(monkeypatch):
    """Regression test for the flake TC-430 fixed. ``decide`` is a pure
    function of its two measurements, so the same fixed inputs give the same
    answer every time; and the manifest decision test must still pass when
    ``measure`` (the only place wall-clock timing enters) is made to raise,
    proving it does not re-measure."""
    first = decide(_RECORDED_EMBEDDED, _RECORDED_SERVICE)
    second = decide(_RECORDED_EMBEDDED, _RECORDED_SERVICE)
    assert first == second

    def _forbidden_measure(*args, **kwargs):
        raise AssertionError("the decision test must not re-measure wall-clock timings")

    monkeypatch.setattr(topology_spike, "measure", _forbidden_measure)
    monkeypatch.setitem(globals(), "measure", _forbidden_measure)

    test_manifest_storage_topology_matches_the_measured_decision_and_is_not_the_placeholder()

    # Prove the guard is live: a call to measure() now raises.
    with pytest.raises(AssertionError, match="must not re-measure"):
        measure(EmbeddedProfile(), CORPUS, QUERIES, top_k=TOP_K)


def test_manifest_still_validates_against_the_product_manifest_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = _load_manifest()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(manifest))
    assert errors == [], f"manifest failed schema validation: {errors}"
