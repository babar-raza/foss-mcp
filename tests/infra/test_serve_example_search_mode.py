"""G3/TC-443 (REQ-G3-007): the example-search mode is stated where an operator and a peer will see it.

``find_examples`` searches verified examples lexically first, and its vector stage needs the embedding
gateway at run time. A standalone container without the gateway therefore searches examples lexically
only. ``infra/serve_http.py`` states the mode in one startup log record and in an ``X-Example-Search``
header on ``/readyz``, both from ``foss_mcp.mcp.tools.example_search_mode`` (the helper
``report_index_freshness`` also uses), and ``docs/RELEASE.md`` explains it.

Offline and deterministic: every test drives ``serve_http.build_app`` with a real
``GenerationManifestStore`` under ``tmp_path`` through Starlette's in-process ``TestClient``, exactly as
``tests/infra/test_serve_http.py`` does. Nothing opens a socket and nothing reads the wall clock.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.tools.example_search_mode import EXAMPLE_SEARCH_GATEWAY, EXAMPLE_SEARCH_LEXICAL_ONLY
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

# infra/ is not a package; import the module from its path, as tests/infra/test_serve_http.py does.
sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVE_HTTP_SOURCE = REPO_ROOT / "infra" / "serve_http.py"
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"

ENDPOINT = "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"
API_KEY = "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"
DIMENSION = "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION"
GATEWAY_VARIABLES = (ENDPOINT, API_KEY, DIMENSION)

DEPLOYMENT_CONFIG = DeploymentConfig(family="pdf", platform="net")
SCOPE = Scope(family="pdf", platform="net")
SCOPE_KEY = "pdf::net::self_extracted"
COMMIT = "10a363830fea952c29df20fdb37d35ad3a881433"

# The embedding provider the publisher uses is 8-dimensional, so the gateway mode needs the
# configured dimension to say 8.
STORED_DIMENSION = "8"

SYMBOL_CHUNK = f"FQN: Aspose.Pdf.Alpha\nKind: class_declaration\nSummary of Alpha.\nSource-Commit: {COMMIT}"
EXAMPLE_CHUNK = (
    "FQN: Example: sign a file\nKind: verified_example\n"
    f"Example:\ndoc.Sign(certificate, signature)\nSource-Commit: {COMMIT}"
)

# A value that must never appear in a header or a log record.
CANARY = "canary-4b1d7e90-do-not-leak"

EVENT = "example search mode"


@pytest.fixture(autouse=True)
def _gateway_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts as the standalone container does: no gateway variables."""
    for name in GATEWAY_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _configure_gateway(monkeypatch: pytest.MonkeyPatch, dimension: str = STORED_DIMENSION) -> None:
    monkeypatch.setenv(ENDPOINT, "https://gateway.invalid/v1")
    monkeypatch.setenv(API_KEY, "unit-test-key")
    monkeypatch.setenv(DIMENSION, dimension)


def _publish(store: GenerationManifestStore, texts: list[str]) -> None:
    """Publish one generation whose chunks have exactly *texts*."""
    body = "\n\n".join(f"## section {index}\n\n{text}" for index, text in enumerate(texts))
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit=COMMIT),
        evidence_refs=(),
        title="pdf/net synthetic",
        body=body,
    )
    chunks = chunk_document(doc)
    assert [chunk.text for chunk in chunks] == texts
    lease = store.acquire_lease(SCOPE_KEY, "worker-1", "pending")
    publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )


def _store(tmp_path: Path, texts: list[str] | None) -> GenerationManifestStore:
    """A store under *tmp_path*; with *texts* given, one generation is published into it."""
    store = GenerationManifestStore(tmp_path / "manifests")
    if texts is not None:
        _publish(store, texts)
    return store


def _client(store: GenerationManifestStore) -> TestClient:
    return TestClient(serve_http.build_app(DEPLOYMENT_CONFIG, manifest_store=store, allowed_origins=[]))


def _header(response) -> str:
    return response.headers[serve_http.EXAMPLE_SEARCH_HEADER]


def _startup_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == EVENT]


# --- the /readyz header -----------------------------------------------------------------------------


def test_the_header_name_is_pinned() -> None:
    assert serve_http.EXAMPLE_SEARCH_HEADER == "X-Example-Search"


def test_readyz_says_lexical_only_with_the_gateway_variables_unset(tmp_path: Path) -> None:
    with _client(_store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.text == "ready"
    assert response.headers["X-Example-Search"] == EXAMPLE_SEARCH_LEXICAL_ONLY == "lexical-only"


def test_readyz_says_gateway_with_the_variables_set_on_a_generation_with_an_example(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
    _configure_gateway(monkeypatch)
    with _client(store) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.text == "ready"
    assert response.headers["X-Example-Search"] == EXAMPLE_SEARCH_GATEWAY == "lexical+vector(gateway)"


def test_readyz_says_lexical_only_on_a_generation_without_an_example_even_with_the_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(tmp_path, [SYMBOL_CHUNK])
    _configure_gateway(monkeypatch)
    with _client(store) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.text == "ready"
    assert _header(response) == EXAMPLE_SEARCH_LEXICAL_ONLY


def test_readyz_503_for_an_empty_store_keeps_its_body_and_carries_the_header(tmp_path: Path) -> None:
    with _client(_store(tmp_path, None)) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.text == "not ready"
    assert _header(response) == EXAMPLE_SEARCH_LEXICAL_ONLY


def test_readyz_503_with_the_gateway_set_still_says_lexical_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No generation means no example vectors, whatever the environment says."""
    _configure_gateway(monkeypatch)
    with _client(_store(tmp_path, None)) as client:
        response = client.get("/readyz")
    assert (response.status_code, response.text) == (503, "not ready")
    assert _header(response) == EXAMPLE_SEARCH_LEXICAL_ONLY


def test_the_header_is_read_at_request_time_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
    with _client(store) as client:
        seen = [_header(client.get("/readyz"))]
        _configure_gateway(monkeypatch)
        seen.append(_header(client.get("/readyz")))
        monkeypatch.delenv(ENDPOINT)
        seen.append(_header(client.get("/readyz")))
    assert seen == [EXAMPLE_SEARCH_LEXICAL_ONLY, EXAMPLE_SEARCH_GATEWAY, EXAMPLE_SEARCH_LEXICAL_ONLY]


def test_the_header_follows_the_generation_active_at_request_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An app started before anything was published reports the generation that is active later."""
    _configure_gateway(monkeypatch)
    store = _store(tmp_path, None)
    with _client(store) as client:
        before = client.get("/readyz")
        _publish(store, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
        after = client.get("/readyz")
    assert (before.status_code, _header(before)) == (503, EXAMPLE_SEARCH_LEXICAL_ONLY)
    assert (after.status_code, _header(after)) == (200, EXAMPLE_SEARCH_GATEWAY)


def test_a_failure_computing_the_mode_never_changes_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
    _configure_gateway(monkeypatch)

    def explode(*args: object, **kwargs: object) -> tuple[str, str]:
        raise RuntimeError(f"boom {CANARY}")

    monkeypatch.setattr(serve_http, "example_search_mode", explode)
    with _client(store) as client:
        response = client.get("/readyz")
    assert (response.status_code, response.text) == (200, "ready")
    assert _header(response) == EXAMPLE_SEARCH_LEXICAL_ONLY
    assert CANARY not in json.dumps(dict(response.headers))


# --- the header agrees with report_index_freshness ----------------------------------------------------


@pytest.mark.parametrize("gateway", [False, True], ids=["gateway-unset", "gateway-set"])
@pytest.mark.parametrize(
    "texts",
    [[SYMBOL_CHUNK, EXAMPLE_CHUNK], [SYMBOL_CHUNK]],
    ids=["with-example", "without-example"],
)
def test_the_header_equals_the_freshness_reports_example_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, texts: list[str], gateway: bool
) -> None:
    store = _store(tmp_path, texts)
    if gateway:
        _configure_gateway(monkeypatch)
    report = report_index_freshness(store, SCOPE, "self_extracted", COMMIT)
    with _client(store) as client:
        response = client.get("/readyz")
    assert _header(response) == report.example_search


def test_the_header_equals_the_freshness_report_for_an_empty_store(tmp_path: Path) -> None:
    store = _store(tmp_path, None)
    report = report_index_freshness(store, SCOPE, "self_extracted", COMMIT)
    with _client(store) as client:
        response = client.get("/readyz")
    assert _header(response) == report.example_search == EXAMPLE_SEARCH_LEXICAL_ONLY


# --- the startup record -------------------------------------------------------------------------------


def test_the_startup_record_is_emitted_once_with_its_four_fields(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    store = _store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
    with caplog.at_level(logging.INFO):
        client = _client(store)
    records = _startup_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.INFO
    assert record.example_search == EXAMPLE_SEARCH_LEXICAL_ONLY  # type: ignore[attr-defined]
    assert record.example_search_reason == "embedding gateway not configured"  # type: ignore[attr-defined]
    assert record.family == "pdf"  # type: ignore[attr-defined]
    assert record.platform == "net"  # type: ignore[attr-defined]
    client.close()


def test_serving_requests_emits_no_further_startup_record(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    store = _store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
    with caplog.at_level(logging.INFO):
        with _client(store) as client:
            for _ in range(3):
                client.get("/readyz")
    assert len(_startup_records(caplog)) == 1


def test_the_startup_record_states_the_gateway_mode_when_configured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    store = _store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
    _configure_gateway(monkeypatch)
    with caplog.at_level(logging.INFO):
        client = _client(store)
    (record,) = _startup_records(caplog)
    assert record.example_search == EXAMPLE_SEARCH_GATEWAY  # type: ignore[attr-defined]
    assert record.example_search_reason  # type: ignore[attr-defined]
    client.close()


def test_the_startup_record_for_an_empty_store_says_no_active_generation(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    with caplog.at_level(logging.INFO):
        client = _client(_store(tmp_path, None))
    (record,) = _startup_records(caplog)
    assert record.example_search == EXAMPLE_SEARCH_LEXICAL_ONLY  # type: ignore[attr-defined]
    assert record.example_search_reason == "no active generation"  # type: ignore[attr-defined]
    client.close()


def test_the_startup_record_reaches_the_json_log_line(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """The four fields survive the container's JSON formatter as top-level keys."""
    store = _store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
    with caplog.at_level(logging.INFO):
        client = _client(store)
    (record,) = _startup_records(caplog)
    line = json.loads(serve_http.JsonLogFormatter().format(record))
    assert line["message"] == EVENT
    assert line["level"] == "INFO"
    assert {key: line[key] for key in ("example_search", "example_search_reason", "family", "platform")} == {
        "example_search": EXAMPLE_SEARCH_LEXICAL_ONLY,
        "example_search_reason": "embedding gateway not configured",
        "family": "pdf",
        "platform": "net",
    }
    client.close()


# --- nothing leaks ------------------------------------------------------------------------------------


@pytest.mark.parametrize("texts", [[SYMBOL_CHUNK, EXAMPLE_CHUNK], None], ids=["generation", "empty-store"])
def test_a_canary_in_the_gateway_variables_appears_in_no_header_and_no_log_record(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    texts: list[str] | None,
) -> None:
    store = _store(tmp_path, texts)
    monkeypatch.setenv(ENDPOINT, CANARY)
    monkeypatch.setenv(API_KEY, CANARY)
    monkeypatch.setenv(DIMENSION, CANARY)
    with caplog.at_level(logging.DEBUG):
        with _client(store) as client:
            response = client.get("/readyz")
    formatter = serve_http.JsonLogFormatter()
    assert CANARY not in json.dumps(dict(response.headers))
    assert CANARY not in response.text
    assert all(CANARY not in formatter.format(record) for record in caplog.records)
    assert CANARY not in caplog.text


def test_a_canary_with_a_matching_dimension_still_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    store = _store(tmp_path, [SYMBOL_CHUNK, EXAMPLE_CHUNK])
    monkeypatch.setenv(ENDPOINT, CANARY)
    monkeypatch.setenv(API_KEY, CANARY)
    monkeypatch.setenv(DIMENSION, STORED_DIMENSION)
    with caplog.at_level(logging.DEBUG):
        with _client(store) as client:
            response = client.get("/readyz")
    assert _header(response) == EXAMPLE_SEARCH_GATEWAY
    assert CANARY not in json.dumps(dict(response.headers))
    assert CANARY not in caplog.text


# --- docs/RELEASE.md ----------------------------------------------------------------------------------


def _doc() -> str:
    return RELEASE_DOC.read_text(encoding="utf-8")


def _section_8() -> str:
    lines = _doc().splitlines()
    start = lines.index("## 8. Known limitations")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if re.match(r"## (?!#)", line):
            break
        body.append(line)
    return "\n".join(body)


def _subsection() -> str:
    lines = _section_8().splitlines()
    start = lines.index("### Example search without the embedding gateway")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("### "):
            break
        body.append(line)
    return "\n".join(body)


def test_release_md_has_the_section_and_the_subsection_headings_once() -> None:
    lines = _doc().splitlines()
    assert lines.count("## 8. Known limitations") == 1
    assert lines.count("### Example search without the embedding gateway") == 1


def test_the_example_search_subsection_is_the_first_one_in_section_8() -> None:
    subheadings = [line for line in _section_8().splitlines() if line.startswith("### ")]
    assert subheadings[0] == "### Example search without the embedding gateway"


def test_release_md_names_both_modes_and_both_variables_in_the_subsection() -> None:
    text = _subsection()
    for needle in ("lexical-only", "lexical+vector(gateway)", ENDPOINT, API_KEY):
        assert needle in text, f"the subsection does not name {needle}"


def test_release_md_says_where_to_see_the_mode() -> None:
    text = _subsection()
    for needle in ("report_index_freshness", "example_search", "X-Example-Search", "example search mode"):
        assert needle in text, f"the subsection does not name {needle}"


def test_release_md_explains_why_there_is_no_offline_fallback() -> None:
    flat = " ".join(_subsection().split())
    assert "4096-dimension" in flat
    assert "no offline fallback" in flat
    assert "0.507" in flat  # the measured score of an unrelated query


def test_the_subsection_names_variables_only_never_values_or_hosts() -> None:
    text = _subsection()
    assert not re.search(r"https?://", text)
    assert not re.search(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*\S", text)


# --- wiring: the helper is called by the real entrypoint -----------------------------------------------


def test_serve_http_imports_the_shared_helper() -> None:
    source = SERVE_HTTP_SOURCE.read_text(encoding="utf-8")
    assert re.search(
        r"^from foss_mcp\.mcp\.tools\.example_search_mode import .*\bexample_search_mode\b", source, re.M
    )


def test_serve_http_calls_the_helper_and_sets_the_header_on_the_readyz_route() -> None:
    source = SERVE_HTTP_SOURCE.read_text(encoding="utf-8")
    assert "example_search_mode(" in source
    assert source.count("EXAMPLE_SEARCH_HEADER") >= 2  # the definition and the use in the route
