"""A stdlib-only Prometheus text exposition for one serving container (G3/TC-509, REQ-G3-008).

The container serves exactly one product (a ``family`` and a ``platform``), so those two values are
CONSTANT labels on every series and are fixed when the registry is built. Every other label is
bounded by construction: ``tool`` is a registered tool name or the literal ``unknown`` (the caller
collapses a client-chosen name before it gets here), ``outcome`` is ``success`` or ``error`` and
``code`` is one of ``ok``, ``not_found``, ``invalid_argument``, ``internal``, ``unavailable``. A query
text, an argument or an identifier a client chose is never a label.

``MetricsRegistry`` holds the in-process counters and the latency histogram behind one lock, so the
worker threads and the event loop can all observe at once. The gauges that are read at scrape time
(open sessions, readiness, the active generation, the usage queue) are NOT stored here: the scraper
passes them to ``render_prometheus``, which omits a gauge whose value is ``None``.

Production caller: ``foss_mcp.mcp.server.create_server`` observes every tool call into the registry
and ``infra/serve_http.py`` renders it on ``/metrics`` when the scraper asks for Prometheus text.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping

TOOL_CALLS_TOTAL = "foss_mcp_tool_calls_total"
TOOL_MISSES_TOTAL = "foss_mcp_tool_misses_total"
TOOL_LATENCY_SECONDS = "foss_mcp_tool_latency_seconds"
OPEN_SESSIONS = "foss_mcp_open_sessions"
SESSION_REJECTIONS_TOTAL = "foss_mcp_session_rejections_total"
GENERATION_INFO = "foss_mcp_generation_info"
READY = "foss_mcp_ready"
USAGE_EVENTS_QUEUED = "foss_mcp_usage_events_queued"
USAGE_EVENTS_DROPPED_TOTAL = "foss_mcp_usage_events_dropped_total"

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_CallKey = tuple[str, str, str]  # (tool, outcome, code)


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _number(value: float) -> str:
    """An integer prints as an integer, anything else as its shortest round-trip form."""
    if isinstance(value, int):
        return str(value)
    return repr(float(value))


class _Histogram:
    """Non-cumulative bucket counts plus sum and count for one tool."""

    __slots__ = ("buckets", "count", "total")

    def __init__(self) -> None:
        self.buckets = [0] * len(LATENCY_BUCKETS)
        self.total = 0.0
        self.count = 0

    def observe(self, seconds: float) -> None:
        self.total += seconds
        self.count += 1
        for index, edge in enumerate(LATENCY_BUCKETS):
            if seconds <= edge:
                self.buckets[index] += 1
                return


class MetricsRegistry:
    """Counters and the latency histogram of one serving container, safe to share across threads."""

    def __init__(self, *, family: str, platform: str) -> None:
        self._family = family
        self._platform = platform
        self._lock = threading.Lock()
        self._calls: dict[_CallKey, int] = {}
        self._misses: dict[str, int] = {}
        self._latency: dict[str, _Histogram] = {}
        self._rejections = 0

    @property
    def family(self) -> str:
        return self._family

    @property
    def platform(self) -> str:
        return self._platform

    def observe_tool_call(
        self, *, tool: str, outcome: str, code: str, latency_seconds: float, miss: bool
    ) -> None:
        """Count one finished tool call: the calls counter, the latency histogram and, when *miss*,
        the misses counter. A negative or non-finite latency is recorded as zero."""
        seconds = latency_seconds if math.isfinite(latency_seconds) and latency_seconds > 0 else 0.0
        with self._lock:
            key = (tool, outcome, code)
            self._calls[key] = self._calls.get(key, 0) + 1
            self._latency.setdefault(tool, _Histogram()).observe(seconds)
            if miss:
                self._misses[tool] = self._misses.get(tool, 0) + 1

    def count_session_rejection(self) -> None:
        """Count one MCP session refused at the session cap."""
        with self._lock:
            self._rejections += 1

    def _constant_labels(self) -> list[tuple[str, str]]:
        return [("family", self._family), ("platform", self._platform)]

    def _series(self, name: str, extra: list[tuple[str, str]], value: float, suffix: str = "") -> str:
        pairs = self._constant_labels() + extra
        labels = ",".join(f'{key}="{_escape_label_value(val)}"' for key, val in pairs)
        return f"{name}{suffix}{{{labels}}} {_number(value)}"

    def render_prometheus(
        self,
        *,
        open_sessions: int | None,
        ready: bool | None,
        generation_info: Mapping[str, str] | None,
        usage_queued: int,
        usage_dropped: int,
    ) -> str:
        """The Prometheus text exposition (version 0.0.4) of everything this registry knows plus the
        scrape-time values passed in. Series are sorted so the same state renders the same text; a
        gauge whose value is ``None`` is omitted entirely."""
        with self._lock:
            calls = sorted(self._calls.items())
            misses = sorted(self._misses.items())
            latency = sorted((tool, list(h.buckets), h.total, h.count) for tool, h in self._latency.items())
            rejections = self._rejections

        lines: list[str] = []

        def family_header(name: str, kind: str, help_text: str) -> None:
            lines.append(f"# HELP {name} {_escape_help(help_text)}")
            lines.append(f"# TYPE {name} {kind}")

        family_header(TOOL_CALLS_TOTAL, "counter", "Tool calls finished, by tool, outcome and error code.")
        for (tool, outcome, code), value in calls:
            lines.append(
                self._series(TOOL_CALLS_TOTAL, [("tool", tool), ("outcome", outcome), ("code", code)], value)
            )

        family_header(
            TOOL_MISSES_TOTAL, "counter", "Retrieval tool calls that honestly found nothing, by tool."
        )
        for tool, value in misses:
            lines.append(self._series(TOOL_MISSES_TOTAL, [("tool", tool)], value))

        family_header(TOOL_LATENCY_SECONDS, "histogram", "Tool call latency in seconds, by tool.")
        for tool, buckets, total, count in latency:
            running = 0
            for edge, in_bucket in zip(LATENCY_BUCKETS, buckets, strict=True):
                running += in_bucket
                lines.append(
                    self._series(
                        TOOL_LATENCY_SECONDS, [("tool", tool), ("le", repr(edge))], running, "_bucket"
                    )
                )
            lines.append(
                self._series(TOOL_LATENCY_SECONDS, [("tool", tool), ("le", "+Inf")], count, "_bucket")
            )
            lines.append(self._series(TOOL_LATENCY_SECONDS, [("tool", tool)], total, "_sum"))
            lines.append(self._series(TOOL_LATENCY_SECONDS, [("tool", tool)], count, "_count"))

        if open_sessions is not None:
            family_header(OPEN_SESSIONS, "gauge", "MCP sessions currently open.")
            lines.append(self._series(OPEN_SESSIONS, [], open_sessions))

        family_header(
            SESSION_REJECTIONS_TOTAL,
            "counter",
            "New MCP sessions refused because the session cap was reached.",
        )
        lines.append(self._series(SESSION_REJECTIONS_TOTAL, [], rejections))

        if ready is not None:
            family_header(READY, "gauge", "1 when the deployment passes its readiness check, else 0.")
            lines.append(self._series(READY, [], 1 if ready else 0))

        if generation_info is not None:
            family_header(
                GENERATION_INFO, "gauge", "The active generation: always 1, the details are the labels."
            )
            lines.append(self._series(GENERATION_INFO, sorted(generation_info.items()), 1))

        family_header(USAGE_EVENTS_QUEUED, "gauge", "Usage events currently held in the in-memory ring.")
        lines.append(self._series(USAGE_EVENTS_QUEUED, [], usage_queued))

        family_header(
            USAGE_EVENTS_DROPPED_TOTAL,
            "counter",
            "Usage events evicted or refused because the ring was full.",
        )
        lines.append(self._series(USAGE_EVENTS_DROPPED_TOTAL, [], usage_dropped))

        return "\n".join(lines) + "\n"
