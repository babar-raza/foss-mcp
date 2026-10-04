"""Canary and unit tests for the D5 card linter (DECISION_LOG 2026-10-04).

The last two tests are regression controls against real history, not fixtures:
the original TC-168 card, which failed on exactly the defects this linter targets,
must be flagged, and the card in progress must lint clean.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import cardlint as L
import gatectl as G
import pytest

REPO = Path(__file__).resolve().parents[2]


def _card(**over):
    base = {
        "id": "TC-999",
        "gate": "G2",
        "checks": [{"command": "{python} -m pytest tests/sample/test_one.py -q"}],
        "network": False,
        "negative_control": {"mutate": "python -c \"print(1)\""},
    }
    base.update(over)
    return base


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "tests" / "sample"
    d.mkdir(parents=True)
    (d / "test_one.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return tmp_path


def test_a_clean_offline_card_has_no_findings(repo):
    assert L.lint_card(_card(), repo, accepted=False) == []


def test_an_accepted_card_is_grandfathered_even_if_it_is_bad(repo):
    bad = _card(negative_control={"mutate": "x \\\" y"})
    (repo / "tests" / "sample" / "test_one.py").write_text("GIT_CLONE = 'git clone x'\n", encoding="utf-8")
    assert L.lint_card(bad, repo, accepted=True) == [], "accepted cards must never be retroactively failed"


def test_a_backslash_in_the_falsifier_is_flagged_l1(repo):
    problems = L.lint_card(_card(negative_control={"mutate": 'python -c "x=\\"y\\""'}), repo, accepted=False)
    assert any("backslash" in p for p in problems)


def test_a_chr_built_falsifier_is_not_flagged_l1(repo):
    mutate = "python -X utf8 -c \"import pathlib;q=chr(34);p=pathlib.Path('x.py')\""
    assert L.lint_card(_card(negative_control={"mutate": mutate}), repo, accepted=False) == []


def test_network_reach_under_an_offline_card_is_flagged_l2(repo):
    (repo / "tests" / "sample" / "test_one.py").write_text(
        "def fetch():\n    return git('clone', 'https://github.com/x/y.git')\n", encoding="utf-8"
    )
    problems = L.lint_card(_card(network=False), repo, accepted=False)
    assert any("reaches the network" in p for p in problems)


def test_the_same_network_test_is_fine_when_the_card_declares_network_l2(repo):
    (repo / "tests" / "sample" / "test_one.py").write_text("def f():\n    git('clone', 'x')\n", encoding="utf-8")
    assert L.lint_card(_card(network=True), repo, accepted=False) == []


def test_a_test_that_runs_the_ci_is_flagged_l3(repo):
    (repo / "tests" / "sample" / "test_one.py").write_text(
        "import subprocess\nsubprocess.run(['bash', 'scripts/ci_check.sh'])\n", encoding="utf-8"
    )
    problems = L.lint_card(_card(), repo, accepted=False)
    assert any("invokes the CI or a hook" in p for p in problems)


def test_a_directory_in_the_check_command_is_expanded(repo):
    (repo / "tests" / "sample" / "test_two.py").write_text("GIT_CLONE = 'git clone'\n", encoding="utf-8")
    card = _card(checks=[{"command": "{python} -m pytest tests/sample/ -q"}])
    assert any("test_two.py" in p for p in L.lint_card(card, repo, accepted=False))


def test_the_card_in_progress_lints_clean_against_the_real_repo():
    # Liveness and regression: the linter must not fail the work that is legitimately
    # in progress. TC-174 is the current unaccepted card that this check exercises.
    card, _ = G.load_card_at_rev("TC-174", "HEAD")
    assert L.lint_card(card, REPO, accepted=False) == []


def test_the_original_tc168_card_would_have_been_caught_before_dispatch():
    # Historical canary. TC-168 attempt 1 failed on a backslash falsifier and on
    # network-only fixtures under network: false. Load its card as it stood at its
    # issue revision, and require the linter to flag both defects.
    rc = subprocess.run(
        ["git", "show", "e607ceff:plans/TC-168.yaml"], cwd=REPO, capture_output=True, text=True, encoding="utf-8"
    )
    assert rc.returncode == 0, rc.stderr
    card = G.load_yaml_text(rc.stdout)
    problems = L.lint_card(card, REPO, accepted=False)
    assert any("backslash" in p for p in problems), "must catch the shell-mangled falsifier (L1)"
    assert any("reaches the network" in p for p in problems), "must catch network-only fixtures (L2)"
