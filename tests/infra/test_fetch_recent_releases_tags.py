"""Sidecar cap of 20 releases and the tag fallback of ``infra/fetch_recent_releases.py`` (G3/TC-519).

Fully offline: ``fetch_releases`` and ``fetch_tags`` are monkeypatched, nothing sleeps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.fetch_recent_releases as fetch_recent_releases
from foss_mcp.extraction.github_release_reader import Release

REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-Go"
_SHA = "0123456789abcdef0123456789abcdef01234567"


def _set_argv(monkeypatch: pytest.MonkeyPatch, output: Path, *extra: str) -> None:
    argv = ["fetch_recent_releases.py", "--repository", REPOSITORY, "--output", str(output), *extra]
    monkeypatch.setattr(sys, "argv", argv)


def _releases(count: int) -> list[Release]:
    return [Release(f"v1.{index}.0", "Fixed `x`", "detailed") for index in range(count, 0, -1)]


def _tags(count: int) -> list[Release]:
    return [
        Release(
            f"t{index}",
            "",
            "none",
            url=f"https://github.com/o/r/releases/tag/t{index}",
            target_commitish=_SHA,
            source="tag",
        )
        for index in range(count, 0, -1)
    ]


def _install(
    monkeypatch: pytest.MonkeyPatch, releases: list[Release], tags: list[Release] | Exception
) -> dict[str, int]:
    calls = {"releases": 0, "tags": 0}

    def _fetch_releases(repository: str, **kwargs: object) -> list[Release]:
        calls["releases"] += 1
        return list(releases)

    def _fetch_tags(repository: str, **kwargs: object) -> list[Release]:
        calls["tags"] += 1
        if isinstance(tags, Exception):
            raise tags
        return list(tags)

    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fetch_releases)
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", _fetch_tags)
    return calls


def test_the_default_limit_is_20() -> None:
    assert fetch_recent_releases._DEFAULT_LIMIT == 20


def test_more_than_10_releases_are_kept_up_to_20(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _releases(25), [])
    output = tmp_path / "recent_releases_pdf_go.json"
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert len(written) == 20
    assert written[0]["tag_name"] == "v1.25.0"


def test_twelve_releases_are_all_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _releases(12), [])
    output = tmp_path / "recent_releases_pdf_go.json"
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert len(json.loads(output.read_text(encoding="utf-8"))) == 12


def test_the_limit_argument_still_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _releases(25), [])
    output = tmp_path / "recent_releases_pdf_go.json"
    _set_argv(monkeypatch, output, "--limit", "3")

    fetch_recent_releases.main()

    assert len(json.loads(output.read_text(encoding="utf-8"))) == 3


def test_zero_releases_fall_back_to_tags_and_mark_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install(monkeypatch, [], _tags(4))
    output = tmp_path / "recent_releases_pdf_go.json"
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert calls == {"releases": 1, "tags": 1}
    assert [entry["tag_name"] for entry in written] == ["t4", "t3", "t2", "t1"]
    assert all(entry["source"] == "tag" for entry in written)
    assert all(entry["richness"] == "none" and entry["body"] == "" for entry in written)
    assert written[0]["target_commitish"] == _SHA
    meta = json.loads(fetch_recent_releases.releases_meta_path(output).read_text(encoding="utf-8"))
    assert meta["state"] == "ok"
    assert meta["release_count"] == 4
    assert not fetch_recent_releases._fetch_failed_marker(output).exists()


def test_the_tag_fallback_keeps_at_most_the_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [], _tags(30))
    output = tmp_path / "recent_releases_pdf_go.json"
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert len(json.loads(output.read_text(encoding="utf-8"))) == 20


def test_releases_present_means_tags_are_not_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, _releases(3), _tags(5))
    output = tmp_path / "recent_releases_pdf_go.json"
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert calls == {"releases": 1, "tags": 0}
    written = json.loads(output.read_text(encoding="utf-8"))
    assert all("source" not in entry for entry in written)


def test_a_failing_tags_fetch_follows_the_failure_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install(monkeypatch, [], RuntimeError("boom"))
    output = tmp_path / "recent_releases_pdf_go.json"
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert calls == {"releases": 1, "tags": 1}
    assert json.loads(output.read_text(encoding="utf-8")) == []
    assert fetch_recent_releases._fetch_failed_marker(output).is_file()
    meta = json.loads(fetch_recent_releases.releases_meta_path(output).read_text(encoding="utf-8"))
    assert meta["state"] == "fetch_failed"
    assert "RuntimeError" in capsys.readouterr().err


def test_a_failing_tags_fetch_keeps_a_good_existing_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "recent_releases_pdf_go.json"
    before = json.dumps([{"tag_name": "t1", "body": "", "richness": "none", "source": "tag"}])
    output.write_text(before, encoding="utf-8")
    _install(monkeypatch, [], RuntimeError("boom"))
    _set_argv(monkeypatch, output, "--force")

    fetch_recent_releases.main()

    assert output.read_text(encoding="utf-8") == before
    assert fetch_recent_releases._fetch_failed_marker(output).is_file()


def test_a_tag_entry_round_trips_through_the_sidecar_and_an_old_entry_loads_as_a_release(
    tmp_path: Path,
) -> None:
    tag = Release("t1", "", "none", url="https://x/t1", target_commitish=_SHA, source="tag")
    release = Release("v1", "Fixed `a`", "detailed")
    assert fetch_recent_releases._serialize_release(tag)["source"] == "tag"
    assert "source" not in fetch_recent_releases._serialize_release(release)

    output = tmp_path / "recent_releases_pdf_go.json"
    fetch_recent_releases.atomic_write_json(
        output,
        [fetch_recent_releases._serialize_release(tag), fetch_recent_releases._serialize_release(release)],
    )
    assert fetch_recent_releases.load_recent_releases_sidecar(output) == (tag, release)

    output.write_text(json.dumps([{"tag_name": "v0", "body": "", "richness": "none"}]), encoding="utf-8")
    loaded = fetch_recent_releases.load_recent_releases_sidecar(output)
    assert loaded is not None
    assert loaded[0].source == "release"


def test_a_tag_only_sidecar_is_not_kept_by_a_not_modified_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The releases list of a tag-only repository is empty and never changes, so an ETag taken from it
    # must not be sent: tags are re-read instead.
    output = tmp_path / "recent_releases_pdf_go.json"
    fetch_recent_releases.atomic_write_json(
        output, [{"tag_name": "t1", "body": "", "richness": "none", "source": "tag"}]
    )
    fetch_recent_releases.atomic_write_json(
        fetch_recent_releases.releases_meta_path(output),
        {"state": "ok", "source_repository": REPOSITORY, "etag": 'W/"abc"', "release_count": 1},
    )
    seen: list[object] = []

    def _fetch_releases(repository: str, **kwargs: object) -> list[Release]:
        seen.append(kwargs.get("etag"))
        return []

    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fetch_releases)
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", lambda repository, **kwargs: _tags(2))
    _set_argv(monkeypatch, output, "--force")

    fetch_recent_releases.main()

    assert seen == [None]
    assert [entry["tag_name"] for entry in json.loads(output.read_text(encoding="utf-8"))] == ["t2", "t1"]
