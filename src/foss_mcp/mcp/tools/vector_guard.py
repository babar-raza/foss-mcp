"""A hard deadline and a circuit breaker for the query-time vector fallback (G3/TC-508).

``find_examples`` embeds the agent's query through the external embedding gateway when its lexical
stages miss. That call used to inherit the ingestion provider's settings (a 30 second socket timeout,
three attempts, 1 s then 2 s of backoff: about 93 s worst case), so a slow or black-holed gateway
stalled the tool, and ``lookup`` with it, for tens of seconds, and kept receiving the raw query on
every miss. ``VectorFallbackGuard`` bounds that call:

- A hard overall deadline (default 2.0 s, ``FOSS_MCP_VECTOR_DEADLINE_SECONDS``) measured from the
  moment the call is submitted. The call runs on a worker thread; when the deadline passes the caller
  gets ``skipped_timeout`` immediately and the worker thread is left to finish on its own.
- No retries (the provider is told to make one attempt; see ``QUERY_TIME_MAX_ATTEMPTS``).
- A circuit breaker: ``CIRCUIT_FAILURE_THRESHOLD`` consecutive gateway failures or timeouts open the
  circuit for ``CIRCUIT_OPEN_SECONDS``. While it is open the function is never called, so the raw
  query is never sent. After that time the next call is let through as a probe: a success closes the
  circuit, a failure re-opens it at once.

Only gateway-shaped failures count (``GatewayEmbeddingError``, ``EmbeddingBudgetExceededError``,
``OSError``, ``TimeoutError`` and a deadline overrun). Any other exception, for example the
``RuntimeError`` of a deployment with no gateway configured, is reported as ``skipped_error`` but is
not counted, so an unconfigured gateway never opens the circuit.

No environment value, query text or endpoint is ever placed in a result, an exception or a log line
by this module.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TypeVar

from foss_mcp.indexing.gateway_embedding_provider import EmbeddingBudgetExceededError, GatewayEmbeddingError

T = TypeVar("T")

DEADLINE_ENV = "FOSS_MCP_VECTOR_DEADLINE_SECONDS"
DEFAULT_DEADLINE_SECONDS = 2.0
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_OPEN_SECONDS = 60.0
QUERY_TIME_MAX_ATTEMPTS = 1

STATUS_OK = "ok"
STATUS_SKIPPED_TIMEOUT = "skipped_timeout"
STATUS_SKIPPED_CIRCUIT_OPEN = "skipped_circuit_open"
STATUS_SKIPPED_ERROR = "skipped_error"

_COUNTED_ERRORS = (GatewayEmbeddingError, EmbeddingBudgetExceededError, OSError, TimeoutError)

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _default_executor() -> ThreadPoolExecutor:
    """The one module-level pool, created on first use."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vector-fallback")
        return _executor


def _deadline_from_environment() -> float:
    raw = os.environ.get(DEADLINE_ENV, "").strip()
    if not raw:
        return DEFAULT_DEADLINE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{DEADLINE_ENV} must be a positive number of seconds") from None
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{DEADLINE_ENV} must be a positive number of seconds")
    return value


class VectorFallbackGuard:
    """Runs one query-time embedding call under a deadline and a circuit breaker. Thread-safe."""

    def __init__(
        self,
        *,
        deadline_seconds: float | None = None,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        open_seconds: float = CIRCUIT_OPEN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.deadline_seconds = _deadline_from_environment() if deadline_seconds is None else deadline_seconds
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._clock = clock
        self._executor = executor
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_until = 0.0

    def is_open(self) -> bool:
        """True while the circuit is open, that is while ``clock()`` is before its reopen time."""
        with self._lock:
            return self._clock() < self._opened_until

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_until = 0.0

    def _record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._opened_until = self._clock() + self._open_seconds

    def call(self, fn: Callable[[], T]) -> tuple[str, T | None]:
        """``(status, value)``: ``fn``'s result under the deadline, or why it was skipped."""
        if self.is_open():
            return STATUS_SKIPPED_CIRCUIT_OPEN, None

        executor = self._executor if self._executor is not None else _default_executor()
        future: Future[T] = executor.submit(fn)
        try:
            value = future.result(timeout=self.deadline_seconds)
        except FutureTimeoutError as exc:
            if future.done():
                # ``fn`` itself raised a TimeoutError (the same class on Python 3.11 and later):
                # that is a gateway failure, not a deadline overrun.
                return self._after_exception(exc)
            future.cancel()
            self._record_failure()
            return STATUS_SKIPPED_TIMEOUT, None
        except Exception as exc:
            return self._after_exception(exc)
        self._record_success()
        return STATUS_OK, value

    def _after_exception(self, exc: BaseException) -> tuple[str, None]:
        if isinstance(exc, _COUNTED_ERRORS):
            self._record_failure()
        return STATUS_SKIPPED_ERROR, None


_guard: VectorFallbackGuard | None = None
_guard_lock = threading.Lock()


def get_guard() -> VectorFallbackGuard:
    """The one process-wide guard, created on first use."""
    global _guard
    with _guard_lock:
        if _guard is None:
            _guard = VectorFallbackGuard()
        return _guard


def _set_guard(guard: VectorFallbackGuard | None) -> None:
    """Replace the process-wide guard (``None`` resets it). For tests."""
    global _guard
    with _guard_lock:
        _guard = guard
