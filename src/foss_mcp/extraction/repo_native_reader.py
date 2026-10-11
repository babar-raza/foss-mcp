"""Read a repo's own governance documents (AGENTS.md, CONTRIBUTING.md, ...), honestly.

Absence is a first-class result, not a failure to paper over: a repo with no CONTRIBUTING.md
returns an explicit, typed "not present" - never a fabricated summary standing in for content
that was never there.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from base64 import b64decode
from dataclasses import dataclass

from foss_mcp.extraction.github_http import urlopen_with_backoff, with_auth

_API_ROOT = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "foss-mcp-extraction"}


@dataclass(frozen=True)
class DocumentPresent:
    path: str
    sha: str
    size: int
    content: str


@dataclass(frozen=True)
class DocumentNotPresent:
    path: str


DocumentResult = DocumentPresent | DocumentNotPresent


def read_repo_document(
    repository: str, path: str, *, ref: str | None = None, etag: str | None = None
) -> DocumentResult:
    """The Contents API's answer for *path* in *repository*, reported exactly as it is.

    A 404 is not an error to swallow into a guess: it is the answer. It comes back as
    ``DocumentNotPresent``, never smoothed into a fabricated summary of content that does not
    exist.
    """
    url = f"{_API_ROOT}/repos/{repository}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    headers = with_auth(_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urlopen_with_backoff(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return DocumentNotPresent(path=path)
        raise
    content = b64decode(payload["content"]).decode("utf-8") if payload.get("content") else ""
    return DocumentPresent(path=path, sha=payload["sha"], size=payload["size"], content=content)


def list_directory_entries(
    repository: str, path: str = "", *, ref: str | None = None
) -> list[tuple[str, str]]:
    """The ``(name, type)`` pairs of the Contents API listing of directory *path* in *repository*
    (``""`` is the repository root), in the order the API returns them. ``type`` is the API's own
    value (``"file"``, ``"dir"``, ``"symlink"`` or ``"submodule"``).

    A 404 is the answer for a directory that does not exist and comes back as ``[]``. A payload
    that is not a listing (a file path, an unexpected shape) also yields ``[]``: callers use this
    only to resolve the real spelling of a name, so an unreadable listing means "nothing to
    resolve", never an invented entry.
    """
    url = f"{_API_ROOT}/repos/{repository}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    request = urllib.request.Request(url, headers=with_auth(_HEADERS))
    try:
        with urlopen_with_backoff(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    if not isinstance(payload, list):
        return []
    entries: list[tuple[str, str]] = []
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            entries.append((item["name"], str(item.get("type", ""))))
    return entries


def list_root_entries(repository: str, *, ref: str | None = None) -> list[tuple[str, str]]:
    """The ``(name, type)`` pairs of *repository*'s root directory at *ref* (see
    ``list_directory_entries``); a 404 returns ``[]``."""
    return list_directory_entries(repository, "", ref=ref)
