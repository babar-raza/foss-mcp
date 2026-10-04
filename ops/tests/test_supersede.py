"""Canary tests for the D6 SUPERSEDED state (DECISION_LOG 2026-10-04).

Derived state is built from a small, controlled set of cards, instructions and receipts.
The three rules are: a supersession holds only while the successor is ACCEPTED; a superseded
card satisfies the cards that depend on it; and it does not hide unfinished work.
"""

from __future__ import annotations

import json

import gateverify as V
import pytest


@pytest.fixture
def world(tmp_path, monkeypatch):
    cards = {
        "TC-001": {"id": "TC-001", "gate": "G2", "depends_on": []},
        "TC-002": {"id": "TC-002", "gate": "G2", "depends_on": []},
        "TC-003": {"id": "TC-003", "gate": "G2", "depends_on": ["TC-001"]},
    }
    receipts = {}
    instructions = tmp_path / "instructions.jsonl"
    monkeypatch.setattr(V, "all_cards", lambda: cards)
    monkeypatch.setattr(V, "load_receipt", lambda gate, card: receipts.get(card))
    monkeypatch.setattr(V, "load_env_blocked", lambda gate, card: None)
    monkeypatch.setattr(V, "load_owner_items", lambda: [])
    monkeypatch.setattr(V, "INSTRUCTIONS_JSONL", instructions)
    monkeypatch.setattr(V, "QUESTIONS_JSONL", tmp_path / "none.jsonl")

    def set_instructions(lines):
        instructions.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")

    return cards, receipts, set_instructions


def _rows(state):
    return {r["id"]: r for r in state["cards"]}


SUPERSEDE_001_BY_002 = {
    "ts": "2026-10-04T10:00:00Z", "target_card": "TC-001", "kind": "supersede",
    "instruction": "carried by TC-002", "card_sha256": "NONE", "issue_rev": "NONE",
    "attempt": 0, "successor": "TC-002",
}


def test_a_supersession_holds_once_the_successor_is_accepted(world):
    _, receipts, set_instructions = world
    set_instructions([SUPERSEDE_001_BY_002])
    receipts["TC-002"] = {"accepted": True, "head_rev": "a" * 40, "gate": "G2", "card": "TC-002"}
    rows = _rows(V.rebuild_state())
    assert rows["TC-001"]["status"] == "SUPERSEDED"
    assert rows["TC-002"]["status"] == "ACCEPTED"


def test_a_supersession_does_not_hold_while_the_successor_is_unaccepted(world):
    _, _, set_instructions = world
    set_instructions([SUPERSEDE_001_BY_002])
    rows = _rows(V.rebuild_state())
    assert rows["TC-001"]["status"] != "SUPERSEDED", "an unaccepted successor must never retire a card"


def test_a_superseded_card_satisfies_its_dependants(world):
    _, receipts, set_instructions = world
    set_instructions([SUPERSEDE_001_BY_002])
    receipts["TC-002"] = {"accepted": True, "head_rev": "a" * 40, "gate": "G2", "card": "TC-002"}
    rows = _rows(V.rebuild_state())
    assert rows["TC-003"]["status"] == "READY", "a dependency on a superseded card must be released"


def test_an_unsuperseded_dependency_still_blocks_its_dependant(world):
    _, _, set_instructions = world
    set_instructions([])
    rows = _rows(V.rebuild_state())
    assert rows["TC-003"]["status"] == "PENDING"


def test_the_superseded_state_and_kind_are_in_the_schemas():
    from pathlib import Path

    repo = Path(V.__file__).resolve().parents[1]  # ops/gateverify.py -> repo root
    state = json.loads((repo / "schemas" / "state.schema.json").read_text(encoding="utf-8"))
    inst = json.loads((repo / "schemas" / "instruction-line.schema.json").read_text(encoding="utf-8"))
    assert "SUPERSEDED" in state["properties"]["cards"]["items"]["properties"]["status"]["enum"]
    assert "supersede" in inst["properties"]["kind"]["enum"]
    assert "successor" in inst["properties"]
