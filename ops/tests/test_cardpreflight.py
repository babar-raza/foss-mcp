"""The card preflight (Round-3 item SUP-1): each rule fails a card with the defect it was written for."""

from __future__ import annotations

import cardpreflight as P

REAL_MUTATE = "python -X utf8 -c \"print('changes a file')\"\n"


def card(**kw):
    base = {
        "id": "TC-900",
        "purpose": "a finalised purpose",
        "write_paths": ["src/x.py", "tests/test_x.py"],
        "min_tests": 3,
        "checks": [{"command": "{python} -m pytest tests/test_x.py -q -rfE -p no:cacheprovider"}],
        "negative_control": {"mutate": REAL_MUTATE, "rationale": "breaks the claim"},
    }
    base.update(kw)
    return base


def repo_with(tmp_path, *files):
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    return tmp_path


def count_of(n):
    return lambda paths, args: n


def test_split_pytest_reads_paths_and_selection_options():
    cmd = '{python} -m pytest tests/a.py tests/b/ -q -rfE -p no:cacheprovider -k "not live and not rust" --ignore tests/c.py'
    paths, args = P.split_pytest(cmd)
    assert paths == ["tests/a.py", "tests/b/"]
    assert args == ["-k", "not live and not rust", "--ignore", "tests/c.py"]


def test_split_pytest_ignores_node_ids_suffix_and_non_pytest_commands():
    assert P.split_pytest("{python} -m pytest tests/a.py::test_one -q")[0] == ["tests/a.py"]
    assert P.split_pytest("{python} scripts/x.py --flag") is None


def test_a_clean_card_has_no_problems(tmp_path):
    repo = repo_with(tmp_path, "src/x.py", "tests/test_x.py")
    problems, warnings = P.preflight(card(), repo, count_of(5))
    assert problems == [] and warnings == []


def test_min_tests_above_the_collected_count_is_a_problem(tmp_path):
    """TC-554 and TC-555: min_tests 300 and 500 against checks that collect 96 and 394 tests."""
    repo = repo_with(tmp_path, "src/x.py", "tests/test_x.py")
    problems, _ = P.preflight(card(min_tests=300), repo, count_of(96))
    assert any("collects 96 tests but min_tests is 300" in p for p in problems)


def test_a_check_naming_a_missing_file_is_a_problem(tmp_path):
    """TC-567: the check named tests/deploy/test_e2e_pins_match_shipped_data.py, which did not exist yet."""
    repo = repo_with(tmp_path, "src/x.py", "tests/test_x.py")
    c = card(checks=[{"command": "{python} -m pytest tests/test_x.py tests/deploy/test_missing.py -q"}])
    problems, _ = P.preflight(c, repo, count_of(9))
    assert any("tests/deploy/test_missing.py" in p and "not in write_paths" in p for p in problems)


def test_a_new_test_file_declared_in_write_paths_is_allowed_with_a_warning_when_the_count_is_unknowable(
    tmp_path,
):
    repo = repo_with(tmp_path, "src/x.py")
    c = card(
        min_tests=100,
        write_paths=["src/x.py", "tests/test_new.py"],
        checks=[{"command": "{python} -m pytest tests/test_new.py -q"}],
    )
    problems, warnings = P.preflight(c, repo, count_of(0))
    assert problems == []
    assert any("must add at least 100" in w for w in warnings)


def test_new_tests_plus_existing_ones_are_counted_together(tmp_path):
    repo = repo_with(tmp_path, "src/x.py", "tests/test_old.py")
    c = card(
        min_tests=30,
        write_paths=["tests/test_new.py"],
        checks=[{"command": "{python} -m pytest tests/test_new.py tests/test_old.py -q"}],
    )
    problems, warnings = P.preflight(c, repo, count_of(28))
    assert problems == []
    assert any("existing + new tests must reach min_tests 30" in w for w in warnings)


def test_the_registration_placeholder_negative_control_is_a_problem(tmp_path):
    repo = repo_with(tmp_path, "src/x.py", "tests/test_x.py")
    placeholder = {
        "mutate": "python -X utf8 -c \"print('negative control to be authored before dispatch')\"\n",
        "rationale": "PLACEHOLDER written at registration.",
    }
    problems, _ = P.preflight(card(negative_control=placeholder), repo, count_of(5))
    assert any("registration placeholder" in p for p in problems)


def test_an_empty_mutate_is_a_problem(tmp_path):
    repo = repo_with(tmp_path, "src/x.py", "tests/test_x.py")
    problems, _ = P.preflight(card(negative_control={"mutate": "  ", "rationale": "r"}), repo, count_of(5))
    assert any("no mutate command" in p for p in problems)


def test_a_card_still_marked_as_a_draft_is_a_problem(tmp_path):
    repo = repo_with(tmp_path, "src/x.py", "tests/test_x.py")
    problems, _ = P.preflight(
        card(purpose="it does x. REGISTERED AS A DRAFT on 2026-10-19"), repo, count_of(5)
    )
    assert any("REGISTERED AS A DRAFT" in p for p in problems)


def test_a_write_path_that_does_not_exist_outside_tests_scripts_docs_is_a_warning(tmp_path):
    repo = repo_with(tmp_path, "tests/test_x.py")
    _, warnings = P.preflight(card(write_paths=["src/typo/missing.py", "tests/test_x.py"]), repo, count_of(5))
    assert any("src/typo/missing.py" in w for w in warnings)


def test_an_uncountable_check_is_a_warning_not_a_problem(tmp_path):
    repo = repo_with(tmp_path, "src/x.py", "tests/test_x.py")
    problems, warnings = P.preflight(card(), repo, lambda paths, args: None)
    assert problems == []
    assert any("could not count" in w for w in warnings)


def test_selection_options_reach_the_counter(tmp_path):
    repo = repo_with(tmp_path, "src/x.py", "tests/test_x.py")
    seen = {}

    def counter(paths, args):
        seen["args"] = args
        return 9

    c = card(checks=[{"command": '{python} -m pytest tests/test_x.py -q -k "not live"'}])
    P.preflight(c, repo, counter)
    assert seen["args"] == ["-k", "not live"]


def test_dispatch_next_refuses_a_card_that_fails_the_preflight(monkeypatch):
    import gatecli as C
    import gatectl as G

    monkeypatch.setattr(C.V, "rebuild_state", lambda: {"cards": []})
    monkeypatch.setattr(
        C,
        "_preflight_card",
        lambda cid: (["TC-900 check 1: pytest collects 96 tests but min_tests is 300"], []),
    )
    called = []
    monkeypatch.setattr(C, "cmd_instruct", lambda ns: called.append(ns) or G.EXIT_OK)
    args = type(
        "A", (), {"card": "TC-900", "kind": "dispatch", "instruction": None, "attempt": 1, "preflight": True}
    )()
    assert C.cmd_dispatch_next(args) == G.EXIT_FAIL
    assert called == [], "a refused card must not be dispatched"


def test_dispatch_next_dispatches_a_card_that_passes_the_preflight(monkeypatch):
    import gatecli as C
    import gatectl as G

    monkeypatch.setattr(C.V, "rebuild_state", lambda: {"cards": []})
    monkeypatch.setattr(C, "_preflight_card", lambda cid: ([], ["TC-900: a warning"]))
    called = []
    monkeypatch.setattr(C, "cmd_instruct", lambda ns: called.append(ns) or G.EXIT_OK)
    args = type(
        "A", (), {"card": "TC-900", "kind": "dispatch", "instruction": None, "attempt": 1, "preflight": True}
    )()
    assert C.cmd_dispatch_next(args) == G.EXIT_OK
    assert called and called[0].target == "TC-900"


def test_the_real_cli_turns_the_preflight_on_for_dispatch_next():
    import gatecli as C

    source = open(C.__file__, encoding="utf-8").read()
    assert "sp.set_defaults(preflight=True)" in source
