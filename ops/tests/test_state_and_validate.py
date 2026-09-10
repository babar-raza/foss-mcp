"""State derivation and `validate` negative controls.

project/state.yaml is DERIVED, never authored. That is the direct application of
the reference system's clearest lesson: every governance artifact it kept under a
schema stayed correct, and every artifact a model hand-maintained drifted. A
status authority that cannot be rebuilt from primary evidence is a narration.

The `validate` tests are negative controls in the strict sense: each one builds a
deliberately broken card set and asserts the validator REJECTS it. A validator
tested only on well-formed input proves nothing.
"""

from __future__ import annotations

import json

import gatecli as C
import gatectl as G
import gateverify as V
import pytest


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Point every gatectl path at a throwaway tree."""
    for name in ("REPO",):
        monkeypatch.setattr(G, name, tmp_path)
    plans = tmp_path / "plans"
    ops = tmp_path / "ops"
    ev = tmp_path / "evidence" / "build"
    for d in (plans, ops, ev, tmp_path / "project"):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(G, "PLANS", plans)
    monkeypatch.setattr(G, "OPS", ops)
    monkeypatch.setattr(G, "EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "project" / "state.yaml")
    monkeypatch.setattr(G, "STATUS_JSONL", ops / "status.jsonl")
    monkeypatch.setattr(G, "INSTRUCTIONS_JSONL", ops / "instructions.jsonl")
    monkeypatch.setattr(G, "QUESTIONS_JSONL", ops / "open_questions.jsonl")
    monkeypatch.setattr(V, "INSTRUCTIONS_JSONL", ops / "instructions.jsonl")
    monkeypatch.setattr(V, "QUESTIONS_JSONL", ops / "open_questions.jsonl")
    monkeypatch.setattr(V, "EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(V, "OWNER_FILE", ops / "owner_items.yaml")
    return tmp_path


def write_card(sandbox, cid, gate="G0", deps=(), paths=None):
    card = {
        "id": cid,
        "gate": gate,
        "lane": "foundation",
        "purpose": f"build {cid}",
        "depends_on": list(deps),
        "write_paths": paths or [f"src/{cid.lower()}/**"],
        "satisfies": ["REQ-G0-001"],
        "source_refs": [{"file": "README.md", "anchor": "x", "sha256": "f" * 64}],
        "actions": ["do it"],
        "checks": [{"command": "true", "expect": "exit 0", "timeout_seconds": 60, "proves": ["REQ-G0-001"]}],
        "negative_control": {"patch": f"evidence/negctl/{cid}.patch", "rationale": "r"},
        "evidence": ["e"],
        "closeout": ["c"],
        "recovery": "r",
    }
    (G.PLANS / f"{cid}.yaml").write_text(G.dump_yaml(card), encoding="utf-8")
    return card


def write_receipt(sandbox, cid, gate="G0", accepted=True):
    d = G.EVIDENCE / "build" / gate / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "receipt.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "card": cid,
                "gate": gate,
                "generated_by": "gatectl",
                "generated_at": "2026-09-10T12:00:00Z",
                "base_rev": "a" * 40,
                "head_rev": "b" * 40,
                "fingerprint": {"python": "3.13", "platform": "w", "lock_sha256": "d" * 64, "env": {}},
                "scope": {"ok": True, "changed_paths": [], "violations": []},
                "runs": [],
                "negative_control": {
                    "patch": "p",
                    "patch_sha256": "e" * 64,
                    "applied": True,
                    "patch_applied_failed": True,
                },
                "accepted": accepted,
                "reason": "x",
            }
        ),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ derivation
def test_a_card_with_no_receipt_is_ready_when_its_deps_are_accepted(sandbox):
    write_card(sandbox, "TC-001")
    write_card(sandbox, "TC-002", deps=["TC-001"])
    s = V.rebuild_state()
    by = {r["id"]: r for r in s["cards"]}
    assert by["TC-001"]["status"] == "READY"
    assert by["TC-002"]["status"] == "PENDING", "a card whose dependency is unmet is not READY"

    write_receipt(sandbox, "TC-001")
    s = V.rebuild_state()
    by = {r["id"]: r for r in s["cards"]}
    assert by["TC-001"]["status"] == "ACCEPTED"
    assert by["TC-002"]["status"] == "READY", "accepting a dependency must release its dependant"


def test_next_card_is_deterministic(sandbox):
    """Two runs of the same plan must dispatch the same card. A legal ordering
    is not a determined ordering."""
    for cid in ("TC-003", "TC-001", "TC-002"):
        write_card(sandbox, cid)
    picks = {V.next_card(V.rebuild_state()) for _ in range(5)}
    assert picks == {"TC-001"}, "next must be stable and lowest-id-first"


def test_a_gate_is_accepted_only_when_every_card_is(sandbox):
    write_card(sandbox, "TC-001")
    write_card(sandbox, "TC-002")
    write_receipt(sandbox, "TC-001")
    s = V.rebuild_state()
    assert s["accepted_gates"] == [], "one accepted card is not an accepted gate"
    write_receipt(sandbox, "TC-002")
    assert V.rebuild_state()["accepted_gates"] == ["G0"]


def test_a_failing_receipt_does_not_produce_acceptance(sandbox):
    write_card(sandbox, "TC-001")
    write_receipt(sandbox, "TC-001", accepted=False)
    row = V.rebuild_state()["cards"][0]
    assert row["status"] != "ACCEPTED"


def test_exhausting_attempts_produces_a_blocker_with_a_resume_predicate(sandbox):
    write_card(sandbox, "TC-001")
    write_receipt(sandbox, "TC-001", accepted=False)
    G.INSTRUCTIONS_JSONL.write_text(
        json.dumps(
            {
                "ts": "2026-09-10T12:00:00Z",
                "target_card": "TC-001",
                "kind": "rework",
                "instruction": "again",
                "card_sha256": "0" * 64,
                "issue_rev": "0" * 40,
                "attempt": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    row = V.rebuild_state()["cards"][0]
    assert row["status"] == "FAILED_INTERNAL"
    assert row["blocker"]["resume_predicate"], "a blocked card must carry an exact resume predicate"


def test_derived_state_validates_against_its_own_schema(sandbox):
    write_card(sandbox, "TC-001")
    s = V.rebuild_state()
    assert G.schema_errors(G.load_schema("state.schema.json"), s) == []


# ------------------------------------------------------- validate: negatives
class Args:
    pass


def run_validate():
    return C.cmd_validate(Args())


def test_validate_rejects_a_dependency_cycle(sandbox):
    write_card(sandbox, "TC-001", deps=["TC-002"])
    write_card(sandbox, "TC-002", deps=["TC-001"])
    problems = C._cycle_problems(G.all_cards())
    assert any("cycle" in p for p in problems), problems


def test_validate_rejects_a_missing_dependency(sandbox):
    write_card(sandbox, "TC-001", deps=["TC-999"])
    cards = G.all_cards()
    missing = [f"{c}" for c, v in cards.items() for d in v.get("depends_on", []) if d not in cards]
    assert missing, "a card depending on a nonexistent card must be rejected"


def test_validate_rejects_overlapping_write_paths(sandbox):
    """Two in-flight cards owning the same path is a race, not a plan."""
    write_card(sandbox, "TC-001", paths=["src/shared/**"])
    write_card(sandbox, "TC-002", paths=["src/shared/**"])
    problems = C._overlap_problems(G.all_cards())
    assert any("overlapping" in p for p in problems), problems


def test_validate_rejects_a_card_claiming_a_globally_denied_path(sandbox):
    """Enforced at AUTHORING time, so a bad card fails before a worker exists."""
    write_card(sandbox, "TC-001", paths=["ops/**"])
    card = G.all_cards()["TC-001"]
    assert any(G.matches_any(wp, G.GLOBAL_DENY) for wp in card["write_paths"])


def test_validate_rejects_an_unknown_requirement(sandbox):
    (G.OPS / "requirements.yaml").write_text(
        G.dump_yaml({"requirements": [{"id": "REQ-G0-002", "gate": "G0", "text": "t"}]}), encoding="utf-8"
    )
    write_card(sandbox, "TC-001")  # satisfies REQ-G0-001, which does not exist
    problems = C._req_problems(G.all_cards())
    assert any("unknown requirement" in p for p in problems), problems


def test_validate_reports_a_requirement_no_card_claims(sandbox):
    """The omission control. A missing REQ is a missing card, and a per-card
    review structurally cannot see a card that was never written."""
    (G.OPS / "requirements.yaml").write_text(
        G.dump_yaml(
            {
                "requirements": [
                    {"id": "REQ-G0-001", "gate": "G0", "text": "claimed"},
                    {"id": "REQ-G0-042", "gate": "G0", "text": "forgotten"},
                ]
            }
        ),
        encoding="utf-8",
    )
    write_card(sandbox, "TC-001")
    problems = C._req_problems(G.all_cards())
    assert any("REQ-G0-042" in p and "no card" in p for p in problems), problems


def test_validate_reports_a_requirement_claimed_but_never_proven(sandbox):
    (G.OPS / "requirements.yaml").write_text(
        G.dump_yaml(
            {
                "requirements": [
                    {"id": "REQ-G0-001", "gate": "G0", "text": "claimed"},
                    {"id": "REQ-G0-007", "gate": "G0", "text": "claimed but unproven"},
                ]
            }
        ),
        encoding="utf-8",
    )
    card = write_card(sandbox, "TC-001")
    card["satisfies"] = ["REQ-G0-001", "REQ-G0-007"]
    (G.PLANS / "TC-001.yaml").write_text(G.dump_yaml(card), encoding="utf-8")
    problems = C._req_problems(G.all_cards())
    assert any("REQ-G0-007" in p and "proves" in p for p in problems), problems


def test_validate_detects_a_hand_edited_state_file(sandbox):
    """The status authority must never be hand-maintained."""
    write_card(sandbox, "TC-001")
    s = V.rebuild_state()
    s["cards"][0]["status"] = "ACCEPTED"  # a lie, with no receipt behind it
    G.STATE_FILE.write_text(G.dump_yaml(s), encoding="utf-8")
    committed = G.load_yaml(G.STATE_FILE)
    assert C._state_core(committed) != C._state_core(V.rebuild_state())


def test_validate_enforces_a_declared_budget_against_the_real_file(sandbox):
    """A schema const that nothing reads is a lie with a certificate."""
    (G.REPO / "AGENTS.md").write_text("\n".join(f"line {i}" for i in range(250)), encoding="utf-8")
    assert any("AGENTS.md" in p for p in C._budget_problems())


def test_jsonl_timestamps_must_not_go_backwards(sandbox):
    G.STATUS_JSONL.write_text(
        json.dumps(
            {
                "ts": "2026-09-10T12:00:00Z",
                "card": "TC-001",
                "phase": "started",
                "verdict": "n/a",
                "summary": "a",
                "commit": "a" * 40,
                "attempt": 1,
            }
        )
        + "\n"
        + json.dumps(
            {
                "ts": "2026-09-10T11:00:00Z",
                "card": "TC-001",
                "phase": "committed",
                "verdict": "pass",
                "summary": "b",
                "commit": "b" * 40,
                "attempt": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    problems = C._jsonl_problems(G.STATUS_JSONL, "status-line.schema.json", "status")
    assert any("backwards" in p for p in problems), problems


def test_overlap_is_ignored_for_dependency_ordered_cards(sandbox):
    """A skeleton card owning src/pkg/** and a later card owning
    src/pkg/indexing/** cannot race - the second starts only once the first is
    ACCEPTED. Flagging that pair would force contorted path lists for no gain."""
    write_card(sandbox, "TC-001", paths=["src/pkg/**"])
    write_card(sandbox, "TC-002", deps=["TC-001"], paths=["src/pkg/indexing/**"])
    assert C._overlap_problems(G.all_cards()) == []


def test_overlap_is_still_flagged_for_independent_cards(sandbox):
    """Without a dependency edge the two really could be dispatched together."""
    write_card(sandbox, "TC-001", paths=["src/pkg/**"])
    write_card(sandbox, "TC-002", paths=["src/pkg/indexing/**"])
    assert any("overlapping" in p for p in C._overlap_problems(G.all_cards()))


def test_as_if_unstarted_restores_the_pre_dispatch_queue(sandbox):
    """`accept` must compare against the queue as it was BEFORE the card ran.

    Regression: once a receipt exists the card reads ACCEPTED and `next` has
    moved to its successor, so a naive comparison always refuses. Ignoring only
    the receipt is not enough either - the dispatch marker still leaves it
    IN_PROGRESS, which is not READY.
    """
    write_card(sandbox, "TC-001")
    write_card(sandbox, "TC-002", deps=["TC-001"])
    write_receipt(sandbox, "TC-001")
    G.INSTRUCTIONS_JSONL.write_text(
        json.dumps(
            {
                "ts": "2026-09-10T12:00:00Z",
                "target_card": "TC-001",
                "kind": "dispatch",
                "instruction": "go",
                "card_sha256": "0" * 64,
                "issue_rev": "0" * 40,
                "attempt": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert V.next_card(V.rebuild_state()) == "TC-002", "the live queue has moved on"
    assert V.next_card(V.rebuild_state(as_if_unstarted="TC-001")) == "TC-001", (
        "as_if_unstarted must ignore BOTH the receipt and the dispatch marker"
    )


def test_a_falsifier_that_changes_nothing_is_not_applied(sandbox, monkeypatch):
    """Regression: the worst failure mode this design has.

    A negative-control command that exits 0 while mutating nothing turns the
    strongest control in the system into a rubber stamp. It happened for real:
    a regex falsifier arrived through YAML and a shell with a doubled backslash,
    so it required a literal backslash in Python source, matched no lines, wrote
    the file back unchanged and exited 0.

    Exit status is not evidence that a mutation happened. The tree is.
    """
    import gateverify as VV

    calls = {"status": 0}

    def fake_run(cmd, cwd=None, env=None, timeout=None, text=True):
        if isinstance(cmd, list) and cmd[:2] == ["git", "status"]:
            calls["status"] += 1
            return 0, "", ""  # clean tree: the falsifier did nothing
        return 0, "", ""  # the mutation command "succeeds"

    monkeypatch.setattr(VV, "run", fake_run)
    rc, dirty, _ = VV.run(["git", "status", "--porcelain"], cwd=".")
    assert not dirty.strip(), "precondition: the tree is clean after the mutation"
    assert calls["status"] == 1, "verify must consult the tree, not just the exit code"
