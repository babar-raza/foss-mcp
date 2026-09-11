"""The structured telemetry contract, delivered through a bounded queue that never blocks the
serving path and never grows without limit.

``symbol_fqn`` and raw query text are NEVER metric labels: either one is effectively unbounded
cardinality (one label value per distinct symbol or query ever asked), which turns a metrics
backend's label index into an unbounded-growth problem of its own. Neither field exists on
``UsageEvent`` at all - the contract's eleven fields are exactly what plan 18.8 names, and
``query_shape_category`` (a small, closed set of buckets) is the label-safe stand-in a caller
gets instead of the raw text.

Nested tool calls (``lookup`` dispatching to ``search_symbols``, say) are recorded as separate
``UsageEvent`` records sharing one ``request_correlation_id`` - never flattened into a single
record that would hide which underlying tool actually ran.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass
from typing import Literal

QueryShapeCategory = Literal["empty", "exact_fqn", "phrase", "keyword"]
Outcome = Literal["success", "miss", "error"]
CacheStatus = Literal["hit", "miss", "bypass", "unknown"]

# The complete contract (plan 18.8) - checked structurally by UsageEvent.to_dict(), not just
# by convention, so a future edit that drops or renames a field is caught immediately.
EVENT_FIELDS: tuple[str, ...] = (
    "event_id",
    "request_correlation_id",
    "deployment_id",
    "generation_id",
    "tool_name",
    "outcome",
    "latency_ms",
    "result_count",
    "citation_count",
    "cache_status",
    "query_shape_category",
)


@dataclass(frozen=True)
class UsageEvent:
    """One tool invocation's telemetry record - exactly the eleven contract fields, nothing
    else. There is deliberately no ``symbol_fqn`` or raw-query-text field: unbounded-
    cardinality values are never given a place to be attached as a label downstream.
    """

    event_id: str
    request_correlation_id: str
    deployment_id: str
    generation_id: str | None
    tool_name: str
    outcome: Outcome
    latency_ms: float
    result_count: int
    citation_count: int
    cache_status: CacheStatus
    query_shape_category: QueryShapeCategory

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in EVENT_FIELDS}


def new_correlation_id() -> str:
    """One id per top-level request; every nested tool call reuses it."""
    return uuid.uuid4().hex


def _new_event_id() -> str:
    return uuid.uuid4().hex


def classify_query_shape(query: str | None) -> QueryShapeCategory:
    """A bounded, label-safe stand-in for the raw query text - never the text itself.

    Four buckets, never more: an empty caller query, a dotted-or-capitalized exact-FQN-shaped
    query, a multi-word phrase, or a single bare keyword.
    """
    if query is None or not query.strip():
        return "empty"
    stripped = query.strip()
    if " " in stripped:
        return "phrase"
    if "." in stripped or stripped[:1].isupper():
        return "exact_fqn"
    return "keyword"


def build_event(
    *,
    request_correlation_id: str,
    deployment_id: str,
    generation_id: str | None,
    tool_name: str,
    outcome: Outcome,
    latency_ms: float,
    result_count: int = 0,
    citation_count: int = 0,
    cache_status: CacheStatus = "unknown",
    query: str | None = None,
) -> UsageEvent:
    """Build one contract-complete event. *query* is read only to derive
    ``query_shape_category`` - the raw text itself is never retained anywhere on the event.
    """
    return UsageEvent(
        event_id=_new_event_id(),
        request_correlation_id=request_correlation_id,
        deployment_id=deployment_id,
        generation_id=generation_id,
        tool_name=tool_name,
        outcome=outcome,
        latency_ms=latency_ms,
        result_count=result_count,
        citation_count=citation_count,
        cache_status=cache_status,
        query_shape_category=classify_query_shape(query),
    )


class UsageRecorder:
    """A bounded, in-memory delivery queue for ``UsageEvent`` records.

    ``record()`` never blocks and never grows the queue past ``max_queued``: once full, the
    NEW event is dropped and counted (the queue's existing contents are never evicted), so a
    burst cannot silently overwrite events a consumer has not drained yet, and the serving
    path calling ``record()`` never pays for however slow or stalled delivery is.
    """

    def __init__(self, max_queued: int = 1000) -> None:
        if max_queued < 1:
            raise ValueError("max_queued must be at least 1")
        self._max_queued = max_queued
        self._queue: deque[UsageEvent] = deque()
        self._dropped_count = 0

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    def __len__(self) -> int:
        return len(self._queue)

    def record(self, event: UsageEvent) -> bool:
        """Enqueue *event*. Returns ``False`` (and increments ``dropped_count``) if the queue
        is already at capacity; never raises, never blocks.
        """
        if len(self._queue) >= self._max_queued:
            self._dropped_count += 1
            return False
        self._queue.append(event)
        return True

    def drain(self, limit: int | None = None) -> list[UsageEvent]:
        """Remove and return up to *limit* queued events, oldest first (``None`` drains all)."""
        count = len(self._queue) if limit is None else min(limit, len(self._queue))
        return [self._queue.popleft() for _ in range(count)]
