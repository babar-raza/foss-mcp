"""G3/TC-508 (REQ-G3-008): the deadline and circuit breaker around the query-time vector fallback.

Offline and deterministic. Time is injected: the circuit breaker's ``clock`` is a fake the test
advances, never a real wait. A call that must time out blocks on a ``threading.Event`` the test sets
in a ``finally``, and the assertion is that ``call`` RETURNED ``skipped_timeout`` while that function
was still blocked, never a measured duration. No test is skipped.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from foss_mcp.indexing.gateway_embedding_provider import EmbeddingBudgetExceededError, GatewayEmbeddingError
from foss_mcp.mcp.tools import vector_guard
from foss_mcp.mcp.tools.vector_guard import (
    CIRCUIT_FAILURE_THRESHOLD,
    CIRCUIT_OPEN_SECONDS,
    DEADLINE_ENV,
    DEFAULT_DEADLINE_SECONDS,
    QUERY_TIME_MAX_ATTEMPTS,
    STATUS_OK,
    STATUS_SKIPPED_CIRCUIT_OPEN,
    STATUS_SKIPPED_ERROR,
    STATUS_SKIPPED_TIMEOUT,
    VectorFallbackGuard,
    _set_guard,
    get_guard,
)


class FakeClock:
    """A list-backed clock the test advances by hand."""

    def __init__(self) -> None:
        self._now = [1000.0]

    def __call__(self) -> float:
        return self._now[0]

    def advance(self, seconds: float) -> None:
        self._now[0] += seconds


@pytest.fixture
def executor() -> Iterator[ThreadPoolExecutor]:
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="test-vector-guard")
    try:
        yield pool
    finally:
        pool.shutdown(wait=True)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def _no_deadline_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEADLINE_ENV, raising=False)


def _guard(clock: FakeClock, executor: ThreadPoolExecutor, **kwargs: object) -> VectorFallbackGuard:
    return VectorFallbackGuard(clock=clock, executor=executor, **kwargs)  # type: ignore[arg-type]


def _raising(exc: BaseException) -> Callable[[], str]:
    def _fn() -> str:
        raise exc

    return _fn


class _Counter:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.calls = 0
        self._exc = exc

    def __call__(self) -> str:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return "vector"


def _fail_three_times(guard: VectorFallbackGuard, counter: _Counter | None = None) -> _Counter:
    counter = counter or _Counter(GatewayEmbeddingError("down"))
    for _ in range(3):
        assert guard.call(counter) == (STATUS_SKIPPED_ERROR, None)
    return counter


def test_the_defaults_are_two_seconds_three_failures_and_sixty_seconds(
    clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    assert DEFAULT_DEADLINE_SECONDS == 2.0
    assert CIRCUIT_FAILURE_THRESHOLD == 3
    assert CIRCUIT_OPEN_SECONDS == 60.0
    assert QUERY_TIME_MAX_ATTEMPTS == 1

    guard = _guard(clock, executor)
    assert guard.deadline_seconds == 2.0
    _fail_three_times(guard)
    assert guard.is_open() is True
    clock.advance(59.9)
    assert guard.is_open() is True
    clock.advance(0.2)
    assert guard.is_open() is False


def test_the_deadline_env_variable_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    assert DEADLINE_ENV == "FOSS_MCP_VECTOR_DEADLINE_SECONDS"
    monkeypatch.setenv(DEADLINE_ENV, "0.25")

    assert _guard(clock, executor).deadline_seconds == 0.25
    assert _guard(clock, executor, deadline_seconds=0.5).deadline_seconds == 0.5


@pytest.mark.parametrize("bad", ["canary-not-a-number", "0", "-1", "-0.5", "nan", "inf"])
def test_an_invalid_deadline_value_raises_and_does_not_echo_it(
    bad: str, monkeypatch: pytest.MonkeyPatch, clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    monkeypatch.setenv(DEADLINE_ENV, bad)

    with pytest.raises(ValueError) as caught:
        _guard(clock, executor)

    message = str(caught.value)
    assert DEADLINE_ENV in message
    assert bad not in message.replace(DEADLINE_ENV, "")
    assert caught.value.__cause__ is None


def test_a_fast_call_returns_ok_with_its_value(clock: FakeClock, executor: ThreadPoolExecutor) -> None:
    guard = _guard(clock, executor, deadline_seconds=5.0)

    assert guard.call(lambda: "the-vector") == (STATUS_OK, "the-vector")
    assert guard.is_open() is False


def test_a_blocked_call_returns_skipped_timeout_while_the_function_is_still_running(
    clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    guard = _guard(clock, executor, deadline_seconds=0.05)
    release = threading.Event()
    try:
        result = guard.call(lambda: release.wait(timeout=30))

        assert result == (STATUS_SKIPPED_TIMEOUT, None)
        assert not release.is_set()  # call() returned while the function was still blocked
    finally:
        release.set()


def test_a_timeout_counts_as_a_failure(clock: FakeClock, executor: ThreadPoolExecutor) -> None:
    guard = _guard(clock, executor, deadline_seconds=0.05, failure_threshold=1)
    release = threading.Event()
    try:
        assert guard.is_open() is False
        assert guard.call(lambda: release.wait(timeout=30)) == (STATUS_SKIPPED_TIMEOUT, None)

        assert guard.is_open() is True
        assert guard.call(lambda: "x") == (STATUS_SKIPPED_CIRCUIT_OPEN, None)
    finally:
        release.set()


def test_three_consecutive_gateway_failures_open_the_circuit(
    clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    guard = _guard(clock, executor)
    counter = _Counter(GatewayEmbeddingError("gateway said no"))

    guard.call(counter)
    guard.call(counter)
    assert guard.is_open() is False
    guard.call(counter)

    assert guard.is_open() is True


def test_an_open_circuit_never_calls_the_function(clock: FakeClock, executor: ThreadPoolExecutor) -> None:
    guard = _guard(clock, executor)
    counter = _fail_three_times(guard)
    assert counter.calls == 3

    for _ in range(5):
        assert guard.call(counter) == (STATUS_SKIPPED_CIRCUIT_OPEN, None)

    assert counter.calls == 3


def test_the_circuit_closes_after_sixty_seconds_of_injected_time_and_a_good_probe_resets_it(
    clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    guard = _guard(clock, executor)
    _fail_three_times(guard)
    assert guard.is_open() is True
    clock.advance(61.0)
    assert guard.is_open() is False

    assert guard.call(lambda: "probe") == (STATUS_OK, "probe")

    assert guard.is_open() is False
    # The failure count was reset: two further failures do not open it, a third does.
    failing = _Counter(GatewayEmbeddingError("down again"))
    guard.call(failing)
    guard.call(failing)
    assert guard.is_open() is False
    guard.call(failing)
    assert guard.is_open() is True


def test_a_failed_probe_reopens_the_circuit_at_once(clock: FakeClock, executor: ThreadPoolExecutor) -> None:
    guard = _guard(clock, executor)
    failing = _fail_three_times(guard)
    clock.advance(61.0)
    assert guard.is_open() is False

    assert guard.call(failing) == (STATUS_SKIPPED_ERROR, None)

    assert failing.calls == 4  # the probe really went through
    assert guard.is_open() is True
    assert guard.call(failing) == (STATUS_SKIPPED_CIRCUIT_OPEN, None)
    assert failing.calls == 4
    clock.advance(61.0)
    assert guard.is_open() is False


def test_a_success_between_failures_resets_the_count(clock: FakeClock, executor: ThreadPoolExecutor) -> None:
    guard = _guard(clock, executor)
    failing = _Counter(EmbeddingBudgetExceededError("budget"))

    guard.call(failing)
    guard.call(failing)
    assert guard.call(lambda: "fine") == (STATUS_OK, "fine")
    guard.call(failing)
    guard.call(failing)

    assert guard.is_open() is False
    guard.call(failing)
    assert guard.is_open() is True


def test_a_configuration_error_is_skipped_error_and_never_opens_the_circuit(
    clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    guard = _guard(clock, executor)
    unconfigured = _Counter(RuntimeError("the gateway endpoint must be set"))

    for _ in range(10):
        assert guard.call(unconfigured) == (STATUS_SKIPPED_ERROR, None)

    assert unconfigured.calls == 10
    assert guard.is_open() is False


def test_an_oserror_counts_as_a_failure(clock: FakeClock, executor: ThreadPoolExecutor) -> None:
    guard = _guard(clock, executor)
    failing = _Counter(ConnectionResetError("reset"))

    for _ in range(3):
        assert guard.call(failing) == (STATUS_SKIPPED_ERROR, None)

    assert guard.is_open() is True


def test_a_timeout_error_raised_by_the_function_is_an_error_that_counts(
    clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    guard = _guard(clock, executor, deadline_seconds=5.0, failure_threshold=2)
    failing = _Counter(TimeoutError("socket timed out"))

    assert guard.call(failing) == (STATUS_SKIPPED_ERROR, None)
    assert guard.call(failing) == (STATUS_SKIPPED_ERROR, None)

    assert guard.is_open() is True


def test_is_open_follows_the_injected_clock(clock: FakeClock, executor: ThreadPoolExecutor) -> None:
    guard = _guard(clock, executor, failure_threshold=1, open_seconds=10.0)
    assert guard.is_open() is False

    guard.call(_raising(GatewayEmbeddingError("down")))
    assert guard.is_open() is True
    clock.advance(9.99)
    assert guard.is_open() is True
    clock.advance(0.02)
    assert guard.is_open() is False


def test_get_guard_returns_one_process_wide_guard_and_set_guard_replaces_it(
    clock: FakeClock, executor: ThreadPoolExecutor
) -> None:
    previous = get_guard()
    try:
        assert get_guard() is previous
        assert vector_guard.get_guard() is previous

        replacement = _guard(clock, executor)
        _set_guard(replacement)

        assert get_guard() is replacement
        assert get_guard() is get_guard()
    finally:
        _set_guard(previous)
    assert get_guard() is previous
