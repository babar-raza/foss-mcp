"""infra.fetch_recent_releases's first test coverage (G2/TC-244).

Before this card, ``foss_mcp.extraction.github_release_reader.fetch_releases`` was a real,
already-tested GitHub Releases reader with zero production callers, and
``infra/serve_http.py`` never passed any real releases to ``create_server`` -
``list_recent_changes`` always answered ``[]`` in production. These tests exercise
``infra/fetch_recent_releases.py`` as that first real production caller.

Fully offline per this card's own ``network: false``: every test here mocks
``fetch_recent_releases.fetch_releases`` itself (never the raw HTTP layer), so no test in this
file ever reaches the real network.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

import infra.fetch_recent_releases as fetch_recent_releases
from foss_mcp.extraction.github_release_reader import Release

REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"

_RELEASES = (
    Release(tag_name="v2.1.0", body="Fixed a bug in the parser", richness="detailed"),
    Release(tag_name="v2.0.0", body="", richness="none"),
)


def _fake_fetch_releases(calls: list[str]):
    def _fetch(repository: str, **kwargs: object) -> list[Release]:
        calls.append(repository)
        return list(_RELEASES)

    return _fetch


def _must_not_be_called(*args: object, **kwargs: object) -> None:
    raise AssertionError("fetch_releases must not be called")


def _set_argv(monkeypatch: pytest.MonkeyPatch, output_path: Path, *extra: str) -> None:
    argv = ["fetch_recent_releases.py", "--repository", REPOSITORY, "--output", str(output_path), *extra]
    monkeypatch.setattr(sys, "argv", argv)


def test_main_writes_the_real_shaped_json_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fake_fetch_releases(calls))
    output_path = tmp_path / "recent_releases.json"
    _set_argv(monkeypatch, output_path)

    fetch_recent_releases.main()

    assert calls == [REPOSITORY]
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written == [
        {"tag_name": "v2.1.0", "body": "Fixed a bug in the parser", "richness": "detailed"},
        {"tag_name": "v2.0.0", "body": "", "richness": "none"},
    ]


def test_main_respects_the_limit_argument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fake_fetch_releases(calls))
    output_path = tmp_path / "recent_releases.json"
    _set_argv(monkeypatch, output_path, "--limit", "1")

    fetch_recent_releases.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written == [{"tag_name": "v2.1.0", "body": "Fixed a bug in the parser", "richness": "detailed"}]


def test_main_skips_the_fetch_when_the_existing_output_is_within_the_freshness_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output_path = tmp_path / "recent_releases.json"
    existing = [{"tag_name": "v1.0.0", "body": "", "richness": "none"}]
    output_path.write_text(json.dumps(existing), encoding="utf-8")
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _must_not_be_called)
    _set_argv(monkeypatch, output_path)

    fetch_recent_releases.main()

    out = capsys.readouterr().out
    assert "skip" in out.lower()
    assert REPOSITORY in out
    # The file on disk is untouched - the skip returns before any write.
    assert json.loads(output_path.read_text(encoding="utf-8")) == existing


def test_main_fetches_again_when_the_existing_output_is_older_than_the_freshness_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    output_path = tmp_path / "recent_releases.json"
    output_path.write_text(
        json.dumps([{"tag_name": "v1.0.0", "body": "", "richness": "none"}]), encoding="utf-8"
    )
    stale_time = time.time() - fetch_recent_releases._SIDECAR_FRESHNESS_SECONDS - 1
    os.utime(output_path, (stale_time, stale_time))
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fake_fetch_releases(calls))
    _set_argv(monkeypatch, output_path)

    fetch_recent_releases.main()

    assert calls == [REPOSITORY]
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written[0]["tag_name"] == "v2.1.0"


def test_main_treats_a_malformed_existing_output_as_absent_and_fetches_live_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    output_path = tmp_path / "recent_releases.json"
    output_path.write_text("{this is not valid json at all", encoding="utf-8")
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fake_fetch_releases(calls))
    _set_argv(monkeypatch, output_path)

    fetch_recent_releases.main()

    assert calls == [REPOSITORY]
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written[0]["tag_name"] == "v2.1.0"


def test_main_writes_an_empty_sidecar_and_does_not_raise_when_fetch_releases_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _fetch_that_fails(repository: str, **kwargs: object) -> list[Release]:
        raise RuntimeError("boom: simulated GitHub failure")

    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fetch_that_fails)
    output_path = tmp_path / "recent_releases.json"
    _set_argv(monkeypatch, output_path)

    fetch_recent_releases.main()  # must not raise

    assert json.loads(output_path.read_text(encoding="utf-8")) == []
    out = capsys.readouterr().out
    assert REPOSITORY in out
    assert "failed" in out.lower()
    assert "boom: simulated GitHub failure" in out


def test_load_recent_releases_sidecar_round_trips_what_main_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fake_fetch_releases(calls))
    output_path = tmp_path / "recent_releases.json"
    _set_argv(monkeypatch, output_path)

    fetch_recent_releases.main()

    loaded = fetch_recent_releases.load_recent_releases_sidecar(output_path)
    assert loaded == _RELEASES


def test_load_recent_releases_sidecar_returns_none_when_the_file_is_absent(tmp_path: Path) -> None:
    assert fetch_recent_releases.load_recent_releases_sidecar(tmp_path / "missing.json") is None


def test_load_recent_releases_sidecar_raises_on_a_present_but_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "recent_releases.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        fetch_recent_releases.load_recent_releases_sidecar(path)


def test_load_recent_releases_sidecar_raises_when_the_json_is_not_a_list(tmp_path: Path) -> None:
    path = tmp_path / "recent_releases.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError):
        fetch_recent_releases.load_recent_releases_sidecar(path)


def test_sidecar_name_rejects_an_uppercase_identity_part() -> None:
    with pytest.raises(ValueError):
        fetch_recent_releases.recent_releases_sidecar_name("PDF", "net")


def test_sidecar_name_matches_the_expected_shape() -> None:
    assert fetch_recent_releases.recent_releases_sidecar_name("pdf", "net") == "recent_releases_pdf_net.json"
