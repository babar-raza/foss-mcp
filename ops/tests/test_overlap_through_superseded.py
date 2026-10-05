"""Canary: a dependency chain through a superseded card still orders its endpoints (2026-10-05).

_overlap_problems used to drop superseded cards before computing ancestry. Superseding four cards at
once then made accepted cards that were ordered by a chain through a retired card look concurrent, and
validate reported races that could not happen. A retired card is not a racing participant, but it stays
in the dependency graph.
"""

from __future__ import annotations

import gatecli as C


def _card(cid, deps=(), paths=()):
    return {"id": cid, "depends_on": list(deps), "write_paths": list(paths)}


def test_a_chain_through_a_superseded_card_still_orders_its_endpoints(monkeypatch):
    monkeypatch.setattr(C, "_superseded_card_ids", lambda: {"TC-MID"})
    cards = {
        "TC-A": _card("TC-A", paths=["src/shared/x.py"]),
        "TC-MID": _card("TC-MID", deps=["TC-A"]),
        "TC-B": _card("TC-B", deps=["TC-MID"], paths=["src/shared/x.py"]),
    }
    assert C._overlap_problems(cards) == [], "B is ordered after A through the retired MID"


def test_two_unordered_cards_still_race_when_a_retired_card_is_elsewhere(monkeypatch):
    monkeypatch.setattr(C, "_superseded_card_ids", lambda: {"TC-OLD"})
    cards = {
        "TC-A": _card("TC-A", paths=["src/shared/x.py"]),
        "TC-B": _card("TC-B", paths=["src/shared/x.py"]),
        "TC-OLD": _card("TC-OLD", paths=["src/shared/x.py"]),
    }
    problems = C._overlap_problems(cards)
    assert any("TC-A and TC-B" in p for p in problems), "unordered live cards must still be flagged"
    assert not any("TC-OLD" in p for p in problems), "a retired card claims nothing"
