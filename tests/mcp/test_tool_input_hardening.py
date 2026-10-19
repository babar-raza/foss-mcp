"""TC-380: every tool's input model is strict, closed, bounded and normalised.

Driven through the REAL served call path (``infra/serve_http.build_app`` over
``starlette.testclient.TestClient``, exactly as ``tests/mcp/test_server_wiring.py`` does), over
an in-memory-style store under ``tmp_path`` holding one small published generation. A bad
argument must always be a clean ``invalid_argument`` that echoes nothing large and never
touches the filesystem (the QA run found ``source_kind`` reaching ``mkdir``).
"""

from __future__ import annotations

import json
import os
import sys
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import (
    _INPUT_MODELS,
    GetProductReferenceInput,
    LookupInput,
    ReportIndexFreshnessInput,
    SearchDocsInput,
    public_validation_error_message,
)
from foss_mcp.mcp.tools.get_product_reference import SECTIONS
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

VALID_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Origin": "https://example.com",
    "MCP-Protocol-Version": "2025-06-18",
}
ALLOWED_ORIGINS = ["https://example.com"]

SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
FQN = "Aspose.Pdf.Document"

VALID_ARGUMENTS = {
    "lookup": {"query": "Document"},
    "search_docs": {"query": "install", "content_type": "developer_guide"},
    "search_symbols": {"query": "Document"},
    "get_symbol": {"fqn": FQN},
    "list_members": {"fqn": FQN},
    "find_examples": {"query": "Document"},
    "get_product_reference": {"section": "license"},
    "list_recent_changes": {},
    "report_index_freshness": {},
}

QA_TRAVERSAL_SOURCE_KINDS = [
    "x/../slides__python__self_extracted",
    "../../../../tmp/qa_probe_dir",
    "self_extracted/../../manifests/cells__rust__self_extracted",
    "x" + chr(92) + ".." + chr(92) + "slides__python__self_extracted",
    "a" * 300,
    "",
]


def _publish(store: GenerationManifestStore) -> None:
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="https://example.invalid/repo", commit=SOURCE_COMMIT),
        evidence_refs=(),
        title="pdf/net API surface",
        body=f"## {FQN}\n\nFQN: {FQN}\nKind: class\nSource-Commit: {SOURCE_COMMIT}",
    )
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunk_document(doc),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )


@pytest.fixture
def store(tmp_path: Path) -> GenerationManifestStore:
    manifest_store = GenerationManifestStore(tmp_path / "manifests")
    _publish(manifest_store)
    return manifest_store


class _Client:
    """One initialised MCP session over the real ASGI app."""

    def __init__(self, client: TestClient) -> None:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "hardening-test", "version": "0.0.1"},
                },
            },
            headers=VALID_HEADERS,
        )
        assert response.status_code == 200, response.text
        self._client = client
        self._headers = dict(VALID_HEADERS, **{"mcp-session-id": response.headers["mcp-session-id"]})
        client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=self._headers
        )

    def call(self, name: str, arguments: dict) -> tuple[dict, str]:
        """Returns (the JSON-RPC ``result`` object, the raw HTTP response text)."""
        response = self._client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=self._headers,
        )
        assert response.status_code == 200, response.text
        for line in response.text.splitlines():
            if line.startswith("data:"):
                body = json.loads(line[len("data:") :].strip())
                assert "result" in body, body
                return body["result"], response.text
        raise AssertionError(f"no SSE data line in {response.text!r}")


@pytest.fixture
def client(store: GenerationManifestStore):
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="net"),
        manifest_store=store,
        allowed_origins=ALLOWED_ORIGINS,
    )
    with TestClient(app) as test_client:
        yield _Client(test_client)


def _assert_invalid_argument(result: dict) -> str:
    assert result["isError"] is True, result
    assert result["structuredContent"]["code"] == "invalid_argument", result
    return result["content"][0]["text"]


def _listing(store: GenerationManifestStore) -> list[str]:
    return sorted(os.listdir(store.root))


# --------------------------------------------------------------------- traversal / closed sets


@pytest.mark.parametrize("source_kind", QA_TRAVERSAL_SOURCE_KINDS)
def test_a_traversal_source_kind_is_invalid_argument_and_creates_no_directory(
    client: _Client, store: GenerationManifestStore, source_kind: str
) -> None:
    before = _listing(store)
    result, _ = client.call("report_index_freshness", {"source_kind": source_kind})
    _assert_invalid_argument(result)
    assert _listing(store) == before == ["pdf__net__self_extracted"]


def test_a_traversal_source_kind_does_not_reach_another_pilots_generation(
    client: _Client,
) -> None:
    result, _ = client.call("report_index_freshness", {"source_kind": "x/../slides__python__self_extracted"})
    _assert_invalid_argument(result)
    assert "structuredContent" in result and "result" not in result["structuredContent"]


@pytest.mark.parametrize("source_kind", ["totally_new_kind", "SELF_EXTRACTED", " self_extracted"])
def test_an_unknown_source_kind_is_invalid_argument_and_creates_nothing(
    client: _Client, store: GenerationManifestStore, source_kind: str
) -> None:
    before = _listing(store)
    result, _ = client.call("report_index_freshness", {"source_kind": source_kind})
    _assert_invalid_argument(result)
    assert _listing(store) == before


@pytest.mark.parametrize("kind", [member.value for member in SourceKind])
def test_every_real_source_kind_is_accepted_and_reads_without_creating_anything(
    client: _Client, store: GenerationManifestStore, kind: str
) -> None:
    before = _listing(store)
    result, _ = client.call("report_index_freshness", {"source_kind": kind})
    assert result["isError"] is False, result
    assert _listing(store) == before


def test_the_closed_sets_are_derived_from_the_tools_own_constants() -> None:
    def literal_members(model: type, field: str) -> set[str]:
        annotation = model.model_fields[field].annotation
        members: set[str] = set()
        for arg in (annotation, *typing.get_args(annotation)):
            if typing.get_origin(arg) is typing.Literal:
                members.update(typing.get_args(arg))
        return members

    assert literal_members(ReportIndexFreshnessInput, "source_kind") == {k.value for k in SourceKind}
    assert literal_members(SearchDocsInput, "content_type") == set(CONTENT_TYPES)
    assert literal_members(LookupInput, "content_type") == set(CONTENT_TYPES)
    assert literal_members(GetProductReferenceInput, "section") == set(SECTIONS)


# --------------------------------------------------------------------- strictness


def test_a_boolean_top_k_is_invalid_argument(client: _Client) -> None:
    result, _ = client.call("search_symbols", {"query": "Document", "top_k": True})
    _assert_invalid_argument(result)


def test_a_string_top_k_is_invalid_argument(client: _Client) -> None:
    result, _ = client.call("search_symbols", {"query": "Document", "top_k": "3"})
    _assert_invalid_argument(result)


def test_a_string_limit_is_invalid_argument(client: _Client) -> None:
    result, _ = client.call("list_recent_changes", {"limit": "5"})
    _assert_invalid_argument(result)


@pytest.mark.parametrize("extra_name", ["family", "scope", "extra"])
def test_an_unknown_argument_is_invalid_argument(client: _Client, extra_name: str) -> None:
    result, _ = client.call("search_symbols", {"query": "Document", extra_name: "pdf"})
    _assert_invalid_argument(result)


def test_an_unknown_argument_on_a_tool_without_arguments_is_invalid_argument(client: _Client) -> None:
    result, _ = client.call("list_recent_changes", {"family": "cells"})
    _assert_invalid_argument(result)


def test_a_very_long_unknown_argument_name_is_not_echoed_back(client: _Client) -> None:
    result, raw = client.call("search_symbols", {"query": "Document", "n" * 5000: 1})
    _assert_invalid_argument(result)
    assert len(raw) < 2000
    assert "nnnn" not in raw


# --------------------------------------------------------------------- bounds


def test_a_50000_character_query_is_invalid_argument_and_not_echoed(client: _Client) -> None:
    result, raw = client.call("lookup", {"query": "q" * 50_000})
    _assert_invalid_argument(result)
    assert len(raw) < 2000


def test_a_20000_character_fqn_is_invalid_argument_and_not_echoed(client: _Client) -> None:
    result, raw = client.call("get_symbol", {"fqn": "F" * 20_000})
    _assert_invalid_argument(result)
    assert len(raw) < 2000


def test_a_query_at_the_cap_is_accepted_and_one_over_is_not(client: _Client) -> None:
    ok, _ = client.call("search_symbols", {"query": "q" * 1000})
    assert ok["isError"] is False
    too_long, _ = client.call("search_symbols", {"query": "q" * 1001})
    _assert_invalid_argument(too_long)


def test_an_fqn_at_the_cap_is_accepted_and_one_over_is_not(client: _Client) -> None:
    ok, _ = client.call("get_symbol", {"fqn": "F" * 512})
    assert ok["isError"] is False
    too_long, _ = client.call("get_symbol", {"fqn": "F" * 513})
    _assert_invalid_argument(too_long)


def test_an_over_long_commit_is_invalid_argument(client: _Client) -> None:
    result, raw = client.call("report_index_freshness", {"current_source_commit": "a" * 5000})
    _assert_invalid_argument(result)
    assert len(raw) < 2000


# --------------------------------------------------------------------- control characters


def test_a_nul_in_fqn_is_invalid_argument_with_a_clean_message(client: _Client) -> None:
    result, _ = client.call("get_symbol", {"fqn": "Aspose" + chr(0) + ".Pdf"})
    text = _assert_invalid_argument(result)
    assert "mkdir" not in text and "null character" not in text.lower()


def test_a_nul_in_source_kind_is_invalid_argument_with_a_clean_message(
    client: _Client, store: GenerationManifestStore
) -> None:
    before = _listing(store)
    result, _ = client.call("report_index_freshness", {"source_kind": "self_extracted" + chr(0)})
    text = _assert_invalid_argument(result)
    assert "mkdir" not in text and "null character" not in text.lower()
    assert _listing(store) == before


def test_a_newline_inside_an_fqn_is_invalid_argument_but_a_query_may_contain_one(client: _Client) -> None:
    bad, _ = client.call("get_symbol", {"fqn": "Aspose.Pdf" + chr(10) + "Document"})
    _assert_invalid_argument(bad)
    good, _ = client.call("search_symbols", {"query": "Document" + chr(10) + "Page"})
    assert good["isError"] is False


def test_a_bell_control_character_in_a_query_is_invalid_argument(client: _Client) -> None:
    result, _ = client.call("search_symbols", {"query": "Doc" + chr(7) + "ument"})
    _assert_invalid_argument(result)


# --------------------------------------------------------------------- section


def test_a_traversal_section_names_the_allowed_sections_not_the_generic_internal_error(
    client: _Client,
) -> None:
    result, _ = client.call("get_product_reference", {"section": "../../etc/passwd"})
    text = _assert_invalid_argument(result)
    assert "internal error" not in text.lower()
    for section in SECTIONS:
        assert section in text
    assert "passwd" not in text


# --------------------------------------------------------------------- current_source_commit


@pytest.mark.parametrize(
    "commit",
    [SOURCE_COMMIT.upper(), "  " + SOURCE_COMMIT + "  ", SOURCE_COMMIT[:7]],
    ids=["uppercase", "padded", "seven_character_prefix"],
)
def test_a_normalisable_commit_is_accepted(client: _Client, commit: str) -> None:
    result, _ = client.call("report_index_freshness", {"current_source_commit": commit})
    assert result["isError"] is False, result


@pytest.mark.parametrize("commit", [SOURCE_COMMIT.upper(), "  " + SOURCE_COMMIT + "\n"])
def test_the_tool_receives_the_normalised_commit(client: _Client, commit: str) -> None:
    result, _ = client.call("report_index_freshness", {"current_source_commit": commit})
    report = result["structuredContent"]["result"]
    assert report["current_source_commit"] == SOURCE_COMMIT
    assert report["stale"] is False


@pytest.mark.parametrize("commit", ["", "   ", "xyz", "a" * 65, "abcdef", "g" * 40])
def test_an_unusable_commit_is_invalid_argument(client: _Client, commit: str) -> None:
    result, _ = client.call("report_index_freshness", {"current_source_commit": commit})
    _assert_invalid_argument(result)


def test_a_64_character_commit_is_accepted(client: _Client) -> None:
    result, _ = client.call("report_index_freshness", {"current_source_commit": "a" * 64})
    assert result["isError"] is False


# --------------------------------------------------------------------- normalisation


def test_get_symbol_strips_its_fqn_and_resolves_the_same_symbol(client: _Client) -> None:
    plain, _ = client.call("get_symbol", {"fqn": FQN})
    padded, _ = client.call("get_symbol", {"fqn": "  " + FQN + "\n"})
    assert plain["isError"] is False and padded["isError"] is False
    assert padded["structuredContent"] == plain["structuredContent"]
    assert plain["structuredContent"]["result"]["fqn"] == FQN


def test_list_members_strips_its_fqn_and_resolves_the_same_symbol(client: _Client) -> None:
    plain, _ = client.call("list_members", {"fqn": FQN})
    padded, _ = client.call("list_members", {"fqn": "\t" + FQN + "  "})
    assert padded["isError"] is False
    assert padded["structuredContent"] == plain["structuredContent"]


def test_models_return_stripped_query_and_fqn() -> None:
    assert LookupInput.model_validate({"query": "  hello world \n"}).query == "hello world"
    assert _INPUT_MODELS["get_symbol"].model_validate({"fqn": " X \n"}).fqn == "X"  # type: ignore[attr-defined]


# --------------------------------------------------------------------- messages


def test_validation_messages_never_contain_the_rejected_input() -> None:
    marker = "ZZ_SECRET_MARKER_ZZ"
    for model, arguments in (
        (GetProductReferenceInput, {"section": marker}),
        (ReportIndexFreshnessInput, {"source_kind": marker}),
        (ReportIndexFreshnessInput, {"current_source_commit": marker}),
        (SearchDocsInput, {"query": "q", "content_type": marker}),
        (LookupInput, {"query": "q", "top_k": marker}),
        (LookupInput, {"query": "q" + chr(0) + marker}),
        (LookupInput, {"query": "q", marker: 1}),
    ):
        with pytest.raises(ValidationError) as caught:
            model.model_validate(arguments)
        assert marker not in public_validation_error_message(caught.value), (model, arguments)


def test_a_rejected_value_is_not_echoed_over_the_wire(client: _Client) -> None:
    marker = "ZZ_SECRET_MARKER_ZZ"
    result, raw = client.call("get_product_reference", {"section": marker})
    _assert_invalid_argument(result)
    assert marker not in raw


# --------------------------------------------------------------------- unchanged behaviour


@pytest.mark.parametrize("tool_name", sorted(VALID_ARGUMENTS))
def test_a_valid_call_of_each_tool_still_succeeds(client: _Client, tool_name: str) -> None:
    result, _ = client.call(tool_name, VALID_ARGUMENTS[tool_name])
    if tool_name == "list_recent_changes":
        # TC-564: an absent recent-releases sidecar answered `[]` -> `unavailable` (a missing file is not "no releases").
        assert result["isError"] is True, result
        assert result["structuredContent"]["code"] == "unavailable"
        return
    assert result["isError"] is False, result
    assert "result" in result["structuredContent"]


def test_the_published_symbol_is_really_served_not_an_empty_miss(client: _Client) -> None:
    result, _ = client.call("get_symbol", {"fqn": FQN})
    payload = result["structuredContent"]["result"]
    assert payload["fqn"] == FQN
