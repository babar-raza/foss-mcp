"""foss_mcp.indexing.generation_manifest — TC-005: the immutable generation
manifest, its CAS active pointer, and lease-fenced publication/rollback.

Plan 7.1 / 18.3 (see ``plans/TC-005.yaml`` inputs) set four rules this
module exists to enforce, all as one small, local-filesystem authority:

1. **Generation identity covers the full addressable unit** —
   ``(family, platform, source_kind, version)``. Dropping any one of these
   four from the identity is exactly the defect this design prevents:
   publishing a new version of one ``source_kind`` (say, an API reference)
   must never deactivate a different ``source_kind`` for the same
   ``(family, platform)`` (say, a cookbook). :class:`GenerationKey` keeps
   ``version`` inside ``generation_id`` (what a generation IS) while
   deliberately excluding it from ``scope`` (what the active pointer and
   lease are keyed by), so each ``(family, platform, source_kind)`` triple
   owns an independent active pointer that rotates across versions.

2. **A generation is an immutable manifest.** Once a generation has been
   written to the store, its content can never change —
   :meth:`GenerationManifestStore.write_generation` raises
   :class:`ManifestImmutableError` if asked to write different content
   under a ``generation_id`` that already exists (re-writing byte-identical
   content is a harmless no-op, which is what makes rollback-to-a-prior-
   generation possible without re-validating or re-mutating anything).

3. **Publication is a compare-and-swap on one active pointer**, gated by a
   monotonically fencing-tokened lease (:class:`Lease`). Every activation —
   forward publish or rollback — funnels through the single private method
   :meth:`GenerationManifestStore.cas_activate`, which checks the fencing
   token FIRST (a stale lease has no observable effect at all), then the
   compare (``expected_active`` against what is actually active), and only
   then writes the new pointer. Activation therefore never precedes
   write-and-validate completion: the module-level :func:`publish`
   writes and validates the generation as part of computing the argument
   it hands to ``cas_activate``, strictly before that call can run.

4. **Rollback travels the identical authority path.** :func:`rollback` is
   not a separate, weaker code path — it is a fenced CAS through the exact
   same ``cas_activate`` used by :func:`publish`, targeting a
   generation_id that was written earlier. A stale worker's late publish
   is fenced out identically whether the most recently accepted action
   ahead of it was a promotion or a rollback, because both leave behind
   the same one piece of state ``cas_activate`` checks: the scope's
   latest issued fencing token.

Both :func:`publish` and :func:`rollback` are deliberately plain,
module-level functions (not methods) that take a
:class:`GenerationManifestStore` as their first argument: they are the
ONE authority path, and keeping them at module scope means there is
exactly one ``publish``-named definition in this entire file for the
CAS/fencing behaviour to live behind.

Stdlib-only, local filesystem, no network — this is not a distributed
consensus system; it is the local authority a single host process (or a
sequence of them, one at a time) uses to make activation racy-worker-safe.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SEP = "::"


# ---------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------


class GenerationManifestError(Exception):
    """Base class for every error this module raises."""


class ManifestImmutableError(GenerationManifestError):
    """A generation, once written, cannot be rewritten with different
    content."""


class ManifestValidationError(GenerationManifestError):
    """A generation manifest failed structural validation."""


class StaleFencingTokenError(GenerationManifestError):
    """A CAS write carried a fencing token that is no longer the latest
    one issued for its scope — a newer lease has since fenced it out."""


class ActivePointerConflictError(GenerationManifestError):
    """The CAS ``expected_active`` no longer matches what is actually
    active for the scope. Distinct from fencing: this is a genuine
    compare failure under a CURRENT, valid token."""


class UnknownGenerationError(GenerationManifestError):
    """A rollback (or read) named a generation_id that was never written
    for the target scope."""


# ---------------------------------------------------------------------
# Generation identity
# ---------------------------------------------------------------------


def _clean_component(name: str, value: str) -> str:
    if not value or not isinstance(value, str) or _SEP in value:
        raise ManifestValidationError(f"invalid generation identity component {name}={value!r}")
    return value


@dataclass(frozen=True)
class GenerationKey:
    """Identity of one generation: the full addressable unit.

    ``scope`` intentionally excludes ``version`` — it names the
    (family, platform, source_kind) triple whose *active* generation
    rotates across versions over time. ``generation_id`` intentionally
    includes all four fields, because two generations that differ only in
    ``source_kind`` (or in any other single field) MUST be different
    generations with different, independently activatable identities.
    """

    family: str
    platform: str
    source_kind: str
    version: str

    def __post_init__(self) -> None:
        _clean_component("family", self.family)
        _clean_component("platform", self.platform)
        _clean_component("source_kind", self.source_kind)
        _clean_component("version", self.version)

    @property
    def scope(self) -> str:
        return _SEP.join((self.family, self.platform, self.source_kind))

    @property
    def generation_id(self) -> str:
        return _SEP.join((self.family, self.platform, self.source_kind, self.version))


@dataclass(frozen=True)
class GenerationManifest:
    """An immutable generation: identity plus an opaque JSON-shaped
    payload. Two manifests are the "same" generation iff their
    ``generation_id`` matches; the store enforces that same-id manifests
    must also carry identical content (see :class:`ManifestImmutableError`).
    """

    key: GenerationKey
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def generation_id(self) -> str:
        return self.key.generation_id

    @property
    def scope(self) -> str:
        return self.key.scope

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "family": self.key.family,
            "platform": self.key.platform,
            "source_kind": self.key.source_kind,
            "version": self.key.version,
            "payload": self.payload,
        }


def build_manifest(
    family: str, platform: str, source_kind: str, version: str, payload: dict[str, Any] | None = None
) -> GenerationManifest:
    """Build a generation manifest. Building never touches the filesystem
    and never validates against the store — see :func:`validate_manifest`
    and :meth:`GenerationManifestStore.write_generation` for that."""
    key = GenerationKey(family=family, platform=platform, source_kind=source_kind, version=version)
    return GenerationManifest(key=key, payload=dict(payload or {}))


def validate_manifest(manifest: GenerationManifest) -> None:
    """Structural validation every generation must pass before it can ever
    be written to the store or activated. Raises
    :class:`ManifestValidationError` on the first problem found."""
    if not isinstance(manifest, GenerationManifest):
        raise ManifestValidationError(f"not a GenerationManifest: {manifest!r}")
    doc = manifest.to_json_dict()
    for required_field in ("generation_id", "family", "platform", "source_kind", "version"):
        if not doc.get(required_field):
            raise ManifestValidationError(f"generation manifest missing required field {required_field!r}")
    if doc["generation_id"] != manifest.key.generation_id:
        raise ManifestValidationError("generation_id is inconsistent with its identity fields")
    if not isinstance(doc["payload"], dict):
        raise ManifestValidationError("payload must be a JSON object (dict)")


# ---------------------------------------------------------------------
# Lease with a monotonic fencing token
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Lease:
    """``{held_by, generation_id, acquired_at, expires_at, fencing_token}``
    per plan 18.3. ``fencing_token`` is the value that must be presented,
    unchanged, to every CAS write made under this lease; it is rejected the
    moment a newer lease has been acquired for the same scope."""

    held_by: str
    generation_id: str
    acquired_at: str
    expires_at: str
    fencing_token: int


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_plus(start_iso: str, ttl_seconds: float) -> str:
    start = datetime.fromisoformat(start_iso)
    return (start + timedelta(seconds=ttl_seconds)).isoformat()


# ---------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------


class GenerationManifestStore:
    """Local-filesystem authority for generations, the per-scope active
    pointer, and per-scope leases.

    Every mutation of the active pointer — forward publish or rollback —
    goes through the single method :meth:`cas_activate`, called only from
    the module-level :func:`publish` and :func:`rollback` functions below.
    That is the one authority path plan 18.3 asks for: there is exactly
    one place in this whole module where the active-pointer file is ever
    written.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- filesystem layout ------------------------------------------------

    def _scope_dir(self, scope: str) -> Path:
        safe = scope.replace(_SEP, "__")
        d = self.root / safe
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _generation_path(self, key: GenerationKey) -> Path:
        return self._scope_dir(key.scope) / f"generation__{key.version}.json"

    def _generation_path_for_id(self, scope: str, generation_id: str) -> Path:
        # generation_id is "family::platform::source_kind::version"; the
        # version is everything after the LAST separator, since none of
        # the identity components may themselves contain the separator
        # (enforced by _clean_component at construction time).
        version = generation_id.rsplit(_SEP, 1)[-1]
        return self._scope_dir(scope) / f"generation__{version}.json"

    def _active_pointer_path(self, scope: str) -> Path:
        return self._scope_dir(scope) / "active_pointer.json"

    def _lease_state_path(self, scope: str) -> Path:
        return self._scope_dir(scope) / "lease_state.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_write_json(path: Path, doc: dict[str, Any]) -> None:
        """Write ``doc`` to ``path`` atomically: a reader can only ever
        observe the fully-old or the fully-new content, never a partial
        write, because the final step is a single filesystem rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    # -- generations: immutable once written ------------------------------

    def write_generation(self, manifest: GenerationManifest) -> Path:
        """Persist ``manifest``. Validates first (nothing invalid is ever
        written). Idempotent when the exact same content is written again
        under the same ``generation_id`` (this is what makes rollback safe
        — reactivating a generation does not require rewriting it).
        Raises :class:`ManifestImmutableError` if different content is
        offered under a ``generation_id`` that already exists on disk."""
        validate_manifest(manifest)
        path = self._generation_path(manifest.key)
        doc = manifest.to_json_dict()
        existing = self._read_json(path)
        if existing is not None:
            if existing != doc:
                raise ManifestImmutableError(
                    f"generation {manifest.generation_id!r} is already published with "
                    "different content and cannot be mutated"
                )
            return path
        self._atomic_write_json(path, doc)
        return path

    def read_generation(self, scope: str, generation_id: str) -> GenerationManifest:
        """Read back a previously-written generation by id. Raises
        :class:`UnknownGenerationError` if it was never written for
        ``scope``."""
        path = self._generation_path_for_id(scope, generation_id)
        doc = self._read_json(path)
        if doc is None or doc.get("generation_id") != generation_id:
            raise UnknownGenerationError(f"no generation {generation_id!r} written for scope {scope!r}")
        key = GenerationKey(
            family=doc["family"], platform=doc["platform"], source_kind=doc["source_kind"], version=doc["version"]
        )
        return GenerationManifest(key=key, payload=doc["payload"])

    def write_and_validate(self, generation: GenerationManifest) -> str:
        """Write-and-validate ``generation`` and return its generation_id,
        ready to hand to :meth:`cas_activate`. Nothing about the active
        pointer is touched here — this is deliberately the ONLY step that
        runs before activation, so activation can never precede it."""
        self.write_generation(generation)
        return generation.generation_id

    # -- leases: monotonic fencing token per scope ------------------------

    def current_fencing_token(self, scope: str) -> int:
        """The latest fencing token issued for ``scope`` (0 if none has
        ever been acquired)."""
        state = self._read_json(self._lease_state_path(scope))
        return int(state["latest_token"]) if state else 0

    def acquire_lease(
        self,
        scope: str,
        held_by: str,
        generation_id: str,
        ttl_seconds: float = 300.0,
        acquired_at: str | None = None,
    ) -> Lease:
        """Acquire (or re-acquire) the lease for ``scope``, incrementing
        its monotonic fencing token. Acquiring again — by the same holder
        or a different one — always issues a strictly greater token, which
        immediately fences out every lease acquired before it: any CAS
        write later presenting an older token is rejected by
        :meth:`cas_activate`, no matter who holds it or what it was
        trying to do."""
        acquired_at = acquired_at or _iso_now()
        expires_at = _iso_plus(acquired_at, ttl_seconds)
        next_token = self.current_fencing_token(scope) + 1
        self._atomic_write_json(
            self._lease_state_path(scope),
            {
                "latest_token": next_token,
                "held_by": held_by,
                "generation_id": generation_id,
                "acquired_at": acquired_at,
                "expires_at": expires_at,
            },
        )
        return Lease(
            held_by=held_by,
            generation_id=generation_id,
            acquired_at=acquired_at,
            expires_at=expires_at,
            fencing_token=next_token,
        )

    # -- active pointer -----------------------------------------------------

    def read_active(self, scope: str) -> str | None:
        """The generation_id currently active for ``scope``, or ``None``
        if nothing has ever been activated."""
        doc = self._read_json(self._active_pointer_path(scope))
        return doc["generation_id"] if doc else None

    # -- the one authority path ----------------------------------------------

    def cas_activate(self, scope: str, expected_active: str | None, generation_id: str, lease: Lease) -> str:
        """The single fenced compare-and-swap every activation of the
        active pointer — forward publish or rollback — must go through.
        Not meant to be called directly: use the module-level
        :func:`publish` and :func:`rollback` functions below, which are
        the actual authority path.

        Order is deliberate and load-bearing:
          1. fencing check FIRST — a stale lease produces no observable
             effect whatsoever, not even an informative CAS-mismatch.
          2. THEN the compare (``expected_active`` vs what is actually
             active).
          3. Only if both hold does the pointer get written, and that
             write is atomic (see :meth:`_atomic_write_json`).
        """
        current_token = self.current_fencing_token(scope)
        if lease.fencing_token != current_token:
            raise StaleFencingTokenError(
                f"fencing token {lease.fencing_token} for scope {scope!r} is stale "
                f"(latest issued token is {current_token})"
            )
        actual_active = self.read_active(scope)
        if actual_active != expected_active:
            raise ActivePointerConflictError(
                f"expected active {expected_active!r} for scope {scope!r}, found {actual_active!r}"
            )
        self._atomic_write_json(self._active_pointer_path(scope), {"generation_id": generation_id})
        return generation_id

    def generation_exists(self, scope: str, generation_id: str) -> bool:
        """Whether ``generation_id`` was ever written for ``scope``."""
        return self._read_json(self._generation_path_for_id(scope, generation_id)) is not None


# ---------------------------------------------------------------------
# The one authority path: publish and rollback
# ---------------------------------------------------------------------
#
# Both are deliberately plain module-level functions, not methods, and
# deliberately delegate their entire body to `store.cas_activate` /
# `store.write_and_validate` rather than inlining any logic here. That is
# what makes `publish` -- the only definition in this whole file whose
# name matches a "*publish*" pattern -- a genuine single point of failure
# for the whole CAS/fencing contract: there is nowhere else the check
# could be hiding.


def publish(
    store: GenerationManifestStore,
    scope: str,
    expected_active: str | None,
    generation: GenerationManifest,
    lease: Lease,
): return store.cas_activate(scope, expected_active, store.write_and_validate(generation), lease)


def rollback(
    store: GenerationManifestStore,
    scope: str,
    expected_active: str | None,
    target_generation_id: str,
    lease: Lease,
) -> str:
    """Roll back ``scope`` to a PREVIOUSLY-published generation.

    Travels the identical authority path as :func:`publish`: it is a
    fenced CAS through :meth:`GenerationManifestStore.cas_activate`,
    nothing more and nothing less, so a stale worker's late publish is
    fenced out exactly the same way whether the most recently accepted
    action ahead of it was a promotion or a rollback. The only thing
    rollback adds is a sanity check that the target generation actually
    exists — you can only roll back to something that was really
    published.
    """
    if not store.generation_exists(scope, target_generation_id):
        raise UnknownGenerationError(
            f"cannot roll back scope {scope!r} to unknown generation {target_generation_id!r}"
        )
    return store.cas_activate(scope, expected_active, target_generation_id, lease)
