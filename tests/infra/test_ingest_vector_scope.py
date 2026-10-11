"""TC-340: ``infra.ingest --vector-scope {all,examples}``.

Runs the REAL ``ingest.main()`` entry point against a REAL ``GenerationManifestStore`` in
``tmp_path`` and a deterministic offline embedding provider (never a mock of either), reading
the stored generation back:

- ``--vector-scope examples`` stores vector points ONLY for chunks carrying the ``Example:``
  marker, while the lexical index still holds ALL chunks;
- the default and ``--vector-scope all`` give every chunk a point, exactly as before this card;
- an invalid scope is refused by argparse (exit 2).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.ingest as ingest
from foss_mcp.indexing.generation_manifest import GenerationManifestStore

RECORDING_PROVIDER_SPEC = "tests.infra.test_ingest_vector_scope:RecordingEmbeddingProvider"
SCOPE = "pdf::net::self_extracted"

_PROVENANCE = {
    "repository": "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
    "commit": "b717287" * 5,
    "path": "api_surface.json",
}

EXAMPLE_ONE = "`Document.Open` opens a file.\nExample:\n```csharp\nvar d = Document.Open(path);\n```"
EXAMPLE_TWO = "`Page.Rotate` rotates a page.\nExample:\n```csharp\npage.Rotate(90);\n```"
PLAIN_ONE = "Document is the root object of a PDF file."
PLAIN_TWO = "A page carries properties, for example a width."
PLAIN_THREE = "Nothing here has a sample block."


class RecordingEmbeddingProvider:
    """Deterministic, offline, and instantiated by ``ingest`` itself from its import path - so
    it records every ``embed`` input on the CLASS, where the test can read it back."""

    dimension = 8
    calls: list[list[str]] = []

    def embed(self, texts):
        from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

        type(self).calls.append(list(texts))
        return DeterministicEmbeddingProvider().embed(texts)


@pytest.fixture(autouse=True)
def _reset_recorded_calls():
    RecordingEmbeddingProvider.calls = []
    yield
    RecordingEmbeddingProvider.calls = []


def _chunk_entry(section_title: str, text: str) -> dict:
    return {
        "section_title": section_title,
        "text": text,
        "source_kind": "self_extracted",
        "content_type": "api_surface",
        "provenance": dict(_PROVENANCE),
        "trust_tier": "highest",
        "evidence_refs": [f"{_PROVENANCE['repository']}@{_PROVENANCE['commit']}:api_surface.json"],
    }


def _write_mixed_chunks(path: Path) -> None:
    entries = [
        _chunk_entry("Document", PLAIN_ONE),
        _chunk_entry("Document.Open", EXAMPLE_ONE),
        _chunk_entry("Page", PLAIN_TWO),
        _chunk_entry("Page.Rotate", EXAMPLE_TWO),
        _chunk_entry("Misc", PLAIN_THREE),
    ]
    path.write_text(json.dumps({"chunks": entries}), encoding="utf-8")


def _argv(chunks_path: Path, manifest_store_path: Path, *extra: str) -> list[str]:
    return [
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
        str(manifest_store_path),
        "--embedding-provider",
        RECORDING_PROVIDER_SPEC,
        *extra,
    ]


def _run_ingest(monkeypatch, tmp_path: Path, *extra: str):
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    _write_mixed_chunks(chunks_path)
    monkeypatch.setattr(sys, "argv", _argv(chunks_path, manifest_store_path, *extra))
    ingest.main()
    store = GenerationManifestStore(manifest_store_path)
    generation_id = store.read_active(SCOPE)
    assert generation_id is not None
    return store.read_generation(SCOPE, generation_id)


def _lexical_texts(manifest) -> set[str]:
    return {doc["text"] for doc in manifest.payload["lexical_index"]["documents"].values()}


def test_examples_scope_stores_vector_points_only_for_marked_chunks_but_lexical_keeps_all(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _run_ingest(monkeypatch, tmp_path, "--vector-scope", "examples")

    vector_index = manifest.payload["vector_index"]
    assert [p["text"] for p in vector_index["points"]] == [EXAMPLE_ONE, EXAMPLE_TWO]
    assert vector_index["dimension"] == RecordingEmbeddingProvider.dimension
    assert vector_index["generation_id"] == manifest.generation_id
    assert _lexical_texts(manifest) == {PLAIN_ONE, EXAMPLE_ONE, PLAIN_TWO, EXAMPLE_TWO, PLAIN_THREE}
    # the gateway saw only the example-bearing texts, in one call.
    assert RecordingEmbeddingProvider.calls == [[EXAMPLE_ONE, EXAMPLE_TWO]]


def test_examples_scope_with_no_example_chunks_publishes_an_empty_vector_index_without_embedding(
    tmp_path: Path, monkeypatch
) -> None:
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    chunks_path.write_text(
        json.dumps({"chunks": [_chunk_entry("Document", PLAIN_ONE), _chunk_entry("Page", PLAIN_TWO)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", _argv(chunks_path, manifest_store_path, "--vector-scope", "examples"))

    ingest.main()

    store = GenerationManifestStore(manifest_store_path)
    generation_id = store.read_active(SCOPE)
    assert generation_id is not None, "an examples-only run with no example chunks must still publish"
    manifest = store.read_generation(SCOPE, generation_id)
    assert manifest.payload["vector_index"]["points"] == []
    assert _lexical_texts(manifest) == {PLAIN_ONE, PLAIN_TWO}
    assert RecordingEmbeddingProvider.calls == []


def test_default_scope_gives_every_chunk_a_vector_point(tmp_path: Path, monkeypatch) -> None:
    manifest = _run_ingest(monkeypatch, tmp_path)

    points = manifest.payload["vector_index"]["points"]
    assert [p["text"] for p in points] == [PLAIN_ONE, EXAMPLE_ONE, PLAIN_TWO, EXAMPLE_TWO, PLAIN_THREE]
    assert _lexical_texts(manifest) == {PLAIN_ONE, EXAMPLE_ONE, PLAIN_TWO, EXAMPLE_TWO, PLAIN_THREE}
    assert RecordingEmbeddingProvider.calls == [[PLAIN_ONE, EXAMPLE_ONE, PLAIN_TWO, EXAMPLE_TWO, PLAIN_THREE]]


def test_explicit_all_scope_gives_every_chunk_a_vector_point(tmp_path: Path, monkeypatch) -> None:
    manifest = _run_ingest(monkeypatch, tmp_path, "--vector-scope", "all")

    points = manifest.payload["vector_index"]["points"]
    assert [p["text"] for p in points] == [PLAIN_ONE, EXAMPLE_ONE, PLAIN_TWO, EXAMPLE_TWO, PLAIN_THREE]
    assert _lexical_texts(manifest) == {PLAIN_ONE, EXAMPLE_ONE, PLAIN_TWO, EXAMPLE_TWO, PLAIN_THREE}


def test_an_invalid_vector_scope_is_refused_by_argparse_with_exit_2(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    _write_mixed_chunks(chunks_path)
    monkeypatch.setattr(sys, "argv", _argv(chunks_path, manifest_store_path, "--vector-scope", "bogus"))

    with pytest.raises(SystemExit) as excinfo:
        ingest.main()

    assert excinfo.value.code == 2
    assert "--vector-scope" in capsys.readouterr().err
    assert GenerationManifestStore(manifest_store_path).read_active(SCOPE) is None, "nothing may be published"
    assert RecordingEmbeddingProvider.calls == []
