"""G3/TC-613 (REQ-G3-012): a GitHub Release's real commit is resolved before drift detection runs.

Before this card, ``infra/fetch_recent_releases.py``'s ``_with_ancestry`` passed a Release-sourced
entry straight to ``fetch_drift`` even when its ``target_commitish`` was a branch name (e.g.
"main") rather than a real commit - ``report_index_freshness`` then reported drift as "unknown"
for every such repository (the measured 3D-NET-007 gap: 34%, 101-of-297-type, for 3d/net), even
though ``infra/verify_package_registry.resolve_tag_commit`` - already built, already tested -
could resolve that same branch name (or the release's own tag name) to the real commit GitHub's
``commits/{ref}`` endpoint reports right now.

Fully offline per this card's own ``network: false``: every test here monkeypatches
``fetch_recent_releases.resolve_tag_commit`` directly (never the raw HTTP call inside
``infra.verify_package_registry`` itself) and ``github_release_reader._get_json`` (the HTTP layer
``fetch_drift`` calls), so no test in this file ever reaches the real network.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

import infra.fetch_recent_releases as fetch_recent_releases
from foss_mcp.extraction import github_release_reader as reader
from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.mcp.tools.release_alignment import drift_from_record

SHA_INDEXED = "1234567890abcdef1234567890abcdef12345678"


def _compare(ahead: int, behind: int, base_sha: str) -> dict:
    return {
        "status": "ahead" if ahead and not behind else "behind" if behind and not ahead else "diverged",
        "ahead_by": ahead,
        "behind_by": behind,
        "base_commit": {"sha": base_sha},
        "commits": [],
        "files": [],
    }


class _Http:
    """A ``github_release_reader._get_json`` stand-in keyed by a URL fragment, the same shape
    ``tests/infra/test_round3_release_tags.py`` already uses for ``fetch_drift``'s own tests."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.urls: list[str] = []

    def __call__(self, url: str, *, etag: str | None = None) -> tuple[int, Any, str | None]:
        self.urls.append(url)
        for fragment, body in self.answers.items():
            if fragment in url:
                return 200, body, None
        raise AssertionError(f"unexpected url {url}")


def _args(repository: str, limit: int = 10) -> argparse.Namespace:
    return argparse.Namespace(repository=repository, limit=limit)


def _no_newer_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """A release-sourced entry isn't ``source == "tag"``, so ``_with_ancestry`` always reads tags
    looking for a newer one first; stub that read to "none" so each test's own ``_Http`` only ever
    needs to answer the ``compare`` call it actually cares about."""
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", lambda repository, **kwargs: [])


# --- (a) a branch-name target_commitish is resolved before drift is computed --------------------


def test_a_branch_name_target_commitish_is_resolved_before_drift_is_computed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = "o/r"
    resolved_sha = "a" * 40
    release = Release(tag_name="v1.2.3", body="", richness="none", target_commitish="main", source="release")
    resolve_calls: list[tuple[str, str]] = []

    def _resolve(repo: str, ref: str) -> str | None:
        resolve_calls.append((repo, ref))
        return resolved_sha

    _no_newer_tags(monkeypatch)
    monkeypatch.setattr(fetch_recent_releases, "resolve_tag_commit", _resolve)
    http = _Http({f"/compare/{resolved_sha}...": _compare(2, 0, base_sha=resolved_sha)})
    monkeypatch.setattr(reader, "_get_json", http)

    merged = fetch_recent_releases._with_ancestry(_args(repository), [release], SHA_INDEXED)

    # Resolved by the release's own TAG NAME, not by the literal branch-name target_commitish.
    assert resolve_calls == [(repository, "v1.2.3")]
    assert merged[0].target_commitish == resolved_sha
    assert merged[0].target_commit == resolved_sha
    # The drift record was computed against the REAL resolved commit, not the literal string
    # "main" nor the bare tag name "v1.2.3".
    assert http.urls == [
        f"https://api.github.com/repos/{repository}/compare/{resolved_sha}...{SHA_INDEXED}?per_page=1"
    ]
    assert merged[0].drift is not None
    assert merged[0].drift.ahead_by == 2
    assert merged[0].drift.tag_commit == resolved_sha
    assert drift_from_record(merged[0].drift, indexed_commit=SHA_INDEXED)["drift"] != "unknown"


# --- (b) a resolution failure degrades gracefully ------------------------------------------------


def test_a_resolution_failure_leaves_the_entry_exactly_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = "o/r"
    release = Release(tag_name="v1.2.3", body="", richness="none", target_commitish="main", source="release")

    _no_newer_tags(monkeypatch)
    monkeypatch.setattr(fetch_recent_releases, "resolve_tag_commit", lambda repo, ref: None)
    # Unresolved: fetch_drift must still fall back to the bare tag name as base, exactly as it did
    # before this card (the pre-existing, pre-TC-613 "unknown"-class shape).
    http = _Http({"/compare/v1.2.3...": _compare(0, 0, base_sha="irrelevant")})
    monkeypatch.setattr(reader, "_get_json", http)

    merged = fetch_recent_releases._with_ancestry(_args(repository), [release], SHA_INDEXED)

    assert merged[0].target_commitish == "main"  # unchanged - no fabricated commit
    assert merged[0].target_commit is None
    assert merged[0].drift is not None
    assert merged[0].drift.tag == "v1.2.3"
    assert http.urls == [
        f"https://api.github.com/repos/{repository}/compare/v1.2.3...{SHA_INDEXED}?per_page=1"
    ]


def test_a_resolution_exception_is_caught_and_also_leaves_the_entry_unchanged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = "o/r"
    release = Release(tag_name="v1.2.3", body="", richness="none", target_commitish="main", source="release")

    def _raise(repo: str, ref: str) -> str | None:
        raise OSError("offline")

    _no_newer_tags(monkeypatch)
    monkeypatch.setattr(fetch_recent_releases, "resolve_tag_commit", _raise)
    http = _Http({"/compare/v1.2.3...": _compare(0, 0, base_sha="irrelevant")})
    monkeypatch.setattr(reader, "_get_json", http)

    merged = fetch_recent_releases._with_ancestry(_args(repository), [release], SHA_INDEXED)

    assert merged[0].target_commitish == "main"  # no crash, no fabricated commit
    assert "WARNING" in capsys.readouterr().err


# --- (c) a tag-sourced entry is completely unaffected ---------------------------------------------


def test_a_tag_sourced_entry_is_completely_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = "o/r"
    sha = "b" * 40
    tag_entry = Release(tag_name="v0.9.0", body="", richness="none", target_commitish=sha, source="tag")

    def _must_not_be_called(repo: str, ref: str) -> str | None:
        raise AssertionError("resolve_tag_commit must not be called for an already-resolved entry")

    monkeypatch.setattr(fetch_recent_releases, "resolve_tag_commit", _must_not_be_called)
    http = _Http({f"/compare/{sha}...": _compare(1, 0, base_sha=sha)})
    monkeypatch.setattr(reader, "_get_json", http)

    merged = fetch_recent_releases._with_ancestry(_args(repository), [tag_entry], SHA_INDEXED)

    # Byte-identical to before this card: same target_commitish, same source, same drift shape.
    assert merged[0].target_commitish == sha
    assert merged[0].source == "tag"
    assert merged[0].drift is not None
    assert merged[0].drift.ahead_by == 1
    assert merged[0].drift.tag_commit == sha
    assert http.urls == [
        f"https://api.github.com/repos/{repository}/compare/{sha}...{SHA_INDEXED}?per_page=1"
    ]


# --- (d) a concrete reproduction of the measured 3D-NET-007 case ---------------------------------


def test_the_3d_net_branch_name_release_resolves_instead_of_reporting_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 3D-NET-007: 3d/net's newest GitHub Release carries a branch-name target_commitish ("main"),
    # the exact shape that made report_index_freshness report drift as "unknown" for a real,
    # measurable 34%/101-of-297-type gap, even though the index was genuinely behind the package.
    repository = "3d/net"
    fake_resolved_sha = "c" * 40
    release = Release(tag_name="v3.4.0", body="Release notes", richness="detailed", target_commitish="main")

    _no_newer_tags(monkeypatch)
    monkeypatch.setattr(fetch_recent_releases, "resolve_tag_commit", lambda repo, ref: fake_resolved_sha)
    http = _Http({f"/compare/{fake_resolved_sha}...": _compare(0, 101, base_sha=fake_resolved_sha)})
    monkeypatch.setattr(reader, "_get_json", http)

    merged = fetch_recent_releases._with_ancestry(_args(repository), [release], SHA_INDEXED)

    assert merged[0].drift is not None
    assert merged[0].drift.error is None
    assert merged[0].drift.behind_by == 101
    verdict = drift_from_record(merged[0].drift, indexed_commit=SHA_INDEXED)
    # Before this card: an unresolved branch-name target_commitish fell back to the bare tag name
    # as base, which is not a real ref GitHub can compare from the branch's own shape - the
    # measured, reported outcome was "unknown" purely because of that shape, never a real verdict.
    assert verdict["drift"] != "unknown"
