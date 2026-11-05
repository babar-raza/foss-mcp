"""The additive `gh` CLI transport fast-path (G3/TC-687).

``gh_cli_json`` never reads, constructs, passes, or logs a credential itself - it only shells out
to the operator's own already-authenticated `gh` CLI session. Offline by design: every
``subprocess.run`` call is stubbed, so no test here ever shells out to a real `gh` binary or
touches the network.
"""

from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.request

from foss_mcp.extraction import github_http, repo_native_reader
from foss_mcp.extraction.github_http import gh_cli_json


class _FakeCompletedProcess:
    """A minimal stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, returncode: int, stdout: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = b""


class _Response:
    """A minimal stand-in for the response ``urlopen`` returns."""

    def __init__(self, body: bytes = b"{}") -> None:
        self._body = body
        self.status = 200
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


# --- gh_cli_json itself: the 4 failure/success proofs the card requires --------------------


def test_gh_cli_json_returns_real_parsed_json_on_a_clean_exit_0(monkeypatch) -> None:
    seen_argv: list[list[str]] = []

    def fake_run(argv, *, capture_output, timeout, check):
        seen_argv.append(argv)
        assert capture_output is True
        assert check is False
        return _FakeCompletedProcess(0, json.dumps({"sha": "abc123", "size": 3}).encode())

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = gh_cli_json("repos/owner/name/contents/README.md")
    assert result == {"sha": "abc123", "size": 3}
    assert seen_argv == [["gh", "api", "repos/owner/name/contents/README.md"]]


def test_gh_cli_json_returns_none_never_raises_on_a_non_zero_exit(monkeypatch) -> None:
    def fake_run(argv, *, capture_output, timeout, check):
        return _FakeCompletedProcess(1, b'{"message":"Not Found"}')

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gh_cli_json("repos/owner/name/contents/MISSING.md") is None


def test_gh_cli_json_returns_none_never_raises_when_gh_is_not_installed(monkeypatch) -> None:
    """Confirms the specific exception `gh` missing raises (``FileNotFoundError``) is the one
    caught - not merely a generic ``Exception`` catch-all."""

    def fake_run(argv, *, capture_output, timeout, check):
        raise FileNotFoundError("gh: command not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gh_cli_json("repos/owner/name/contents/README.md") is None


def test_gh_cli_json_returns_none_never_raises_on_a_timeout(monkeypatch) -> None:
    def fake_run(argv, *, capture_output, timeout, check):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gh_cli_json("repos/owner/name/contents/README.md", timeout=5) is None


def test_gh_cli_json_returns_none_on_malformed_json(monkeypatch) -> None:
    def fake_run(argv, *, capture_output, timeout, check):
        return _FakeCompletedProcess(0, b"this is not json at all")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gh_cli_json("repos/owner/name/contents/README.md") is None


def test_gh_cli_available_is_memoized_and_reports_a_clean_exit(monkeypatch) -> None:
    github_http.gh_cli_available.cache_clear()
    calls: list[list[str]] = []

    def fake_run(argv, *, capture_output, timeout, check):
        calls.append(argv)
        return _FakeCompletedProcess(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert github_http.gh_cli_available() is True
    assert github_http.gh_cli_available() is True
    assert calls == [["gh", "auth", "status"]]  # memoized: only shelled out once
    github_http.gh_cli_available.cache_clear()


# --- One real call site (repo_native_reader.read_repo_document): the skip/fall-through proofs ---


def test_read_repo_document_skips_the_http_call_entirely_when_gh_cli_json_succeeds(monkeypatch) -> None:
    """With the fast path explicitly turned on (FOSS_MCP_GH_CLI_FASTPATH=1) and gh_cli_json
    stubbed to succeed, the HTTP call is skipped entirely."""
    monkeypatch.setenv(github_http.GH_CLI_FASTPATH_ENV, "1")
    content = base64.b64encode(b"hello from gh").decode()
    cli_payload = {"sha": "deadbeef", "size": 13, "content": content}

    def fake_gh_cli_json(path, *, timeout=15):
        assert path == "/repos/owner/name/contents/README.md"
        return cli_payload

    def fail_if_called(*args, **kwargs):
        raise AssertionError("urlopen_with_backoff must not be called when gh_cli_json succeeds")

    monkeypatch.setattr(repo_native_reader, "gh_cli_json", fake_gh_cli_json)
    monkeypatch.setattr(repo_native_reader, "urlopen_with_backoff", fail_if_called)

    result = repo_native_reader.read_repo_document("owner/name", "README.md")

    assert isinstance(result, repo_native_reader.DocumentPresent)
    assert result.sha == "deadbeef"
    assert result.size == 13
    assert result.content == "hello from gh"


def test_read_repo_document_falls_through_to_the_existing_http_path_when_gh_cli_json_returns_none(
    monkeypatch,
) -> None:
    """With the fast path turned on but gh_cli_json stubbed to return None (gh unavailable, not
    authenticated, a real 404, ...), the existing urllib path runs exactly as it did before this
    card - same request shape, same response handling."""
    monkeypatch.setenv(github_http.GH_CLI_FASTPATH_ENV, "1")
    payload = json.dumps({"sha": "abc", "size": 2, "content": base64.b64encode(b"hi").decode()}).encode()
    calls: list[urllib.request.Request] = []
    gh_calls: list[str] = []

    def fake_gh_cli_json(path, *, timeout=15):
        gh_calls.append(path)
        return None

    def fake_urlopen_with_backoff(request, timeout=30):
        calls.append(request)
        return _Response(payload)

    monkeypatch.setattr(repo_native_reader, "gh_cli_json", fake_gh_cli_json)
    monkeypatch.setattr(repo_native_reader, "urlopen_with_backoff", fake_urlopen_with_backoff)

    result = repo_native_reader.read_repo_document("owner/name", "README.md")

    assert gh_calls == ["/repos/owner/name/contents/README.md"]  # the fast path really was tried
    assert len(calls) == 1
    request = calls[0]
    assert request.full_url == "https://api.github.com/repos/owner/name/contents/README.md"
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert isinstance(result, repo_native_reader.DocumentPresent)
    assert result.content == "hi"


def test_read_repo_document_with_an_etag_never_tries_the_gh_cli_fast_path(monkeypatch) -> None:
    """A conditional (``etag``) read asks for a cheap 304 when unchanged - `gh api` has no
    conditional-header support here, so the fast path must not be tried at all for this call,
    even with the fast path turned on, and the existing If-None-Match behavior must be exactly as
    before."""
    monkeypatch.setenv(github_http.GH_CLI_FASTPATH_ENV, "1")

    def fail_if_called(path, *, timeout=15):
        raise AssertionError("gh_cli_json must not be tried for a conditional (etag) read")

    def fake_urlopen(request, timeout=30):
        raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr(repo_native_reader, "gh_cli_json", fail_if_called)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    try:
        repo_native_reader.read_repo_document("owner/name", "README.md", etag='"v1"')
    except urllib.error.HTTPError as exc:
        assert exc.code == 304
    else:  # pragma: no cover - defensive; the real code path always raises on a 304 here
        raise AssertionError("expected the existing 304 behavior to propagate unchanged")


def test_read_repo_document_never_tries_gh_cli_json_at_all_when_the_fast_path_is_off_by_default(
    monkeypatch,
) -> None:
    """Regression guard for the exact failure measured while authoring this card: on a
    workstation with `gh` already installed and authenticated, an unconditional fast-path
    attempt made 2 existing, otherwise-unrelated tests fail for real (the live `gh api` call won
    the race against their own pinned stub). With FOSS_MCP_GH_CLI_FASTPATH unset (the default),
    gh_cli_json must never even be called, so no existing test's behavior can depend on whether
    a real `gh` session happens to exist."""
    monkeypatch.delenv(github_http.GH_CLI_FASTPATH_ENV, raising=False)
    payload = json.dumps({"sha": "abc", "size": 2, "content": base64.b64encode(b"hi").decode()}).encode()
    calls: list[urllib.request.Request] = []

    def fail_if_called(path, *, timeout=15):
        raise AssertionError("gh_cli_json must not be tried when the fast path is off")

    def fake_urlopen_with_backoff(request, timeout=30):
        calls.append(request)
        return _Response(payload)

    monkeypatch.setattr(repo_native_reader, "gh_cli_json", fail_if_called)
    monkeypatch.setattr(repo_native_reader, "urlopen_with_backoff", fake_urlopen_with_backoff)

    result = repo_native_reader.read_repo_document("owner/name", "README.md")

    assert len(calls) == 1
    assert isinstance(result, repo_native_reader.DocumentPresent)
    assert result.content == "hi"
