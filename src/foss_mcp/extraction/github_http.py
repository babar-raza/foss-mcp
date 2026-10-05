"""Shared HTTP plumbing for the GitHub readers: optional token auth and a bounded rate-limit wait.

Two behaviours, and nothing else:

* ``with_auth`` adds ``Authorization: Bearer <token>`` when the ``GITHUB_TOKEN`` environment
  variable is set, which raises the account's request budget from 60 to 5000 per hour.
* ``urlopen_with_backoff`` waits out a rate-limit answer (HTTP 429, or 403 with
  ``x-ratelimit-remaining: 0``) when the wait fits a fixed budget, and retries. Every other
  answer, and a wait that would exceed the budget, re-raises the original ``HTTPError``
  unchanged. No answer is guessed.

The token is never logged and never placed in an exception message.
"""

from __future__ import annotations

import email.utils
import os
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


def _seconds_until(retry_after: str, now: float) -> float | None:
    """Seconds from *now* until an HTTP-date *retry_after*, or ``None`` if it is not one."""
    try:
        parsed = email.utils.parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        return None
    return parsed.timestamp() - now


def rate_limit_wait_seconds(code: int, headers: Any, now: float) -> float | None:
    """Seconds to wait before retrying a rate-limited answer, or ``None`` if this is not one.

    A rate limit is HTTP 429, or HTTP 403 whose ``x-ratelimit-remaining`` is exactly ``0``. A 403
    without that header is a permission error and is not retried. The wait comes from
    ``Retry-After`` when present, else from ``x-ratelimit-reset`` minus *now*, and is never
    negative. A rate-limit answer that carries neither hint returns ``None``: no wait is guessed.
    """
    if code == 429:
        is_rate_limit = True
    elif code == 403:
        remaining = _header(headers, _RATE_LIMIT_REMAINING)
        is_rate_limit = remaining is not None and remaining.strip() == "0"
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
    if wait is None:
        reset = _header(headers, _RATE_LIMIT_RESET)
        if reset is not None:
            try:
                wait = float(reset) - now
            except ValueError:
                wait = None
    if wait is None:
        return None
    return max(0.0, wait)


def urlopen_with_backoff(
    request: urllib.request.Request,
    timeout: float = 30,
    max_attempts: int = 4,
    max_total_wait: float = 120,
    sleep: Callable[[float], object] = time.sleep,
    now: Callable[[], float] = time.time,
    opener: Callable[..., Any] | None = None,
) -> Any:
    """Open *request*, waiting out a rate-limit answer within a bounded budget.

    On an ``HTTPError`` that is a rate limit with a wait that fits the remaining *max_total_wait*
    budget, sleeps that wait plus one second and tries again, up to *max_attempts* in all. Any
    other error, a wait that does not fit, or the last attempt re-raises the original error.
    *opener* defaults to ``urllib.request.urlopen``, looked up at call time.
    """
    open_fn = opener if opener is not None else urllib.request.urlopen
    waited = 0.0
    attempt = 1
    while True:
        try:
            return open_fn(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if attempt >= max_attempts:
                raise
            wait = rate_limit_wait_seconds(exc.code, exc.headers, now())
            if wait is None:
                raise
            delay = wait + _SAFETY_MARGIN_SECONDS
            if waited + delay > max_total_wait:
                raise
            sleep(delay)
            waited += delay
            attempt += 1
