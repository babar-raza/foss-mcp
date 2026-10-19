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
from collections.abc import Sequence
from dataclasses import dataclass

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.coverage_note import coverage_note_from_text, parse_api_coverage
from foss_mcp.mcp.tools.example_search_mode import example_search_mode
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.release_alignment import (
    DRIFT_DIVERGED,
    DRIFT_IN_SYNC,
    DRIFT_UNKNOWN,
    INDEX_AHEAD,
    PACKAGE_AHEAD,
    align_releases,
    drift_from_record,
    generation_indexed_at,
    package_version,
)
from foss_mcp.mcp.tools.search_symbols import scope_key

_SOURCE_COMMIT_LINE = re.compile(r"^Source-Commit:\s*(\S+)$", re.MULTILINE)

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
    example_search: str = "lexical-only"
    example_search_reason: str = "no active generation"
    # G3/TC-519. ``stale`` stays True for an unknown verdict (it is ``freshness_state != "fresh"``);
    # ``freshness_state`` (fresh / stale / unknown) carries the distinction and ``freshness_basis``
    # (caller_commit, git_ancestry, latest_release_tag_commit, latest_release_version, none) says how it was decided.
    freshness_state: str = "unknown"
    freshness_basis: str = "none"
    indexed_at: str | None = None
    source_repository: str | None = None
    latest_release_tag: str | None = None
    latest_release_in_index: bool | None = None
    warnings: tuple[str, ...] = ()
    compared_against_commit: str | None = None
    # G3/TC-560: git-ancestry drift between the indexed commit and the latest release/tag commit, decided
    # by ``release_alignment.drift_verdict``. ``drift`` is index_ahead_of_package / package_ahead_of_index /
    # diverged / in_sync / unknown; ``unknown`` (the value for every sidecar written before the card) changes
    # nothing else in the report. ``drift_commit_distance`` is how far the leading side is ahead and
    # ``published_version`` the version of the latest release/tag it was measured against.
    drift: str = DRIFT_UNKNOWN
    index_ahead_of_package: bool | None = None
    package_ahead_of_index: bool | None = None
    drift_commit_distance: int | None = None
    published_version: str | None = None
    drift_message: str | None = None


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
    coverage_text: str | None = None


def _read_provenance(manifest_payload: dict) -> _Provenance:
    documents = (manifest_payload.get("lexical_index") or {}).get("documents") or {}
    per_commit: dict[str, dict[str, int]] = {}
    without_commit = 0
    indexed_types: int | None = None
    total_types: int | None = None
    coverage_text: str | None = None
    # Sorted by document id so "the first API-Coverage line" does not depend on dict order.
    for _, document in sorted(documents.items()):
        text = document["text"]
        kind = _chunk_kind(text)
        if kind == _SYMBOL and indexed_types is None:
            coverage = parse_api_coverage(text)
            if coverage is not None:
                indexed_types, total_types = coverage.indexed, coverage.total
                coverage_text = text
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
    return _Provenance(tuple(commits), without_commit, indexed_types, total_types, coverage_text)


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


_ABBREVIATED_COMMIT_LENGTH = 40


def read_indexed_commits(
    store: GenerationManifestStore, scope: Scope, source_kind: str = "self_extracted"
) -> tuple[str, ...]:
    """The distinct source commits of *scope*'s active generation, in the report's order (most chunks
    first). Empty when nothing is published or no chunk records a commit."""
    key = scope_key(scope, source_kind)
    generation_id = store.read_active(key)
    if generation_id is None:
        return ()
    manifest = store.read_generation(key, generation_id)
    return tuple(entry.commit for entry in _read_provenance(manifest.payload).source_commits)


def _commit_verdict(commits: tuple[CommitCount, ...], indexed_commit: str, current: str) -> tuple[str, str]:
    """``(state, reason)`` of comparing every indexed commit with *current*: fresh only when all match."""
    if len(commits) > 1:
        # Fresh only when EVERY distinct indexed commit matches the given commit.
        if all(_commit_matches(entry.commit, current) for entry in commits):
            return "fresh", "index matches the current source commit"
        return "stale", _mixed_reason(commits, current)
    if not _commit_matches(indexed_commit, current):
        return "stale", f"indexed {indexed_commit}, current source is {current}"
    return "fresh", "index matches the current source commit"


def _abbreviation_warnings(current_source_commit: str | None) -> tuple[str, ...]:
    if current_source_commit is None:
        return ()
    length = len(current_source_commit.strip())
    if 0 < length < _ABBREVIATED_COMMIT_LENGTH:
        return (
            f"current_source_commit is abbreviated ({length} characters): it is matched as a prefix of "
            "the indexed commit; pass the full commit for an exact comparison.",
        )
    return ()


def report_index_freshness(
    store: GenerationManifestStore,
    scope: Scope,
    source_kind: str,
    current_source_commit: str | None,
    *,
    pinned_library_commit: str | None = None,
    product_reference: ProductReferenceInputs | None = None,
    releases: Sequence[Release] = (),
) -> FreshnessReport:
    """Compare every commit ``scope``'s active generation (for *source_kind*) was indexed from
    against *current_source_commit*. Never publishes a generation, never guesses freshness
    when the information needed to know is simply absent.

    When *pinned_library_commit* is given, the report also says whether the API symbols were
    extracted at it. When the chunks state how much of the API surface they hold, the report
    carries that share; otherwise it stays unknown.

    G3/TC-519: the verdict is tri-state (``freshness_state`` fresh / stale / unknown; ``stale`` is
    ``freshness_state != "fresh"``) with its ``freshness_basis``. When *current_source_commit* is
    omitted, the newest of *releases* is the reference: its tag commit when it has one, else its
    version against the version the package manifest (*product_reference*) declares; with neither
    the verdict stays unknown and the legacy reason is kept.
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
            freshness_state="unknown",
        )

    manifest = store.read_generation(key, generation_id)
    provenance = _read_provenance(manifest.payload)
    commits = provenance.source_commits
    indexed_commit = _indexed_source_commit(commits)
    pinned_matches = _pinned_matches_symbols(commits, pinned_library_commit)

    example_mode, example_reason = example_search_mode(manifest.payload)
    coverage_note = coverage_note_from_text(provenance.coverage_text) if provenance.coverage_text else None

    indexed_version = package_version(product_reference)
    alignment = align_releases(
        releases, indexed_commits=[entry.commit for entry in commits], indexed_version=indexed_version
    )
    latest = releases[0] if releases else None
    latest_in_index = alignment[latest.tag_name][0] if latest is not None else None
    drift_record = next((release.drift for release in releases if release.drift is not None), None)
    if drift_record is None and product_reference is not None:
        drift_record = product_reference.drift
    drift = drift_from_record(drift_record, indexed_commit=indexed_commit, indexed_version=indexed_version)
    drift_state = drift["drift"]
    drift_warnings = (
        (drift["message"],) if drift_state in (INDEX_AHEAD, PACKAGE_AHEAD, DRIFT_DIVERGED) else ()
    )

    def build(state: str, reason: str, basis: str, compared_against: str | None = None) -> FreshnessReport:
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
            state != "fresh",
            reason,
            source_commits=commits,
            chunks_without_commit=provenance.chunks_without_commit,
            pinned_library_commit=pinned_library_commit,
            pinned_matches_symbols=pinned_matches,
            indexed_types=provenance.indexed_types,
            total_types=provenance.total_types,
            coverage_note=coverage_note,
            example_search=example_mode,
            example_search_reason=example_reason,
            freshness_state=state,
            freshness_basis=basis,
            indexed_at=generation_indexed_at(generation_id),
            source_repository=product_reference.source_repository if product_reference else None,
            latest_release_tag=latest.tag_name if latest is not None else None,
            latest_release_in_index=latest_in_index,
            warnings=_abbreviation_warnings(current_source_commit) + drift_warnings,
            compared_against_commit=compared_against,
            drift=drift_state,
            index_ahead_of_package=drift["index_ahead_of_package"],
            package_ahead_of_index=drift["package_ahead_of_index"],
            drift_commit_distance=drift["commit_distance"],
            published_version=drift["published_version"],
            drift_message=drift["message"],
        )

    if indexed_commit is None:
        return build("unknown", "indexed generation does not record its source commit", "none")
    if current_source_commit is not None:
        state, reason = _commit_verdict(commits, indexed_commit, current_source_commit)
        return build(state, reason, "caller_commit")
    if drift_state != DRIFT_UNKNOWN:
        # G3/TC-560: the direction comes from git ancestry, not from "the commits differ": an index
        # that is AHEAD of the latest release tag is not stale, a package that is ahead of the index is.
        state = "fresh" if drift_state in (INDEX_AHEAD, DRIFT_IN_SYNC) else "stale"
        reason = f"{drift['message']} No current_source_commit was given."
        if drift_state == INDEX_AHEAD:
            reason += " Nothing newer than the index was found."
        return build(state, reason, "git_ancestry")
    if latest is not None and latest.target_commit is not None:
        state, reason = _commit_verdict(commits, indexed_commit, latest.target_commit)
        reason += (
            f"; compared against the latest release tag {latest.tag_name} "
            f"({latest.target_commit[:_SHORT_COMMIT_LENGTH]}) because no current_source_commit was given"
        )
        return build(state, reason, "latest_release_tag_commit", compared_against=latest.target_commit)
    if latest is not None and latest_in_index is False and indexed_version is not None:
        indexed_text = ".".join(str(part) for part in indexed_version)
        return build(
            "stale",
            f"latest release {latest.tag_name} is newer than the indexed package version {indexed_text}; "
            "no current_source_commit was given",
            "latest_release_version",
        )
    if len(commits) > 1:
        return build("unknown", _mixed_reason(commits, None), "none")
    return build("unknown", "current source commit is unknown; staleness cannot be ruled out", "none")
