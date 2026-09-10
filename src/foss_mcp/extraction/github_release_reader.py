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

# A body can pass the boilerplate-line check above and still say nothing about the release: a
# hand-maintained package blurb (headings, bullets, an install command in a fenced code block)
# that repeats near-verbatim every version, with only the version string changing, is
# beautifully formatted and carries no per-release information either. Markup structure - a
# heading, a bullet, a fenced block - is not evidence of content; measured 2026-09-11 against
# aspose-font-foss/Aspose.Font-FOSS-for-Python, whose release bodies are 458 characters long,
# byte-for-byte identical apart from the version number, across at least six consecutive
# releases (26.9.2, 26.9.1, 26.8.4, 26.8.3, 26.8.2, 26.8.1).
#
# What a real note has that a template doesn't, checked on a single body (no other release's
# text is available to compare against): a verb describing what actually happened this release,
# and something SPECIFIC it happened to - a code-formatted identifier or flag (an inline
# `` `backtick span` ``, not a fenced install command, which every release template also has),
# or a plain number that is not itself a version string (a count, a measurement, an issue
# number). A version-info blurb ("this release publishes the source package", "will be added in
# a later release") uses process verbs about the ACT of releasing, never paired with a named,
# specific fact about what changed - verified against real bodies: aspose-pdf-foss's Go and
# aspose-cells-foss's Python releases each name several specific, changed things and classify
# 'detailed'; the font-python template names none and classifies 'templated'.
_FENCED_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_VERSION_LIKE_NUMBER = re.compile(r"\b\d+(?:\.\d+){1,3}\b")
_CHANGE_VERB = re.compile(
    r"\b(fix(?:ed|es|ing)?|add(?:ed|s|ing)?|remov(?:ed|es|al)?|improv(?:ed|es|ement)?|"
    r"resolv(?:ed|es)?|deprecat(?:ed|es|ion)?|introduc(?:ed|es)?|support(?:ed|s|ing)?|"
    r"chang(?:ed|es)?|updat(?:ed|es)?|refactor(?:ed|s)?|optimiz(?:ed|es)?|"
    r"preserv(?:ed|es)?|avoid(?:ed|s)?|synchroniz(?:ed|es)?|correct(?:ed|s)?|"
    r"prevent(?:ed|s)?|broke|break(?:ing|s)?|regress(?:ed|ion)?|reproduc(?:ed|es)?)\b",
    re.IGNORECASE,
)
_INLINE_CODE_SPAN = re.compile(r"`[^`\n]+`")
_SPECIFIC_NUMBER = re.compile(r"\b\d[\d,]+\b")


def _names_something_specific(text_without_fences: str) -> bool:
    """A code-formatted identifier or flag, or a number that is not a version string."""
    without_versions = _VERSION_LIKE_NUMBER.sub(" ", text_without_fences)
    if _INLINE_CODE_SPAN.search(text_without_fences):
        return True
    return bool(_SPECIFIC_NUMBER.search(without_versions))


def classify_richness(release_body: str | None) -> RichnessVerdict:
    """'none' for an empty body; 'templated' for GitHub's own auto-generated boilerplate, or a
    hand-maintained blurb that says nothing specific about this particular release;
    'detailed' for a body that names an actual change and something specific it happened to.
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
    if not remaining:
        return "templated"

    text_without_fences = _FENCED_BLOCK.sub(" ", release_body)
    has_change_verb = bool(_CHANGE_VERB.search(text_without_fences))
    names_something_specific = _names_something_specific(text_without_fences)
    return "detailed" if has_change_verb and names_something_specific else "templated"


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
