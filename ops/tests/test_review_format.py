"""Canaries for the review format rule (2026-10-05).

Review ran a card's own checks and the related offline tests, but never the formatter. TC-209, TC-212
and TC-213 were accepted with test files that `ruff format --check` rejects, and the CI format gate
would have failed on push. The rule now: review also runs the formatter over the Python files the card
changed. These canaries pin the selection rule, which is pure, and prove the review command calls the
step, because a correct helper that nothing calls is the failure this project has already had.
"""

from __future__ import annotations

import inspect

import gatecli as C


def test_only_python_files_are_selected():
    changed = ["src/a.py", "plans/TC-1.yaml", "Dockerfile.ingestion", "tests/test_a.py"]
    assert C.format_check_targets(changed) == ["src/a.py", "tests/test_a.py"]


def test_the_selection_is_sorted_and_stable():
    assert C.format_check_targets(["z.py", "a.py"]) == ["a.py", "z.py"]


def test_no_python_change_selects_nothing():
    assert C.format_check_targets(["docs/DECISION_LOG.md"]) == []


def test_a_format_rejection_turns_the_accepted_receipt_into_a_rejected_one():
    accepted = {"accepted": True, "reason": "all gates passed", "card": "TC-217", "runs": [{"run": 1}]}
    rejected = C.receipt_rejected_by_format(accepted, "unformatted: File would be reformatted\n  --> x.py:1")
    assert rejected["accepted"] is False, "the derived state reads this, so it must not stay accepted"
    assert rejected["reason"].startswith("ruff format --check failed: unformatted")
    assert rejected["runs"] == accepted["runs"], "the evidence of what ran is kept"
    assert accepted["accepted"] is True, "the input is not mutated"


def test_a_coverage_rejection_also_turns_the_accepted_receipt_into_a_rejected_one():
    # The coverage gate (added 2026-10-04) ran after do_verify had already written an accepted receipt,
    # same as the format gate, and had the identical bug the whole time: found on TC-227, 2026-10-07.
    accepted = {"accepted": True, "reason": "all gates passed", "card": "TC-227", "runs": [{"run": 1}]}
    rejected = C.receipt_rejected_by_review_gate(
        accepted, "offline tests of files this card touches fail at the head", "assert 20 == 14"
    )
    assert rejected["accepted"] is False
    assert rejected["reason"].startswith(
        "offline tests of files this card touches fail at the head: assert 20"
    )


def test_review_rejects_the_receipt_when_the_coverage_step_fails():
    import inspect as _i

    src = _i.getsource(C.cmd_review)
    assert "_reject_receipt(\n            args.card, r, " in src or "_reject_receipt(args.card, r, " in src
    assert "offline tests of files this card touches fail at the head" in src
    assert src.index("_review_related_tests(") < src.index("offline tests of files this card touches fail")
    assert src.index("offline tests of files this card touches fail") < src.index("cmd_accept(")


def test_review_rejects_the_receipt_when_the_format_step_fails():
    import inspect as _i

    src = _i.getsource(C.cmd_review)
    assert "_reject_receipt(args.card" in src
    assert src.index("_review_format(") < src.index("_reject_receipt(args.card")
    assert src.index("_reject_receipt(args.card") < src.index("cmd_accept(")


def test_review_calls_the_format_step_after_coverage():
    source = inspect.getsource(C.cmd_review)
    assert "_review_format(" in source
    assert source.index("_review_related_tests(") < source.index("_review_format(")
    assert source.index("_review_format(") < source.index("cmd_accept(")
