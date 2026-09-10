"""Schema tests, with negative controls.

Every artifact in the reference system that had a schema PLUS negative-control
tests stayed correct for weeks. Every artifact without one drifted - its status
JSONL carried `commit: null` on every real line and nothing noticed. These tests
are that lesson, applied before the drift rather than after.

A suite that only proves well-formed documents pass proves almost nothing. Each
positive case below is paired with cases that MUST fail.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import gatectl as G
import jsonschema
import pytest

SCHEMA_DIR = G.REPO / "schemas"


def load(name):
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def errors(schema, instance):
    return list(jsonschema.Draft202012Validator(schema).iter_errors(instance))


def test_every_schema_is_itself_valid():
    files = sorted(glob.glob(str(SCHEMA_DIR / "*.json")))
    assert files, "no schemas found"
    for f in files:
        jsonschema.Draft202012Validator.check_schema(json.loads(Path(f).read_text(encoding="utf-8")))


# ---------------------------------------------------------------- status line
def _status_line(**over):
    line = {
        "ts": "2026-09-10T12:00:00Z",
        "card": "TC-001",
        "phase": "committed",
        "verdict": "pass",
        "summary": "did the thing",
        "commit": "a" * 40,
        "attempt": 1,
    }
    line.update(over)
    return line


def test_status_line_accepts_a_well_formed_line():
    assert errors(load("status-line.schema.json"), _status_line()) == []


@pytest.mark.parametrize(
    ("field", "bad", "why"),
    [
        ("commit", None, "commit: null is THE observed drift in the reference system"),
        ("commit", "", "empty commit must not pass"),
        ("commit", "nothex" * 6, "commit must be 40 hex chars"),
        ("ts", "2026-09-10 12:00:00", "timestamp must be strict ISO-8601 Z"),
        ("ts", "not-a-time", "garbage timestamp must not pass"),
        ("phase", "done", "phase is a closed enum"),
        ("verdict", "maybe", "verdict is a closed enum"),
        ("attempt", 0, "attempt is 1-based"),
        ("attempt", 4, "attempt is capped at the 3-attempt limit"),
    ],
)
def test_status_line_rejects_drift(field, bad, why):
    assert errors(load("status-line.schema.json"), _status_line(**{field: bad})), why


def test_status_line_rejects_unknown_fields():
    assert errors(load("status-line.schema.json"), _status_line(extra="x"))


def test_status_line_requires_every_field():
    schema = load("status-line.schema.json")
    for field in schema["required"]:
        line = _status_line()
        del line[field]
        assert errors(schema, line), f"a line missing {field} must be rejected"


# ---------------------------------------------------------------- state cursor
def _state(**over):
    s = {
        "schema_version": 1,
        "project": "foss-mcp",
        "generated_by": "gatectl",
        "generated_at": "2026-09-10T12:00:00Z",
        "current_gate": {"id": "G0", "status": "READY"},
        "accepted_gates": [],
        "cards": [{"id": "TC-001", "gate": "G0", "status": "READY", "attempt": 0, "blocker": None}],
        "owner_items": [],
        "open_questions": {"open": 0, "ids": []},
        "execution_limits": {
            "workers_in_flight": 1,
            "max_attempts_per_card": 3,
            "supervisor_writes_product_code": False,
            "worker_issues_verdicts": False,
        },
    }
    s.update(over)
    return s


def test_state_accepts_a_well_formed_cursor():
    assert errors(load("state.schema.json"), _state()) == []


def test_state_rejects_a_relaxed_execution_limit():
    """The limits are `const` on purpose: an agent cannot quietly raise them.

    Relaxing a limit must require editing the schema, which is a visibly
    different kind of diff from editing a value.
    """
    s = _state()
    s["execution_limits"]["max_attempts_per_card"] = 99
    assert errors(load("state.schema.json"), s)
    s = _state()
    s["execution_limits"]["worker_issues_verdicts"] = True
    assert errors(load("state.schema.json"), s), "the worker must never issue verdicts"


def test_state_ties_blockers_to_blocked_statuses():
    """A blocked card cannot exist without an exact resume predicate, and an
    unblocked card cannot carry a blocker. This coupling is what makes
    'blocked' an honest status rather than a place to park work."""
    schema = load("state.schema.json")

    s = _state()
    s["cards"][0]["status"] = "BLOCKED_EXTERNAL"
    assert errors(schema, s), "blocked status with blocker:null must be rejected"

    s = _state()
    s["cards"][0]["blocker"] = {
        "class": "BLOCKED_EXTERNAL",
        "summary": "needs a credential",
        "resume_predicate": "the credential exists",
        "recorded_at": "2026-09-10T12:00:00Z",
    }
    assert errors(schema, s), "a READY card must not carry a blocker"

    s = _state()
    s["cards"][0]["status"] = "FAILED_INTERNAL"
    s["cards"][0]["blocker"] = {
        "class": "FAILED_INTERNAL",
        "summary": "x",
        "resume_predicate": "y",
        "recorded_at": "2026-09-10T12:00:00Z",
    }
    assert errors(schema, s) == [], "a blocked card WITH a resume predicate is valid"


def test_state_blocker_requires_a_resume_predicate():
    schema = load("state.schema.json")
    s = _state()
    s["cards"][0]["status"] = "BLOCKED_EXTERNAL"
    s["cards"][0]["blocker"] = {
        "class": "BLOCKED_EXTERNAL",
        "summary": "needs a credential",
        "recorded_at": "2026-09-10T12:00:00Z",
    }
    assert errors(schema, s), "a blocker without resume_predicate must be rejected"


def test_state_rejects_a_competing_project_or_extra_key():
    assert errors(load("state.schema.json"), _state(project="something-else"))
    assert errors(load("state.schema.json"), _state(roadmap=["a competing authority"]))


# ---------------------------------------------------------------- receipt
def _receipt(**over):
    check = {"command": "pytest", "exit_code": 0, "duration_s": 1.0, "stdout_sha256": "b" * 64}
    r = {
        "schema_version": 1,
        "card": "TC-001",
        "gate": "G0",
        "generated_by": "gatectl",
        "generated_at": "2026-09-10T12:00:00Z",
        "base_rev": "a" * 40,
        "head_rev": "c" * 40,
        "fingerprint": {
            "python": "3.13.2",
            "platform": "Windows",
            "lock_sha256": "d" * 64,
            "env": {"PYTHONHASHSEED": "0"},
        },
        "scope": {"ok": True, "changed_paths": [], "violations": []},
        "runs": [
            {"run": 1, "checks": [check], "all_passed": True},
            {"run": 2, "checks": [check], "all_passed": True},
        ],
        "negative_control": {
            "kind": "patch",
            "patch": "evidence/negctl/TC-001.patch",
            "patch_sha256": "e" * 64,
            "applied": True,
            "patch_applied_failed": True,
        },
        "accepted": True,
    }
    r.update(over)
    return r


def test_receipt_accepts_a_well_formed_document():
    assert errors(load("receipt.schema.json"), _receipt()) == []


def test_receipt_requires_exactly_two_clean_runs():
    """One run cannot detect a flake, and a third is wasted wall clock."""
    schema = load("receipt.schema.json")
    r = _receipt()
    r["runs"] = r["runs"][:1]
    assert errors(schema, r), "a single run must be rejected"
    r = _receipt()
    r["runs"] = r["runs"] + [r["runs"][0]]
    assert errors(schema, r), "three runs must be rejected"


def test_receipt_requires_the_environment_fingerprint():
    schema = load("receipt.schema.json")
    r = _receipt()
    del r["fingerprint"]["lock_sha256"]
    assert errors(schema, r), "a receipt without a lock digest cannot be compared across reruns"


# ---------------------------------------------------------------- taskcard
def _card(**over):
    c = {
        "id": "TC-001",
        "gate": "G0",
        "lane": "foundation",
        "purpose": "do a thing",
        "depends_on": [],
        "write_paths": ["src/thing/**"],
        "satisfies": ["REQ-G0-001"],
        "source_refs": [{"file": "README.md", "anchor": "# foss-mcp", "sha256": "f" * 64}],
        "actions": ["write the thing"],
        "checks": [
            {"command": "pytest", "expect": "exit 0", "timeout_seconds": 60, "proves": ["REQ-G0-001"]}
        ],
        "negative_control": {"patch": "evidence/negctl/TC-001.patch", "rationale": "breaks the thing"},
        "evidence": ["evidence/build/G0/TC-001/receipt.json"],
        "closeout": ["checks pass and the falsifier fails them"],
        "recovery": "re-run from a clean tree",
    }
    c.update(over)
    return c


def test_taskcard_accepts_a_well_formed_card():
    assert errors(load("taskcard.schema.json"), _card()) == []


def test_taskcard_requires_a_negative_control():
    """A card with no falsifier can only ever prove that its own tests agree
    with its own code."""
    schema = load("taskcard.schema.json")
    c = _card()
    del c["negative_control"]
    assert errors(schema, c)


def test_taskcard_requires_requirement_traceability():
    schema = load("taskcard.schema.json")
    c = _card()
    c["satisfies"] = []
    assert errors(schema, c), "a card satisfying no requirement is untraceable"
    c = _card()
    c["checks"][0].pop("proves")
    assert errors(schema, c), "a check that proves nothing is not evidence"


def test_taskcard_requires_source_refs_with_digests():
    schema = load("taskcard.schema.json")
    c = _card()
    c["source_refs"] = []
    assert errors(schema, c)
    c = _card()
    c["source_refs"][0]["sha256"] = "short"
    assert errors(schema, c)


def test_taskcard_rejects_a_bad_id_or_gate():
    schema = load("taskcard.schema.json")
    assert errors(schema, _card(id="TC-1"))
    assert errors(schema, _card(gate="G9"))
    assert errors(schema, _card(write_paths=[]))
