"""Canaries for the worker commit path (AGENTS.md: a worker commits only its own card).

Before this change, FOSS_MCP_WORKER=1 skipped the commit guard for every path, so a worker
could commit anything the hook let through. These tests pin the rule that replaces it: a
worker names its own card, and may commit only the paths that card declares.
"""

from __future__ import annotations

import gatecli as C


def test_staged_paths_inside_write_paths_are_allowed():
    assert C._worker_commit_problems("TC-1", ["infra/**"], ["infra/helm/foss-mcp/a.yaml"]) == []


def test_a_staged_path_outside_write_paths_is_refused():
    problems = C._worker_commit_problems("TC-1", ["infra/**"], ["scripts/toolchain/activate.ps1"])
    assert problems and "outside TC-1" in problems[0]


def test_a_globally_denied_path_is_refused_even_when_a_write_path_would_match():
    problems = C._worker_commit_problems("TC-1", ["ops/**"], ["ops/gatectl.py"])
    assert problems and "globally denied" in problems[0]


def test_an_empty_stage_is_refused():
    assert C._worker_commit_problems("TC-1", ["infra/**"], [])


def test_every_bad_path_is_reported_not_just_the_first():
    problems = C._worker_commit_problems("TC-1", ["infra/**"], ["infra/ok.yaml", "x/a.py", "y/b.py"])
    assert len(problems) == 2
