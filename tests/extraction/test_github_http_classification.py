"""Failure classification, backoff and the proactive stop of the shared GitHub helper (G3/TC-511).

Fully offline and deterministic: every network call is an injected opener, every sleep, jitter and
clock is injected (or a monkeypatched module-level default), and the token is a fake value. Nothing
sleeps for real and nothing opens a socket.
"""

from __future__ import annotations

import inspect
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable

import pytest

from foss_mcp.extraction import github_http
from foss_mcp.extraction.github_http import (
    DEFAULT_REMAINING_FLOOR,
    GITHUB_TOKEN_ENV,
    GitHubAuthError,
    RateBudget,
    RateLimited,
    failure_reason,
    rate_limit_wait_seconds,
    reset_default_budget,
    urlopen_with_backoff,
    with_auth,
)

_URL = "https://api.github.com/repos/owner/name/releases"
_NOW = 1_000_000.0
_FAKE_TOKEN = "ghp_FAKE0000000000000000000000000000000"


class _Response:
    def __init__(self, body: bytes = b"{}", headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.status = 200
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body


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
        self.calls = 0

    def __call__(self, request: urllib.request.Request, timeout: float = 30) -> object:
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(_URL, code, f"status {code}", headers or {}, None)  # type: ignore[arg-type]


def _request() -> urllib.request.Request:
    return urllib.request.Request(_URL, headers={"Accept": "application/vnd.github+json"})


@pytest.fixture(autouse=True)
def _clean_default_budget() -> object:
    reset_default_budget()
    yield
    reset_default_budget()


def _call(opener: _Opener, sleep: _Recorder, **kwargs: object) -> object:
    """``urlopen_with_backoff`` with a private budget, a fixed clock and a zero jitter unless overridden."""
    options: dict[str, object] = {"budget": RateBudget(), "jitter": lambda: 0.0}
    options.update(kwargs)
    return urlopen_with_backoff(_request(), sleep=sleep, now=lambda: _NOW, opener=opener, **options)  # type: ignore[arg-type]


# 401 -----------------------------------------------------------------------------------------


def test_401_raises_github_auth_error_once_with_no_sleep() -> None:
    error = _http_error(401)
    sleep = _Recorder()
    opener = _Opener([error, _Response()])
    with pytest.raises(GitHubAuthError) as caught:
        _call(opener, sleep)
    assert isinstance(caught.value, urllib.error.HTTPError)
    assert caught.value.code == 401
    assert caught.value.__cause__ is error
    assert opener.calls == 1
    assert sleep.delays == []


def test_401_error_never_contains_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, _FAKE_TOKEN)
    request = urllib.request.Request(_URL, headers=with_auth({"Accept": "x"}))
    assert request.get_header("Authorization") == f"Bearer {_FAKE_TOKEN}"
    opener = _Opener([_http_error(401)])
    with pytest.raises(GitHubAuthError) as caught:
        urlopen_with_backoff(request, sleep=_Recorder(), now=lambda: _NOW, opener=opener, budget=RateBudget())
    for text in (
        str(caught.value),
        repr(caught.value),
        str(caught.value.__cause__),
        repr(caught.value.__cause__),
    ):
        assert _FAKE_TOKEN not in text
        assert "Bearer" not in text
    assert _FAKE_TOKEN not in repr(caught.value.args)


# rate limits ---------------------------------------------------------------------------------


def test_403_remaining_zero_with_a_short_reset_waits_then_succeeds() -> None:
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(_NOW) + 30)}
    response = _Response()
    sleep = _Recorder()
    opener = _Opener([_http_error(403, headers), response])
    assert _call(opener, sleep) is response
    assert sleep.delays == [31.0]
    assert opener.calls == 2


def test_403_remaining_zero_with_a_reset_over_two_minutes_raises_rate_limited_with_the_reset_time() -> None:
    reset = int(_NOW) + 3600
    error = _http_error(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)})
    sleep = _Recorder()
    opener = _Opener([error])
    with pytest.raises(RateLimited) as caught:
        _call(opener, sleep)
    assert caught.value.reset_at == float(reset)
    assert caught.value.wait_seconds == 3600.0
    assert caught.value.proactive is False
    assert sleep.delays == []
    assert opener.calls == 1


def test_rate_limited_is_an_http_error_and_keeps_the_original_as_its_cause() -> None:
    error = _http_error(429, {"Retry-After": "9999"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        _call(_Opener([error]), _Recorder())
    limited = caught.value
    assert isinstance(limited, RateLimited)
    assert limited.code == 429
    assert limited.url == _URL
    assert limited.__cause__ is error
    assert limited.reset_at is None
    assert limited.wait_seconds == 9999.0


def test_secondary_403_waits_the_retry_after_then_succeeds() -> None:
    headers = {"Retry-After": "7", "x-ratelimit-remaining": "57"}
    response = _Response()
    sleep = _Recorder()
    opener = _Opener([_http_error(403, headers), response])
    assert _call(opener, sleep) is response
    assert sleep.delays == [8.0]


def test_secondary_403_with_a_long_retry_after_raises_rate_limited() -> None:
    error = _http_error(403, {"Retry-After": "600", "x-ratelimit-remaining": "57"})
    sleep = _Recorder()
    with pytest.raises(RateLimited) as caught:
        _call(_Opener([error]), sleep)
    assert caught.value.wait_seconds == 600.0
    assert caught.value.__cause__ is error
    assert sleep.delays == []


def test_plain_403_and_hintless_429_are_raised_unchanged() -> None:
    for error in (_http_error(403), _http_error(429), _http_error(403, {"x-ratelimit-remaining": "4"})):
        sleep = _Recorder()
        opener = _Opener([error])
        with pytest.raises(urllib.error.HTTPError) as caught:
            _call(opener, sleep)
        assert caught.value is error
        assert not isinstance(caught.value, RateLimited)
        assert sleep.delays == []
        assert opener.calls == 1


def test_secondary_limit_detection_in_rate_limit_wait_seconds() -> None:
    # A 403 with Retry-After and a non-zero or absent remaining is a secondary limit that waits Retry-After.
    assert rate_limit_wait_seconds(403, {"Retry-After": "12"}, _NOW) == 12.0
    assert rate_limit_wait_seconds(403, {"Retry-After": "12", "x-ratelimit-remaining": "3"}, _NOW) == 12.0
    # It never falls back to the reset header: only Retry-After is a secondary-limit hint.
    assert (
        rate_limit_wait_seconds(403, {"Retry-After": "soon", "x-ratelimit-reset": str(int(_NOW) + 9)}, _NOW)
        is None
    )
    # The existing cases are unchanged.
    assert rate_limit_wait_seconds(403, {}, _NOW) is None
    assert rate_limit_wait_seconds(403, {"x-ratelimit-remaining": "3"}, _NOW) is None
    assert rate_limit_wait_seconds(403, {"x-ratelimit-remaining": "0", "Retry-After": "5"}, _NOW) == 5.0
    assert rate_limit_wait_seconds(429, {"Retry-After": "7"}, _NOW) == 7.0
    assert rate_limit_wait_seconds(500, {"Retry-After": "7"}, _NOW) is None


# 5xx and transport failures ------------------------------------------------------------------


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_5xx_is_retried_with_exponential_backoff_and_jitter(code: int) -> None:
    response = _Response()
    sleep = _Recorder()
    opener = _Opener([_http_error(code), _http_error(code), _http_error(code), response])
    assert _call(opener, sleep, jitter=lambda: 0.5) is response
    assert sleep.delays == [1.5, 2.5, 4.5]
    assert opener.calls == 4


def test_5xx_exhaustion_re_raises_the_last_error_unchanged() -> None:
    errors = [_http_error(503) for _ in range(4)]
    sleep = _Recorder()
    opener = _Opener(list(errors))
    with pytest.raises(urllib.error.HTTPError) as caught:
        _call(opener, sleep)
    assert caught.value is errors[-1]
    assert type(caught.value) is urllib.error.HTTPError
    assert opener.calls == 4
    assert sleep.delays == [1.0, 2.0, 4.0]


def test_backoff_delay_is_capped_at_thirty_seconds() -> None:
    sleep = _Recorder()
    opener = _Opener([_http_error(500) for _ in range(8)] + [_Response()])
    _call(opener, sleep, max_attempts=9, max_total_wait=1000.0)
    assert sleep.delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


def test_backoff_counts_against_the_total_wait_budget() -> None:
    errors = [_http_error(502) for _ in range(3)]
    sleep = _Recorder()
    opener = _Opener(list(errors) + [_Response()])
    with pytest.raises(urllib.error.HTTPError) as caught:
        _call(opener, sleep, max_total_wait=2.5)
    # The first delay (1.0) fits; the second would bring the total to 3.0 and is refused.
    assert caught.value is errors[1]
    assert sleep.delays == [1.0]
    assert opener.calls == 2


@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(lambda: TimeoutError(), id="timeout"),
        pytest.param(lambda: ConnectionResetError(), id="connection-reset"),
        pytest.param(lambda: urllib.error.URLError(TimeoutError()), id="urlerror-timeout"),
        pytest.param(lambda: urllib.error.URLError(socket.gaierror()), id="urlerror-gaierror"),
    ],
)
def test_transient_transport_failures_are_retried_then_succeed(
    make_error: Callable[[], BaseException],
) -> None:
    response = _Response()
    sleep = _Recorder()
    opener = _Opener([make_error(), response])
    assert _call(opener, sleep) is response
    assert sleep.delays == [1.0]


def test_a_transport_failure_that_never_recovers_re_raises_the_last_error() -> None:
    errors = [TimeoutError() for _ in range(4)]
    sleep = _Recorder()
    opener = _Opener(list(errors))
    with pytest.raises(TimeoutError) as caught:
        _call(opener, sleep)
    assert caught.value is errors[-1]
    assert sleep.delays == [1.0, 2.0, 4.0]


def test_a_tls_certificate_error_is_not_retried() -> None:
    error = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    sleep = _Recorder()
    opener = _Opener([error, _Response()])
    with pytest.raises(urllib.error.URLError) as caught:
        _call(opener, sleep)
    assert caught.value is error
    assert opener.calls == 1
    assert sleep.delays == []


@pytest.mark.parametrize("code", [404, 304, 422])
def test_404_304_and_422_are_raised_unchanged_with_no_sleep(code: int) -> None:
    error = _http_error(code)
    sleep = _Recorder()
    opener = _Opener([error, _Response()])
    with pytest.raises(urllib.error.HTTPError) as caught:
        _call(opener, sleep)
    assert caught.value is error
    assert caught.value.code == code
    assert opener.calls == 1
    assert sleep.delays == []


# the proactive stop --------------------------------------------------------------------------


def _headers(remaining: int, reset: int) -> dict[str, str]:
    return {"x-ratelimit-remaining": str(remaining), "x-ratelimit-reset": str(reset)}


def test_proactive_stop_raises_before_calling_the_opener_when_remaining_is_below_the_floor() -> None:
    budget = RateBudget()
    reset = int(_NOW) + 600
    opener = _Opener([_Response(headers=_headers(4, reset)), _Response()])
    assert _call(opener, _Recorder(), budget=budget) is not None
    assert opener.calls == 1
    with pytest.raises(RateLimited) as caught:
        _call(opener, _Recorder(), budget=budget)
    assert caught.value.proactive is True
    assert caught.value.reset_at == float(reset)
    assert caught.value.wait_seconds == 600.0
    assert caught.value.code == 429
    assert opener.calls == 1  # the opener was NOT called a second time


def test_remaining_equal_to_the_floor_does_not_stop() -> None:
    budget = RateBudget()
    opener = _Opener([_Response(headers=_headers(5, int(_NOW) + 600)), _Response(), _Response()])
    _call(opener, _Recorder(), budget=budget)
    _call(opener, _Recorder(), budget=budget)
    assert opener.calls == 2


def test_the_default_floor_is_five() -> None:
    assert DEFAULT_REMAINING_FLOOR == 5
    assert inspect.signature(urlopen_with_backoff).parameters["min_remaining"].default == 5
    # With the default floor, 4 remaining stops the next request and 5 does not.
    for remaining, stops in ((4, True), (5, False)):
        budget = RateBudget()
        opener = _Opener([_Response(headers=_headers(remaining, int(_NOW) + 600)), _Response()])
        urlopen_with_backoff(_request(), now=lambda: _NOW, opener=opener, budget=budget)
        if stops:
            with pytest.raises(RateLimited):
                urlopen_with_backoff(_request(), now=lambda: _NOW, opener=opener, budget=budget)
        else:
            urlopen_with_backoff(_request(), now=lambda: _NOW, opener=opener, budget=budget)


def test_a_past_reset_clears_the_proactive_state() -> None:
    budget = RateBudget()
    clock = {"now": _NOW}
    opener = _Opener([_Response(headers=_headers(0, int(_NOW) + 60)), _Response()])
    urlopen_with_backoff(_request(), now=lambda: clock["now"], opener=opener, budget=budget)
    with pytest.raises(RateLimited):
        urlopen_with_backoff(_request(), now=lambda: clock["now"], opener=opener, budget=budget)
    clock["now"] = _NOW + 61
    urlopen_with_backoff(_request(), now=lambda: clock["now"], opener=opener, budget=budget)
    assert opener.calls == 2
    assert budget.remaining is None and budget.reset_at is None


def test_min_remaining_zero_disables_the_proactive_stop() -> None:
    budget = RateBudget()
    opener = _Opener([_Response(headers=_headers(0, int(_NOW) + 600)), _Response()])
    _call(opener, _Recorder(), budget=budget, min_remaining=0)
    _call(opener, _Recorder(), budget=budget, min_remaining=0)
    assert opener.calls == 2


def test_independent_budgets_do_not_share_state() -> None:
    first, second = RateBudget(), RateBudget()
    opener = _Opener([_Response(headers=_headers(0, int(_NOW) + 600)), _Response()])
    _call(opener, _Recorder(), budget=first)
    # The second budget has seen nothing, so the request goes out.
    _call(opener, _Recorder(), budget=second)
    assert opener.calls == 2
    with pytest.raises(RateLimited):
        _call(_Opener([_Response()]), _Recorder(), budget=first)


def test_the_default_budget_is_shared_and_resettable() -> None:
    opener = _Opener([_Response(headers=_headers(1, int(_NOW) + 600)), _Response()])
    urlopen_with_backoff(_request(), now=lambda: _NOW, opener=opener)
    with pytest.raises(RateLimited):
        urlopen_with_backoff(_request(), now=lambda: _NOW, opener=opener)
    assert opener.calls == 1
    reset_default_budget()
    urlopen_with_backoff(_request(), now=lambda: _NOW, opener=opener)
    assert opener.calls == 2


def test_sleep_and_jitter_defaults_are_resolved_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = _Recorder()
    monkeypatch.setattr(github_http, "_sleep", sleep)
    monkeypatch.setattr(github_http, "_jitter", lambda: 0.25)
    monkeypatch.setattr(github_http, "_now", lambda: _NOW)
    opener = _Opener([_http_error(500), _http_error(429, {"Retry-After": "3"}), _Response()])
    urlopen_with_backoff(_request(), opener=opener, budget=RateBudget())
    assert sleep.delays == [1.25, 4.0]


# failure_reason ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make_error", "expected"),
    [
        pytest.param(lambda: GitHubAuthError(_http_error(401)), "auth_failed", id="auth"),
        pytest.param(
            lambda: RateLimited(_http_error(429), reset_at=None, wait_seconds=None),
            "rate_limited",
            id="rate-limited",
        ),
        pytest.param(lambda: _http_error(502), "http_502", id="http-502"),
        pytest.param(lambda: urllib.error.URLError("no route"), "network", id="urlerror"),
        pytest.param(lambda: TimeoutError("slow"), "network", id="timeout"),
        pytest.param(lambda: ConnectionResetError("reset"), "network", id="connection"),
        pytest.param(lambda: ValueError("boom ghp_SECRET https://x.invalid"), "error", id="other"),
    ],
)
def test_failure_reason_names(make_error: Callable[[], BaseException], expected: str) -> None:
    reason = failure_reason(make_error())
    assert reason == expected
    assert "ghp_" not in reason and "http" not in reason.replace("http_", "")
