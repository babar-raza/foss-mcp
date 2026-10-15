"""TC-389: tool handlers run off the event loop, and a generation is parsed once.

Offline and deterministic: ordering is proved with ``threading.Event`` handshakes, never with
wall-clock thresholds. ``anyio.fail_after`` is only a deadlock guard.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import _INPUT_MODELS, _to_jsonable, create_server
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

REPO_ROOT = Path(__file__).parents[2]
DEMO_WORDS_NET = REPO_ROOT / "infra" / "demo-manifests" / "words__net__self_extracted"
SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
FQN = "Aspose.Pdf.Document"
GUARD_SECONDS = 60

VALID_ARGUMENTS: dict[str, dict[str, Any]] = {
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


_ENVELOPE_KEYS = ("scope", "content_origin", "untrusted_upstream_content")


def _without_envelope(value: Any) -> Any:
    """*value* without the keys the additive response envelope (G3/TC-514) puts on a result: the
    served payload now carries a scope, a content origin and an untrusted marker that a direct call
    of the handler does not."""
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if key not in _ENVELOPE_KEYS}
    if isinstance(value, list):
        return [_without_envelope(item) for item in value]
    return value


def _publish_pdf(store: GenerationManifestStore) -> None:
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
def pdf_store(tmp_path: Path) -> GenerationManifestStore:
    store = GenerationManifestStore(tmp_path / "pdf-manifests")
    _publish_pdf(store)
    return store


@pytest.fixture
def words_store(tmp_path: Path) -> GenerationManifestStore:
    target = tmp_path / "words-manifests" / "words__net__self_extracted"
    shutil.copytree(DEMO_WORDS_NET, target)
    return GenerationManifestStore(tmp_path / "words-manifests")


def _call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Dispatch exactly as the transport does: through the server's registered tools/call handler."""
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


def _words_server(store: GenerationManifestStore) -> Any:
    return create_server(DeploymentConfig(family="words", platform="net"), manifest_store=store)


def _pdf_server(store: GenerationManifestStore) -> Any:
    return create_server(DeploymentConfig(family="pdf", platform="net"), manifest_store=store)


@pytest.fixture
def generation_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every call that reaches the (cached) generation reader."""
    seen: list[str] = []
    original = GenerationManifestStore._cached_generation_document

    def counting(self: GenerationManifestStore, path: Path) -> Any:
        seen.append(path.name)
        return original(self, path)

    monkeypatch.setattr(GenerationManifestStore, "_cached_generation_document", counting)
    return seen


@pytest.fixture
def generation_parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every generation file actually parsed from disk."""
    seen: list[str] = []
    original = GenerationManifestStore._read_json

    def counting(path: Path) -> Any:
        if path.name.startswith("generation__") and path.exists():
            seen.append(path.name)
        return original(path)

    monkeypatch.setattr(GenerationManifestStore, "_read_json", staticmethod(counting))
    return seen


def _patch_registry(monkeypatch: pytest.MonkeyPatch, replacements: dict[str, Any]) -> None:
    original = server_module._build_tool_registry

    def patched(*args: Any, **kwargs: Any) -> dict[str, Any]:
        registry = original(*args, **kwargs)
        registry.update(replacements)
        return registry

    monkeypatch.setattr(server_module, "_build_tool_registry", patched)


# ---------------------------------------------------------------- the loop is not blocked


def test_the_event_loop_runs_while_a_handler_is_blocked(
    monkeypatch: pytest.MonkeyPatch, pdf_store: GenerationManifestStore
) -> None:
    entered, release = threading.Event(), threading.Event()
    order: list[str] = []

    def blocking_lookup(*, query: str, content_type: str | None = None, top_k: int = 10) -> Any:
        entered.set()
        order.append("handler-started")
        assert release.wait(timeout=GUARD_SECONDS), "the loop never got to run"
        order.append("handler-finished")
        return {"blocked": True}

    _patch_registry(monkeypatch, {"lookup": blocking_lookup})
    server = _pdf_server(pdf_store)
    entry = server.get_request_handler("tools/call")

    async def scenario() -> CallToolResult:
        results: list[CallToolResult] = []

        async def call() -> None:
            results.append(
                await entry.handler(None, CallToolRequestParams(name="lookup", arguments={"query": "x"}))
            )

        with anyio.fail_after(GUARD_SECONDS):
            async with anyio.create_task_group() as tg:
                tg.start_soon(call)
                await anyio.to_thread.run_sync(entered.wait, GUARD_SECONDS)
                order.append("loop-ran")
                release.set()
        return results[0]

    result = anyio.run(scenario)
    assert result.is_error is False
    # The response envelope adds scope keys to a dict result (G3/TC-514); the handler's own payload is unchanged.
    assert _without_envelope(result.structured_content["result"]) == {"blocked": True}
    assert order == ["handler-started", "loop-ran", "handler-finished"]


def test_a_handler_does_not_run_on_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch, pdf_store: GenerationManifestStore
) -> None:
    threads: dict[str, int] = {}

    def probe(*, query: str, content_type: str | None = None, top_k: int = 10) -> Any:
        threads["handler"] = threading.get_ident()
        return {"ok": True}

    _patch_registry(monkeypatch, {"lookup": probe})
    server = _pdf_server(pdf_store)
    entry = server.get_request_handler("tools/call")

    async def scenario() -> None:
        threads["loop"] = threading.get_ident()
        await entry.handler(None, CallToolRequestParams(name="lookup", arguments={"query": "x"}))

    anyio.run(scenario)
    assert threads["handler"] != threads["loop"]


def test_two_blocked_calls_do_not_stop_a_third_from_completing(
    monkeypatch: pytest.MonkeyPatch, pdf_store: GenerationManifestStore
) -> None:
    both_in, release = threading.Barrier(3), threading.Event()
    finished: list[str] = []

    def blocking(*, query: str, content_type: str | None = None, top_k: int = 10) -> Any:
        if query != "fast":
            both_in.wait(timeout=GUARD_SECONDS)
            assert release.wait(timeout=GUARD_SECONDS)
        return {"query": query}

    _patch_registry(monkeypatch, {"lookup": blocking})
    entry = _pdf_server(pdf_store).get_request_handler("tools/call")

    async def scenario() -> None:
        async def call(query: str) -> None:
            result = await entry.handler(
                None, CallToolRequestParams(name="lookup", arguments={"query": query})
            )
            assert result.is_error is False
            finished.append(query)

        with anyio.fail_after(GUARD_SECONDS):
            async with anyio.create_task_group() as tg:
                tg.start_soon(call, "slow-1")
                tg.start_soon(call, "slow-2")
                await anyio.to_thread.run_sync(both_in.wait, GUARD_SECONDS)
                await call("fast")
                assert finished == ["fast"]
                release.set()

    anyio.run(scenario)
    assert sorted(finished) == ["fast", "slow-1", "slow-2"]


# ---------------------------------------------------------------- errors map as before


def test_a_value_error_in_a_threaded_handler_is_invalid_argument(
    monkeypatch: pytest.MonkeyPatch, pdf_store: GenerationManifestStore
) -> None:
    def bad(*, query: str, content_type: str | None = None, top_k: int = 10) -> Any:
        raise ValueError("the caller asked for something wrong")

    _patch_registry(monkeypatch, {"lookup": bad})
    result = _call(_pdf_server(pdf_store), "lookup", {"query": "x"})
    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_argument"
    assert "correlation_id" in result.structured_content


def test_any_other_exception_in_a_threaded_handler_is_internal(
    monkeypatch: pytest.MonkeyPatch, pdf_store: GenerationManifestStore
) -> None:
    def broken(*, query: str, content_type: str | None = None, top_k: int = 10) -> Any:
        raise RuntimeError("boom with /secret/path inside")

    _patch_registry(monkeypatch, {"lookup": broken})
    result = _call(_pdf_server(pdf_store), "lookup", {"query": "x"})
    assert result.is_error is True
    assert result.structured_content["code"] == "internal"
    assert "/secret/path" not in result.content[0].text


def test_a_validation_failure_never_reaches_the_handler(
    monkeypatch: pytest.MonkeyPatch, pdf_store: GenerationManifestStore
) -> None:
    called: list[str] = []

    def spy(*, query: str, content_type: str | None = None, top_k: int = 10) -> Any:
        called.append(query)
        return {}

    _patch_registry(monkeypatch, {"lookup": spy})
    result = _call(_pdf_server(pdf_store), "lookup", {"query": "x", "top_k": 0})
    assert result.structured_content["code"] == "invalid_argument"
    assert called == []


def test_an_unknown_tool_is_still_not_found(pdf_store: GenerationManifestStore) -> None:
    result = _call(_pdf_server(pdf_store), "no_such_tool", {})
    assert result.is_error is True
    assert result.structured_content["code"] == "not_found"


# ---------------------------------------------------------------- same content as a direct call


@pytest.mark.parametrize("tool_name", sorted(VALID_ARGUMENTS))
def test_a_valid_call_returns_the_same_structured_content_as_a_direct_call(
    pdf_store: GenerationManifestStore, tool_name: str
) -> None:
    server = _pdf_server(pdf_store)
    scope = server_module.resolve_scope(DeploymentConfig(family="pdf", platform="net"), request=None)
    registry = server_module._build_tool_registry(
        pdf_store, scope, server_module.ProductReferenceInputs(), ()
    )
    arguments = _INPUT_MODELS[tool_name].model_validate(VALID_ARGUMENTS[tool_name]).model_dump()
    direct = registry[tool_name](**arguments)
    result = _call(server, tool_name, VALID_ARGUMENTS[tool_name])
    assert result.is_error is False, result
    # The response envelope adds scope keys beside and inside the result (G3/TC-514); the payload is unchanged.
    assert _without_envelope(result.structured_content["result"]) == _without_envelope(_to_jsonable(direct))
    assert type(result.structured_content["result"]) is type(_to_jsonable(direct))


# ---------------------------------------------------------------- the adversarial-suite scenario


def test_words_net_demo_manifest_is_present() -> None:
    assert (DEMO_WORDS_NET / "active_pointer.json").is_file()


def test_top_k_of_two_to_the_63_is_invalid_argument_with_no_generation_read(
    words_store: GenerationManifestStore, generation_reads: list[str]
) -> None:
    result = _call(
        _words_server(words_store),
        "search_docs",
        {"query": "install", "content_type": sorted(CONTENT_TYPES)[0], "top_k": 2**63},
    )
    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_argument"
    assert generation_reads == []


def test_a_690000_character_query_is_invalid_argument_with_no_generation_read(
    words_store: GenerationManifestStore, generation_reads: list[str]
) -> None:
    result = _call(_words_server(words_store), "lookup", {"query": "a" * 690_000})
    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_argument"
    assert generation_reads == []
    assert len(result.content[0].text) < 2_000


def test_healthz_style_coroutine_completes_while_a_real_words_net_lookup_is_held(
    monkeypatch: pytest.MonkeyPatch, words_store: GenerationManifestStore
) -> None:
    scope = server_module.resolve_scope(DeploymentConfig(family="words", platform="net"), request=None)
    real = server_module._build_tool_registry(words_store, scope, server_module.ProductReferenceInputs(), ())[
        "lookup"
    ]
    entered, release = threading.Event(), threading.Event()
    order: list[str] = []

    def held_lookup(*, query: str, content_type: str | None = None, top_k: int = 10) -> Any:
        entered.set()
        assert release.wait(timeout=GUARD_SECONDS), "the loop never got to run"
        result = real(query=query, content_type=content_type, top_k=top_k)
        order.append("lookup-done")
        return result

    _patch_registry(monkeypatch, {"lookup": held_lookup})
    entry = _words_server(words_store).get_request_handler("tools/call")

    async def healthz() -> str:
        await anyio.sleep(0)
        order.append("healthz-done")
        return "ok"

    async def scenario() -> CallToolResult:
        results: list[CallToolResult] = []

        async def call() -> None:
            results.append(
                await entry.handler(
                    None, CallToolRequestParams(name="lookup", arguments={"query": "Document"})
                )
            )

        with anyio.fail_after(GUARD_SECONDS):
            async with anyio.create_task_group() as tg:
                tg.start_soon(call)
                await anyio.to_thread.run_sync(entered.wait, GUARD_SECONDS)
                assert await healthz() == "ok"
                release.set()
        return results[0]

    result = anyio.run(scenario)
    assert result.is_error is False
    assert order == ["healthz-done", "lookup-done"]


def test_twenty_sequential_valid_calls_parse_the_words_net_generation_once(
    words_store: GenerationManifestStore, generation_parses: list[str]
) -> None:
    server = _words_server(words_store)
    for _ in range(20):
        result = _call(server, "lookup", {"query": "Document"})
        assert result.is_error is False, result
    assert len(generation_parses) == 1


def test_a_readiness_probe_does_not_re_parse_an_unchanged_generation(
    words_store: GenerationManifestStore, generation_parses: list[str]
) -> None:
    from foss_mcp.mcp.health import DeploymentGenerationStore, round_trip_check

    probe_store = DeploymentGenerationStore(words_store, "words::net::self_extracted")
    for _ in range(5):
        assert round_trip_check(probe_store) is True
    assert len(generation_parses) == 1
