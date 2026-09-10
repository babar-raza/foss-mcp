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
    StaleFencingTokenError,
    UnknownGenerationError,
    build_manifest,
    publish,
    rollback,
)


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


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
    gen = build_manifest("pdf", "net", "api-reference", "1", payload={"pages": 10})
    lease = store.acquire_lease(gen.scope, "worker-1", gen.generation_id)

    result = publish(store, gen.scope, None, gen, lease)

    assert result == gen.generation_id
    assert store.read_active(gen.scope) == gen.generation_id
    round_tripped = store.read_generation(gen.scope, gen.generation_id)
    assert round_tripped.payload == {"pages": 10}


def test_publish_requires_the_current_expected_active_value(tmp_path):
    """A genuine CAS compare failure (current, valid token; wrong
    ``expected_active``) is a distinct, differently-typed failure from
    fencing -- callers must be able to tell "someone else already moved
    this pointer" apart from "my lease is stale."""
    store = _store(tmp_path)
    gen1 = build_manifest("pdf", "net", "api-reference", "1")
    gen2 = build_manifest("pdf", "net", "api-reference", "2")
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
    gen2 = build_manifest("pdf", "net", "api-reference", "2")

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
    gen = build_manifest("pdf", "net", "api-reference", "1")

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
    gen_a = build_manifest("pdf", "net", "api-reference", "1")
    gen_b = build_manifest("pdf", "net", "api-reference", "2")
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
    gen = build_manifest("pdf", "net", "api-reference", "1")
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
    gen_v1 = build_manifest("pdf", "net", "api-reference", "1", payload={"checksum": "aaa"})
    lease = store.acquire_lease(gen_v1.scope, "worker-1", gen_v1.generation_id)
    publish(store, gen_v1.scope, None, gen_v1, lease)

    # Same identity (same generation_id), DIFFERENT content.
    mutated = build_manifest("pdf", "net", "api-reference", "1", payload={"checksum": "bbb"})
    lease2 = store.acquire_lease(gen_v1.scope, "worker-1", gen_v1.generation_id)
    with pytest.raises(ManifestImmutableError):
        publish(store, mutated.scope, gen_v1.generation_id, mutated, lease2)

    # The originally published content is untouched.
    on_disk = store.read_generation(gen_v1.scope, gen_v1.generation_id)
    assert on_disk.payload == {"checksum": "aaa"}
    assert store.read_active(gen_v1.scope) == gen_v1.generation_id


def test_republishing_identical_content_is_idempotent_not_an_error(tmp_path):
    """Rollback relies on this: re-publishing byte-identical content under
    the same generation_id is a harmless no-op, not a mutation."""
    store = _store(tmp_path)
    gen = build_manifest("pdf", "net", "api-reference", "1", payload={"x": 1})
    lease1 = store.acquire_lease(gen.scope, "worker-1", gen.generation_id)
    publish(store, gen.scope, None, gen, lease1)

    same_content_again = build_manifest("pdf", "net", "api-reference", "1", payload={"x": 1})
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
    valid = build_manifest("pdf", "net", "api-reference", "1", payload={"ok": True})
    lease2 = store.acquire_lease(key.scope, "worker-1", key.generation_id)
    publish(store, key.scope, None, valid, lease2)
    assert store.read_active(key.scope) == key.generation_id


# ---------------------------------------------------------------------
# NEGATIVE CONTROL (integration): publishing one source_kind must never
# deactivate another for the same product
# ---------------------------------------------------------------------


def test_publishing_one_source_kind_does_not_deactivate_another(tmp_path):
    store = _store(tmp_path)
    api = build_manifest("pdf", "net", "api-reference", "24.9", payload={"kind": "api"})
    cookbook = build_manifest("pdf", "net", "cookbook", "24.9", payload={"kind": "cookbook"})
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
