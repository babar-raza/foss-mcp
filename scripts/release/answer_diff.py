#!/usr/bin/env python3
"""Seeded, offline before/after comparator of SERVED ANSWERS for the demo manifests (TC-831 / REQ-G3-017).

Usage:
    python scripts/release/answer_diff.py                          # OLD = HEAD's committed tree, NEW = the working tree
    python scripts/release/answer_diff.py --old-rev REV            # OLD = the tree committed at REV
    python scripts/release/answer_diff.py --old-dir DIR --new-dir DIR2
    options: --new-rev REV  --seed N  --queries-per-pilot N  --pilots a__b,c__d  --max-diffs N
             --allow-lost KEY=REASON (repeatable)  --json-out FILE  --markdown-out FILE

``generation_diff.py`` compares the DATA two manifest trees hold. This tool compares what the in-process MCP tool
functions would SERVE from them, because the "no pilot ever gets worse" rule (AGENTS.md) is about answers: a
regeneration can keep every symbol and still stop answering a question a caller used to get a confident answer to.

For every pilot (``family__platform``) it builds ONE deterministic, seeded query set and runs it, unchanged, against
both trees through the real tool functions (``get_symbol``, ``list_members``, ``search_symbols``, ``find_examples``,
``search_docs``, ``lookup``). The query set per pilot (``--queries-per-pilot`` = N):

  * N symbols sampled with ``random.Random`` from the pilot's own symbol FQNs (N from each side, so a symbol that
    only exists on one side is still probed): ``get_symbol(fqn)``, ``list_members(fqn)``, ``search_symbols(fqn)``
    (``search_symbols_exact``) and ``search_symbols(first three characters of the short name)``
    (``search_symbols_prefix``);
  * N of the pilot's own ``Example: <title>`` chunks: ``find_examples`` on the title's first three words;
  * six canonical questions through ``search_docs`` and ``lookup``: install, limitations, formats, quick start,
    authentication/licence, "what is not supported".

Every query is classified by comparing the OLD and the NEW answer:

  identical    same answer, or both a miss;
  changed      both answered, same-or-better match quality, different content (new-better quality is "changed");
  lost         the OLD answered and the NEW is a miss OR has a lower match_quality / confidence;
  new          the OLD was a miss and the NEW answers.

A pilot present on one side only answers every query as a miss on the other side. Run time of the in-process tools is
bounded by ``--pilots`` and ``--queries-per-pilot``. Reads only the two trees (a git revision is read with
``git archive`` into a temporary directory: no worktree, no checkout); no network, no gateway (the embedding-gateway
env vars are unset for the run), no docker, no clock.

Exit codes: 0 no lost answer (or every one is covered by ``--allow-lost``); 1 at least one lost answer that no
``--allow-lost`` covers; 2 an input is unusable (missing directory, bad revision, unknown pilot, malformed
allowance).

``--allow-lost KEY=REASON`` accepts a pilot (``cells__net``) or one exact query id (``pilot::tool::query``) and
requires a non-empty REASON; every accepted loss and its reason is recorded in the report.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import difflib
import enum
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    # foss_mcp is not an installed package; this module must also run as a plain script.
    sys.path.insert(0, str(_SRC))

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV  # noqa: E402
from foss_mcp.indexing.generation_manifest import GenerationManifestStore  # noqa: E402
from foss_mcp.mcp.routing import Scope  # noqa: E402
from foss_mcp.mcp.tools.find_examples import find_examples  # noqa: E402
from foss_mcp.mcp.tools.get_symbol import extract_fqn, get_symbol  # noqa: E402
from foss_mcp.mcp.tools.list_members import list_members  # noqa: E402
from foss_mcp.mcp.tools.lookup import lookup  # noqa: E402
from foss_mcp.mcp.tools.relevance import EXAMPLE, SYMBOL, chunk_kind  # noqa: E402
from foss_mcp.mcp.tools.search_docs import search_docs  # noqa: E402
from foss_mcp.mcp.tools.search_symbols import scope_key, search_symbols  # noqa: E402

MANIFEST_PREFIX = "infra/demo-manifests"
DEFAULT_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SCOPE_SUFFIX = "__self_extracted"
GIT_TIMEOUT_SECONDS = 300

DEFAULT_SEED = 831
DEFAULT_QUERIES_PER_PILOT = 3
DEFAULT_MAX_DIFFS = 5
MAX_DIFF_CHARS = 2000
PREFIX_LENGTH = 3
EXAMPLE_TITLE_WORDS = 3
EXAMPLE_PREFIX = "Example:"

CANONICAL_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("install", "how do I install it"),
    ("limitations", "what are the known limitations"),
    ("formats", "which file formats are supported"),
    ("quick-start", "quick start"),
    ("authentication-licence", "authentication and licensing"),
    ("not-supported", "what is not supported"),
)
TOOL_ORDER: tuple[str, ...] = (
    "get_symbol",
    "list_members",
    "search_symbols_exact",
    "search_symbols_prefix",
    "find_examples",
    "search_docs",
    "lookup",
)
CLASSES: tuple[str, ...] = ("identical", "changed", "lost", "new")

# Identity fields that differ between any two generations for the same content and say nothing about the answer.
_VOLATILE_KEYS = frozenset({"generation_id", "doc_id", "chunk_id", "scope"})
_MISS_CLASS_NAMES = frozenset({"Miss", "NoExampleFound", "NotFound"})
_RANK_BY_QUALITY = {"exact": 2, "confident": 2, "partial": 1, "loose": 1}
_ANSWERED_UNGRADED_RANK = 2


class AnswerDiffError(Exception):
    """An input is unusable; the message names it."""


# --------------------------------------------------------------------------- loading a manifest tree


@contextlib.contextmanager
def no_embedding_gateway_env() -> Iterator[None]:
    """Unset the embedding-gateway endpoint/API-key env vars for the run (names only, values never read into the
    report), then restore whatever was there: the comparator is offline by construction."""
    saved = {name: os.environ.pop(name, None) for name in (_ENDPOINT_ENV, _API_KEY_ENV)}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def _git_archive(rev: str, repo: Path) -> bytes:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", rev, "--", MANIFEST_PREFIX],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnswerDiffError(f"git archive {rev}: {exc!r}") from exc
    if done.returncode != 0:
        raise AnswerDiffError(f"git archive {rev} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def export_git_tree(rev: str, dest: Path, repo: Path = REPO_ROOT) -> Path:
    """Write every ``*__self_extracted`` scope directory committed at *rev* under ``infra/demo-manifests`` into
    *dest* (a worktree-free export through ``git archive``) and return *dest*."""
    dest.mkdir(parents=True, exist_ok=True)
    wrote = 0
    with tarfile.open(fileobj=io.BytesIO(_git_archive(rev, repo)), mode="r:") as archive:
        for member in archive:
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts
            prefix_parts = PurePosixPath(MANIFEST_PREFIX).parts
            relative = parts[len(prefix_parts) :]
            if parts[: len(prefix_parts)] != prefix_parts or len(relative) != 2:
                continue
            scope, name = relative
            if not scope.endswith(SCOPE_SUFFIX) or ".." in relative:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            target = dest / scope / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())
            wrote += 1
    if not wrote:
        raise AnswerDiffError(f"{rev}: no {SCOPE_SUFFIX} scope directories under {MANIFEST_PREFIX}/")
    return dest


def copy_tree(source: Path, dest: Path) -> Path:
    """Copy every ``*__self_extracted`` directory of *source* into *dest*; nothing is ever written back."""
    if not source.is_dir():
        raise AnswerDiffError(f"{source}: not a directory")
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for directory in sorted(source.glob(f"*{SCOPE_SUFFIX}")):
        if directory.is_dir():
            shutil.copytree(directory, dest / directory.name)
            copied += 1
    if not copied:
        raise AnswerDiffError(f"{source}: no {SCOPE_SUFFIX} scope directories")
    return dest


@dataclass
class TreePilot:
    """One pilot of a loaded tree: its scope and the raw lexical-index documents of its active generation."""

    name: str
    scope: Scope
    documents: list[dict[str, Any]] = field(default_factory=list)

    def texts_of_kind(self, kind: str) -> list[str]:
        return [document["text"] for document in self.documents if chunk_kind(document["text"]) == kind]


@dataclass
class LoadedTree:
    """A manifest tree copied under a temporary directory with a store over it."""

    label: str
    store: GenerationManifestStore
    pilots: dict[str, TreePilot]


def load_tree(root: Path, label: str) -> LoadedTree:
    """Load every pilot's ACTIVE generation from *root* (a directory already holding the scope directories)."""
    store = GenerationManifestStore(root)
    pilots: dict[str, TreePilot] = {}
    for directory in sorted(root.glob(f"*{SCOPE_SUFFIX}")):
        family, platform, source_kind = directory.name.split("__")
        scope = Scope(family=family, platform=platform, source_kind=source_kind)
        key = scope_key(scope, "self_extracted")
        generation_id = store.read_active(key)
        if generation_id is None:
            raise AnswerDiffError(f"{label}: {directory.name} has no active pointer")
        try:
            payload = store.read_generation(key, generation_id).payload
            documents = payload["lexical_index"]["documents"]
        except Exception as exc:  # a malformed generation is an unusable input, not a lost answer
            raise AnswerDiffError(f"{label}: {directory.name}: unreadable generation ({exc!r})") from exc
        name = f"{family}__{platform}"
        pilots[name] = TreePilot(name, scope, list(documents.values()))
    if not pilots:
        raise AnswerDiffError(f"{label}: no pilots")
    return LoadedTree(label, store, pilots)


# --------------------------------------------------------------------------- the seeded query set


@dataclass(frozen=True)
class Query:
    tool: str
    argument: str

    def query_id(self, pilot: str) -> str:
        return f"{pilot}::{self.tool}::{self.argument}"


def _sample(items: Sequence[str], seed_text: str, n: int) -> list[str]:
    """Deterministic: same items, seed text and n give the same list. ``random.Random`` seeded with a string is
    stable across processes (it does not use ``hash()``), and the global ``random`` state is never touched."""
    pool = sorted(set(items))
    return random.Random(seed_text).sample(pool, max(0, min(len(pool), n)))


def _short_name(fqn: str) -> str:
    return fqn.strip().replace("::", ".").split(".")[-1]


def _symbol_fqns(pilot: TreePilot | None) -> list[str]:
    if pilot is None:
        return []
    fqns = [extract_fqn(text) for text in pilot.texts_of_kind(SYMBOL)]
    return [fqn for fqn in fqns if fqn]


def _example_titles(pilot: TreePilot | None) -> list[str]:
    if pilot is None:
        return []
    titles: list[str] = []
    for text in pilot.texts_of_kind(EXAMPLE):
        fqn = extract_fqn(text)
        if fqn:
            titles.append(fqn[len(EXAMPLE_PREFIX) :].strip() if fqn.startswith(EXAMPLE_PREFIX) else fqn)
    return titles


def build_queries(name: str, old: TreePilot | None, new: TreePilot | None, seed: int, n: int) -> list[Query]:
    """The deterministic query set of pilot *name*: a pure function of the two pilots' content, *seed* and *n*."""
    queries: list[Query] = []
    sampled: list[str] = []
    for side, pilot in (("old", old), ("new", new)):
        sampled.extend(_sample(_symbol_fqns(pilot), f"{seed}:{name}:symbols:{side}", n))
    for fqn in sorted(set(sampled)):
        queries.append(Query("get_symbol", fqn))
        queries.append(Query("list_members", fqn))
        queries.append(Query("search_symbols_exact", fqn))
        short = _short_name(fqn)
        if len(short) >= PREFIX_LENGTH:
            queries.append(Query("search_symbols_prefix", short[:PREFIX_LENGTH]))
    titles: list[str] = []
    for side, pilot in (("old", old), ("new", new)):
        titles.extend(_sample(_example_titles(pilot), f"{seed}:{name}:examples:{side}", n))
    first_words = {" ".join(title.split()[:EXAMPLE_TITLE_WORDS]) for title in titles}
    for words in sorted(first_words - {""}):
        queries.append(Query("find_examples", words))
    for _label, question in CANONICAL_QUESTIONS:
        queries.append(Query("search_docs", question))
        queries.append(Query("lookup", question))
    return queries


# --------------------------------------------------------------------------- running a query


@dataclass(frozen=True)
class Outcome:
    """One served answer, reduced to what a comparison needs.

    ``rank`` is 0 for a miss, 1 for a partial/loose answer, 2 for an exact/confident (or ungraded) answer."""

    rank: int
    answer: str

    @property
    def answered(self) -> bool:
        return self.rank > 0


MISSING_PILOT = Outcome(0, "pilot not present in this tree")


def _plain(value: Any) -> Any:
    """A JSON-able, identity-free rendering of a tool result: dataclasses become dicts (minus ``_VOLATILE_KEYS``),
    enums their value, tuples/sets lists."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _plain(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if f.name not in _VOLATILE_KEYS
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _is_miss(result: Any) -> bool:
    return type(result).__name__ in _MISS_CLASS_NAMES


def _rank_of(result: Any) -> int:
    if _is_miss(result):
        return 0
    if isinstance(result, list):
        if not result:
            return 0
        result = result[0]
    if isinstance(result, tuple):
        return _ANSWERED_UNGRADED_RANK
    for attribute in ("match_quality", "confidence"):
        quality = getattr(result, attribute, "")
        if quality in _RANK_BY_QUALITY:
            return _RANK_BY_QUALITY[quality]
    return _ANSWERED_UNGRADED_RANK


def _dispatch(tree: LoadedTree, scope: Scope, query: Query) -> Any:
    store = tree.store
    if query.tool == "get_symbol":
        return get_symbol(store, scope, query.argument)
    if query.tool == "list_members":
        return list_members(store, scope, query.argument)
    if query.tool in ("search_symbols_exact", "search_symbols_prefix"):
        return search_symbols(store, scope, query.argument, top_k=10)
    if query.tool == "find_examples":
        return find_examples(store, scope, query.argument, top_k=5)
    if query.tool == "search_docs":
        return search_docs(store, scope, query.argument, top_k=10)
    if query.tool == "lookup":
        return lookup(store, scope, query.argument, top_k=10)
    raise AnswerDiffError(f"unknown tool {query.tool!r}")


def run_query(tree: LoadedTree, pilot: TreePilot | None, query: Query) -> Outcome:
    """Run *query* in-process against *pilot* of *tree*. A raised exception is a miss whose text names it."""
    if pilot is None:
        return MISSING_PILOT
    try:
        result = _dispatch(tree, pilot.scope, query)
    except Exception as exc:
        return Outcome(0, f"error: {type(exc).__name__}: {exc}")
    answer = json.dumps(_plain(result), sort_keys=True, ensure_ascii=False, indent=1)
    return Outcome(_rank_of(result), answer)


def classify(old: Outcome, new: Outcome) -> str:
    """``identical`` | ``changed`` | ``lost`` | ``new`` (see the module docstring)."""
    if old.answered and new.rank < old.rank:
        return "lost"
    if not old.answered:
        if new.answered:
            return "new"
        return "identical"
    return "identical" if old.answer == new.answer and old.rank == new.rank else "changed"


def _bounded_diff(old: Outcome, new: Outcome) -> str:
    def lines(outcome: Outcome) -> list[str]:
        return outcome.answer.replace("\\n", "\n").splitlines()

    text = "\n".join(difflib.unified_diff(lines(old), lines(new), "old", "new", n=1, lineterm=""))
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS] + f"\n... [diff truncated at {MAX_DIFF_CHARS} characters]"
    return text


# --------------------------------------------------------------------------- the diff and the report


def parse_allowances(values: Sequence[str]) -> dict[str, str]:
    """``KEY=REASON`` entries to ``{key: reason}``; a missing key or an empty reason is an unusable input."""
    allowances: dict[str, str] = {}
    for value in values:
        key, separator, reason = value.partition("=")
        if not separator or not key.strip() or not reason.strip():
            raise AnswerDiffError(f"--allow-lost {value!r}: expected KEY=REASON with a non-empty reason")
        allowances[key.strip()] = reason.strip()
    return allowances


def run_answer_diff(
    old: LoadedTree,
    new: LoadedTree,
    *,
    pilots: Sequence[str] | None = None,
    seed: int = DEFAULT_SEED,
    queries_per_pilot: int = DEFAULT_QUERIES_PER_PILOT,
    allow_lost: Mapping[str, str] | None = None,
    max_diffs: int = DEFAULT_MAX_DIFFS,
) -> dict[str, Any]:
    """Run the seeded query set of every selected pilot against *old* and *new* and return the report dict."""
    allowances = dict(allow_lost or {})
    universe = sorted(set(old.pilots) | set(new.pilots))
    selected = sorted(set(pilots)) if pilots else universe
    unknown = [name for name in selected if name not in universe]
    if unknown:
        raise AnswerDiffError(f"unknown pilot(s) {unknown}; known: {universe}")
    used: set[str] = set()
    report_pilots: dict[str, Any] = {}
    totals = dict.fromkeys(CLASSES, 0)
    lost_entries: list[dict[str, Any]] = []
    for name in selected:
        old_pilot, new_pilot = old.pilots.get(name), new.pilots.get(name)
        status = "both" if old_pilot and new_pilot else ("removed" if old_pilot else "added")
        tools: dict[str, Any] = {
            tool: {**dict.fromkeys(CLASSES, 0), "total": 0, "old_answered": 0, "new_answered": 0, "diffs": []}
            for tool in TOOL_ORDER
        }
        for query in build_queries(name, old_pilot, new_pilot, seed, queries_per_pilot):
            before = run_query(old, old_pilot, query)
            after = run_query(new, new_pilot, query)
            verdict = classify(before, after)
            bucket = tools[query.tool]
            bucket[verdict] += 1
            bucket["total"] += 1
            bucket["old_answered"] += before.answered
            bucket["new_answered"] += after.answered
            totals[verdict] += 1
            if verdict == "identical" or verdict == "new":
                continue
            entry: dict[str, Any] = {
                "id": query.query_id(name),
                "query": query.argument,
                "class": verdict,
                "old_rank": before.rank,
                "new_rank": after.rank,
            }
            if verdict == "lost":
                key = next((k for k in (query.query_id(name), name) if k in allowances), None)
                if key is not None:
                    used.add(key)
                    entry["allowed"] = allowances[key]
                lost_entries.append({"pilot": name, "tool": query.tool, **entry})
            if len(bucket["diffs"]) < max_diffs:
                bucket["diffs"].append({**entry, "diff": _bounded_diff(before, after)})
        report_pilots[name] = {"status": status, "tools": tools}
    unallowed = [entry for entry in lost_entries if "allowed" not in entry]
    return {
        "old": old.label,
        "new": new.label,
        "seed": seed,
        "queries_per_pilot": queries_per_pilot,
        "totals": totals,
        "old_answered": sum(t["old_answered"] for p in report_pilots.values() for t in p["tools"].values()),
        "new_answered": sum(t["new_answered"] for p in report_pilots.values() for t in p["tools"].values()),
        "pilots": report_pilots,
        "lost": lost_entries,
        "allowed_lost": [e for e in lost_entries if "allowed" in e],
        "unused_allowances": {k: v for k, v in sorted(allowances.items()) if k not in used},
        "failed": bool(unallowed),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """The human-reviewable form of *report*: a summary table, the lost answers, then every bounded diff."""
    totals = report["totals"]
    out = [
        "# Served-answer diff",
        "",
        f"OLD `{report['old']}` -> NEW `{report['new']}`; seed {report['seed']}, "
        f"{report['queries_per_pilot']} queries per pilot sample.",
        "",
        "Totals: " + ", ".join(f"{name} {totals[name]}" for name in CLASSES) + ".",
        f"Answered (not a miss): OLD {report['old_answered']}, NEW {report['new_answered']}.",
        "Verdict: "
        + (
            "FAIL - lost answers not covered by --allow-lost"
            if report["failed"]
            else "no unallowed lost answer"
        ),
        "",
        "| pilot | tool | identical | changed | lost | new |",
        "|---|---|---|---|---|---|",
    ]
    for name, pilot in report["pilots"].items():
        for tool in TOOL_ORDER:
            row = pilot["tools"][tool]
            if row["total"]:
                out.append(
                    f"| {name} | {tool} | {row['identical']} | {row['changed']} | {row['lost']} | {row['new']} |"
                )
    if report["lost"]:
        out += ["", "## Lost answers", ""]
        for entry in report["lost"]:
            allowed = f" (accepted: {entry['allowed']})" if "allowed" in entry else ""
            out.append(f"- `{entry['id']}` rank {entry['old_rank']} -> {entry['new_rank']}{allowed}")
    if report["unused_allowances"]:
        out += ["", "## Unused --allow-lost entries", ""]
        out += [f"- `{key}`: {reason}" for key, reason in report["unused_allowances"].items()]
    diffs = [
        (name, tool, diff)
        for name, pilot in report["pilots"].items()
        for tool in TOOL_ORDER
        for diff in pilot["tools"][tool]["diffs"]
    ]
    if diffs:
        out += ["", "## Changed and lost answers (bounded)", ""]
        for name, tool, diff in diffs:
            out += [
                f"### {name} / {tool} / {diff['class']}: `{diff['query']}`",
                "",
                "```diff",
                diff["diff"],
                "```",
                "",
            ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- command line


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], prog="answer_diff.py")
    old = parser.add_mutually_exclusive_group()
    old.add_argument(
        "--old-rev", help="OLD = the manifest tree committed at this git revision (default HEAD)"
    )
    old.add_argument(
        "--old-dir", type=Path, help="OLD = this directory of *__self_extracted scope directories"
    )
    new = parser.add_mutually_exclusive_group()
    new.add_argument("--new-rev", help="NEW = the manifest tree committed at this git revision")
    new.add_argument("--new-dir", type=Path, help="NEW = this directory (default infra/demo-manifests)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--queries-per-pilot",
        type=int,
        default=DEFAULT_QUERIES_PER_PILOT,
        help="N symbols and N example titles sampled per pilot (default %(default)s)",
    )
    parser.add_argument("--pilots", help="comma-separated pilot names (family__platform); default all")
    parser.add_argument(
        "--max-diffs", type=int, default=DEFAULT_MAX_DIFFS, help="bounded diffs kept per pilot and tool"
    )
    parser.add_argument("--allow-lost", action="append", default=[], metavar="KEY=REASON")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    return parser


def _load_side(
    rev: str | None, directory: Path | None, default_dir: Path | None, work: Path, label: str
) -> LoadedTree:
    if rev is not None:
        source = export_git_tree(rev, work / f"{label}-export")
        return load_tree(copy_tree(source, work / label), f"git:{rev}")
    chosen = directory if directory is not None else default_dir
    assert chosen is not None
    return load_tree(copy_tree(chosen, work / label), str(chosen))


def main(argv: Sequence[str] | None = None, *, repo_manifests: Path = DEFAULT_MANIFESTS) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        allowances = parse_allowances(args.allow_lost)
        pilots = [p.strip() for p in args.pilots.split(",") if p.strip()] if args.pilots else None
        with tempfile.TemporaryDirectory(prefix="answer-diff-") as scratch, no_embedding_gateway_env():
            work = Path(scratch)
            old_rev = args.old_rev if args.old_rev or args.old_dir else "HEAD"
            old = _load_side(old_rev, args.old_dir, None, work, "old")
            new = _load_side(args.new_rev, args.new_dir, repo_manifests, work, "new")
            report = run_answer_diff(
                old,
                new,
                pilots=pilots,
                seed=args.seed,
                queries_per_pilot=args.queries_per_pilot,
                allow_lost=allowances,
                max_diffs=args.max_diffs,
            )
    except AnswerDiffError as exc:
        print(f"answer_diff: {exc}", file=sys.stderr)
        return 2
    markdown = render_markdown(report)
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.markdown_out:
        args.markdown_out.write_text(markdown, encoding="utf-8")
    sys.stdout.buffer.write(markdown.encode("utf-8", "replace"))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
