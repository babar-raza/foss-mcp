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
from dataclasses import dataclass, field

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.known_defects import KnownDefect, defect_is_stale, load_known_defects
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import search_docs
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
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES
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
    # (caller_commit, git_ancestry, latest_release_tag_commit, latest_release_version,
    # registry_served_version, none) says how it was decided.
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
    # G3/TC-643 (KD5, REQ-G3-015): rendered known-defect disclosure lines for this pilot's
    # config/known_defects registry (foss_mcp.indexing.known_defects), in registry file order.
    # A commit-scoped defect (``applies_to.indexed_commit`` set) applies only when that commit
    # equals ``indexed_source_commit`` above, exactly. A package-version-scoped defect (no
    # ``indexed_commit``) always applies, worded as a flat assertion unless ``defect_is_stale``
    # finds the real served version (``registry_served_version``, TC-621/TC-629) has moved past
    # the version the defect was last verified broken on - the first real production caller of
    # ``defect_is_stale`` - in which case it carries the Decision C downgrade wording instead
    # (DECISION_LOG.md's 2026-10-30 entry, decision C).
    known_defects: tuple[str, ...] = ()
    # G3/TC-651 (REQ-G3-013, workstream I): every one of search_docs.CONTENT_TYPES mapped to its
    # real integer documentation-chunk count for the active generation - including an explicit 0
    # for an empty category, never an absent key. 15 of 17 round-2 issues (PDF-NET-004 and 14
    # others) are the same shape: a category (usually faq, sometimes troubleshooting) is
    # completely empty for a pilot with no indication of that before a caller spends a query
    # discovering it. Defaults to all-zero (never published / no documentation chunks at all).
    documentation_content_type_counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(CONTENT_TYPES, 0)
    )


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
    # G3/TC-651: every search_docs.CONTENT_TYPES value mapped to its real documentation-chunk
    # count across the whole generation (all commits combined), zero-inclusive.
    content_type_counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(CONTENT_TYPES, 0))


def _read_provenance(manifest_payload: dict) -> _Provenance:
    documents = (manifest_payload.get("lexical_index") or {}).get("documents") or {}
    per_commit: dict[str, dict[str, int]] = {}
    without_commit = 0
    indexed_types: int | None = None
    total_types: int | None = None
    coverage_text: str | None = None
    # G3/TC-651: tallied across every chunk _chunk_kind classifies as documentation, regardless of
    # which (or whether any) source commit it carries - a caller asking "which categories are
    # empty" cares about the whole generation, not one commit's slice of it.
    content_type_counts: dict[str, int] = dict.fromkeys(CONTENT_TYPES, 0)
    # Sorted by document id so "the first API-Coverage line" does not depend on dict order.
    for _, document in sorted(documents.items()):
        text = document["text"]
        kind = _chunk_kind(text)
        if kind == _SYMBOL and indexed_types is None:
            coverage = parse_api_coverage(text)
            if coverage is not None:
                indexed_types, total_types = coverage.indexed, coverage.total
                coverage_text = text
        if kind == _DOCUMENTATION:
            content_type_counts[search_docs.classify_content_type(text)] += 1
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
    return _Provenance(
        tuple(commits), without_commit, indexed_types, total_types, coverage_text, content_type_counts
    )


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


def read_generation_commits(
    store: GenerationManifestStore, scope: Scope, source_kind: str = "self_extracted"
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """``(generation_id, indexed_source_commit, every_commit)`` of *scope*'s active generation (G3/TC-564).

    ``indexed_source_commit`` is the commit ``report_index_freshness`` reports as the one the API symbols
    came from, ``every_commit`` all distinct commits in the report's order. ``(None, None, ())`` when
    nothing is published. Raises whatever the store raises for a pointer or generation it cannot read."""
    key = scope_key(scope, source_kind)
    generation_id = store.read_active(key)
    if generation_id is None:
        return None, None, ()
    commits = _read_provenance(store.read_generation(key, generation_id).payload).source_commits
    return generation_id, _indexed_source_commit(commits), tuple(entry.commit for entry in commits)


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


def _render_known_defect(defect: KnownDefect) -> str:
    """One factual disclosure line built from *defect*'s own recorded ``summary``/``user_action``/
    ``evidence.repro`` - the exact wording ``get_symbol._render_known_defect`` and
    ``search_symbols._render_known_defect`` already use, reimplemented locally here per this
    disclosure program's established convention (KD2/KD3): each tool's rendering helper is its
    own independent copy, not a shared import."""
    return f"Known defect: {defect.summary} {defect.user_action} (repro: {defect.evidence.repro})"


def _render_stale_known_defect(defect: KnownDefect, served_version: tuple[int, ...]) -> str:
    """The Decision C downgrade wording (DECISION_LOG.md's ``## 2026-10-30`` entry, decision C):
    once the real served package version has moved past the version *defect* was last verified
    broken on, the disclosure downgrades from a flat assertion to "last verified broken on X; not
    re-verified on Y" rather than silently going stale."""
    served_text = ".".join(str(part) for part in served_version)
    return (
        f"Known defect (last verified broken on {defect.evidence.verified_on.version}; "
        f"not re-verified on {served_text}): {defect.summary} {defect.user_action} "
        f"(repro: {defect.evidence.repro})"
    )


def known_defect_lines(
    defects: Sequence[KnownDefect],
    indexed_source_commit: str | None,
    registry_served_version: tuple[int, ...] | None,
) -> tuple[str, ...]:
    """Every known-defect disclosure line for this report, in registry file order (KD5,
    G3/REQ-G3-015).

    A defect scoped by ``applies_to.indexed_commit`` applies only when that commit equals
    *indexed_source_commit* exactly - no abbreviation, the registry only ever records a full
    40-hex commit (``known_defects.validate_known_defects``'s own ``_COMMIT_RE``). A
    package-version-scoped defect (no ``indexed_commit``) always applies, the same
    "no live registry check against a currently served package version" default KD2/KD3/KD4
    already use for it (``get_symbol.known_defect_lines``'s own docstring) - except here, for the
    first time, a real served version can be available: when *registry_served_version* is given
    and ``defect_is_stale`` finds it has moved past the version the defect was last verified
    broken on, the line carries the Decision C downgrade wording instead of the flat assertion.
    """
    lines: list[str] = []
    for defect in defects:
        commit_scope = defect.applies_to.indexed_commit
        if commit_scope is not None:
            if commit_scope == indexed_source_commit:
                lines.append(_render_known_defect(defect))
            continue
        if registry_served_version is not None and defect_is_stale(defect, registry_served_version):
            lines.append(_render_stale_known_defect(defect, registry_served_version))
        else:
            lines.append(_render_known_defect(defect))
    return tuple(lines)


def _registry_version_drift(
    padded_registry_version: tuple[int, ...],
    padded_indexed_version: tuple[int, ...],
    registry_text: str,
    indexed_text: str,
) -> dict:
    """The same evidence shape ``release_alignment.drift_from_record`` returns, built from a
    version comparison instead of git ancestry (G3/TC-657, fixing SLIDES-JAVA-004): the
    ``registry_served_version`` branch has no commit-ancestry counts to decide
    ``index_ahead_of_package``/``package_ahead_of_index`` from, but the version comparison it
    already performs is itself a real, disclosed ancestry signal, and a confident ``"fresh"``/
    ``"stale"`` verdict from this branch must carry it exactly like the ``git_ancestry`` branch's
    own confident verdicts do - never a bare verdict with no supporting fields.

    *padded_registry_version*/*padded_indexed_version* are the equal-width tuples the branch
    already compares; *registry_text*/*indexed_text* are the unpadded, as-given strings used in
    the report's own ``reason`` text, reused here so ``published_version`` matches it exactly.
    """
    if padded_registry_version > padded_indexed_version:
        return {
            "drift": PACKAGE_AHEAD,
            "index_ahead_of_package": False,
            "package_ahead_of_index": True,
            "commit_distance": None,
            "published_version": registry_text,
            "message": (
                f"The package registry serves version {registry_text}, newer than the indexed "
                f"package version {indexed_text}: the published package can have types and "
                "members this index does not describe."
            ),
        }
    if padded_registry_version == padded_indexed_version:
        return {
            "drift": DRIFT_IN_SYNC,
            "index_ahead_of_package": False,
            "package_ahead_of_index": False,
            "commit_distance": 0,
            "published_version": registry_text,
            "message": f"The indexed package version is the version the registry serves ({registry_text}).",
        }
    return {
        "drift": INDEX_AHEAD,
        "index_ahead_of_package": True,
        "package_ahead_of_index": False,
        "commit_distance": None,
        "published_version": registry_text,
        "message": (
            f"The indexed package version {indexed_text} is ahead of the version the registry "
            f"serves ({registry_text}): the index describes source that is not in the published "
            "package, which can lack types and members documented here."
        ),
    }


def report_index_freshness(
    store: GenerationManifestStore,
    scope: Scope,
    source_kind: str,
    current_source_commit: str | None,
    *,
    pinned_library_commit: str | None = None,
    product_reference: ProductReferenceInputs | None = None,
    releases: Sequence[Release] = (),
    registry_served_version: tuple[int, ...] | None = None,
) -> FreshnessReport:
    """Compare every commit ``scope``'s active generation (for *source_kind*) was indexed from
    against *current_source_commit*. Never publishes a generation, and never invents a verdict
    when the information needed to know is simply absent - but it is not a pure commit-equality
    check either: when no commit signal resolves it, it falls back through a sequence of
    progressively weaker, explicitly-graded bases (``freshness_basis``) ending in a version
    comparison against the live package registry (G3/TC-621, below), each one disclosed, never
    silently assumed.

    When *pinned_library_commit* is given, the report also says whether the API symbols were
    extracted at it. When the chunks state how much of the API surface they hold, the report
    carries that share; otherwise it stays unknown.

    G3/TC-519: the verdict is tri-state (``freshness_state`` fresh / stale / unknown; ``stale`` is
    ``freshness_state != "fresh"``) with its ``freshness_basis``. When *current_source_commit* is
    omitted, the newest of *releases* is the reference: its tag commit when it has one, else its
    version against the version the package manifest (*product_reference*) declares; with neither
    the verdict stays unknown and the legacy reason is kept.

    G3/TC-621: when every signal above still leaves the verdict unresolved and *registry_served_version*
    is given (already parsed, matching ``release_alignment.package_version``'s own shape - the caller
    is responsible for parsing whatever raw string the real package registry serves), it is compared
    against ``indexed_version`` (the manifest's own declared version): a strictly newer registry
    version is ``"stale"`` with basis ``"registry_served_version"``, an equal or older one is
    ``"fresh"`` with the same basis - and, as of G3/TC-657, this verdict populates ``drift``,
    ``published_version`` and ``index_ahead_of_package``/``package_ahead_of_index`` the same way the
    ``git_ancestry`` branch above already does for an equivalent verdict, derived from the version
    comparison itself rather than from commit ancestry (no ancestry data is available here).

    G3/TC-657: ``indexed_version`` is only trustworthy for this comparison when its major version
    number agrees with *registry_served_version*'s - the same guard
    ``release_alignment.release_in_index`` already applies to a release tag against the manifest
    version (a calendar-style tag such as ``v26.09`` against a placeholder ``0.1.0`` manifest has a
    different major and is never compared there either). Some pilots' packaging manifests declare a
    real, meaningful version that tracks the registry; others (confirmed: email/net's ``.csproj``)
    declare a generic, never-bumped placeholder such as ``0.1.0`` that means nothing next to a real
    served version like ``26.7.0``. Comparing those would produce an arithmetically "correct" but
    semantically meaningless confident verdict, so a major-version mismatch here reports
    ``"unknown"`` instead - an honest miss, not a guess dressed up as a verdict. With no
    ``indexed_version`` to compare against, or that mismatch, the verdict falls through to the
    legacy ``"unknown"`` fallback unchanged. Wiring a live registry fetch into
    *registry_served_version* is a separate card's job; this function only consumes it.
    """
    key = scope_key(scope, source_kind)
    defects = load_known_defects(scope.family, scope.platform)
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
            known_defects=known_defect_lines(defects, None, registry_served_version),
            documentation_content_type_counts=dict.fromkeys(CONTENT_TYPES, 0),
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

    def build(
        state: str,
        reason: str,
        basis: str,
        compared_against: str | None = None,
        version_drift: dict | None = None,
    ) -> FreshnessReport:
        if pinned_matches is False and pinned_library_commit is not None:
            reason = (
                f"{reason}. API symbols were not extracted at the pinned library commit "
                f"{pinned_library_commit.strip()[:_SHORT_COMMIT_LENGTH]} (they come from "
                f"{(indexed_commit or '')[:_SHORT_COMMIT_LENGTH]})"
            )
        # G3/TC-657 (SLIDES-JAVA-004): *version_drift*, when given, is the registry_served_version
        # branch's own version-comparison-derived evidence - used in place of the git-ancestry
        # *drift* computed above, which is always DRIFT_UNKNOWN by the time that branch is reached
        # (any non-unknown git-ancestry drift would already have returned via the git_ancestry
        # branch above), so a confident verdict from the registry branch is never left with none
        # of the supporting fields the sibling git_ancestry branch already populates.
        evidence = version_drift if version_drift is not None else drift
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
            drift=evidence["drift"],
            index_ahead_of_package=evidence["index_ahead_of_package"],
            package_ahead_of_index=evidence["package_ahead_of_index"],
            drift_commit_distance=evidence["commit_distance"],
            published_version=evidence["published_version"],
            drift_message=evidence["message"],
            known_defects=known_defect_lines(defects, indexed_commit, registry_served_version),
            documentation_content_type_counts=dict(provenance.content_type_counts),
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
    if (
        registry_served_version is not None
        and indexed_version is not None
        and registry_served_version[0] == indexed_version[0]
    ):
        # G3/TC-657 (EML-NET-007): the major-version-agreement check above is what makes the
        # manifest's declared version a trustworthy signal here at all - the same guard
        # release_alignment.release_in_index already applies to a release tag against the
        # manifest version. Some pilots' manifests never get bumped from a generic placeholder
        # (confirmed: email/net's .csproj declares "0.1.0" while the registry serves "26.7.0");
        # comparing that against a real registry version would produce an arithmetically
        # "correct" but meaningless confident verdict. A major mismatch falls through to the
        # honest "unknown" fallback below instead, same as having no indexed_version at all - it
        # does NOT disable this branch for pilots whose manifest version is real (same major as
        # the registry's).
        indexed_text = ".".join(str(part) for part in indexed_version)
        registry_text = ".".join(str(part) for part in registry_served_version)
        # G3/TC-629: pad both tuples to equal width with trailing zeros before comparing, the
        # same pattern release_alignment.release_in_index already uses - a version served at
        # higher precision than the manifest declares (e.g. "26.7.0" vs "26.7") is not newer.
        width = max(len(registry_served_version), len(indexed_version))
        padded_registry_version = registry_served_version + (0,) * (width - len(registry_served_version))
        padded_indexed_version = indexed_version + (0,) * (width - len(indexed_version))
        # G3/TC-657 (SLIDES-JAVA-004): the confident verdict below now carries the same
        # drift/published_version/index_ahead_of_package evidence the git_ancestry branch's
        # own confident verdicts already carry, derived from this version comparison itself.
        version_drift = _registry_version_drift(
            padded_registry_version, padded_indexed_version, registry_text, indexed_text
        )
        if padded_registry_version > padded_indexed_version:
            return build(
                "stale",
                f"the package registry serves version {registry_text}, newer than the indexed "
                f"package version {indexed_text}",
                "registry_served_version",
                version_drift=version_drift,
            )
        return build(
            "fresh",
            f"the package registry serves version {registry_text}, no newer than the indexed "
            f"package version {indexed_text}",
            "registry_served_version",
            version_drift=version_drift,
        )
    if (
        registry_served_version is not None
        and indexed_version is not None
        and registry_served_version[0] != indexed_version[0]
    ):
        # G3/TC-712 (EML-NET-005): the major-version-agreement branch above correctly withholds a
        # confident verdict here - the manifest's own declared version (indexed_version) is not a
        # trustworthy freshness signal once its major disagrees with the registry's, exactly as
        # that branch's own comment explains. But the real registry-served version
        # (registry_served_version) is still known and still a real fact, just not one that can
        # decide fresh/stale by itself. Disclose it in published_version without upgrading the
        # verdict: reuse drift (always DRIFT_UNKNOWN by this point, same as the legacy fallback
        # below) with only published_version overridden from the real registry text - every other
        # evidence field (drift/index_ahead_of_package/package_ahead_of_index/commit_distance/
        # message) stays exactly what the legacy fallback already carries.
        indexed_text = ".".join(str(part) for part in indexed_version)
        registry_text = ".".join(str(part) for part in registry_served_version)
        return build(
            "unknown",
            f"the package registry serves version {registry_text}, but the indexed package "
            f"version {indexed_text} has a different major version; the manifest's own declared "
            "version is not a trustworthy freshness signal here",
            "registry_served_version_major_mismatch",
            version_drift={**drift, "published_version": registry_text},
        )
    if len(commits) > 1:
        return build("unknown", _mixed_reason(commits, None), "none")
    return build("unknown", "current source commit is unknown; staleness cannot be ruled out", "none")
