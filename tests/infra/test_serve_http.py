"""``/readyz`` reflects the real active-generation state of the real manifest store, never
process reachability - the confirmed reference-system defect (health.py's own docstring, and
the 2026-09-25 audit that found ``is_ready``/``round_trip_check`` had zero call sites outside
their own tests) this card closes.

Both tests drive ``serve_http.build_app``'s real ASGI app with a real
``GenerationManifestStore`` rooted at ``tmp_path`` - never a mock - through Starlette's
``TestClient``, the same in-process pattern ``tests/mcp/test_server_wiring.py`` already uses.
The "ready" case publishes a real generation through ``foss_mcp.indexing.publisher.publish_generation``
with real ``Chunk`` objects and the real ``DeterministicEmbeddingProvider`` from
``tests.indexing.test_index_writers`` - never a hand-built payload - so the assertion is
actually exercising the same path a live ingest would.
"""

from __future__ import annotations

import sys
from pathlib import Path

from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance
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


def test_mcp_transport_is_still_reachable_alongside_readyz(tmp_path: Path) -> None:
    """/readyz is mounted alongside the MCP app, never in place of it - the existing transport
    (and its Origin/protocol-version rejection) must be unaffected."""
    store = _store(tmp_path)
    with _client(store) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                # No Origin (allowed_origins is [] here; an absent Origin is never rejected on
                # its own) but a missing MCP-Protocol-Version IS rejected by the transport.
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "rejected"
