"""G3/TC-538 (REQ-G3-008, REQ-G3-001): no e2e test may pin the retired plain-text ``/readyz`` body.

TC-506 changed ``/readyz`` from the plain text ``ready`` to a JSON object. Seven live-content e2e tests
kept asserting the old body and nothing offline noticed. This module pins the contract three ways:
the seven named e2e files assert the JSON shape, no file under ``tests/e2e`` may contain the retired
plain-text assertion, and the real ``serve_http.build_app`` really returns the shape those tests assert.

Offline and deterministic: file-text checks plus one in-process Starlette ``TestClient``; no docker.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.normalization.document_schema import Provenance
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider
from tests.infra.readiness_support import ready_chunks

REPO_ROOT = Path(__file__).parents[2]
E2E_DIR = REPO_ROOT / "tests" / "e2e"
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402

READYZ_E2E_FILES = (
    "test_pdf_go_live_content.py",
    "test_pdf_typescript_live_content.py",
    "test_pdf_net_live_content.py",
    "test_pdf_java_live_content.py",
    "test_pdf_cpp_live_content.py",
    "test_cells_rust_live_content.py",
    "test_slides_python_live_content.py",
)
RETIRED_DOUBLE = '.text == "ready"'
RETIRED_SINGLE = ".text == 'ready'"
JSON_ASSERTION = 'body["ready"] is True'
PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", READYZ_E2E_FILES)
def test_the_named_e2e_file_does_not_pin_the_plain_text_body(name: str) -> None:
    text = _text(E2E_DIR / name)
    assert RETIRED_DOUBLE not in text
    assert RETIRED_SINGLE not in text


@pytest.mark.parametrize("name", READYZ_E2E_FILES)
def test_the_named_e2e_file_asserts_the_json_readiness_contract(name: str) -> None:
    text = _text(E2E_DIR / name)
    assert JSON_ASSERTION in text
    assert 'body["reasons"] == []' in text


def test_no_e2e_file_reintroduces_the_plain_text_readyz_contract() -> None:
    files = sorted(E2E_DIR.glob("*.py"))
    assert len(files) >= len(READYZ_E2E_FILES)
    offenders = [path.name for path in files if RETIRED_DOUBLE in _text(path)]
    assert offenders == []


def test_the_real_readyz_returns_the_json_shape_the_e2e_tests_assert(tmp_path: Path) -> None:
    store = GenerationManifestStore(tmp_path / "manifests")
    lease = store.acquire_lease("pdf::net::self_extracted", held_by="test-worker", generation_id="pending")
    publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=ready_chunks(PROVENANCE),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"), manifest_store=store, allowed_origins=[]
    )
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["reasons"] == []
