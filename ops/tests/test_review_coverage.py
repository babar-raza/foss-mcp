"""Canaries for the review coverage rule (2026-10-04).

Review used to run only a card's own checks. TC-190 then broke TC-188's accepted test on main, and
nothing noticed until a worker did. The rule now: review also runs the offline tests that exercise the
same modules as the card's write_paths. These canaries pin the selection rule, which is pure.
"""

from __future__ import annotations

import gatecli as C


def test_a_test_naming_a_write_path_module_is_selected():
    tests = {"tests/infra/test_build_chunks_platforms.py": "import build_chunks as b"}
    assert C.related_offline_tests(["infra/build_chunks.py"], tests) == [
        "tests/infra/test_build_chunks_platforms.py"
    ]


def test_a_test_that_names_no_write_path_module_is_not_selected():
    tests = {"tests/infra/test_other.py": "import something_else"}
    assert C.related_offline_tests(["infra/build_chunks.py"], tests) == []


def test_a_test_that_reaches_the_network_is_excluded():
    tests = {"tests/indexing/test_live.py": "import build_chunks\nsubprocess.run(['git', 'clone', url])"}
    assert C.related_offline_tests(["infra/build_chunks.py"], tests) == []


def test_a_test_that_runs_the_ci_is_excluded():
    tests = {"tests/test_ci.py": "import build_chunks\nrun(['bash', 'scripts/ci_check.sh'])"}
    assert C.related_offline_tests(["infra/build_chunks.py"], tests) == []


def test_a_short_stem_is_too_generic_to_select_tests():
    tests = {"tests/test_a.py": "x = 'app' + 'go'"}
    assert C.related_offline_tests(["src/app.py"], tests) == []


def test_test_paths_in_the_write_set_do_not_drive_selection():
    tests = {"tests/infra/test_thing.py": "import test_helpers"}
    assert C.related_offline_tests(["tests/infra/test_helpers.py"], tests) == []
