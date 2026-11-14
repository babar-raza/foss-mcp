"""Tests for ``foss_mcp.indexing.cached_embedding_provider.FallbackEmbeddingProvider`` (TC-830).

The cache-then-gateway provider with write-back used by a regeneration. No test makes a network
call: the gateway is either a small in-process fake handed in through the provider's keyword-only
``_gateway`` parameter, or the REAL ``GatewayEmbeddingProvider`` whose module-level default
transport is replaced by a fake callable (so the zero-argument construction ``infra/ingest.py``
uses is exercised end to end). One test reads the REAL committed ``infra/demo-inputs/embeddings``
cache for the one pilot whose baked vectors are 4096-dimensional today and proves a pure cache
hit never reaches the gateway and never rewrites the committed file.

The last group is the AGENTS.md "Integration and liveness" proof: ``infra/ingest.py``'s own source
names the ``cached-gateway`` choice and the real provider class, the CLI builds it, and a real
``ingest.main()`` run with it is cache-first and byte-stable on the second run.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import infra.ingest as ingest
from foss_mcp.indexing import gateway_embedding_provider
from foss_mcp.indexing.cached_embedding_provider import (
    CachedEmbeddingDimensionConflictError,
    FallbackEmbeddingProvider,
    _text_hash,
)
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from tests.infra.test_ingest import (
    EMBEDDING_PROVIDER_SPEC,
    SCOPE,
    _chunk_entry,
    _write_chunks_fixture,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CACHE_ROOT = _REPO_ROOT / "infra" / "demo-inputs" / "embeddings"
_REAL_MANIFESTS_ROOT = _REPO_ROOT / "infra" / "demo-manifests"
_INGEST_SOURCE = _REPO_ROOT / "infra" / "ingest.py"

_ENV_NAMES = (
    "FOSS_MCP_CACHED_EMBEDDING_ROOT",
    "FOSS_MCP_CACHED_EMBEDDING_SCOPE",
    "FOSS_MCP_CACHED_EMBEDDING_WRITEBACK_FILE",
    "FOSS_MCP_CACHED_EMBEDDING_DECIMALS",
    "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT",
    "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY",
    "FOSS_MCP_EMBEDDING_GATEWAY_MODEL",
    "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION",
    "FOSS_MCP_EMBEDDING_GATEWAY_BATCH_SIZE",
    "FOSS_MCP_EMBEDDING_GATEWAY_TIMEOUT_SECONDS",
    "FOSS_MCP_EMBEDDING_GATEWAY_MAX_REQUESTS",
)

_DIM = 3


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


class FakeGateway:
    """An in-process ``EmbeddingProvider``: records every batch it is asked for and returns
    deterministic, many-decimal vectors so rounding is observable."""

    def __init__(self, dimension: int = _DIM, vector_length: int | None = None) -> None:
        self.dimension = dimension
        self._vector_length = dimension if vector_length is None else vector_length
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        self.calls.append(list(texts))
        return [
            tuple(len(text) + (column + 1) * 0.123456789 for column in range(self._vector_length))
            for text in texts
        ]


def _write_cache(path: Path, entries: dict[str, list[float]], dimension: int = _DIM) -> bytes:
    document = json.dumps(
        {"dimension": dimension, "entries": entries}, sort_keys=True, ensure_ascii=False, indent=2
    )
    payload = (document + "\n").encode("utf-8")
    path.write_bytes(payload)
    return payload


def _scoped(monkeypatch: pytest.MonkeyPatch, root: Path, scope: str = "pdf__net") -> Path:
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(root))
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", scope)
    return root / f"{scope}.json"


# --- cache first -------------------------------------------------------------------------------


def test_cached_text_is_served_from_the_cache_without_calling_the_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path)
    before = _write_cache(cache_file, {_text_hash("alpha"): [0.1, 0.2, 0.3]})
    gateway = FakeGateway()

    provider = FallbackEmbeddingProvider(_gateway=gateway)
    vectors = provider.embed(["alpha"])

    assert vectors == [(0.1, 0.2, 0.3)]
    assert gateway.calls == []
    assert provider.stats() == {
        "hits": 1,
        "misses": 0,
        "gateway_calls": 0,
        "gateway_texts": 0,
        "recorded": 0,
        "persisted": True,
    }
    assert cache_file.read_bytes() == before


def test_miss_calls_the_gateway_once_and_order_is_preserved_across_hits_and_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path)
    _write_cache(cache_file, {_text_hash("alpha"): [0.1, 0.2, 0.3]})
    gateway = FakeGateway()

    provider = FallbackEmbeddingProvider(_gateway=gateway)
    vectors = provider.embed(["new one", "alpha", "another new"])

    assert gateway.calls == [["new one", "another new"]]
    assert vectors[1] == (0.1, 0.2, 0.3)
    assert vectors[0] == (7.12346, 7.24691, 7.37037)  # len("new one") + k * 0.123456789, 5 dp
    assert vectors[2] == (11.12346, 11.24691, 11.37037)  # len("another new") == 11
    assert provider.hits == 1
    assert provider.misses == 2
    assert provider.gateway_calls == 1
    assert provider.gateway_texts == 2


def test_a_duplicate_missing_text_is_sent_to_the_gateway_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scoped(monkeypatch, tmp_path)
    gateway = FakeGateway()

    provider = FallbackEmbeddingProvider(_gateway=gateway)
    vectors = provider.embed(["same", "same"])

    assert gateway.calls == [["same"]]
    assert vectors[0] == vectors[1]
    assert (provider.misses, provider.gateway_texts) == (2, 1)


def test_a_recorded_text_is_a_hit_on_a_later_call_of_the_same_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scoped(monkeypatch, tmp_path)
    gateway = FakeGateway()
    provider = FallbackEmbeddingProvider(_gateway=gateway)

    first = provider.embed(["once"])
    second = provider.embed(["once"])

    assert first == second
    assert len(gateway.calls) == 1
    assert (provider.hits, provider.misses) == (1, 1)


# --- write-back ---------------------------------------------------------------------------------


def test_write_back_persists_the_rounded_vector_in_the_cache_writers_canonical_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path)
    _write_cache(cache_file, {_text_hash("alpha"): [0.1, 0.2, 0.3]})

    provider = FallbackEmbeddingProvider(_gateway=FakeGateway())
    provider.embed(["beta"])

    raw = cache_file.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"}\n")
    document = json.loads(raw)
    assert document["dimension"] == _DIM
    assert document["entries"][_text_hash("alpha")] == [0.1, 0.2, 0.3]  # existing entry kept exactly
    assert document["entries"][_text_hash("beta")] == [4.12346, 4.24691, 4.37037]
    expected = json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    assert raw.decode("utf-8") == expected  # deterministic key order, 2-space indent, trailing newline
    assert not list(tmp_path.glob("*.tmp"))


def test_second_run_is_cache_only_and_leaves_the_cache_file_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path)
    texts = ["one", "two two", "three three three"]

    first_gateway = FakeGateway()
    first = FallbackEmbeddingProvider(_gateway=first_gateway).embed(texts)
    after_first = cache_file.read_bytes()
    assert len(first_gateway.calls) == 1

    second_gateway = FakeGateway()
    second_provider = FallbackEmbeddingProvider(_gateway=second_gateway)
    second = second_provider.embed(texts)

    assert second == first
    assert second_gateway.calls == []
    assert second_provider.stats()["gateway_calls"] == 0
    assert second_provider.hits == 3
    assert cache_file.read_bytes() == after_first


def test_a_scope_whose_cache_file_does_not_exist_yet_starts_empty_and_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path / "fresh-root", scope="brand__new")
    assert not cache_file.exists()

    FallbackEmbeddingProvider(_gateway=FakeGateway()).embed(["x"])

    assert json.loads(cache_file.read_text(encoding="utf-8"))["dimension"] == _DIM
    assert list(json.loads(cache_file.read_text(encoding="utf-8"))["entries"]) == [_text_hash("x")]


def test_without_a_scope_the_writeback_file_variable_names_the_target_and_other_files_are_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(tmp_path))
    other = tmp_path / "pdf__net.json"
    other_bytes = _write_cache(other, {_text_hash("alpha"): [0.1, 0.2, 0.3]})
    target = tmp_path / "out" / "new.json"
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_WRITEBACK_FILE", str(target))
    gateway = FakeGateway()

    provider = FallbackEmbeddingProvider(_gateway=gateway)
    provider.embed(["alpha", "gamma"])  # alpha is a hit in the merged cache, gamma a miss

    assert gateway.calls == [["gamma"]]
    assert list(json.loads(target.read_text(encoding="utf-8"))["entries"]) == [_text_hash("gamma")]
    assert other.read_bytes() == other_bytes


def test_without_scope_or_writeback_file_misses_are_recorded_in_memory_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(tmp_path))
    gateway = FakeGateway()

    provider = FallbackEmbeddingProvider(_gateway=gateway)
    provider.embed(["m"])
    provider.embed(["m"])

    assert len(gateway.calls) == 1
    assert provider.stats()["persisted"] is False
    assert provider.stats()["recorded"] == 1
    assert list(tmp_path.iterdir()) == []


def test_decimals_variable_changes_the_write_back_rounding_and_is_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path)
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_DECIMALS", "2")

    vectors = FallbackEmbeddingProvider(_gateway=FakeGateway()).embed(["abc"])

    assert vectors == [(3.12, 3.25, 3.37)]
    assert json.loads(cache_file.read_text(encoding="utf-8"))["entries"][_text_hash("abc")] == [
        3.12,
        3.25,
        3.37,
    ]

    for bad in ("0", "13", "many"):
        monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_DECIMALS", bad)
        with pytest.raises(ValueError, match="FOSS_MCP_CACHED_EMBEDDING_DECIMALS"):
            FallbackEmbeddingProvider(_gateway=FakeGateway())


# --- dimension handling: never pad, never truncate ----------------------------------------------


def test_cache_dimension_differing_from_the_gateway_dimension_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path)
    _write_cache(cache_file, {_text_hash("alpha"): [0.1, 0.2]}, dimension=2)

    with pytest.raises(CachedEmbeddingDimensionConflictError, match="does not match the gateway"):
        FallbackEmbeddingProvider(_gateway=FakeGateway(dimension=_DIM))


def test_a_cached_vector_whose_length_is_not_the_dimension_raises_when_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path)
    bad_hash = _text_hash("short")
    _write_cache(cache_file, {bad_hash: [0.1, 0.2]})  # declares dimension 3 but holds 2 values

    with pytest.raises(ValueError, match=bad_hash):
        FallbackEmbeddingProvider(_gateway=FakeGateway(dimension=_DIM))


def test_a_gateway_vector_of_the_wrong_length_raises_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = _scoped(monkeypatch, tmp_path)
    provider = FallbackEmbeddingProvider(_gateway=FakeGateway(dimension=_DIM, vector_length=_DIM - 1))

    with pytest.raises(ValueError, match="expected the embedding dimension 3"):
        provider.embed(["bad"])

    assert provider.stats()["recorded"] == 0
    assert not cache_file.exists()


def test_a_gateway_returning_the_wrong_number_of_vectors_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scoped(monkeypatch, tmp_path)

    class ShortGateway(FakeGateway):
        def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
            return super().embed(texts)[:-1]

    with pytest.raises(RuntimeError, match="returned 1 vector"):
        FallbackEmbeddingProvider(_gateway=ShortGateway()).embed(["p", "q"])


# --- the real gateway class and the real committed cache ----------------------------------------


def test_zero_argument_construction_uses_the_real_gateway_provider_with_its_dimension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FallbackEmbeddingProvider()`` - the way ``infra/ingest.py`` builds it - wraps a real
    ``GatewayEmbeddingProvider`` (configured from the environment) and inherits its dimension; the
    module's default transport is replaced by a fake so nothing leaves the process."""
    _scoped(monkeypatch, tmp_path)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT", "https://gateway.example.test/v1/")
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_API_KEY", "test-key")
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION", str(_DIM))
    requests: list[bytes] = []

    def transport(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        requests.append(body)
        inputs = json.loads(body)["input"]
        data = [{"index": i, "embedding": [0.5, 0.25, 0.125]} for i in range(len(inputs))]
        return json.dumps({"data": data}).encode("utf-8")

    monkeypatch.setattr(gateway_embedding_provider, "_default_transport", transport)

    provider = FallbackEmbeddingProvider()

    assert provider.dimension == _DIM
    assert provider.embed(["live"]) == [(0.5, 0.25, 0.125)]
    assert len(requests) == 1


def test_real_committed_cache_hit_never_reaches_the_gateway_and_never_rewrites_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Against the real committed imaging__net cache (4096-dim, the one pilot whose baked vector
    index is 4096-dim today): every one of that pilot's real example texts is a cache hit, the
    vectors equal the real baked vectors, the gateway is never called and the file is untouched."""
    scope_dir = _REAL_MANIFESTS_ROOT / "imaging__net__self_extracted"
    pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
    version = pointer["generation_id"].rsplit("::", 1)[-1]
    vector_index = json.loads((scope_dir / f"generation__{version}.json").read_text(encoding="utf-8"))[
        "payload"
    ]["vector_index"]
    points = vector_index["points"]
    assert points and vector_index["dimension"] == 4096

    cache_file = _scoped(monkeypatch, _REAL_CACHE_ROOT, scope="imaging__net")
    before = cache_file.read_bytes()
    gateway = FakeGateway(dimension=4096)

    provider = FallbackEmbeddingProvider(_gateway=gateway)
    vectors = provider.embed([point["text"] for point in points])

    assert vectors == [tuple(point["vector"]) for point in points]
    assert gateway.calls == []
    assert provider.misses == 0
    assert cache_file.read_bytes() == before


# --- production wiring: infra/ingest.py builds it (AGENTS.md "Integration and liveness") --------


def _ingest_ast() -> ast.Module:
    return ast.parse(_INGEST_SOURCE.read_text(encoding="utf-8"))


def test_ingest_source_names_the_cached_gateway_choice_and_the_real_provider_class() -> None:
    """A parsed-source assertion on the committed entrypoint file, not on the provider's own
    unit tests: ``infra/ingest.py`` maps ``cached-gateway`` to the real class and its loader
    instantiates whatever that mapping names."""
    tree = _ingest_ast()
    aliases = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_EMBEDDING_PROVIDER_ALIASES" for t in node.targets
        ):
            aliases = ast.literal_eval(node.value)
    assert aliases == {
        "cached-gateway": "foss_mcp.indexing.cached_embedding_provider:FallbackEmbeddingProvider"
    }

    loader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_load_embedding_provider"
    )
    names_used = {node.id for node in ast.walk(loader) if isinstance(node, ast.Name)}
    assert "_EMBEDDING_PROVIDER_ALIASES" in names_used
    calls_getattr_then_call = [
        node
        for node in ast.walk(loader)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Call)
        and getattr(node.func.func, "id", "") == "getattr"
    ]
    assert calls_getattr_then_call, "the loader no longer instantiates the resolved class"

    # main() reaches the loader with the parsed --embedding-provider value.
    main_source = ast.get_source_segment(
        _INGEST_SOURCE.read_text(encoding="utf-8"),
        next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"),
    )
    assert main_source is not None
    assert "_load_embedding_provider(args.embedding_provider)" in main_source


def test_ingest_loader_builds_the_fallback_provider_for_cached_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scoped(monkeypatch, tmp_path)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT", "https://gateway.example.test/v1/")
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_API_KEY", "test-key")

    provider = ingest._load_embedding_provider("cached-gateway")

    assert isinstance(provider, FallbackEmbeddingProvider)
    assert provider.dimension == 4096  # the gateway provider's default, unchanged


def test_existing_embedding_provider_choices_resolve_exactly_as_before() -> None:
    assert (
        type(ingest._load_embedding_provider(EMBEDDING_PROVIDER_SPEC)).__name__
        == "DeterministicEmbeddingProvider"
    )
    hashing = ingest._load_embedding_provider(
        "foss_mcp.indexing.hashing_embedding_provider:HashingEmbeddingProvider"
    )
    assert type(hashing).__name__ == "HashingEmbeddingProvider"
    with pytest.raises(ValueError, match="--embedding-provider must be"):
        ingest._load_embedding_provider("not-a-spec")


def _run_main(chunks_path: Path, store_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    argv = [
        "ingest.py",
        "--family",
        "pdf",
        "--platform",
        "net",
        "--source-kind",
        "self_extracted",
        "--chunks",
        str(chunks_path),
        "--manifest-store",
        str(store_path),
        "--embedding-provider",
        provider,
        "--min-symbols",
        "0",
        "--min-docs",
        "0",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    ingest.main()


def test_ingest_main_with_cached_gateway_is_cache_first_and_byte_stable_on_the_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cache_root = tmp_path / "cache"
    cache_file = _scoped(monkeypatch, cache_root)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT", "https://gateway.example.test/v1/")
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_API_KEY", "test-key")
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION", str(_DIM))
    transport_calls: list[list[str]] = []

    def transport(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        inputs = json.loads(body)["input"]
        transport_calls.append(inputs)
        data = [
            {"index": i, "embedding": [len(text) + 0.1234567, len(text) + 0.7654321, 1.0 / 3.0]}
            for i, text in enumerate(inputs)
        ]
        return json.dumps({"data": data}).encode("utf-8")

    monkeypatch.setattr(gateway_embedding_provider, "_default_transport", transport)
    chunks_path = tmp_path / "chunks.json"
    _write_chunks_fixture(
        chunks_path,
        [
            _chunk_entry("Document.Open", "`Document.Open` opens a file."),
            _chunk_entry("Page", "A page carries properties."),
        ],
    )

    _run_main(chunks_path, tmp_path / "store-1", monkeypatch, "cached-gateway")
    first_output = capsys.readouterr().out
    first_cache = cache_file.read_bytes()
    first_store = GenerationManifestStore(tmp_path / "store-1")
    first_points = first_store.read_generation(SCOPE, first_store.read_active(SCOPE)).payload["vector_index"][
        "points"
    ]

    assert len(transport_calls) == 1
    assert "embedding cache: hits=0 misses=2 gateway_calls=1 gateway_texts=2" in first_output
    assert len(first_points) == 2

    _run_main(chunks_path, tmp_path / "store-2", monkeypatch, "cached-gateway")
    second_output = capsys.readouterr().out
    second_store = GenerationManifestStore(tmp_path / "store-2")
    second_points = second_store.read_generation(SCOPE, second_store.read_active(SCOPE)).payload[
        "vector_index"
    ]["points"]

    assert len(transport_calls) == 1  # the second regeneration made no gateway call at all
    assert "embedding cache: hits=2 misses=0 gateway_calls=0 gateway_texts=0" in second_output
    assert cache_file.read_bytes() == first_cache

    # point_id embeds the (time-based) generation version, so compare the content that matters
    def content(points: list[dict]) -> list[tuple]:
        return [(point["chunk_id"], point["text"], point["vector"]) for point in points]

    assert content(second_points) == content(first_points)


def test_ingest_main_prints_no_cache_summary_for_a_provider_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    chunks_path = tmp_path / "chunks.json"
    _write_chunks_fixture(chunks_path, [_chunk_entry("Page", "A page carries properties.")])

    _run_main(chunks_path, tmp_path / "store", monkeypatch, EMBEDDING_PROVIDER_SPEC)

    output = capsys.readouterr().out
    assert "published " in output
    assert "embedding cache:" not in output
