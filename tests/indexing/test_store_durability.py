"""TC-529: store hardening and rollback validation (REQ-G3-008, findings GRR-07, GRR-09, GRR-14, GRR-08).

An independent review measured, against the real store, that the lease and CAS pair was unlocked (two
concurrent publishers both held fencing token 2), that nothing was fsynced, that nothing removed orphans
or old generations, that the pointer was read whole on every probe, and that ``rollback`` accepted a
generation id of another product when a file with that version happened to exist. These tests pin the
repaired contract offline, on ``tmp_path`` only, with threads rather than processes and no timing in any
assertion: where a test needs a lock to be held while another caller waits, the waiting caller's sleep is
replaced by a function that inspects the store and then releases the lock.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import types
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing import generation_manifest as gm
from foss_mcp.indexing.generation_manifest import (
    LOCK_NAME,
    POINTER_MAX_BYTES,
    GenerationManifest,
    GenerationManifestError,
    GenerationManifestStore,
    ManifestValidationError,
    PublishPolicy,
    StaleFencingTokenError,
    UnknownGenerationError,
    build_manifest,
    check_generation_shape,
    publish,
    rollback,
)
from foss_mcp.indexing.hashing_embedding_provider import HashingEmbeddingProvider
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance

FAMILY, PLATFORM, SOURCE_KIND = "pdf", "go", "self_extracted"
SCOPE = "::".join((FAMILY, PLATFORM, SOURCE_KIND))
SCOPE_DIR_NAME = "__".join((FAMILY, PLATFORM, SOURCE_KIND))
BOM = b"\xef\xbb\xbf"


@pytest.fixture
def store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def _scope_dir(store: GenerationManifestStore, scope: str = SCOPE) -> Path:
    return store.root / "__".join(scope.split("::"))


def _payload(text: str = "hello world", documents: int = 1) -> dict[str, Any]:
    return {
        "lexical_index": {
            "documents": {
                f"d{index}": {"chunk_id": f"c{index}", "tokens": ["hello", "world"], "text": text}
                for index in range(documents)
            },
            "doc_freq": {"hello": documents, "world": documents},
        }
    }


def _manifest(version: str, payload: dict[str, Any] | None = None, scope_parts=None) -> GenerationManifest:
    family, platform, source_kind = scope_parts or (FAMILY, PLATFORM, SOURCE_KIND)
    return build_manifest(family, platform, source_kind, version, payload or _payload())


def _publish_version(
    store: GenerationManifestStore, version: str, payload: dict[str, Any] | None = None
) -> str:
    manifest = _manifest(version, payload)
    expected = store.read_active(SCOPE)
    lease = store.acquire_lease(SCOPE, "test", manifest.generation_id)
    return publish(store, SCOPE, expected, manifest, lease)


def _gid(version: str) -> str:
    return f"{SCOPE}::{version}"


def _generation_file_names(store: GenerationManifestStore) -> list[str]:
    return sorted(path.name for path in _scope_dir(store).glob("generation__*.json"))


def _hold_lock(store: GenerationManifestStore, scope: str = SCOPE):
    """Take the scope lock in this thread and return the context manager that releases it."""
    context = store.scope_lock(scope)
    context.__enter__()
    return context


def _release(context) -> None:
    context.__exit__(None, None, None)


def _fake_clock(sleep: Callable[[float], None]) -> types.SimpleNamespace:
    return types.SimpleNamespace(monotonic=time.monotonic, time=time.time, sleep=sleep)


# --------------------------------------------------------------------------------------
# The exclusive scope lock
# --------------------------------------------------------------------------------------


def test_concurrent_lease_acquirers_get_unique_tokens(tmp_path: Path) -> None:
    store = GenerationManifestStore(tmp_path / "manifests")
    threads = 8
    for round_number in range(10):
        scope = f"pdf::go::round{round_number}"
        barrier = threading.Barrier(threads)

        def acquire(index: int, scope: str = scope, barrier: threading.Barrier = barrier) -> int:
            barrier.wait(timeout=60)
            return store.acquire_lease(scope, f"holder-{index}", "pending").fencing_token

        with ThreadPoolExecutor(max_workers=threads) as pool:
            tokens = [
                future.result(timeout=120) for future in [pool.submit(acquire, i) for i in range(threads)]
            ]
        assert sorted(tokens) == list(range(1, threads + 1)), f"round {round_number}: {sorted(tokens)}"
        assert store.current_fencing_token(scope) == threads


def test_acquire_lease_waits_while_the_scope_lock_is_held(
    store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = _hold_lock(store)
    observed: list[tuple[bool, bool]] = []

    def sleep(_seconds: float) -> None:
        # Called only because acquire_lease could not take the lock: nothing may have been written yet.
        observed.append(
            ((_scope_dir(store) / LOCK_NAME).exists(), (_scope_dir(store) / "lease_state.json").exists())
        )
        if len(observed) == 3:
            _release(held)

    monkeypatch.setattr(gm, "time", _fake_clock(sleep))
    lease = store.acquire_lease(SCOPE, "waiter", "pending")

    assert len(observed) == 3
    assert all(lock_present and not lease_written for lock_present, lease_written in observed)
    assert lease.fencing_token == 1
    assert store.current_fencing_token(SCOPE) == 1


def test_cas_activate_waits_while_the_scope_lock_is_held(
    store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest("v1")
    lease = store.acquire_lease(SCOPE, "w", manifest.generation_id)
    generation_id = store.write_and_validate(manifest)
    held = _hold_lock(store)
    observed: list[bool] = []

    def sleep(_seconds: float) -> None:
        observed.append((_scope_dir(store) / "active_pointer.json").exists())
        if len(observed) == 2:
            _release(held)

    monkeypatch.setattr(gm, "time", _fake_clock(sleep))
    assert store.cas_activate(SCOPE, None, generation_id, lease) == generation_id

    assert observed == [False, False]
    assert store.read_active(SCOPE) == generation_id


def test_lock_timeout_raises_a_manifest_error(tmp_path: Path) -> None:
    store = GenerationManifestStore(tmp_path / "manifests", lock_timeout=0.05)
    held = _hold_lock(store)
    try:
        with pytest.raises(GenerationManifestError, match="publish lock"):
            store.acquire_lease(SCOPE, "waiter", "pending")
        assert not (_scope_dir(store) / "lease_state.json").exists()
    finally:
        _release(held)
    assert store.acquire_lease(SCOPE, "after", "pending").fencing_token == 1


def test_a_stale_lock_is_taken_over(store: GenerationManifestStore) -> None:
    scope_dir = _scope_dir(store)
    scope_dir.mkdir(parents=True)
    lock = scope_dir / LOCK_NAME
    lock.write_text(json.dumps({"pid": 1, "ts": "crashed"}), encoding="utf-8")
    long_ago = time.time() - 3600
    os.utime(lock, (long_ago, long_ago))

    lease = store.acquire_lease(SCOPE, "survivor", "pending")

    assert lease.fencing_token == 1
    assert not lock.exists()


def test_a_fresh_lock_is_not_taken_over(tmp_path: Path) -> None:
    store = GenerationManifestStore(tmp_path / "manifests", lock_timeout=0.05)
    scope_dir = _scope_dir(store)
    scope_dir.mkdir(parents=True)
    lock = scope_dir / LOCK_NAME
    lock.write_text("{}", encoding="utf-8")

    with pytest.raises(GenerationManifestError):
        store.acquire_lease(SCOPE, "waiter", "pending")

    assert lock.exists()


def test_no_lock_file_remains_after_publish_or_failure(store: GenerationManifestStore) -> None:
    _publish_version(store, "v1")
    assert sorted(path.name for path in _scope_dir(store).iterdir()) == [
        "active_pointer.json",
        "generation__v1.json",
        "lease_state.json",
    ]

    stale = store.acquire_lease(SCOPE, "old", "pending")
    store.acquire_lease(SCOPE, "new", "pending")
    with pytest.raises(StaleFencingTokenError):
        store.cas_activate(SCOPE, _gid("v1"), _gid("v1"), stale)
    assert not (_scope_dir(store) / LOCK_NAME).exists()

    with pytest.raises(RuntimeError, match="boom"):
        with store.scope_lock(SCOPE):
            raise RuntimeError("boom")
    assert not (_scope_dir(store) / LOCK_NAME).exists()


# --------------------------------------------------------------------------------------
# Durability
# --------------------------------------------------------------------------------------


def test_atomic_write_fsyncs_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def recording_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def recording_replace(src: Any, dst: Any) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "replace", recording_replace)

    target = tmp_path / "doc.json"
    GenerationManifestStore._atomic_write_json(target, {"a": 1})

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert "fsync" in events and "replace" in events
    assert events.index("fsync") < events.index("replace"), (
        "the file must be synced before it is renamed into place"
    )


def test_atomic_write_fsyncs_the_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Path, bool]] = []
    target = tmp_path / "doc.json"

    def recording(path: Path) -> None:
        seen.append((path, target.exists()))

    monkeypatch.setattr(GenerationManifestStore, "_fsync_directory", staticmethod(recording))
    GenerationManifestStore._atomic_write_json(target, {"a": 1})

    assert seen == [(tmp_path, True)], "the directory is synced once, after the rename"


def test_real_fsync_directory_never_raises(tmp_path: Path) -> None:
    GenerationManifestStore._fsync_directory(tmp_path)
    GenerationManifestStore._fsync_directory(tmp_path / "does-not-exist")
    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    GenerationManifestStore._fsync_directory(tmp_path / "a-file")


# --------------------------------------------------------------------------------------
# Bounded and tolerant reads
# --------------------------------------------------------------------------------------


def test_pointer_with_bom_is_read(store: GenerationManifestStore) -> None:
    _publish_version(store, "v1")
    pointer = _scope_dir(store) / "active_pointer.json"
    pointer.write_bytes(BOM + json.dumps({"generation_id": _gid("v1")}).encode("utf-8"))
    assert store.read_active(SCOPE) == _gid("v1")


def test_generation_file_with_bom_is_read(store: GenerationManifestStore) -> None:
    _publish_version(store, "v1")
    path = _scope_dir(store) / "generation__v1.json"
    path.write_bytes(BOM + path.read_bytes())
    fresh = GenerationManifestStore(store.root)
    assert fresh.read_generation(SCOPE, _gid("v1")).payload == _payload()
    assert fresh.generation_exists(SCOPE, _gid("v1")) is True
    assert fresh.list_generations(SCOPE) == [_gid("v1")]


def _pointer_bytes(total: int) -> bytes:
    base = len(json.dumps({"generation_id": _gid("v1"), "pad": ""}).encode("utf-8"))
    document = json.dumps({"generation_id": _gid("v1"), "pad": "x" * (total - base)}).encode("utf-8")
    assert len(document) == total
    return document


def test_oversized_pointer_is_rejected_without_parsing_it(
    store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_version(store, "v1")
    (_scope_dir(store) / "active_pointer.json").write_bytes(_pointer_bytes(POINTER_MAX_BYTES + 1))
    (_scope_dir(store) / "lease_state.json").write_bytes(_pointer_bytes(POINTER_MAX_BYTES + 1))

    def parsed(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an oversized file must be rejected before it is parsed")

    monkeypatch.setattr(json, "loads", parsed)
    with pytest.raises(GenerationManifestError, match="64 KiB"):
        store.read_active(SCOPE)
    with pytest.raises(GenerationManifestError, match="64 KiB"):
        store.current_fencing_token(SCOPE)


def test_read_json_with_a_cap_reads_at_most_one_byte_over_it(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    path.write_bytes(_pointer_bytes(POINTER_MAX_BYTES + 1))
    with pytest.raises(GenerationManifestError, match="64 KiB"):
        GenerationManifestStore._read_json(path, max_bytes=POINTER_MAX_BYTES)
    assert GenerationManifestStore._read_json(path)["generation_id"] == _gid("v1")  # type: ignore[index]
    assert GenerationManifestStore._read_json(tmp_path / "missing.json", max_bytes=10) is None


def test_pointer_at_the_limit_is_read(store: GenerationManifestStore) -> None:
    _publish_version(store, "v1")
    (_scope_dir(store) / "active_pointer.json").write_bytes(_pointer_bytes(POINTER_MAX_BYTES))
    assert store.read_active(SCOPE) == _gid("v1")


def test_a_large_generation_file_is_still_read(store: GenerationManifestStore) -> None:
    big = _payload(text="word " * 40_000, documents=3)
    _publish_version(store, "v1", big)
    path = _scope_dir(store) / "generation__v1.json"
    assert path.stat().st_size > 5 * 64 * 1024
    fresh = GenerationManifestStore(store.root)
    assert fresh.read_generation(SCOPE, _gid("v1")).payload == big
    assert fresh.generation_exists(SCOPE, _gid("v1")) is True


# --------------------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------------------

NOW = 1_800_000_000.0


def test_prune_keeps_active_previous_and_the_last_three(store: GenerationManifestStore) -> None:
    for number in range(1, 9):
        _publish_version(store, f"v{number}")
    store.append_publish_log(
        SCOPE, {"event": "published", "generation_id": _gid("v8"), "previous_active": _gid("v3")}
    )

    removed = store.prune(SCOPE, now=time.time() + 7200)

    assert sorted(removed) == [f"generation__v{n}.json" for n in (1, 2, 4, 5)]
    assert store.list_generations(SCOPE) == [_gid("v8"), _gid("v7"), _gid("v6"), _gid("v3")]
    assert store.read_active(SCOPE) == _gid("v8")
    pruned = [entry for entry in store.read_publish_log(SCOPE) if entry["event"] == "pruned"]
    assert len(pruned) == 1 and sorted(pruned[0]["removed"]) == sorted(removed)
    assert store.prune(SCOPE, now=time.time() + 7200) == []
    assert [e for e in store.read_publish_log(SCOPE) if e["event"] == "pruned"] == pruned


def test_prune_never_removes_active_or_previous_even_when_old(store: GenerationManifestStore) -> None:
    for number in range(1, 7):
        _publish_version(store, f"v{number}")
    lease = store.acquire_lease(SCOPE, "rollback", "pending")
    rollback(store, SCOPE, _gid("v6"), _gid("v1"), lease)
    for name in _generation_file_names(store):
        os.utime(_scope_dir(store) / name, (NOW - 86400, NOW - 86400))

    removed = store.prune(SCOPE, keep_last=0, now=NOW)

    assert sorted(removed) == [f"generation__v{n}.json" for n in (2, 3, 4, 5)]
    assert _generation_file_names(store) == ["generation__v1.json", "generation__v6.json"]
    assert store.read_active(SCOPE) == _gid("v1")


def test_prune_never_removes_a_protected_generation(store: GenerationManifestStore) -> None:
    for number in range(1, 6):
        _publish_version(store, f"v{number}")
    removed = store.prune(SCOPE, keep_last=1, protect=(_gid("v2"), None), now=time.time() + 7200)
    assert sorted(removed) == ["generation__v1.json", "generation__v3.json", "generation__v4.json"]
    assert store.list_generations(SCOPE) == [_gid("v5"), _gid("v2")]


def test_prune_removes_stale_tmp_files_only_after_an_hour(store: GenerationManifestStore) -> None:
    _publish_version(store, "v1")
    scope_dir = _scope_dir(store)
    for name, age in ((".tmp-old.json", 3601), (".tmp-edge.json", 3599), (".tmp-new.json", 5)):
        (scope_dir / name).write_text("{}", encoding="utf-8")
        os.utime(scope_dir / name, (NOW - age, NOW - age))
    (scope_dir / "notes.txt").write_text("keep", encoding="utf-8")
    os.utime(scope_dir / "notes.txt", (NOW - 86400, NOW - 86400))

    removed = store.prune(SCOPE, now=NOW)

    assert removed == [".tmp-old.json"]
    remaining = sorted(path.name for path in scope_dir.iterdir())
    assert ".tmp-edge.json" in remaining and ".tmp-new.json" in remaining and "notes.txt" in remaining
    assert "active_pointer.json" in remaining and "generation__v1.json" in remaining


def test_prune_skips_young_generation_files(store: GenerationManifestStore) -> None:
    for number in range(1, 7):
        _publish_version(store, f"v{number}")
    assert store.prune(SCOPE) == []
    assert len(_generation_file_names(store)) == 6
    removed = store.prune(SCOPE, generation_min_age_seconds=0.0)
    assert sorted(removed) == [f"generation__v{n}.json" for n in (1, 2, 3)]


def test_prune_of_a_scope_that_does_not_exist_removes_nothing(store: GenerationManifestStore) -> None:
    assert store.prune(SCOPE) == []
    assert not _scope_dir(store).exists()


def _symbol_chunks(count: int, docs: int = 1) -> list[Chunk]:
    provenance = Provenance(repository="org/repo-a", commit="c" * 40, path="api_surface.json")
    texts = [f"FQN: Sym{i}\nKind: type_spec\nA documented type number {i}." for i in range(count)]
    texts += [f"FQN: Doc: Guide{i}\nA guide section number {i}." for i in range(docs)]
    return [
        Chunk(
            section_title=f"section-{i}",
            text=text,
            source_kind="self_extracted",
            content_type="api_surface",
            provenance=provenance,
            trust_tier="highest",
        )
        for i, text in enumerate(texts)
    ]


def _publish_real(store: GenerationManifestStore, version: str, policy: PublishPolicy | None) -> str:
    expected = store.read_active(SCOPE)
    lease = store.acquire_lease(SCOPE, "test", "pending")
    return publish_generation(
        store,
        family=FAMILY,
        platform=PLATFORM,
        source_kind=SOURCE_KIND,
        expected_active=expected,
        chunks=_symbol_chunks(4),
        embedding_provider=HashingEmbeddingProvider(),
        lease=lease,
        version=version,
        policy=policy,
    )


def test_publish_with_policy_prunes_and_logs(store: GenerationManifestStore) -> None:
    policy = PublishPolicy(generation_min_age_seconds=0.0)
    for number in range(1, 7):
        _publish_real(store, f"v{number}", policy)

    # active v6, the previous v5 and the last three (v6, v5, v4) survive; v1..v3 were removed as they aged out.
    assert store.list_generations(SCOPE) == [_gid("v6"), _gid("v5"), _gid("v4")]
    assert store.read_active(SCOPE) == _gid("v6")
    log = store.read_publish_log(SCOPE)
    assert [entry["event"] for entry in log if entry["event"] == "published"] == ["published"] * 6
    removed = [name for entry in log if entry["event"] == "pruned" for name in entry["removed"]]
    assert sorted(removed) == ["generation__v1.json", "generation__v2.json", "generation__v3.json"]
    assert not (_scope_dir(store) / LOCK_NAME).exists()


def test_publish_with_prune_disabled_keeps_everything(store: GenerationManifestStore) -> None:
    policy = PublishPolicy(prune=False, generation_min_age_seconds=0.0)
    for number in range(1, 7):
        _publish_real(store, f"v{number}", policy)
    assert len(store.list_generations(SCOPE)) == 6
    assert not [entry for entry in store.read_publish_log(SCOPE) if entry["event"] == "pruned"]


def test_publish_with_the_default_policy_keeps_young_generations(store: GenerationManifestStore) -> None:
    for number in range(1, 7):
        _publish_real(store, f"v{number}", PublishPolicy())
    assert len(store.list_generations(SCOPE)) == 6


def test_publish_without_a_policy_never_prunes(store: GenerationManifestStore) -> None:
    scope_dir = _scope_dir(store)
    for number in range(1, 8):
        _publish_version(store, f"v{number}")
    stale = scope_dir / ".tmp-stale.json"
    stale.write_text("{}", encoding="utf-8")
    os.utime(stale, (NOW - 86400, NOW - 86400))
    _publish_version(store, "v8")

    assert len(_generation_file_names(store)) == 8
    assert stale.exists()
    assert not scope_dir.joinpath(gm.PUBLISH_LOG_NAME).exists()


# --------------------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------------------


def test_rollback_refuses_a_foreign_prefix_id_even_if_the_version_exists(
    store: GenerationManifestStore,
) -> None:
    _publish_version(store, "v1")
    foreign = build_manifest("email", "go", "self_extracted", "v2", _payload())
    foreign_store_path = store.write_generation(foreign)
    planted = _scope_dir(store) / "generation__v2.json"
    shutil.copyfile(foreign_store_path, planted)
    foreign_id = "email::go::self_extracted::v2"
    assert store.generation_exists(SCOPE, foreign_id) is True

    lease = store.acquire_lease(SCOPE, "rollback", "pending")
    for validate in (False, True):
        with pytest.raises(UnknownGenerationError):
            rollback(store, SCOPE, _gid("v1"), foreign_id, lease, validate=validate)
    assert store.read_active(SCOPE) == _gid("v1")
    assert not [entry for entry in store.read_publish_log(SCOPE) if entry["event"] == "rollback"]


def _bad_payloads() -> list[tuple[str, dict[str, Any]]]:
    good = _payload()
    no_documents = {"lexical_index": {"documents": {}, "doc_freq": {}}}
    no_tokens = _payload()
    del no_tokens["lexical_index"]["documents"]["d0"]["tokens"]
    empty_text = _payload(text="")
    no_doc_freq = _payload()
    del no_doc_freq["lexical_index"]["doc_freq"]
    assert good["lexical_index"]["documents"]
    return [
        ("no-documents", no_documents),
        ("document-without-tokens", no_tokens),
        ("empty-text", empty_text),
        ("no-doc-freq", no_doc_freq),
        ("no-lexical-index", {"vector_index": {}}),
    ]


@pytest.mark.parametrize(("label", "payload"), _bad_payloads(), ids=[label for label, _ in _bad_payloads()])
def test_rollback_validate_rejects_malformed_payloads(
    store: GenerationManifestStore, label: str, payload: dict[str, Any]
) -> None:
    _publish_version(store, "v1")
    _publish_version(store, "v2")
    store.write_generation(_manifest("v0", payload))  # written but never activated
    lease = store.acquire_lease(SCOPE, "rollback", "pending")

    with pytest.raises(ManifestValidationError):
        rollback(store, SCOPE, _gid("v2"), _gid("v0"), lease, validate=True)

    assert store.read_active(SCOPE) == _gid("v2"), label
    assert not [entry for entry in store.read_publish_log(SCOPE) if entry["event"] == "rollback"]


def test_rollback_validate_rejects_a_file_whose_inside_id_differs(store: GenerationManifestStore) -> None:
    _publish_version(store, "v1")
    _publish_version(store, "v2")
    other = store.write_generation(_manifest("v9"))
    shutil.copyfile(other, _scope_dir(store) / "generation__v7.json")
    lease = store.acquire_lease(SCOPE, "rollback", "pending")
    with pytest.raises(ManifestValidationError):
        rollback(store, SCOPE, _gid("v2"), _gid("v7"), lease, validate=True)
    assert store.read_active(SCOPE) == _gid("v2")


def test_rollback_validate_accepts_a_real_generation(store: GenerationManifestStore) -> None:
    first = _publish_real(store, "v1", None)
    _publish_real(store, "v2", None)
    lease = store.acquire_lease(SCOPE, "rollback", "pending")
    assert rollback(store, SCOPE, _gid("v2"), first, lease, validate=True) == first
    assert store.read_active(SCOPE) == first
    assert check_generation_shape(store.read_generation(SCOPE, first), first) == []


def test_check_generation_shape_names_every_kind_of_problem() -> None:
    assert check_generation_shape(_manifest("v1")) == []
    assert check_generation_shape(_manifest("v1"), _gid("v2")) != []
    broken = {
        "lexical_index": {
            "documents": {"a": "not an object", "b": {"chunk_id": 5, "tokens": "x", "text": 7}},
            "doc_freq": [],
        }
    }
    problems = check_generation_shape(_manifest("v1", broken))
    assert len(problems) >= 5


def test_rollback_without_validate_keeps_the_legacy_contract(store: GenerationManifestStore) -> None:
    legacy = {"lexical_index": {"documents": {"d1": {"text": ""}}}}
    _publish_version(store, "v1", legacy)
    _publish_version(store, "v2", legacy)
    lease = store.acquire_lease(SCOPE, "rollback", "pending")
    assert rollback(store, SCOPE, _gid("v2"), _gid("v1"), lease) == _gid("v1")
    assert store.read_active(SCOPE) == _gid("v1")


def test_rollback_appends_a_log_line(store: GenerationManifestStore) -> None:
    _publish_version(store, "v1")
    _publish_version(store, "v2")
    lease = store.acquire_lease(SCOPE, "rollback", "pending")
    rollback(store, SCOPE, _gid("v2"), _gid("v1"), lease, validate=True)

    entries = [entry for entry in store.read_publish_log(SCOPE) if entry["event"] == "rollback"]
    assert len(entries) == 1
    assert entries[0]["scope"] == SCOPE
    assert entries[0]["generation_id"] == _gid("v1")
    assert entries[0]["previous_active"] == _gid("v2")


def test_list_generations_and_previous_generation(store: GenerationManifestStore) -> None:
    assert store.list_generations(SCOPE) == []
    assert store.previous_generation(SCOPE) is None

    _publish_version(store, "v1")
    assert store.list_generations(SCOPE) == [_gid("v1")]
    assert store.previous_generation(SCOPE) is None

    _publish_version(store, "v2")
    _publish_version(store, "v3")
    assert store.list_generations(SCOPE) == [_gid("v3"), _gid("v2"), _gid("v1")]
    assert store.previous_generation(SCOPE) == _gid("v2")  # no log: the second newest

    store.append_publish_log(SCOPE, {"event": "published", "previous_active": _gid("v1")})
    assert store.previous_generation(SCOPE) == _gid("v1")  # the log wins

    store.append_publish_log(SCOPE, {"event": "pruned", "removed": ["generation__v9.json"]})
    assert store.previous_generation(SCOPE) == _gid("v1")  # other events are ignored

    store.append_publish_log(SCOPE, {"event": "rollback", "previous_active": _gid("v9")})
    assert store.previous_generation(SCOPE) == _gid("v2")  # the logged one no longer exists

    store.append_publish_log(SCOPE, {"event": "rollback", "previous_active": "email::go::self_extracted::v2"})
    assert store.previous_generation(SCOPE) == _gid("v2")  # a foreign id is never offered


def test_list_generations_skips_unreadable_files(store: GenerationManifestStore) -> None:
    _publish_version(store, "v1")
    (_scope_dir(store) / "generation__v2.json").write_text("not json", encoding="utf-8")
    (_scope_dir(store) / "generation__v3.json").write_text('{"no": "id"}', encoding="utf-8")
    assert store.list_generations(SCOPE) == [_gid("v1")]
