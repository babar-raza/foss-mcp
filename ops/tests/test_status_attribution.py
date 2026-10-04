"""Canaries for which commits a card's status lines attribute to it (TC-196, 2026-10-04).

A committed status line for TC-196 named the supervisor's dispatch commit, because the worker ran
status-append from the main checkout. The scope check then counted the dispatch commit as the card's
own work. The rule now: the latest status line for each attempt governs, and an append-only channel is
corrected by a later line, not by an edit.
"""

from __future__ import annotations

import json

import gatectl as G
import pytest


def _write(path, lines):
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


@pytest.fixture
def status(tmp_path, monkeypatch):
    path = tmp_path / "status.jsonl"
    monkeypatch.setattr(G, "STATUS_JSONL", path)
    return path


def test_the_commit_of_a_single_line_is_attributed(status):
    _write(status, [{"card": "TC-1", "phase": "committed", "attempt": 1, "commit": "a" * 40}])
    assert G.commits_recorded_for("TC-1") == {"a" * 40}


def test_a_later_line_for_the_same_attempt_replaces_the_wrong_one(status):
    # The wrong line (the supervisor's commit) is followed by the corrected worker commit.
    _write(
        status,
        [
            {"card": "TC-1", "phase": "committed", "attempt": 1, "commit": "d" * 40},
            {"card": "TC-1", "phase": "committed", "attempt": 1, "commit": "w" * 40},
        ],
    )
    assert G.commits_recorded_for("TC-1") == {"w" * 40}


def test_separate_attempts_both_stay_in_scope(status):
    _write(
        status,
        [
            {"card": "TC-1", "phase": "committed", "attempt": 1, "commit": "a" * 40},
            {"card": "TC-1", "phase": "committed", "attempt": 2, "commit": "b" * 40},
        ],
    )
    assert G.commits_recorded_for("TC-1") == {"a" * 40, "b" * 40}


def test_another_cards_lines_are_ignored(status):
    _write(status, [{"card": "TC-2", "phase": "committed", "attempt": 1, "commit": "c" * 40}])
    assert G.commits_recorded_for("TC-1") == set()


def test_a_missing_status_file_attributes_nothing(status):
    assert not status.exists()
    assert G.commits_recorded_for("TC-1") == set()
