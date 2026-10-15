"""G3/TC-509 (REQ-G3-008): the stdlib Prometheus exposition behind ``/metrics``.

Offline and pure: no clock, no network, no server. Each test builds a ``MetricsRegistry`` and reads the
text ``render_prometheus`` produces, so a drift in a metric name, a label or the text format fails here
before any scraper sees it.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from foss_mcp.telemetry import metrics
from foss_mcp.telemetry.metrics import LATENCY_BUCKETS, MetricsRegistry

CALLS = "foss_mcp_tool_calls_total"
MISSES = "foss_mcp_tool_misses_total"
LATENCY = "foss_mcp_tool_latency_seconds"
CONSTANTS = 'family="pdf",platform="go"'

_LABEL = r'[a-zA-Z_][a-zA-Z0-9_]*="(?:[^"\\\n]|\\["\\n])*"'
_SAMPLE = re.compile(
    rf"^[a-zA-Z_:][a-zA-Z0-9_:]*\{{{_LABEL}(?:,{_LABEL})*\}} (?:-?\d+(?:\.\d+)?(?:e[+-]?\d+)?|\+Inf|NaN)$"
)

GENERATION = {
    "generation_id": "pdf::go::self_extracted::20261014T1-abc",
    "source_commit": "b717287",
    "build_revision": "0123456789ab",
}


def _registry() -> MetricsRegistry:
    return MetricsRegistry(family="pdf", platform="go")


def _render(registry: MetricsRegistry, **overrides: Any) -> str:
    arguments: dict[str, Any] = {
        "open_sessions": 3,
        "ready": True,
        "generation_info": GENERATION,
        "usage_queued": 5,
        "usage_dropped": 2,
    }
    arguments.update(overrides)
    return registry.render_prometheus(**arguments)


def _samples(text: str) -> dict[str, float]:
    """Every sample line as ``{"name{labels}": value}``."""
    found: dict[str, float] = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            series, value = line.rsplit(" ", 1)
            found[series] = float("inf") if value == "+Inf" else float(value)
    return found


def _observe(registry: MetricsRegistry, **overrides: Any) -> None:
    arguments: dict[str, Any] = {
        "tool": "search_symbols",
        "outcome": "success",
        "code": "ok",
        "latency_seconds": 0.01,
        "miss": False,
    }
    arguments.update(overrides)
    registry.observe_tool_call(**arguments)


def test_the_metric_names_are_the_documented_constants() -> None:
    assert metrics.TOOL_CALLS_TOTAL == "foss_mcp_tool_calls_total"
    assert metrics.TOOL_MISSES_TOTAL == "foss_mcp_tool_misses_total"
    assert metrics.TOOL_LATENCY_SECONDS == "foss_mcp_tool_latency_seconds"
    assert metrics.OPEN_SESSIONS == "foss_mcp_open_sessions"
    assert metrics.SESSION_REJECTIONS_TOTAL == "foss_mcp_session_rejections_total"
    assert metrics.GENERATION_INFO == "foss_mcp_generation_info"
    assert metrics.READY == "foss_mcp_ready"
    assert metrics.USAGE_EVENTS_QUEUED == "foss_mcp_usage_events_queued"
    assert metrics.USAGE_EVENTS_DROPPED_TOTAL == "foss_mcp_usage_events_dropped_total"
    assert metrics.CONTENT_TYPE == "text/plain; version=0.0.4; charset=utf-8"


def test_every_series_carries_the_constant_family_and_platform_labels() -> None:
    registry = _registry()
    _observe(registry, miss=True)
    registry.count_session_rejection()
    samples = _samples(_render(registry))
    assert samples
    for series in samples:
        assert f"{{{CONSTANTS}" in series, series


def test_a_success_call_increments_calls_total_with_code_ok() -> None:
    registry = _registry()
    _observe(registry)
    _observe(registry)
    series = f'{CALLS}{{{CONSTANTS},tool="search_symbols",outcome="success",code="ok"}}'
    assert _samples(_render(registry))[series] == 2


def test_an_error_call_increments_calls_total_with_its_code_and_outcome() -> None:
    registry = _registry()
    _observe(registry, tool="get_symbol", outcome="error", code="invalid_argument")
    samples = _samples(_render(registry))
    assert samples[f'{CALLS}{{{CONSTANTS},tool="get_symbol",outcome="error",code="invalid_argument"}}'] == 1
    assert not any('code="ok"' in series for series in samples)


def test_a_miss_increments_misses_total_once() -> None:
    registry = _registry()
    _observe(registry, miss=True)
    samples = _samples(_render(registry))
    assert samples[f'{MISSES}{{{CONSTANTS},tool="search_symbols"}}'] == 1
    assert samples[f'{CALLS}{{{CONSTANTS},tool="search_symbols",outcome="success",code="ok"}}'] == 1


def test_a_hit_does_not_increment_misses_total() -> None:
    registry = _registry()
    _observe(registry, miss=False)
    assert not any(series.startswith(f"{MISSES}{{") for series in _samples(_render(registry)))


def test_the_histogram_is_cumulative_with_sum_count_and_plus_inf() -> None:
    registry = _registry()
    for seconds in (0.003, 0.2, 20.0):
        _observe(registry, latency_seconds=seconds)
    samples = _samples(_render(registry))
    prefix = f'{LATENCY}_bucket{{{CONSTANTS},tool="search_symbols",le='
    assert samples[f'{prefix}"0.005"}}'] == 1
    assert samples[f'{prefix}"0.1"}}'] == 1
    assert samples[f'{prefix}"0.25"}}'] == 2
    assert samples[f'{prefix}"10.0"}}'] == 2
    assert samples[f'{prefix}"+Inf"}}'] == 3
    assert samples[f'{LATENCY}_count{{{CONSTANTS},tool="search_symbols"}}'] == 3
    assert abs(samples[f'{LATENCY}_sum{{{CONSTANTS},tool="search_symbols"}}'] - 20.203) < 1e-9
    counts = [value for series, value in samples.items() if series.startswith(f"{LATENCY}_bucket")]
    assert counts == sorted(counts)


def test_the_histogram_uses_the_documented_bucket_edges() -> None:
    assert LATENCY_BUCKETS == (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    registry = _registry()
    _observe(registry)
    edges = re.findall(r'_bucket\{[^}]*le="([^"]+)"\}', _render(registry))
    assert edges == [repr(edge) for edge in LATENCY_BUCKETS] + ["+Inf"]


def test_session_rejections_counter() -> None:
    registry = _registry()
    series = f"foss_mcp_session_rejections_total{{{CONSTANTS}}}"
    assert _samples(_render(registry))[series] == 0
    registry.count_session_rejection()
    registry.count_session_rejection()
    assert _samples(_render(registry))[series] == 2


def test_a_gauge_with_no_value_is_omitted() -> None:
    text = _render(_registry(), open_sessions=None, ready=None, generation_info=None)
    for name in ("foss_mcp_open_sessions", "foss_mcp_ready", "foss_mcp_generation_info"):
        assert name not in text
    present = _render(_registry())
    for name in ("foss_mcp_open_sessions", "foss_mcp_ready", "foss_mcp_generation_info"):
        assert name in present


def test_generation_info_is_one_with_exactly_three_labels_plus_the_constants() -> None:
    text = _render(_registry())
    lines = [line for line in text.splitlines() if line.startswith("foss_mcp_generation_info{")]
    assert len(lines) == 1
    labels = dict(re.findall(r'([a-z_]+)="([^"]*)"', lines[0].split("} ")[0]))
    assert set(labels) == {"family", "platform", "generation_id", "source_commit", "build_revision"}
    assert labels["generation_id"] == GENERATION["generation_id"]
    assert lines[0].endswith(" 1")


def test_label_values_are_escaped_for_quote_backslash_and_newline() -> None:
    hostile = 'a"b\\c\nd'
    text = _render(_registry(), generation_info={"generation_id": hostile})
    line = next(line for line in text.splitlines() if line.startswith("foss_mcp_generation_info{"))
    assert 'generation_id="a\\"b\\\\c\\nd"' in line
    assert all(_SAMPLE.match(row) for row in text.splitlines() if row and not row.startswith("#"))


def test_ready_is_zero_or_one() -> None:
    series = f"foss_mcp_ready{{{CONSTANTS}}}"
    assert _samples(_render(_registry(), ready=True))[series] == 1
    assert _samples(_render(_registry(), ready=False))[series] == 0


def test_usage_numbers_render_as_a_gauge_and_a_counter() -> None:
    text = _render(_registry(), usage_queued=7, usage_dropped=11)
    assert "# TYPE foss_mcp_usage_events_queued gauge" in text
    assert "# TYPE foss_mcp_usage_events_dropped_total counter" in text
    samples = _samples(text)
    assert samples[f"foss_mcp_usage_events_queued{{{CONSTANTS}}}"] == 7
    assert samples[f"foss_mcp_usage_events_dropped_total{{{CONSTANTS}}}"] == 11


def test_help_and_type_lines_precede_each_family() -> None:
    registry = _registry()
    _observe(registry, miss=True)
    lines = _render(registry).splitlines()
    declared: set[str] = set()
    for index, line in enumerate(lines):
        if line.startswith("# TYPE "):
            name = line.split()[2]
            assert lines[index - 1].startswith(f"# HELP {name} ")
            declared.add(name)
        elif not line.startswith("#"):
            sample = line.split("{", 1)[0]
            family = re.sub(r"_(bucket|sum|count)$", "", sample)
            assert family in declared, f"{sample} appears before its TYPE line"
    assert {CALLS, MISSES, LATENCY, "foss_mcp_session_rejections_total"} <= declared


def test_every_non_comment_line_is_a_valid_exposition_sample() -> None:
    registry = _registry()
    _observe(registry, miss=True)
    _observe(registry, tool="unknown", outcome="error", code="not_found", latency_seconds=0.0001)
    registry.count_session_rejection()
    text = _render(registry)
    assert text.endswith("\n") and not text.endswith("\n\n")
    for line in text.splitlines():
        if line.startswith("#"):
            assert re.match(r"^# (HELP|TYPE) [a-zA-Z_:][a-zA-Z0-9_:]* .+$", line), line
        else:
            assert _SAMPLE.match(line), line


def test_concurrent_observation_is_exact() -> None:
    registry = _registry()

    def work() -> None:
        for _ in range(500):
            _observe(registry, miss=True)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    samples = _samples(_render(registry))
    assert samples[f'{CALLS}{{{CONSTANTS},tool="search_symbols",outcome="success",code="ok"}}'] == 4000
    assert samples[f'{MISSES}{{{CONSTANTS},tool="search_symbols"}}'] == 4000
    assert samples[f'{LATENCY}_count{{{CONSTANTS},tool="search_symbols"}}'] == 4000
    assert samples[f'{LATENCY}_bucket{{{CONSTANTS},tool="search_symbols",le="+Inf"}}'] == 4000


def test_render_is_deterministic_for_the_same_state() -> None:
    first, second = _registry(), _registry()
    plan = [
        {"tool": "lookup"},
        {"tool": "get_symbol", "outcome": "error", "code": "not_found"},
        {"tool": "search_docs", "miss": True},
    ]
    for kwargs in plan:
        _observe(first, **kwargs)
    for kwargs in reversed(plan):
        _observe(second, **kwargs)
    assert _render(first) == _render(first)
    assert _render(first) == _render(second)
