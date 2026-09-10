"""Prove the pre-push gate fails CLOSED on an unrecognised remote (G0 / TC-006).

The hook this project forked matched the remote URL with a shell `case` and
`exit 0`'d on any non-match. Git treats hook exit 0 as "hook passed" - so a
push to a differently-named remote silently ran no checks at all, and nothing
about that failure is visible anywhere (no error, no log line someone would
notice). `.githooks/pre-push` deliberately inverts this: an unrecognised or
empty remote URL must BLOCK (non-zero exit), not wave the push through.

These tests prove that behavior by actually invoking the hook as a subprocess
- never by inspecting its source text - because the whole point of the card
is that fail-open and fail-closed hooks can look identical on paper (both are
short scripts with an `exit` in them) while behaving oppositely at the only
interface that matters: the exit code git reads.

Deliberately NOT exercised here: pushing with this repo's own ("*foss-mcp*")
remote URL. That path runs the full scripts/ci_check.sh suite, which is slow
and is not this card's concern - this card only proves the refusal paths.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / ".githooks" / "pre-push"


def _run_hook(remote_name: str, remote_url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK_PATH), remote_name, remote_url],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_hook_file_exists_and_is_not_empty():
    assert HOOK_PATH.is_file(), f"{HOOK_PATH} does not exist"
    assert HOOK_PATH.stat().st_size > 0, f"{HOOK_PATH} is empty"


def test_nonmatching_remote_url_exits_nonzero():
    """A remote that is plainly not this project must BLOCK the push.

    This is the exact defect being guarded against: the forked original
    exited 0 here, which git reports as the hook PASSING.
    """
    result = _run_hook("origin", "https://github.com/someone/unrelated.git")
    assert result.returncode != 0, (
        "pre-push exited 0 for a non-matching remote URL - this is the fail-"
        f"open regression the card exists to prevent.\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )


def test_empty_remote_url_exits_nonzero():
    """No remote URL supplied must BLOCK rather than assume safety."""
    result = _run_hook("origin", "")
    assert result.returncode != 0, (
        "pre-push exited 0 for an empty remote URL - it must refuse rather "
        f"than assume safe.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_core_hooks_path_is_configured():
    """A versioned hook under .githooks/ is inert until core.hooksPath points
    at it - git otherwise only looks in .git/hooks/, which is untracked and
    per-clone. Nothing else in this repository reports whether that has been
    done, so this is the only place it is asserted.
    """
    result = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        "git config core.hooksPath is not set at all - versioned hooks under "
        f".githooks/ will never run.\nstderr={result.stderr}"
    )
    assert result.stdout.strip() == ".githooks", (
        f"core.hooksPath is {result.stdout.strip()!r}, expected '.githooks'"
    )
