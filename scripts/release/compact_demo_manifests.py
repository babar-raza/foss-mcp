#!/usr/bin/env python3
"""Compact infra/demo-manifests/ for the lean standalone demo image (TC-346 / REQ-G3-002).

Usage:
    python scripts/release/compact_demo_manifests.py [--root DIR] [--check] [--dry-run]

Every 4096-dimension chunk vector bloats a generation file (tens of MB), yet the only consumer of the
vector index, find_examples' fallback, can return only chunks whose text contains the ``Example:`` marker.
For every scope directory under ``--root`` this tool:

  1. resolves the active generation id from ``active_pointer.json`` and keeps ONLY that generation file,
     deleting any other ``generation__*.json`` in the scope;
  2. in the kept generation's ``payload.vector_index.points`` keeps only the points whose ``text`` contains
     ``vector_index_writer.EXAMPLE_MARKER`` (order and every field of a kept point preserved; ``dimension``
     and ``generation_id`` untouched);
  3. leaves ``payload.lexical_index`` equal in content;
  4. rewrites the file as compact JSON (``separators=(",", ":")``, ``sort_keys=True``, UTF-8, one trailing
     newline), atomically (temp file in the same directory, then ``os.replace``).

The tool is OFFLINE: it makes no network or embedding call. It is idempotent - a second run changes nothing.

Exit codes: 0 success (and, with --check, "already compact"); 1 --check found a scope that is not compact;
2 a scope is unusable (pointer missing, pointer names a missing generation, malformed generation).

  --check    report only, change nothing, exit 1 if any scope is not already compact.
  --dry-run  report what would change, change nothing, exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "infra" / "demo-manifests"
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    # foss_mcp is not an installed package; the tool must also run as a plain script.
    sys.path.insert(0, str(_SRC))

from foss_mcp.indexing.vector_index_writer import EXAMPLE_MARKER  # noqa: E402

_SEP = "::"
ACTIVE_POINTER = "active_pointer.json"
GENERATION_GLOB = "generation__*.json"


class CompactionError(Exception):
    """A scope cannot be compacted; the message names the scope."""


@dataclass
class ScopeResult:
    scope: str
    old_bytes: int
    new_bytes: int
    kept: int
    dropped: int
    removed_files: list[str] = field(default_factory=list)
    rewrite: bool = False

    @property
    def changed(self) -> bool:
        return self.rewrite or bool(self.removed_files)


def scope_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        raise CompactionError(f"{root}: not a directory")
    return sorted(p for p in root.iterdir() if p.is_dir())


def active_generation_path(scope_dir: Path) -> tuple[str, Path]:
    """The active generation id and the file it names. Raises CompactionError naming the scope."""
    scope = scope_dir.name
    pointer = scope_dir / ACTIVE_POINTER
    if not pointer.is_file():
        raise CompactionError(f"{scope}: {ACTIVE_POINTER} is missing")
    try:
        generation_id = json.loads(pointer.read_text(encoding="utf-8"))["generation_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CompactionError(f"{scope}: {ACTIVE_POINTER} is unreadable ({exc!r})") from exc
    if not isinstance(generation_id, str) or not generation_id:
        raise CompactionError(f"{scope}: {ACTIVE_POINTER} has no generation_id")
    # Same derivation as GenerationManifestStore._generation_path_for_id: the version is
    # everything after the LAST separator.
    version = generation_id.rsplit(_SEP, 1)[-1]
    path = scope_dir / f"generation__{version}.json"
    if not path.is_file():
        raise CompactionError(f"{scope}: active pointer names {generation_id!r} but {path.name} is missing")
    return generation_id, path


def render(doc: dict[str, Any]) -> bytes:
    """The canonical compact serialization: sorted keys, no whitespace, UTF-8, one trailing newline."""
    text = json.dumps(doc, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def is_example_point(point: Any) -> bool:
    text = point.get("text") if isinstance(point, dict) else None
    return isinstance(text, str) and EXAMPLE_MARKER in text


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def compact_scope(scope_dir: Path, *, apply: bool) -> ScopeResult:
    """Compact one scope. With ``apply=False`` nothing on disk is touched."""
    scope = scope_dir.name
    generation_id, keep_path = active_generation_path(scope_dir)
    raw = keep_path.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
        payload = doc["payload"]
        vector_index = payload.get("vector_index")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CompactionError(f"{scope}: {keep_path.name} is malformed ({exc!r})") from exc
    if doc.get("generation_id") != generation_id:
        raise CompactionError(
            f"{scope}: {keep_path.name} carries generation_id {doc.get('generation_id')!r}, "
            f"but the active pointer names {generation_id!r}"
        )

    kept = dropped = 0
    if vector_index is not None:
        points = vector_index.get("points") if isinstance(vector_index, dict) else None
        if not isinstance(points, list):
            raise CompactionError(f"{scope}: payload.vector_index.points is not a list")
        examples = [p for p in points if is_example_point(p)]
        kept, dropped = len(examples), len(points) - len(examples)
        vector_index["points"] = examples

    data = render(doc)
    stale = [p for p in sorted(scope_dir.glob(GENERATION_GLOB)) if p != keep_path]
    result = ScopeResult(
        scope=scope,
        old_bytes=len(raw) + sum(p.stat().st_size for p in stale),
        new_bytes=len(data),
        kept=kept,
        dropped=dropped,
        removed_files=[p.name for p in stale],
        rewrite=data != raw,
    )
    if apply:
        if result.rewrite:
            _atomic_write_bytes(keep_path, data)
        for p in stale:
            p.unlink()
    return result


def _format(result: ScopeResult) -> str:
    note = f" removed {','.join(result.removed_files)}" if result.removed_files else ""
    state = "change" if result.changed else "ok"
    return (
        f"{result.scope}: {result.old_bytes} -> {result.new_bytes} bytes, "
        f"points kept {result.kept} dropped {result.dropped} [{state}]{note}"
    )


def run(root: Path, *, check: bool, dry_run: bool) -> int:
    apply = not (check or dry_run)
    dirs = scope_dirs(root)
    for d in dirs:
        # Resolve every pointer BEFORE changing anything: a broken scope aborts the whole run untouched.
        active_generation_path(d)
    results = []
    for d in dirs:
        result = compact_scope(d, apply=apply)
        results.append(result)
        print(_format(result))
    old_total = sum(r.old_bytes for r in results)
    new_total = sum(r.new_bytes for r in results)
    kept = sum(r.kept for r in results)
    dropped = sum(r.dropped for r in results)
    pending = sum(1 for r in results if r.changed)
    print(
        f"total: {len(results)} scopes, {old_total} -> {new_total} bytes, "
        f"points kept {kept} dropped {dropped}, {pending} scope(s) {'not compact' if pending else 'compact'}"
    )
    if check and pending:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compact the committed demo manifests.")
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT, help="manifest root (default: %(default)s)"
    )
    parser.add_argument(
        "--check", action="store_true", help="report only; exit 1 if any scope is not compact"
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would change; change nothing")
    args = parser.parse_args(argv)
    try:
        return run(args.root, check=args.check, dry_run=args.dry_run)
    except CompactionError as exc:
        print(f"compact_demo_manifests: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
