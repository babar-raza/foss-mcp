"""Canary for the supersede rule in the write-path overlap check (2026-10-04).

TC-199 was superseded by TC-202, which carries the same write paths. Before this rule, the validator still counted the
retired card's claim and failed the successor. A superseded card no longer writes anything, so its claim must not count.
"""

from __future__ import annotations

import gatecli as C


def _card(paths):
    return {"write_paths": paths}


def test_two_live_cards_claiming_the_same_file_overlap(monkeypatch):
    monkeypatch.setattr(C, "_superseded_card_ids", lambda: set())
    cards = {"TC-A": _card(["src/x.py"]), "TC-B": _card(["src/x.py"])}
    assert C._overlap_problems(cards), "two live cards on one file must still be reported"


def test_a_superseded_card_does_not_overlap_its_successor(monkeypatch):
    monkeypatch.setattr(C, "_superseded_card_ids", lambda: {"TC-A"})
    cards = {"TC-A": _card(["src/x.py"]), "TC-B": _card(["src/x.py"])}
    assert C._overlap_problems(cards) == []
