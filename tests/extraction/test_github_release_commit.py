"""Per-release commit information and the ETag plumbing of the releases reader (G3/TC-511).

GitHub's list-releases answer carries ``target_commitish`` (a branch name or a 40-hex sha) and
``created_at`` with no extra call; both are kept on ``Release``. Fully offline: ``urllib.request.urlopen``
is replaced by a fake that counts its calls, and nothing sleeps.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import infra.fetch_recent_releases as fetch_recent_releases
from foss_mcp.extraction.github_http import GITHUB_TOKEN_ENV, reset_default_budget
from foss_mcp.extraction.github_release_reader import Release, fetch_releases

_SHA = "0123456789abcdef0123456789abcdef01234567"


class _Response:
    def __init__(self, payload: object, headers: dict[str, str] | None = None) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = 200
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeUrlopen:
    """A replacement for ``urllib.request.urlopen`` that plays back a script and records requests."""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float = 30) -> object:
        self.requests.append(request)
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GITHUB_TOKEN_ENV, raising=False)
    reset_default_budget()


def _install(monkeypatch: pytest.MonkeyPatch, script: list[object]) -> _FakeUrlopen:
    fake = _FakeUrlopen(script)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def _entry(tag: str, **extra: object) -> dict[str, object]:
    return {"tag_name": tag, "body": "Fixed `thing` in 3 places", **extra}


def test_release_keeps_target_commitish_and_created_at() -> None:
    release = Release("v1", "b", "none", target_commitish="main", created_at="2026-01-02T03:04:05Z")
    assert release.target_commitish == "main"
    assert release.created_at == "2026-01-02T03:04:05Z"
    plain = Release("v1", "b", "none")
    assert plain.target_commitish is None and plain.created_at is None


def test_target_commit_is_only_a_forty_hex_sha() -> None:
    assert Release("v1", "b", "none", target_commitish=_SHA).target_commit == _SHA
    for not_a_sha in (_SHA.upper(), _SHA[:-1], _SHA + "0", "g" * 40, "", None):
        assert Release("v1", "b", "none", target_commitish=not_a_sha).target_commit is None


def test_a_branch_name_is_not_a_commit() -> None:
    for branch in ("main", "master", "release/1.0", "deadbeef"):
        assert Release("v1", "b", "none", target_commitish=branch).target_commit is None


def test_serialisation_omits_the_new_keys_when_none() -> None:
    bare = fetch_recent_releases._serialize_release(Release("v1", "b", "none"))
    assert "target_commitish" not in bare and "created_at" not in bare
    full = fetch_recent_releases._serialize_release(
        Release("v1", "b", "none", target_commitish=_SHA, created_at="2026-01-02T03:04:05Z")
    )
    assert full["target_commitish"] == _SHA
    assert full["created_at"] == "2026-01-02T03:04:05Z"
    roundtrip = fetch_recent_releases._deserialize_release(full)
    assert roundtrip == Release("v1", "b", "none", target_commitish=_SHA, created_at="2026-01-02T03:04:05Z")


def test_an_old_sidecar_entry_loads_with_none() -> None:
    old = {"tag_name": "v1", "body": "b", "richness": "detailed", "published_at": "2026-01-01T00:00:00Z"}
    release = fetch_recent_releases._deserialize_release(old)
    assert release.target_commitish is None
    assert release.created_at is None
    assert release.target_commit is None


def test_fetch_releases_fills_the_fields_from_the_list_answer_without_an_extra_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        _entry("v2", target_commitish=_SHA, created_at="2026-02-02T00:00:00Z"),
        _entry("v1", target_commitish="main", created_at=""),
        _entry("v0"),
    ]
    fake = _install(monkeypatch, [_Response(payload)])
    releases = fetch_releases("owner/name")
    assert len(fake.requests) == 1
    assert [r.tag_name for r in releases] == ["v2", "v1", "v0"]
    assert releases[0].target_commit == _SHA
    assert releases[0].created_at == "2026-02-02T00:00:00Z"
    assert releases[1].target_commitish == "main" and releases[1].target_commit is None
    assert releases[1].created_at is None  # an empty string is None
    assert releases[2].target_commitish is None and releases[2].created_at is None


def test_fetch_releases_meta_receives_the_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, [_Response([_entry("v1")], headers={"ETag": '"abc"'})])
    meta: dict[str, object] = {}
    releases = fetch_releases("owner/name", meta=meta)
    assert [r.tag_name for r in releases] == ["v1"]
    assert meta["etag"] == '"abc"'
    assert "not_modified" not in meta
    assert fake.requests[0].get_header("If-none-match") is None


def test_fetch_releases_meta_flags_a_304(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError("https://api.github.com/x", 304, "Not Modified", {}, None)  # type: ignore[arg-type]
    fake = _install(monkeypatch, [error])
    meta: dict[str, object] = {}
    releases = fetch_releases("owner/name", etag='"abc"', meta=meta)
    assert releases == []
    assert meta["not_modified"] is True
    assert meta["etag"] == '"abc"'
    assert fake.requests[0].get_header("If-none-match") == '"abc"'


def test_fetch_releases_without_meta_behaves_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_Response([_entry("v1")])])
    assert [r.tag_name for r in fetch_releases("owner/name")] == ["v1"]
