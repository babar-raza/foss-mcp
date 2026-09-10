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
import yaml

from foss_mcp.indexing.topology_spike import (
    EmbeddedProfile,
    LabelledQuery,
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


def test_manifest_storage_topology_matches_the_measured_decision_and_is_not_the_placeholder():
    """Load-bearing test for TC-004's negative control: if the manifest's
    storage_topology is ever reverted to the 'undecided_pending_TC-004'
    placeholder, this must fail, because that would mean the measured
    decision was never actually written back."""
    manifest = _load_manifest()
    recorded = manifest["storage_topology"]

    assert recorded != PLACEHOLDER
    assert recorded, "storage_topology must be non-empty"

    embedded_measurement = measure(EmbeddedProfile(), CORPUS, QUERIES, top_k=TOP_K)
    service_measurement = measure(ServiceShapedProfile(), CORPUS, QUERIES, top_k=TOP_K)
    expected_decision = decide(embedded_measurement, service_measurement)

    assert recorded == expected_decision


def test_manifest_still_validates_against_the_product_manifest_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = _load_manifest()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(manifest))
    assert errors == [], f"manifest failed schema validation: {errors}"
