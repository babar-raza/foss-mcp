"""G3/TC-390: stdio and HTTP answer identically.

Before this card ``serve_stdio`` called ``run_stdio(config)``, which creates the server WITHOUT the
product-reference and recent-releases sidecars, so over stdio ``get_product_reference`` answered "no
packaging manifest was provided" and ``list_recent_changes`` answered ``[]`` where HTTP answered with real
content. These tests build both servers over the same tmp manifests directory (a really published generation
plus both sidecars) and call the same tools through each server's own call path: the in-process MCP client
for the stdio-built server, an HTTP session through the real ASGI app for ``build_app``. No real stdio
streams are opened.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from starlette.testclient import TestClient

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

INFRA_DIR = Path(__file__).parents[2] / "infra"
sys.path.insert(0, str(INFRA_DIR))
import fetch_recent_releases  # noqa: E402
import serve_http  # noqa: E402
import serve_stdio  # noqa: E402

CONFIG = DeploymentConfig(family="pdf", platform="net")
FIXTURES = Path(__file__).parents[1] / "fixtures" / "repo_native"
PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}
RELEASES = (
    Release(tag_name="v2.1.0", body="Fixed a real bug in the parser", richness="detailed"),
    Release(tag_name="v2.0.0", body="", richness="none"),
)


def _publish(store: GenerationManifestStore) -> None:
    chunk = Chunk(
        "Document",
        "The Document class opens and saves PDF files.",
        "self_extracted",
        "api_surface",
        PROVENANCE,
        "highest",
        (),
        NOT_CHECKED,
    )
    lease = store.acquire_lease("pdf::net::self_extracted", held_by="test-worker", generation_id="pending")
    publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=[chunk],
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )


def _write_sidecars(manifests: Path) -> None:
    sidecar = {
        "manifest_text": (FIXTURES / "pdf_net.csproj").read_text(encoding="utf-8"),
        "platform": "net",
        "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
        "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
    }
    (manifests / serve_http.sidecar_name("pdf", "net")).write_text(json.dumps(sidecar), encoding="utf-8")
    releases = [fetch_recent_releases._serialize_release(release) for release in RELEASES]
    (manifests / fetch_recent_releases.recent_releases_sidecar_name("pdf", "net")).write_text(
        json.dumps(releases), encoding="utf-8"
    )


@pytest.fixture
def deployment(tmp_path: Path) -> tuple[GenerationManifestStore, Path]:
    """A manifests directory holding a published generation (as the store) and both sidecars."""
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    store = GenerationManifestStore(manifests)
    _publish(store)
    _write_sidecars(manifests)
    return store, manifests


def _normalise(content: list[dict[str, Any]], structured: Any, is_error: bool) -> dict[str, Any]:
    return {"content": content, "structured": structured, "is_error": is_error}


def _call_over_stdio_server(
    store: GenerationManifestStore, manifests: Path, calls: list[tuple[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    server = serve_stdio.build_stdio_server(CONFIG, store, manifests)

    async def run() -> list[dict[str, Any]]:
        answers: list[dict[str, Any]] = []
        async with Client(server) as client:
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
                dumped = result.model_dump(by_alias=True, exclude_none=True)
                answers.append(
                    _normalise(
                        dumped["content"], dumped.get("structuredContent"), bool(dumped.get("isError", False))
                    )
                )
        return answers

    return anyio.run(run)


def _decode_sse(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    return json.loads(text)


def _call_over_http_app(
    store: GenerationManifestStore, manifests: Path, calls: list[tuple[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    app = serve_http.build_app(CONFIG, manifest_store=store, allowed_origins=[], manifests_dir=manifests)
    answers: list[dict[str, Any]] = []
    with TestClient(app) as client:
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "parity-test", "version": "0.0.1"},
                },
            },
            headers=HEADERS,
        )
        assert init.status_code == 200, init.text
        headers = dict(HEADERS, **{"mcp-session-id": init.headers["mcp-session-id"]})
        client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
        for index, (name, arguments) in enumerate(calls, start=1):
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            result = _decode_sse(response.text)["result"]
            answers.append(
                _normalise(
                    result["content"], result.get("structuredContent"), bool(result.get("isError", False))
                )
            )
    return answers


CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_product_reference", {"section": "install"}),
    ("get_product_reference", {"section": "license"}),
    ("list_recent_changes", {}),
]


def test_stdio_and_http_answer_the_sidecar_backed_tools_identically(
    deployment: tuple[GenerationManifestStore, Path],
) -> None:
    store, manifests = deployment
    over_stdio = _call_over_stdio_server(store, manifests, CALLS)
    over_http = _call_over_http_app(store, manifests, CALLS)
    assert len(over_stdio) == len(over_http) == len(CALLS)
    for call, stdio_answer, http_answer in zip(CALLS, over_stdio, over_http, strict=True):
        assert stdio_answer == http_answer, call


def test_get_product_reference_install_has_real_content_over_stdio(
    deployment: tuple[GenerationManifestStore, Path],
) -> None:
    store, manifests = deployment
    (answer,) = _call_over_stdio_server(store, manifests, [CALLS[0]])
    assert answer["is_error"] is False
    text = answer["content"][0]["text"]
    assert text.strip()
    assert "no packaging manifest was provided" not in text


def test_get_product_reference_license_has_real_content_over_stdio(
    deployment: tuple[GenerationManifestStore, Path],
) -> None:
    store, manifests = deployment
    (answer,) = _call_over_stdio_server(store, manifests, [CALLS[1]])
    assert answer["is_error"] is False
    text = answer["content"][0]["text"]
    assert text.strip()
    assert "no packaging manifest was provided" not in text


def test_list_recent_changes_over_stdio_returns_the_sidecar_releases(
    deployment: tuple[GenerationManifestStore, Path],
) -> None:
    store, manifests = deployment
    (answer,) = _call_over_stdio_server(store, manifests, [CALLS[2]])
    assert answer["is_error"] is False
    text = answer["content"][0]["text"]
    assert "v2.1.0" in text
    assert "v2.0.0" in text
    assert len(answer["structured"]["result"]) == len(RELEASES)


def test_a_manifests_directory_without_sidecars_still_serves_and_answers_honestly(tmp_path: Path) -> None:
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    store = GenerationManifestStore(manifests)
    _publish(store)
    over_stdio = _call_over_stdio_server(store, manifests, CALLS)
    over_http = _call_over_http_app(store, manifests, CALLS)
    assert over_stdio[:2] == over_http[:2]
    assert "no packaging manifest was provided" in over_stdio[0]["content"][0]["text"]
    # TC-564: a missing recent-releases sidecar answered `[]` (a "complete answer") -> the same honest `unavailable`
    # error over both transports (compared without the per-call correlation id).
    for answer in (over_stdio[2], over_http[2]):
        assert answer["is_error"] is True
        assert answer["structured"]["code"] == "unavailable"
        assert answer["content"][0]["text"] == answer["structured"]["message"]
    assert over_stdio[2]["content"] == over_http[2]["content"]
    assert over_stdio[2]["structured"]["message"] == over_http[2]["structured"]["message"]


def test_the_server_stdio_builds_lists_the_same_tools_as_the_http_app_server(
    deployment: tuple[GenerationManifestStore, Path],
) -> None:
    store, manifests = deployment
    server = serve_stdio.build_stdio_server(CONFIG, store, manifests)

    async def listed() -> list[str]:
        async with Client(server) as client:
            return sorted(tool.name for tool in (await client.list_tools()).tools)

    names = anyio.run(listed)
    assert len(names) == 9
    assert {"get_product_reference", "list_recent_changes", "lookup"} <= set(names)


def test_stdio_resolves_the_manifests_directory_from_the_same_variable_as_http(
    deployment: tuple[GenerationManifestStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, manifests = deployment
    monkeypatch.setenv(serve_http.MANIFESTS_DIR_ENV, str(manifests))
    server = serve_stdio.build_stdio_server(CONFIG, store)

    async def call() -> str:
        async with Client(server) as client:
            result = await client.call_tool("list_recent_changes", {})
            return result.content[0].text

    assert "v2.1.0" in anyio.run(call)


def test_stdio_reuses_the_http_sidecar_loaders_and_does_not_copy_them() -> None:
    assert serve_stdio._serving_product_reference_inputs is serve_http._serving_product_reference_inputs
    assert serve_stdio._serving_recent_releases is serve_http._serving_recent_releases
    assert serve_stdio.MANIFESTS_DIR_ENV == serve_http.MANIFESTS_DIR_ENV
    assert serve_stdio.DEFAULT_MANIFESTS_DIR == serve_http.DEFAULT_MANIFESTS_DIR


def test_the_stdio_entrypoint_source_calls_both_loaders_and_serves_over_stdio_server() -> None:
    """The call site exists in the real entrypoint file, not merely in a unit test (AGENTS.md,
    "Integration and liveness")."""
    tree = ast.parse((INFRA_DIR / "serve_stdio.py").read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {
        "create_server",
        "_serving_product_reference_inputs",
        "_serving_recent_releases",
        "stdio_server",
    } <= called
    assert "run_stdio" in {node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}


def test_run_stdio_builds_its_server_through_build_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() -> run_stdio -> build_stdio_server: proven by replacing the builder and entering run_stdio
    with stdio_server replaced by an in-memory stand-in, so no real stream is touched."""
    import contextlib

    built: list[DeploymentConfig] = []

    class FakeServer:
        def create_initialization_options(self) -> str:
            return "options"

        def run(self, read: Any, write: Any, options: Any) -> tuple[Any, Any, Any]:
            return read, write, options

    def fake_build(config: DeploymentConfig, *args: Any, **kwargs: Any) -> FakeServer:
        built.append(config)
        return FakeServer()

    @contextlib.asynccontextmanager
    async def fake_stdio_server():
        yield "read", "write"

    monkeypatch.setattr(serve_stdio, "build_stdio_server", fake_build)
    monkeypatch.setattr(serve_stdio, "stdio_server", fake_stdio_server)

    async def run() -> Any:
        async with serve_stdio.run_stdio(CONFIG) as running:
            return running

    assert anyio.run(run) == ("read", "write", "options")
    assert built == [CONFIG]
