"""report_index_freshness: the indexed generation's source revision against the current one.

The published generation's lexical-index payload (``foss_mcp.indexing.lexical_index_writer``,
out of this card's write_paths) persists each chunk's ``text`` but not the ``Provenance``
(repository/commit) the chunk was built from, so the source commit a generation was indexed
from has to be recoverable from the text itself - this project's own convention (a
``Source-Commit:`` line), the same pattern ``get_symbol``'s ``FQN:`` line already established
for the same underlying constraint. A generation whose chunks carry no such line is honestly
reported as unknown, never assumed fresh.

A generation is often built from SEVERAL source commits (API symbols extracted at one commit,
documentation and verified examples at the library's pinned commit). The report therefore lists
every distinct commit with the number of chunks of each kind that came from it, and a caller's
commit is compared against all of them. Nothing is invented: whatever the chunks do not record
(the commit, the API coverage) is reported as unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import scope_key

_SOURCE_COMMIT_LINE = re.compile(r"^Source-Commit:\s*(\S+)$", re.MULTILINE)
_API_COVERAGE_LINE = re.compile(r"^API-Coverage:\s*(\d+)\s+of\s+(\d+)\s+types\b", re.MULTILINE)

_EXAMPLE_PREFIX = "Example: "
_DOCUMENTATION_PREFIX = "Doc: "
_MIN_PREFIX_LENGTH = 7
_SHORT_COMMIT_LENGTH = 12

_SYMBOL = "symbol"
_EXAMPLE = "example"
_DOCUMENTATION = "documentation"
_UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class CommitCount:
    """How many chunks of the active generation were built from one source commit."""

    commit: str
    chunks: int
    symbol_chunks: int = 0
    example_chunks: int = 0
    documentation_chunks: int = 0


@dataclass(frozen=True)
class FreshnessReport:
    scope: Scope
    source_kind: str
    indexed_generation_id: str | None
    indexed_source_commit: str | None
    current_source_commit: str | None
    stale: bool
    reason: str
    source_commits: tuple[CommitCount, ...] = ()
    chunks_without_commit: int = 0
    pinned_library_commit: str | None = None
    pinned_matches_symbols: bool | None = None
    indexed_types: int | None = None
    total_types: int | None = None
    coverage_note: str | None = None


def _commit_matches(indexed_commit: str, current_commit: str) -> bool:
    """True when *indexed_commit* equals *current_commit* (both stripped and lowercased), or
    *current_commit* is an abbreviation of at least 7 characters that prefixes it."""
    indexed = indexed_commit.strip().lower()
    current = current_commit.strip().lower()
    if not current:
        return False
    if indexed == current:
        return True
    return len(current) >= _MIN_PREFIX_LENGTH and indexed.startswith(current)


def _chunk_kind(text: str) -> str:
    """symbol / example / documentation by the chunk's ``FQN:`` line; a chunk with no FQN line
    cannot be told apart, so it is counted as ``unclassified`` rather than guessed."""
    fqn = extract_fqn(text)
    if fqn is None:
        return _UNCLASSIFIED
    if fqn.startswith(_EXAMPLE_PREFIX):
        return _EXAMPLE
    if fqn.startswith(_DOCUMENTATION_PREFIX):
        return _DOCUMENTATION
    return _SYMBOL


@dataclass(frozen=True)
class _Provenance:
    source_commits: tuple[CommitCount, ...]
    chunks_without_commit: int
    indexed_types: int | None
    total_types: int | None


def _read_provenance(manifest_payload: dict) -> _Provenance:
    documents = (manifest_payload.get("lexical_index") or {}).get("documents") or {}
    per_commit: dict[str, dict[str, int]] = {}
    without_commit = 0
    indexed_types: int | None = None
    total_types: int | None = None
    # Sorted by document id so "the first API-Coverage line" does not depend on dict order.
    for _, document in sorted(documents.items()):
        text = document["text"]
        kind = _chunk_kind(text)
        if kind == _SYMBOL and indexed_types is None:
            coverage = _API_COVERAGE_LINE.search(text)
            if coverage is not None:
                indexed_types, total_types = int(coverage.group(1)), int(coverage.group(2))
        commit_match = _SOURCE_COMMIT_LINE.search(text)
        if commit_match is None:
            without_commit += 1
            continue
        counts = per_commit.setdefault(commit_match.group(1), {})
        counts["chunks"] = counts.get("chunks", 0) + 1
        counts[kind] = counts.get(kind, 0) + 1
    commits = [
        CommitCount(
            commit=commit,
            chunks=counts["chunks"],
            symbol_chunks=counts.get(_SYMBOL, 0),
            example_chunks=counts.get(_EXAMPLE, 0),
            documentation_chunks=counts.get(_DOCUMENTATION, 0),
        )
        for commit, counts in per_commit.items()
    ]
    commits.sort(key=lambda entry: (-entry.chunks, entry.commit))
    return _Provenance(tuple(commits), without_commit, indexed_types, total_types)


def _indexed_source_commit(commits: tuple[CommitCount, ...]) -> str | None:
    """The commit with the most API-symbol chunks (ties: the smaller commit string); when no
    chunk is a symbol chunk, the commit with the most chunks (same tie-break)."""
    if not commits:
        return None
    if any(entry.symbol_chunks for entry in commits):
        return min(commits, key=lambda entry: (-entry.symbol_chunks, entry.commit)).commit
    return min(commits, key=lambda entry: (-entry.chunks, entry.commit)).commit


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _unclassified(entry: CommitCount) -> int:
    return entry.chunks - entry.symbol_chunks - entry.documentation_chunks - entry.example_chunks


def _kinds_phrase(entries: list[CommitCount]) -> str:
    names = [
        name
        for name, count in (
            ("API symbols", sum(entry.symbol_chunks for entry in entries)),
            ("documentation", sum(entry.documentation_chunks for entry in entries)),
            ("examples", sum(entry.example_chunks for entry in entries)),
            ("chunks of unknown kind", sum(_unclassified(entry) for entry in entries)),
        )
        if count
    ]
    return _join(names)


def _chunk_counts(entry: CommitCount) -> str:
    parts = [
        f"{count} {label}"
        for label, count in (
            ("API symbols", entry.symbol_chunks),
            ("documentation", entry.documentation_chunks),
            ("examples", entry.example_chunks),
            ("of unknown kind", _unclassified(entry)),
        )
        if count
    ]
    if len(parts) > 1:
        return f"({entry.chunks} chunks: {', '.join(parts)})"
    return f"({entry.chunks} chunks)"


def _mixed_reason(commits: tuple[CommitCount, ...], current: str | None) -> str:
    segments = [
        f"{_kinds_phrase([entry])} from {entry.commit[:_SHORT_COMMIT_LENGTH]} {_chunk_counts(entry)}"
        for entry in commits
    ]
    reason = "index is mixed: " + "; ".join(segments)
    if current is None:
        return reason + "; current source commit is unknown; staleness cannot be ruled out"
    matching = [entry for entry in commits if _commit_matches(entry.commit, current)]
    shown = current.strip()[:_SHORT_COMMIT_LENGTH]
    if not matching:
        return reason + f"; current {shown} matches none of them"
    if len(matching) == len(commits):
        return reason + f"; current {shown} matches all of them"
    return reason + f"; current {shown} matches {_kinds_phrase(matching)} only"


def _pinned_matches_symbols(commits: tuple[CommitCount, ...], pinned: str | None) -> bool | None:
    symbol_commits = [entry for entry in commits if entry.symbol_chunks]
    if pinned is None or not symbol_commits:
        return None
    return all(_commit_matches(entry.commit, pinned) for entry in symbol_commits)


def report_index_freshness(
    store: GenerationManifestStore,
    scope: Scope,
    source_kind: str,
    current_source_commit: str | None,
    *,
    pinned_library_commit: str | None = None,
) -> FreshnessReport:
    """Compare every commit ``scope``'s active generation (for *source_kind*) was indexed from
    against *current_source_commit*. Never publishes a generation, never guesses freshness
    when the information needed to know is simply absent.

    When *pinned_library_commit* is given, the report also says whether the API symbols were
    extracted at it. When the chunks state how much of the API surface they hold, the report
    carries that share; otherwise it stays unknown.
    """
    key = scope_key(scope, source_kind)
    generation_id = store.read_active(key)
    if generation_id is None:
        return FreshnessReport(
            scope,
            source_kind,
            None,
            None,
            current_source_commit,
            True,
            "never published",
            pinned_library_commit=pinned_library_commit,
        )

    manifest = store.read_generation(key, generation_id)
    provenance = _read_provenance(manifest.payload)
    commits = provenance.source_commits
    indexed_commit = _indexed_source_commit(commits)
    pinned_matches = _pinned_matches_symbols(commits, pinned_library_commit)

    coverage_note: str | None = None
    if (
        provenance.indexed_types is not None
        and provenance.total_types is not None
        and provenance.indexed_types < provenance.total_types
    ):
        coverage_note = (
            f"the API index holds {provenance.indexed_types} of {provenance.total_types} types; "
            "a type missing from it may still exist upstream"
        )

    def build(stale: bool, reason: str) -> FreshnessReport:
        if pinned_matches is False and pinned_library_commit is not None:
            reason = (
                f"{reason}. API symbols were not extracted at the pinned library commit "
                f"{pinned_library_commit.strip()[:_SHORT_COMMIT_LENGTH]} (they come from "
                f"{(indexed_commit or '')[:_SHORT_COMMIT_LENGTH]})"
            )
        return FreshnessReport(
            scope,
            source_kind,
            generation_id,
            indexed_commit,
            current_source_commit,
            stale,
            reason,
            source_commits=commits,
            chunks_without_commit=provenance.chunks_without_commit,
            pinned_library_commit=pinned_library_commit,
            pinned_matches_symbols=pinned_matches,
            indexed_types=provenance.indexed_types,
            total_types=provenance.total_types,
            coverage_note=coverage_note,
        )

    if indexed_commit is None:
        return build(True, "indexed generation does not record its source commit")
    if len(commits) > 1:
        # Fresh only when EVERY distinct indexed commit matches the caller's commit.
        all_match = current_source_commit is not None and all(
            _commit_matches(entry.commit, current_source_commit) for entry in commits
        )
        if all_match:
            return build(False, "index matches the current source commit")
        return build(True, _mixed_reason(commits, current_source_commit))
    if current_source_commit is None:
        return build(True, "current source commit is unknown; staleness cannot be ruled out")
    if not _commit_matches(indexed_commit, current_source_commit):
        return build(True, f"indexed {indexed_commit}, current source is {current_source_commit}")
    return build(False, "index matches the current source commit")
