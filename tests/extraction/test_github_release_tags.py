"""``fetch_tags``: tag-only entries for a repository that publishes no GitHub Releases (G3/TC-519).

Fully offline: ``urllib.request.urlopen`` is replaced by a fake that counts its requests.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from foss_mcp.extraction.github_http import GITHUB_TOKEN_ENV, reset_default_budget
from foss_mcp.extraction.github_release_reader import Release, fetch_tags

_SHA_ONE = "0123456789abcdef0123456789abcdef01234567"
_SHA_TWO = "fedcba9876543210fedcba9876543210fedcba98"


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = 200
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeUrlopen:
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


def test_fetch_tags_parses_names_and_shas(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"name": "v2.0.0", "commit": {"sha": _SHA_ONE, "url": "https://api.github.com/x"}},
        {"name": "v1.0.0", "commit": {"sha": _SHA_TWO}},
    ]
    _install(monkeypatch, [_Response(payload)])

    tags = fetch_tags("o/r")

    assert [tag.tag_name for tag in tags] == ["v2.0.0", "v1.0.0"]
    assert [tag.target_commit for tag in tags] == [_SHA_ONE, _SHA_TWO]
    assert all(isinstance(tag, Release) for tag in tags)


def test_a_tag_entry_has_no_notes_and_is_marked_as_a_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_Response([{"name": "v1", "commit": {"sha": _SHA_ONE}}])])

    (tag,) = fetch_tags("o/r")

    assert tag.source == "tag"
    assert tag.body == ""
    assert tag.richness == "none"
    assert tag.published_at is None


def test_fetch_tags_builds_the_tag_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_Response([{"name": "v1.2.3", "commit": {"sha": _SHA_ONE}}])])

    (tag,) = fetch_tags("owner/name")

    assert tag.url == "https://github.com/owner/name/releases/tag/v1.2.3"


def test_fetch_tags_skips_a_nameless_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"commit": {"sha": _SHA_ONE}},
        {"name": "", "commit": {"sha": _SHA_ONE}},
        {"name": "kept", "commit": {"sha": _SHA_TWO}},
    ]
    _install(monkeypatch, [_Response(payload)])

    tags = fetch_tags("o/r")

    assert [tag.tag_name for tag in tags] == ["kept"]


def test_fetch_tags_makes_exactly_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # A full page is returned, yet no second page is requested: one request, newest tag first.
    payload = [{"name": f"v{index}", "commit": {"sha": _SHA_ONE}} for index in range(100)]
    fake = _install(monkeypatch, [_Response(payload)])

    tags = fetch_tags("o/r")

    assert len(tags) == 100
    assert len(fake.requests) == 1
    assert fake.requests[0].full_url == "https://api.github.com/repos/o/r/tags?per_page=100"


def test_fetch_tags_honours_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, [_Response([])])

    assert fetch_tags("o/r", per_page=5) == []
    assert fake.requests[0].full_url.endswith("/repos/o/r/tags?per_page=5")


def test_a_tag_without_a_commit_keeps_no_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_Response([{"name": "v1"}])])

    (tag,) = fetch_tags("o/r")

    assert tag.target_commitish is None
    assert tag.target_commit is None


def test_a_failing_request_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError("https://api.github.com/x", 404, "Not Found", {}, None)  # type: ignore[arg-type]
    _install(monkeypatch, [error])

    with pytest.raises(urllib.error.HTTPError):
        fetch_tags("o/r")
