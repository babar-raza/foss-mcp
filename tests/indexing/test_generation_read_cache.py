"""TC-389: a parsed generation is cached in process, keyed by the file's identity.

Offline and deterministic: the number of parses is counted by wrapping the store's own
``_read_json``, never by timing anything.
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing.generation_manifest import (
    GenerationManifestStore,
    ManifestImmutableError,
    UnknownGenerationError,
    build_manifest,
    publish,
    rollback,
)

SCOPE = "pdf::net::self_extracted"


def _payload(label: str = "a") -> dict[str, Any]:
    return {"lexical_index": {"documents": {label: {"text": label}}}}


def _manifest(version: str, label: str = "a"):
    return build_manifest("pdf", "net", "self_extracted", version, _payload(label))


@pytest.fixture
def store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Names of every generation file ``_read_json`` actually parsed."""
    seen: list[str] = []
    original = GenerationManifestStore._read_json

    def counting(path: Path) -> dict[str, Any] | None:
        if path.name.startswith("generation__") and path.exists():
            seen.append(path.name)
        return original(path)

    monkeypatch.setattr(GenerationManifestStore, "_read_json", staticmethod(counting))
    return seen


def test_cached_reader_exists_by_exact_name() -> None:
    assert callable(getattr(GenerationManifestStore, "_cached_generation_document", None))


def test_two_reads_of_an_unchanged_generation_parse_once(
    store: GenerationManifestStore, parses: list[str]
) -> None:
    manifest = _manifest("v1")
    store.write_generation(manifest)
    parses.clear()
    first = store.read_generation(SCOPE, manifest.generation_id)
    second = store.read_generation(SCOPE, manifest.generation_id)
    assert first == second
    assert len(parses) == 1


def test_many_reads_parse_once(store: GenerationManifestStore, parses: list[str]) -> None:
    manifest = _manifest("v1")
    store.write_generation(manifest)
    parses.clear()
    for _ in range(25):
        store.read_generation(SCOPE, manifest.generation_id)
    assert len(parses) == 1


def test_read_returns_the_written_content(store: GenerationManifestStore) -> None:
    manifest = _manifest("v1", "zeta")
    store.write_generation(manifest)
    read = store.read_generation(SCOPE, manifest.generation_id)
    assert read.payload == _payload("zeta")
    assert read.generation_id == manifest.generation_id


def test_a_rewritten_file_is_re_read(store: GenerationManifestStore, parses: list[str]) -> None:
    manifest = _manifest("v1", "old")
    path = store.write_generation(manifest)
    assert store.read_generation(SCOPE, manifest.generation_id).payload == _payload("old")
    stat = path.stat()
    replacement = _manifest("v1", "new").to_json_dict()
    path.write_text(json.dumps(replacement), encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    parses.clear()
    assert store.read_generation(SCOPE, manifest.generation_id).payload == _payload("new")
    assert len(parses) == 1


def test_a_same_size_rewrite_with_a_new_mtime_is_re_read(
    store: GenerationManifestStore, parses: list[str]
) -> None:
    manifest = _manifest("v1", "aaaa")
    path = store.write_generation(manifest)
    store.read_generation(SCOPE, manifest.generation_id)
    stat = path.stat()
    original_text = path.read_bytes().decode("utf-8")
    path.write_bytes(original_text.replace("aaaa", "bbbb").encode("utf-8"))
    assert path.stat().st_size == stat.st_size
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 7_000_000_000))
    parses.clear()
    assert store.read_generation(SCOPE, manifest.generation_id).payload == _payload("bbbb")
    assert len(parses) == 1


def test_the_cache_never_exceeds_eight_entries(store: GenerationManifestStore) -> None:
    manifests = [_manifest(f"v{i}") for i in range(14)]
    for manifest in manifests:
        store.write_generation(manifest)
    for manifest in manifests:
        store.read_generation(SCOPE, manifest.generation_id)
        assert len(store._generation_cache) <= 8
    assert len(store._generation_cache) == 8


def test_the_least_recently_used_entry_is_evicted(store: GenerationManifestStore, parses: list[str]) -> None:
    manifests = [_manifest(f"v{i}") for i in range(9)]
    for manifest in manifests:
        store.write_generation(manifest)
    for manifest in manifests[:8]:
        store.read_generation(SCOPE, manifest.generation_id)
    # touch v0 so v1 is now the least recently used, then overflow with v8
    store.read_generation(SCOPE, manifests[0].generation_id)
    store.read_generation(SCOPE, manifests[8].generation_id)
    parses.clear()
    store.read_generation(SCOPE, manifests[0].generation_id)
    assert parses == []
    store.read_generation(SCOPE, manifests[1].generation_id)
    assert len(parses) == 1


def test_a_missing_file_is_never_cached(store: GenerationManifestStore) -> None:
    manifest = _manifest("v1")
    with pytest.raises(UnknownGenerationError):
        store.read_generation(SCOPE, manifest.generation_id)
    assert len(store._generation_cache) == 0
    store.write_generation(manifest)
    assert store.read_generation(SCOPE, manifest.generation_id).generation_id == manifest.generation_id


def test_the_cache_is_per_store_instance(tmp_path: Path, parses: list[str]) -> None:
    root = tmp_path / "shared"
    first = GenerationManifestStore(root)
    second = GenerationManifestStore(root)
    manifest = _manifest("v1")
    first.write_generation(manifest)
    parses.clear()
    first.read_generation(SCOPE, manifest.generation_id)
    second.read_generation(SCOPE, manifest.generation_id)
    assert len(parses) == 2
    assert first._generation_cache is not second._generation_cache


def test_a_rollback_is_visible_on_the_next_read_active(store: GenerationManifestStore) -> None:
    one, two = _manifest("v1", "one"), _manifest("v2", "two")
    lease = store.acquire_lease(SCOPE, "w", one.generation_id)
    publish(store, SCOPE, None, one, lease)
    lease = store.acquire_lease(SCOPE, "w", two.generation_id)
    publish(store, SCOPE, one.generation_id, two, lease)
    assert store.read_active(SCOPE) == two.generation_id
    store.read_generation(SCOPE, two.generation_id)
    lease = store.acquire_lease(SCOPE, "w", one.generation_id)
    rollback(store, SCOPE, two.generation_id, one.generation_id, lease)
    assert store.read_active(SCOPE) == one.generation_id
    assert store.read_generation(SCOPE, store.read_active(SCOPE) or "").payload == _payload("one")


def test_the_immutability_check_still_rejects_different_content(store: GenerationManifestStore) -> None:
    store.write_generation(_manifest("v1", "one"))
    store.read_generation(SCOPE, _manifest("v1").generation_id)
    with pytest.raises(ManifestImmutableError):
        store.write_generation(_manifest("v1", "other"))


def test_rewriting_identical_content_is_still_a_no_op(store: GenerationManifestStore) -> None:
    manifest = _manifest("v1", "one")
    path = store.write_generation(manifest)
    store.read_generation(SCOPE, manifest.generation_id)
    assert store.write_generation(manifest) == path


def test_the_immutability_check_compares_against_the_real_file_not_the_cache(
    store: GenerationManifestStore,
) -> None:
    manifest = _manifest("v1", "one")
    path = store.write_generation(manifest)
    store.read_generation(SCOPE, manifest.generation_id)  # now cached
    path.write_text(json.dumps(_manifest("v1", "tampered").to_json_dict()), encoding="utf-8")
    with pytest.raises(ManifestImmutableError):
        store.write_generation(manifest)


def test_concurrent_reads_from_eight_threads_return_equal_documents(
    store: GenerationManifestStore,
) -> None:
    manifest = _manifest("v1", "shared")
    store.write_generation(manifest)
    barrier = threading.Barrier(8)

    def read() -> Any:
        barrier.wait(timeout=10)
        return store.read_generation(SCOPE, manifest.generation_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [f.result(timeout=30) for f in [pool.submit(read) for _ in range(8)]]
    assert all(result == results[0] for result in results)
    assert results[0].payload == _payload("shared")
    assert len(store._generation_cache) == 1


def test_a_corrupt_file_still_raises_and_is_not_cached(store: GenerationManifestStore) -> None:
    manifest = _manifest("v1")
    path = store.write_generation(manifest)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        store.read_generation(SCOPE, manifest.generation_id)
    assert len(store._generation_cache) == 0


def test_read_active_stays_uncached(store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest("v1")
    lease = store.acquire_lease(SCOPE, "w", manifest.generation_id)
    publish(store, SCOPE, None, manifest, lease)
    calls: list[str] = []
    original = GenerationManifestStore._read_json

    def counting(path: Path) -> dict[str, Any] | None:
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr(GenerationManifestStore, "_read_json", staticmethod(counting))
    store.read_active(SCOPE)
    store.read_active(SCOPE)
    assert calls.count("active_pointer.json") == 2
