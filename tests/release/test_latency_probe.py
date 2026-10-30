"""G3/TC-599 (REQ-G3-016): proves ``scripts/release/latency_probe.py`` against real baked pilots.

Runs a small, fast N (10-20, never the CLI's own real default of 500) so this stays an offline
unit test, not a slow benchmark. Asserts structural properties and a generous latency bound - never
exact wall-clock values, since timing is inherently noisy and an equality assertion on it would make
this test itself the next source of flakiness.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "release"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import latency_probe as lp  # noqa: E402

# A small pilot (prior research found imaging/net has only 8 documents) and a larger one (pdf/java),
# per the card's own instruction to pick "a couple of real pilots... one small... and one larger".
SMALL_PILOT = "imaging__net"
LARGER_PILOT = "pdf__java"
PILOTS = (SMALL_PILOT, LARGER_PILOT)

# Small N to keep this an offline unit test, not a benchmark - the card asks for 10-20, not the CLI's
# real default of 500.
N_FAST = 15

# The card's own example bound was 500ms, based on prior research claiming 0.5-83ms even on the
# largest pilot's index. Measuring FRESH with this very script: that number does not hold for
# pdf/java (the card's own suggested "larger" pilot, ~5.2MB / thousands of documents) on the
# corpus-scanning tools (lookup, search_symbols, find_examples, search_docs) - repeated real calls
# measured p50 ~150-300ms with worst-observed single-call outliers up to ~850ms. get_symbol, which
# does a cheap exact-FQN-equality scan instead of a lexical/BM25 scoring pass, stayed at ~2ms - so
# the cost is specific to the scanning tools recomputing their ranking fresh on every call, with no
# persistent cross-call index cache (only the raw parsed JSON is cached - see
# GenerationManifestStore._cached_generation_document).
#
# This is itself the honest finding the card's own recovery instructions ask for: the bound below
# is raised from the card's 500ms EXAMPLE to 2000ms, grounded in the real worst-observed number
# above plus a margin for a slower machine - never raised merely because a run failed. 2000ms keeps
# a comfortable >2x margin over the worst real call observed, while staying more than 3x below the
# round-2 audit's lowest observed hosting-layer stall (7s) and over 20x below its max (42s) - still
# nowhere near order-of-magnitude range of that tail, so the in-process-compute hypothesis (the
# stall lives outside this repository's own code) still holds even though the originally-suggested
# 500ms number does not.
P99_BOUND_MS = 2000.0


def _assert_valid_report_shape(report: dict[str, dict[str, float]], expected_pilots: tuple[str, ...]) -> None:
    assert set(report) == set(expected_pilots)
    for pilot_name, stats in report.items():
        assert set(stats) == {"p50", "p95", "p99", "max"}, pilot_name
        for key, value in stats.items():
            assert isinstance(value, float), (pilot_name, key, value)
            assert value >= 0.0, (pilot_name, key, value)
        assert stats["p50"] <= stats["p95"] <= stats["p99"] <= stats["max"], (pilot_name, stats)


@pytest.fixture(scope="module")
def lookup_report() -> dict[str, dict[str, float]]:
    return lp.run_probe(lp.DEMO_MANIFESTS, list(PILOTS), "lookup", "open a file", N_FAST)


def test_the_report_has_a_valid_shape_on_two_real_pilots(lookup_report: dict[str, dict[str, float]]) -> None:
    _assert_valid_report_shape(lookup_report, PILOTS)


def test_p99_in_process_latency_is_within_the_justified_bound(
    lookup_report: dict[str, dict[str, float]],
) -> None:
    # If this ever fails, do NOT raise the bound: per the card's own recovery instructions, a p99
    # anywhere near the audit's observed 7-42 second tail is itself the finding, not a bug in the
    # test - report it honestly and stop.
    for pilot_name, stats in lookup_report.items():
        assert stats["p99"] < P99_BOUND_MS, (pilot_name, stats)


def test_running_the_probe_twice_stays_structurally_valid_and_does_not_crash() -> None:
    # Timing is inherently noisy - this asserts shape and bound validity on each independent run,
    # never equality between the two runs' actual numbers.
    first = lp.run_probe(lp.DEMO_MANIFESTS, [SMALL_PILOT], "lookup", "open a file", N_FAST)
    second = lp.run_probe(lp.DEMO_MANIFESTS, [SMALL_PILOT], "lookup", "open a file", N_FAST)
    for report in (first, second):
        _assert_valid_report_shape(report, (SMALL_PILOT,))
        assert report[SMALL_PILOT]["p99"] < P99_BOUND_MS


def test_get_symbol_resolves_a_real_fqn_and_measures_it_within_the_bound() -> None:
    # get_symbol matches by exact FQN, not free text - run_probe must substitute a real resolved
    # FQN per pilot (see lp._resolve_text) rather than handing it the free-text --query default.
    report = lp.run_probe(lp.DEMO_MANIFESTS, list(PILOTS), "get_symbol", "irrelevant free text", N_FAST)
    _assert_valid_report_shape(report, PILOTS)
    for pilot_name, stats in report.items():
        assert stats["p99"] < P99_BOUND_MS, (pilot_name, stats)


@pytest.mark.parametrize("tool_name", ["find_examples", "search_symbols", "search_docs"])
def test_every_other_tool_also_produces_a_valid_in_bound_report(tool_name: str) -> None:
    report = lp.run_probe(lp.DEMO_MANIFESTS, [SMALL_PILOT], tool_name, "open a file", N_FAST)
    _assert_valid_report_shape(report, (SMALL_PILOT,))
    assert report[SMALL_PILOT]["p99"] < P99_BOUND_MS


def test_the_cli_writes_a_valid_json_report_file(tmp_path: Path) -> None:
    report_path = tmp_path / "latency.json"
    exit_code = lp.main(
        [
            "--pilots",
            ",".join(PILOTS),
            "--tool",
            "lookup",
            "--query",
            "open a file",
            "--n",
            str(N_FAST),
            "--report",
            str(report_path),
        ]
    )
    assert exit_code == 0
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    _assert_valid_report_shape(loaded, PILOTS)


def test_an_unknown_pilot_name_is_a_clear_error_not_a_crash() -> None:
    exit_code = lp.main(["--pilots", "not__a__real__pilot", "--n", str(N_FAST)])
    assert exit_code == 2


def test_the_real_demo_manifests_directory_is_never_written_to() -> None:
    before = sorted(str(path.relative_to(lp.DEMO_MANIFESTS)) for path in lp.DEMO_MANIFESTS.rglob("*"))
    lp.run_probe(lp.DEMO_MANIFESTS, [SMALL_PILOT], "lookup", "open a file", N_FAST)
    after = sorted(str(path.relative_to(lp.DEMO_MANIFESTS)) for path in lp.DEMO_MANIFESTS.rglob("*"))
    assert before == after
