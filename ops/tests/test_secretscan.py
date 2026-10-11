"""Canary tests for the secret scanner (DECISION_LOG 2026-10-11).

The operator hands this repository real credentials and asked that no agent ever expose one. These tests prove the scanner
catches each credential shape and the exact value of any secret-looking environment variable, never prints a value, scans the
whole of a push (a secret added and later removed is still caught), and fails closed when git itself fails. Test tokens are
assembled at runtime so this file contains no credential-shaped literal.
"""

from __future__ import annotations

import subprocess

import gatecli as C
import gatectl as G
import pytest
import secretscan as S

GH = "gh" + "p_" + ("A1b2C3" * 6)  # 36 chars after the prefix
GH_PAT = "github" + "_pat_" + ("Ab1" * 10)
AWS = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
SLACK = "xo" + "xb-" + "1234567890-abcdef"
OPENAI = "s" + "k-" + ("a1B2" * 9)
BEARER = "Bear" + "er " + ("Zz9" * 12)
PRIVATE = "-----BEGIN RSA " + "PRIVATE KEY-----"
DOCKER_AUTH = '"au' + 'th": "' + ("QUJD" * 8) + '"'
ENV_SECRET = "Zq8-unusual-registry-pass-93f1"  # any format, not a known token shape


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("github_token", f"export T={GH}"),
        ("github_pat", f"token: {GH_PAT}"),
        ("aws_access_key", f"id = {AWS}"),
        ("slack_token", f"hook {SLACK}"),
        ("openai_style_key", f"key={OPENAI}"),
        ("bearer_literal", f"Authorization: {BEARER}"),
        ("private_key", PRIVATE),
        ("docker_auth", DOCKER_AUTH),
    ],
)
def test_each_credential_shape_is_detected(name, text):
    assert [f.kind for f in S.scan_text(text, "x.txt", {})] == [name]


@pytest.mark.parametrize(
    "text",
    [
        "pip install foss-mcp",
        "Authorization: Bearer $TOKEN",
        "GITHUB_TOKEN=${GITHUB_TOKEN}",
        "ghp_ is the prefix GitHub uses for personal access tokens",
        "sk-learn is a library",
        "AKIA is the AWS prefix",
        "the token key is stored in a Secret named foss-mcp-github-token",
    ],
)
def test_ordinary_text_and_placeholders_are_not_flagged(text):
    assert S.scan_text(text, "docs.md", {}) == []


def test_the_allow_marker_skips_a_fixture_line():
    assert S.scan_text(f"t = {GH}  # secretscan:allow fake fixture", "t.py", {}) == []


def test_the_exact_value_of_a_secret_looking_env_var_is_detected_in_any_format_and_never_printed():
    env = {"MY_REGISTRY_PASSWORD": ENV_SECRET, "PATH": "C:\\bin;C:\\tools", "SHORT_TOKEN": "abc"}
    secrets = S.secret_env_values(env)
    assert list(secrets) == ["MY_REGISTRY_PASSWORD"], (
        "short values, path-like values and non-secret names are ignored"
    )
    found = S.scan_text(f"login --password {ENV_SECRET}", "ci.sh", secrets)
    assert [f.kind for f in found] == ["env:MY_REGISTRY_PASSWORD"]
    text = S.report(found)
    assert ENV_SECRET not in text and "MY_REGISTRY_PASSWORD" in text


def test_a_value_with_whitespace_or_a_path_separator_is_not_treated_as_a_secret():
    env = {
        "A_TOKEN": "has some spaces in it ok",
        "B_KEY": "C:\\Users\\someone\\file.txt",
        "C_SECRET": "a/b/c/d/e/f/g/h/i/j",
    }
    assert S.secret_env_values(env) == {}


def test_added_lines_from_patch_numbers_new_lines_and_ignores_removed_ones():
    patch = (
        "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n"
        "@@ -1,2 +10,3 @@\n context\n-removed line\n+added one\n+added two\n"
        "diff --git a/g.py b/g.py\n--- /dev/null\n+++ b/g.py\n@@ -0,0 +1 @@\n+first\n"
    )
    assert S.added_lines_from_patch(patch) == [
        ("f.txt", 10, "added one"),
        ("f.txt", 11, "added two"),
        ("g.py", 1, "first"),
    ]


def test_a_secret_on_a_removed_line_is_not_reported_as_added():
    patch = f"--- a/f\n+++ b/f\n@@ -1 +1 @@\n-token {GH}\n+token REDACTED\n"
    assert S.scan_patch(patch, {}) == []


def test_binary_suffixes_are_skipped():
    patch = f"--- /dev/null\n+++ b/logo.png\n@@ -0,0 +1 @@\n+{GH}\n"
    assert S.scan_patch(patch, {}) == []


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "t")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.invalid")
    r = tmp_path / "r"
    r.mkdir()
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=r, check=True, capture_output=True)
    monkeypatch.setattr(G, "REPO", r)
    monkeypatch.setattr(S, "secret_env_values", lambda env=None: {})
    return r


def _git(r, *args):
    return subprocess.run(["git", *args], cwd=r, check=True, capture_output=True, text=True).stdout.strip()


def test_staged_scan_blocks_a_token_and_passes_clean_content(repo):
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    assert S.scan_staged({}) == []
    (repo / "b.env").write_text(f"T={GH}\n", encoding="utf-8")
    _git(repo, "add", "b.env")
    found = S.scan_staged({})
    assert [(f.path, f.line, f.kind) for f in found] == [("b.env", 1, "github_token")]


def test_a_staged_secret_is_caught_by_value_even_in_an_unknown_format(repo):
    (repo / "notes.md").write_text(f"registry password is {ENV_SECRET}\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    found = S.scan_staged({"REGISTRY_PASSWORD": ENV_SECRET})
    assert [f.kind for f in found] == ["env:REGISTRY_PASSWORD"]


def test_a_secret_added_and_removed_inside_one_push_is_still_caught(repo):
    (repo / "x.txt").write_text(f"{GH}\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    _git(repo, "commit", "-q", "-m", "oops")
    (repo / "x.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "remove it")
    head = _git(repo, "rev-parse", "HEAD")
    assert [f.kind for f in S.scan_pushed(head, {})] == ["github_token"]


def test_commits_already_on_a_remote_are_not_rescanned(repo, tmp_path):
    (repo / "x.txt").write_text(f"{GH}\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    _git(repo, "commit", "-q", "-m", "old leak, already published")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    head = _git(repo, "rev-parse", "HEAD")
    assert S.scan_pushed(head, {}) == []


def test_tree_scan_finds_a_tracked_file_and_skips_binaries(repo):
    (repo / "c.txt").write_text(f"k={OPENAI}\n", encoding="utf-8")
    (repo / "img.png").write_bytes(GH.encode())
    _git(repo, "add", "c.txt", "img.png")
    _git(repo, "commit", "-q", "-m", "files")
    assert [(f.path, f.kind) for f in S.scan_tree({})] == [("c.txt", "openai_style_key")]


def test_the_cli_fails_on_a_finding_passes_when_clean_and_fails_closed_when_git_breaks(
    repo, capsys, monkeypatch
):
    class A:
        staged = True
        pushed = None
        tree = False

    (repo / "ok.txt").write_text("fine\n", encoding="utf-8")
    _git(repo, "add", "ok.txt")
    assert C.cmd_secret_scan(A()) == G.EXIT_OK
    assert "OK" in capsys.readouterr().out

    (repo / "bad.txt").write_text(f"{GH}\n", encoding="utf-8")
    _git(repo, "add", "bad.txt")
    assert C.cmd_secret_scan(A()) == G.EXIT_FAIL
    out = capsys.readouterr().out
    assert "bad.txt:1" in out and GH not in out

    monkeypatch.setattr(G, "REPO", repo / "not-a-repo")
    assert C.cmd_secret_scan(A()) == G.EXIT_FAIL
    assert "COULD NOT RUN" in capsys.readouterr().out
