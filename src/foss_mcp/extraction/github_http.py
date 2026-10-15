"""Shared HTTP plumbing for the GitHub readers: optional token auth, a bounded wait, and classification.

* ``with_auth`` adds ``Authorization: Bearer <token>`` when the ``GITHUB_TOKEN`` environment
  variable is set, which raises the account's request budget from 60 to 5000 per hour.
* ``urlopen_with_backoff`` waits out a rate-limit answer (HTTP 429, a 403 with
  ``x-ratelimit-remaining: 0``, or a secondary-limit 403 carrying ``Retry-After``) when the wait
  fits a fixed budget, retries a 5xx answer or a transient transport failure with exponential
  backoff and jitter inside the same budget, and stops BEFORE the limit once the remaining-request
  count has fallen below a floor.
* A failure that cannot be waited out is classified: a 401 is a ``GitHubAuthError`` (never
  retried), a rate limit that does not fit is a ``RateLimited`` carrying the reset time. Both are
  ``HTTPError`` subclasses, so every existing ``except HTTPError`` and ``exc.code == 404`` branch
  keeps working. Every other answer (404, 304, 422, a hint-less 403 or 429) is raised unchanged.
* ``failure_reason`` names a failure in one fixed word, never a message.

The token is never logged and never placed in an exception message.
"""

from __future__ import annotations

import contextlib
import email.message
import email.utils
import http.client
import os
import random
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

_RATE_LIMIT_REMAINING = "x-ratelimit-remaining"
_RATE_LIMIT_RESET = "x-ratelimit-reset"
_RETRY_AFTER = "Retry-After"

# Seconds added to every computed rate-limit wait, so the retry lands after the reset.
_SAFETY_MARGIN_SECONDS = 1.0

# Stop BEFORE the limit: when the last answer reported fewer requests remaining than this and the
# window has not reset yet, the next request is refused without being sent.
DEFAULT_REMAINING_FLOOR = 5

_RETRYABLE_SERVER_ERRORS = frozenset({500, 502, 503, 504})
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 30.0


def _sleep(seconds: float) -> None:
    """The default sleep, looked up at call time so a test can replace it."""
    time.sleep(seconds)


def _now() -> float:
    """The default clock (epoch seconds), looked up at call time so a test can replace it."""
    return time.time()


def _jitter() -> float:
    """The default jitter in [0, 1), looked up at call time so a test can replace it."""
    return random.random()


class GitHubAuthError(urllib.error.HTTPError):
    """HTTP 401: the credential was refused. Never retried; the original is the ``__cause__``."""

    def __init__(self, original: urllib.error.HTTPError) -> None:
        super().__init__(original.url, original.code, original.msg, original.hdrs, original.fp)


class RateLimited(urllib.error.HTTPError):
    """A rate limit that could not be waited out (or that was avoided before it was hit).

    ``reset_at`` is the epoch second the window resets at (``None`` when unknown), ``wait_seconds``
    the wait the answer asked for, and ``proactive`` is True when no request was sent because the
    remaining count was already below the floor.
    """

    reset_at: float | None
    wait_seconds: float | None
    proactive: bool

    def __init__(
        self,
        original: urllib.error.HTTPError,
        *,
        reset_at: float | None,
        wait_seconds: float | None,
        proactive: bool = False,
    ) -> None:
        super().__init__(original.url, original.code, original.msg, original.hdrs, original.fp)
        self.reset_at = reset_at
        self.wait_seconds = wait_seconds
        self.proactive = proactive


def with_auth(headers: Mapping[str, str]) -> dict[str, str]:
    """A copy of *headers*, plus a bearer ``Authorization`` when ``GITHUB_TOKEN`` is set and non-empty."""
    result = dict(headers)
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    if token:
        result["Authorization"] = f"Bearer {token}"
    return result


def _header(headers: Any, name: str) -> str | None:
    """A response header by name, matched case-insensitively. ``None`` when absent or no headers."""
    if headers is None:
        return None
    value = headers.get(name)
    if value is not None:
        return str(value)
    lowered = name.lower()
    for key, val in headers.items():
        if str(key).lower() == lowered:
            return str(val)
    return None


def _float_header(headers: Any, name: str) -> float | None:
    """A numeric response header, or ``None`` when it is absent or not a number."""
    value = _header(headers, name)
    if value is None:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def _seconds_until(retry_after: str, now: float) -> float | None:
    """Seconds from *now* until an HTTP-date *retry_after*, or ``None`` if it is not one."""
    try:
        parsed = email.utils.parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        return None
    return parsed.timestamp() - now


class RateBudget:
    """The last seen ``x-ratelimit-remaining`` and ``x-ratelimit-reset``, for the proactive stop."""

    def __init__(self) -> None:
        self.remaining: float | None = None
        self.reset_at: float | None = None

    def observe(self, headers: Any, now: float) -> None:
        """Record the rate-limit headers of a successful response. A past reset clears the state."""
        remaining = _float_header(headers, _RATE_LIMIT_REMAINING)
        if remaining is None:
            return
        self.remaining = remaining
        self.reset_at = _float_header(headers, _RATE_LIMIT_RESET)
        if self.reset_at is not None and self.reset_at <= now:
            self.clear()

    def clear(self) -> None:
        self.remaining = None
        self.reset_at = None

    def check(self, now: float, min_remaining: int, url: str = "") -> None:
        """Raise ``RateLimited(proactive=True)`` when the last remaining count was below
        *min_remaining* and the reset is still in the future. A past reset clears the state;
        ``min_remaining=0`` disables the stop."""
        if self.reset_at is not None and self.reset_at <= now:
            self.clear()
        if min_remaining <= 0 or self.remaining is None or self.reset_at is None:
            return
        if self.remaining < min_remaining:
            original = urllib.error.HTTPError(
                url,
                429,
                "request budget nearly exhausted, stopped before the limit",
                email.message.Message(),
                None,
            )
            raise RateLimited(
                original, reset_at=self.reset_at, wait_seconds=max(0.0, self.reset_at - now), proactive=True
            )


_DEFAULT_BUDGET = RateBudget()


def reset_default_budget() -> None:
    """Forget what the module-level budget has seen (for tests and long-lived callers)."""
    _DEFAULT_BUDGET.clear()


def rate_limit_wait_seconds(code: int, headers: Any, now: float) -> float | None:
    """Seconds to wait before retrying a rate-limited answer, or ``None`` if this is not one.

    A rate limit is HTTP 429, or HTTP 403 whose ``x-ratelimit-remaining`` is exactly ``0``, or a
    secondary-limit HTTP 403: one that carries ``Retry-After`` while ``x-ratelimit-remaining`` is
    absent or non-zero (it waits ``Retry-After`` only). A 403 with neither hint is a permission
    error and is not retried. The wait comes from ``Retry-After`` when present, else from
    ``x-ratelimit-reset`` minus *now*, and is never negative. A rate-limit answer that carries
    neither hint returns ``None``: no wait is guessed.
    """
    secondary = False
    if code == 429:
        is_rate_limit = True
    elif code == 403:
        remaining = _header(headers, _RATE_LIMIT_REMAINING)
        if remaining is not None and remaining.strip() == "0":
            is_rate_limit = True
        else:
            secondary = _header(headers, _RETRY_AFTER) is not None
            is_rate_limit = secondary
    else:
        is_rate_limit = False
    if not is_rate_limit:
        return None

    wait: float | None = None
    retry_after = _header(headers, _RETRY_AFTER)
    if retry_after is not None:
        try:
            wait = float(retry_after)
        except ValueError:
            wait = _seconds_until(retry_after, now)
    if wait is None and not secondary:
        reset = _header(headers, _RATE_LIMIT_RESET)
        if reset is not None:
            try:
                wait = float(reset) - now
            except ValueError:
                wait = None
    if wait is None:
        return None
    return max(0.0, wait)


def _is_transient_transport_failure(exc: BaseException) -> bool:
    """A timeout, a dropped connection or a DNS hiccup. Never a TLS or certificate error."""
    transient = (TimeoutError, ConnectionError, http.client.RemoteDisconnected, socket.gaierror)
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, transient)
    return isinstance(exc, (TimeoutError, ConnectionError))


def _backoff_delay(attempt: int, jitter: Callable[[], float]) -> float:
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * 2 ** (attempt - 1) + jitter())


def urlopen_with_backoff(
    request: urllib.request.Request,
    timeout: float = 30,
    max_attempts: int = 4,
    max_total_wait: float = 120,
    sleep: Callable[[float], object] | None = None,
    now: Callable[[], float] | None = None,
    opener: Callable[..., Any] | None = None,
    jitter: Callable[[], float] | None = None,
    budget: RateBudget | None = None,
    min_remaining: int = DEFAULT_REMAINING_FLOOR,
) -> Any:
    """Open *request*, waiting out a rate limit and retrying transient failures within a budget.

    * HTTP 401 raises ``GitHubAuthError`` at once: no retry, no sleep.
    * A rate-limit answer (see ``rate_limit_wait_seconds``) whose wait fits the remaining
      *max_total_wait* budget sleeps that wait plus one second and retries, up to *max_attempts*
      in all; a wait that does not fit, or the last attempt, raises ``RateLimited`` (with the reset
      time) whose ``__cause__`` is the original error.
    * HTTP 500/502/503/504 and transient transport failures are retried with
      ``min(30, 1 * 2 ** (attempt - 1) + jitter())`` seconds, counted in the same cumulative
      budget; on exhaustion the LAST original error is re-raised unchanged.
    * Everything else (404, 304, 422, a hint-less 403 or 429, any other error) is raised unchanged
      on the first attempt.
    * Before every request *budget* (the module default when ``None``) is consulted: when the last
      successful answer reported fewer than *min_remaining* requests left and the window has not
      reset, ``RateLimited(proactive=True)`` is raised without calling the opener.

    *sleep*, *now*, *jitter* and *opener* default to ``_sleep``, ``_now``, ``_jitter`` and
    ``urllib.request.urlopen``, all looked up at call time.
    """
    open_fn = opener if opener is not None else urllib.request.urlopen
    sleep_fn = sleep if sleep is not None else _sleep
    now_fn = now if now is not None else _now
    jitter_fn = jitter if jitter is not None else _jitter
    rate_budget = budget if budget is not None else _DEFAULT_BUDGET
    waited = 0.0
    attempt = 1
    while True:
        rate_budget.check(now_fn(), min_remaining, request.full_url)
        try:
            response = open_fn(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise GitHubAuthError(exc) from exc
            wait = rate_limit_wait_seconds(exc.code, exc.headers, now_fn())
            if wait is not None:
                delay = wait + _SAFETY_MARGIN_SECONDS
                if attempt >= max_attempts or waited + delay > max_total_wait:
                    raise RateLimited(
                        exc, reset_at=_float_header(exc.headers, _RATE_LIMIT_RESET), wait_seconds=wait
                    ) from exc
            elif exc.code in _RETRYABLE_SERVER_ERRORS:
                delay = _backoff_delay(attempt, jitter_fn)
                if attempt >= max_attempts or waited + delay > max_total_wait:
                    raise
            else:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if not _is_transient_transport_failure(exc):
                raise
            delay = _backoff_delay(attempt, jitter_fn)
            if attempt >= max_attempts or waited + delay > max_total_wait:
                raise
        else:
            # A response whose headers cannot be read is still a response.
            with contextlib.suppress(AttributeError, TypeError):
                rate_budget.observe(getattr(response, "headers", None), now_fn())
            return response
        sleep_fn(delay)
        waited += delay
        attempt += 1


def failure_reason(exc: BaseException) -> str:
    """One fixed word for why a fetch failed; never a message, URL or token."""
    if isinstance(exc, GitHubAuthError):
        return "auth_failed"
    if isinstance(exc, RateLimited):
        return "rate_limited"
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return "network"
    return "error"
