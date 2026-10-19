"""Rollback entrypoint: a plain CLI, no message broker.

TC-130's own import-graph walk confirmed ``foss_mcp.indexing.publisher.rollback_generation``
has no caller anywhere that would ever trigger it in production - there was presently no way
to actually invoke a rollback outside a test. This module wraps that already-implemented,
already-tested function in a one-shot command, mirroring ``infra/ingest.py``'s own real CLI
shape exactly (argparse, ``GenerationManifestStore`` construction, lease acquisition pattern)
rather than inventing a different convention. "Rollback" is deliberately not a worker or a
queue consumer either: it runs once, rolls the scope's active pointer back to a previously
published generation, prints the result, and exits.

Rollback travels the identical authority path a forward publish does (see
``foss_mcp.indexing.generation_manifest``'s module docstring): it is a fenced CAS through
``GenerationManifestStore.cas_activate``, targeting a generation_id that was written earlier.

G3/TC-564 (RD3-121, RD3-122) makes it safe to run in production:

* Both shipped images contain this file (``Dockerfile.serving`` and ``Dockerfile.ingestion`` copy it), so
  ``python /app/infra/rollback.py --list`` is the documented runbook command and it runs.
* Before flipping, the target must pass what a forward publish must pass (unless ``--force``): the production
  floors (symbols and documentation), the same source repository as the active generation, and the per-kind
  regression limits measured against the generation being replaced.
* The sidecar for this identity is re-checked: a product reference that names ANOTHER repository refuses the
  rollback; one that still records a NEWER commit than the target is reported (the server's install notice
  follows the active generation's own commit, so nothing needs rewriting).
* After the flip the same readiness probe ``/readyz`` runs is applied to the new active generation; if it is
  not ready the pointer is flipped back to the generation it replaced and the command exits 3.
* ``--list`` never crashes: a corrupt pointer, a corrupt generation file or a file with no readable id is
  named on its own line and skipped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from foss_mcp.indexing.generation_manifest import (
    MIN_BASELINE_FOR_PERCENT_RULE,
    PRODUCTION_MIN_DOCS,
    PRODUCTION_MIN_SYMBOLS,
    GenerationManifest,
    GenerationManifestError,
    GenerationManifestStore,
    ManifestValidationError,
    PublishPolicy,
    UnknownGenerationError,
    check_generation_shape,
    payload_stats,
)
from foss_mcp.indexing.publisher import rollback_generation
from foss_mcp.mcp.health import DeploymentGenerationStore, readiness_report

EXIT_REFUSED = 1
EXIT_REVERTED = 3
EXIT_REVERT_FAILED = 4
_KINDS = ("symbols", "docs", "examples", "total")
_SEP = "::"
# infra/fetch_product_reference.py's ``sidecar_name``. This module ships alone in an image and must not depend
# on its siblings being importable, so the name is repeated here (a test pins that the two agree).
_PRODUCT_REFERENCE_SIDECAR = "product_reference_{family}_{platform}.json"
_SIDECAR_MAX_BYTES = 8 * 1024 * 1024
_HEX_COMMIT = re.compile(r"[0-9a-f]{7,64}")


class RollbackRefusedError(GenerationManifestError):
    """The target is a generation the rollback checks refuse to activate. Pass ``--force`` to override."""


def _stats(manifest: GenerationManifest) -> dict[str, int]:
    """The four per-kind counts of *manifest*: its persisted ``payload.stats`` when it carries them all, else
    computed from the payload."""
    stats = manifest.payload.get("stats")
    if isinstance(stats, dict) and all(isinstance(stats.get(kind), int) for kind in _KINDS):
        return {kind: int(stats[kind]) for kind in _KINDS}
    return payload_stats(manifest.payload)


def _repositories(manifest: GenerationManifest) -> list[str] | None:
    persisted = manifest.payload.get("stats")
    value = persisted.get("repositories") if isinstance(persisted, dict) else None
    return sorted(map(str, value)) if isinstance(value, list) else None


def _normalised_slug(repository: str) -> str:
    """The lowercase ``owner/name`` of a repository slug or URL, without a trailing ``.git``."""
    value = repository.strip()
    if "://" in value:
        value = value.split("://", 1)[1].partition("/")[2]
    return value.strip("/").lower().removesuffix(".git")


def _commits_of(manifest: GenerationManifest) -> set[str]:
    """Every ``Source-Commit:`` value in the generation's chunk texts."""
    documents = (manifest.payload.get("lexical_index") or {}).get("documents") or {}
    found: set[str] = set()
    for document in documents.values():
        text = document.get("text") if isinstance(document, dict) else None
        if isinstance(text, str):
            found.update(re.findall(r"^Source-Commit:\s*(\S+)\s*$", text, re.MULTILINE))
    return found


def _validate_target(store: GenerationManifestStore, scope: str, target: str) -> GenerationManifest:
    """The target manifest, after the checks ``rollback(validate=True)`` makes (it exists, it belongs to the
    scope, it is a readable generation of the right shape). Raises the same exceptions."""
    if not store.generation_exists(scope, target):
        raise UnknownGenerationError(f"cannot roll back scope {scope!r} to unknown generation {target!r}")
    if target.rsplit(_SEP, 1)[0] != scope:
        raise UnknownGenerationError(f"generation {target!r} does not belong to scope {scope!r}")
    try:
        manifest = store.read_generation(scope, target)
    except (UnknownGenerationError, KeyError, TypeError, ManifestValidationError) as exc:
        raise ManifestValidationError(
            f"generation {target!r} is not a readable generation of scope {scope!r}"
        ) from exc
    problems = check_generation_shape(manifest, target)
    if problems:
        raise ManifestValidationError(f"generation {target!r} failed validation: " + "; ".join(problems))
    return manifest


def _safety_problems(target: GenerationManifest, active: GenerationManifest | None) -> list[str]:
    """Why a forward publish of *target* over *active* would be refused: the production floors, a changed
    source repository, and the per-kind regression limits against *active*. ``[]`` when none applies."""
    policy = PublishPolicy()
    new = _stats(target)
    problems: list[str] = []
    if new["total"] == 0:
        problems.append("the target generation is empty (0 chunks)")
    if new["symbols"] < PRODUCTION_MIN_SYMBOLS:
        problems.append(
            f"{new['symbols']} symbol chunk(s) is below the production floor of {PRODUCTION_MIN_SYMBOLS}"
        )
    if new["docs"] < PRODUCTION_MIN_DOCS:
        problems.append(
            f"{new['docs']} documentation chunk(s) is below the production floor of {PRODUCTION_MIN_DOCS}"
        )
    if active is None:
        return problems
    target_repositories, active_repositories = _repositories(target), _repositories(active)
    if (
        target_repositories is not None
        and active_repositories is not None
        and target_repositories != active_repositories
    ):
        problems.append(
            f"the target was indexed from {target_repositories}, the active generation from {active_repositories}"
        )
    old = _stats(active)
    for kind in _KINDS:
        limit = policy.max_symbol_drop if kind == "symbols" else policy.max_drop
        if old[kind] >= MIN_BASELINE_FOR_PERCENT_RULE and new[kind] < old[kind] * (1 - limit):
            problems.append(
                f"{kind} would fall from {old[kind]} to {new[kind]}, more than {limit:.0%} below the active generation"
            )
    return problems


def _read_product_reference(manifests_dir: Path, family: str, platform: str) -> dict[str, object] | None:
    """The identity's product-reference sidecar as a dict, or ``None`` when it is absent or unusable."""
    path = manifests_dir / _PRODUCT_REFERENCE_SIDECAR.format(family=family, platform=platform)
    try:
        with path.open("rb") as handle:
            raw = handle.read(_SIDECAR_MAX_BYTES + 1)
        if len(raw) > _SIDECAR_MAX_BYTES:
            return None
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _sidecar_problems(sidecar: dict[str, object] | None, target: GenerationManifest) -> list[str]:
    """A product reference that names ANOTHER repository than the target generation is a refusal."""
    if sidecar is None:
        return []
    repository, repositories = sidecar.get("source_repository"), _repositories(target)
    if isinstance(repository, str) and repositories:
        if _normalised_slug(repository) not in {_normalised_slug(item) for item in repositories}:
            return [
                f"the product-reference sidecar names repository {repository!r}, "
                f"the target was indexed from {repositories}"
            ]
    return []


def _sidecar_notice(sidecar: dict[str, object] | None, active: GenerationManifest) -> str | None:
    """A one-line notice when the sidecar records a commit the now-active generation was not built from."""
    ref = sidecar.get("source_ref") if sidecar else None
    commits = _commits_of(active)
    if not isinstance(ref, str) or not ref or not commits:
        return None
    if any(c.lower().startswith(ref.lower()) or ref.lower().startswith(c.lower()) for c in commits):
        return None
    shown = ", ".join(sorted(commits)[:3])
    return (
        f"note: the product-reference sidecar records commit {ref[:12]}, which the active generation was not "
        f"built from ({shown[:60]}); the install notice follows the active generation's own commit"
    )


def _list_generations(store: GenerationManifestStore, scope: str) -> None:
    """Print one line per stored generation, newest first. Takes no lease and writes nothing.

    A corrupt active pointer, a generation file that cannot be read in full, and a file with no readable
    generation id are each named (on stderr, or as a ``CORRUPT`` line) and skipped, never a crash."""
    try:
        active = store.read_active(scope)
    except Exception as exc:  # noqa: BLE001 - a damaged pointer must not stop the listing.
        active = None
        print(f"rollback: the active pointer is unreadable ({type(exc).__name__})", file=sys.stderr)
    for generation_id in store.list_generations(scope):
        try:
            manifest = store.read_generation(scope, generation_id)
            stats = manifest.payload.get("stats")
            if not (
                isinstance(stats, dict)
                and all(kind in stats for kind in ("symbols", "docs", "examples", "total"))
            ):
                stats = payload_stats(manifest.payload)
            marker = "ACTIVE" if generation_id == active else "      "
            line = (
                f"{generation_id} {marker} symbols={stats['symbols']} docs={stats['docs']} "
                f"examples={stats['examples']} total={stats['total']}"
            )
        except Exception as exc:  # noqa: BLE001 - one corrupt generation must not hide the others.
            marker = "ACTIVE" if generation_id == active else "      "
            line = f"{generation_id} {marker} CORRUPT ({type(exc).__name__})"
        print(line)
    for name, reason in store.unlisted_generation_files(scope):
        print(f"rollback: skipped {name}: {reason}", file=sys.stderr)


def _flip_back(
    store: GenerationManifestStore, scope: str, held_by: str, flipped_to: str, previous: str | None
) -> bool:
    """Point the scope back at *previous*. False when there was nothing to go back to or it failed."""
    if previous is None:
        return False
    try:
        lease = store.acquire_lease(scope, held_by, "pending")
        rollback_generation(store, scope, flipped_to, previous, lease)
    except Exception as exc:  # noqa: BLE001 - reported to the operator, who must act.
        print(f"rollback: could not flip back ({type(exc).__name__})", file=sys.stderr)
        return False
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--family", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--source-kind", required=True)
    parser.add_argument("--manifest-store", type=Path, required=True)
    parser.add_argument("--held-by", default="rollback")
    parser.add_argument(
        "--force",
        action="store_true",
        help="skip the safety checks (production floors, repository identity, regression limits) and keep "
        "the target even when the post-flip readiness probe fails",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--target-generation",
        help="the generation id to roll back to (must already have been published)",
    )
    mode.add_argument(
        "--list",
        action="store_true",
        help="list the scope's stored generations, newest first, marking the active one",
    )
    mode.add_argument(
        "--previous",
        action="store_true",
        help="roll back to the generation the last publish or rollback replaced",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns 0 on success and ``EXIT_REVERTED`` (3) when the post-flip probe failed and the
    pointer was flipped back; raises ``GenerationManifestError`` (``RollbackRefusedError`` for a refused
    target) for everything else."""
    args = _build_parser().parse_args(argv)

    store = GenerationManifestStore(args.manifest_store)
    scope = _SEP.join((args.family, args.platform, args.source_kind))
    if args.list:
        _list_generations(store, scope)
        return 0

    target = args.target_generation
    if args.previous:
        target = store.previous_generation(scope)
        if target is None:
            raise UnknownGenerationError(f"scope {scope!r} has no previous generation to roll back to")
    expected_active = store.read_active(scope)
    target_manifest = _validate_target(store, scope, target)
    sidecar = _read_product_reference(args.manifest_store, args.family, args.platform)
    if not args.force:
        active_manifest: GenerationManifest | None = None
        if expected_active is not None:
            try:
                active_manifest = store.read_generation(scope, expected_active)
            except GenerationManifestError:
                active_manifest = None  # a damaged active generation is the reason to roll back
        problems = _safety_problems(target_manifest, active_manifest) + _sidecar_problems(
            sidecar, target_manifest
        )
        if problems:
            raise RollbackRefusedError(
                f"refusing to roll back to {target}: " + "; ".join(problems) + "; pass --force to override"
            )
    lease = store.acquire_lease(scope, args.held_by, "pending")

    generation_id = rollback_generation(
        store,
        scope,
        expected_active,
        target,
        lease,
        validate=True,
    )
    print(f"rolled back to {generation_id}")

    report = readiness_report(DeploymentGenerationStore(store, scope))
    if not report.ready:
        reasons = "; ".join(report.reasons)
        if args.force:
            print(
                f"warning: the rolled-back generation is not ready ({reasons}); kept because of --force",
                file=sys.stderr,
            )
        else:
            reverted = _flip_back(store, scope, args.held_by, generation_id, expected_active)
            print(
                f"rollback: {generation_id} failed the readiness probe ({reasons}); "
                + (f"flipped back to {expected_active}" if reverted else "could NOT flip back, act now"),
                file=sys.stderr,
            )
            return EXIT_REVERTED if reverted else EXIT_REVERT_FAILED
    notice = _sidecar_notice(sidecar, target_manifest)
    if notice:
        print(notice)
    return 0


def cli() -> None:
    """The ``__main__`` wrapper: one line on stderr and a non-zero exit instead of a traceback."""
    try:
        code = main()
    except GenerationManifestError as exc:
        print(f"rollback: {exc}", file=sys.stderr)
        sys.exit(EXIT_REFUSED)
    sys.exit(code)


if __name__ == "__main__":
    cli()
