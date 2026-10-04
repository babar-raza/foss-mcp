"""Canary tests for the D7 atomic integrate step (DECISION_LOG 2026-10-04).

The end-to-end tests build a real throwaway git repository. They point the governance
module at it, and they redirect the state file, so the real project/state.yaml is never
written. The failure tests prove that nothing changes when a precondition fails.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import gatectl as G
import gateverify as V
import integrate as I
import pytest


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway repository: main with two files, and a worker branch off it."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "test@example.invalid")
    _git(r, "config", "user.name", "test")
    (r / "src").mkdir()
    (r / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (r / "project").mkdir()
    (r / "project" / "state.yaml").write_text("old: true\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "checkout", "-q", "-b", "worker/TC-901")
    (r / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "feat: worker change one")
    (r / "src" / "c.py").write_text("z = 3\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "feat: worker change two")
    head = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "main")

    monkeypatch.setattr(G, "REPO", r)
    monkeypatch.setattr(G, "STATE_FILE", r / "project" / "state.yaml")
    monkeypatch.setattr(V, "rebuild_state", lambda: {"schema_version": 1, "cards": []})
    monkeypatch.setattr(
        V,
        "load_receipt",
        lambda gate, card: {"accepted": True, "head_rev": head} if card == "TC-901" else None,
    )
    return r


def test_end_to_end_replays_the_reviewed_commits_and_records_evidence(repo):
    result = I.integrate("TC-901")
    assert len(result["replayed"]) == 2
    log = _git(repo, "log", "--format=%s", "-n", "3")
    assert "worker change two" in log and "worker change one" in log
    assert (repo / "src" / "c.py").exists()
    assert "rebuild derived state" in _git(repo, "log", "--format=%s", "-n", "1")


def test_a_conflict_rolls_main_back_to_where_it_started(repo, monkeypatch):
    # Main changes the same file the worker changes, so the second cherry-pick must conflict.
    (repo / "src" / "a.py").write_text("x = 999\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "main diverges")
    _git(repo, "checkout", "-q", "worker/TC-901")
    (repo / "src" / "a.py").write_text("x = 555\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "worker edits a.py")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    monkeypatch.setattr(
        V,
        "load_receipt",
        lambda gate, card: {"accepted": True, "head_rev": head} if card == "TC-901" else None,
    )
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(I.IntegrateRefused, match="rolled back"):
        I.integrate("TC-901")
    assert _git(repo, "rev-parse", "HEAD") == before, "main must be exactly as it was"


def test_an_unaccepted_card_is_refused_before_anything_is_written(repo, monkeypatch):
    monkeypatch.setattr(V, "load_receipt", lambda gate, card: {"accepted": False, "head_rev": "0" * 40})
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(I.IntegrateRefused, match="not accepted"):
        I.integrate("TC-901")
    assert _git(repo, "rev-parse", "HEAD") == before


def test_a_dirty_product_path_refuses_the_integration(repo):
    (repo / "src" / "untracked.py").write_text("q = 0\n", encoding="utf-8")
    with pytest.raises(I.IntegrateRefused, match="uncommitted paths"):
        I.plan("TC-901")


def test_a_reviewed_commit_not_on_the_branch_is_refused(repo, monkeypatch):
    monkeypatch.setattr(V, "load_receipt", lambda gate, card: {"accepted": True, "head_rev": "f" * 40})
    with pytest.raises(I.IntegrateRefused):
        I.plan("TC-901")


def test_the_status_line_is_copied_verbatim_once(tmp_path):
    wt = tmp_path / "wt"
    (wt / "ops").mkdir(parents=True)
    line = json.dumps(
        {
            "ts": "2026-10-04T01:00:00Z",
            "card": "TC-901",
            "phase": "committed",
            "verdict": "pass",
            "summary": "x",
            "commit": "a" * 40,
            "attempt": 1,
        }
    )
    (wt / "ops" / "status.jsonl").write_text(line + "\n", encoding="utf-8")
    main_status = tmp_path / "status.jsonl"
    main_status.write_text("", encoding="utf-8")
    assert I._status_line_for("TC-901", "a" * 40, wt, main_status) == line
    main_status.write_text(line + "\n", encoding="utf-8")
    assert I._status_line_for("TC-901", "a" * 40, wt, main_status) is None, "never copied twice"


def test_a_status_line_is_found_even_when_its_hash_predates_a_rebase(tmp_path):
    # Canary (2026-10-04). A rebase changes the commit hash but not the content. The worker's
    # committed line still carries the old hash, so matching on the reviewed head found nothing.
    wt = tmp_path / "wt"
    (wt / "ops").mkdir(parents=True)
    line = json.dumps({"ts": "2026-10-04T10:14:43Z", "card": "TC-901", "phase": "committed",
                       "verdict": "pass", "summary": "x", "commit": "1" * 40, "attempt": 2})
    (wt / "ops" / "status.jsonl").write_text(line + "\n", encoding="utf-8")
    main_status = tmp_path / "status.jsonl"
    main_status.write_text("", encoding="utf-8")
    assert I._status_line_for("TC-901", "2" * 40, wt, main_status) == line


def test_supervisor_owned_paths_do_not_count_as_dirty_product(repo, monkeypatch):
    monkeypatch.setattr(I.G, "git", lambda *a, **k: (0, " M ops/gatecli.py\n?? plans/TC-999.yaml\n", ""))
    clean, dirty = I._tree_is_clean_of_product_paths()
    assert clean and dirty == []


def test_the_first_porcelain_line_keeps_its_full_path(monkeypatch):
    # Regression canary (2026-10-04). G.git strips the whole output, so the first line of
    # `git status --porcelain` lost its leading space. A fixed `line[3:]` then reported
    # "vidence/build/..." as a dirty product path and refused a valid integration.
    monkeypatch.setattr(I.G, "git", lambda *a, **k: (0, " M evidence/build/G2/TC-175/receipt.json\n M project/state.yaml", ""))
    clean, dirty = I._tree_is_clean_of_product_paths()
    assert clean and dirty == [], f"governance paths must not be dirty product paths: {dirty}"
    monkeypatch.setattr(I.G, "git", lambda *a, **k: (0, " M src/foss_mcp/x.py", ""))
    clean, dirty = I._tree_is_clean_of_product_paths()
    assert dirty == ["src/foss_mcp/x.py"], "a real product path must still be reported in full"


def test_the_integrate_command_is_wired_into_the_cli():
    import gatecli as C

    assert "integrate" in C.HANDLERS
