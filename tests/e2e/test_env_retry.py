"""G2/TC-196: offline proof of the environment-failure retry. No docker, no network.

Every test drives ``run_with_environment_retry`` with a plain fake object that carries only a
``returncode`` attribute, so the retry policy is checked on its own, without a live container.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from .env_retry import ATTEMPTS, ENVIRONMENT_EXIT, run_with_environment_retry


@dataclass
class _FakeRun:
    returncode: int


def _scripted(returncodes: list[int]) -> tuple[Callable[[], _FakeRun], list[_FakeRun]]:
    """A ``run`` that returns one scripted result per call, and records every result it gave."""
    given: list[_FakeRun] = []
    queue = list(returncodes)

    def run() -> _FakeRun:
        result = _FakeRun(queue.pop(0))
        given.append(result)
        return result

    return run, given


def test_a_successful_run_is_returned_once() -> None:
    run, given = _scripted([0])

    result = run_with_environment_retry(run)

    assert result.returncode == 0
    assert len(given) == 1


def test_an_environment_exit_is_retried_and_the_second_success_is_returned() -> None:
    run, given = _scripted([ENVIRONMENT_EXIT, 0])

    result = run_with_environment_retry(run)

    assert result.returncode == 0
    assert len(given) == 2
    assert result is given[1]


def test_another_nonzero_exit_is_returned_at_once_with_no_retry() -> None:
    run, given = _scripted([1])

    result = run_with_environment_retry(run)

    assert result.returncode == 1
    assert len(given) == 1


def test_an_environment_exit_on_every_attempt_raises_after_exactly_attempts_calls() -> None:
    run, given = _scripted([ENVIRONMENT_EXIT] * ATTEMPTS)

    with pytest.raises(RuntimeError, match="cargo could not reach its registry") as excinfo:
        run_with_environment_retry(run)

    assert len(given) == ATTEMPTS
    assert f"after {ATTEMPTS} attempts" in str(excinfo.value)
    assert "not a verdict on the code" in str(excinfo.value)
