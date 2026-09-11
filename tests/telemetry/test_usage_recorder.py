"""The structured telemetry contract: every field present, a bounded queue with an explicit
drop counter, and symbol_fqn/raw query text never becoming a label - or appearing on the
event at all.
"""

from __future__ import annotations

from foss_mcp.telemetry.usage_recorder import (
    EVENT_FIELDS,
    UsageEvent,
    UsageRecorder,
    build_event,
    classify_query_shape,
    new_correlation_id,
)


def _event(**overrides):
    defaults = dict(
        request_correlation_id="corr-1",
        deployment_id="pdf::net",
        generation_id="pdf::net::self_extracted::v1",
        tool_name="search_symbols",
        outcome="success",
        latency_ms=12.5,
        result_count=3,
        citation_count=1,
        cache_status="miss",
        query="Document.Open",
    )
    defaults.update(overrides)
    return build_event(**defaults)


def test_every_contract_field_round_trips_through_to_dict() -> None:
    event = _event()
    data = event.to_dict()
    assert set(data) == set(EVENT_FIELDS)
    assert data["tool_name"] == "search_symbols"
    assert data["outcome"] == "success"
    assert data["result_count"] == 3
    assert data["citation_count"] == 1
    assert data["cache_status"] == "miss"
    assert data["query_shape_category"] == "exact_fqn"


def test_symbol_fqn_and_raw_query_text_never_appear_on_the_event() -> None:
    """The unbounded-cardinality inputs a caller passes in (a real symbol name, a whole
    sentence) must never survive into the event or its dict - only the bounded category."""
    event = _event(query="Aspose.Pdf.Document.AddTextAnnotation")
    data = event.to_dict()
    assert "symbol_fqn" not in data
    assert "query" not in data
    assert "Aspose.Pdf.Document.AddTextAnnotation" not in data.values()
    assert not any("Aspose" in str(value) for value in data.values())
    assert "symbol_fqn" not in UsageEvent.__dataclass_fields__
    assert "query" not in UsageEvent.__dataclass_fields__


def test_classify_query_shape_never_returns_the_raw_text() -> None:
    for query in ("", "   ", "Document.Open", "how do I open a pdf", "widget"):
        category = classify_query_shape(query)
        assert category in ("empty", "exact_fqn", "phrase", "keyword")
        assert category != query


def test_a_dropped_event_increments_the_drop_counter_and_the_queue_never_exceeds_capacity() -> None:
    recorder = UsageRecorder(max_queued=3)
    accepted = [recorder.record(_event(tool_name=f"tool-{i}")) for i in range(5)]

    assert accepted == [True, True, True, False, False]
    assert recorder.dropped_count == 2
    assert len(recorder) == 3  # never grew past max_queued


def test_recording_never_blocks_or_raises_once_the_queue_is_full() -> None:
    recorder = UsageRecorder(max_queued=1)
    assert recorder.record(_event()) is True
    for _ in range(100):
        assert recorder.record(_event()) is False  # dropped, not raised, not blocked
    assert recorder.dropped_count == 100


def test_nested_calls_stay_correlated_but_are_recorded_as_separate_events() -> None:
    """lookup dispatching to search_symbols: two real tool invocations, one correlation id -
    never flattened into a single record."""
    recorder = UsageRecorder()
    correlation_id = new_correlation_id()

    recorder.record(
        build_event(
            request_correlation_id=correlation_id,
            deployment_id="pdf::net",
            generation_id="pdf::net::self_extracted::v1",
            tool_name="lookup",
            outcome="success",
            latency_ms=20.0,
            result_count=1,
            query="Document.Open",
        )
    )
    recorder.record(
        build_event(
            request_correlation_id=correlation_id,
            deployment_id="pdf::net",
            generation_id="pdf::net::self_extracted::v1",
            tool_name="search_symbols",
            outcome="success",
            latency_ms=8.0,
            result_count=1,
            query="Document.Open",
        )
    )

    events = recorder.drain()
    assert len(events) == 2
    assert {event.tool_name for event in events} == {"lookup", "search_symbols"}
    assert {event.request_correlation_id for event in events} == {correlation_id}
    assert len({event.event_id for event in events}) == 2  # distinct records, not flattened
