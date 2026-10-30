"""G3/TC-598 (REQ-G3-010): the committed-baseline contract.

A baseline file is a small JSON document shaped::

    {"strict": bool, "known_failures": [{"pilot": str, "case_id": str, "reason": str}, ...]}

``load_baseline`` reads one; ``assert_no_new_failures`` is the one assertion every F4-F9 invariant test
(and every later program's closure card: Q17, I14, O10, C11, XA19, KD11, RT5, R7) calls against its own
measured failures. A failure outside the baseline always fails the check. Once a baseline's ``"strict"``
is flipped to ``True`` (by the program's own closure card, never by this one), a baseline entry that no
longer reproduces also fails the check - baselines may only shrink, never go stale. This module is pure
and has no pytest dependency of its own; it is exercised by ``test_conformance_core.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KnownFailure:
    """One already-known, already-explained failure a baseline carries forward."""

    pilot: str
    case_id: str
    reason: str


@dataclass(frozen=True)
class Baseline:
    """A loaded baseline: whether it is strict, and the failures it already knows about."""

    strict: bool
    known_failures: tuple[KnownFailure, ...]

    def known_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset((failure.pilot, failure.case_id) for failure in self.known_failures)


def load_baseline(path: Path) -> Baseline:
    """Load a ``Baseline`` from the JSON file at *path*, shaped as this module's own docstring
    describes. ``"strict"`` defaults to ``False`` and ``"known_failures"`` defaults to empty if either
    key is absent, so a minimal ``{}`` file is a valid, permissive baseline."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    known_failures = tuple(
        KnownFailure(pilot=str(entry["pilot"]), case_id=str(entry["case_id"]), reason=str(entry["reason"]))
        for entry in payload.get("known_failures", [])
    )
    return Baseline(strict=bool(payload.get("strict", False)), known_failures=known_failures)


def _observed_key(failure: Any) -> tuple[str, str]:
    """A ``(pilot, case_id)`` key from one observed-failure record, accepted in any of the shapes a
    caller is likely to already have on hand: a mapping with ``"pilot"``/``"case_id"`` keys, a 2-item
    sequence of ``(pilot, case_id)``, or any object exposing ``.pilot``/``.case_id`` attributes (a
    ``KnownFailure`` itself, for instance)."""
    if isinstance(failure, Mapping):
        return (str(failure["pilot"]), str(failure["case_id"]))
    if isinstance(failure, (tuple, list)) and len(failure) == 2:
        return (str(failure[0]), str(failure[1]))
    return (str(failure.pilot), str(failure.case_id))


def assert_no_new_failures(baseline: Baseline, observed_failures: Iterable[Any]) -> None:
    """Raise ``AssertionError`` if *observed_failures* contains a ``(pilot, case_id)`` the baseline does
    not already know about, naming exactly which case(s) are new. When ``baseline.strict`` is ``True``,
    also raise if a baseline entry is NOT present in *observed_failures* (i.e. it now passes and the
    baseline must be tightened); when ``baseline.strict`` is ``False`` (the default), that same absence
    never fails - a baseline may always be ahead of what is currently observed."""
    observed_keys = frozenset(_observed_key(failure) for failure in observed_failures)
    known_keys = baseline.known_keys()

    new_failures = observed_keys - known_keys
    if new_failures:
        named = ", ".join(f"{pilot}:{case_id}" for pilot, case_id in sorted(new_failures))
        raise AssertionError(f"new failure(s) not present in the baseline: {named}")

    if baseline.strict:
        stale = known_keys - observed_keys
        if stale:
            named = ", ".join(f"{pilot}:{case_id}" for pilot, case_id in sorted(stale))
            raise AssertionError(
                f"baseline entry/entries no longer fail and the strict baseline must be tightened: {named}"
            )
