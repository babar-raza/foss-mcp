#!/usr/bin/env python3
"""Reproducible claims ledger for the release evidence (TC-568 / REQ-G3-009, rows RD3-164 and RD3-165).

Usage:
    python scripts/release/claims_ledger.py [--manifests DIR] [--fixtures DIR] [--check claims.json]

Every release number is recomputed from committed files only, deterministically and offline (standard
library only, no network, no docker, no clock). With no ``--check`` it prints ONE JSON document (sorted
keys, two-space indent, LF line ends, one trailing newline) with, per pilot (``family/platform``) and in
``totals``:

  * source commit, generation id, source kind;
  * symbol chunks, split by kind (``types``, ``functions``, ``constants``) with ``by_kind`` and the
    ``members`` of the type symbols (bulleted member lines);
  * documentation chunks, with ``by_category`` (the documentation title);
  * the three example figures, which must never be conflated:
      ``served_chunks``    example chunks the active generation serves (first line ``FQN: Example: ``),
      ``cache_entries``    entries in ``tests/fixtures/verified_examples/<pilot>.json``,
      ``verified_entries`` / ``failed_entries`` / ``other_entries``  those entries by their ``verified`` verdict;
  * the fixture ``tests/fixtures/<family>_<platform>/api_surface.json``: extractor_version, symbol_counts,
    parse_failures;
  * the three sidecars (package_registry, product_reference, recent_releases) as one of
    present, empty, fetch_failed, absent (or unreadable when the file is not valid JSON);
  * data bytes (generation, sidecars, total, pointer included in the total).

Chunk classification is by the first line of the lexical-index chunk text: ``FQN: Doc: <title>`` is a
documentation chunk, ``FQN: Example: <title>`` an example chunk, any other ``FQN: <fqn>`` a symbol chunk
(its second line is ``Kind: <kind>``), anything else is counted as ``unclassified_chunks``.

``--check claims.json`` compares claimed numbers with the recomputed ledger and prints the claims that
differ. The claims file is either ``{"claims": [{"claim": "...", "path": "totals.symbols.total", "value": 1}]}``
or a plain ``{"<dotted path>": value}`` mapping; a path is dotted over the ledger (pilot keys contain a
slash, never a dot). A path that does not exist differs (actual null).

Exit codes: 0 success (and, with --check, every claim holds); 1 --check found at least one differing claim;
2 an input is unusable (missing directory, no active pointer, malformed generation or claims file).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
DEFAULT_FIXTURES = REPO_ROOT / "tests" / "fixtures"

EXAMPLE_PREFIX = "FQN: Example: "
DOC_PREFIX = "FQN: Doc: "
SYMBOL_PREFIX = "FQN: "

ACTIVE_POINTER = "active_pointer.json"
FUNCTION_KINDS = frozenset({"function"})
CONSTANT_KINDS = frozenset({"constant"})
SIDECARS = ("package_registry", "product_reference", "recent_releases")
SIDECAR_STATES = ("absent", "empty", "fetch_failed", "present", "unreadable")

_SOURCE_COMMIT = re.compile(r"^Source-Commit: ([0-9a-fA-F]{7,64})\s*$", re.MULTILINE)
_MEMBER_LINE = re.compile(r"^  - ", re.MULTILINE)


class LedgerError(Exception):
    """An input is unusable; the message names it."""


# --------------------------------------------------------------------------- reading


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LedgerError(f"{path}: unreadable ({exc!r})") from exc


def scope_dirs(manifests: Path) -> list[Path]:
    if not manifests.is_dir():
        raise LedgerError(f"{manifests}: not a directory")
    return sorted(p for p in manifests.iterdir() if p.is_dir())


def active_generation_path(scope_dir: Path) -> tuple[str, Path]:
    pointer = scope_dir / ACTIVE_POINTER
    if not pointer.is_file():
        raise LedgerError(f"{scope_dir.name}: {ACTIVE_POINTER} is missing")
    pointer_doc = _read_json(pointer)
    generation_id = pointer_doc.get("generation_id") if isinstance(pointer_doc, dict) else None
    if not isinstance(generation_id, str) or not generation_id:
        raise LedgerError(f"{scope_dir.name}: {ACTIVE_POINTER} has no generation_id")
    path = scope_dir / f"generation__{generation_id.rsplit('::', 1)[-1]}.json"
    if not path.is_file():
        raise LedgerError(
            f"{scope_dir.name}: active pointer names {generation_id!r} but {path.name} is missing"
        )
    return generation_id, path


def chunk_texts(generation: dict[str, Any], where: str) -> list[str]:
    try:
        documents = generation["payload"]["lexical_index"]["documents"]
        return [str(documents[k]["text"]) for k in sorted(documents)]
    except (KeyError, TypeError) as exc:
        raise LedgerError(f"{where}: no payload.lexical_index.documents ({exc!r})") from exc


def classify(text: str) -> str:
    """'doc', 'example', 'symbol' or 'other' from the first line of a chunk."""
    first = text.split("\n", 1)[0]
    if first.startswith(EXAMPLE_PREFIX):
        return "example"
    if first.startswith(DOC_PREFIX):
        return "doc"
    if first.startswith(SYMBOL_PREFIX):
        return "symbol"
    return "other"


def symbol_kind(text: str) -> str:
    lines = text.split("\n", 2)
    if len(lines) > 1 and lines[1].startswith("Kind:"):
        return lines[1].split(":", 1)[1].strip() or "unknown"
    return "unknown"


def doc_category(text: str) -> str:
    return text.split("\n", 1)[0][len(DOC_PREFIX) :].strip()


# --------------------------------------------------------------------------- per pilot


def _symbol_summary(symbol_texts: list[str]) -> tuple[dict[str, Any], int]:
    by_kind: Counter[str] = Counter()
    members = 0
    for text in symbol_texts:
        kind = symbol_kind(text)
        by_kind[kind] += 1
        if kind not in FUNCTION_KINDS and kind not in CONSTANT_KINDS:
            members += len(_MEMBER_LINE.findall(text))
    functions = sum(n for k, n in by_kind.items() if k in FUNCTION_KINDS)
    constants = sum(n for k, n in by_kind.items() if k in CONSTANT_KINDS)
    total = len(symbol_texts)
    summary = {
        "by_kind": dict(sorted(by_kind.items())),
        "constants": constants,
        "functions": functions,
        "total": total,
        "types": total - functions - constants,
    }
    return summary, members


def source_commit_of(symbol_texts: list[str]) -> str | None:
    counts: Counter[str] = Counter()
    for text in symbol_texts:
        found = _SOURCE_COMMIT.findall(text)
        if found:
            counts[found[-1]] += 1
    if not counts:
        return None
    # most common, ties broken by the commit string so the choice is deterministic
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def sidecar_state(path: Path) -> str:
    if not path.is_file():
        return "absent"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable"
    if isinstance(data, dict) and "fetch_failed" in (data.get("state"), data.get("status")):
        return "fetch_failed"
    if data in (None, "", [], {}):
        return "empty"
    return "present"


def _cache_summary(path: Path) -> dict[str, int]:
    out = {"cache_entries": 0, "failed_entries": 0, "other_entries": 0, "verified_entries": 0}
    if not path.is_file():
        return out
    data = _read_json(path)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise LedgerError(f"{path}: no entries list")
    for entry in entries:
        out["cache_entries"] += 1
        verdict = entry.get("verified") if isinstance(entry, dict) else None
        if verdict is True:
            out["verified_entries"] += 1
        elif verdict is False:
            out["failed_entries"] += 1
        else:
            out["other_entries"] += 1
    return out


def _fixture_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"present": False}
    data = _read_json(path)
    if not isinstance(data, dict):
        raise LedgerError(f"{path}: not a JSON object")
    parse_failures = data.get("parse_failures")
    failed = parse_failures.get("failed", 0) if isinstance(parse_failures, dict) else 0
    return {
        "extractor_version": data.get("extractor_version"),
        "parse_failures": parse_failures,
        "parse_failures_failed": failed if isinstance(failed, int) else 0,
        "present": True,
        "source_commit": data.get("source_commit"),
        "symbol_counts": data.get("symbol_counts"),
    }


def pilot_record(scope_dir: Path, manifests: Path, fixtures: Path) -> tuple[str, dict[str, Any], str]:
    parts = scope_dir.name.split("__")
    if len(parts) != 3:
        raise LedgerError(f"{scope_dir.name}: scope directory is not <family>__<platform>__<source_kind>")
    family, platform, source_kind = parts
    generation_id, gen_path = active_generation_path(scope_dir)
    generation = _read_json(gen_path)
    texts = chunk_texts(generation, gen_path.name)

    symbols = [t for t in texts if classify(t) == "symbol"]
    docs = [t for t in texts if classify(t) == "doc"]
    examples = [t for t in texts if classify(t) == "example"]
    unclassified = len(texts) - len(symbols) - len(docs) - len(examples)
    symbol_summary, members = _symbol_summary(symbols)

    stem = f"{family}_{platform}"
    cache = _cache_summary(fixtures / "verified_examples" / f"{stem}.json")
    sidecar_files = {name: manifests / f"{name}_{stem}.json" for name in SIDECARS}
    sidecar_bytes = sum(p.stat().st_size for p in sidecar_files.values() if p.is_file())
    generation_bytes = gen_path.stat().st_size
    pointer_bytes = (scope_dir / ACTIVE_POINTER).stat().st_size
    stats = generation.get("payload", {}).get("stats")
    declared = (
        {k: stats[k] for k in ("docs", "examples", "symbols", "total") if k in stats}
        if isinstance(stats, dict)
        else {}
    )

    record = {
        "data_bytes": {
            "generation": generation_bytes,
            "sidecars": sidecar_bytes,
            "total": generation_bytes + pointer_bytes + sidecar_bytes,
        },
        "declared_stats": declared,
        "documentation": {
            "by_category": dict(sorted(Counter(doc_category(t) for t in docs).items())),
            "total": len(docs),
        },
        "examples": {"served_chunks": len(examples), **cache},
        "family": family,
        "fixture": _fixture_summary(fixtures / stem / "api_surface.json"),
        "generation_id": generation_id,
        "members": members,
        "platform": platform,
        "sidecars": {name: sidecar_state(p) for name, p in sidecar_files.items()},
        "source_commit": source_commit_of(symbols),
        "source_kind": source_kind,
        "symbols": symbol_summary,
        "unclassified_chunks": unclassified,
    }
    return f"{family}/{platform}", record, f"{stem}.json"


# --------------------------------------------------------------------------- the ledger


def _totals(pilots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    records = list(pilots.values())
    symbol_counts: Counter[str] = Counter()
    for r in records:
        counts = r["fixture"].get("symbol_counts")
        if isinstance(counts, dict):
            symbol_counts.update({k: v for k, v in counts.items() if isinstance(v, int)})
    by_kind: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    for r in records:
        by_kind.update(r["symbols"]["by_kind"])
        categories.update(r["documentation"]["by_category"])
    sidecars = {
        name: {state: sum(1 for r in records if r["sidecars"][name] == state) for state in SIDECAR_STATES}
        for name in SIDECARS
    }
    example_keys = ("cache_entries", "failed_entries", "other_entries", "served_chunks", "verified_entries")
    return {
        "data_bytes": {
            k: sum(r["data_bytes"][k] for r in records) for k in ("generation", "sidecars", "total")
        },
        "documentation": {
            "by_category": dict(sorted(categories.items())),
            "total": sum(r["documentation"]["total"] for r in records),
        },
        "examples": {k: sum(r["examples"][k] for r in records) for k in example_keys},
        "fixtures": {
            "extractor_versions": dict(
                sorted(
                    Counter(
                        str(r["fixture"].get("extractor_version")) for r in records if r["fixture"]["present"]
                    ).items()
                )
            ),
            "parse_failures_failed": sum(r["fixture"].get("parse_failures_failed", 0) for r in records),
            "pilots_with_fixture": sum(1 for r in records if r["fixture"]["present"]),
            "symbol_counts": dict(sorted(symbol_counts.items())),
        },
        "members": sum(r["members"] for r in records),
        "pilots": len(records),
        "sidecars": sidecars,
        "symbols": {
            "by_kind": dict(sorted(by_kind.items())),
            "constants": sum(r["symbols"]["constants"] for r in records),
            "functions": sum(r["symbols"]["functions"] for r in records),
            "total": sum(r["symbols"]["total"] for r in records),
            "types": sum(r["symbols"]["types"] for r in records),
        },
        "unclassified_chunks": sum(r["unclassified_chunks"] for r in records),
    }


def build_ledger(manifests: Path, fixtures: Path) -> dict[str, Any]:
    pilots: dict[str, dict[str, Any]] = {}
    used_cache_files: set[str] = set()
    for scope_dir in scope_dirs(manifests):
        key, record, cache_file = pilot_record(scope_dir, manifests, fixtures)
        pilots[key] = record
        used_cache_files.add(cache_file)
    cache_dir = fixtures / "verified_examples"
    unmatched = (
        sorted(p.name for p in cache_dir.glob("*.json") if p.name not in used_cache_files)
        if cache_dir.is_dir()
        else []
    )
    return {
        "pilots": dict(sorted(pilots.items())),
        "schema": "claims_ledger/1",
        "totals": _totals(pilots),
        "unmatched_verified_example_files": unmatched,
    }


def render(ledger: dict[str, Any]) -> bytes:
    return (json.dumps(ledger, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- --check


_MISSING = object()


def lookup(ledger: dict[str, Any], path: str) -> Any:
    node: Any = ledger
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return _MISSING
    return node


def _claim_list(claims: Any) -> list[dict[str, Any]]:
    if isinstance(claims, dict) and isinstance(claims.get("claims"), list):
        items = claims["claims"]
        if not all(isinstance(i, dict) and "path" in i and "value" in i for i in items):
            raise LedgerError("claims file: every claim needs a path and a value")
        return [{"claim": i.get("claim", i["path"]), "path": i["path"], "value": i["value"]} for i in items]
    if isinstance(claims, dict):
        return [{"claim": path, "path": path, "value": value} for path, value in claims.items()]
    raise LedgerError("claims file: expected a JSON object")


def check_claims(ledger: dict[str, Any], claims: Any) -> dict[str, Any]:
    items = _claim_list(claims)
    mismatches = []
    for item in items:
        actual = lookup(ledger, item["path"])
        if actual is _MISSING:
            actual = None
        if actual != item["value"] or isinstance(actual, bool) != isinstance(item["value"], bool):
            mismatches.append(
                {"actual": actual, "claim": item["claim"], "claimed": item["value"], "path": item["path"]}
            )
    return {"checked": len(items), "mismatches": mismatches}


# --------------------------------------------------------------------------- CLI


def _write_stdout(data: bytes) -> None:
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the reproducible release claims ledger.")
    parser.add_argument(
        "--manifests", type=Path, default=DEFAULT_MANIFESTS, help="manifest root (default: %(default)s)"
    )
    parser.add_argument(
        "--fixtures", type=Path, default=DEFAULT_FIXTURES, help="fixtures root (default: %(default)s)"
    )
    parser.add_argument("--check", type=Path, default=None, help="claims file; print the claims that differ")
    args = parser.parse_args(argv)
    try:
        built = build_ledger(args.manifests, args.fixtures)
        if args.check is None:
            _write_stdout(render(built))
            return 0
        report = check_claims(built, _read_json(args.check))
    except LedgerError as exc:
        print(f"claims_ledger: {exc}", file=sys.stderr)
        return 2
    _write_stdout(render(report))
    return 1 if report["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
