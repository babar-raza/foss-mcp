#!/usr/bin/env python3
"""G3/TC-599 (REQ-G3-016): in-process tool-call latency probe over the real baked demo manifests.

Usage:
    python scripts/release/latency_probe.py --tool lookup --query "merge cells together"
    python scripts/release/latency_probe.py --tool get_symbol --pilots pdf__java,imaging__net --n 100
    python scripts/release/latency_probe.py --report /tmp/latency.json

The round-2 portfolio audit observed p50 ~70-90ms but p95 up to 15.1s and max up to 42s through the
public cloudflared quick tunnel (3d/net, cells/net, cells/python). A prior in-process measurement
(recorded in the mission plan, not reproduced here) found the app's own tool-call compute is
0.5-83ms even on the largest pilot's index. This script is the reusable, offline harness that makes
that in-process layer measurable and re-checkable on demand: for a chosen MCP tool and a chosen set
of pilots, it runs N identical in-process calls against a GenerationManifestStore built from a TEMP
COPY of ``infra/demo-manifests`` (the real committed manifests are only ever read, never written to)
and reports p50/p95/p99/max latency in milliseconds per pilot.

It is deliberately ONLY this layer: no network call, no subprocess, no server, no container. RT2/RT4
build the container-direct, nginx and tunnel layers on top of this one once the hosting config this
card's dependents will version in actually exists.

Exit codes: 0 success; 2 an input is unusable (unknown pilot, unresolvable symbol for get_symbol).
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import find_examples
from foss_mcp.mcp.tools.get_symbol import extract_fqn, get_symbol
from foss_mcp.mcp.tools.lookup import lookup
from foss_mcp.mcp.tools.relevance import SYMBOL, chunk_kind
from foss_mcp.mcp.tools.search_docs import search_docs
from foss_mcp.mcp.tools.search_symbols import scope_key, search_symbols

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SOURCE_KIND = "self_extracted"
DEFAULT_QUERY = "how do I use this library"
DEFAULT_N = 500

# Every tool this card asks for, each callable the same way: (store, scope, text, ...). ``get_symbol``
# matches *text* by exact FQN equality rather than free-text search, so ``_resolve_text`` below
# substitutes a real resolved FQN for it instead of the caller's free-text --query.
TOOLS: dict[str, Callable[..., Any]] = {
    "lookup": lookup,
    "get_symbol": get_symbol,
    "find_examples": find_examples,
    "search_symbols": search_symbols,
    "search_docs": search_docs,
}


def discover_pilots(root: Path) -> dict[str, Scope]:
    """Every ``*__self_extracted`` pilot directory under *root*: same discovery
    ``tests/mcp/tools/test_round3_retrieval_corpus.py``'s own ``Corpus`` uses - a pilot's name is
    ``family__platform``, and its scope's ``source_kind`` is always ``self_extracted``."""
    pilots: dict[str, Scope] = {}
    for directory in sorted(root.glob("*__self_extracted")):
        family, platform, source_kind = directory.name.split("__")
        pilots[f"{family}__{platform}"] = Scope(family=family, platform=platform, source_kind=source_kind)
    return pilots


def copy_demo_manifests(source: Path, dest: Path) -> None:
    """Copy every ``*__self_extracted`` pilot directory from *source* into *dest*.

    *source* (the real, committed ``infra/demo-manifests``) is only ever read here - never written
    to - exactly like ``tests/mcp/tools/test_round3_retrieval_corpus.py``'s ``corpus`` fixture copies
    it into a pytest ``tmp_path`` before building a store over the copy.
    """
    for directory in source.glob("*__self_extracted"):
        if directory.is_dir():
            shutil.copytree(directory, dest / directory.name)


def first_resolvable_fqn(store: GenerationManifestStore, scope: Scope) -> str | None:
    """A real, exact FQN from *scope*'s active generation that ``get_symbol`` can match.

    Only a SYMBOL-kind chunk's own ``FQN:`` line is considered - never an ``Example:``/``Doc:``
    pseudo-symbol, which ``get_symbol`` never matches (see ``get_symbol.py``'s own docstring).
    """
    key = scope_key(scope, SOURCE_KIND)
    generation_id = store.read_active(key)
    if generation_id is None:
        return None
    documents = store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
    for document in documents.values():
        text = document["text"]
        if chunk_kind(text) != SYMBOL:
            continue
        fqn = extract_fqn(text)
        if fqn:
            return fqn
    return None


def _resolve_text(tool_name: str, store: GenerationManifestStore, scope: Scope, query: str) -> str:
    """The third positional argument the chosen tool is actually called with: a real FQN for
    ``get_symbol``, the caller-supplied free-text *query* for every other tool."""
    if tool_name != "get_symbol":
        return query
    fqn = first_resolvable_fqn(store, scope)
    if fqn is None:
        raise LookupError(f"no symbol with an FQN found in pilot scope {scope!r} to probe get_symbol with")
    return fqn


def measure_calls(call: Callable[[], Any], n: int) -> dict[str, float]:
    """Run *call* *n* times with ``time.perf_counter()`` around each call; return p50/p95/p99/max
    in milliseconds.

    Percentiles use the standard nearest-rank method (``ceil(p * n) - 1``, clamped into range) -
    simple, needs no interpolation, and is exact enough for this card's generous bound and its
    deliberately small ``n`` (10-20 in the offline test; the CLI's own default stays 500).
    """
    samples_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        call()
        samples_ms.append((time.perf_counter() - start) * 1000.0)
    samples_ms.sort()

    def percentile(p: float) -> float:
        index = max(0, min(len(samples_ms) - 1, math.ceil(p * len(samples_ms)) - 1))
        return samples_ms[index]

    return {
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": samples_ms[-1],
    }


def run_probe(
    manifests_root: Path,
    pilots: list[str] | None,
    tool_name: str,
    query: str,
    n: int,
) -> dict[str, dict[str, float]]:
    """Measure *tool_name* in-process, *n* times per pilot, over a temp copy of *manifests_root*.

    *pilots* is a list of ``family__platform`` names (default: every pilot baked into
    *manifests_root*). Returns ``{pilot_name: {"p50": ..., "p95": ..., "p99": ..., "max": ...}}``.
    """
    with tempfile.TemporaryDirectory(prefix="foss-mcp-latency-probe-") as tmp:
        tmp_root = Path(tmp)
        copy_demo_manifests(manifests_root, tmp_root)
        store = GenerationManifestStore(tmp_root)
        available = discover_pilots(tmp_root)
        selected = pilots if pilots is not None else sorted(available)
        unknown = [name for name in selected if name not in available]
        if unknown:
            raise LookupError(f"unknown pilot(s) {unknown}; available: {sorted(available)}")

        tool = TOOLS[tool_name]
        report: dict[str, dict[str, float]] = {}
        for pilot_name in selected:
            scope = available[pilot_name]
            text = _resolve_text(tool_name, store, scope, query)
            call = functools.partial(tool, store, scope, text)
            report[pilot_name] = measure_calls(call, n)
        return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure in-process MCP tool-call latency against the real baked demo manifests. "
            "Offline: no network call, no subprocess, no server."
        )
    )
    parser.add_argument(
        "--pilots",
        default=None,
        help="Comma-separated pilot names (family__platform), e.g. pdf__java,imaging__net. "
        "Default: every pilot baked into --manifests-root.",
    )
    parser.add_argument(
        "--tool",
        choices=sorted(TOOLS),
        default="lookup",
        help="MCP tool to call in-process (default: lookup).",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="Free-text query used for every tool except get_symbol (get_symbol always resolves "
        "a real FQN from each pilot's own index, since it matches by exact FQN, not free text).",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=DEFAULT_N,
        help=f"Identical calls per pilot (default: {DEFAULT_N}).",
    )
    parser.add_argument(
        "--manifests-root",
        type=Path,
        default=DEMO_MANIFESTS,
        help="Source of the real baked demo manifests to copy from (default: infra/demo-manifests; "
        "never written to).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the JSON report to this path; omit to print it to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    pilots = [name.strip() for name in args.pilots.split(",")] if args.pilots else None
    try:
        report = run_probe(args.manifests_root, pilots, args.tool, args.query, args.n)
    except LookupError as exc:
        print(f"latency_probe: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report is not None:
        args.report.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
