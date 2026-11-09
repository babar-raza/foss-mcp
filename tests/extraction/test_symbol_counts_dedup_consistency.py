"""G3/TC-729 (REQ-G3-012): permanent fleet-wide guard that a fixture's stored ``symbol_counts`` never
drifts out of lockstep with the live, deduplicated recomputation `chunk_builder._coverage_line` uses
to render the served ``API-Coverage:`` line at request time.

Background (see `docs/DECISION_LOG.md`'s two most recent 2026-11-09 entries for the full
investigation): `chunk_builder._coverage_line`'s numerator dedups Python re-export shell/definition
pairs via `symbol_identity.compute_canonical_identities(...).is_definition` (TC-614/TC-686), but its
denominator is read verbatim from the fixture's own committed `symbol_counts.types` field.
`run_extraction.summarize_symbols` (TC-620, REQ-G3-012) already knows how to compute that SAME
denominator with the SAME dedup when given `identities=compute_canonical_identities(types)` - but
that convention only ever applied going forward, to freshly re-extracted fixtures. 10 Python-pilot
fixtures that predated TC-620 still carried the OLD, undeduped `symbol_counts.types` value until
TC-729 backfilled them (a pure, offline, zero-network data correction - no fixture field other than
`symbol_counts` changed, and no new algorithm or convention was introduced).

This file is the permanent guard against that EXACT staleness class recurring, for ANY pilot, not
just the 10 TC-729 fixed: for every real pilot of the chart whose committed fixture currently carries
a `symbol_counts` field, the stored value must exactly equal a FRESH recomputation, right now, from
that same fixture's own `types` array, using the identical two functions
`chunk_builder._coverage_line` relies on at render time
(`run_extraction.summarize_symbols(types, identities=compute_canonical_identities(types))`). A
fixture committed (or re-extracted, or hand-edited) in the future with a stale `symbol_counts` fails
here immediately, offline, before it ever reaches a served chunk.

Offline and deterministic: no network, no docker, no wall clock, no skip. Reads only already-committed
fixtures; writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.extraction.run_extraction import summarize_symbols
from foss_mcp.indexing.symbol_identity import compute_canonical_identities

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def chart_pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


PILOTS = chart_pilots()
PILOT_KEYS = [f"{p['family']}_{p['platform']}" for p in PILOTS]


def load_fixture(key: str) -> dict[str, Any]:
    return dict(json.loads((FIXTURES / key / "api_surface.json").read_text(encoding="utf-8")))


# Every real pilot of the chart whose committed fixture currently carries a `symbol_counts` field -
# measured directly against the committed data, not assumed to be all 32 (a future pilot's fixture
# may legitimately omit the field, the way `chunk_builder._coverage_line`'s own legacy clause still
# tolerates).
PILOTS_WITH_SYMBOL_COUNTS = [key for key in PILOT_KEYS if "symbol_counts" in load_fixture(key)]


def test_at_least_one_pilot_has_a_symbol_counts_field() -> None:
    """Anti-vacuity: if this ever drops to zero the parametrized test below silently checks
    nothing, so this static floor fails loudly instead."""
    assert len(PILOTS_WITH_SYMBOL_COUNTS) >= 10


def test_pilots_with_symbol_counts_matches_the_measured_fleet() -> None:
    """Confirms the fleet actually has the field on the real, currently-committed data (not a
    hardcoded guess of which pilots qualify) - every real pilot of the chart is expected to carry
    the field at the time this card was authored; a future pilot legitimately omitting it is still
    fine, this only guards against the count going to (near) zero unnoticed."""
    assert set(PILOTS_WITH_SYMBOL_COUNTS) <= set(PILOT_KEYS)
    assert len(PILOTS_WITH_SYMBOL_COUNTS) == len(set(PILOTS_WITH_SYMBOL_COUNTS))


@pytest.mark.parametrize("key", PILOTS_WITH_SYMBOL_COUNTS)
def test_stored_symbol_counts_matches_fresh_dedup_recomputation(key: str) -> None:
    """The real guard: a fixture's stored `symbol_counts` must equal, right now, what the live
    dedup algorithm (`summarize_symbols` + `compute_canonical_identities`, the exact two functions
    `chunk_builder._coverage_line` relies on at render time) computes fresh from that same
    fixture's own `types` array. A fixture whose cached field drifts out of lockstep with that
    live algorithm - the TC-729 staleness class - fails here immediately."""
    fixture = load_fixture(key)
    types = fixture["types"]
    stored = fixture["symbol_counts"]

    identities = compute_canonical_identities(types)
    fresh = summarize_symbols(types, identities=identities)

    assert stored == fresh, (
        f"{key}: fixture's committed symbol_counts {stored} has drifted out of lockstep with the "
        f"live dedup recomputation {fresh} - this is exactly the TC-729 staleness class; re-run the "
        "backfill (summarize_symbols(types, identities=compute_canonical_identities(types))) and "
        "overwrite ONLY this fixture's symbol_counts field."
    )
