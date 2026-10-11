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


def test_the_allow_marker_skips_a_fixture_line_only_under_test_paths():
    line = f"t = {GH}  # secretscan:allow fake fixture"
    assert S.scan_text(line, "ops/tests/t.py", {}) == []
    assert [f.kind for f in S.scan_text(line, "src/app.py", {})] == ["github_token"]
    assert [f.kind for f in S.scan_message(line, "commit abc message", {})] == ["github_token"]


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
        "C_SECRET": "/usr/local/bin/some-tool",
        "D_SECRET": "aaaaaaaaaaaa",
    }
    assert S.secret_env_values(env) == {}
    assert "E_KEY" in S.secret_env_values({"E_KEY": "ab/cd+ef/gh1234=="}), "base64 keys contain slashes"


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


def test_a_credential_is_not_exempt_because_the_file_has_a_binary_suffix():
    patch = f"--- /dev/null\n+++ b/logo.png\n@@ -0,0 +1 @@\n+{GH}\n"
    assert [(f.path, f.kind) for f in S.scan_patch(patch, {})] == [("logo.png", "github_token")]


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


def test_tree_scan_finds_a_tracked_file_whatever_its_suffix(repo):
    (repo / "c.txt").write_text(f"k={OPENAI}\n", encoding="utf-8")
    (repo / "img.png").write_bytes(GH.encode())
    _git(repo, "add", "c.txt", "img.png")
    _git(repo, "commit", "-q", "-m", "files")
    assert [(f.path, f.kind) for f in S.scan_tree({})] == [
        ("c.txt", "openai_style_key"),
        ("img.png", "github_token"),
    ]


def test_the_cli_fails_on_a_finding_passes_when_clean_and_fails_closed_when_git_breaks(
    repo, capsys, monkeypatch
):
    class A:
        staged = True
        pushed = None
        tree = False
        message = None
        base = None
        remote = None

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


# ---- red-team hardening (2026-10-11): each test is a way the first scanner could be blinded --------------------


def _diff(*lines: str) -> str:
    return "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -0,0 +1,{n} @@\n".replace(
        "{n}", str(len(lines))
    ) + "".join(f"+{line}\n" for line in lines)


def test_an_added_line_that_looks_like_a_file_header_does_not_blind_the_parser():
    patch = _diff("++ b/decoy.png", GH)  # the first added line renders as `+++ b/decoy.png`
    assert [(f.path, f.line, f.kind) for f in S.scan_patch(patch, {})] == [("f.txt", 2, "github_token")]


def test_exotic_line_separators_cannot_hide_a_token_or_forge_a_header():
    patch = _diff("pad\u2028+++ b/other.png\u2028" + GH, "x\x0c" + GH)
    found = S.scan_patch(patch, {})
    assert [(f.path, f.line) for f in found] == [("f.txt", 1), ("f.txt", 2)]


def test_a_removed_line_that_looks_like_a_header_does_not_change_the_file():
    patch = (
        "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,1 @@\n"
        "--- b/decoy.txt\n+" + GH + "\n"
    )
    assert [f.path for f in S.scan_patch(patch, {})] == ["f.txt"]


def test_a_credential_in_a_file_name_is_found():
    patch = f"diff --git a/{GH}.txt b/{GH}.txt\nnew file mode 100644\n"
    assert any(f.kind == "github_token" for f in S.scan_patch(patch, {}))


def test_the_env_value_is_found_in_its_base64_and_url_encoded_forms():
    import base64
    import urllib.parse

    secret = {"REGISTRY_PASSWORD": "p@ss word/with+odd=chars!9"}
    b64 = base64.b64encode(secret["REGISTRY_PASSWORD"].encode()).decode()
    quoted = urllib.parse.quote(secret["REGISTRY_PASSWORD"], safe="")
    for text in (f"auth: {b64}", f"url=https://u:{quoted}@host"):
        assert [f.kind for f in S.scan_text(text, "x", secret)] == ["env:REGISTRY_PASSWORD"], text


@pytest.mark.parametrize(
    "name", ["GITHUB_TOKEN", "MY_PAT", "REG_PASS", "OPENAI_APIKEY", "svc-auth", "DB_PASSWORD"]
)
def test_secret_looking_names_of_many_spellings_are_covered_even_for_short_values(name):
    assert name in S.secret_env_values({name: "Ab3dEf9x"})


def test_a_commit_message_a_tag_message_and_a_new_branch_are_scanned_and_the_marker_is_ignored_there(repo):
    (repo / "a.txt").write_text("ok\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", f"fix token {GH} secretscan:allow")
    head = _git(repo, "rev-parse", "HEAD")
    found = S.scan_pushed(head, {})
    assert [f.kind for f in found] == ["github_token"] and "message" in found[0].path
    _git(repo, "tag", "-a", "v1", "-m", f"release {GH}")
    tag = _git(repo, "rev-parse", "v1")
    assert any("tag" in f.path for f in S.scan_pushed(tag, {}))


def test_a_secret_added_in_a_merge_commit_is_found(repo):
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "s.txt").write_text("side\n", encoding="utf-8")
    _git(repo, "add", "s.txt")
    _git(repo, "commit", "-q", "-m", "side")
    _git(repo, "checkout", "-q", "main")
    (repo / "m.txt").write_text("main\n", encoding="utf-8")
    _git(repo, "add", "m.txt")
    _git(repo, "commit", "-q", "-m", "main")
    _git(repo, "merge", "--no-ff", "--no-commit", "side")
    (repo / "evil.txt").write_text(f"{GH}\n", encoding="utf-8")
    _git(repo, "add", "evil.txt")
    _git(repo, "commit", "-q", "-m", "merge")
    head = _git(repo, "rev-parse", "HEAD")
    assert [(f.path, f.kind) for f in S.scan_pushed(head, {})] == [("evil.txt", "github_token")]


def test_a_binary_attribute_cannot_suppress_the_diff(repo):
    (repo / ".gitattributes").write_text("*.dat binary\n", encoding="utf-8")
    (repo / "x.dat").write_text(f"{GH}\n", encoding="utf-8")
    _git(repo, "add", ".gitattributes", "x.dat")
    assert [(f.path, f.kind) for f in S.scan_staged({})] == [("x.dat", "github_token")]


def test_the_push_range_is_exactly_the_remotes_recorded_tip_to_the_pushed_commit(repo):
    (repo / "old.txt").write_text(f"{GH}\n", encoding="utf-8")
    _git(repo, "add", "old.txt")
    _git(repo, "commit", "-q", "-m", "old")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "new.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "new.txt")
    _git(repo, "commit", "-q", "-m", "new")
    head = _git(repo, "rev-parse", "HEAD")
    assert S.scan_pushed(head, {}, base=base) == [], "already-published history is not rescanned"
    assert [f.kind for f in S.scan_pushed(head, {}, base="0" * 40)] == ["github_token"], (
        "a new ref scans everything new"
    )


def test_the_commit_msg_cli_reads_a_message_file(repo, capsys, tmp_path):
    class A:
        staged = False
        pushed = None
        tree = False
        base = None
        remote = None

    msg = tmp_path / "MSG"
    msg.write_text(f"subject\n\nbody {GH}\n", encoding="utf-8")
    A.message = str(msg)
    assert C.cmd_secret_scan(A()) == G.EXIT_FAIL
    out = capsys.readouterr().out
    assert "commit message:3" in out and GH not in out
    msg.write_text("subject\n", encoding="utf-8")
    assert C.cmd_secret_scan(A()) == G.EXIT_OK
