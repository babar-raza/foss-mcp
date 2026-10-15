"""G3/TC-509 (REQ-G3-008): ``UsageRecorder`` can be a ring buffer.

Nothing in production drains the recorder, so the original drop-new queue sat pinned at its cap holding
the first 1,000 events forever. ``overflow="drop_oldest"`` keeps the latest events instead; the default
(``drop_new``) is unchanged for every existing caller. Offline and pure.
"""

from __future__ import annotations

import pytest

from foss_mcp.telemetry.usage_recorder import UsageEvent, UsageRecorder, build_event


def _event(index: int) -> UsageEvent:
    return build_event(
        request_correlation_id=f"corr-{index}",
        deployment_id="pdf::go",
        generation_id=None,
        tool_name="search_symbols",
        outcome="success",
        latency_ms=1.0,
    )


def _ids(events: list[UsageEvent]) -> list[str]:
    return [event.request_correlation_id for event in events]


def test_the_default_mode_still_drops_the_new_event_when_full() -> None:
    recorder = UsageRecorder(max_queued=2)
    assert recorder.record(_event(0)) is True
    assert recorder.record(_event(1)) is True
    assert recorder.record(_event(2)) is False
    assert recorder.dropped_count == 1
    assert _ids(recorder.drain()) == ["corr-0", "corr-1"]


def test_drop_oldest_keeps_the_newest_events_in_order() -> None:
    recorder = UsageRecorder(max_queued=3, overflow="drop_oldest")
    for index in range(10):
        recorder.record(_event(index))
    assert _ids(recorder.drain()) == ["corr-7", "corr-8", "corr-9"]


def test_drop_oldest_counts_every_eviction() -> None:
    recorder = UsageRecorder(max_queued=3, overflow="drop_oldest")
    for index in range(10):
        recorder.record(_event(index))
    assert recorder.dropped_count == 7


def test_drop_oldest_never_exceeds_max_queued() -> None:
    recorder = UsageRecorder(max_queued=4, overflow="drop_oldest")
    for index in range(50):
        recorder.record(_event(index))
        assert len(recorder) <= 4
    assert len(recorder) == 4


def test_drop_oldest_record_returns_true() -> None:
    recorder = UsageRecorder(max_queued=1, overflow="drop_oldest")
    assert recorder.record(_event(0)) is True
    assert recorder.record(_event(1)) is True
    assert recorder.dropped_count == 1


def test_an_unknown_overflow_mode_raises_value_error() -> None:
    with pytest.raises(ValueError):
        UsageRecorder(overflow="drop_everything")
    with pytest.raises(ValueError):
        UsageRecorder(overflow="")


def test_drain_works_in_ring_mode_and_empties_the_queue() -> None:
    recorder = UsageRecorder(max_queued=3, overflow="drop_oldest")
    for index in range(5):
        recorder.record(_event(index))
    assert _ids(recorder.drain(limit=2)) == ["corr-2", "corr-3"]
    assert len(recorder) == 1
    assert _ids(recorder.drain()) == ["corr-4"]
    assert len(recorder) == 0
    assert recorder.drain() == []
    assert recorder.record(_event(9)) is True
    assert recorder.dropped_count == 2
