"""G2/TC-223: the pilot release mapping in scripts/deploy/pilots.py, offline.

The chart's own values file is read, so the fourteen releases are the chart's fourteen pilots. No
helm or kubectl runs here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
PILOTS_SOURCE = DEPLOY_DIR / "pilots.py"

sys.path.insert(0, str(DEPLOY_DIR))

import pilots  # noqa: E402

# The fourteen release names the chart's pilots map to, pinned here so that a change to the prefix
# or to the naming rule fails a test rather than silently renaming every release.
PINNED_RELEASES = [
    "foss-mcp-pdf-net",
    "foss-mcp-slides-python",
    "foss-mcp-pdf-typescript",
    "foss-mcp-pdf-go",
    "foss-mcp-pdf-java",
    "foss-mcp-cells-rust",
    "foss-mcp-pdf-cpp",
    "foss-mcp-words-python",
    "foss-mcp-words-net",
    "foss-mcp-slides-java",
    "foss-mcp-cells-go",
    "foss-mcp-cells-typescript",
    "foss-mcp-jmap-rust",
    "foss-mcp-cells-cpp",
    "foss-mcp-cells-net",
    "foss-mcp-cells-python",
    "foss-mcp-jmap-python",
    "foss-mcp-pdf-python",
    "foss-mcp-slides-cpp",
    "foss-mcp-slides-net",
    "foss-mcp-email-python",
    "foss-mcp-email-cpp",
    "foss-mcp-email-net",
    "foss-mcp-barcode-python",
    "foss-mcp-note-python",
    "foss-mcp-html-python",
    "foss-mcp-page-python",
    "foss-mcp-imaging-net",
]


@pytest.fixture(scope="module")
def chart_pilots() -> list[dict]:
    return pilots.load_pilots(pilots.CHART_VALUES)


def test_chart_declares_exactly_twenty_eight_pilots(chart_pilots):
    assert len(chart_pilots) == 28
    assert pilots.PILOT_COUNT == 28


def test_release_names_are_the_pinned_twenty_eight(chart_pilots):
    assert [pilots.release_name(p) for p in chart_pilots] == PINNED_RELEASES


def test_release_names_are_unique_and_carry_the_prefix(chart_pilots):
    names = [pilots.release_name(p) for p in chart_pilots]
    assert len(set(names)) == len(names) == 28
    assert all(name.startswith(pilots.RELEASE_PREFIX) for name in names)
    assert all(name.startswith("foss-mcp-") for name in names)


def test_release_name_is_prefix_family_and_platform(chart_pilots):
    pdf_net = next(p for p in chart_pilots if pilots.pilot_key(p) == "pdf_net")
    assert pilots.release_name(pdf_net) == "foss-mcp-pdf-net"


def test_pilot_key_is_family_underscore_platform(chart_pilots):
    keys = [pilots.pilot_key(p) for p in chart_pilots]
    assert len(set(keys)) == 28
    assert "cells_rust" in keys and "jmap_rust" in keys


def test_pilot_values_hold_one_pilot_and_the_deployment_identity(chart_pilots):
    pilot = chart_pilots[0]
    values = pilots.pilot_values(pilot)
    assert set(values) == {"deployment", "ingestion"}
    assert values["deployment"] == {
        "family": pilot["family"],
        "platform": pilot["platform"],
        "sourceKind": pilot["sourceKind"],
    }
    assert values["ingestion"] == {"pilots": [pilot]}


def test_write_values_writes_one_file_per_pilot_that_round_trips(tmp_path, chart_pilots):
    paths = pilots.write_values(chart_pilots, tmp_path / "values")
    assert len(paths) == 28
    assert len({p.name for p in paths}) == 28
    for pilot, path in zip(chart_pilots, paths, strict=True):
        assert path.parent == tmp_path / "values"
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == pilots.pilot_values(pilot)


def test_select_pilots_without_only_returns_all_twenty_eight(chart_pilots):
    assert pilots.select_pilots(chart_pilots, []) == chart_pilots


def test_select_pilots_honours_only_and_rejects_an_unknown_name(chart_pilots):
    selected = pilots.select_pilots(chart_pilots, ["pdf_net", "cells_cpp"])
    assert [pilots.pilot_key(p) for p in selected] == ["pdf_net", "cells_cpp"]
    with pytest.raises(ValueError, match="unknown pilot"):
        pilots.select_pilots(chart_pilots, ["pdf_nope"])


def test_load_pilots_refuses_a_values_file_without_pilots(tmp_path):
    values = tmp_path / "values.yaml"
    values.write_text("ingestion: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no ingestion.pilots"):
        pilots.load_pilots(values)


def test_release_prefix_text_appears_only_in_the_prefix_constant():
    text = PILOTS_SOURCE.read_text(encoding="utf-8")
    assert text.count("foss-mcp-") == 1
    assert 'RELEASE_PREFIX = "foss-mcp-"' in text
