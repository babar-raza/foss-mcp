"""A failed release fetch must never be written as an empty "fresh" list (G3/TC-388).

Before this card ``infra/fetch_recent_releases.py`` wrote ``[]`` on ANY exception and then treated
that well-formed list as fresh for 24 hours, so a rate limit became "this product has no releases"
and was baked into the image. Fully offline: ``fetch_releases`` is monkeypatched (never the raw
HTTP layer), mtimes are set explicitly with ``os.utime``, nothing sleeps, and nothing compares
against a wall-clock threshold.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import infra.fetch_recent_releases as fetch_recent_releases
from foss_mcp.extraction import github_release_reader
from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.mcp.tools.list_recent_changes import list_recent_changes

REPOSITORY = "aspose-slides-foss/Aspose.Slides-FOSS-for-.NET"

_GOOD_SIDECAR = [{"tag_name": "v1.0.0", "body": "Fixed a parser bug in `read()`", "richness": "detailed"}]


class _SimulatedRateLimit(Exception):
    pass


def _set_argv(monkeypatch: pytest.MonkeyPatch, output: Path, *extra: str) -> None:
    argv = ["fetch_recent_releases.py", "--repository", REPOSITORY, "--output", str(output), *extra]
    monkeypatch.setattr(sys, "argv", argv)


def _failing_fetch(message: str = "HTTP Error 403: rate limit exceeded"):
    def _fetch(repository: str, **kwargs: object) -> list[Release]:
        raise _SimulatedRateLimit(message)

    return _fetch


def _recording_fetch(calls: list[str], releases: list[Release] | None = None):
    result = releases if releases is not None else [Release("v9.0.0", "Added `thing`", "detailed")]

    def _fetch(repository: str, **kwargs: object) -> list[Release]:
        calls.append(repository)
        return list(result)

    return _fetch


def _write_sidecar(path: Path, payload: object, *, mtime: float) -> bytes:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path.read_bytes()


def test_failure_with_a_good_existing_sidecar_leaves_its_bytes_unchanged_and_writes_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "recent_releases.json"
    # Make the sidecar stale so main reaches the fetch (the failure path is what is under test).
    before = _write_sidecar(output, _GOOD_SIDECAR, mtime=1.0)
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _failing_fetch())
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert output.read_bytes() == before
    assert fetch_recent_releases._fetch_failed_marker(output).is_file()


def test_failure_with_no_sidecar_writes_an_empty_list_and_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "recent_releases.json"
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _failing_fetch())
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert json.loads(output.read_text(encoding="utf-8")) == []
    assert fetch_recent_releases._fetch_failed_marker(output).is_file()


def test_failure_over_a_malformed_sidecar_writes_an_empty_list_and_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "recent_releases.json"
    output.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _failing_fetch())
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert json.loads(output.read_text(encoding="utf-8")) == []
    assert fetch_recent_releases._fetch_failed_marker(output).is_file()


def test_marker_path_is_the_output_path_plus_a_failed_suffix(tmp_path: Path) -> None:
    output = tmp_path / "recent_releases_slides_net.json"
    marker = fetch_recent_releases._fetch_failed_marker(output)
    assert marker.parent == output.parent
    assert marker.name == "recent_releases_slides_net.json.failed"


def test_a_fresh_looking_sidecar_is_not_fresh_while_the_marker_exists(tmp_path: Path) -> None:
    output = tmp_path / "recent_releases.json"
    output.write_text(json.dumps(_GOOD_SIDECAR), encoding="utf-8")
    # Control: with no marker, a just-written sidecar IS fresh.
    assert fetch_recent_releases._sidecar_is_fresh(output) is True

    fetch_recent_releases._fetch_failed_marker(output).write_text(
        "X 2026-01-01T00:00:00Z\n", encoding="utf-8"
    )

    assert fetch_recent_releases._sidecar_is_fresh(output) is False


def test_the_next_run_refetches_when_the_marker_is_present_even_though_the_sidecar_looks_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "recent_releases.json"
    output.write_text(json.dumps([]), encoding="utf-8")  # a just-written, fresh-looking []
    fetch_recent_releases._fetch_failed_marker(output).write_text(
        "X 2026-01-01T00:00:00Z\n", encoding="utf-8"
    )
    calls: list[str] = []
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _recording_fetch(calls))
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert calls == [REPOSITORY]
    assert json.loads(output.read_text(encoding="utf-8"))[0]["tag_name"] == "v9.0.0"


def test_a_failed_run_followed_by_another_run_refetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "recent_releases.json"
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _failing_fetch())
    _set_argv(monkeypatch, output)
    fetch_recent_releases.main()  # fails: writes [] + marker, both brand new (so "fresh-looking")
    calls: list[str] = []
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _recording_fetch(calls))

    fetch_recent_releases.main()

    assert calls == [REPOSITORY]


def test_success_deletes_the_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "recent_releases.json"
    marker = fetch_recent_releases._fetch_failed_marker(output)
    marker.write_text("X 2026-01-01T00:00:00Z\n", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _recording_fetch(calls))
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert calls == [REPOSITORY]
    assert not marker.exists()
    assert fetch_recent_releases._sidecar_is_fresh(output) is True


def test_success_without_a_marker_does_not_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "recent_releases.json"
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _recording_fetch([]))
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert not fetch_recent_releases._fetch_failed_marker(output).exists()


def test_force_refetches_a_fresh_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "recent_releases.json"
    output.write_text(json.dumps(_GOOD_SIDECAR), encoding="utf-8")
    assert fetch_recent_releases._sidecar_is_fresh(output) is True
    calls: list[str] = []
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _recording_fetch(calls))
    _set_argv(monkeypatch, output, "--force")

    fetch_recent_releases.main()

    assert calls == [REPOSITORY]
    assert json.loads(output.read_text(encoding="utf-8"))[0]["tag_name"] == "v9.0.0"


def test_without_force_a_fresh_sidecar_is_still_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "recent_releases.json"
    output.write_text(json.dumps(_GOOD_SIDECAR), encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _recording_fetch(calls))
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    assert calls == []


def test_the_marker_holds_the_exception_class_name_and_a_timestamp_but_no_message_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_message = "HTTP Error 403 at https://api.github.com/repos/x/y/releases token ghp_SECRETSECRET123"
    output = tmp_path / "recent_releases.json"
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _failing_fetch(secret_message))
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    text = fetch_recent_releases._fetch_failed_marker(output).read_text(encoding="utf-8")
    parts = text.split()
    assert parts[0] == "_SimulatedRateLimit"
    assert len(parts) == 2
    assert parts[1].endswith("Z") and "T" in parts[1]
    for fragment in ("403", "https", "api.github.com", "ghp_", "SECRET", "rate"):
        assert fragment not in text


def test_the_printed_failure_line_names_the_class_and_never_leaks_a_url_or_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_message = "HTTP Error 403 at https://api.github.com/repos/x/y/releases token ghp_SECRETSECRET123"
    output = tmp_path / "recent_releases.json"
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _failing_fetch(secret_message))
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    out = capsys.readouterr().out
    assert "failed" in out.lower()
    assert "_SimulatedRateLimit" in out
    assert REPOSITORY in out
    assert "https://" not in out
    assert "ghp_" not in out
    assert "SECRETSECRET" not in out


def test_published_at_and_url_round_trip_through_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Release(
        "v3.0.0",
        "Added `x`",
        "detailed",
        published_at="2026-09-01T12:00:00Z",
        url="https://github.com/o/r/releases/tag/v3.0.0",
    )
    output = tmp_path / "recent_releases.json"
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _recording_fetch([], [release]))
    _set_argv(monkeypatch, output)

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written[0]["published_at"] == "2026-09-01T12:00:00Z"
    assert written[0]["url"] == "https://github.com/o/r/releases/tag/v3.0.0"
    assert fetch_recent_releases.load_recent_releases_sidecar(output) == (release,)


def test_serialisation_omits_the_two_keys_when_they_are_none() -> None:
    serialised = fetch_recent_releases._serialize_release(Release("v1", "", "none"))
    assert serialised == {"tag_name": "v1", "body": "", "richness": "none"}


def test_an_old_sidecar_without_the_new_keys_loads_with_none(tmp_path: Path) -> None:
    output = tmp_path / "recent_releases.json"
    output.write_text(json.dumps(_GOOD_SIDECAR), encoding="utf-8")

    loaded = fetch_recent_releases.load_recent_releases_sidecar(output)

    assert loaded is not None
    assert loaded[0].tag_name == "v1.0.0"
    assert loaded[0].published_at is None
    assert loaded[0].url is None


def test_list_recent_changes_carries_published_at_and_url() -> None:
    release = Release("v2", "Fixed `a`", "detailed", published_at="2026-08-01T00:00:00Z", url="https://x/y")
    bare = Release("v1", "", "none")

    entries = list_recent_changes([release, bare])

    assert entries[0].published_at == "2026-08-01T00:00:00Z"
    assert entries[0].url == "https://x/y"
    assert entries[1].published_at is None
    assert entries[1].url is None


def test_positional_release_construction_still_works() -> None:
    release = Release("v1.2.3", "body", "templated")
    assert (release.tag_name, release.body, release.richness) == ("v1.2.3", "body", "templated")
    assert release.published_at is None
    assert release.url is None


def test_fetch_releases_fills_published_at_and_url_from_the_api_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {
            "tag_name": "v2",
            "body": "Fixed `a`",
            "published_at": "2026-08-01T00:00:00Z",
            "html_url": "https://h/2",
        },
        {"tag_name": "v1", "body": None},
    ]

    def _fake_get_json(url: str, *, etag: str | None = None):
        return 200, payload, None

    monkeypatch.setattr(github_release_reader, "_get_json", _fake_get_json)

    releases = github_release_reader.fetch_releases("o/r")

    assert releases[0].published_at == "2026-08-01T00:00:00Z"
    assert releases[0].url == "https://h/2"
    assert releases[1].published_at is None
    assert releases[1].url is None
