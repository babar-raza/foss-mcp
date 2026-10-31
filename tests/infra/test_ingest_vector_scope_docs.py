"""TC-632 (QV1): ``infra.ingest --vector-scope-docs``.

Runs the REAL ``ingest.main()`` entry point against a REAL ``GenerationManifestStore`` in
``tmp_path`` and a deterministic offline embedding provider (never a mock of either), reading
the stored generation back:

- ``--vector-scope-docs`` threads through to a real ``doc_vector_chunk_filter=is_doc_chunk``
  argument reaching ``publish_generation``, storing vector points ONLY for documentation chunks
  in a SEPARATE ``vector_index_docs`` key, while the lexical index still holds ALL chunks and
  ``vector_index`` is unaffected;
- omitting the flag leaves ``doc_vector_chunk_filter`` unset/``None`` - no ``vector_index_docs``
  key at all, no behaviour change from today.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import infra.ingest as ingest
from foss_mcp.indexing.generation_manifest import GenerationManifestStore

RECORDING_PROVIDER_SPEC = "tests.infra.test_ingest_vector_scope_docs:RecordingEmbeddingProvider"
SCOPE = "pdf::net::self_extracted"

_PROVENANCE = {
    "repository": "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
    "commit": "b717287" * 5,
    "path": "api_surface.json",
}

DOC_ONE = "FQN: Doc: Getting Started\nInstall the package and open your first document."
DOC_TWO = "FQN: Doc: Licensing\nApply a license before calling any API."
EXAMPLE_ONE = "`Document.Open` opens a file.\nExample:\n```csharp\nvar d = Document.Open(path);\n```"
PLAIN_ONE = "Document is the root object of a PDF file."
PLAIN_TWO = "This sentence mentions a Doc in passing, not at the start."


class RecordingEmbeddingProvider:
    """Deterministic, offline, and instantiated by ``ingest`` itself from its import path - so
    it records every ``embed`` input on the CLASS, where the test can read it back."""

    dimension = 8
    calls: list[list[str]] = []

    def embed(self, texts):
        from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

        type(self).calls.append(list(texts))
        return DeterministicEmbeddingProvider().embed(texts)


def _reset_recorded_calls():
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
        _chunk_entry("Getting Started", DOC_ONE),
        _chunk_entry("Document.Open", EXAMPLE_ONE),
        _chunk_entry("Licensing", DOC_TWO),
        _chunk_entry("Misc", PLAIN_TWO),
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
        "--min-symbols",
        "0",
        "--min-docs",
        "0",
        *extra,
    ]


def _run_ingest(monkeypatch, tmp_path: Path, *extra: str):
    _reset_recorded_calls()
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


def test_vector_scope_docs_flag_stores_doc_only_points_in_a_separate_key(tmp_path: Path, monkeypatch) -> None:
    manifest = _run_ingest(monkeypatch, tmp_path, "--vector-scope-docs")

    assert "vector_index_docs" in manifest.payload
    doc_points = manifest.payload["vector_index_docs"]
    assert [p["text"] for p in doc_points["points"]] == [DOC_ONE, DOC_TWO]
    assert doc_points["dimension"] == RecordingEmbeddingProvider.dimension
    assert doc_points["generation_id"] == manifest.generation_id
    # vector_index (the default, unscoped) is unaffected by the new flag.
    assert [p["text"] for p in manifest.payload["vector_index"]["points"]] == [
        PLAIN_ONE,
        DOC_ONE,
        EXAMPLE_ONE,
        DOC_TWO,
        PLAIN_TWO,
    ]
    assert _lexical_texts(manifest) == {PLAIN_ONE, DOC_ONE, EXAMPLE_ONE, DOC_TWO, PLAIN_TWO}
    # the gateway was asked to embed the full chunk set once for vector_index, and again just
    # for the doc chunks for vector_index_docs.
    assert RecordingEmbeddingProvider.calls == [
        [PLAIN_ONE, DOC_ONE, EXAMPLE_ONE, DOC_TWO, PLAIN_TWO],
        [DOC_ONE, DOC_TWO],
    ]


def test_omitting_vector_scope_docs_flag_leaves_doc_vector_chunk_filter_unset(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _run_ingest(monkeypatch, tmp_path)

    assert "vector_index_docs" not in manifest.payload
    assert [p["text"] for p in manifest.payload["vector_index"]["points"]] == [
        PLAIN_ONE,
        DOC_ONE,
        EXAMPLE_ONE,
        DOC_TWO,
        PLAIN_TWO,
    ]
    assert _lexical_texts(manifest) == {PLAIN_ONE, DOC_ONE, EXAMPLE_ONE, DOC_TWO, PLAIN_TWO}
    assert RecordingEmbeddingProvider.calls == [[PLAIN_ONE, DOC_ONE, EXAMPLE_ONE, DOC_TWO, PLAIN_TWO]]


def test_vector_scope_docs_combined_with_vector_scope_examples_keeps_both_indexes_independent(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _run_ingest(monkeypatch, tmp_path, "--vector-scope", "examples", "--vector-scope-docs")

    assert [p["text"] for p in manifest.payload["vector_index"]["points"]] == [EXAMPLE_ONE]
    assert [p["text"] for p in manifest.payload["vector_index_docs"]["points"]] == [DOC_ONE, DOC_TWO]
    assert _lexical_texts(manifest) == {PLAIN_ONE, DOC_ONE, EXAMPLE_ONE, DOC_TWO, PLAIN_TWO}
