"""G3/TC-474: one manifests directory for symbols, documents and sidecars, and no ANSI log copies.

Before this card ``build_app`` and ``build_stdio_server`` read the sidecars from
``FOSS_MCP_MANIFESTS_DIR`` but built the generation store from a hard-coded ``/data/manifests``, so a
peer who mounted their own manifests got pilots validated and sidecars read from their directory while
symbols and documents came from the baked one. Separately, ``JsonLogFormatter`` copied uvicorn's
``color_message`` extra (the message with ANSI colour escapes) into every JSON line.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.normalization.document_schema import Provenance
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider
from tests.infra.readiness_support import ready_chunks

INFRA_DIR = Path(__file__).parents[2] / "infra"
sys.path.insert(0, str(INFRA_DIR))
import serve_http  # noqa: E402
import serve_stdio  # noqa: E402

CONFIG = DeploymentConfig(family="pdf", platform="net")
PROVENANCE = Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5)


def _publish(store: GenerationManifestStore) -> None:
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


@pytest.fixture
def manifests(tmp_path: Path) -> Path:
    directory = tmp_path / "manifests"
    directory.mkdir()
    _publish(GenerationManifestStore(directory))
    return directory


class _RecordedStore:
    """Stands in for GenerationManifestStore so nothing is created on disk; records its root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)


class _Captured(Exception):
    pass


def _store_handed_to_create_server(
    monkeypatch: pytest.MonkeyPatch, module: Any, build: Any, replace_store_class: bool
) -> Any:
    seen: list[Any] = []

    def fake_create_server(config: DeploymentConfig, store: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(store)
        raise _Captured

    monkeypatch.setattr(module, "create_server", fake_create_server)
    if replace_store_class:
        monkeypatch.setattr(module, "GenerationManifestStore", _RecordedStore)
    with pytest.raises(_Captured):
        build()
    (store,) = seen
    return store


def test_stdio_serves_symbols_from_the_directory_named_by_the_variable(
    manifests: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(serve_http.MANIFESTS_DIR_ENV, str(manifests))
    server = serve_stdio.build_stdio_server(CONFIG)

    async def call() -> str:
        async with Client(server) as client:
            result = await client.call_tool("search_symbols", {"query": "Document"})
            assert not result.is_error
            return json.dumps(result.model_dump(by_alias=True, exclude_none=True))

    assert "Document" in anyio.run(call)


def test_build_app_with_the_variable_set_answers_readyz_from_that_directory(
    manifests: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv(serve_http.MANIFESTS_DIR_ENV, str(manifests))
    app = serve_http.build_app(CONFIG, allowed_origins=[])
    with TestClient(app) as client:
        assert client.get(serve_http.READYZ_PATH).status_code == 200


def test_build_app_store_root_is_the_variable_directory(
    manifests: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(serve_http.MANIFESTS_DIR_ENV, str(manifests))
    store = _store_handed_to_create_server(
        monkeypatch, serve_http, lambda: serve_http.build_app(CONFIG, allowed_origins=[]), False
    )
    assert isinstance(store, GenerationManifestStore)
    assert store.root == manifests


def test_build_stdio_server_store_root_is_the_variable_directory(
    manifests: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(serve_http.MANIFESTS_DIR_ENV, str(manifests))
    store = _store_handed_to_create_server(
        monkeypatch, serve_stdio, lambda: serve_stdio.build_stdio_server(CONFIG), False
    )
    assert store.root == manifests


def test_an_explicit_manifests_dir_argument_wins_over_the_variable(
    manifests: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(serve_http.MANIFESTS_DIR_ENV, str(tmp_path / "elsewhere"))
    store = _store_handed_to_create_server(
        monkeypatch,
        serve_http,
        lambda: serve_http.build_app(CONFIG, allowed_origins=[], manifests_dir=manifests),
        False,
    )
    assert store.root == manifests


def test_with_the_variable_unset_the_store_root_is_the_default_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(serve_http.MANIFESTS_DIR_ENV, raising=False)
    http_store = _store_handed_to_create_server(
        monkeypatch, serve_http, lambda: serve_http.build_app(CONFIG, allowed_origins=[]), True
    )
    stdio_store = _store_handed_to_create_server(
        monkeypatch, serve_stdio, lambda: serve_stdio.build_stdio_server(CONFIG), True
    )
    assert http_store.root == Path("/data/manifests")
    assert stdio_store.root == Path("/data/manifests")


def test_an_injected_store_wins_over_the_variable(
    manifests: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(serve_http.MANIFESTS_DIR_ENV, str(tmp_path / "ignored"))
    injected = GenerationManifestStore(manifests)
    http_store = _store_handed_to_create_server(
        monkeypatch,
        serve_http,
        lambda: serve_http.build_app(CONFIG, injected, allowed_origins=[]),
        False,
    )
    stdio_store = _store_handed_to_create_server(
        monkeypatch, serve_stdio, lambda: serve_stdio.build_stdio_server(CONFIG, injected), False
    )
    assert http_store is injected
    assert stdio_store is injected


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "x", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_color_message_is_dropped_and_other_extras_are_kept() -> None:
    esc = chr(27)
    record = _record(color_message=f"{esc}[32mINFO{esc}[0m x", tool="lookup")
    line = serve_http.JsonLogFormatter().format(record)
    payload = json.loads(line)
    assert payload["tool"] == "lookup"
    assert "color_message" not in payload
    assert payload["message"] == "x"


def test_a_formatted_line_with_color_message_contains_no_escape_character() -> None:
    esc = chr(27)
    record = _record(color_message=f"{esc}[32mINFO{esc}[0m x")
    line = serve_http.JsonLogFormatter().format(record)
    assert esc not in line
    assert "\\u001b" not in line


def test_a_record_without_color_message_formats_as_before() -> None:
    record = _record(tool="lookup", outcome="ok")
    payload = json.loads(serve_http.JsonLogFormatter().format(record))
    assert set(payload) == {"ts", "level", "logger", "message", "tool", "outcome"}
    assert payload["level"] == "INFO"
    assert payload["logger"] == "uvicorn.error"
    assert payload["tool"] == "lookup"
    assert payload["outcome"] == "ok"


def test_dropped_log_attrs_names_color_message() -> None:
    assert serve_http.DROPPED_LOG_ATTRS == frozenset({"color_message"})
