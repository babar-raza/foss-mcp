"""infra.ingest's first real test coverage (TC-060).

Before this card, infra/ingest.py had zero tests anywhere in the repo and never called
``foss_mcp.normalization.citation``'s validate_document/citable_chunks (TC-014) at all - a real
generation could carry an unresolved or fabricated claim and it would be indexed and served
exactly like a verified one. These tests cover both things at once: (1) ``_load_chunks`` and
``main()``'s actual publish behavior, against a REAL ``GenerationManifestStore`` in ``tmp_path``
and the REAL ``DeterministicEmbeddingProvider`` (never a mock of either); (2) the new
``--api-surface`` wiring - omitted, publish behavior is unchanged; passed, a chunk carrying an
unresolvable claim (an anchor naming a symbol absent from the fixture, or a numeric claim
contradicting the fixture's real counts) is excluded from what actually gets published, verified
by reading the published generation's own content back, not merely a process exit code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import infra.ingest as ingest
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

EMBEDDING_PROVIDER_SPEC = "tests.indexing.test_index_writers:DeterministicEmbeddingProvider"
SCOPE = "pdf::net::self_extracted"

_PROVENANCE = {
    "repository": "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
    "commit": "b717287" * 5,
    "path": "api_surface.json",
}


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


def _write_chunks_fixture(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"chunks": entries}), encoding="utf-8")


def _write_api_surface_fixture(path: Path) -> None:
    """A small, real ``symbol_index_from_api_surface``/``known_counts_from_fixture`` input:
    exactly one class, ``Document``, with one method (``Open``) and one property
    (``PageCount``) - enough to resolve ``Document.Open`` and to know a confident count of
    ``classes`` (1) and ``types`` (1), and nothing else.
    """
    payload = {
        "types": [
            {
                "name": "Document",
                "kind": "class_declaration",
                "methods": [{"name": "Open"}],
                "properties": [{"name": "PageCount"}],
            }
        ],
        "type_count": 1,
        "truncated": False,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _base_argv(chunks_path: Path, manifest_store_path: Path) -> list[str]:
    return [
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
        EMBEDDING_PROVIDER_SPEC,
    ]


def test_load_chunks_reads_a_real_fixture_and_returns_correct_chunk_objects(tmp_path: Path) -> None:
    chunks_path = tmp_path / "chunks.json"
    _write_chunks_fixture(
        chunks_path,
        [
            _chunk_entry("Document", "`Document.Open` opens a file."),
            _chunk_entry("Page", "A page carries properties."),
        ],
    )

    chunks = ingest._load_chunks(chunks_path)

    assert len(chunks) == 2
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert chunks[0].section_title == "Document"
    assert chunks[0].text == "`Document.Open` opens a file."
    assert chunks[0].source_kind == "self_extracted"
    assert chunks[0].content_type == "api_surface"
    assert chunks[0].trust_tier == "highest"
    assert chunks[0].provenance == Provenance(**_PROVENANCE)
    assert chunks[0].evidence_refs == (
        f"{_PROVENANCE['repository']}@{_PROVENANCE['commit']}:api_surface.json",
    )
    assert chunks[1].section_title == "Page"
    assert chunks[1].text == "A page carries properties."


def test_main_without_api_surface_publishes_every_loaded_chunk_unchanged(tmp_path: Path, monkeypatch) -> None:
    """Today's behavior, byte-for-byte: with ``--api-surface`` omitted, every chunk
    ``_load_chunks`` produced - including one with an anchor that would NOT resolve against any
    symbol index, and one with a numeric claim no symbol index would corroborate - is published
    as-is, because nothing in this path validates them.
    """
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    _write_chunks_fixture(
        chunks_path,
        [
            _chunk_entry("Document.Open", "`Document.Open` opens a file."),
            _chunk_entry("Document.Nope", "`Document.Nope` does not exist."),
            _chunk_entry("Count", "The API exposes 999 classes."),
        ],
    )

    monkeypatch.setattr(sys, "argv", ["ingest.py", *_base_argv(chunks_path, manifest_store_path)])
    ingest.main()

    store = GenerationManifestStore(manifest_store_path)
    generation_id = store.read_active(SCOPE)
    assert generation_id is not None
    manifest = store.read_generation(SCOPE, generation_id)
    published_texts = {doc["text"] for doc in manifest.payload["lexical_index"]["documents"].values()}
    assert published_texts == {
        "`Document.Open` opens a file.",
        "`Document.Nope` does not exist.",
        "The API exposes 999 classes.",
    }
    assert len(manifest.payload["vector_index"]["points"]) == 3


def test_main_with_api_surface_excludes_unresolvable_claims_from_what_gets_published(
    tmp_path: Path, monkeypatch
) -> None:
    """The card's core wiring: a chunk whose inline code-span anchor resolves against the
    ``--api-surface`` fixture's types IS published; a chunk making an unresolvable claim - an
    anchor naming a symbol absent from the fixture, or a numeric claim contradicting the
    fixture's real counts - is EXCLUDED from what gets published. Verified by reading the
    published generation's actual content back, not merely that the CLI exits 0.
    """
    chunks_path = tmp_path / "chunks.json"
    api_surface_path = tmp_path / "api_surface.json"
    manifest_store_path = tmp_path / "manifests"
    _write_chunks_fixture(
        chunks_path,
        [
            _chunk_entry("Document.Open", "`Document.Open` opens a file."),
            _chunk_entry("Document.Nope", "`Document.Nope` does not exist."),
            _chunk_entry("Count", "The API exposes 999 classes."),
            _chunk_entry("Plain", "Document is the root object of a PDF file."),
        ],
    )
    _write_api_surface_fixture(api_surface_path)

    argv = [*_base_argv(chunks_path, manifest_store_path), "--api-surface", str(api_surface_path)]
    monkeypatch.setattr(sys, "argv", ["ingest.py", *argv])
    ingest.main()

    store = GenerationManifestStore(manifest_store_path)
    generation_id = store.read_active(SCOPE)
    assert generation_id is not None
    manifest = store.read_generation(SCOPE, generation_id)
    published_texts = {doc["text"] for doc in manifest.payload["lexical_index"]["documents"].values()}

    assert published_texts == {
        "`Document.Open` opens a file.",
        "Document is the root object of a PDF file.",
    }, "only the chunk with a resolving anchor and the claim-free chunk should survive"
    assert "`Document.Nope` does not exist." not in published_texts, (
        "an anchor naming a symbol absent from the api-surface fixture must be excluded"
    )
    assert "The API exposes 999 classes." not in published_texts, (
        "a numeric claim contradicting the api-surface fixture's real counts must be excluded"
    )
    assert len(manifest.payload["vector_index"]["points"]) == 2


def test_main_prints_the_published_generation_id(tmp_path: Path, monkeypatch, capsys) -> None:
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    _write_chunks_fixture(chunks_path, [_chunk_entry("Document", "Document is a top-level object.")])

    monkeypatch.setattr(sys, "argv", ["ingest.py", *_base_argv(chunks_path, manifest_store_path)])
    ingest.main()

    store = GenerationManifestStore(manifest_store_path)
    generation_id = store.read_active(SCOPE)
    out = capsys.readouterr().out
    assert out.strip() == f"published {generation_id}"


def test_api_surface_argument_is_optional_and_defaults_to_none(tmp_path: Path, monkeypatch) -> None:
    """``--api-surface`` is NOT required: omitting it must not raise argparse's "required
    argument missing" error, and the CLI's default is ``None`` (the sentinel ``main()`` checks
    to decide whether to run the validation branch at all).
    """
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    _write_chunks_fixture(chunks_path, [_chunk_entry("Document", "Document is a top-level object.")])

    monkeypatch.setattr(sys, "argv", ["ingest.py", *_base_argv(chunks_path, manifest_store_path)])
    # main() must not raise SystemExit (argparse's failure mode for a missing required arg).
    ingest.main()

    store = GenerationManifestStore(manifest_store_path)
    assert store.read_active(SCOPE) is not None


def _embedding_provider_smoke_check() -> None:
    """Guards the test double itself is real, not accidentally shadowed by a mock."""
    provider = DeterministicEmbeddingProvider()
    vectors = provider.embed(["a", "b"])
    assert len(vectors) == 2
    assert len(vectors[0]) == DeterministicEmbeddingProvider.dimension


def test_deterministic_embedding_provider_is_the_real_test_double() -> None:
    _embedding_provider_smoke_check()
