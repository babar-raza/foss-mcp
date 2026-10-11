#!/usr/bin/env python3
"""Snapshot each pilot's active generation and its sidecars from the live cluster into the demo data
(G3/TC-349 / REQ-G3-002).

Usage:
    python scripts/release/snapshot_demo_from_cluster.py --namespace foss-mcp [--root DIR]
        [--only FAMILY_PLATFORM ...] [--allow-missing-sidecars] [--dry-run]

infra/demo-manifests/ is what the standalone demo image bakes in. It must hold exactly what the live
chart serves: per pilot the active generation AND the three sidecar files the server reads from the
manifests root (product_reference_<f>_<p>.json, recent_releases_<f>_<p>.json, package_registry_<f>_<p>.json).
For each selected pilot this tool:

  1. finds the pilot's serving pod by the label selector the installer uses
     (`app.kubernetes.io/instance=<release>,app.kubernetes.io/component=serving`) and requires phase
     Running and the Ready condition True - otherwise the pilot FAILS and nothing is written for it;
  2. streams `kubectl exec ... -c serving -- tar cf - -C /data/manifests .` as BYTES and unpacks it in memory
     (tarfile, filter="data"), refusing the whole archive on any absolute path, `..` component, link or
     special member;
  3. requires the pilot's scope directory with active_pointer.json and the generation file it names, and
     requires that generation's lexical index to hold real chunks - a well-formed empty file is a failure;
  4. requires all three sidecars (the file names come from the same functions the server reads them
     with); a missing one FAILS the pilot and is named, unless --allow-missing-sidecars;
  5. writes into --root atomically per pilot: the scope directory is replaced by ONLY the pointer and the
     compacted active generation, and the sidecars are written at the root. A failed pilot leaves its
     existing files untouched; an unchanged pilot is not rewritten at all.

Finally the TC-346 compaction (compact_demo_manifests) runs over --root. The only kubectl commands this
tool ever runs are `get pods` and `exec ... tar`; it never prints a credential or any value from the
environment.

Exit codes: 0 every selected pilot succeeded; 1 at least one pilot (or the final compaction) failed;
2 bad arguments (unknown --only name, invalid namespace, unreadable chart).

  --dry-run  read and validate from the cluster, print what would be written, write nothing.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PureWindowsPath
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_DIR = REPO_ROOT / "scripts" / "release"
_DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
for _dir in (_RELEASE_DIR, _DEPLOY_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import compact_demo_manifests as compact  # noqa: E402  (also puts src/ on sys.path)
import install_pilots  # noqa: E402
import pilots  # noqa: E402

SERVING_CONTAINER = "serving"
SERVING_COMPONENT = "serving"
MANIFESTS_MOUNT = "/data/manifests"
ACTIVE_POINTER = compact.ACTIVE_POINTER
STAGE_PREFIX = ".snapshot-stage-"
GET_TIMEOUT_SECONDS = 60
EXEC_TIMEOUT_SECONDS = 900
REPLACE_ATTEMPTS = 5

# The generation version is everything after the last "::" of the pointer's generation_id; it becomes part of
# a file name, so it is restricted to a safe alphabet.
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*")
# Kubernetes DNS-1123 label (namespace) and subdomain (pod name).
_NAMESPACE = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")
_POD_NAME = re.compile(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?")

_INFRA_SIDECAR_HELPERS = (
    # (label, infra module file, function)
    ("product_reference", "fetch_product_reference.py", "sidecar_name"),
    ("recent_releases", "fetch_recent_releases.py", "recent_releases_sidecar_name"),
    ("package_registry", "verify_package_registry.py", "verify_package_registry_sidecar_name"),
)


class SnapshotError(Exception):
    """A pilot cannot be snapshotted; the message says why and is printed on that pilot's line."""


@dataclass
class PilotResult:
    pilot: str
    ok: bool = False
    status: str = ""
    message: str = ""
    chunks: int = 0
    points_kept: int = 0
    generation_bytes: int = 0
    compacted: bool = False
    sidecar_bytes: dict[str, int | None] = field(default_factory=dict)
    scope: str = ""
    generation_file: str = ""


# ---------------------------------------------------------------------------------------------------
# sidecar names - always derived from the functions the server reads them with
# ---------------------------------------------------------------------------------------------------


@cache
def _infra_function(filename: str, function: str) -> Any:
    path = REPO_ROOT / "infra" / filename
    spec = importlib.util.spec_from_file_location(f"_snapshot_infra_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise SnapshotError(f"cannot load {path}")
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function)


def sidecar_names(family: str, platform: str) -> dict[str, str]:
    """label -> file name of the pilot's three sidecars, in a fixed order."""
    return {
        label: _infra_function(filename, function)(family, platform)
        for label, filename, function in _INFRA_SIDECAR_HELPERS
    }


# ---------------------------------------------------------------------------------------------------
# kubectl - only `get pods` and `exec ... tar`
# ---------------------------------------------------------------------------------------------------


def selector_for(pilot: dict[str, Any]) -> str:
    return (
        f"{install_pilots.INSTANCE_LABEL}={pilots.release_name(pilot)},"
        f"{install_pilots.COMPONENT_LABEL}={SERVING_COMPONENT}"
    )


def get_pods_command(namespace: str, pilot: dict[str, Any]) -> list[str]:
    return ["kubectl", "get", "pods", "-n", namespace, "-l", selector_for(pilot), "-o", "json"]


def export_command(namespace: str, pod: str) -> list[str]:
    return [
        "kubectl",
        "exec",
        "-n",
        namespace,
        pod,
        "-c",
        SERVING_CONTAINER,
        "--",
        "tar",
        "cf",
        "-",
        "-C",
        MANIFESTS_MOUNT,
        ".",
    ]


def _assert_allowed(cmd: list[str]) -> None:
    is_get = cmd[:3] == ["kubectl", "get", "pods"]
    is_export = cmd[:2] == ["kubectl", "exec"] and cmd[-6:] == ["tar", "cf", "-", "-C", MANIFESTS_MOUNT, "."]
    if not (is_get or is_export):
        raise SnapshotError("refusing to run a kubectl command other than `get pods` and `exec ... tar`")


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    _assert_allowed(cmd)
    return subprocess.run(cmd, capture_output=True, check=False, timeout=timeout)


def _as_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def _tail(value: Any, limit: int = 200) -> str:
    text = _as_bytes(value).decode("utf-8", errors="replace")
    return " ".join(text.split())[-limit:]


def ready_serving_pod(namespace: str, pilot: dict[str, Any]) -> str:
    """The name of the pilot's serving pod; raises SnapshotError unless one is Running and Ready."""
    selector = selector_for(pilot)
    proc = _run(get_pods_command(namespace, pilot), GET_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise SnapshotError(f"kubectl get pods failed (rc {proc.returncode}): {_tail(proc.stderr)}")
    try:
        items = json.loads(_as_bytes(proc.stdout).decode("utf-8"))["items"]
        if not isinstance(items, list):
            raise TypeError("items is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        raise SnapshotError(f"kubectl get pods returned unreadable output ({type(exc).__name__})") from exc
    if not items:
        raise SnapshotError(f"no serving pod matches {selector}")
    ready: list[str] = []
    states: list[str] = []
    for item in items:
        meta = item.get("metadata") if isinstance(item, dict) else None
        status = item.get("status") if isinstance(item, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        status = status if isinstance(status, dict) else {}
        name = meta.get("name")
        conditions = status.get("conditions")
        is_ready = isinstance(conditions, list) and any(
            isinstance(c, dict) and c.get("type") == "Ready" and c.get("status") == "True" for c in conditions
        )
        phase = status.get("phase")
        terminating = bool(meta.get("deletionTimestamp"))
        states.append(f"{name} phase={phase} ready={is_ready}{' terminating' if terminating else ''}")
        if (
            isinstance(name, str)
            and _POD_NAME.fullmatch(name)
            and phase == "Running"
            and is_ready
            and not terminating
        ):
            ready.append(name)
    if not ready:
        raise SnapshotError("no serving pod is Running and Ready: " + "; ".join(states))
    return sorted(ready)[0]


def export_manifests(namespace: str, pod: str) -> bytes:
    proc = _run(export_command(namespace, pod), EXEC_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise SnapshotError(f"kubectl exec tar failed (rc {proc.returncode}): {_tail(proc.stderr)}")
    data = _as_bytes(proc.stdout)
    if not data:
        raise SnapshotError("kubectl exec tar returned no data")
    return data


# ---------------------------------------------------------------------------------------------------
# the archive - unpacked in memory, every member vetted before anything is read
# ---------------------------------------------------------------------------------------------------


def _safe_relpath(member: tarfile.TarInfo) -> str:
    """The member's path relative to the archive root, or SnapshotError for anything unsafe."""
    name = member.name
    if not name or "\x00" in name:
        raise SnapshotError(f"unsafe archive member: empty or NUL-bearing name {name!r}")
    if "\\" in name:
        raise SnapshotError(f"unsafe archive member {name!r}: backslash in path")
    windows = PureWindowsPath(name)
    if name.startswith("/") or windows.is_absolute() or windows.drive:
        raise SnapshotError(f"unsafe archive member {name!r}: absolute path")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise SnapshotError(f"unsafe archive member {name!r}: '..' path component")
    if member.issym() or member.islnk():
        raise SnapshotError(f"unsafe archive member {name!r}: link member")
    if not (member.isreg() or member.isdir()):
        raise SnapshotError(f"unsafe archive member {name!r}: not a regular file or directory")
    return "/".join(parts)


def read_archive(data: bytes, wanted: Callable[[str], bool], *, dest: Path) -> dict[str, bytes]:
    """The bytes of every regular file whose relative path ``wanted(rel)`` accepts.

    The whole archive is vetted first (any unsafe member refuses it), each member also passes
    tarfile's ``data`` filter, and nothing is ever extracted to disk.
    """
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    except tarfile.TarError as exc:
        raise SnapshotError(f"the archive is not a readable tar ({type(exc).__name__}: {exc})") from exc
    files: dict[str, bytes] = {}
    with archive:
        try:
            members = archive.getmembers()
            vetted: list[tuple[str, tarfile.TarInfo]] = []
            for member in members:
                rel = _safe_relpath(member)
                tarfile.data_filter(member, str(dest))
                vetted.append((rel, member))
            for rel, member in vetted:
                if member.isreg() and rel and wanted(rel):
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise SnapshotError(f"archive member {member.name!r} cannot be read")
                    files[rel] = handle.read()
        except tarfile.TarError as exc:
            raise SnapshotError(f"the archive was refused ({type(exc).__name__}: {exc})") from exc
    return files


# ---------------------------------------------------------------------------------------------------
# validation of what was fetched
# ---------------------------------------------------------------------------------------------------


def generation_file_name(scope: str, pointer: bytes, pilot_scope: str) -> tuple[str, str]:
    """(generation_id, generation file name) named by the pointer; SnapshotError if it is unusable."""
    try:
        generation_id = json.loads(pointer.decode("utf-8"))["generation_id"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise SnapshotError(f"{scope}: {ACTIVE_POINTER} is unreadable ({type(exc).__name__})") from exc
    if not isinstance(generation_id, str) or not generation_id:
        raise SnapshotError(f"{scope}: {ACTIVE_POINTER} has no generation_id")
    if not generation_id.startswith(pilot_scope + "::"):
        raise SnapshotError(f"{scope}: the active pointer names {generation_id!r}, another pilot's scope")
    version = generation_id.rsplit("::", 1)[-1]
    if _VERSION.fullmatch(version) is None or ".." in version:
        raise SnapshotError(f"{scope}: the active generation version {version!r} is not a safe file name")
    return generation_id, f"generation__{version}.json"


def inspect_generation(scope: str, raw: bytes) -> tuple[int, int]:
    """(chunks, example vector points) of a generation file; SnapshotError unless it holds real content."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotError(f"{scope}: the generation file is not valid JSON ({type(exc).__name__})") from exc
    payload = doc.get("payload") if isinstance(doc, dict) else None
    if not isinstance(payload, dict):
        raise SnapshotError(f"{scope}: the generation file has no payload")
    vector_index = payload.get("vector_index")
    points = vector_index.get("points") if isinstance(vector_index, dict) else None
    kept = sum(1 for p in points if compact.is_example_point(p)) if isinstance(points, list) else 0
    lexical = payload.get("lexical_index")
    index = lexical if isinstance(lexical, dict) else {}
    chunks = index.get("documents")
    if not isinstance(chunks, dict) or not chunks:
        raise SnapshotError(
            f"{scope}: the generation's lexical index is empty - there is nothing real to serve"
        )
    return len(chunks), kept


def _check_sidecar(name: str, data: bytes) -> None:
    try:
        json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotError(f"sidecar {name} is not valid JSON ({type(exc).__name__})") from exc


# ---------------------------------------------------------------------------------------------------
# writing - staged beside the target, then swapped in; a failed swap is rolled back
# ---------------------------------------------------------------------------------------------------


def _replace(src: Path, dst: Path) -> None:
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def _dir_files(path: Path) -> dict[str, bytes] | None:
    """name -> bytes of a flat directory, or None if it is absent or holds anything but plain files."""
    if not path.is_dir():
        return None
    entries = sorted(path.iterdir())
    if not all(p.is_file() for p in entries):
        return None
    return {p.name: p.read_bytes() for p in entries}


def apply_pilot(
    root: Path,
    scope: str,
    pointer: bytes,
    generation_name: str,
    generation: bytes,
    sidecars: dict[str, bytes],
) -> tuple[str, int]:
    """Replace the pilot's files in root. Returns ("written" | "unchanged", compacted generation bytes)."""
    stage = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=root))
    try:
        new_scope = stage / scope
        new_scope.mkdir()
        (new_scope / ACTIVE_POINTER).write_bytes(pointer)
        (new_scope / generation_name).write_bytes(generation)
        compact.compact_scope(new_scope, apply=True)
        generation_bytes = (new_scope / generation_name).stat().st_size
        staged_sidecars = stage / "sidecars"
        staged_sidecars.mkdir()
        for name, data in sidecars.items():
            (staged_sidecars / name).write_bytes(data)

        final = root / scope
        same_scope = _dir_files(final) == _dir_files(new_scope)
        same_sidecars = all(
            (root / name).is_file() and (root / name).read_bytes() == data for name, data in sidecars.items()
        )
        if same_scope and same_sidecars:
            return "unchanged", generation_bytes

        old = stage / f"{scope}.old"
        moved_old = installed = False
        undo: list[tuple[Path, bytes | None]] = []
        try:
            if final.exists():
                _replace(final, old)
                moved_old = True
            _replace(new_scope, final)
            installed = True
            for name in sidecars:
                target = root / name
                undo.append((target, target.read_bytes() if target.is_file() else None))
                _replace(staged_sidecars / name, target)
        except BaseException:
            if installed:
                shutil.rmtree(final, ignore_errors=True)
            if moved_old:
                _replace(old, final)
            for target, prior in undo:
                if prior is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(prior)
            raise
        return "written", generation_bytes
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------
# one pilot
# ---------------------------------------------------------------------------------------------------


def snapshot_pilot(
    pilot: dict[str, Any],
    namespace: str,
    root: Path,
    *,
    allow_missing_sidecars: bool,
    dry_run: bool,
) -> PilotResult:
    result = PilotResult(pilot=pilots.pilot_key(pilot))
    try:
        _snapshot_pilot(pilot, namespace, root, allow_missing_sidecars, dry_run, result)
    except (
        SnapshotError,
        compact.CompactionError,
        tarfile.TarError,
        subprocess.SubprocessError,
        OSError,
        ValueError,
    ) as exc:
        result.ok = False
        result.status = "failed"
        result.message = str(exc) or type(exc).__name__
    return result


def _snapshot_pilot(
    pilot: dict[str, Any],
    namespace: str,
    root: Path,
    allow_missing_sidecars: bool,
    dry_run: bool,
    result: PilotResult,
) -> None:
    family, platform, source_kind = pilot["family"], pilot["platform"], pilot["sourceKind"]
    pilot_scope = "::".join((family, platform, source_kind))
    scope = "__".join((family, platform, source_kind))
    result.scope = scope
    names = sidecar_names(family, platform)

    pod = ready_serving_pod(namespace, pilot)
    data = export_manifests(namespace, pod)

    exact = {f"{scope}/{ACTIVE_POINTER}", *names.values()}

    def wanted(rel: str) -> bool:
        if rel in exact:
            return True
        head, _, tail = rel.partition("/")
        return (
            head == scope and "/" not in tail and tail.startswith("generation__") and tail.endswith(".json")
        )

    files = read_archive(data, wanted, dest=root.resolve())

    pointer = files.get(f"{scope}/{ACTIVE_POINTER}")
    if pointer is None:
        raise SnapshotError(f"{scope}: the cluster has no {ACTIVE_POINTER} for this pilot")
    _, generation_name = generation_file_name(scope, pointer, pilot_scope)
    generation = files.get(f"{scope}/{generation_name}")
    if generation is None:
        raise SnapshotError(
            f"{scope}: the active pointer names {generation_name} but the cluster has no such file"
        )
    result.chunks, result.points_kept = inspect_generation(scope, generation)
    result.generation_bytes = len(generation)
    result.generation_file = generation_name

    sidecars: dict[str, bytes] = {}
    missing: list[str] = []
    for label, name in names.items():
        content = files.get(name)
        if content is None:
            missing.append(name)
            result.sidecar_bytes[label] = None
            continue
        _check_sidecar(name, content)
        sidecars[name] = content
        result.sidecar_bytes[label] = len(content)
    if missing and not allow_missing_sidecars:
        raise SnapshotError(f"{scope}: missing sidecar(s) {', '.join(missing)}")

    if dry_run:
        result.ok, result.status = True, "dry-run"
        return
    result.status, result.generation_bytes = apply_pilot(
        root, scope, pointer, generation_name, generation, sidecars
    )
    result.compacted = True
    result.ok = True


# ---------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------


def compact_root(root: Path) -> dict[str, compact.ScopeResult]:
    """The TC-346 compaction over every scope in root, after every pointer has been resolved."""
    dirs = [d for d in compact.scope_dirs(root) if not d.name.startswith(".")]
    for d in dirs:
        compact.active_generation_path(d)
    return {d.name: compact.compact_scope(d, apply=True) for d in dirs}


def _remove_stale_stages(root: Path) -> None:
    for leftover in root.glob(f"{STAGE_PREFIX}*"):
        shutil.rmtree(leftover, ignore_errors=True)


def tree_size(root: Path) -> tuple[int, int]:
    files = [p for p in root.rglob("*") if p.is_file()] if root.is_dir() else []
    return sum(p.stat().st_size for p in files), len(files)


def format_result(result: PilotResult) -> str:
    if not result.ok:
        return f"{result.pilot}: FAILED {result.message}"
    sidecars = " ".join(
        f"{label}={'MISSING' if size is None else f'{size}B'}" for label, size in result.sidecar_bytes.items()
    )
    raw = "" if result.compacted else "(raw)"
    return (
        f"{result.pilot}: ok [{result.status}] chunks={result.chunks} "
        f"vector_points_kept={result.points_kept} generation={result.generation_bytes}B{raw} {sidecars}"
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Snapshot each pilot's active generation and sidecars from the live cluster."
    )
    parser.add_argument("--namespace", required=True, help="Kubernetes namespace of the pilot releases")
    parser.add_argument(
        "--root", type=Path, default=compact.DEFAULT_ROOT, help="manifest root (default: %(default)s)"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FAMILY_PLATFORM",
        help="snapshot only this pilot, e.g. pdf_net (repeatable)",
    )
    parser.add_argument(
        "--allow-missing-sidecars",
        action="store_true",
        help="do not fail a pilot whose cluster copy lacks a sidecar (the gap is still reported)",
    )
    parser.add_argument("--dry-run", action="store_true", help="read and validate; write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if _NAMESPACE.fullmatch(args.namespace) is None:
        print(
            "snapshot_demo_from_cluster: --namespace is not a valid Kubernetes namespace name",
            file=sys.stderr,
        )
        return 2
    try:
        selected = pilots.select_pilots(pilots.load_pilots(), args.only)
    except (ValueError, OSError) as exc:
        print(f"snapshot_demo_from_cluster: {exc}", file=sys.stderr)
        return 2

    root: Path = args.root
    if not args.dry_run:
        root.mkdir(parents=True, exist_ok=True)
        _remove_stale_stages(root)

    results: list[PilotResult] = []
    for pilot in selected:
        result = snapshot_pilot(
            pilot,
            args.namespace,
            root,
            allow_missing_sidecars=args.allow_missing_sidecars,
            dry_run=args.dry_run,
        )
        results.append(result)
        print(format_result(result), flush=True)

    compaction_failed = False
    if not args.dry_run:
        try:
            compact_root(root)
        except compact.CompactionError as exc:
            compaction_failed = True
            print(f"snapshot_demo_from_cluster: compaction failed: {exc}", file=sys.stderr)

    failed = [r for r in results if not r.ok]
    size, count = tree_size(root)
    print(
        f"total: {len(results) - len(failed)} of {len(results)} pilot(s) ok, {len(failed)} failed; "
        f"tree {size} bytes in {count} file(s) under {root}"
        f"{' (dry run, nothing written)' if args.dry_run else ''}"
    )
    return 1 if failed or compaction_failed else 0


if __name__ == "__main__":
    sys.exit(main())
