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
    headers = dict(_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return DocumentNotPresent(path=path)
        raise
    content = b64decode(payload["content"]).decode("utf-8") if payload.get("content") else ""
    return DocumentPresent(path=path, sha=payload["sha"], size=payload["size"], content=content)
