"""Tests for the TC-005 generation manifest (plan 7.1 / 18.3, REQ-G0-007 /
REQ-G0-008): an immutable generation, a CAS active pointer scoped to the
full addressable unit, and lease-fenced publication/rollback.

Several of these are NEGATIVE controls by design, per the taskcard:

* a write carrying a stale fencing token is rejected
  (``test_stale_fencing_token_write_is_rejected``)
* a second acquirer fences out the first
  (``test_second_acquirer_fences_out_first``)
* a late publish arriving after a rollback is rejected
  (``test_late_publish_after_rollback_is_rejected``)
* a published manifest cannot be mutated
  (``test_published_manifest_cannot_be_mutated``)
* the active pointer does not advance if validation fails partway
  (``test_active_pointer_does_not_advance_if_validation_fails_partway``)
* generation identity distinguishes two source_kinds within the same
  (family, platform, version)
  (``test_generation_identity_distinguishes_source_kind`` and
  ``test_publishing_one_source_kind_does_not_deactivate_another``)

Every one of these routes its assertion through the module-level
``publish()`` function (or ``rollback()``, which is documented to travel
the identical authority path) rather than around it, so that a falsifier
which guts ``publish``'s body cannot leave these tests accidentally green.

TC-242 moved the empty-generation / >50% regression guard
(``assert_publish_is_safe``, raising ``PublishSafetyError``) from
``infra/ingest.py``'s CLI into ``publish()`` itself, so it is enforced for
every direct caller, not only that CLI. That means every payload published
below now carries a non-empty ``lexical_index.documents`` (via
``_lexical_index``) purely to stay a "healthy" publish as far as THIS guard
is concerned - these tests are otherwise still exercising CAS/fencing
behaviour, not the safety guard, which gets its own dedicated tests near the
bottom of this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import (
    ActivePointerConflictError,
    GenerationKey,
    GenerationManifest,
    GenerationManifestStore,
    ManifestImmutableError,
    ManifestValidationError,
    PublishSafetyError,
    StaleFencingTokenError,
    UnknownGenerationError,
    assert_publish_is_safe,
    build_manifest,
    publish,
    rollback,
)


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def _lexical_index(count: int) -> dict:
    """A minimal, valid ``lexical_index`` payload fragment carrying exactly ``count``
    documents - enough for ``assert_publish_is_safe`` (enforced inside ``publish()``
    itself as of TC-242) to see a real, non-empty document count. Matches
    ``lexical_index_writer.build_lexical_index``'s own shape closely enough for this
    guard's own read (``payload["lexical_index"]["documents"]``), not a stand-in for
    every field a real lexical index carries.
    """
    return {
        "documents": {f"doc-{i}": {"chunk_id": f"chunk-{i}", "tokens": [], "text": ""} for i in range(count)}
    }


def _manifest_with_documents(
    family: str, platform: str, source_kind: str, version: str, count: int, **extra_payload
) -> GenerationManifest:
    payload = {**extra_payload, "lexical_index": _lexical_index(count)}
    return build_manifest(family, platform, source_kind, version, payload=payload)


# ---------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------


def test_generation_identity_distinguishes_source_kind():
    """The full addressable unit is (family, platform, source_kind,
    version). Two generations sharing everything EXCEPT source_kind must
    have different generation_id AND different scope -- omitting
    source_kind from identity is exactly the defect this design exists to
    prevent."""
    api = GenerationKey(family="pdf", platform="net", source_kind="api-reference", version="24.9")
    cookbook = GenerationKey(family="pdf", platform="net", source_kind="cookbook", version="24.9")

    assert api.generation_id != cookbook.generation_id
    assert api.scope != cookbook.scope
    # Sanity: identity really is a function of all four fields, not fewer.
    assert api.family == cookbook.family
    assert api.platform == cookbook.platform
    assert api.version == cookbook.version
    assert api.source_kind != cookbook.source_kind


def test_generation_key_rejects_empty_or_separator_bearing_component():
    with pytest.raises(ManifestValidationError):
        GenerationKey(family="", platform="net", source_kind="api", version="1")
    with pytest.raises(ManifestValidationError):
        GenerationKey(family="pdf::evil", platform="net", source_kind="api", version="1")


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


def test_build_publish_and_read_active_round_trip(tmp_path):
    store = _store(tmp_path)
    gen = _manifest_with_documents("pdf", "net", "api-reference", "1", 1, pages=10)
    lease = store.acquire_lease(gen.scope, "worker-1", gen.generation_id)

    result = publish(store, gen.scope, None, gen, lease)

    assert result == gen.generation_id
    assert store.read_active(gen.scope) == gen.generation_id
    round_tripped = store.read_generation(gen.scope, gen.generation_id)
    assert round_tripped.payload == gen.payload


def test_publish_requires_the_current_expected_active_value(tmp_path):
    """A genuine CAS compare failure (current, valid token; wrong
    ``expected_active``) is a distinct, differently-typed failure from
    fencing -- callers must be able to tell "someone else already moved
    this pointer" apart from "my lease is stale."""
    store = _store(tmp_path)
    gen1 = _manifest_with_documents("pdf", "net", "api-reference", "1", 2)
    gen2 = _manifest_with_documents("pdf", "net", "api-reference", "2", 2)
    lease = store.acquire_lease(gen1.scope, "worker-1", gen1.generation_id)
    publish(store, gen1.scope, None, gen1, lease)

    lease2 = store.acquire_lease(gen1.scope, "worker-1", gen2.generation_id)
    with pytest.raises(ActivePointerConflictError):
        publish(store, gen2.scope, "some-other-generation-entirely", gen2, lease2)
    assert store.read_active(gen1.scope) == gen1.generation_id


# ---------------------------------------------------------------------
# NEGATIVE CONTROL: stale fencing token
# ---------------------------------------------------------------------


def test_stale_fencing_token_write_is_rejected(tmp_path):
    """Re-acquiring a lease for the same scope issues a NEW token; a write
    carrying the OLD token must be rejected by the publish path, and must
    leave the active pointer completely untouched."""
    store = _store(tmp_path)
    gen1 = build_manifest("pdf", "net", "api-reference", "1")
    gen2 = _manifest_with_documents("pdf", "net", "api-reference", "2", 1)

    stale_lease = store.acquire_lease(gen1.scope, "worker-1", gen1.generation_id)
    # Renewal: the SAME holder re-acquires, which still mints a strictly
    # newer token and immediately stales out the one above.
    store.acquire_lease(gen1.scope, "worker-1", gen1.generation_id)

    with pytest.raises(StaleFencingTokenError):
        publish(store, gen2.scope, None, gen2, stale_lease)

    assert store.read_active(gen2.scope) is None


# ---------------------------------------------------------------------
# NEGATIVE CONTROL: second acquirer fences out the first
# ---------------------------------------------------------------------


def test_second_acquirer_fences_out_first(tmp_path):
    """A DIFFERENT acquirer taking the lease for a scope fences out
    whoever held it before, regardless of identity -- the write that
    matters is the token, not who is holding it."""
    store = _store(tmp_path)
    gen = _manifest_with_documents("pdf", "net", "api-reference", "1", 1)

    first = store.acquire_lease(gen.scope, "worker-A", gen.generation_id)
    second = store.acquire_lease(gen.scope, "worker-B", gen.generation_id)
    assert second.fencing_token > first.fencing_token

    with pytest.raises(StaleFencingTokenError):
        publish(store, gen.scope, None, gen, first)
    assert store.read_active(gen.scope) is None

    # The current holder succeeds using the SAME generation content
    # (write_generation is idempotent for identical bytes).
    assert publish(store, gen.scope, None, gen, second) == gen.generation_id
    assert store.read_active(gen.scope) == gen.generation_id


# ---------------------------------------------------------------------
# NEGATIVE CONTROL: late publish after a rollback is rejected
# ---------------------------------------------------------------------


def test_late_publish_after_rollback_is_rejected(tmp_path):
    """Rollback travels the identical authority path as publish, so a
    stale worker's late publish must be fenced out whether the most
    recently accepted action ahead of it was a promotion OR a rollback."""
    store = _store(tmp_path)
    gen_a = _manifest_with_documents("pdf", "net", "api-reference", "1", 2)
    gen_b = _manifest_with_documents("pdf", "net", "api-reference", "2", 2)
    scope = gen_a.scope

    slow_worker_lease = store.acquire_lease(scope, "worker-slow", gen_a.generation_id)
    publish(store, scope, None, gen_a, slow_worker_lease)  # active = gen_a

    fast_worker_lease = store.acquire_lease(scope, "worker-fast", gen_b.generation_id)
    publish(store, scope, gen_a.generation_id, gen_b, fast_worker_lease)  # active = gen_b

    rollback_lease = store.acquire_lease(scope, "worker-ops", gen_a.generation_id)
    rollback(store, scope, gen_b.generation_id, gen_a.generation_id, rollback_lease)  # active = gen_a again
    assert store.read_active(scope) == gen_a.generation_id

    # The slow worker now finally gets around to (re-)publishing gen_b,
    # using the lease it acquired before any of the above happened. It
    # must be fenced out -- the most recent authoritative action was a
    # rollback, not a forward publish, and that must not matter.
    with pytest.raises(StaleFencingTokenError):
        publish(store, scope, gen_a.generation_id, gen_b, slow_worker_lease)

    # Fenced out cleanly: the rollback's result stands, untouched.
    assert store.read_active(scope) == gen_a.generation_id


def test_rollback_to_unknown_generation_is_rejected(tmp_path):
    store = _store(tmp_path)
    gen = _manifest_with_documents("pdf", "net", "api-reference", "1", 1)
    lease = store.acquire_lease(gen.scope, "worker-1", gen.generation_id)
    publish(store, gen.scope, None, gen, lease)

    lease2 = store.acquire_lease(gen.scope, "worker-1", gen.generation_id)
    with pytest.raises(UnknownGenerationError):
        rollback(store, gen.scope, gen.generation_id, "pdf::net::api-reference::never-published", lease2)
    assert store.read_active(gen.scope) == gen.generation_id


# ---------------------------------------------------------------------
# NEGATIVE CONTROL: a published manifest cannot be mutated
# ---------------------------------------------------------------------


def test_published_manifest_cannot_be_mutated(tmp_path):
    store = _store(tmp_path)
    gen_v1 = _manifest_with_documents("pdf", "net", "api-reference", "1", 1, checksum="aaa")
    lease = store.acquire_lease(gen_v1.scope, "worker-1", gen_v1.generation_id)
    publish(store, gen_v1.scope, None, gen_v1, lease)

    # Same identity (same generation_id), DIFFERENT content.
    mutated = _manifest_with_documents("pdf", "net", "api-reference", "1", 1, checksum="bbb")
    lease2 = store.acquire_lease(gen_v1.scope, "worker-1", gen_v1.generation_id)
    with pytest.raises(ManifestImmutableError):
        publish(store, mutated.scope, gen_v1.generation_id, mutated, lease2)

    # The originally published content is untouched.
    on_disk = store.read_generation(gen_v1.scope, gen_v1.generation_id)
    assert on_disk.payload == gen_v1.payload
    assert store.read_active(gen_v1.scope) == gen_v1.generation_id


def test_republishing_identical_content_is_idempotent_not_an_error(tmp_path):
    """Rollback relies on this: re-publishing byte-identical content under
    the same generation_id is a harmless no-op, not a mutation."""
    store = _store(tmp_path)
    gen = _manifest_with_documents("pdf", "net", "api-reference", "1", 1, x=1)
    lease1 = store.acquire_lease(gen.scope, "worker-1", gen.generation_id)
    publish(store, gen.scope, None, gen, lease1)

    same_content_again = _manifest_with_documents("pdf", "net", "api-reference", "1", 1, x=1)
    lease2 = store.acquire_lease(gen.scope, "worker-1", gen.generation_id)
    # expected_active is already gen.generation_id, so this is a genuine
    # CAS no-op re-publish of identical content -- must not raise.
    publish(store, gen.scope, gen.generation_id, same_content_again, lease2)
    assert store.read_active(gen.scope) == gen.generation_id


# ---------------------------------------------------------------------
# NEGATIVE CONTROL: the active pointer never advances past a validation
# failure
# ---------------------------------------------------------------------


def test_active_pointer_does_not_advance_if_validation_fails_partway(tmp_path):
    store = _store(tmp_path)
    key = GenerationKey(family="pdf", platform="net", source_kind="api-reference", version="1")
    # Bypass build_manifest (which always coerces payload to a dict) to
    # construct a manifest that fails structural validation.
    invalid = GenerationManifest(key=key, payload=["not", "a", "dict"])  # type: ignore[arg-type]
    lease = store.acquire_lease(key.scope, "worker-1", key.generation_id)

    with pytest.raises(ManifestValidationError):
        publish(store, key.scope, None, invalid, lease)

    assert store.read_active(key.scope) is None
    with pytest.raises(UnknownGenerationError):
        store.read_generation(key.scope, key.generation_id)

    # A subsequent, VALID publish for the same identity must still work
    # cleanly -- the failed attempt left no partial state behind.
    valid = _manifest_with_documents("pdf", "net", "api-reference", "1", 1, ok=True)
    lease2 = store.acquire_lease(key.scope, "worker-1", key.generation_id)
    publish(store, key.scope, None, valid, lease2)
    assert store.read_active(key.scope) == key.generation_id


# ---------------------------------------------------------------------
# NEGATIVE CONTROL (integration): publishing one source_kind must never
# deactivate another for the same product
# ---------------------------------------------------------------------


def test_publishing_one_source_kind_does_not_deactivate_another(tmp_path):
    store = _store(tmp_path)
    api = _manifest_with_documents("pdf", "net", "api-reference", "24.9", 1, kind="api")
    cookbook = _manifest_with_documents("pdf", "net", "cookbook", "24.9", 1, kind="cookbook")
    assert api.scope != cookbook.scope  # same family/platform/version, different source_kind

    api_lease = store.acquire_lease(api.scope, "worker-api", api.generation_id)
    publish(store, api.scope, None, api, api_lease)
    assert store.read_active(api.scope) == api.generation_id
    assert store.read_active(cookbook.scope) is None

    cookbook_lease = store.acquire_lease(cookbook.scope, "worker-cookbook", cookbook.generation_id)
    publish(store, cookbook.scope, None, cookbook, cookbook_lease)

    # Publishing the cookbook generation must not have touched the API
    # scope's active pointer at all.
    assert store.read_active(api.scope) == api.generation_id
    assert store.read_active(cookbook.scope) == cookbook.generation_id


# ---------------------------------------------------------------------
# TC-242: assert_publish_is_safe, and its enforcement from INSIDE publish()
# itself, so every direct caller is covered - not only infra/ingest.py's CLI.
# ---------------------------------------------------------------------


def test_assert_publish_is_safe_raises_on_zero_chunks_regardless_of_active_state():
    """An empty generation is refused unconditionally - both when nothing is
    currently active and when a healthy generation is already live."""
    with pytest.raises(PublishSafetyError):
        assert_publish_is_safe(0, None)
    with pytest.raises(PublishSafetyError):
        assert_publish_is_safe(0, 10)


def test_assert_publish_is_safe_raises_on_more_than_50_percent_regression():
    """4 active documents, only 1 new chunk: 1 < 4 * 0.5 (2.0), a genuine >50% regression."""
    with pytest.raises(PublishSafetyError):
        assert_publish_is_safe(1, 4)


def test_assert_publish_is_safe_allows_exactly_half_or_better():
    """Exactly half of the active document count is NOT a regression (the guard's own
    condition is strictly-less-than), and growth over the active count is obviously fine."""
    assert_publish_is_safe(2, 4)
    assert_publish_is_safe(5, 4)


def test_assert_publish_is_safe_allows_any_positive_count_with_no_active_generation():
    assert_publish_is_safe(1, None)


def test_publish_refuses_an_empty_generation_even_when_called_directly(tmp_path):
    """TC-242's core defect, fixed: before this card, an empty-generation publish was only
    ever refused by infra/ingest.py's own CLI pre-check. Calling publish() directly -
    exactly as any OTHER caller of publish()/GenerationManifestStore would, bypassing that
    CLI entirely - must be refused identically, and must not activate anything."""
    store = _store(tmp_path)
    empty = build_manifest("pdf", "net", "api-reference", "1")  # payload={} -> 0 documents
    lease = store.acquire_lease(empty.scope, "worker-1", empty.generation_id)

    with pytest.raises(PublishSafetyError):
        publish(store, empty.scope, None, empty, lease)

    assert store.read_active(empty.scope) is None


def test_publish_refuses_a_more_than_50_percent_regression_even_when_called_directly(tmp_path):
    """Same defect, the regression branch: a direct caller publishing far fewer documents
    than half of what is currently active must be refused, and the good, currently-active
    generation must remain active, unreplaced."""
    store = _store(tmp_path)
    healthy = _manifest_with_documents("pdf", "net", "api-reference", "1", 4)
    lease = store.acquire_lease(healthy.scope, "worker-1", healthy.generation_id)
    publish(store, healthy.scope, None, healthy, lease)
    assert store.read_active(healthy.scope) == healthy.generation_id

    regressive = _manifest_with_documents("pdf", "net", "api-reference", "2", 1)
    lease2 = store.acquire_lease(regressive.scope, "worker-1", regressive.generation_id)

    with pytest.raises(PublishSafetyError):
        publish(store, regressive.scope, healthy.generation_id, regressive, lease2)

    assert store.read_active(healthy.scope) == healthy.generation_id


def test_publish_allows_a_safe_generation_with_no_active_generation_yet(tmp_path):
    """A normal first publish - any positive document count, nothing active yet - succeeds
    exactly as it did before this card."""
    store = _store(tmp_path)
    safe = _manifest_with_documents("pdf", "net", "api-reference", "1", 1)
    lease = store.acquire_lease(safe.scope, "worker-1", safe.generation_id)

    assert publish(store, safe.scope, None, safe, lease) == safe.generation_id
    assert store.read_active(safe.scope) == safe.generation_id


def test_publish_allows_a_comfortable_update_against_an_active_generation(tmp_path):
    """A healthy update - comfortably more than half of the currently-active document
    count - succeeds exactly as it did before this card."""
    store = _store(tmp_path)
    first = _manifest_with_documents("pdf", "net", "api-reference", "1", 4)
    lease = store.acquire_lease(first.scope, "worker-1", first.generation_id)
    publish(store, first.scope, None, first, lease)

    second = _manifest_with_documents("pdf", "net", "api-reference", "2", 4)
    lease2 = store.acquire_lease(second.scope, "worker-1", second.generation_id)

    assert publish(store, second.scope, first.generation_id, second, lease2) == second.generation_id
    assert store.read_active(second.scope) == second.generation_id


# ---------------------------------------------------------------------
# TC-253: a publish refused by assert_publish_is_safe must never be durably
# written, so a later rollback() can never be pointed at it (B10 of the
# 2026-10-08 second independent audit - the rollback safety bypass).
# ---------------------------------------------------------------------


def test_a_refused_publish_is_never_durably_written_so_rollback_cannot_bypass_it(tmp_path):
    """Before this card's ordering fix, publish() called store.write_and_validate(generation)
    - a durable write - BEFORE assert_publish_is_safe. An empty-generation publish was still
    correctly refused with PublishSafetyError and never activated, but the refused
    generation's bytes were already on disk: store.generation_exists() returned True for it,
    and rollback() - whose only check is generation_exists(), by design, since a normal
    rollback target is an old, already-vetted generation - would happily activate the exact
    generation this guard had just refused. Proves the bypass is actually closed, not just
    that the write didn't happen: asserts generation_exists() is False for the refused id,
    AND that rollback() to that id raises UnknownGenerationError."""
    store = _store(tmp_path)
    healthy = _manifest_with_documents("pdf", "net", "api-reference", "1", 4)
    lease = store.acquire_lease(healthy.scope, "worker-1", healthy.generation_id)
    publish(store, healthy.scope, None, healthy, lease)
    assert store.read_active(healthy.scope) == healthy.generation_id

    empty = build_manifest("pdf", "net", "api-reference", "2")  # payload={} -> 0 documents, distinct generation_id
    lease2 = store.acquire_lease(empty.scope, "worker-1", empty.generation_id)

    with pytest.raises(PublishSafetyError):
        publish(store, empty.scope, healthy.generation_id, empty, lease2)

    # The refused publish changed nothing about the active pointer...
    assert store.read_active(healthy.scope) == healthy.generation_id
    # ...and, the actual point of this card, left nothing durably written for a later
    # rollback() to find.
    assert store.generation_exists(empty.scope, empty.generation_id) is False

    with pytest.raises(UnknownGenerationError):
        rollback(store, empty.scope, healthy.generation_id, empty.generation_id, lease2)
