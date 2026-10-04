"""Canary tests for per-card worker dispatch (DECISION_LOG 2026-10-04, parallel workers).

Before this change, `worker-tick` printed the single most recent open dispatch across all cards,
so two workers could never be dispatched at once. These tests prove that each card's worker
sees only its own dispatch, that closing one card leaves the others open, that the commit guard
sees every open dispatch, and that the channel is read from the main checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

import gatecli as C
import gatectl as G
import pytest


def _line(ts, card, attempt=1, kind="dispatch"):
    return {"ts": ts, "target_card": card, "kind": kind, "instruction": "x",
            "card_sha256": "0" * 64, "issue_rev": "a" * 40, "attempt": attempt}


def _status(card, attempt=1, phase="committed"):
    return {"ts": "2026-10-04T10:00:00Z", "card": card, "phase": phase, "verdict": "pass",
            "summary": "x", "commit": "b" * 40, "attempt": attempt}


@pytest.fixture
def channel(tmp_path, monkeypatch):
    main = tmp_path / "main"
    (main / "ops").mkdir(parents=True)
    (main / ".git").mkdir()
    instructions = main / "ops" / "instructions.jsonl"
    status = tmp_path / "status.jsonl"
    status.write_text("", encoding="utf-8")
    monkeypatch.setattr(C, "_main_checkout", lambda: main)
    monkeypatch.setattr(G, "STATUS_JSONL", status)

    def write(instr_lines, status_lines=()):
        instructions.write_text("".join(json.dumps(x) + "\n" for x in instr_lines), encoding="utf-8")
        status.write_text("".join(json.dumps(x) + "\n" for x in status_lines), encoding="utf-8")

    return write


def test_each_card_sees_only_its_own_open_dispatch(channel):
    channel([_line("2026-10-04T10:00:00Z", "TC-A"), _line("2026-10-04T10:05:00Z", "TC-B")])
    assert C._open_dispatch("TC-A")["target_card"] == "TC-A"
    assert C._open_dispatch("TC-B")["target_card"] == "TC-B"


def test_without_a_card_the_most_recent_open_dispatch_is_returned_as_before(channel):
    channel([_line("2026-10-04T10:00:00Z", "TC-A"), _line("2026-10-04T10:05:00Z", "TC-B")])
    assert C._open_dispatch()["target_card"] == "TC-B", "legacy single-worker behaviour must not change"


def test_closing_one_card_leaves_the_others_open(channel):
    channel(
        [_line("2026-10-04T10:00:00Z", "TC-A"), _line("2026-10-04T10:05:00Z", "TC-B")],
        [_status("TC-A")],
    )
    assert C._open_dispatch("TC-A") is None, "a committed card must not stay open"
    assert C._open_dispatch("TC-B") is not None, "another card's commit must not close this one"


def test_a_card_with_no_dispatch_is_not_open(channel):
    channel([_line("2026-10-04T10:00:00Z", "TC-A")])
    assert C._open_dispatch("TC-Z") is None


def test_the_commit_guard_sees_every_open_dispatch_not_just_the_newest(channel):
    channel(
        [_line("2026-10-04T10:00:00Z", "TC-A"), _line("2026-10-04T10:05:00Z", "TC-B")],
        [_status("TC-B")],
    )
    open_cards = [ins["target_card"] for ins in C._all_open_dispatches()]
    assert open_cards == ["TC-A"], "an earlier open dispatch must still block a supervisor commit"


def test_the_channel_is_read_from_the_main_checkout(tmp_path, monkeypatch):
    # The main checkout is the one with the .git directory, and that is where the channel lives.
    # A worker worktree must not read its own stale copy.
    root = Path(__file__).resolve().parents[2]
    assert (C._main_checkout() / ".git").exists()
    assert C._main_checkout().resolve() == root.resolve()


def test_the_worker_tick_path_is_per_card():
    import inspect

    src = inspect.getsource(C.cmd_worker_tick)
    assert 'getattr(args, "card", None)' in src, "worker-tick must select its own card's dispatch"
    assert "_all_open_dispatches()" in inspect.getsource(C.cmd_commit_guard), (
        "the commit guard must consult every open dispatch"
    )


def test_the_cap_in_the_schema_matches_the_derived_state():
    import gateverify as V

    schema = json.loads((Path(V.__file__).resolve().parents[1] / "schemas" / "state.schema.json").read_text(encoding="utf-8"))
    cap = schema["properties"]["execution_limits"]["properties"]["workers_in_flight"]
    assert cap["maximum"] == V.MAX_WORKERS_IN_FLIGHT
