"""Read a product's own GitHub Releases and classify how much each one actually says.

Honest degradation is the point: a release whose body is a real, hand-written migration note
must not be reported the same way as one that is GitHub's own auto-generated "What's Changed"
boilerplate, or one with no body at all. Those are three different facts about the release, and
collapsing them into "has a body: yes/no" would throw away exactly the distinction a reader of
the furnished content needs.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

_API_ROOT = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "foss-mcp-extraction"}

RichnessVerdict = Literal["detailed", "templated", "none"]

# GitHub's own "Generate release notes" button emits exactly this skeleton (a "What's Changed"
# heading, one bullet per merged PR crediting its author, an optional "New Contributors"
# section, and a "Full Changelog" compare link). A release body that is *only* this skeleton
# carries no product information a reader didn't already have from the PR list itself.
_BOILERPLATE_LINE_PATTERNS = [
    re.compile(r"^#+\s*what.?s changed", re.IGNORECASE),
    re.compile(r"^\*\s+.*\bby\s+@[\w-]+\s+in\s+(https?://\S+|#\d+)", re.IGNORECASE),
    re.compile(r"^\*\*full changelog\*\*", re.IGNORECASE),
    re.compile(r"^#+\s*new contributors", re.IGNORECASE),
    re.compile(r"^\*\s+@[\w-]+ made (their|his|her) first contribution", re.IGNORECASE),
]


def classify_richness(release_body: str | None) -> RichnessVerdict:
    """'none' for an empty body, 'templated' for pure GitHub auto-generated boilerplate,
    'detailed' for anything that says more than the boilerplate does.
    """
    if not release_body or not release_body.strip():
        return "none"
    remaining = []
    for line in release_body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in _BOILERPLATE_LINE_PATTERNS):
            continue
        remaining.append(stripped)
    return "templated" if not remaining else "detailed"


@dataclass(frozen=True)
class Release:
    tag_name: str
    body: str
    richness: RichnessVerdict


def _get_json(url: str, *, etag: str | None = None) -> tuple[int, Any, str | None]:
    """One conditional GET. Returns (status, decoded body or None, response ETag or None).

    Sends *etag* as ``If-None-Match`` when given, so a caller re-reading an unchanged listing
    gets a cheap 304 instead of the full payload - a conditional read does not count against
    GitHub's unauthenticated rate limit either way.
    """
    headers = dict(_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else None), response.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, None, etag
        raise


def fetch_releases(repository: str, *, per_page: int = 100, etag: str | None = None) -> list[Release]:
    """Every release for *repository* ("owner/name"), paginating until a page comes back short.

    Unauthenticated; ``etag`` short-circuits to nothing new (304) when the whole listing is
    unchanged since the caller's last read.
    """
    releases: list[Release] = []
    page = 1
    while True:
        url = f"{_API_ROOT}/repos/{repository}/releases?per_page={per_page}&page={page}"
        status, batch, _response_etag = _get_json(url, etag=etag if page == 1 else None)
        if status == 304 or not batch:
            break
        for entry in batch:
            body = entry.get("body") or ""
            releases.append(Release(entry["tag_name"], body, classify_richness(body)))
        if len(batch) < per_page:
            break
        page += 1
    return releases
