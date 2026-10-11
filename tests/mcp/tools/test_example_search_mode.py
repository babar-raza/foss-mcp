"""G3/TC-444: the example-search mode of a generation is decided from its payload and the gateway
configuration, and report_index_freshness states it.

Offline and deterministic. Synthetic generations are published through the real
``publish_generation``; the real-data tests read the committed ``infra/demo-manifests`` read-only.
Nothing here asserts on wall-clock time and no test is skipped.
"""

from __future__ import annotations

import ast
import json
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

import foss_mcp.mcp.tools.find_examples as find_examples_module
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.example_search_mode import (
    EXAMPLE_SEARCH_GATEWAY,
    EXAMPLE_SEARCH_LEXICAL_ONLY,
    example_search_mode,
)
from foss_mcp.mcp.tools.find_examples import ExampleMatch, NoExampleFound, find_examples
from foss_mcp.mcp.tools.report_index_freshness import FreshnessReport, report_index_freshness
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider
from tests.mcp.test_server_wiring import ALLOWED_ORIGINS, _McpSession, _sse_json, serve_http

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
VALUES_YAML = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
FRESHNESS_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "report_index_freshness.py"

ENDPOINT = "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"
API_KEY = "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"
DIMENSION = "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION"
GATEWAY_VARIABLES = (ENDPOINT, API_KEY, DIMENSION)

NO_VECTORS = "no example vectors in this generation"
NOT_CONFIGURED = "embedding gateway not configured"
DIM_MISMATCH = "configured embedding dimension does not match the stored vectors"

SCOPE = Scope(family="pdf", platform="net")
COMMIT = "10a363830fea952c29df20fdb37d35ad3a881433"

# A value that must never show up anywhere in a reason or in a serialized report.
CANARY = "canary-7f3a9c1e-do-not-leak"


@pytest.fixture(autouse=True)
def _gateway_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with the standalone default; a test that wants the gateway sets it."""
    for name in GATEWAY_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _configure(monkeypatch: pytest.MonkeyPatch, *, dimension: str | None = "4096") -> None:
    monkeypatch.setenv(ENDPOINT, "https://gateway.invalid/v1")
    monkeypatch.setenv(API_KEY, "unit-test-key")
    if dimension is None:
        monkeypatch.delenv(DIMENSION, raising=False)
    else:
        monkeypatch.setenv(DIMENSION, dimension)


def _payload(point_texts: list[str], dimension: object = 4096) -> dict:
    return {
        "vector_index": {
            "dimension": dimension,
            "points": [
                {"point_id": f"p{i}", "chunk_id": f"c{i}", "vector": [1.0], "text": t}
                for i, t in enumerate(point_texts)
            ],
        }
    }


EXAMPLE_TEXT = "FQN: Example: sign a file\nExample: doc.Sign()"
SYMBOL_TEXT = "FQN: Aspose.Pdf.Document\nKind: class_declaration"


def _env(
    endpoint: str | None = "https://gw.invalid", key: str | None = "k", dimension: str | None = None
) -> dict[str, str]:
    env: dict[str, str] = {}
    if endpoint is not None:
        env[ENDPOINT] = endpoint
    if key is not None:
        env[API_KEY] = key
    if dimension is not None:
        env[DIMENSION] = dimension
    return env


# --- the helper, branch by branch ---------------------------------------------------------------


def test_constants_have_the_pinned_values() -> None:
    assert EXAMPLE_SEARCH_GATEWAY == "lexical+vector(gateway)"
    assert EXAMPLE_SEARCH_LEXICAL_ONLY == "lexical-only"


def test_gateway_mode_when_examples_configured_and_dimension_matches() -> None:
    mode, reason = example_search_mode(_payload([EXAMPLE_TEXT]), _env(dimension="4096"))
    assert mode == EXAMPLE_SEARCH_GATEWAY
    assert reason  # a short reason is always given


def test_gateway_mode_with_the_default_dimension_when_the_variable_is_unset() -> None:
    mode, _ = example_search_mode(_payload([EXAMPLE_TEXT], 4096), _env())
    assert mode == EXAMPLE_SEARCH_GATEWAY


def test_gateway_mode_with_a_blank_dimension_variable_uses_the_default() -> None:
    mode, _ = example_search_mode(_payload([EXAMPLE_TEXT], 4096), _env(dimension="   "))
    assert mode == EXAMPLE_SEARCH_GATEWAY


def test_gateway_mode_with_a_non_default_matching_dimension() -> None:
    mode, _ = example_search_mode(_payload([EXAMPLE_TEXT], 8), _env(dimension="8"))
    assert mode == EXAMPLE_SEARCH_GATEWAY


def test_gateway_mode_when_only_one_of_many_points_is_an_example() -> None:
    payload = _payload([SYMBOL_TEXT, SYMBOL_TEXT, EXAMPLE_TEXT])
    assert example_search_mode(payload, _env())[0] == EXAMPLE_SEARCH_GATEWAY


def test_gateway_mode_tolerates_whitespace_around_the_dimension() -> None:
    assert (
        example_search_mode(_payload([EXAMPLE_TEXT], 8), _env(dimension=" 8 \n"))[0] == EXAMPLE_SEARCH_GATEWAY
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"vector_index": None},
        {"vector_index": {}},
        {"vector_index": {"dimension": 4096}},
        {"vector_index": {"dimension": 4096, "points": []}},
        {"vector_index": {"dimension": 4096, "points": None}},
        _payload([SYMBOL_TEXT]),
        _payload([SYMBOL_TEXT, "FQN: Doc: Install\nDocumentation page."]),
        {"lexical_index": {"documents": {"d": {"text": EXAMPLE_TEXT}}}},
    ],
    ids=[
        "empty-payload",
        "vector-index-none",
        "vector-index-empty",
        "no-points-key",
        "empty-points",
        "points-none",
        "symbol-only",
        "symbol-and-doc-only",
        "examples-only-in-lexical-index",
    ],
)
def test_no_example_vectors_is_lexical_only_even_when_the_gateway_is_configured(payload: dict) -> None:
    assert example_search_mode(payload, _env(dimension="4096")) == (EXAMPLE_SEARCH_LEXICAL_ONLY, NO_VECTORS)


def test_no_example_vectors_wins_over_a_missing_gateway() -> None:
    assert example_search_mode(_payload([SYMBOL_TEXT]), {}) == (EXAMPLE_SEARCH_LEXICAL_ONLY, NO_VECTORS)


def test_no_example_vectors_wins_over_a_dimension_mismatch() -> None:
    assert example_search_mode(_payload([SYMBOL_TEXT], 8), _env(dimension="4096"))[1] == NO_VECTORS


@pytest.mark.parametrize(
    "environment",
    [
        {},
        _env(endpoint=None),
        _env(key=None),
        _env(endpoint=""),
        _env(key=""),
        _env(endpoint="   "),
        _env(key="\t\n "),
        _env(endpoint=None, key=None),
        _env(endpoint="   ", key="   "),
    ],
    ids=[
        "nothing-set",
        "endpoint-missing",
        "key-missing",
        "endpoint-empty",
        "key-empty",
        "endpoint-blank",
        "key-blank",
        "both-missing",
        "both-blank",
    ],
)
def test_an_unset_or_blank_endpoint_or_key_is_not_configured(environment: dict[str, str]) -> None:
    assert example_search_mode(_payload([EXAMPLE_TEXT]), environment) == (
        EXAMPLE_SEARCH_LEXICAL_ONLY,
        NOT_CONFIGURED,
    )


def test_not_configured_wins_over_a_dimension_mismatch() -> None:
    environment = _env(endpoint=None, dimension="7")
    assert example_search_mode(_payload([EXAMPLE_TEXT], 4096), environment)[1] == NOT_CONFIGURED


@pytest.mark.parametrize("dimension", ["abc", "4096.0", "0", "-4096", "12 34", "0x1000", "1e3"])
def test_a_non_integer_or_non_positive_dimension_is_lexical_only(dimension: str) -> None:
    assert example_search_mode(_payload([EXAMPLE_TEXT], 4096), _env(dimension=dimension)) == (
        EXAMPLE_SEARCH_LEXICAL_ONLY,
        DIM_MISMATCH,
    )


@pytest.mark.parametrize(("configured", "stored"), [("8", 4096), ("4096", 8), ("4095", 4096), ("4097", 4096)])
def test_a_dimension_that_differs_from_the_stored_one_is_lexical_only(configured: str, stored: int) -> None:
    assert example_search_mode(_payload([EXAMPLE_TEXT], stored), _env(dimension=configured)) == (
        EXAMPLE_SEARCH_LEXICAL_ONLY,
        DIM_MISMATCH,
    )


def test_the_default_dimension_does_not_match_a_non_default_stored_dimension() -> None:
    assert example_search_mode(_payload([EXAMPLE_TEXT], 8), _env())[1] == DIM_MISMATCH


@pytest.mark.parametrize(
    "stored", [None, "4096", 4096.5, True, [4096]], ids=["none", "string", "float", "bool", "list"]
)
def test_an_absent_or_malformed_stored_dimension_is_lexical_only(stored: object) -> None:
    payload = _payload([EXAMPLE_TEXT], stored)
    assert example_search_mode(payload, _env(dimension="1")) == (EXAMPLE_SEARCH_LEXICAL_ONLY, DIM_MISMATCH)
    assert example_search_mode(payload, _env())[0] == EXAMPLE_SEARCH_LEXICAL_ONLY


def test_the_environment_defaults_to_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload([EXAMPLE_TEXT], 4096)
    assert example_search_mode(payload) == (EXAMPLE_SEARCH_LEXICAL_ONLY, NOT_CONFIGURED)
    _configure(monkeypatch, dimension="4096")
    assert example_search_mode(payload)[0] == EXAMPLE_SEARCH_GATEWAY
    monkeypatch.setenv(DIMENSION, "9")
    assert example_search_mode(payload) == (EXAMPLE_SEARCH_LEXICAL_ONLY, DIM_MISMATCH)


def test_an_explicit_environment_ignores_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, dimension="4096")
    assert example_search_mode(_payload([EXAMPLE_TEXT]), {}) == (EXAMPLE_SEARCH_LEXICAL_ONLY, NOT_CONFIGURED)


def test_the_helper_does_not_mutate_its_arguments() -> None:
    payload = _payload([EXAMPLE_TEXT])
    environment = _env(dimension="4096")
    payload_before, environment_before = json.dumps(payload), dict(environment)
    example_search_mode(payload, environment)
    assert json.dumps(payload) == payload_before
    assert environment == environment_before


def test_the_only_reasons_the_lexical_only_mode_gives_are_the_three_pinned_ones() -> None:
    reasons = {
        example_search_mode({}, {})[1],
        example_search_mode(_payload([EXAMPLE_TEXT]), {})[1],
        example_search_mode(_payload([EXAMPLE_TEXT], 8), _env())[1],
    }
    assert reasons == {NO_VECTORS, NOT_CONFIGURED, DIM_MISMATCH}


# --- no environment value ever leaks ------------------------------------------------------------


@pytest.mark.parametrize(
    "environment",
    [
        _env(endpoint=CANARY, key=CANARY, dimension=CANARY),
        _env(endpoint=CANARY, key=CANARY, dimension="8"),
        _env(endpoint=CANARY, key=None, dimension=CANARY),
        _env(endpoint=None, key=CANARY, dimension=CANARY),
        _env(endpoint=CANARY, key=CANARY, dimension="4096"),
    ],
    ids=[
        "all-canary",
        "canary-endpoint-key",
        "canary-endpoint-dimension",
        "canary-key-dimension",
        "valid-dimension",
    ],
)
@pytest.mark.parametrize("payload", [{}, _payload([EXAMPLE_TEXT], 4096), _payload([EXAMPLE_TEXT], 8)])
def test_a_canary_in_any_gateway_variable_never_appears_in_the_mode_or_reason(
    environment: dict[str, str], payload: dict
) -> None:
    mode, reason = example_search_mode(payload, environment)
    assert CANARY not in mode
    assert CANARY not in reason


def test_a_canary_never_appears_in_a_serialized_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK])
    for dimension in (CANARY, "8", "4096"):
        monkeypatch.setenv(ENDPOINT, CANARY)
        monkeypatch.setenv(API_KEY, CANARY)
        monkeypatch.setenv(DIMENSION, dimension)
        report = report_index_freshness(store, SCOPE, "self_extracted", COMMIT)
        assert CANARY not in json.dumps(asdict(report), default=str)


# --- synthetic generations ----------------------------------------------------------------------

SYMBOL_TEXT_WITH_COMMIT = (
    f"FQN: Aspose.Pdf.Alpha\nKind: class_declaration\nSummary of Alpha.\nSource-Commit: {COMMIT}"
)
# TC-465: a real verified example - the FQN line starts "Example: ", the verifier's own
# "Kind: verified_example" line, a line exactly "Example:", then the code.
EXAMPLE_CHUNK = (
    "FQN: Example: sign a file\nKind: verified_example\n"
    f"Example:\ndoc.Sign(certificate, signature)\nSource-Commit: {COMMIT}"
)


def _publish(root: Path, texts: list[str]) -> GenerationManifestStore:
    """Publish one generation whose chunks have exactly *texts*, embedded 8-dimensionally."""
    store = GenerationManifestStore(root)
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
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
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
    return store


def _report(store: GenerationManifestStore) -> FreshnessReport:
    return report_index_freshness(store, SCOPE, "self_extracted", COMMIT)


def test_report_has_the_additive_fields_with_their_defaults() -> None:
    report = FreshnessReport(SCOPE, "self_extracted", None, None, None, True, "never published")
    assert report.example_search == "lexical-only"
    assert report.example_search_reason == "no active generation"


def test_report_for_a_never_published_scope_says_no_active_generation(tmp_path: Path) -> None:
    report = _report(GenerationManifestStore(tmp_path / "empty"))
    assert report.reason == "never published"
    assert report.example_search == EXAMPLE_SEARCH_LEXICAL_ONLY
    assert report.example_search_reason == "no active generation"


def test_report_is_lexical_only_with_the_gateway_variables_unset(tmp_path: Path) -> None:
    report = _report(_publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK]))
    assert report.example_search == EXAMPLE_SEARCH_LEXICAL_ONLY
    assert report.example_search_reason == NOT_CONFIGURED


def test_report_is_the_gateway_mode_with_the_variables_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK])
    _configure(monkeypatch, dimension="8")
    report = _report(store)
    assert report.example_search == EXAMPLE_SEARCH_GATEWAY
    assert report.example_search_reason


def test_both_modes_are_reachable_on_one_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK])
    seen = {_report(store).example_search}
    _configure(monkeypatch, dimension="8")
    seen.add(_report(store).example_search)
    monkeypatch.delenv(ENDPOINT)
    seen.add(_report(store).example_search)
    assert seen == {EXAMPLE_SEARCH_GATEWAY, EXAMPLE_SEARCH_LEXICAL_ONLY}


def test_report_with_the_default_dimension_does_not_match_an_eight_dimensional_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK])
    _configure(monkeypatch, dimension=None)
    report = _report(store)
    assert (report.example_search, report.example_search_reason) == (
        EXAMPLE_SEARCH_LEXICAL_ONLY,
        DIM_MISMATCH,
    )


def test_report_for_a_generation_without_examples_says_no_example_vectors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT])
    _configure(monkeypatch, dimension="8")
    report = _report(store)
    assert (report.example_search, report.example_search_reason) == (EXAMPLE_SEARCH_LEXICAL_ONLY, NO_VECTORS)


def test_the_existing_report_fields_and_reason_are_unchanged(tmp_path: Path) -> None:
    report = _report(_publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK]))
    assert report.stale is False
    assert report.reason == "index matches the current source commit"
    assert report.indexed_source_commit == COMMIT
    assert report.chunks_without_commit == 0
    assert sum(entry.chunks for entry in report.source_commits) == 2


# --- consistency with what find_examples really does --------------------------------------------

MISSING_QUERY = "zzqq wombat tangerine"


class _OwnVectorProvider:
    """A fake gateway whose query embedding is exactly the stored example point's own vector."""

    vector: tuple[float, ...] = ()

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [self.vector for _ in texts]


def _example_point_vector(store: GenerationManifestStore) -> tuple[float, ...]:
    key = "pdf::net::self_extracted"
    manifest = store.read_generation(key, store.read_active(key) or "")
    points = [p for p in manifest.payload["vector_index"]["points"] if "Example:" in p["text"]]
    assert len(points) == 1
    return tuple(points[0]["vector"])


def test_with_the_variables_unset_find_examples_misses_and_the_report_says_lexical_only(
    tmp_path: Path,
) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK])
    assert isinstance(find_examples(store, SCOPE, MISSING_QUERY), NoExampleFound)
    assert _report(store).example_search == EXAMPLE_SEARCH_LEXICAL_ONLY


def test_with_the_variables_set_find_examples_matches_by_vector_and_the_report_says_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK])
    _configure(monkeypatch, dimension="8")
    provider = type("Provider", (_OwnVectorProvider,), {"vector": _example_point_vector(store)})
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", provider)

    result = find_examples(store, SCOPE, MISSING_QUERY)

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], ExampleMatch)
    assert result[0].source == "vector"
    assert _report(store).example_search == EXAMPLE_SEARCH_GATEWAY


def test_the_lexical_stage_still_answers_without_the_gateway_in_both_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK])
    query = "certificate signature"
    first = find_examples(store, SCOPE, query)
    assert isinstance(first, list)
    assert first[0].source == "lexical"
    assert _report(store).example_search == EXAMPLE_SEARCH_LEXICAL_ONLY
    _configure(monkeypatch, dimension="8")
    second = find_examples(store, SCOPE, query)
    assert isinstance(second, list)
    assert second[0].source == "lexical"


def test_when_the_report_says_lexical_only_for_a_generation_without_examples_find_examples_cannot_use_vectors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_TEXT_WITH_COMMIT])
    _configure(monkeypatch, dimension="8")
    assert _report(store).example_search == EXAMPLE_SEARCH_LEXICAL_ONLY
    assert isinstance(find_examples(store, SCOPE, MISSING_QUERY), NoExampleFound)


# --- through the real MCP server ----------------------------------------------------------------


def test_the_served_report_index_freshness_result_contains_example_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from starlette.testclient import TestClient

    from foss_mcp.mcp.routing import DeploymentConfig

    def call() -> dict:
        store = GenerationManifestStore(tmp_path / "manifests")
        app = serve_http.build_app(
            DeploymentConfig(family="pdf", platform="net"),
            manifest_store=store,
            allowed_origins=ALLOWED_ORIGINS,
        )
        with TestClient(app) as client:
            session = _McpSession(client)
            response = session.request(
                "tools/call",
                {"name": "report_index_freshness", "arguments": {"current_source_commit": COMMIT}},
            )
            assert response.status_code == 200
            result = _sse_json(response.text)["result"]
            assert result["isError"] is False
            assert json.loads(result["content"][0]["text"]) == result["structuredContent"]["result"]
            return result["structuredContent"]["result"]

    _publish(tmp_path / "manifests", [SYMBOL_TEXT_WITH_COMMIT, EXAMPLE_CHUNK])
    unset = call()
    assert unset["example_search"] == EXAMPLE_SEARCH_LEXICAL_ONLY
    assert unset["example_search_reason"] == NOT_CONFIGURED

    _configure(monkeypatch, dimension="8")
    configured = call()
    assert configured["example_search"] == EXAMPLE_SEARCH_GATEWAY
    assert configured["example_search_reason"]


# --- wiring on committed source -----------------------------------------------------------------


def test_report_index_freshness_imports_and_calls_example_search_mode() -> None:
    tree = ast.parse(FRESHNESS_SOURCE.read_text(encoding="utf-8"))
    imported = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("foss_mcp.mcp.tools.example_search_mode", "example_search_mode") in imported
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "example_search_mode" in called


def test_the_helper_takes_its_variable_names_from_the_gateway_provider_not_retyped() -> None:
    source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "example_search_mode.py").read_text(
        encoding="utf-8"
    )
    assert "FOSS_MCP_EMBEDDING_GATEWAY" not in source.split('"""', 2)[2]
    assert "def example_search_mode(" in source


def test_the_helper_reads_the_same_variable_names_as_the_gateway_provider() -> None:
    from foss_mcp.indexing import gateway_embedding_provider as provider
    from foss_mcp.mcp.tools import example_search_mode as helper

    assert helper._ENDPOINT_ENV == provider._ENDPOINT_ENV == ENDPOINT
    assert helper._API_KEY_ENV == provider._API_KEY_ENV == API_KEY
    assert helper._DIMENSION_ENV == provider._DIMENSION_ENV == DIMENSION
    assert helper._DEFAULT_DIMENSION == provider._DEFAULT_DIMENSION == 4096


# --- the 40 committed pilots --------------------------------------------------------------------


def _pilots() -> list[tuple[str, str, str]]:
    values = yaml.safe_load(VALUES_YAML.read_text(encoding="utf-8"))
    return [(p["family"], p["platform"], p["sourceKind"]) for p in values["ingestion"]["pilots"]]


PILOTS = _pilots()
PILOT_IDS = [f"{family}-{platform}" for family, platform, _ in PILOTS]


def _demo_payload(family: str, platform: str, source_kind: str) -> dict:
    store = GenerationManifestStore(DEMO_MANIFESTS)
    key = f"{family}::{platform}::{source_kind}"
    generation_id = store.read_active(key)
    assert generation_id is not None
    return store.read_generation(key, generation_id).payload


def _example_point_count(payload: dict) -> int:
    points = (payload.get("vector_index") or {}).get("points") or []
    return sum("Example:" in point["text"] for point in points)


def test_there_are_forty_pilots() -> None:
    assert len(PILOTS) == 40
    assert len(set(PILOTS)) == 40


def test_the_committed_demo_generations_include_pilots_with_and_without_example_points() -> None:
    counts = [_example_point_count(_demo_payload(*pilot)) for pilot in PILOTS]
    assert any(count > 0 for count in counts)
    assert any(count == 0 for count in counts)


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_a_committed_pilot_generation_is_lexical_only_without_the_gateway(
    pilot: tuple[str, str, str],
) -> None:
    family, platform, source_kind = pilot
    report = report_index_freshness(
        GenerationManifestStore(DEMO_MANIFESTS), Scope(family=family, platform=platform), source_kind, None
    )
    assert report.indexed_generation_id is not None
    assert report.example_search == EXAMPLE_SEARCH_LEXICAL_ONLY
    expected = NO_VECTORS if _example_point_count(_demo_payload(*pilot)) == 0 else NOT_CONFIGURED
    assert report.example_search_reason == expected


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_a_committed_pilot_generation_reports_the_gateway_mode_exactly_when_it_has_example_points(
    monkeypatch: pytest.MonkeyPatch, pilot: tuple[str, str, str]
) -> None:
    family, platform, source_kind = pilot
    _configure(monkeypatch, dimension="4096")
    report = report_index_freshness(
        GenerationManifestStore(DEMO_MANIFESTS), Scope(family=family, platform=platform), source_kind, None
    )
    if _example_point_count(_demo_payload(*pilot)) > 0:
        assert report.example_search == EXAMPLE_SEARCH_GATEWAY
    else:
        assert (report.example_search, report.example_search_reason) == (
            EXAMPLE_SEARCH_LEXICAL_ONLY,
            NO_VECTORS,
        )


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_a_committed_pilot_generation_stores_the_default_gateway_dimension(
    pilot: tuple[str, str, str],
) -> None:
    payload = _demo_payload(*pilot)
    assert (payload.get("vector_index") or {}).get("dimension") == 4096
