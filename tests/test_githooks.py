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

CAUTION - a previous attempt at this file invoked the hook with an *absolute*
path (`str(HOOK_PATH)`, e.g. `D:\\Users\\...\\pre-push`). Git Bash mangles a
Windows-style absolute path like that into garbage
(`DUsers...githookspre-push`) and returns exit code 127 ("command not
found") - which is non-zero, so a bare `result.returncode != 0` assertion
passed even though the hook itself never ran. A hook hard-coded to `exit 0`
still made that suite report 4 passed. Every test below therefore:

  1. invokes the hook by a path RELATIVE to `cwd=REPO_ROOT`
     (`bash .githooks/pre-push ...`), which Git Bash resolves correctly, and
  2. asserts on more than "nonzero" - either the hook's own refusal text on
     stderr, or an explicit rejection of the 127 fail-open-by-accident case -
     so a hook that silently fails to execute cannot be mistaken for a hook
     that ran and refused.

Deliberately NOT exercised here: pushing with this repo's own ("*foss-mcp*")
remote URL. That path runs the full scripts/ci_check.sh suite, which is slow
and is not this card's concern - this card only proves the refusal paths.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / ".githooks" / "pre-push"
HOOK_RELATIVE = ".githooks/pre-push"


def _run_hook(remote_name: str, remote_url: str) -> subprocess.CompletedProcess:
    """Invoke the hook by a path RELATIVE to cwd=REPO_ROOT.

    Do not pass an absolute path here (e.g. str(HOOK_PATH)) - on Windows,
    Git Bash mangles an absolute Windows-style path (drive letter, backslashes)
    into something that does not resolve, and bash exits 127 ("command not
    found") without ever reading the hook's contents. A relative path resolved
    against `cwd` is what git itself uses to invoke hooks, and it is what
    actually executes this file.
    """
    return subprocess.run(
        ["bash", HOOK_RELATIVE, remote_name, remote_url],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_hook_file_exists_and_is_not_empty():
    assert HOOK_PATH.is_file(), f"{HOOK_PATH} does not exist"
    assert HOOK_PATH.stat().st_size > 0, f"{HOOK_PATH} is empty"


def test_hook_is_actually_executed_by_bash_not_a_mangled_path():
    """Guard against the historical false-pass: invoking the hook via an
    absolute Windows path makes Git Bash return 127 (command not found)
    without ever running the script, and a bare `!= 0` assertion would wrongly
    treat that as "the hook blocked the push". This test proves bash can
    actually locate and execute `.githooks/pre-push` at all.

    Uses this project's own remote *name* ("origin") together with a URL that
    deliberately does NOT contain "foss-mcp", so the fast refusal path is
    taken rather than the slow scripts/ci_check.sh path.
    """
    result = _run_hook("origin", "https://github.com/someone/unrelated.git")
    assert result.returncode != 127, (
        "hook invocation returned 127 (command not found) - bash could not "
        "find/execute .githooks/pre-push at all (e.g. an absolute path being "
        f"mangled by Git Bash), so nothing about the hook's own logic was "
        f"actually tested.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "refusing" in result.stderr, (
        "expected the hook's own refusal message on stderr, proving the "
        f"script ran (not just 'exited nonzero').\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )


def test_nonmatching_remote_url_exits_nonzero_and_refuses():
    """A remote that is plainly not this project must BLOCK the push.

    This is the exact defect being guarded against: the forked original
    exited 0 here, which git reports as the hook PASSING. Asserting only
    `returncode != 0` is not enough on its own - bash returns 127 for a
    mangled/unresolvable path, which is also nonzero but means the hook
    never ran. So this also pins the exit code away from 127 and checks the
    hook's own refusal text landed on stderr.
    """
    result = _run_hook("origin", "https://github.com/someone/unrelated.git")
    assert result.returncode not in (0, 127), (
        "pre-push exited 0 (fail-open regression) or 127 (hook never actually "
        f"ran) for a non-matching remote URL.\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    assert "refusing" in result.stderr, (
        "hook blocked the push but did not print its expected refusal "
        f"message - cannot confirm it refused for the right reason.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_empty_remote_url_exits_nonzero_and_refuses():
    """No remote URL supplied must BLOCK rather than assume safety."""
    result = _run_hook("origin", "")
    assert result.returncode not in (0, 127), (
        "pre-push exited 0 (fail-open regression) or 127 (hook never actually "
        f"ran) for an empty remote URL.\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    assert "refusing" in result.stderr, (
        "hook blocked the push but did not print its expected refusal "
        f"message - cannot confirm it refused for the right reason.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
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


def test_gitlab_mirror_remote_is_recognized_as_this_project_not_refused():
    """GitHub is the source of truth; a GitHub Actions workflow pushes `main` and
    tags to a GitLab mirror on every push (`.github/workflows/mirror-gitlab.yml`).
    That workflow authenticates over HTTPS with a token embedded in the URL, not
    through a named local remote, so this hook only matters here if someone ever
    pushes to the mirror by hand from a clone that has it configured as a remote.
    Either way, the URL must be recognized as "this project", not refused.

    A matching URL falls through into `scripts/ci_check.sh`, which is slow and is
    deliberately not exercised by the other tests in this file. So this test only
    proves the pattern match: run the hook with a short timeout and confirm it
    printed "running scripts/ci_check.sh" (proceeded past the case statement)
    rather than its refusal text, without waiting for the full suite to finish.

    Accepted bounded risk: on timeout, `subprocess.run` terminates the immediate
    `bash` child but does not recursively kill whatever short-lived lint process
    it had just started. Two seconds is chosen to land inside the fast `ruff
    check` step, which self-terminates almost immediately even if orphaned, and
    the calls made in that window are all read-only.
    """
    gitlab_url = "https://gitlab.recruitize.ai/sialkot/cantt-smallize/aspose-foss-dev-context-mcp.git"
    try:
        result = subprocess.run(
            ["bash", HOOK_RELATIVE, "gitlab", gitlab_url],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
        )
        combined = result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        combined = out + err

    assert "refusing" not in combined, (
        f"the gitlab mirror URL was refused instead of recognized as this project's own remote: "
        f"{combined[:300]}"
    )
    assert "running scripts/ci_check.sh" in combined, (
        f"the hook did not proceed past the case statement for the gitlab mirror URL, so the "
        f"pattern match is unproven: {combined[:300]}"
    )
