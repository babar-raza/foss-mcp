"""Regression test for the 2026-10-10 incident (docs/DECISION_LOG.md): an ambient GIT_DIR,
inherited from a linked worktree's own hook invocation, overrode git's normal repo discovery
and let a test fixture's throwaway `git init`/`git config` land in this real repo's shared
.git/config instead of its own tmp_path - also setting core.bare=true, and together explaining
both that corruption and a separate commit-authorship incident the same day.

This proves `gatectl.run`/`gatectl.git` (ops/gatectl.py) - the one shared subprocess helper
nearly every other command in this governance tool is built on - never lets GIT_DIR/
GIT_WORK_TREE/etc. leak through from its own caller's environment, with a REAL leaked value
actually present (via monkeypatch.setenv), not merely absent by chance in this CI run. Without
`run()`'s own env-scrub, `git -C <cwd> rev-parse --show-toplevel` would resolve to whatever
bogus/real path GIT_DIR points at instead of `cwd`, exactly reproducing the incident.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import gatecli as C
import gatectl as G


def _clean_env() -> dict[str, str]:
    """A real environment with no GIT_* repo-discovery vars - used only to set up the
    sentinel repo itself, never the call under test (that must scrub them on its own)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def test_run_never_inherits_an_ambient_git_dir_or_work_tree(monkeypatch, tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=sentinel, check=True, env=_clean_env())

    bogus_gitdir = tmp_path / "not-the-sentinel" / ".git"
    monkeypatch.setenv("GIT_DIR", str(bogus_gitdir))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "not-the-sentinel"))

    rc, out, err = G.git("rev-parse", "--show-toplevel", cwd=sentinel)

    assert rc == 0, f"git call failed under a leaked GIT_DIR: {err}"
    assert Path(out).resolve() == sentinel.resolve(), (
        f"GIT_DIR leaked through: resolved toplevel {out!r} is not the real sentinel "
        f"{sentinel!r} passed via cwd - this is exactly the 2026-10-10 corruption mechanism"
    )


def test_run_never_inherits_an_ambient_git_index_file(monkeypatch, tmp_path: Path) -> None:
    """GIT_INDEX_FILE alone (no GIT_DIR) is a separate, narrower leak vector - a stray index
    path can make a commit silently include/exclude the wrong files relative to the real
    index at `cwd`. Checked independently of the GIT_DIR case above."""
    sentinel = tmp_path / "sentinel2"
    sentinel.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=sentinel, check=True, env=_clean_env())
    (sentinel / "real.txt").write_text("real\n", encoding="utf-8")

    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "bogus-index"))

    rc, out, err = G.git("add", "-A", cwd=sentinel)
    assert rc == 0, f"git add failed under a leaked GIT_INDEX_FILE: {err}"
    rc, out, err = G.git("status", "--porcelain", cwd=sentinel)
    assert rc == 0, err
    assert "real.txt" in out, (
        f"GIT_INDEX_FILE leaked through: the sentinel's own real.txt is not staged as "
        f"expected ({out!r}) - the add almost certainly wrote to the bogus index instead"
    )


def test_identity_problems_is_clean_on_a_normal_checkout(monkeypatch, tmp_path: Path) -> None:
    r = tmp_path / "clean_repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True, env=_clean_env())
    monkeypatch.setattr(G, "REPO", r)
    assert C._git_identity_problems() == []


def test_identity_problems_catches_a_local_user_email_override(monkeypatch, tmp_path: Path) -> None:
    """The exact corruption left behind by the 2026-10-10 incident: a LOCAL (not global)
    user.email, which only ever gets set by the leaked-GIT_DIR bug this suite guards against -
    this is `gatectl validate`'s own always-on detector for it, independent of either hook."""
    r = tmp_path / "bad_repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True, env=_clean_env())
    subprocess.run(
        ["git", "config", "--local", "user.email", "test@example.invalid"],
        cwd=r, check=True, env=_clean_env(),
    )
    monkeypatch.setattr(G, "REPO", r)
    problems = C._git_identity_problems()
    assert any("user.email" in p for p in problems)


def test_identity_problems_catches_core_bare_true(monkeypatch, tmp_path: Path) -> None:
    r = tmp_path / "bare_repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True, env=_clean_env())
    subprocess.run(
        ["git", "config", "core.bare", "true"], cwd=r, check=True, env=_clean_env(),
    )
    monkeypatch.setattr(G, "REPO", r)
    problems = C._git_identity_problems()
    assert any("core.bare" in p for p in problems)
