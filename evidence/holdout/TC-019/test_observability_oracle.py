"""HOLDOUT oracle for TC-019 — supervisor-authored, not named by the card.

Both halves of this card are trivially satisfiable in ways that pass a suite and
fail in production.

**Readiness.** `is_ready` returning False is correct when nothing is published
and catastrophic when something is — a probe that is always False takes a healthy
deployment out of rotation forever, and one that is always True is the exact
reference-system defect being replaced (reporting ready while serving nothing).
Only asserting *both* directions, against a real store, distinguishes a working
probe from a constant.

**Cardinality.** The rule is that `symbol_fqn` and raw query text never become
metric labels. A suite can check that one named field is absent while the raw
query rides along inside some other field. So this oracle feeds a query
containing a distinctive sentinel through the real builder and asserts the
sentinel appears *nowhere* in the resulting event — any field, nested or not.

**Drop accounting.** A bounded queue is only honest if the drop counter is real.
An implementation that silently evicts old events to make room also "never grows
past max", and loses data a consumer had not drained.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

WT = pathlib.Path(__file__).resolve().parents[3]
for p in (str(WT), str(WT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from foss_mcp.indexing.generation_manifest import GenerationManifestStore  # noqa: E402
from foss_mcp.indexing.publisher import publish_generation  # noqa: E402
from foss_mcp.mcp.health import (  # noqa: E402
    DeploymentGenerationStore,
    is_alive,
    is_ready,
    round_trip_check,
)
from foss_mcp.mcp.routing import Scope  # noqa: E402
from foss_mcp.normalization.chunker import chunk_document  # noqa: E402
from foss_mcp.normalization.document_schema import (  # noqa: E402
    Provenance,
    SourceKind,
    make_document,
)
from foss_mcp.telemetry.usage_recorder import UsageRecorder, build_event  # noqa: E402

PDF_NET = Scope(family="pdf", platform="net")
SENTINEL = "Aspose.Pdf.SecretInternalSymbol.QueryTextSentinel"


class _Provider:
    dimension = 8

    def embed(self, texts):
        return [[float((len(t) + i) % 5) for i in range(self.dimension)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


def _deployment(tmp_path):
    return DeploymentGenerationStore.for_scope(
        GenerationManifestStore(tmp_path / "manifests"), PDF_NET, "self_extracted"
    )


# ------------------------------------------------------------------- readiness
def test_readiness_is_false_before_anything_is_published(tmp_path):
    """The deployment can answer, but it has nothing to answer WITH."""
    store = _deployment(tmp_path)
    assert is_alive() is True, "liveness must not depend on published state"
    assert is_ready(store) is False, (
        "readiness reported True with no active generation - this is precisely the defect being "
        "replaced: ready while serving nothing"
    )


def test_readiness_becomes_true_once_a_generation_is_active(tmp_path):
    """Anti-vacuity, and the failure mode nobody tests for: a probe hardwired to
    False takes a healthy deployment out of rotation permanently."""
    store = _deployment(tmp_path)
    assert is_ready(store) is False

    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="holdout/oracle", commit="0" * 40, path="p"),
        title="t",
        body="## Document\n\nDocument is a class.\n\n## Page\n\nPage is a class.",
    )
    lease = store.manifest_store.acquire_lease(store.scope_key, "holdout", "pending")
    publish_generation(
        store.manifest_store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunk_document(doc),
        embedding_provider=_Provider(),
        lease=lease,
    )

    assert is_ready(store) is True, "readiness stayed False after a real generation was published"
    assert round_trip_check(store) is True, "the round-trip probe could not read what was just published"


def test_readiness_tracks_the_deployments_OWN_scope_not_any_generation(tmp_path):
    """A probe that reports ready because SOMEBODY published something is a
    cross-product leak wearing a health check."""
    manifest_store = GenerationManifestStore(tmp_path / "manifests")
    mine = DeploymentGenerationStore.for_scope(manifest_store, PDF_NET, "self_extracted")
    other = Scope(family="cells", platform="python")

    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="holdout/oracle", commit="0" * 40, path="p"),
        title="t",
        body="## Workbook\n\nWorkbook is a class.",
    )
    key = "::".join((other.family, other.platform, "self_extracted"))
    lease = manifest_store.acquire_lease(key, "holdout", "pending")
    publish_generation(
        manifest_store,
        family=other.family,
        platform=other.platform,
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunk_document(doc),
        embedding_provider=_Provider(),
        lease=lease,
    )

    assert is_ready(mine) is False, (
        "pdf/net reported ready because cells/python had published - readiness is not bound to the "
        "deployment's own scope"
    )


# ----------------------------------------------------------------- cardinality
def test_raw_query_text_survives_nowhere_on_the_event():
    """Not "the field is absent" - the TEXT is absent, anywhere, nested or not.

    A high-cardinality value that escapes into any field will eventually reach a
    metric label, and unbounded label cardinality is how a metrics backend dies.
    """
    event = build_event(
        request_correlation_id="corr-1",
        deployment_id="pdf-net-1",
        generation_id="pdf::net::self_extracted::v1",
        tool_name="search_symbols",
        outcome="success",
        latency_ms=12.5,
        query=SENTINEL,
    )
    rendered = json.dumps(event, default=lambda o: getattr(o, "__dict__", str(o)))
    assert SENTINEL not in rendered, (
        f"the raw query text survived onto the event: {rendered[:400]}"
    )
    assert "QueryTextSentinel" not in rendered, "a fragment of the raw query survived"

    shape = getattr(event, "query_shape_category", None)
    assert shape, "query_shape_category is missing - the bounded summary that replaces raw text"
    assert SENTINEL not in str(shape)


def test_the_event_still_carries_its_contract_fields():
    """Anti-vacuity: dropping everything would also pass the test above."""
    event = build_event(
        request_correlation_id="corr-2",
        deployment_id="pdf-net-1",
        generation_id="pdf::net::self_extracted::v1",
        tool_name="get_symbol",
        outcome="not_found",
        latency_ms=3.0,
        query="Document",
    )
    for field in ("event_id", "request_correlation_id", "deployment_id", "generation_id",
                  "tool_name", "outcome", "latency_ms", "query_shape_category"):
        assert getattr(event, field, None) is not None, f"contract field {field} is missing"
    assert event.tool_name == "get_symbol"
    assert event.outcome == "not_found", "a miss must be recorded as a miss, not smoothed to success"


# --------------------------------------------------------------- drop accounting
def test_overflow_drops_the_new_event_and_counts_it_rather_than_evicting():
    """A queue that evicts to make room also "never grows past max" - and loses
    events a consumer had not drained yet."""
    recorder = UsageRecorder(max_queued=3)

    def ev(n):
        return build_event(
            request_correlation_id=f"corr-{n}",
            deployment_id="d",
            generation_id="g",
            tool_name="lookup",
            outcome="success",
            latency_ms=1.0,
        )

    kept = [ev(i) for i in range(3)]
    for e in kept:
        assert recorder.record(e) is True

    assert recorder.record(ev(99)) is False, "an over-capacity record claimed success"
    assert recorder.dropped_count == 1, f"the drop was not counted: {recorder.dropped_count}"
    assert len(recorder) == 3, "the queue grew past its bound"

    drained = recorder.drain()
    assert [e.request_correlation_id for e in drained] == ["corr-0", "corr-1", "corr-2"], (
        "the earliest events were evicted to make room; the bound must drop the NEW event so a "
        "burst cannot silently destroy undrained history"
    )


def test_recording_never_raises_even_when_saturated():
    """The serving path must never pay for stalled delivery."""
    recorder = UsageRecorder(max_queued=1)
    for _ in range(50):
        recorder.record(
            build_event(
                request_correlation_id="c",
                deployment_id="d",
                generation_id="g",
                tool_name="lookup",
                outcome="success",
                latency_ms=1.0,
            )
        )
    assert len(recorder) == 1
    assert recorder.dropped_count == 49, f"drops undercounted: {recorder.dropped_count}"
