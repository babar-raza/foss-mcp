"""Regression tests for `gatectl commit-guard`.

Real incident, twice: `git add -A` while a worker's card was in flight swept its
uncommitted deliverable into a supervisor commit under the wrong authorship,
message, and trailer - and because the commit carried the supervisor trailer,
scope attribution classified it as governance and silently skipped scope-checking
that product code entirely.

The fix went through two shapes and both needed a real test:

1. A path heuristic (GLOBAL_DENY-complement) that blocks any "product-shaped"
   uncommitted path. This over-blocks: the supervisor doing legitimate direct
   infrastructure work (`.github/workflows/**`, a governance test file) outside
   GLOBAL_DENY gets refused identically to a genuinely swept worker deliverable.
2. The actual signal: whether a worker dispatch is currently OPEN. Nothing in
   the tree can be an in-flight worker's forgotten work when no card is
   dispatched-and-unacknowledged, by definition - so that case must be exempted
   from the path heuristic entirely, and the heuristic only applies while a
   dispatch is genuinely open.

Both directions are asserted here, against a real git repo (not a mock), because
`git status --porcelain` is the actual signal the guard reads.
"""

from __future__ import annotations

import json
import subprocess

import gatecli as C
import gatectl as G
import pytest


def sh(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout.strip()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    r = tmp_path / "r"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    sh(r, "config", "user.email", "t@example.com")
    sh(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n", encoding="utf-8")
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", "base")

    ops = r / "ops"
    ops.mkdir()
    monkeypatch.setattr(G, "REPO", r)
    monkeypatch.setattr(G, "OPS", ops)
    monkeypatch.setattr(G, "INSTRUCTIONS_JSONL", ops / "instructions.jsonl")
    monkeypatch.setattr(G, "STATUS_JSONL", ops / "status.jsonl")
    return r


class Args:
    pass


def run_guard():
    return C.cmd_commit_guard(Args())


def dispatch(repo, card="TC-999", kind="dispatch", attempt=1):
    line = {
        "ts": "2026-09-24T00:00:00Z",
        "target_card": card,
        "kind": kind,
        "instruction": "go",
        "card_sha256": "0" * 64,
        "issue_rev": "0" * 40,
        "attempt": attempt,
    }
    with G.INSTRUCTIONS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def close_dispatch(repo, card="TC-999", attempt=1, commit="a" * 40):
    line = {
        "ts": "2026-09-24T00:01:00Z",
        "card": card,
        "phase": "committed",
        "verdict": "pass",
        "summary": "done",
        "commit": commit,
        "attempt": attempt,
    }
    with G.STATUS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def test_no_dispatch_open_allows_a_supervisor_commit_over_any_uncommitted_path(repo):
    """The false-positive this design fixed: legitimate supervisor infra work
    (here standing in for `.github/workflows/**`) must not be blocked when no
    card is in flight - nothing in the tree can be a worker's forgotten card."""
    (repo / "some_infra_file.yml").write_text("x\n", encoding="utf-8")
    assert not G.INSTRUCTIONS_JSONL.exists(), "precondition: no instructions at all"
    assert run_guard() == G.EXIT_OK


def test_an_open_dispatch_blocks_a_stray_product_path(repo):
    """The real incident: a card is dispatched, its deliverable is sitting
    uncommitted, and the supervisor must be refused rather than sweep it up."""
    dispatch(repo, card="TC-999")
    (repo / "src_module.py").write_text("x = 1\n", encoding="utf-8")
    assert run_guard() == G.EXIT_FAIL


def test_closing_the_dispatch_releases_the_guard_even_with_the_same_file_present(repo):
    """Once the worker's `committed` status line lands, the dispatch is no
    longer open - the guard trusts that signal rather than re-scanning paths."""
    dispatch(repo, card="TC-999")
    (repo / "src_module.py").write_text("x = 1\n", encoding="utf-8")
    assert run_guard() == G.EXIT_FAIL

    close_dispatch(repo, card="TC-999")
    assert run_guard() == G.EXIT_OK


def test_a_rework_dispatch_at_a_new_attempt_reopens_the_guard(repo):
    """A card can be dispatched, closed, and re-dispatched (rework) - the guard
    must track the LATEST attempt, not just "has this card ever closed"."""
    dispatch(repo, card="TC-999", attempt=1)
    close_dispatch(repo, card="TC-999", attempt=1)
    assert run_guard() == G.EXIT_OK

    dispatch(repo, card="TC-999", kind="rework", attempt=2)
    (repo / "src_module.py").write_text("x = 1\n", encoding="utf-8")
    assert run_guard() == G.EXIT_FAIL

    close_dispatch(repo, card="TC-999", attempt=2)
    assert run_guard() == G.EXIT_OK


def test_governance_paths_are_never_blocked_even_with_a_dispatch_open(repo):
    """GLOBAL_DENY/docs stay exempt regardless of dispatch state - a supervisor
    editing its own governance files is never mistaken for swept worker work."""
    dispatch(repo, card="TC-999")
    (repo / "docs").mkdir()
    (repo / "docs" / "DECISION_LOG.md").write_text("x\n", encoding="utf-8")
    assert run_guard() == G.EXIT_OK
