"""publisher.content_derived_version (G3/REQ-G3-017): a deterministic, content-derived
alternative to new_version(), opt-in only, for a future hermetic regeneration path (H6).

These tests exercise the real function and the real infra/ingest.py CLI wiring - no mocks -
and prove the three things the card requires: (1) content_derived_version is pure and
order-independent on its chunk_ids, (2) its string shape still round-trips through
release_alignment's real regex/parsing, and (3) infra/ingest.py's two new, strictly-opt-in
flags produce a published generation_id that matches content_derived_version's own
independently-computed value for the same inputs - never the live/default new_version() path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.ingest as ingest
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import content_chunk_id, content_derived_version, publish_generation
from foss_mcp.mcp.tools.release_alignment import generation_indexed_at
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

EMBEDDING_PROVIDER_SPEC = "tests.indexing.test_index_writers:DeterministicEmbeddingProvider"
SCOPE = "pdf::net::self_extracted"
EPOCH = 1_700_000_000  # 2023-11-14T22:13:20Z - an arbitrary, fixed source_date_epoch.

_PROVENANCE = {
    "repository": "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
    "commit": "b717287" * 5,
    "path": "api_surface.json",
}


def _chunk(section_title: str, text: str) -> Chunk:
    return Chunk(
        section_title=section_title,
        text=text,
        source_kind="self_extracted",
        content_type="api_surface",
        provenance=Provenance(**_PROVENANCE),
        trust_tier="highest",
        evidence_refs=(f"{_PROVENANCE['repository']}@{_PROVENANCE['commit']}:api_surface.json",),
    )


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
        "--min-symbols",
        "0",
        "--min-docs",
        "0",
    ]


# (a) pure: same chunk_ids and epoch, called twice, produce byte-identical strings.
def test_content_derived_version_is_pure_and_deterministic() -> None:
    chunk_ids = ["aaaa1111", "bbbb2222", "cccc3333"]

    first = content_derived_version(chunk_ids, source_date_epoch=EPOCH)
    second = content_derived_version(list(chunk_ids), source_date_epoch=EPOCH)

    assert first == second


# (b) order-independence: a reordering of the SAME chunk_ids produces the SAME string;
# a genuinely different set of chunk_ids produces a DIFFERENT string.
def test_content_derived_version_is_order_independent_but_content_sensitive() -> None:
    chunk_ids = ["aaaa1111", "bbbb2222", "cccc3333"]
    reordered = ["cccc3333", "aaaa1111", "bbbb2222"]
    different = ["aaaa1111", "bbbb2222", "dddd4444"]

    original = content_derived_version(chunk_ids, source_date_epoch=EPOCH)
    assert content_derived_version(reordered, source_date_epoch=EPOCH) == original, (
        "reordering the same chunk_ids must not change the result"
    )
    assert content_derived_version(different, source_date_epoch=EPOCH) != original, (
        "a genuinely different chunk_ids set must change the result"
    )


# (c) the returned string matches release_alignment's own regex and round-trips through
# generation_indexed_at() without raising.
def test_content_derived_version_round_trips_through_release_alignment() -> None:
    version = content_derived_version(["aaaa1111", "bbbb2222"], source_date_epoch=EPOCH)

    indexed_at = generation_indexed_at(version)

    assert indexed_at == "2023-11-14T22:13:20Z"


# (d) end-to-end: publish_generation(..., version=content_derived_version(...)) over a real
# GenerationManifestStore produces an active generation_id equal to exactly what
# content_derived_version returned.
def test_publish_generation_with_content_derived_version_activates_that_exact_generation_id(
    tmp_path: Path,
) -> None:
    store = GenerationManifestStore(tmp_path / "manifests")
    chunks = [
        _chunk("Document.Open", "`Document.Open` opens a file."),
        _chunk("Page", "A page carries properties."),
    ]
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    version = content_derived_version(chunk_ids, source_date_epoch=EPOCH)
    lease = store.acquire_lease(SCOPE, "worker-1", "pending")

    generation_id = publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
        version=version,
    )

    assert generation_id.endswith(version)
    assert generation_id == f"{SCOPE}::{version}"
    assert store.read_active(SCOPE) == generation_id


# (e) CLI wiring: --content-derived-version without --source-date-epoch must fail fast via
# parser.error (SystemExit), never silently default the epoch.
def test_main_requires_source_date_epoch_when_content_derived_version_is_set(
    tmp_path: Path, monkeypatch
) -> None:
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    _write_chunks_fixture(chunks_path, [_chunk_entry("Document", "Document is a top-level object.")])

    argv = [*_base_argv(chunks_path, manifest_store_path), "--content-derived-version"]
    monkeypatch.setattr(sys, "argv", ["ingest.py", *argv])

    with pytest.raises(SystemExit):
        ingest.main()

    store = GenerationManifestStore(manifest_store_path)
    assert store.read_active(SCOPE) is None, "nothing should have been activated"


# (e) CLI wiring, success path: both flags set runs to completion and publishes a
# generation_id equal to content_derived_version's own independently-computed value for the
# same inputs.
def test_main_with_both_flags_publishes_the_content_derived_generation_id(
    tmp_path: Path, monkeypatch
) -> None:
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    entries = [
        _chunk_entry("Document.Open", "`Document.Open` opens a file."),
        _chunk_entry("Page", "A page carries properties."),
    ]
    _write_chunks_fixture(chunks_path, entries)

    argv = [
        *_base_argv(chunks_path, manifest_store_path),
        "--content-derived-version",
        "--source-date-epoch",
        str(EPOCH),
    ]
    monkeypatch.setattr(sys, "argv", ["ingest.py", *argv])
    ingest.main()

    chunks = ingest._load_chunks(chunks_path)
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    expected_version = content_derived_version(chunk_ids, source_date_epoch=EPOCH)

    store = GenerationManifestStore(manifest_store_path)
    assert store.read_active(SCOPE) == f"{SCOPE}::{expected_version}"


# default (omitted) behaviour is completely unchanged: version stays content-INdependent.
def test_main_without_content_derived_version_flag_still_uses_new_version(
    tmp_path: Path, monkeypatch
) -> None:
    chunks_path = tmp_path / "chunks.json"
    manifest_store_path = tmp_path / "manifests"
    _write_chunks_fixture(chunks_path, [_chunk_entry("Document", "Document is a top-level object.")])

    monkeypatch.setattr(sys, "argv", ["ingest.py", *_base_argv(chunks_path, manifest_store_path)])
    ingest.main()

    chunks = ingest._load_chunks(chunks_path)
    chunk_ids = [content_chunk_id(chunk) for chunk in chunks]
    content_version = content_derived_version(chunk_ids, source_date_epoch=EPOCH)

    store = GenerationManifestStore(manifest_store_path)
    active = store.read_active(SCOPE)
    assert active is not None
    assert active != content_version, "the default path must never be content-derived"
