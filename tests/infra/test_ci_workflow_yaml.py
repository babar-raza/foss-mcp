"""TC-603 (REQ-G3-010): CI itself must keep re-proving the retrieval corpus's
PYTHONHASHSEED determinism and must run a conservatively-scoped Windows job.

The workflow's own jobs cannot be invoked from inside this repository's test suite (that
would require GitHub Actions itself), so this file verifies the COMMITTED
.github/workflows/ci.yml's structure instead: it parses the file with yaml.safe_load and
asserts the facts TC-603.yaml's checks describe. Offline and static - it only reads the
workflow file, never executes it.

The ``_CI_JOB_STEPS_BEFORE_TC_603`` fixture below is the real "before" baseline: it was
captured verbatim from ``git show HEAD:.github/workflows/ci.yml`` BEFORE any TC-603 edit was
made, so the "unchanged `ci` job" assertion is a real diff against history, not a copy of
whatever the file happens to say after editing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"

# Captured from `git show HEAD:.github/workflows/ci.yml` at commit 8d5b2dc3 (the tip of
# worker/TC-601, i.e. main as checked out for this card), BEFORE this card made any edit.
_CI_JOB_STEPS_BEFORE_TC_603: list[dict[str, Any]] = [
    {
        "uses": "actions/checkout@v5",
        "with": {"fetch-depth": 0},
    },
    {
        "uses": "actions/setup-python@v5",
        "with": {"python-version": "3.13"},
    },
    {
        "name": "Point git at the versioned hooks",
        "run": "git config core.hooksPath .githooks",
    },
    {
        "name": "Install dependencies (hash-verified)",
        "run": "pip install --no-cache-dir --require-hashes -r requirements.lock",
    },
    {
        "name": "Run local CI-equivalent checks (lint, format, typecheck, pytest, gatectl)",
        "env": {"PYTEST_ADDOPTS": "--ignore=tests/e2e --ignore=tests/infra"},
        "run": "bash scripts/ci_check.sh",
    },
]


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _step_text(step: dict[str, Any]) -> str:
    """All text content of a step worth scanning: its run: body plus every env/with value."""
    parts: list[str] = []
    for key in ("run", "name", "uses"):
        value = step.get(key)
        if isinstance(value, str):
            parts.append(value)
    for section in ("env", "with"):
        block = step.get(section)
        if isinstance(block, dict):
            for value in block.values():
                parts.append(str(value))
    return "\n".join(parts)


def test_workflow_file_parses_as_valid_yaml() -> None:
    doc = _workflow()
    assert isinstance(doc, dict)
    assert "jobs" in doc


def test_both_new_jobs_exist_by_name() -> None:
    jobs = _workflow()["jobs"]
    assert "determinism" in jobs, "determinism job missing from .github/workflows/ci.yml"
    assert "windows-python-only" in jobs, "windows-python-only job missing from .github/workflows/ci.yml"


def test_windows_job_runs_on_windows_latest() -> None:
    job = _workflow()["jobs"]["windows-python-only"]
    assert job["runs-on"] == "windows-latest"


def test_windows_job_never_references_activate_ps1_or_dev_tools() -> None:
    job = _workflow()["jobs"]["windows-python-only"]
    for step in job["steps"]:
        text = _step_text(step).lower()
        assert "activate.ps1" not in text, f"windows-python-only step references activate.ps1: {step}"
        assert "c:\\dev-tools" not in text, f"windows-python-only step references C:\\dev-tools: {step}"


def test_windows_job_collects_a_non_empty_test_scope() -> None:
    """Guard against the job silently running zero tests (card's own warning)."""
    job = _workflow()["jobs"]["windows-python-only"]
    combined = "\n".join(_step_text(step) for step in job["steps"])
    assert "pytest" in combined
    # The scope must name at least one real test directory, not just the word "pytest".
    assert "tests/" in combined or "tests\\" in combined


def test_determinism_job_uses_at_least_three_distinct_pythonhashseed_values() -> None:
    job = _workflow()["jobs"]["determinism"]
    seeds: set[str] = set()
    for step in job["steps"]:
        env = step.get("env")
        if isinstance(env, dict) and "PYTHONHASHSEED" in env:
            seeds.add(str(env["PYTHONHASHSEED"]))
    assert len(seeds) >= 3, f"expected >= 3 distinct PYTHONHASHSEED values, found {seeds!r}"


def test_determinism_job_runs_the_retrieval_corpus_each_time() -> None:
    job = _workflow()["jobs"]["determinism"]
    runs_with_seed_and_corpus = 0
    for step in job["steps"]:
        env = step.get("env")
        run = step.get("run", "")
        if isinstance(env, dict) and "PYTHONHASHSEED" in env and "test_round3_retrieval_corpus.py" in run:
            runs_with_seed_and_corpus += 1
    assert runs_with_seed_and_corpus >= 3


def test_determinism_job_has_an_upload_artifact_step() -> None:
    job = _workflow()["jobs"]["determinism"]
    assert any(
        isinstance(step.get("uses"), str) and step["uses"].startswith("actions/upload-artifact")
        for step in job["steps"]
    )


def test_determinism_job_compares_the_three_junit_runs() -> None:
    """A step must actually diff the three runs' outcomes, not just produce them."""
    job = _workflow()["jobs"]["determinism"]
    combined = "\n".join(_step_text(step) for step in job["steps"])
    assert combined.count(".xml") >= 3, "expected at least 3 distinct junit XML references"
    assert "diff" in combined.lower() or "compar" in combined.lower()


def test_ci_job_steps_are_unchanged_from_before_this_card() -> None:
    """The existing `ci` job must be untouched: same step count, same content."""
    ci_job = _workflow()["jobs"]["ci"]
    assert ci_job["steps"] == _CI_JOB_STEPS_BEFORE_TC_603
    assert len(ci_job["steps"]) == len(_CI_JOB_STEPS_BEFORE_TC_603)


def test_ci_job_runs_on_is_unchanged() -> None:
    ci_job = _workflow()["jobs"]["ci"]
    assert ci_job["runs-on"] == "ubuntu-latest"
