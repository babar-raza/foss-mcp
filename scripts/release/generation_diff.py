#!/usr/bin/env python3
"""Per-pilot generation diff report for the demo manifests (TC-568 / REQ-G3-009, rows RD3-164 and RD3-165).

Usage:
    python scripts/release/generation_diff.py OLD_DIR NEW_DIR
    python scripts/release/generation_diff.py --git-rev REV [NEW_DIR]      # tree at REV  vs  NEW_DIR / the checkout
    python scripts/release/generation_diff.py --git-rev REV --new-rev REV2 # tree at REV  vs  tree at REV2
    options: --max-removed-symbols N  --max-signature-changes N  --examples N  --list-limit N  --format json|text

The human-reviewable summary of any re-ingest and the guard for the closing one. Deterministic and offline
(standard library plus ``git show`` for revisions; no network, no docker, no clock). For every pilot
(``family/platform``) it compares the ACTIVE generation of the two manifest trees and reports:

  * ``generation`` and ``source_commit`` old/new/changed;
  * ``symbols``: added, removed and changed by FQN (counts plus the first ``--list-limit`` names). A symbol is
    changed when its chunk text differs after the ``Source-Commit:`` and ``API-Coverage:`` lines are dropped,
    because those two lines change in every symbol of every re-ingest and would hide the real changes;
  * ``members``: totals and the symbols whose member count changed (member lines of type symbols);
  * ``signature_lines``: for symbols present on both sides, the bulleted signature lines removed and added
    (``changed`` is removed + added) with the first ``--examples`` symbols as before/after examples;
  * ``documentation`` and ``examples``: chunks added and removed (identity is the chunk text, not its id,
    which differs between generations) with titles, and the totals and delta.

A tree is a directory of scope directories (``infra/demo-manifests``) or a git revision of that directory,
read through ``git show``. A pilot on one side only has status ``added`` or ``removed``.

Ceilings, applied to EVERY pilot separately, make the tool exit 1 when exceeded:
  --max-removed-symbols N    a pilot lost more than N symbols (removed pilots count all their symbols);
  --max-signature-changes N  a pilot has more than N signature lines changed (removed + added).

Exit codes: 0 success (no ceiling exceeded, or none given); 1 a ceiling was exceeded; 2 an input is
unusable (missing directory, bad revision, no active pointer, malformed generation).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import claims_ledger as cl  # noqa: E402

MANIFEST_PREFIX = "infra/demo-manifests"
IGNORED_LINE_PREFIXES = ("Source-Commit:", "API-Coverage:")
GIT_TIMEOUT_SECONDS = 300


class DiffError(Exception):
    """An input is unusable; the message names it."""


# --------------------------------------------------------------------------- trees


class DirTree:
    """A manifest tree on disk."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise DiffError(f"{self.root}: not a directory")

    def scopes(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def read(self, scope: str, name: str) -> bytes | None:
        path = self.root / scope / name
        return path.read_bytes() if path.is_file() else None


class GitTree:
    """The manifest tree as committed at a git revision, read with ``git ls-tree`` and ``git show``."""

    def __init__(self, rev: str, repo: Path | None = None, prefix: str = MANIFEST_PREFIX) -> None:
        self.rev = rev
        self.repo = Path(repo) if repo is not None else REPO_ROOT
        self.prefix = prefix.strip("/")
        out = self._git("ls-tree", "-r", "--name-only", "-z", rev, "--", self.prefix + "/")
        self._files: set[str] = {p for p in out.decode("utf-8").split("\0") if p}
        if not self._files:
            raise DiffError(f"{rev}: no files under {self.prefix}/")

    def _git(self, *args: str) -> bytes:
        try:
            done = subprocess.run(
                ["git", "-C", str(self.repo), *args],
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiffError(f"git {args[0]}: {exc!r}") from exc
        if done.returncode != 0:
            detail = done.stderr.decode("utf-8", "replace").strip()
            raise DiffError(f"git {' '.join(args[:3])} failed: {detail}")
        return done.stdout

    def scopes(self) -> list[str]:
        head = self.prefix + "/"
        return sorted({f[len(head) :].split("/", 1)[0] for f in self._files if "/" in f[len(head) :]})

    def read(self, scope: str, name: str) -> bytes | None:
        path = f"{self.prefix}/{scope}/{name}"
        if path not in self._files:
            return None
        return self._git("show", f"{self.rev}:{path}")


# --------------------------------------------------------------------------- snapshots


def _normalised(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if not line.startswith(IGNORED_LINE_PREFIXES))


def _bullets(text: str) -> list[str]:
    return [line[4:] for line in text.split("\n") if line.startswith("  - ")]


def _load_json(raw: bytes | None, what: str) -> Any:
    if raw is None:
        raise DiffError(f"{what}: missing")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise DiffError(f"{what}: unreadable ({exc!r})") from exc


def snapshot(tree: DirTree | GitTree, scope: str) -> dict[str, Any]:
    """The comparable content of a scope's active generation."""
    pointer = _load_json(tree.read(scope, cl.ACTIVE_POINTER), f"{scope}/{cl.ACTIVE_POINTER}")
    generation_id = pointer.get("generation_id") if isinstance(pointer, dict) else None
    if not isinstance(generation_id, str) or not generation_id:
        raise DiffError(f"{scope}: {cl.ACTIVE_POINTER} has no generation_id")
    file_name = f"generation__{generation_id.rsplit('::', 1)[-1]}.json"
    generation = _load_json(tree.read(scope, file_name), f"{scope}/{file_name}")
    try:
        texts = cl.chunk_texts(generation, f"{scope}/{file_name}")
    except cl.LedgerError as exc:
        raise DiffError(str(exc)) from exc

    symbols: dict[str, dict[str, Any]] = {}
    docs: Counter[str] = Counter()
    examples: Counter[str] = Counter()
    titles: dict[str, str] = {}
    symbol_texts: list[str] = []
    for text in texts:
        kind = cl.classify(text)
        first = text.split("\n", 1)[0]
        if kind == "symbol":
            symbol_texts.append(text)
            fqn = first[len(cl.SYMBOL_PREFIX) :].strip()
            entry = symbols.setdefault(fqn, {"bullets": [], "kind": cl.symbol_kind(text), "texts": []})
            entry["texts"].append(_normalised(text))
            entry["bullets"].extend(_bullets(text))
        elif kind in ("doc", "example"):
            prefix = cl.DOC_PREFIX if kind == "doc" else cl.EXAMPLE_PREFIX
            body = _normalised(text)
            (docs if kind == "doc" else examples)[body] += 1
            titles[body] = first[len(prefix) :].strip()
    for entry in symbols.values():
        entry["norm"] = "\n\x00\n".join(sorted(entry["texts"]))
        entry["members"] = (
            0
            if entry["kind"] in cl.FUNCTION_KINDS or entry["kind"] in cl.CONSTANT_KINDS
            else len(entry["bullets"])
        )
    return {
        "docs": docs,
        "examples": examples,
        "generation_id": generation_id,
        "source_commit": cl.source_commit_of(symbol_texts),
        "symbols": symbols,
        "titles": titles,
    }


# --------------------------------------------------------------------------- comparison


def _titles(counter: Counter[str], titles: dict[str, str]) -> list[str]:
    return sorted(titles[body] for body, n in counter.items() for _ in range(n))


def _chunk_delta(old: Counter[str], new: Counter[str], titles: dict[str, str], limit: int) -> dict[str, Any]:
    added, removed = new - old, old - new
    added_titles, removed_titles = _titles(added, titles), _titles(removed, titles)
    return {
        "added": added_titles[:limit],
        "added_count": len(added_titles),
        "delta": sum(new.values()) - sum(old.values()),
        "new_total": sum(new.values()),
        "old_total": sum(old.values()),
        "removed": removed_titles[:limit],
        "removed_count": len(removed_titles),
    }


def _empty_snapshot() -> dict[str, Any]:
    return {
        "docs": Counter(),
        "examples": Counter(),
        "generation_id": None,
        "source_commit": None,
        "symbols": {},
        "titles": {},
    }


def diff_pilot(
    old: dict[str, Any] | None, new: dict[str, Any] | None, examples: int, list_limit: int
) -> dict[str, Any]:
    status_hint = "added" if old is None else "removed" if new is None else None
    o, n = old or _empty_snapshot(), new or _empty_snapshot()
    titles = {**o["titles"], **n["titles"]}
    osym, nsym = o["symbols"], n["symbols"]
    added = sorted(set(nsym) - set(osym))
    removed = sorted(set(osym) - set(nsym))
    common = sorted(set(osym) & set(nsym))
    changed = [f for f in common if osym[f]["norm"] != nsym[f]["norm"]]

    sig_removed = sig_added = 0
    sig_examples: list[dict[str, Any]] = []
    for fqn in common:
        gone = sorted((Counter(osym[fqn]["bullets"]) - Counter(nsym[fqn]["bullets"])).elements())
        came = sorted((Counter(nsym[fqn]["bullets"]) - Counter(osym[fqn]["bullets"])).elements())
        sig_removed += len(gone)
        sig_added += len(came)
        if (gone or came) and len(sig_examples) < examples:
            sig_examples.append({"added": came, "fqn": fqn, "removed": gone})

    member_changes = [
        {
            "delta": nsym[f]["members"] - osym[f]["members"],
            "fqn": f,
            "new": nsym[f]["members"],
            "old": osym[f]["members"],
        }
        for f in common
        if osym[f]["members"] != nsym[f]["members"]
    ]
    members_old = sum(s["members"] for s in osym.values())
    members_new = sum(s["members"] for s in nsym.values())

    documentation = _chunk_delta(o["docs"], n["docs"], titles, list_limit)
    example_delta = _chunk_delta(o["examples"], n["examples"], titles, list_limit)
    generation = {
        "changed": o["generation_id"] != n["generation_id"],
        "new": n["generation_id"],
        "old": o["generation_id"],
    }
    commit = {
        "changed": o["source_commit"] != n["source_commit"],
        "new": n["source_commit"],
        "old": o["source_commit"],
    }
    anything = (
        generation["changed"]
        or commit["changed"]
        or added
        or removed
        or changed
        or sig_removed
        or sig_added
        or member_changes
        or members_old != members_new
        or documentation["added_count"]
        or documentation["removed_count"]
        or example_delta["added_count"]
        or example_delta["removed_count"]
    )
    return {
        "documentation": documentation,
        "examples": example_delta,
        "generation": generation,
        "members": {
            "delta": members_new - members_old,
            "new": members_new,
            "old": members_old,
            "symbols_with_member_change": member_changes[:list_limit],
            "symbols_with_member_change_count": len(member_changes),
        },
        "signature_lines": {
            "added": sig_added,
            "changed": sig_added + sig_removed,
            "examples": sig_examples,
            "removed": sig_removed,
        },
        "source_commit": commit,
        "status": status_hint or ("changed" if anything else "unchanged"),
        "symbols": {
            "added": added[:list_limit],
            "added_count": len(added),
            "changed": changed[:list_limit],
            "changed_count": len(changed),
            "new_total": len(nsym),
            "old_total": len(osym),
            "removed": removed[:list_limit],
            "removed_count": len(removed),
        },
    }


def pilot_key(scope: str) -> str:
    parts = scope.split("__")
    if len(parts) != 3:
        raise DiffError(f"{scope}: scope directory is not <family>__<platform>__<source_kind>")
    return f"{parts[0]}/{parts[1]}"


def diff_trees(
    old: DirTree | GitTree, new: DirTree | GitTree, examples: int = 5, list_limit: int = 50
) -> dict[str, Any]:
    old_scopes = {pilot_key(s): s for s in old.scopes()}
    new_scopes = {pilot_key(s): s for s in new.scopes()}
    pilots: dict[str, dict[str, Any]] = {}
    for key in sorted(set(old_scopes) | set(new_scopes)):
        o = snapshot(old, old_scopes[key]) if key in old_scopes else None
        n = snapshot(new, new_scopes[key]) if key in new_scopes else None
        pilots[key] = diff_pilot(o, n, examples, list_limit)
    records = list(pilots.values())
    totals = {
        "documentation_delta": sum(r["documentation"]["delta"] for r in records),
        "examples_delta": sum(r["examples"]["delta"] for r in records),
        "members_delta": sum(r["members"]["delta"] for r in records),
        "pilots": len(records),
        "signature_lines_changed": sum(r["signature_lines"]["changed"] for r in records),
        "status": {
            s: sum(1 for r in records if r["status"] == s)
            for s in ("added", "changed", "removed", "unchanged")
        },
        "symbols_added": sum(r["symbols"]["added_count"] for r in records),
        "symbols_changed": sum(r["symbols"]["changed_count"] for r in records),
        "symbols_removed": sum(r["symbols"]["removed_count"] for r in records),
    }
    return {
        "ceiling_violations": [],
        "changed": any(r["status"] != "unchanged" for r in records),
        "pilots": pilots,
        "schema": "generation_diff/1",
        "totals": totals,
    }


def ceiling_violations(
    report: dict[str, Any], max_removed_symbols: int | None = None, max_signature_changes: int | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, p in report["pilots"].items():
        if max_removed_symbols is not None and p["symbols"]["removed_count"] > max_removed_symbols:
            out.append(
                {
                    "actual": p["symbols"]["removed_count"],
                    "ceiling": max_removed_symbols,
                    "kind": "max_removed_symbols",
                    "pilot": key,
                }
            )
        if max_signature_changes is not None and p["signature_lines"]["changed"] > max_signature_changes:
            out.append(
                {
                    "actual": p["signature_lines"]["changed"],
                    "ceiling": max_signature_changes,
                    "kind": "max_signature_changes",
                    "pilot": key,
                }
            )
    return out


# --------------------------------------------------------------------------- rendering


def render_json(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def render_text(report: dict[str, Any]) -> bytes:
    lines: list[str] = []
    if not report["changed"]:
        lines.append(f"no change across {report['totals']['pilots']} pilot(s)")
    for key, p in report["pilots"].items():
        if p["status"] == "unchanged":
            continue
        s, sig, d, e = p["symbols"], p["signature_lines"], p["documentation"], p["examples"]
        lines.append(f"{key} [{p['status']}]")
        if p["generation"]["changed"]:
            lines.append(f"  generation {p['generation']['old']} -> {p['generation']['new']}")
        if p["source_commit"]["changed"]:
            lines.append(f"  source commit {p['source_commit']['old']} -> {p['source_commit']['new']}")
        lines.append(
            f"  symbols {s['old_total']} -> {s['new_total']}: +{s['added_count']} -{s['removed_count']} ~{s['changed_count']}"
        )
        for label, names in (("added", s["added"]), ("removed", s["removed"]), ("changed", s["changed"])):
            if names:
                lines.append(f"    {label}: {', '.join(names)}")
        lines.append(f"  members {p['members']['old']} -> {p['members']['new']} ({p['members']['delta']:+d})")
        lines.append(f"  signature lines: -{sig['removed']} +{sig['added']}")
        for ex in sig["examples"]:
            lines.append(f"    {ex['fqn']}: -{len(ex['removed'])} +{len(ex['added'])}")
        lines.append(f"  documentation {d['old_total']} -> {d['new_total']} ({d['delta']:+d})")
        lines.append(f"  examples {e['old_total']} -> {e['new_total']} ({e['delta']:+d})")
    for v in report["ceiling_violations"]:
        lines.append(f"CEILING EXCEEDED {v['kind']} {v['pilot']}: {v['actual']} > {v['ceiling']}")
    return ("\n".join(lines) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- CLI


def _repo_root() -> Path:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return REPO_ROOT
    top = done.stdout.decode("utf-8", "replace").strip()
    return Path(top) if done.returncode == 0 and top else REPO_ROOT


def _non_negative(value: str) -> int:
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return n


def _write_stdout(data: bytes) -> None:
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-pilot diff of two demo-manifest trees.")
    parser.add_argument("trees", nargs="*", type=Path, help="OLD_DIR NEW_DIR, or with --git-rev: [NEW_DIR]")
    parser.add_argument("--git-rev", help="read the OLD tree from this git revision")
    parser.add_argument("--new-rev", help="with --git-rev: read the NEW tree from this git revision too")
    parser.add_argument("--max-removed-symbols", type=_non_negative, default=None)
    parser.add_argument("--max-signature-changes", type=_non_negative, default=None)
    parser.add_argument("--examples", type=_non_negative, default=5, help="signature examples per pilot")
    parser.add_argument("--list-limit", type=_non_negative, default=50, help="names listed per category")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)
    try:
        repo = _repo_root()
        if args.git_rev:
            if len(args.trees) > 1 or (args.new_rev and args.trees):
                raise DiffError("--git-rev takes at most one directory and not together with --new-rev")
            old: DirTree | GitTree = GitTree(args.git_rev, repo=repo)
            new: DirTree | GitTree = (
                GitTree(args.new_rev, repo=repo)
                if args.new_rev
                else DirTree(args.trees[0] if args.trees else repo / MANIFEST_PREFIX)
            )
        else:
            if len(args.trees) != 2 or args.new_rev:
                raise DiffError("give OLD_DIR and NEW_DIR, or --git-rev REV")
            old, new = DirTree(args.trees[0]), DirTree(args.trees[1])
        report = diff_trees(old, new, examples=args.examples, list_limit=args.list_limit)
    except DiffError as exc:
        print(f"generation_diff: {exc}", file=sys.stderr)
        return 2
    report["ceiling_violations"] = ceiling_violations(
        report, args.max_removed_symbols, args.max_signature_changes
    )
    _write_stdout(render_json(report) if args.format == "json" else render_text(report))
    return 1 if report["ceiling_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
