"""The shared GitHub HTTP helper: token auth, a bounded rate-limit wait, and the reader wiring.

Offline by design. Every network call is replaced by a fake opener, and every wait is captured
by a fake sleep, so no test sleeps for real or touches the network.
"""

from __future__ import annotations

import base64
import email.message
import json
import logging
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

import pytest

from foss_mcp.extraction import github_http, manifest_reader, repo_native_reader
from foss_mcp.extraction import github_release_reader as release_reader
from foss_mcp.extraction.github_http import (
    GITHUB_TOKEN_ENV,
    rate_limit_wait_seconds,
    urlopen_with_backoff,
    with_auth,
)

_URL = "https://api.github.com/repos/owner/name/contents/README.md"
_NOW = 1_000_000.0
_SECRET = "ghp_TESTSECRETVALUE1234567890"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_READER_FILES = [
    "src/foss_mcp/extraction/manifest_reader.py",
    "src/foss_mcp/extraction/github_release_reader.py",
    "src/foss_mcp/extraction/repo_native_reader.py",
]


class _Response:
    """A minimal stand-in for the response urlopen returns."""

    def __init__(self, body: bytes = b"{}", status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(_URL, code, f"status {code}", headers or {}, None)


def _no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GITHUB_TOKEN_ENV, raising=False)


def _request() -> urllib.request.Request:
    return urllib.request.Request(_URL, headers={"Accept": "application/vnd.github+json"})


class _Recorder:
    """A fake sleep that records every requested delay instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class _Opener:
    """A fake urlopen that plays back a script: each item is a response or an exception to raise."""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, object]] = []

    def __call__(self, request: urllib.request.Request, timeout: float = 30) -> object:
        self.calls.append({"request": request, "timeout": timeout})
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


# with_auth ---------------------------------------------------------------------------------


def test_with_auth_adds_a_bearer_header_when_the_token_is_set(monkeypatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "abc123")
    headers = with_auth({"Accept": "application/vnd.github+json"})
    assert headers["Authorization"] == "Bearer abc123"
    assert headers["Accept"] == "application/vnd.github+json"


def test_with_auth_adds_no_header_when_the_token_is_unset(monkeypatch) -> None:
    _no_token(monkeypatch)
    assert with_auth({"Accept": "x"}) == {"Accept": "x"}


def test_with_auth_treats_an_empty_token_as_unset(monkeypatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "")
    assert "Authorization" not in with_auth({"Accept": "x"})


def test_with_auth_treats_a_whitespace_only_token_as_unset(monkeypatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "   ")
    assert "Authorization" not in with_auth({"Accept": "x"})


def test_with_auth_returns_a_copy_and_leaves_the_input_untouched(monkeypatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "abc123")
    original = {"Accept": "x"}
    result = with_auth(original)
    assert result is not original
    assert "Authorization" not in original


def test_the_token_env_var_name_is_github_token() -> None:
    assert GITHUB_TOKEN_ENV == "GITHUB_TOKEN"


# rate_limit_wait_seconds --------------------------------------------------------------------


def test_a_429_with_retry_after_waits_that_many_seconds() -> None:
    assert rate_limit_wait_seconds(429, {"Retry-After": "7"}, _NOW) == 7.0


def test_a_403_with_remaining_zero_waits_until_the_reset_time() -> None:
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW) + 30)}
    assert rate_limit_wait_seconds(403, headers, _NOW) == 30.0


def test_a_past_reset_time_gives_a_zero_wait_never_a_negative_one() -> None:
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW) - 90)}
    assert rate_limit_wait_seconds(403, headers, _NOW) == 0.0


def test_retry_after_is_preferred_over_the_reset_time() -> None:
    headers = {
        "Retry-After": "5",
        "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": str(int(_NOW) + 300),
    }
    assert rate_limit_wait_seconds(403, headers, _NOW) == 5.0


def test_retry_after_as_an_http_date_is_honoured() -> None:
    headers = {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
    # 2015-10-21 07:28:00 UTC relative to a "now" 42 seconds earlier.
    earlier = 1445412480.0 - 42.0
    assert rate_limit_wait_seconds(429, headers, earlier) == 42.0


def test_a_garbage_retry_after_falls_back_to_the_reset_time() -> None:
    headers = {"Retry-After": "soon", "x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW) + 12)}
    assert rate_limit_wait_seconds(403, headers, _NOW) == 12.0


def test_a_403_without_the_rate_limit_header_is_a_permission_error_not_retried() -> None:
    assert rate_limit_wait_seconds(403, {}, _NOW) is None


def test_a_403_with_remaining_nonzero_is_not_a_rate_limit() -> None:
    headers = {"x-ratelimit-remaining": "4", "x-ratelimit-reset": str(int(_NOW) + 30)}
    assert rate_limit_wait_seconds(403, headers, _NOW) is None


def test_a_500_is_not_retried_even_with_rate_limit_headers() -> None:
    headers = {"Retry-After": "3", "x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW) + 3)}
    assert rate_limit_wait_seconds(500, headers, _NOW) is None


def test_a_404_and_a_304_are_not_rate_limits() -> None:
    assert rate_limit_wait_seconds(404, {"Retry-After": "3"}, _NOW) is None
    assert rate_limit_wait_seconds(304, {}, _NOW) is None


def test_a_429_with_no_wait_hint_returns_none_rather_than_guessing() -> None:
    assert rate_limit_wait_seconds(429, {}, _NOW) is None


def test_the_rate_limit_header_is_matched_case_insensitively() -> None:
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(_NOW) + 9)}
    assert rate_limit_wait_seconds(403, headers, _NOW) == 9.0


def test_an_email_message_header_block_is_read_like_a_mapping() -> None:
    message = email.message.Message()
    message["X-RateLimit-Remaining"] = "0"
    message["X-RateLimit-Reset"] = str(int(_NOW) + 4)
    assert rate_limit_wait_seconds(403, message, _NOW) == 4.0


def test_missing_headers_object_is_not_a_rate_limit() -> None:
    assert rate_limit_wait_seconds(403, None, _NOW) is None


# urlopen_with_backoff -----------------------------------------------------------------------


def test_a_successful_first_attempt_returns_the_response_and_never_sleeps() -> None:
    response = _Response(b"ok")
    sleep = _Recorder()
    opener = _Opener([response])
    result = urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert result is response
    assert sleep.delays == []
    assert len(opener.calls) == 1


def test_the_timeout_is_passed_through_to_the_opener() -> None:
    opener = _Opener([_Response()])
    urlopen_with_backoff(_request(), timeout=12, sleep=_Recorder(), now=lambda: _NOW, opener=opener)
    assert opener.calls[0]["timeout"] == 12


def test_one_rate_limit_answer_is_waited_out_then_the_retry_succeeds() -> None:
    response = _Response(b"ok")
    sleep = _Recorder()
    opener = _Opener([_http_error(429, {"Retry-After": "10"}), response])
    result = urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert result is response
    assert sleep.delays == [11.0]  # the 10-second wait plus one second of margin
    assert len(opener.calls) == 2


def test_a_403_rate_limit_reset_is_waited_out_then_the_retry_succeeds() -> None:
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW) + 20)}
    response = _Response(b"ok")
    sleep = _Recorder()
    opener = _Opener([_http_error(403, headers), response])
    result = urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert result is response
    assert sleep.delays == [21.0]


def test_a_wait_that_exceeds_the_budget_re_raises_the_original_error_without_sleeping() -> None:
    error = _http_error(429, {"Retry-After": "3600"})
    sleep = _Recorder()
    opener = _Opener([error])
    with pytest.raises(urllib.error.HTTPError) as caught:
        urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert caught.value is error
    assert sleep.delays == []
    assert len(opener.calls) == 1


def test_the_total_wait_budget_is_cumulative_across_attempts() -> None:
    first = _http_error(429, {"Retry-After": "50"})
    second = _http_error(429, {"Retry-After": "50"})
    sleep = _Recorder()
    opener = _Opener([first, second, _Response()])
    with pytest.raises(urllib.error.HTTPError) as caught:
        urlopen_with_backoff(_request(), max_total_wait=60, sleep=sleep, now=lambda: _NOW, opener=opener)
    # The first wait (51s) fits the 60s budget; the second would bring the total to 102s and is refused.
    assert caught.value is second
    assert sleep.delays == [51.0]


def test_after_max_attempts_the_last_rate_limit_error_is_raised() -> None:
    errors = [_http_error(429, {"Retry-After": "1"}) for _ in range(3)]
    sleep = _Recorder()
    opener = _Opener(list(errors))
    with pytest.raises(urllib.error.HTTPError) as caught:
        urlopen_with_backoff(_request(), max_attempts=3, sleep=sleep, now=lambda: _NOW, opener=opener)
    assert caught.value is errors[-1]
    assert len(opener.calls) == 3
    assert sleep.delays == [2.0, 2.0]


def test_a_404_reaches_the_caller_on_the_first_attempt_without_sleeping() -> None:
    error = _http_error(404)
    sleep = _Recorder()
    opener = _Opener([error])
    with pytest.raises(urllib.error.HTTPError) as caught:
        urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert caught.value is error
    assert caught.value.code == 404
    assert sleep.delays == []
    assert len(opener.calls) == 1


def test_a_304_reaches_the_caller_on_the_first_attempt_without_sleeping() -> None:
    error = _http_error(304)
    sleep = _Recorder()
    opener = _Opener([error])
    with pytest.raises(urllib.error.HTTPError) as caught:
        urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert caught.value is error
    assert caught.value.code == 304
    assert sleep.delays == []
    assert len(opener.calls) == 1


def test_a_403_without_the_rate_limit_header_is_raised_at_once() -> None:
    error = _http_error(403)
    sleep = _Recorder()
    opener = _Opener([error])
    with pytest.raises(urllib.error.HTTPError) as caught:
        urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert caught.value is error
    assert sleep.delays == []


def test_a_500_is_raised_at_once() -> None:
    error = _http_error(500)
    sleep = _Recorder()
    opener = _Opener([error])
    with pytest.raises(urllib.error.HTTPError) as caught:
        urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert caught.value is error
    assert sleep.delays == []


def test_a_non_http_error_is_not_caught() -> None:
    sleep = _Recorder()
    opener = _Opener([urllib.error.URLError("no route")])
    with pytest.raises(urllib.error.URLError):
        urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener)
    assert sleep.delays == []


def test_the_default_opener_is_looked_up_at_call_time(monkeypatch) -> None:
    response = _Response(b"patched")
    calls: list[urllib.request.Request] = []

    def fake_urlopen(request, timeout=30):
        calls.append(request)
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = urlopen_with_backoff(_request(), sleep=_Recorder(), now=lambda: _NOW)
    assert result is response
    assert len(calls) == 1


# The token is never leaked ------------------------------------------------------------------


def test_the_token_never_appears_in_a_raised_error_or_a_captured_log(monkeypatch, caplog, capsys) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, _SECRET)
    caplog.set_level(logging.DEBUG)
    request = urllib.request.Request(_URL, headers=with_auth({"Accept": "x"}))
    # A permission error, and a rate limit that cannot be waited out: neither may echo the token.
    permission = _Opener([_http_error(403)])
    with pytest.raises(urllib.error.HTTPError) as permission_caught:
        urlopen_with_backoff(request, sleep=_Recorder(), now=lambda: _NOW, opener=permission)
    exhausted = _Opener([_http_error(429, {"Retry-After": "9999"})])
    with pytest.raises(urllib.error.HTTPError) as exhausted_caught:
        urlopen_with_backoff(request, sleep=_Recorder(), now=lambda: _NOW, opener=exhausted)
    for caught in (permission_caught, exhausted_caught):
        assert _SECRET not in str(caught.value)
        assert _SECRET not in repr(caught.value)
    captured = capsys.readouterr()
    assert _SECRET not in captured.out + captured.err
    assert _SECRET not in caplog.text


# Reader wiring -------------------------------------------------------------------------------


@pytest.mark.parametrize("relative", _READER_FILES)
def test_each_github_reader_routes_its_requests_through_the_shared_helper(relative: str) -> None:
    source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
    assert "github_http" in source
    assert "urlopen_with_backoff" in source
    assert "with_auth" in source


def _no_real_wait(monkeypatch, module) -> _Recorder:
    """Route a reader's helper call through a recording sleep, so a retry costs no real time."""
    sleep = _Recorder()
    monkeypatch.setattr(
        module,
        "urlopen_with_backoff",
        partial(github_http.urlopen_with_backoff, sleep=sleep, now=lambda: _NOW),
    )
    return sleep


def test_read_repo_document_sends_the_token_when_set(monkeypatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, _SECRET)
    seen: list[str | None] = []
    payload = json.dumps({"sha": "abc", "size": 2, "content": base64.b64encode(b"hi").decode()}).encode()

    def fake_urlopen(request, timeout=30):
        seen.append(request.get_header("Authorization"))
        return _Response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = repo_native_reader.read_repo_document("owner/name", "README.md")
    assert seen == [f"Bearer {_SECRET}"]
    assert result.content == "hi"


def test_read_repo_document_retries_a_rate_limit_and_then_reads_the_document(monkeypatch) -> None:
    _no_token(monkeypatch)
    sleep = _no_real_wait(monkeypatch, repo_native_reader)
    payload = json.dumps({"sha": "abc", "size": 2, "content": base64.b64encode(b"hi").decode()}).encode()
    script = [
        _http_error(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW) + 5)}),
        _Response(payload),
    ]

    def fake_urlopen(request, timeout=30):
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = repo_native_reader.read_repo_document("owner/name", "README.md")
    assert result.content == "hi"
    assert sleep.delays == [6.0]


def test_read_repo_document_still_reports_a_404_as_not_present(monkeypatch) -> None:
    _no_token(monkeypatch)
    sleep = _no_real_wait(monkeypatch, repo_native_reader)

    def fake_urlopen(request, timeout=30):
        raise _http_error(404)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = repo_native_reader.read_repo_document("owner/name", "CONTRIBUTING.md")
    assert result == repo_native_reader.DocumentNotPresent(path="CONTRIBUTING.md")
    assert sleep.delays == []


def test_fetch_manifest_file_retries_a_rate_limit_and_returns_the_text(monkeypatch) -> None:
    _no_token(monkeypatch)
    sleep = _no_real_wait(monkeypatch, manifest_reader)
    payload = json.dumps({"content": base64.b64encode(b"[package]\n").decode()}).encode()
    script = [_http_error(429, {"Retry-After": "3"}), _Response(payload)]

    def fake_urlopen(request, timeout=30):
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert manifest_reader.fetch_manifest_file("owner/name", "Cargo.toml") == "[package]\n"
    assert sleep.delays == [4.0]


def test_fetch_manifest_file_passes_a_304_through_to_the_caller(monkeypatch) -> None:
    _no_token(monkeypatch)
    sleep = _no_real_wait(monkeypatch, manifest_reader)

    def fake_urlopen(request, timeout=30):
        raise _http_error(304)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError) as caught:
        manifest_reader.fetch_manifest_file("owner/name", "Cargo.toml", etag='"v1"')
    assert caught.value.code == 304
    assert sleep.delays == []


def test_get_json_treats_a_304_as_unchanged_through_the_helper(monkeypatch) -> None:
    _no_token(monkeypatch)
    sleep = _no_real_wait(monkeypatch, release_reader)

    def fake_urlopen(request, timeout=30):
        raise _http_error(304)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, body, etag = release_reader._get_json("https://api.github.com/x", etag='"v1"')
    assert (status, body, etag) == (304, None, '"v1"')
    assert sleep.delays == []


def test_get_json_sends_the_token_and_decodes_the_body(monkeypatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, _SECRET)
    seen: list[str | None] = []

    def fake_urlopen(request, timeout=30):
        seen.append(request.get_header("Authorization"))
        return _Response(b'[{"tag_name": "v1"}]', headers={"ETag": '"e1"'})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, body, etag = release_reader._get_json("https://api.github.com/x")
    assert seen == [f"Bearer {_SECRET}"]
    assert (status, body, etag) == (200, [{"tag_name": "v1"}], '"e1"')
