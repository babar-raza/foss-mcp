"""NOTE-PY-003 (round-2 audit): ``list_recent_changes``'s own tool description promises tag-only
fallback entries fill the remaining ``--limit`` slots "when fewer Releases exist" - not only when
the Releases list is COMPLETELY empty. Before this card, ``infra/fetch_recent_releases.py``'s
``main`` only ever called ``fetch_tags`` when ``fetched`` was entirely empty (``if not fetched``),
so a repository with even one real GitHub Release (note/python: 2 releases, 17 tags) never had its
remaining slots padded with older tags at all.

``pad_with_older_tags`` is the fix: additive to ``main``'s existing output, distinct from
``merge_newer_tags`` (G3/TC-560, built for ``_with_ancestry``'s drift-detection use case, which only
ever adds a tag NEWER than every release). Every tag padded in here may be older or newer than the
existing releases - only an already-covered version, or an unparseable one, is excluded.

Fully offline per this card's own ``network: false``: every test here mocks
``fetch_recent_releases.fetch_releases``/``fetch_tags`` directly (the same pattern
``tests/infra/test_fetch_recent_releases_tags.py`` already uses), never the raw HTTP layer, so no
test in this file ever reaches the real network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.fetch_recent_releases as fetch_recent_releases
from foss_mcp.extraction.github_release_reader import DriftRecord, Release

REPOSITORY = "aspose-note-foss/Aspose.Note-FOSS-for-Python"
_SHA_RELEASE = "a" * 40
_SHA_INDEXED = "b" * 40


def _release(tag_name: str, *, target_commitish: str | None = None) -> Release:
    return Release(tag_name, "Fixed `x`", "detailed", target_commitish=target_commitish)


def _tag(tag_name: str, *, sha: str = _SHA_RELEASE) -> Release:
    return Release(
        tag_name,
        "",
        "none",
        url=f"https://github.com/{REPOSITORY}/releases/tag/{tag_name}",
        target_commitish=sha,
        source="tag",
    )


def _set_argv(monkeypatch: pytest.MonkeyPatch, output: Path, *extra: str) -> None:
    argv = ["fetch_recent_releases.py", "--repository", REPOSITORY, "--output", str(output), *extra]
    monkeypatch.setattr(sys, "argv", argv)


# --- direct unit tests of pad_with_older_tags ------------------------------------------------


def test_pads_with_the_correct_older_tags_newest_first_up_to_limit_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = [_release("v2.0.0")]
    # Deliberately out of version order, to prove the padding sorts its own additions rather than
    # trusting the tags API's own order.
    tags = [_tag("v0.5.0"), _tag("v1.5.0"), _tag("v1.0.0")]
    calls = {"tags": 0}
    monkeypatch.setattr(
        fetch_recent_releases,
        "fetch_tags",
        lambda repository, **kwargs: (calls.__setitem__("tags", calls["tags"] + 1), list(tags))[1],
    )

    padded = fetch_recent_releases.pad_with_older_tags(releases, REPOSITORY, 3)

    assert calls["tags"] == 1
    assert [r.tag_name for r in padded] == ["v2.0.0", "v1.5.0", "v1.0.0"]
    assert len(padded) == 3


def test_releases_already_at_limit_are_never_padded_and_tags_are_not_even_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = [_release("v3.0.0"), _release("v2.0.0"), _release("v1.0.0")]
    calls = {"tags": 0}

    def _fetch_tags(repository: str, **kwargs: object) -> list[Release]:
        calls["tags"] += 1
        return [_tag("v0.5.0")]

    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", _fetch_tags)

    padded = fetch_recent_releases.pad_with_older_tags(releases, REPOSITORY, 3)

    assert calls["tags"] == 0
    assert padded is releases
    assert [r.tag_name for r in padded] == ["v3.0.0", "v2.0.0", "v1.0.0"]


def test_a_tag_whose_version_already_exists_among_the_releases_is_never_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = [_release("v2.0.0")]
    # Same version as the release, spelled without the "v" prefix - still the same version.
    tags = [_tag("2.0.0"), _tag("v1.0.0")]
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", lambda repository, **kwargs: list(tags))

    padded = fetch_recent_releases.pad_with_older_tags(releases, REPOSITORY, 5)

    assert [r.tag_name for r in padded] == ["v2.0.0", "v1.0.0"]


def test_a_tag_with_no_parseable_version_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    releases = [_release("v2.0.0")]
    tags = [_tag("latest"), _tag("nightly-build"), _tag("v1.0.0")]
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", lambda repository, **kwargs: list(tags))

    padded = fetch_recent_releases.pad_with_older_tags(releases, REPOSITORY, 5)

    assert [r.tag_name for r in padded] == ["v2.0.0", "v1.0.0"]


def test_a_failed_tags_fetch_leaves_the_existing_releases_list_intact_and_logs_a_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    releases = [_release("v2.0.0")]

    def _fetch_tags(repository: str, **kwargs: object) -> list[Release]:
        raise RuntimeError("boom: simulated GitHub failure")

    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", _fetch_tags)

    padded = fetch_recent_releases.pad_with_older_tags(releases, REPOSITORY, 5)  # must not raise

    assert padded == releases
    assert "WARNING" in capsys.readouterr().err


def test_an_already_empty_releases_list_is_never_padded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"tags": 0}

    def _fetch_tags(repository: str, **kwargs: object) -> list[Release]:
        calls["tags"] += 1
        return [_tag("v1.0.0")]

    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", _fetch_tags)

    padded = fetch_recent_releases.pad_with_older_tags([], REPOSITORY, 5)

    assert padded == []
    assert calls["tags"] == 0


def test_more_candidate_tags_than_missing_slots_keeps_only_the_newest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = [_release("v5.0.0")]
    tags = [_tag("v4.0.0"), _tag("v3.0.0"), _tag("v2.0.0"), _tag("v1.0.0")]
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", lambda repository, **kwargs: list(tags))

    padded = fetch_recent_releases.pad_with_older_tags(releases, REPOSITORY, 2)

    assert [r.tag_name for r in padded] == ["v5.0.0", "v4.0.0"]


# --- end-to-end through main(): the no-``--indexed-commit`` path ------------------------------


def test_main_pads_the_written_sidecar_when_no_indexed_commit_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fetch_releases(repository: str, **kwargs: object) -> list[Release]:
        return [_release("v2.0.0")]

    def _fetch_tags(repository: str, **kwargs: object) -> list[Release]:
        return [_tag("v1.0.0"), _tag("v0.5.0")]

    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fetch_releases)
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", _fetch_tags)
    output = tmp_path / "recent_releases.json"
    _set_argv(monkeypatch, output, "--limit", "3")

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert [entry["tag_name"] for entry in written] == ["v2.0.0", "v1.0.0", "v0.5.0"]
    assert all("source" not in entry for entry in written if entry["tag_name"] == "v2.0.0")
    assert all(entry.get("source") == "tag" for entry in written if entry["tag_name"] != "v2.0.0")


# --- end-to-end through main(): the ``--indexed-commit`` path is unaffected -------------------


def test_main_pads_without_breaking_the_indexed_commit_drift_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors ``test_fetch_recent_releases_commit_resolution.py``'s own shape: with padding now
    also active (one release, well under the limit), the ``--indexed-commit`` path's own
    ``_with_ancestry``/``merge_newer_tags`` ancestry-newer-tag-merge must still run exactly as
    before - the entry padding would have added (an OLDER tag) must never appear in the final
    output, because ``_with_ancestry`` recomputes ``releases`` from ``fetched`` itself.
    """
    release = _release("v1.0.0", target_commitish=_SHA_RELEASE)
    newer_tag = _tag("v2.0.0", sha=_SHA_RELEASE)
    older_tag = _tag("v0.5.0", sha=_SHA_RELEASE)

    def _fetch_releases(repository: str, **kwargs: object) -> list[Release]:
        return [release]

    def _fetch_tags(repository: str, **kwargs: object) -> list[Release]:
        # Both tags come back for every caller (pad_with_older_tags AND _with_ancestry each call
        # fetch_tags once) - merge_newer_tags only ever keeps the NEWER one.
        return [older_tag, newer_tag]

    drift_calls: list[Release] = []

    def _fetch_drift(repository: str, tag: Release, indexed_commit: str) -> DriftRecord:
        drift_calls.append(tag)
        return DriftRecord(indexed_commit=indexed_commit, tag=tag.tag_name, ahead_by=0, behind_by=0)

    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fetch_releases)
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", _fetch_tags)
    monkeypatch.setattr(fetch_recent_releases, "fetch_drift", _fetch_drift)
    output = tmp_path / "recent_releases.json"
    _set_argv(monkeypatch, output, "--limit", "5", "--indexed-commit", _SHA_INDEXED)

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    # merge_newer_tags' own contract: the newer tag goes to the front, the older tag is never
    # added by it - and critically, pad_with_older_tags' own candidate (the SAME older tag) never
    # leaks into the final output either, because `releases` is fully overwritten by `_with_ancestry`.
    assert [entry["tag_name"] for entry in written] == ["v2.0.0", "v1.0.0"]
    assert written[0]["drift"]["ahead_by"] == 0
    assert drift_calls and drift_calls[0].tag_name == "v2.0.0"
