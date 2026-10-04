"""G2/TC-196: a bounded retry for an environment failure in a live e2e build.

The live cells/rust test runs the real ``ingest-cells-rust`` container, and cargo sometimes cannot
reach its registry from inside it. The container then exits with ``ENVIRONMENT_EXIT`` (75), the
environment-failure code shared with TC-181, TC-184 and TC-189. That is an environment fact, not a
verdict on the code, so exit 75 is retried a bounded number of times. Every other non-zero exit is
a real failure and is returned at once, with no retry.

This module never skips. When every attempt reports an environment failure it raises
``RuntimeError`` and names the environment reason.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

ENVIRONMENT_EXIT = 75
ATTEMPTS = 3


class HasReturncode(Protocol):
    @property
    def returncode(self) -> int: ...


def run_with_environment_retry[R: HasReturncode](run: Callable[[], R], attempts: int = ATTEMPTS) -> R:
    """Call ``run()`` until it returns a result that is not an environment failure.

    Returns the first result whose ``returncode`` is 0, or any other non-zero ``returncode``
    straight away. A ``returncode`` of ``ENVIRONMENT_EXIT`` triggers another call, up to
    ``attempts`` calls in all. If every attempt returns ``ENVIRONMENT_EXIT``, raises
    ``RuntimeError``.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    for _ in range(attempts):
        result = run()
        if result.returncode != ENVIRONMENT_EXIT:
            return result
    raise RuntimeError(
        "environment failure: cargo could not reach its registry, and this persisted after "
        f"{attempts} attempts (exit {ENVIRONMENT_EXIT}). This is not a verdict on the code."
    )
