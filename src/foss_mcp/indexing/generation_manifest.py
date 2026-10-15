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
import re
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

_SEP = "::"

# A scope is exactly three components; each one becomes part of a single directory name, so
# it must be a plain, short token that can never be (or contain) a path.
_SCOPE_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SCOPE_COMPONENT_COUNT = 3
_MAX_VERSION_LENGTH = 128
# TC-510: the production publish policy (see PublishPolicy below). One constant per line.
PRODUCTION_MIN_SYMBOLS = 3
PRODUCTION_MIN_DOCS = 1
RETENTION_KEEP_LAST = 3
MIN_BASELINE_FOR_PERCENT_RULE = 5
PUBLISH_LOG_NAME = "publish_log.jsonl"
# At most this many parsed generation documents are held in process, per store instance.
_GENERATION_CACHE_MAX_ENTRIES = 8


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


class InvalidScopeError(GenerationManifestError, ValueError):
    """A scope string or generation version would not map to a path strictly
    inside the store root (a path separator, ``..``, a control character, the
    wrong number of components, an over-long component...). Its message never
    repeats the offending value."""


def _has_control_character(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _validate_version(version: object) -> str:
    """A generation version is used verbatim inside ``generation__{version}.json``, so it
    must not be able to name anything but a file directly in its scope directory."""
    if (
        not isinstance(version, str)
        or len(version) > _MAX_VERSION_LENGTH
        or "/" in version
        or "\\" in version
        or ".." in version
        or _has_control_character(version)
    ):
        raise InvalidScopeError("invalid generation version")
    return version


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
    return datetime.now(UTC).isoformat()


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
        # (resolved path, st_mtime_ns, st_size) -> parsed generation document, least recently used first.
        self._generation_cache: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()
        self._generation_cache_lock = threading.Lock()

    # -- filesystem layout ------------------------------------------------

    def _contained_scope_dir(self, scope: str) -> Path:
        """Map ``scope`` to its directory under the store root. This is the ONLY place a
        scope string becomes a path: it never creates anything, accepts only
        ``component::component::component`` with each component a short plain token, and
        refuses any directory whose resolved location is not inside the resolved root."""
        if not isinstance(scope, str):
            raise InvalidScopeError("invalid scope")
        components = scope.split(_SEP)
        if len(components) != _SCOPE_COMPONENT_COUNT:
            raise InvalidScopeError("invalid scope")
        for component in components:
            if _SCOPE_COMPONENT_PATTERN.fullmatch(component) is None or ".." in component:
                raise InvalidScopeError("invalid scope")
        candidate = self.root / "__".join(components)
        resolved_root = self.root.resolve()
        resolved = candidate.resolve()
        if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
            raise InvalidScopeError("invalid scope")
        return candidate

    def _scope_dir(self, scope: str) -> Path:
        # Never creates the directory: a READ must have no side effect. Writes create it
        # through _atomic_write_json.
        return self._contained_scope_dir(scope)

    def _generation_path(self, key: GenerationKey) -> Path:
        version = _validate_version(key.version)
        return self._scope_dir(key.scope) / f"generation__{version}.json"

    def _generation_path_for_id(self, scope: str, generation_id: str) -> Path:
        # generation_id is "family::platform::source_kind::version"; the
        # version is everything after the LAST separator, since none of
        # the identity components may themselves contain the separator
        # (enforced by _clean_component at construction time).
        if not isinstance(generation_id, str):
            raise InvalidScopeError("invalid generation version")
        version = _validate_version(generation_id.rsplit(_SEP, 1)[-1])
        return self._scope_dir(scope) / f"generation__{version}.json"

    def _active_pointer_path(self, scope: str) -> Path:
        return self._scope_dir(scope) / "active_pointer.json"

    def _lease_state_path(self, scope: str) -> Path:
        return self._scope_dir(scope) / "lease_state.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))

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

    def _cached_generation_document(self, path: Path) -> dict[str, Any] | None:
        """The parsed JSON document at ``path`` (``None`` if the file is absent), re-parsed only
        when the file's identity (resolved path, ``st_mtime_ns``, ``st_size``) changes. A missing
        file is never cached. Holds at most 8 entries, least recently used evicted."""
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        with self._generation_cache_lock:
            cached = self._generation_cache.get(key)
            if cached is not None:
                self._generation_cache.move_to_end(key)
                return cached
        doc = self._read_json(path)
        if doc is None:
            return None
        with self._generation_cache_lock:
            self._generation_cache[key] = doc
            self._generation_cache.move_to_end(key)
            while len(self._generation_cache) > _GENERATION_CACHE_MAX_ENTRIES:
                self._generation_cache.popitem(last=False)
        return doc

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
        doc = self._cached_generation_document(path)
        if doc is None or doc.get("generation_id") != generation_id:
            raise UnknownGenerationError(f"no generation {generation_id!r} written for scope {scope!r}")
        key = GenerationKey(
            family=doc["family"],
            platform=doc["platform"],
            source_kind=doc["source_kind"],
            version=doc["version"],
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

    # -- publish log (TC-510) ---------------------------------------------

    def append_publish_log(self, scope: str, entry: dict[str, Any]) -> None:
        """Append ``entry`` as one key-sorted JSON line to the scope's ``publish_log.jsonl``,
        then flush and fsync it. Creates the (contained) scope directory if needed."""
        scope_dir = self._scope_dir(scope)
        # os.makedirs, not Path.mkdir: the scope directory comes from the contained _scope_dir, and
        # the containment test pins exactly two Path.mkdir sites (the store root and the atomic write).
        os.makedirs(scope_dir, exist_ok=True)
        line = json.dumps(entry, sort_keys=True, default=str)
        with open(scope_dir / PUBLISH_LOG_NAME, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read_publish_log(self, scope: str) -> list[dict[str, Any]]:
        """Every well-formed entry of the scope's publish log, oldest first. A missing file is
        ``[]``; a malformed line (or a line that is not a JSON object) is skipped."""
        path = self._scope_dir(scope) / PUBLISH_LOG_NAME
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries


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


class PublishSafetyError(Exception):
    """Raised by :func:`assert_publish_is_safe` to refuse a publish that would either
    ship an empty generation or drastically regress against what is already live. Raised
    from inside :func:`publish` itself — before ``cas_activate`` ever runs — so it applies
    to every caller of ``publish``/:class:`GenerationManifestStore`, not only a particular
    CLI built on top of them. Never caught inside this module: it is left to propagate to
    whatever called ``publish``, which must not treat a refused publish as having
    activated anything."""


def assert_publish_is_safe(new_document_count: int, active_document_count: int | None) -> None:
    """Guard called from inside :func:`publish`, before ``cas_activate`` runs.

    Refuses the publish when either is true:

    1. ``new_document_count == 0`` - an empty generation is never a valid publish,
       regardless of whether anything is currently active. A tree-sitter regression, a
       broken extraction, or a citation-validation bug that silently drops every chunk
       must not replace a good, currently-serving generation with nothing.
    2. There IS a currently active generation (``active_document_count`` is not ``None``
       and greater than zero) and the new document count is less than half its document
       count - a drastic regression against what is already live.
    """
    if new_document_count == 0:
        raise PublishSafetyError("refusing to publish an empty generation (0 chunks)")
    if active_document_count is not None and active_document_count > 0:
        if new_document_count < active_document_count * 0.5:
            raise PublishSafetyError(
                f"refusing to publish {new_document_count} chunk(s): this is more than a 50% "
                f"regression against the currently active generation's {active_document_count} "
                "document(s)"
            )


def _document_count(payload: dict[str, Any]) -> int:
    """How many documents ``payload`` carries in its lexical index - the same count
    ``infra/ingest.py`` used to derive from the chunk list it was about to publish, read
    back here from the payload :func:`foss_mcp.indexing.publisher.publish_generation`
    actually writes (confirmed against ``build_lexical_index``: one document per chunk,
    keyed by ``doc_id``)."""
    return len((payload.get("lexical_index") or {}).get("documents") or {})


# ---------------------------------------------------------------------
# TC-510: production publish policy
# ---------------------------------------------------------------------
#
# ``publish(..., policy=None)`` is the legacy behaviour, byte for byte. A caller that opts into a
# ``PublishPolicy`` (infra/ingest.py does) additionally gets per-kind statistics, hard floors,
# per-kind regression limits against the maximum of the last few generations, refusal of
# duplicate / empty chunks and of a changed provenance repository, an explicit, exactly-matching,
# logged override for a deliberate shrink, and a ``publish_log.jsonl`` beside the active pointer.

# The first ``FQN:`` line of a chunk (the same expression ``get_symbol.extract_fqn`` uses). It is
# repeated here, not imported, because ``foss_mcp.mcp`` imports this module.
_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_EXAMPLE_FQN_PREFIX = "Example: "
_DOC_FQN_PREFIX = "Doc: "
_KINDS = ("symbols", "docs", "examples", "total")
_FLOAT_SLACK = 1e-9


def classify_chunk_text(text: str) -> str:
    """``"example"``, ``"doc"`` or ``"symbol"`` for one chunk's text, exactly as
    ``foss_mcp.mcp.tools.relevance.chunk_kind`` decides it: no ``FQN:`` line -> doc; a value
    starting ``Example: `` -> example; starting ``Doc: `` -> doc; anything else -> symbol."""
    match = _FQN_LINE.search(text)
    if match is None:
        return "doc"
    fqn = match.group(1).strip()
    if fqn.startswith(_EXAMPLE_FQN_PREFIX):
        return "example"
    if fqn.startswith(_DOC_FQN_PREFIX):
        return "doc"
    return "symbol"


def payload_stats(payload: dict[str, Any]) -> dict[str, int]:
    """``{"symbols", "docs", "examples", "total"}`` over a payload's lexical-index documents. A
    payload without a lexical index yields zeros; a document whose text is not a string counts
    as a doc."""
    documents = (payload.get("lexical_index") or {}).get("documents") or {}
    items = documents.values() if isinstance(documents, dict) else documents
    symbols = docs = examples = 0
    for document in items:
        text = document.get("text") if isinstance(document, dict) else None
        kind = classify_chunk_text(text) if isinstance(text, str) else "doc"
        if kind == "symbol":
            symbols += 1
        elif kind == "example":
            examples += 1
        else:
            docs += 1
    return {"symbols": symbols, "docs": docs, "examples": examples, "total": symbols + docs + examples}


def _counts(stats: object) -> dict[str, int]:
    """The four per-kind counts of a stats dict; anything missing or not an int is 0."""
    source = stats if isinstance(stats, dict) else {}
    counts: dict[str, int] = {}
    for kind in _KINDS:
        value = source.get(kind, 0)
        counts[kind] = value if isinstance(value, int) and not isinstance(value, bool) else 0
    return counts


def _stats_of(payload: dict[str, Any]) -> dict[str, Any]:
    """The persisted ``payload.stats`` when it carries the four counts, else the stats computed
    from the payload itself (a legacy generation)."""
    stats = payload.get("stats")
    if isinstance(stats, dict) and all(kind in stats for kind in _KINDS):
        return stats
    return payload_stats(payload)


@dataclass(frozen=True)
class PublishPolicy:
    """What a production publish refuses. ``PublishPolicy()`` IS the production policy; the legacy
    behaviour is ``policy=None``.

    ``accept_regressions`` is a tuple of ``(kind, old, new)`` that waives exactly the percent
    refusal of ``kind`` when ``old`` and ``new`` equal the observed baseline and new value; it
    needs a ``reason`` and never waives a floor, a duplicate, empty text or a repository change.
    """

    min_symbols: int = PRODUCTION_MIN_SYMBOLS
    min_docs: int = PRODUCTION_MIN_DOCS
    max_drop: float = 0.5
    max_symbol_drop: float = 0.2
    history: int = RETENTION_KEEP_LAST
    accept_regressions: tuple[tuple[str, int, int], ...] = ()
    reason: str | None = None
    held_by: str = ""


def integrity_refusal_message(duplicate_chunk_ids: int, empty_text_chunks: int) -> str | None:
    """The refusal message for duplicate chunk ids and/or empty-text chunks, or ``None``."""
    problems = []
    if duplicate_chunk_ids > 0:
        problems.append(f"{duplicate_chunk_ids} duplicate chunk id(s)")
    if empty_text_chunks > 0:
        problems.append(f"{empty_text_chunks} chunk(s) with empty text")
    if not problems:
        return None
    return "refusing to publish: " + " and ".join(problems)


def refuse_publish(
    store: GenerationManifestStore,
    scope: str,
    generation_id: str,
    message: str,
    *,
    expected_active: str | None = None,
    held_by: str = "",
    stats: dict[str, Any] | None = None,
) -> NoReturn:
    """Append one ``refused`` line to the scope's publish log, then raise
    :class:`PublishSafetyError` with ``message``. The one place a policy refusal is written."""
    try:
        store.append_publish_log(
            scope,
            {
                "ts": _iso_now(),
                "event": "refused",
                "scope": scope,
                "generation_id": generation_id,
                "previous_active": expected_active,
                "held_by": held_by,
                "stats": stats,
                "reason": message,
            },
        )
    except OSError:
        pass  # a log that cannot be written must not turn a refusal into an activation
    raise PublishSafetyError(message)


def _baseline(
    store: GenerationManifestStore, scope: str, active_stats: dict[str, Any] | None, history: int
) -> dict[str, int]:
    """Per-kind MAX over the active generation's stats and the last ``history`` ``published`` log
    entries, scanning newest to oldest and stopping after the first entry that carries overrides
    (an accepted shrink resets history)."""
    sources: list[dict[str, int]] = []
    if active_stats is not None:
        sources.append(_counts(active_stats))
    published = [entry for entry in store.read_publish_log(scope) if entry.get("event") == "published"]
    taken = 0
    for entry in reversed(published):
        if taken >= history:
            break
        taken += 1
        sources.append(_counts(entry.get("stats")))
        if entry.get("overrides"):
            break
    return {kind: max((source[kind] for source in sources), default=0) for kind in _KINDS}


def _publish_with_policy(
    store: GenerationManifestStore,
    scope: str,
    expected_active: str | None,
    generation: GenerationManifest,
    lease: Lease,
    policy: PublishPolicy,
) -> str:
    generation_id = generation.generation_id
    stats = _stats_of(generation.payload)
    new = _counts(stats)

    def refuse(message: str) -> NoReturn:
        refuse_publish(
            store,
            scope,
            generation_id,
            message,
            expected_active=expected_active,
            held_by=policy.held_by,
            stats=stats,
        )

    if policy.accept_regressions and not (policy.reason and policy.reason.strip()):
        refuse("refusing to publish: --accept-regression needs a non-blank --reason")

    active_stats: dict[str, Any] | None = None
    active_repositories: object = None
    active_generation_id = store.read_active(scope)
    if active_generation_id is not None:
        try:
            active_manifest = store.read_generation(scope, active_generation_id)
        except UnknownGenerationError:
            active_manifest = None
        if active_manifest is not None:
            active_stats = _stats_of(active_manifest.payload)
            persisted = active_manifest.payload.get("stats")
            if isinstance(persisted, dict):
                active_repositories = persisted.get("repositories")
    baseline = _baseline(store, scope, active_stats, policy.history)

    # Refusals that no override can waive.
    if new["total"] == 0:
        refuse("refusing to publish an empty generation (0 chunks)")
    if new["symbols"] < policy.min_symbols:
        refuse(
            f"refusing to publish: {new['symbols']} symbol chunk(s) is below the production floor of "
            f"{policy.min_symbols}"
        )
    if new["docs"] < policy.min_docs:
        refuse(
            f"refusing to publish: {new['docs']} documentation chunk(s) is below the production floor of "
            f"{policy.min_docs}"
        )
    duplicates = stats.get("duplicate_chunk_ids", 0)
    empties = stats.get("empty_text_chunks", 0)
    integrity = integrity_refusal_message(
        duplicates if isinstance(duplicates, int) else 0, empties if isinstance(empties, int) else 0
    )
    if integrity is not None:
        refuse(integrity)
    new_repositories = stats.get("repositories")
    if isinstance(new_repositories, list) and isinstance(active_repositories, list):
        if sorted(map(str, new_repositories)) != sorted(map(str, active_repositories)):
            refuse(
                "refusing to publish: the provenance repository changed from "
                f"{sorted(map(str, active_repositories))} to {sorted(map(str, new_repositories))}; "
                "a deliberate repository change needs a new store"
            )

    # Per-kind regression limits against the baseline (overridable).
    regressions: dict[str, str] = {}
    for kind in _KINDS:
        old, current = baseline[kind], new[kind]
        limit = policy.max_symbol_drop if kind == "symbols" else policy.max_drop
        if old >= MIN_BASELINE_FOR_PERCENT_RULE and current < old * (1 - limit) - _FLOAT_SLACK:
            regressions[kind] = (
                f"{kind} fell from {old} to {current}, more than {limit:.0%} below the maximum of the last "
                f"{policy.history} generation(s)"
            )
        elif kind == "examples" and old >= 3 and current == 0:
            regressions[kind] = f"examples fell from {old} to 0"

    # The legacy total guard (more than 50 percent below the ACTIVE generation) still applies where the
    # percent rule does not (a baseline under MIN_BASELINE_FOR_PERCENT_RULE); a total override waives it.
    if "total" not in regressions:
        active_total = _counts(active_stats)["total"] if active_stats is not None else None
        try:
            assert_publish_is_safe(new["total"], active_total)
        except PublishSafetyError:
            regressions["total"] = (
                f"total fell from {baseline['total']} to {new['total']}, more than 50% below the currently "
                f"active generation's {active_total}"
            )

    waived: set[str] = set()
    for kind, old, current in policy.accept_regressions:
        if kind not in _KINDS:
            refuse(f"refusing to publish: --accept-regression names an unknown kind {kind!r}")
        if old != baseline[kind] or current != new[kind]:
            refuse(
                f"refusing to publish: --accept-regression {kind}:{old}->{current} does not match the observed "
                f"{kind}:{baseline[kind]}->{new[kind]}"
            )
        waived.add(kind)
    unwaived = [kind for kind in regressions if kind not in waived]
    if unwaived:
        flags = " ".join(f"--accept-regression {kind}:{baseline[kind]}->{new[kind]}" for kind in regressions)
        refuse(
            "refusing to publish: "
            + "; ".join(regressions[kind] for kind in unwaived)
            + f"; to accept deliberately: {flags} --reason TEXT"
        )

    result = store.cas_activate(scope, expected_active, store.write_and_validate(generation), lease)
    overrides = [f"{kind}:{old}->{current}" for kind, old, current in policy.accept_regressions]
    store.append_publish_log(
        scope,
        {
            "ts": _iso_now(),
            "event": "published",
            "scope": scope,
            "generation_id": generation_id,
            "previous_active": expected_active,
            "held_by": policy.held_by,
            "stats": stats,
            "baseline": baseline,
            "overrides": overrides,
            "reason": policy.reason,
            "floors": {"symbols": policy.min_symbols, "docs": policy.min_docs},
        },
    )
    return result


def publish(
    store: GenerationManifestStore,
    scope: str,
    expected_active: str | None,
    generation: GenerationManifest,
    lease: Lease,
    policy: PublishPolicy | None = None,
):
    """Validate, safety-check, then write-and-activate ``generation`` - the one authority
    path. Enforces :func:`validate_manifest` first (so a structurally-invalid payload
    still fails with :class:`ManifestValidationError`, not an attribute error from
    :func:`assert_publish_is_safe` trying to read a malformed payload), then
    :func:`assert_publish_is_safe` itself, and only once BOTH have passed does this durably
    write ``generation`` via :meth:`GenerationManifestStore.write_and_validate` and activate
    it via ``cas_activate``. This order is load-bearing: a publish refused by either check
    must never be durably written under any ``generation_id`` at all, so no later
    :func:`rollback` - whose only check is :meth:`GenerationManifestStore.generation_exists`
    - can ever be pointed at a generation this guard has refused.

    ``active_document_count`` is read from whatever generation is REALLY currently active
    for ``scope`` right now (not from the caller-supplied ``expected_active``, which may
    legitimately name a generation that was never written when a caller is deliberately
    testing a CAS mismatch) - falling back to ``None`` when nothing is active yet, or when
    that lookup can't resolve to a real generation.

    ``policy`` (TC-510) is opt-in: ``None`` keeps exactly the behaviour above and writes no log;
    a :class:`PublishPolicy` runs :func:`_publish_with_policy` instead (stats, floors, per-kind
    limits, identity, the logged override, and the ``published`` log line).
    """
    validate_manifest(generation)
    if policy is not None:
        return _publish_with_policy(store, scope, expected_active, generation, lease, policy)

    active_document_count: int | None = None
    active_generation_id = store.read_active(scope)
    if active_generation_id is not None:
        try:
            active_manifest = store.read_generation(scope, active_generation_id)
        except UnknownGenerationError:
            active_manifest = None
        if active_manifest is not None:
            active_document_count = _document_count(active_manifest.payload)

    assert_publish_is_safe(_document_count(generation.payload), active_document_count)

    generation_id = store.write_and_validate(generation)

    return store.cas_activate(scope, expected_active, generation_id, lease)


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
