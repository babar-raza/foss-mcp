"""The related-tests step of `gatectl review` repeats once in a fresh worktree before it rejects.

Real incident (2026-10-16/17): TC-549, TC-530 and TC-550 were each rejected there with failures that matched
the card's own negative control although the head was clean; two passed on a solo rerun. Each false rejection
cost about 45 minutes. A real failure must still fail, so it has to fail twice.
"""

from __future__ import annotations

import gatecli as C


def _runner(results):
    calls = []

    def run_once():
        calls.append(1)
        return results[len(calls) - 1]

    return run_once, calls


def test_a_pass_on_the_first_attempt_runs_once():
    run_once, calls = _runner([(True, "all good")])
    assert C.related_with_one_retry(run_once, 3) == (True, "all good")
    assert len(calls) == 1


def test_a_failure_then_a_pass_is_accepted_and_the_first_failure_is_recorded():
    run_once, calls = _runner([(False, "FAILED test_x cap 9357"), (True, "ok")])
    ok, out = C.related_with_one_retry(run_once, 3)
    assert ok is True
    assert len(calls) == 2
    assert "second attempt" in out and "cap 9357" in out


def test_a_failure_twice_is_rejected_with_both_outputs():
    run_once, calls = _runner([(False, "first failure"), (False, "second failure")])
    ok, out = C.related_with_one_retry(run_once, 3)
    assert ok is False
    assert len(calls) == 2
    assert "first failure" in out and "second failure" in out


def test_a_helper_module_under_tests_is_not_selected_but_a_test_module_is():
    """TC-553 review 2026-10-17: reextraction_checks.py (a helper whose `test_file_entries` is a plain function) was
    named on the pytest command line and errored at setup; only files pytest collects by itself are selected."""
    tests = {
        "tests/extraction/reextraction_checks.py": "import run_extraction",
        "tests/extraction/conftest.py": "import run_extraction",
        "tests/extraction/test_run_extraction_x.py": "import run_extraction",
        "tests/extraction/other_test.py": "import run_extraction",
    }
    assert C.related_offline_tests(["src/foss_mcp/extraction/run_extraction.py"], tests) == [
        "tests/extraction/other_test.py",
        "tests/extraction/test_run_extraction_x.py",
    ]


def test_the_review_uses_the_retrying_runner():
    source = open(C.__file__, encoding="utf-8").read()
    assert "related_with_one_retry(lambda: _run_related_once(related, head)" in source
