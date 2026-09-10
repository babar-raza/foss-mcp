"""Scope-guard tests against REAL git repositories.

`git diff --name-only base..head` is the obvious implementation and it is not
good enough. Each test here corresponds to a specific way that obvious version
lets an out-of-scope change through:

  * an endpoint diff hides touch-and-revert,
  * rename detection prints only the destination, hiding the source,
  * prefix matching lets `src/reg` authorise `src/regression_hack.py`,
  * core.ignorecase=true (live in this repo) makes case comparisons unreliable,
  * a symlink or gitlink puts the real content outside the path check entirely.

These use throwaway repos, never the project's own history.
"""

from __future__ import annotations

import subprocess

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
    (r / "src").mkdir()
    (r / "ops").mkdir()
    (r / "src" / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (r / "ops" / "gatectl.py").write_text("# governance\n", encoding="utf-8")
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", "base")
    monkeypatch.setattr(G, "REPO", r)
    return r


def commit(r, msg):
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", msg)
    return sh(r, "rev-parse", "HEAD")


CARD = {"write_paths": ["src/**"]}


def test_a_clean_in_scope_change_passes(repo):
    base = sh(repo, "rev-parse", "HEAD")
    (repo / "src" / "new.py").write_text("y = 2\n", encoding="utf-8")
    head = commit(repo, "in scope")
    ok, changed, violations = G.check_scope(CARD, base, head)
    assert ok, violations
    assert "src/new.py" in changed


def test_touch_and_revert_is_caught(repo):
    """The whole reason scope walks every commit instead of diffing endpoints.

    A worker modifies a governance file in one commit and reverts it in a later
    one. `git diff base..head` is clean - but the change is in the history, and
    anything that checks out an intermediate revision sees it.
    """
    base = sh(repo, "rev-parse", "HEAD")
    gov = repo / "ops" / "gatectl.py"
    original = gov.read_text(encoding="utf-8")
    gov.write_text("# governance TAMPERED\n", encoding="utf-8")
    commit(repo, "sneak")
    (repo / "src" / "new.py").write_text("y = 2\n", encoding="utf-8")
    commit(repo, "real work")
    gov.write_text(original, encoding="utf-8")
    head = commit(repo, "put it back")

    endpoint = sh(repo, "diff", "--name-only", f"{base}..{head}")
    assert "ops/gatectl.py" not in endpoint, "precondition: the endpoint diff looks clean"

    ok, changed, violations = G.check_scope(CARD, base, head)
    assert not ok, "touch-and-revert must be caught"
    assert any("ops/gatectl.py" in v for v in violations)


def test_a_rename_cannot_hide_the_source_path(repo):
    """With rename detection on, --name-only prints only the destination, so a
    file dragged out of a protected directory leaves no trace at the source."""
    base = sh(repo, "rev-parse", "HEAD")
    sh(repo, "mv", "ops/gatectl.py", "src/gatectl.py")
    head = commit(repo, "relocate")
    ok, changed, violations = G.check_scope(CARD, base, head)
    assert not ok, "the vacated ops/ path must still be reported"
    assert any("ops/gatectl.py" in v for v in violations)


def test_deleting_a_governance_file_is_a_violation(repo):
    base = sh(repo, "rev-parse", "HEAD")
    (repo / "ops" / "gatectl.py").unlink()
    head = commit(repo, "delete governance")
    ok, _, violations = G.check_scope(CARD, base, head)
    assert not ok
    assert any("ops/gatectl.py" in v for v in violations)


def test_a_globally_denied_path_is_rejected_even_if_the_card_declares_it(repo):
    """Defence in depth: even a mis-authored card cannot licence ops/."""
    base = sh(repo, "rev-parse", "HEAD")
    (repo / "ops" / "gatectl.py").write_text("# changed\n", encoding="utf-8")
    head = commit(repo, "touch governance")
    ok, _, violations = G.check_scope({"write_paths": ["ops/**", "src/**"]}, base, head)
    assert not ok
    assert any("globally denied" in v for v in violations)


def test_history_rewriting_is_caught(repo):
    """If the dispatch base is no longer an ancestor of head, the audit chain
    is broken: the commit the worker was given has been amended or reset away.

    Build a genuinely orphaned base: commit B, then reset back to A and commit C.
    A worker dispatched at B and reporting C must be rejected.
    """
    a = sh(repo, "rev-parse", "HEAD")
    (repo / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    b = commit(repo, "commit B - the dispatch base")

    sh(repo, "reset", "-q", "--hard", a)
    (repo / "src" / "c.py").write_text("c = 1\n", encoding="utf-8")
    c = commit(repo, "commit C - a rewritten line of history")

    assert b != c
    ok, _, violations = G.check_scope(CARD, b, c)
    assert not ok, "head that does not descend from the dispatch base must be rejected"
    assert any("does not descend" in v for v in violations), violations


def test_a_submodule_gitlink_is_flagged(repo, tmp_path):
    """A gitlink puts content outside the path check entirely, and
    submodule.active=. means it is active by default in this repo."""
    other = tmp_path / "other"
    other.mkdir()
    sh(other, "init", "-q", "-b", "main")
    sh(other, "config", "user.email", "t@example.com")
    sh(other, "config", "user.name", "t")
    (other / "f.txt").write_text("hi\n", encoding="utf-8")
    sh(other, "add", "-A")
    sh(other, "commit", "-q", "-m", "x")

    base = sh(repo, "rev-parse", "HEAD")
    p = subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(other).replace("\\", "/"),
            "src/vendor",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        pytest.skip(f"submodule add unavailable here: {p.stderr[:120]}")
    head = commit(repo, "add submodule")
    flags = G.risky_modes(base, head)
    assert any("submodule" in f for f in flags), flags


# ---------------------------------------------------------------- matching
@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("src/a/b.py", "src/**", True),
        ("src", "src/**", True),
        ("srcx/a.py", "src/**", False),
        ("src/regression_hack.py", "src/reg", False),
        ("src/reg", "src/reg", True),
        ("SRC/A.PY", "src/**", True),
        ("src/a/b.py", "src/*", False),
        ("src/a.py", "src/*", True),
    ],
)
def test_path_matching_is_segment_wise_not_prefix_wise(path, pattern, expected):
    assert G.path_matches(path, pattern) is expected


def test_illegal_windows_paths_are_reported():
    assert G.illegal_path_reasons("src/con.py")
    assert G.illegal_path_reasons("src/a:b.py")
    assert G.illegal_path_reasons("src/trailing.")
    assert G.illegal_path_reasons("src/ok.py") == []
