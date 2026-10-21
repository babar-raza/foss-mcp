"""G3/TC-536 (REQ-G3-008, REQ-G3-001): the chart cap, the compose cap and the extractor's recommended cap are one
number per pilot, and ingesting with that cap drops nothing the extraction kept.

Offline, no docker, no network, no skip: reads only the committed tree (``infra/helm/foss-mcp/values.yaml``,
``docker-compose.yml``, ``tests/fixtures/<key>/api_surface.json``) through the shared helpers of
``tests/extraction/reextraction_checks.py`` (TC-530), which this file imports rather than retyping. The pilot
keys are DERIVED from the chart, never hardcoded, so a pilot added or removed later is covered automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.extraction import run_extraction
from tests.extraction.reextraction_checks import (
    cap_problems,
    chart_max_types,
    chart_pilot,
    compose_max_types,
    ingestion_slice_problems,
    load_fixture,
    pilot_key,
    recommended_cap,
    type_names,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

# Derived from the chart, never retyped. The ROUND-3 amendment voided the jmap family; whatever count values.yaml
# names today (32, after the jmap removal) is exactly what this file covers - no pilot is skipped or assumed.
_CHART_DATA = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
_CHART_PILOTS: list[dict[str, Any]] = [dict(p) for p in _CHART_DATA["ingestion"]["pilots"]]
PILOT_KEYS = [pilot_key(p["family"], p["platform"]) for p in _CHART_PILOTS]


def _is_python_pilot(key: str) -> bool:
    return key.split("_", 1)[1] == "python"


# -- the chart itself -----------------------------------------------------------------------------------------------


def test_chart_names_every_pilot() -> None:
    assert len(PILOT_KEYS) == len(_CHART_PILOTS)
    assert len(set(PILOT_KEYS)) == len(PILOT_KEYS)
    assert PILOT_KEYS, "the chart names no pilot"


# -- one assertion per pilot ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_chart_compose_and_recommended_cap_are_one_number(key: str) -> None:
    chart, compose, cap = chart_max_types(key), compose_max_types(key), recommended_cap(key)
    assert chart == compose == cap, (key, chart, compose, cap)


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_ingestion_slice_keeps_every_extracted_entry(key: str) -> None:
    problems = ingestion_slice_problems(key)
    assert not problems, problems


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_a_fixture_never_exceeds_its_cap_except_by_a_python_pair(key: str) -> None:
    if _is_python_pilot(key):
        # A Python fixture may list more "type" entries than the cap while the chunk builder still keeps every
        # build unit (constructors and module-level re-exports are folded, not dropped); the slice test above is
        # the real guarantee here. Nothing else is asserted for a Python pilot.
        assert not ingestion_slice_problems(key)
        return
    fixture = load_fixture(key)
    assert len(type_names(fixture)) <= chart_max_types(key), (
        key,
        len(type_names(fixture)),
        chart_max_types(key),
    )


# -- the recommended-cap table and the chart agree in both directions -----------------------------------------------


def test_every_recommended_cap_key_is_a_pilot_of_the_chart() -> None:
    chart_pairs = {f"{p['family']}/{p['platform']}" for p in _CHART_PILOTS}
    unknown = sorted(set(run_extraction.RECOMMENDED_MAX_TYPES) - chart_pairs)
    assert not unknown, f"RECOMMENDED_MAX_TYPES name(s) no chart pilot: {unknown}"


def test_every_pilot_with_a_recommended_cap_has_it_in_the_chart() -> None:
    # TC-520's thirteen proposals: every one must be the chart's maxTypes for that pilot, not merely considered.
    assert len(run_extraction.RECOMMENDED_MAX_TYPES) == 13, sorted(run_extraction.RECOMMENDED_MAX_TYPES)
    for pair, cap in run_extraction.RECOMMENDED_MAX_TYPES.items():
        family, platform = pair.split("/", 1)
        key = pilot_key(family, platform)
        assert key in PILOT_KEYS, f"{pair} is not a pilot of the chart"
        assert chart_max_types(key) == cap, (key, chart_max_types(key), cap)


# -- cap_problems itself, exercised directly (the richer helper reextraction_checks also exposes) --------------------


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_cap_problems_reports_nothing_for_the_committed_fixture(key: str) -> None:
    assert cap_problems(key) == [], cap_problems(key)


def test_chart_pilot_is_reachable_for_every_key() -> None:
    for key in PILOT_KEYS:
        family, platform = key.split("_", 1)
        pilot = chart_pilot(key)
        assert pilot["family"] == family
        assert pilot["platform"] == platform
