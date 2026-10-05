"""Canaries for gate exit as primary evidence (2026-10-05).

The first G2 gate exit found thirteen failing cards, a CI step that never ran, two open questions and
an open owner item, printed GATE G2 NOT MET, and persisted nothing. `gatectl tick` still said
"G2 IN_PROGRESS, no READY card". It also rewrote 367 evidence files. Four defects, four pins:

* the exit report is written on every run and the derived state reads it,
* a gate is accepted only when its exit was MET, not merely when every card is green,
* the CI step uses a real bash, never the Windows WSL launcher, and reports its real error,
* a card that still passes keeps its stored receipt (persist="on_fail").
"""

from __future__ import annotations

import inspect
import json

import gatecli as C
import gatectl as G
import gateverify as V
import pytest
from test_state_and_validate import sandbox, write_card, write_receipt  # noqa: F401  (fixture reuse)


def _result(accepted=True, reason="all gates passed", tests=5):
    return {
        "accepted": accepted,
        "reason": reason,
        "runs": [{"checks": [{"tests": tests}]}],
    }


def _report(**over):
    base = dict(
        gate="G0",
        head="a" * 40,
        results={"TC-001": _result()},
        gate_level=[],
        ci={"exit_code": 0, "steps": ["lint: success"]},
        live_smoke={"status": "PASS", "detail": "ok"},
        open_questions=[],
        owner_items=[],
        blocked_env=[],
    )
    base.update(over)
    return V.build_exit_report(**base)


# ------------------------------------------------------------------ the bash resolver
def test_the_windows_wsl_launcher_is_never_chosen():
    wsl = "C:\\Windows\\System32\\bash.exe"
    git_bash = "C:\\Program Files\\Git\\usr\\bin\\bash.exe"
    assert G.pick_bash([wsl, git_bash]) == git_bash
    assert G.pick_bash(["c:/windows/system32/bash.exe"]) is None


def test_the_first_real_bash_wins_and_empty_candidates_are_skipped():
    assert G.pick_bash([None, "", "/usr/bin/bash", "/bin/bash"]) == "/usr/bin/bash"
    assert G.pick_bash([]) is None


# ------------------------------------------------------------------ the report
def test_a_clean_run_is_met_and_validates_against_its_schema():
    rep = _report()
    assert rep["verdict"] == "MET"
    schema = G.load_schema(C.SCHEMA_FOR["gate_exit"])
    assert G.schema_errors(schema, rep) == []


def test_a_failing_card_makes_the_verdict_not_met_and_carries_its_reason():
    rep = _report(results={"TC-001": _result(), "TC-002": _result(False, "VACUOUS CHECKS: x" + "y" * 600)})
    assert rep["verdict"] == "NOT_MET"
    assert rep["failing_cards"] == ["TC-002"]
    assert rep["cards"]["TC-002"]["reason"].startswith("VACUOUS CHECKS")
    assert len(rep["cards"]["TC-002"]["reason"]) <= 400, "reasons are cut, the report stays compact"
    assert "reason" not in rep["cards"]["TC-001"], "a passing card carries only the fact and its test count"
    assert G.schema_errors(G.load_schema(C.SCHEMA_FOR["gate_exit"]), rep) == []


@pytest.mark.parametrize(
    "over",
    [
        {"gate_level": ["repo-wide CI failed: lint: failure"]},
        {"open_questions": ["OQ-005"]},
        {"owner_items": ["OWNER-06"]},
    ],
)
def test_every_gate_level_blocker_alone_makes_the_verdict_not_met(over):
    assert _report(**over)["verdict"] == "NOT_MET"


def test_an_environment_block_alone_is_not_a_code_failure():
    assert _report(blocked_env=["live-content smoke needs Docker"])["verdict"] == "BLOCKED_ENV"


def test_blockers_list_cards_then_gate_level_then_questions_then_owners():
    rep = _report(
        results={"TC-002": _result(False, "checks failed: pytest; more")},
        gate_level=["repo-wide CI failed: format: failure"],
        open_questions=["OQ-005"],
        owner_items=["OWNER-06"],
    )
    lines = V.gate_exit_blockers(rep)
    assert lines[0].startswith("card TC-002: checks failed: pytest")
    assert "more" not in lines[0], "only the first clause of a reason is shown"
    assert lines[1].startswith("gate: repo-wide CI failed")
    assert lines[2:] == ["open question OQ-005", "owner item OWNER-06"]


def test_a_met_or_missing_report_has_no_blockers():
    assert V.gate_exit_blockers(_report()) == []
    assert V.gate_exit_blockers(None) == []


# ------------------------------------------------------------------ the gate status
@pytest.mark.parametrize(
    ("statuses", "exit_accepted", "verdict", "expected"),
    [
        (["ACCEPTED", "SUPERSEDED"], False, None, "EXIT_PENDING"),
        (["ACCEPTED"], False, "NOT_MET", "EXIT_FAILED"),
        (["ACCEPTED"], False, "BLOCKED_ENV", "EXIT_FAILED"),
        (["ACCEPTED"], True, "MET", "ACCEPTED"),
        (["ACCEPTED", "IN_PROGRESS"], False, "NOT_MET", "IN_PROGRESS"),
        (["ACCEPTED", "FAILED_INTERNAL"], False, None, "FAILED_INTERNAL"),
        (["READY", "PENDING"], False, None, "READY"),
    ],
)
def test_gate_status_needs_a_met_exit_not_just_green_cards(statuses, exit_accepted, verdict, expected):
    assert V.gate_status(statuses, exit_accepted, verdict) == expected


# ------------------------------------------------------------------ the real rebuild
def test_all_cards_accepted_is_exit_pending_until_a_met_manifest_exists(sandbox):  # noqa: F811
    write_card(sandbox, "TC-001")
    write_receipt(sandbox, "TC-001")
    s = V.rebuild_state()
    assert s["accepted_gates"] == []
    assert s["current_gate"] == {"id": "G0", "status": "EXIT_PENDING"}
    assert s["gate_exit"] is None

    (G.EVIDENCE / "build" / "G0" / "manifest.json").write_text(
        json.dumps({"gate_status": "ACCEPTED"}), encoding="utf-8"
    )
    s = V.rebuild_state()
    assert s["accepted_gates"] == ["G0"]


def test_a_failed_exit_report_appears_in_the_derived_state_with_its_blockers(sandbox):  # noqa: F811
    write_card(sandbox, "TC-001")
    write_receipt(sandbox, "TC-001")
    rep = _report(open_questions=["OQ-005"], head="c" * 40)
    V.exit_report_path("G0").write_text(json.dumps(rep), encoding="utf-8")
    s = V.rebuild_state()
    assert s["current_gate"]["status"] == "EXIT_FAILED"
    assert s["gate_exit"]["verdict"] == "NOT_MET"
    assert s["gate_exit"]["checked_head"] == "c" * 40
    assert s["gate_exit"]["blockers"] == ["open question OQ-005"]
    assert G.schema_errors(G.load_schema(C.SCHEMA_FOR["state"]), s) == []


# ------------------------------------------------------------------ wiring (call sites exist)
def test_gate_exit_persists_the_report_and_only_failing_receipts():
    src = inspect.getsource(C.cmd_gate_exit)
    assert 'persist="on_fail"' in src
    assert "V.build_exit_report(" in src
    assert "exit_report_path(" in src
    assert "G.bash_exe()" in src
    assert src.index("exit_report_path(") < src.index("manifest = {"), (
        "the report is written before the MET manifest"
    )


def test_tick_prints_the_gate_exit_and_its_blockers():
    src = inspect.getsource(C.cmd_tick)
    assert "GATE EXIT" in src
    assert "blocker:" in src
    assert "EXIT_PENDING" in src


def test_do_verify_refuses_an_unknown_persist_mode():
    with pytest.raises(ValueError):
        V.do_verify("TC-001", "HEAD", "HEAD", persist="sometimes")
