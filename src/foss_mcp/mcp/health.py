"""Health probes that prove usefulness, not process reachability.

Readiness reads the ACTUAL active generation pointer
(``foss_mcp.indexing.generation_manifest``), never TCP/process reachability - the confirmed
reference-system defect: it reported ready while serving nothing, because "the process
accepts connections" and "the process has something to serve" are different facts, and only
the second one is what a caller actually needs.
"""

from __future__ import annotations

from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope


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


def is_alive() -> bool:
    """Liveness: the process itself can answer at all.

    Never depends on external state - a liveness probe that can fail for a reason a restart
    cannot fix just produces a crash loop instead of surfacing the real problem.
    """
    return True


def is_ready(store: DeploymentGenerationStore) -> bool:
    """Readiness: TRUE only when the deployment's own scope names a real active generation.

    Never TCP/process reachability - the confirmed reference-system defect this replaces:
    reporting ready while serving nothing.
    """
    return store.active_generation_id() is not None


def round_trip_check(store: DeploymentGenerationStore) -> bool:
    """Beyond "a pointer exists": read the generation it names all the way through, and
    confirm it actually carries queryable content.

    Proves the store is intact end-to-end - a real round trip through the filesystem - not
    merely that a pointer file was written once and never verified again.
    """
    generation_id = store.active_generation_id()
    if generation_id is None:
        return False
    try:
        manifest = store.manifest_store.read_generation(store.scope_key, generation_id)
    except Exception:
        return False
    lexical_index = manifest.payload.get("lexical_index") or {}
    return bool(lexical_index.get("documents"))
