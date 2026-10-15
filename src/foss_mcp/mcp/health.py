"""Health probes that prove usefulness, not process reachability.

Readiness reads the ACTUAL active generation pointer
(``foss_mcp.indexing.generation_manifest``), never TCP/process reachability - the confirmed
reference-system defect: it reported ready while serving nothing, because "the process
accepts connections" and "the process has something to serve" are different facts, and only
the second one is what a caller actually needs.

Three probes live here, each with one job:

* ``readiness_report`` is the deep readiness decision behind ``/readyz`` (G3/TC-506). It never
  raises and answers with a ``ReadinessReport``: ready only when the pointer parses (a UTF-8
  byte-order mark is tolerated), the pointer AND the generation both belong to THIS deployment's
  family/platform/source_kind, the lexical index belongs to the active generation, every document
  is well formed under a generation-prefixed id, ``doc_freq`` exists, and the generation carries
  at least ``READINESS_SYMBOL_FLOOR`` symbol chunks and ``READINESS_DOCUMENTATION_FLOOR``
  documentation chunk. Missing examples and mixed source commits are warnings, never a failure.
  Earlier the probe only asked whether some lexical document existed, so a generation of ANOTHER
  product behind this deployment's pointer, or documents with no tokens, answered ready.
* ``round_trip_check`` keeps its boolean signature and returns ``readiness_report(store).ready``
  (TC-140 deleted the earlier, weaker ``is_ready``). It is no longer the only probe, but it is
  still the one boolean an entrypoint can call.
* ``generation_unavailable_reason`` is the CHEAP check the tool dispatcher runs before a
  generation-backed tool: it only asks whether a usable generation exists to search at all, so a
  tool can answer ``unavailable`` instead of a misleading "not found".

No reason or message here ever contains a filesystem path or an exception message, only
exception class names. ``infra/serve_http.py``'s ``/readyz`` route calls ``readiness_report`` and
``round_trip_check``; ``foss_mcp.mcp.server`` calls ``generation_unavailable_reason``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, EXAMPLE, SYMBOL, chunk_kind

READINESS_SYMBOL_FLOOR = 3
READINESS_DOCUMENTATION_FLOOR = 1

GENERATION_UNAVAILABLE_MESSAGE = (
    "This deployment's documentation index is temporarily unavailable. Retry later; "
    "this is not a statement that the API or documentation does not exist."
)

_SOURCE_COMMIT_LINE = re.compile(r"^Source-Commit:\s*(\S+)\s*$", re.MULTILINE)
# A generation id echoed in a report must look like one (letters, digits, ``.``, ``_``, ``:``, ``-``);
# anything else in a doctored pointer (a path, quotes, control characters) is never echoed back.
_SAFE_GENERATION_ID = re.compile(r"[A-Za-z0-9._:\-]{1,200}")


@dataclass(frozen=True)
class DeploymentGenerationStore:
    """A ``GenerationManifestStore`` bound to one deployment's own scope, so a health probe
    never has to be told separately which generation matters - it already knows.
    """

    manifest_store: GenerationManifestStore
    scope_key: str

    @classmethod
    def for_scope(
        cls, manifest_store: GenerationManifestStore, scope: Scope, source_kind: str
    ) -> DeploymentGenerationStore:
        return cls(manifest_store, "::".join((scope.family, scope.platform, source_kind)))

    def active_generation_id(self) -> str | None:
        return self.manifest_store.read_active(self.scope_key)


@dataclass(frozen=True)
class ReadinessReport:
    """The outcome of ``readiness_report``: ``ready`` exactly when there are no ``reasons``.

    ``warnings`` never affect ``ready``. ``composition`` always has the keys ``symbol``, ``doc``
    and ``example`` (counts of chunk kinds; all 0 when the documents could not be counted).
    """

    ready: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    composition: dict[str, int]
    generation_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reasons": list(self.reasons),
            "composition": dict(self.composition),
            "generation_id": self.generation_id,
            "warnings": list(self.warnings),
        }


def is_alive() -> bool:
    """Liveness: the process itself can answer at all.

    Never depends on external state - a liveness probe that can fail for a reason a restart
    cannot fix just produces a crash loop instead of surfacing the real problem.
    """
    return True


def _empty_composition() -> dict[str, int]:
    return {"symbol": 0, "doc": 0, "example": 0}


def readiness_report(store: DeploymentGenerationStore) -> ReadinessReport:
    """Whether this deployment has a usable, correctly scoped generation to serve, and why not.

    NEVER raises: any unexpected exception becomes the single reason
    ``readiness check failed (<ExceptionClassName>)``. Reasons carry exception class names only,
    never a path or an exception message.
    """
    try:
        return _readiness_report(store)
    except Exception as exc:  # noqa: BLE001 - a probe must never raise; see docstring.
        return ReadinessReport(
            False,
            (f"readiness check failed ({type(exc).__name__})",),
            (),
            _empty_composition(),
            None,
        )


def _readiness_report(store: DeploymentGenerationStore) -> ReadinessReport:
    reasons: list[str] = []
    warnings: list[str] = []
    composition = _empty_composition()

    def report(generation_id: str | None = None) -> ReadinessReport:
        shown = generation_id if generation_id and _SAFE_GENERATION_ID.fullmatch(generation_id) else None
        return ReadinessReport(not reasons, tuple(reasons), tuple(warnings), composition, shown)

    try:
        active = store.manifest_store.read_active(store.scope_key)
    except Exception as exc:  # noqa: BLE001 - any read failure is a reason, never a raise.
        reasons.append(f"active pointer is unreadable ({type(exc).__name__})")
        return report()
    if active is None:
        reasons.append("no active generation")
        return report()
    if not isinstance(active, str) or not active:
        reasons.append("active pointer is malformed")
        return report()
    generation_id = active

    pointer_in_scope = generation_id.startswith(store.scope_key + "::")
    if not pointer_in_scope:
        reasons.append("active pointer names a generation of another scope")
    try:
        manifest = store.manifest_store.read_generation(store.scope_key, generation_id)
    except Exception as exc:  # noqa: BLE001 - any read failure is a reason, never a raise.
        reasons.append(f"generation file is unreadable ({type(exc).__name__})")
        return report(generation_id)

    key = manifest.key
    identity_matches = (key.family, key.platform, key.source_kind) == tuple(store.scope_key.split("::"))
    if not identity_matches:
        reasons.append("generation identity does not match this deployment")
    payload = manifest.payload if isinstance(manifest.payload, dict) else {}
    lexical_index = payload.get("lexical_index")
    documents = lexical_index.get("documents") if isinstance(lexical_index, dict) else None
    if not isinstance(lexical_index, dict) or not isinstance(documents, dict) or not documents:
        reasons.append("generation has no lexical documents")
        return report(generation_id)

    index_generation_matches = lexical_index.get("generation_id") == generation_id
    if not index_generation_matches:
        reasons.append("lexical index belongs to a different generation")
    if not isinstance(lexical_index.get("doc_freq"), dict):
        reasons.append("lexical index has no doc_freq")

    malformed = [
        doc_id
        for doc_id, doc in documents.items()
        if not (
            isinstance(doc, dict)
            and isinstance(doc.get("chunk_id"), str)
            and doc["chunk_id"]
            and isinstance(doc.get("text"), str)
            and isinstance(doc.get("tokens"), list)
        )
    ]
    if malformed:
        reasons.append(f"{len(malformed)} documents are malformed (chunk_id, text, tokens)")
    prefix = generation_id + "::doc::"
    foreign = [doc_id for doc_id in documents if not (isinstance(doc_id, str) and doc_id.startswith(prefix))]
    document_ids_in_generation = not foreign
    if not document_ids_in_generation:
        reasons.append(f"{len(foreign)} document ids are not under this generation")

    if not malformed:
        kinds = Counter(chunk_kind(doc["text"]) for doc in documents.values())
        composition = {"symbol": kinds[SYMBOL], "doc": kinds[DOCUMENTATION], "example": kinds[EXAMPLE]}
        if composition["symbol"] < READINESS_SYMBOL_FLOOR:
            reasons.append(f"symbols {composition['symbol']} below the floor {READINESS_SYMBOL_FLOOR}")
        if composition["doc"] < READINESS_DOCUMENTATION_FLOOR:
            reasons.append(
                f"documentation {composition['doc']} below the floor {READINESS_DOCUMENTATION_FLOOR}"
            )
        if composition["example"] == 0:
            warnings.append("no verified examples")
        commits = {sha for doc in documents.values() for sha in _SOURCE_COMMIT_LINE.findall(doc["text"])}
        if len(commits) > 1:
            warnings.append(f"generation mixes {len(commits)} source commits")
    return report(generation_id)


def round_trip_check(store: DeploymentGenerationStore) -> bool:
    """``readiness_report(store).ready``: a real bool, never raises.

    Proves the store is intact end-to-end for THIS deployment - a real round trip through the
    filesystem - not merely that a pointer file was written once and never verified again.
    """
    return readiness_report(store).ready


def generation_unavailable_reason(manifest_store: Any, scope_key: str) -> str | None:
    """``None`` when a usable generation exists to search, else a short reason. Never raises.

    CHEAP by design (it runs before every generation-backed tool call): it reads only the active
    pointer and that generation, and asks only whether the generation has lexical documents. It
    uses nothing but ``read_active`` and ``read_generation(...).payload``, so any store that
    implements those two works. Deep scope and shape checks are ``readiness_report``'s job.
    """
    try:
        generation_id = manifest_store.read_active(scope_key)
    except Exception as exc:  # noqa: BLE001 - a reason, never a raise.
        return f"active pointer is unreadable ({type(exc).__name__})"
    if generation_id is None:
        return "no active generation"
    if not isinstance(generation_id, str) or not generation_id:
        return "active pointer is malformed"
    try:
        payload = manifest_store.read_generation(scope_key, generation_id).payload
    except Exception as exc:  # noqa: BLE001 - a reason, never a raise.
        return f"generation file is unreadable ({type(exc).__name__})"
    lexical_index = payload.get("lexical_index") if isinstance(payload, dict) else None
    documents = lexical_index.get("documents") if isinstance(lexical_index, dict) else None
    if not isinstance(documents, dict) or not documents:
        return "generation has no lexical documents"
    return None
