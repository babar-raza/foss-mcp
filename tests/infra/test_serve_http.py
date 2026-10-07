"""``/healthz`` is real, unconditional liveness; ``/readyz`` reflects the real active-generation
state of the real manifest store AND that the generation it names actually carries queryable
content - never process reachability and never merely "a pointer exists". This closes the
confirmed reference-system defect (health.py's own docstring, and the 2026-09-25 audit that
found ``is_alive``/``round_trip_check`` had zero call sites outside their own tests): TC-137
wires both of ``foss_mcp.mcp.health``'s already-implemented probes into ``infra/serve_http.py``
for the first time.

All tests drive ``serve_http.build_app``'s real ASGI app with a real
``GenerationManifestStore`` rooted at ``tmp_path`` - never a mock - through Starlette's
``TestClient``, the same in-process pattern ``tests/mcp/test_server_wiring.py`` already uses.
The "ready" and "content-less" cases publish a real generation through
``foss_mcp.indexing.publisher.publish_generation`` with real ``Chunk`` objects (or a real, empty
chunk list) and the real ``DeterministicEmbeddingProvider`` from
``tests.indexing.test_index_writers`` - never a hand-built payload - so the assertion is
actually exercising the same path a live ingest would.
"""

from __future__ import annotations

import sys
from pathlib import Path

from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore, build_manifest
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.indexing.publisher import content_chunk_id, new_version, publish_generation
from foss_mcp.indexing.vector_index_writer import build_vector_index
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance
from foss_mcp.telemetry.usage_recorder import UsageRecorder
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

# infra/ is not a package (no __init__.py, matching scripts/ convention) - import its module
# directly from the path, the same way tests/mcp/test_server_wiring.py and infra's own
# entrypoints do it.
sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

DEPLOYMENT_CONFIG = DeploymentConfig(family="pdf", platform="net")
PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def _client(store: GenerationManifestStore) -> TestClient:
    app = serve_http.build_app(DEPLOYMENT_CONFIG, manifest_store=store, allowed_origins=[])
    return TestClient(app)


def _chunks() -> list[Chunk]:
    return [
        Chunk(
            "Document",
            "The Document class opens and saves PDF files.",
            "self_extracted",
            "api_surface",
            PROVENANCE,
            "highest",
            (),
            NOT_CHECKED,
        )
    ]


def _publish(
    store: GenerationManifestStore, scope: str, family: str, platform: str, chunks: list[Chunk]
) -> str:
    lease = store.acquire_lease(scope, held_by="test-worker", generation_id="pending")
    return publish_generation(
        store,
        family=family,
        platform=platform,
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )


def _publish_bypassing_safety_guard(
    store: GenerationManifestStore, scope: str, family: str, platform: str, chunks: list[Chunk]
) -> str:
    """Construct and activate a generation via the store's own lower-level primitives -
    ``write_and_validate`` then ``cas_activate``, the exact two calls
    ``generation_manifest.publish`` itself wraps - deliberately bypassing publish()'s
    ``assert_publish_is_safe`` guard (TC-242). Used ONLY by the empty-active-generation test
    below: that test needs to reach a real, ACTIVE generation with zero chunks to prove
    round_trip_check/readyz's own behavior at that layer, a state the guarded
    ``publish_generation``/``publish`` path now correctly refuses to create. Every other test
    in this file still goes through the real ``publish_generation`` path and must keep doing
    so."""
    source_kind = "self_extracted"
    version = new_version()
    probe_generation_id = build_manifest(family, platform, source_kind, version).generation_id
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    payload = {
        "vector_index": build_vector_index(
            chunks, chunk_ids, DeterministicEmbeddingProvider(), probe_generation_id
        ),
        "lexical_index": build_lexical_index(chunks, chunk_ids, probe_generation_id),
    }
    manifest = build_manifest(family, platform, source_kind, version, payload)
    lease = store.acquire_lease(scope, held_by="test-worker", generation_id="pending")
    generation_id = store.write_and_validate(manifest)
    return store.cas_activate(scope, None, generation_id, lease)


def test_healthz_returns_200_with_no_active_generation(tmp_path: Path) -> None:
    """``/healthz`` is unconditional liveness (``is_alive``'s own documented contract) - it
    must report 200 even when NO generation has ever been published for this deployment's
    scope, unlike ``/readyz`` which correctly stays 503 in that same state."""
    store = _store(tmp_path)
    with _client(store) as client:
        response = client.get("/healthz")
        assert response.status_code == 200


def test_healthz_returns_200_after_a_real_generation_is_published(tmp_path: Path) -> None:
    """``/healthz`` stays 200 regardless of generation state - publishing real content must
    never be a precondition for liveness, only for readiness."""
    store = _store(tmp_path)
    _publish(store, "pdf::net::self_extracted", "pdf", "net", _chunks())
    with _client(store) as client:
        response = client.get("/healthz")
        assert response.status_code == 200


def test_healthz_is_never_rejected_for_missing_mcp_headers(tmp_path: Path) -> None:
    """A plain container healthcheck carries neither an Origin nor an MCP-Protocol-Version
    header - ``/healthz`` must answer on its own merits, never be caught by
    RejectionMiddleware's MCP-transport-only rejection rules."""
    store = _store(tmp_path)
    with _client(store) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.text != '{"error": "rejected"}'


def test_readyz_returns_503_for_a_generation_with_no_queryable_content(tmp_path: Path) -> None:
    """A real, published, ACTIVE generation that carries zero chunks (so its lexical index has
    no documents) must still report 503 - this is the real behavior change round_trip_check
    brings over the old is_ready check: is_ready would see the active-generation pointer and
    wrongly report ready, because it never looks past the pointer to the content it names."""
    store = _store(tmp_path)
    _publish_bypassing_safety_guard(store, "pdf::net::self_extracted", "pdf", "net", [])
    with _client(store) as client:
        response = client.get("/readyz")
        assert response.status_code == 503


def test_readyz_returns_503_against_an_empty_store(tmp_path: Path) -> None:
    """No generation has ever been published for this deployment's scope - readiness must be
    honest about that, never merely "the process answers"."""
    store = _store(tmp_path)
    with _client(store) as client:
        response = client.get("/readyz")
        assert response.status_code == 503


def test_readyz_returns_200_after_a_real_generation_is_published(tmp_path: Path) -> None:
    """After a real generation is published for the SAME (family, platform, source_kind) this
    deployment serves, /readyz must flip to ready."""
    store = _store(tmp_path)
    scope = "pdf::net::self_extracted"
    lease = store.acquire_lease(scope, held_by="test-worker", generation_id="pending")
    publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=_chunks(),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    with _client(store) as client:
        response = client.get("/readyz")
        assert response.status_code == 200


def test_readyz_for_a_different_deployment_scope_stays_503(tmp_path: Path) -> None:
    """Publishing a generation for a DIFFERENT (family, platform) must never make an unrelated
    deployment's /readyz report ready - readiness is scoped, never global."""
    store = _store(tmp_path)
    other_scope = "slides::python::self_extracted"
    lease = store.acquire_lease(other_scope, held_by="test-worker", generation_id="pending")
    publish_generation(
        store,
        family="slides",
        platform="python",
        source_kind="self_extracted",
        expected_active=None,
        chunks=_chunks(),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    with _client(store) as client:  # client is still bound to DEPLOYMENT_CONFIG (pdf/net)
        response = client.get("/readyz")
        assert response.status_code == 503


def test_readyz_is_never_rejected_for_missing_mcp_headers(tmp_path: Path) -> None:
    """Dockerfile.serving's real HEALTHCHECK sends neither an Origin nor an
    MCP-Protocol-Version header - /readyz must answer on its own merits, never be caught by
    RejectionMiddleware's MCP-transport-only rejection rules."""
    store = _store(tmp_path)
    with _client(store) as client:
        response = client.get("/readyz")
        assert response.status_code in (200, 503)  # never 400 "rejected"
        if response.status_code != 200:
            assert response.text != '{"error": "rejected"}'


def _assert_response_is_allowed(response) -> None:
    assert response.status_code != 400, response.text


# ---------------------------------------------------------------------
# /metrics (G2/TC-142): real, live counters from the SAME UsageRecorder instance build_app
# hands to create_server - never a separate or mocked one. The deeper proof that real tool
# calls actually move these counters lives in tests/mcp/test_server_wiring.py, which already
# has a real MCP session helper to drive tools/call through this same app; these tests cover
# /metrics' own shape, header-exemption, and instance-sharing contract directly.
# ---------------------------------------------------------------------


def test_metrics_is_reachable_with_no_origin_or_protocol_version_headers(tmp_path: Path) -> None:
    """A plain metrics scrape carries neither header - /metrics must answer on its own merits,
    never be caught by RejectionMiddleware's MCP-transport-only rejection rules."""
    store = _store(tmp_path)
    with _client(store) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        body = response.json()
        assert body == {"usage_events_queued": 0, "usage_events_dropped": 0}


def test_metrics_reports_the_same_live_recorder_build_app_was_given(tmp_path: Path) -> None:
    """build_app must hand create_server and /metrics the SAME UsageRecorder instance, not two
    separate ones - proven by recording directly into the instance passed to build_app and
    confirming /metrics reflects it without any tool call at all.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
    import serve_http

    from foss_mcp.telemetry.usage_recorder import build_event

    store = _store(tmp_path)
    recorder = UsageRecorder()
    recorder.record(
        build_event(
            request_correlation_id="corr-x",
            deployment_id="pdf::net",
            generation_id=None,
            tool_name="search_symbols",
            outcome="success",
            latency_ms=1.0,
        )
    )
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=store,
        allowed_origins=[],
        usage_recorder=recorder,
    )
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.json() == {"usage_events_queued": 1, "usage_events_dropped": 0}


def test_metrics_reports_a_real_drop_once_the_queue_is_at_capacity(tmp_path: Path) -> None:
    """usage_events_dropped must reflect a genuine drop, not just stay 0 forever."""
    import sys

    sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
    import serve_http

    from foss_mcp.telemetry.usage_recorder import build_event

    store = _store(tmp_path)
    recorder = UsageRecorder(max_queued=1)
    recorder.record(
        build_event(
            request_correlation_id="corr-1",
            deployment_id="pdf::net",
            generation_id=None,
            tool_name="search_symbols",
            outcome="success",
            latency_ms=1.0,
        )
    )
    recorder.record(
        build_event(
            request_correlation_id="corr-2",
            deployment_id="pdf::net",
            generation_id=None,
            tool_name="search_symbols",
            outcome="success",
            latency_ms=1.0,
        )
    )
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=store,
        allowed_origins=[],
        usage_recorder=recorder,
    )
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.json() == {"usage_events_queued": 1, "usage_events_dropped": 1}


def test_mcp_transport_is_still_reachable_alongside_readyz(tmp_path: Path) -> None:
    """/readyz is mounted alongside the MCP app, never in place of it - the existing transport
    (and its Origin/protocol-version rejection) must be unaffected."""
    store = _store(tmp_path)
    with _client(store) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.0.1"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                # No Origin (allowed_origins is [] here; an absent Origin is never rejected on
                # its own) and a missing MCP-Protocol-Version is now correctly allowed through
                # too (TC-124) - a genuine client's very first initialize request never carries
                # it yet, so this must reach real dispatch rather than being rejected at 400.
            },
        )
        _assert_response_is_allowed(response)
