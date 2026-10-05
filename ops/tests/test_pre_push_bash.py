"""Canary: the pre-push hook runs the CI check with the bash that runs the hook (2026-10-05).

A bare `bash` on this Windows host is the WSL launcher, C:\\Windows\\System32\\bash.exe. With no WSL
distribution installed it exits non-zero with no output, so every push was blocked before a single CI
step ran, while the same script passed when run directly. The hook must invoke the running bash.
"""

from __future__ import annotations

import re

import gatectl as G

HOOK = G.REPO / ".githooks" / "pre-push"


def test_the_hook_runs_ci_with_the_running_bash_not_a_bare_bash():
    text = HOOK.read_text(encoding="utf-8")
    assert '"${BASH:-bash}" scripts/ci_check.sh' in text
    assert not re.search(r"(?m)^\s*(if !\s*)?bash scripts/ci_check\.sh", text), (
        "a bare bash resolves to WSL here"
    )


def test_the_hook_still_fails_closed_on_an_unknown_remote():
    text = HOOK.read_text(encoding="utf-8")
    assert "fails CLOSED" in text or "refusing" in text
    assert "exit 1" in text
