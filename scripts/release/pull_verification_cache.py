#!/usr/bin/env python3
"""Pull a cluster run's verification cache back into the repository (REQ-G3-007, TC-441).

Usage:
    python scripts/release/pull_verification_cache.py --namespace NS
        [--only FAMILY_PLATFORM ...] [--dest DIR] [--dry-run]

infra/helm/foss-mcp/templates/ingestion-job.yaml's own ``--verification-cache-out`` writes each
furnished pilot's merged verification cache to ``verification_cache_<family>_<platform>.json`` on
the manifests volume - real compiler/interpreter verdicts that would otherwise live only inside
that one cluster and be thrown away the moment the Job's ``/tmp`` emptyDir is reclaimed. This tool
is the other half: for each selected pilot it

  1. finds the pilot's serving pod with ``snapshot_demo_from_cluster``'s own ``ready_serving_pod``
     (the exact same Ready/Running check the demo snapshot tool uses - imported, never re-typed);
  2. streams `kubectl exec ... -c serving -- tar cf - -C /data/manifests
     verification_cache_<family>_<platform>.json` and unpacks ONLY that one archive member in
     memory, the same safe-unpacking rules ``snapshot_demo_from_cluster.read_archive`` already
     enforces (refusing an absolute path, a ``..`` component, or a link);
  3. validates the pulled cache (schema_version 1, every entry's eight documented fields) with
     ``infra/build_chunks.py``'s own ``parse_verification_cache`` - never a second copy of the
     rules;
  4. merges it into ``<dest>/<family>_<platform>.json`` with ``build_chunks.merge_verification_cache``
     (cluster verdicts win on an identical ``(provenance, code_sha256, library_commit, platform,
     toolchain)`` key; entries of other keys, from other commits or toolchains, are kept), and
     writes it back atomically and only when its bytes change.

The only kubectl commands this tool ever runs are `get pods` (inside ready_serving_pod) and the
single-file `exec ... tar` above; anything else is refused before it reaches subprocess.

Exit codes: 0 every selected pilot was written, unchanged or absent; 1 at least one pilot failed;
2 bad arguments (unknown --only name, invalid pilot list).

  --dry-run  read, validate and report what would be written; write nothing.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = REPO_ROOT / "src"
_INFRA = REPO_ROOT / "infra"
_RELEASE_DIR = REPO_ROOT / "scripts" / "release"
_DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
for _dir in (_SRC, _INFRA, _RELEASE_DIR, _DEPLOY_DIR):
    # Neither foss_mcp nor infra/ is an installed package; this tool must also run as a plain
    # script (not only under pytest, which already puts these on sys.path via conftest.py).
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import build_chunks  # noqa: E402
import pilots  # noqa: E402
import snapshot_demo_from_cluster as snapshot_tool  # noqa: E402

DEFAULT_DEST = REPO_ROOT / "tests" / "fixtures" / "verified_examples"
MANIFESTS_MOUNT = snapshot_tool.MANIFESTS_MOUNT
EXPORT_TIMEOUT_SECONDS = 120


class PullError(Exception):
    """One pilot's verification cache cannot be pulled; the message says why."""


@dataclass
class PullResult:
    pilot: str
    status: str  # "written" | "unchanged" | "absent" | "failed"
    message: str = ""


def cluster_cache_filename(family: str, platform: str) -> str:
    """The name the ingestion Job's ``--verification-cache-out`` writes under the manifests
    volume for this pilot - never ``<family>_<platform>.json`` alone, which is the REPOSITORY's
    own fixture name (TC-441's two paths are deliberately distinct)."""
    return f"verification_cache_{family}_{platform}.json"


def export_command(namespace: str, pod: str, filename: str) -> list[str]:
    return [
        "kubectl",
        "exec",
        "-n",
        namespace,
        pod,
        "-c",
        snapshot_tool.SERVING_CONTAINER,
        "--",
        "tar",
        "cf",
        "-",
        "-C",
        MANIFESTS_MOUNT,
        filename,
    ]


def _assert_allowed(cmd: list[str]) -> None:
    is_get = cmd[:3] == ["kubectl", "get", "pods"]
    tail = cmd[-6:]
    is_export = (
        cmd[:2] == ["kubectl", "exec"]
        and len(tail) == 6
        and tail[:5] == ["tar", "cf", "-", "-C", MANIFESTS_MOUNT]
        and tail[5].startswith("verification_cache_")
        and tail[5].endswith(".json")
    )
    if not (is_get or is_export):
        raise PullError("refusing to run a kubectl command other than `get pods` and `exec ... tar`")


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    _assert_allowed(cmd)
    return subprocess.run(cmd, capture_output=True, check=False, timeout=timeout)


def _tail(value: bytes, limit: int = 200) -> str:
    text = value.decode("utf-8", errors="replace")
    return " ".join(text.split())[-limit:]


def export_cache_bytes(namespace: str, pod: str, filename: str, *, dest: Path) -> bytes | None:
    """The raw bytes of ``filename`` under ``/data/manifests`` inside ``pod``, or ``None`` when the
    pulled archive simply has no member of that name (TC-441: "absent" is a valid outcome, not a
    failure - a pilot whose ingestion Job has never run with the cache flags looks exactly like
    this). Raises ``PullError`` only for a real command or archive failure.
    """
    proc = _run(export_command(namespace, pod, filename), EXPORT_TIMEOUT_SECONDS)
    data = proc.stdout
    if not data:
        if proc.returncode != 0:
            raise PullError(f"kubectl exec tar failed (rc {proc.returncode}): {_tail(proc.stderr)}")
        return None
    try:
        files = snapshot_tool.read_archive(data, lambda rel: rel == filename, dest=dest)
    except snapshot_tool.SnapshotError as exc:
        if proc.returncode != 0:
            raise PullError(f"kubectl exec tar failed (rc {proc.returncode}): {_tail(proc.stderr)}") from exc
        raise PullError(f"the archive is unreadable: {exc}") from exc
    return files.get(filename)


def _pull_pilot(pilot: dict, namespace: str, dest: Path, *, dry_run: bool) -> PullResult:
    key = pilots.pilot_key(pilot)
    try:
        pod = snapshot_tool.ready_serving_pod(namespace, pilot)
        filename = cluster_cache_filename(pilot["family"], pilot["platform"])
        data = export_cache_bytes(namespace, pod, filename, dest=dest)
        if data is None:
            return PullResult(pilot=key, status="absent")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            return PullResult(pilot=key, status="failed", message=f"not valid UTF-8 ({exc})")
        try:
            cluster_entries = build_chunks.parse_verification_cache(text)
        except ValueError as exc:
            return PullResult(pilot=key, status="failed", message=f"cluster cache is malformed: {exc}")

        dest_path = dest / f"{key}.json"
        existing_entries: list[dict] = []
        if dest_path.exists():
            try:
                existing_entries = build_chunks.parse_verification_cache(
                    dest_path.read_text(encoding="utf-8")
                )
            except ValueError as exc:
                return PullResult(pilot=key, status="failed", message=f"existing cache is malformed: {exc}")

        merged = build_chunks.merge_verification_cache(existing_entries, cluster_entries)
        payload = build_chunks.verification_cache_document(merged)
        changed = not dest_path.exists() or dest_path.read_text(encoding="utf-8") != payload
        if not dry_run and changed:
            build_chunks.write_verification_cache_if_changed(dest_path, merged)
        return PullResult(pilot=key, status="written" if changed else "unchanged")
    except (snapshot_tool.SnapshotError, PullError, subprocess.SubprocessError, OSError) as exc:
        return PullResult(pilot=key, status="failed", message=str(exc) or type(exc).__name__)


def format_result(result: PullResult) -> str:
    if result.status == "failed":
        return f"{result.pilot}: failed: {result.message}"
    return f"{result.pilot}: {result.status}"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull each pilot's cluster verification cache back into the repository."
    )
    parser.add_argument("--namespace", required=True, help="Kubernetes namespace of the pilot releases")
    parser.add_argument(
        "--dest", type=Path, default=DEFAULT_DEST, help="verification cache directory (default: %(default)s)"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FAMILY_PLATFORM",
        help="pull only this pilot, e.g. pdf_net (repeatable)",
    )
    parser.add_argument("--dry-run", action="store_true", help="read and validate; write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        selected = pilots.select_pilots(pilots.load_pilots(), args.only)
    except (ValueError, OSError) as exc:
        print(f"pull_verification_cache: {exc}", file=sys.stderr)
        return 2

    if not args.dry_run:
        args.dest.mkdir(parents=True, exist_ok=True)

    failed = False
    for pilot in selected:
        result = _pull_pilot(pilot, args.namespace, args.dest, dry_run=args.dry_run)
        print(format_result(result), flush=True)
        if result.status == "failed":
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
