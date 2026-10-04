"""Canary and unit tests for capability gating (redesign D1, DECISION_LOG 2026-10-04).

These guard the verdict engine itself. A card that declares no `requires` must
behave exactly as it did before D1, so most tests here pin down what must NOT
change, not only what is new.
"""

from __future__ import annotations

import json
from pathlib import Path

import capabilities as CAP
import gateverify as V
import pytest

REPO = Path(__file__).resolve().parents[2]


def test_known_list_and_probe_table_name_the_same_capabilities():
    # Enforced at import too (assert in capabilities.py). Re-checked here so a
    # reader sees the contract without tracing the module.
    assert set(CAP.PROBES) == set(CAP.KNOWN)


def test_schema_enum_matches_the_probe_vocabulary():
    schema = json.loads((REPO / "schemas" / "taskcard.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["requires"]["items"]["enum"]
    assert sorted(enum) == sorted(CAP.KNOWN), "taskcard schema and capabilities.KNOWN must agree"


def test_a_card_without_requires_is_unaffected():
    # The regression control that matters most: every existing card has no
    # `requires`, so none of them may pick up a new verdict.
    assert CAP.missing(None) == []
    assert CAP.missing([]) == []


def test_python_is_present_because_gatectl_runs_under_it():
    assert CAP.missing(["python"]) == []


def test_an_absent_tool_is_reported_missing(monkeypatch):
    monkeypatch.setattr(CAP.shutil, "which", lambda name: None)
    assert CAP.missing(["git", "cmake"]) == ["cmake", "git"]


def test_an_unknown_capability_raises_rather_than_passing_silently():
    with pytest.raises(ValueError, match="unknown capability"):
        CAP.missing(["teleport"])


def test_the_msvc_probe_does_not_restrict_vswhere_to_the_latest_instance():
    # Regression canary (2026-10-04). `-latest` returns only one Visual Studio instance.
    # A newer install without the C++ workload hid the working Build Tools install, so the
    # probe reported msvc missing on a machine that had it. The probe must inspect every instance.
    import inspect

    src = inspect.getsource(CAP._msvc)
    assert '"-latest"' not in src, "msvc probe must not use -latest"
    assert "Microsoft.VisualStudio.Component.VC.Tools.x86.x64" in src


def test_bash_from_windowsapps_or_system32_is_not_accepted(monkeypatch):
    # The WSL launcher resolves as `bash` on this machine. It broke .githooks, so it
    # must never count as the Git bash this project needs.
    monkeypatch.setattr(
        CAP.shutil, "which", lambda name: r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe"
    )
    assert CAP.missing(["bash"]) == ["bash"]
    monkeypatch.setattr(CAP.shutil, "which", lambda name: r"C:\Windows\System32\bash.exe")
    assert CAP.missing(["bash"]) == ["bash"]


def test_do_verify_checks_capabilities_before_any_check_runs():
    # Liveness control (AGENTS, Integration and liveness): the gate must be CALLED
    # from the real verify path, before the first check, not merely defined.
    src = (REPO / "ops" / "gateverify.py").read_text(encoding="utf-8")
    start = src.index("def do_verify(")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    call = body.index('capabilities_missing(card.get("requires"))')
    first_check = body.index("run(")  # the first subprocess run in do_verify
    assert call < first_check, "capability gate must run before any check executes"


@pytest.mark.parametrize(
    "eb, receipt, expected",
    [
        (None, None, False),
        ({"generated_at": "2026-10-04T10:00:00Z"}, None, True),
        ({"generated_at": "2026-10-04T10:00:00Z"}, {"generated_at": "2026-10-04T09:00:00Z"}, True),
        ({"generated_at": "2026-10-04T09:00:00Z"}, {"generated_at": "2026-10-04T10:00:00Z"}, False),
        # A tie goes to the receipt: a card verified in the same second as its
        # blocked record is not blocked.
        ({"generated_at": "2026-10-04T10:00:00Z"}, {"generated_at": "2026-10-04T10:00:00Z"}, False),
    ],
)
def test_an_env_record_only_governs_while_newer_than_the_receipt(eb, receipt, expected):
    assert V.env_block_is_current(eb, receipt) is expected


def test_every_declared_gate_status_is_in_the_state_schema():
    state = json.loads((REPO / "schemas" / "state.schema.json").read_text(encoding="utf-8"))
    card_status = state["properties"]["cards"]["items"]["properties"]["status"]["enum"]
    gate_status = state["properties"]["current_gate"]["properties"]["status"]["enum"]
    assert "BLOCKED_ENV" in card_status
    assert "BLOCKED_ENV" in gate_status
    blocker_class = state["$defs"]["blocker"]["oneOf"][1]["properties"]["class"]["enum"]
    assert "BLOCKED_ENV" in blocker_class
