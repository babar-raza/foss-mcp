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
from urllib.parse import quote

from foss_mcp.extraction.github_http import urlopen_with_backoff, with_auth

_API_ROOT = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "foss-mcp-extraction"}

RichnessVerdict = Literal["detailed", "templated", "none"]

_FULL_SHA = re.compile(r"[0-9a-f]{40}")

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
class DriftRecord:
    """G3/TC-560: how the indexed commit relates, by git ancestry, to the commit of a release tag.

    ``ahead_by`` is the number of commits the indexed commit has that the tag lacks (the index is that
    far past the published package); ``behind_by`` is the number the tag has that the indexed commit
    lacks (the published package is that far past the index). Both come from one GitHub compare call
    (``compare/<tag>...<indexed commit>``). ``None`` for both means the comparison could not be made
    (``error`` then names the failure's class); that is "unknown", never "in sync".
    """

    indexed_commit: str
    tag: str
    tag_commit: str | None = None
    ahead_by: int | None = None
    behind_by: int | None = None
    status: str | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The sidecar form: only the keys that carry a value, so an unknown record stays small."""
        data: dict[str, Any] = {"indexed_commit": self.indexed_commit, "tag": self.tag}
        for key in ("tag_commit", "ahead_by", "behind_by", "status", "error"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    @classmethod
    def from_json(cls, data: object) -> DriftRecord | None:
        """The record a sidecar stored, or None when *data* is absent or not the recorded shape. Never
        raises: a malformed record is the same as no record (drift is then ``unknown``)."""
        if not isinstance(data, dict):
            return None
        indexed_commit, tag = data.get("indexed_commit"), data.get("tag")
        if not isinstance(indexed_commit, str) or not indexed_commit or not isinstance(tag, str) or not tag:
            return None

        def _count(key: str) -> int | None:
            value = data.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        def _text(key: str) -> str | None:
            value = data.get(key)
            return value if isinstance(value, str) and value else None

        return cls(
            indexed_commit=indexed_commit,
            tag=tag,
            tag_commit=_text("tag_commit"),
            ahead_by=_count("ahead_by"),
            behind_by=_count("behind_by"),
            status=_text("status"),
            error=_text("error"),
        )


@dataclass(frozen=True)
class Release:
    tag_name: str
    body: str
    richness: RichnessVerdict
    # Additive and last so positional ``Release(tag, body, richness)`` keeps working. Filled from
    # the API's ``published_at`` / ``html_url`` when GitHub returns them, so a client can tell how
    # recent "recent" is and where the release lives.
    published_at: str | None = None
    url: str | None = None
    # G3/TC-511: from the same list-releases answer, with no extra call. ``target_commitish`` is a
    # branch name or a 40-hex sha (GitHub's own field), ``created_at`` the release's creation time.
    target_commitish: str | None = None
    created_at: str | None = None
    # G3/TC-519: "release" for a GitHub Release, "tag" for an entry synthesised from a bare git tag
    # of a repository that publishes no Releases (no notes: richness "none").
    source: str = "release"
    # G3/TC-560: ancestry of the indexed commit against THIS entry's tag, written to the sidecar by
    # ``infra/fetch_recent_releases.py`` on the newest entry only; None for every other entry and for
    # every sidecar written before the card.
    drift: DriftRecord | None = None

    @property
    def target_commit(self) -> str | None:
        """``target_commitish`` when it is exactly 40 lowercase hex characters, else None. A branch
        name such as ``main`` is NOT a commit; resolving a tag to its commit is a later card's job."""
        value = self.target_commitish
        return value if value is not None and _FULL_SHA.fullmatch(value) else None


def _get_json(url: str, *, etag: str | None = None) -> tuple[int, Any, str | None]:
    """One conditional GET. Returns (status, decoded body or None, response ETag or None).

    Sends *etag* as ``If-None-Match`` when given, so a caller re-reading an unchanged listing
    gets a cheap 304 instead of the full payload - a conditional read does not count against
    GitHub's unauthenticated rate limit either way.
    """
    headers = with_auth(_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urlopen_with_backoff(request, timeout=30) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else None), response.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, None, etag
        raise


def fetch_releases(
    repository: str,
    *,
    per_page: int = 100,
    etag: str | None = None,
    meta: dict[str, Any] | None = None,
) -> list[Release]:
    """Every release for *repository* ("owner/name"), paginating until a page comes back short.

    Unauthenticated; ``etag`` short-circuits to nothing new (304) when the whole listing is
    unchanged since the caller's last read. When *meta* (a dict) is given, ``meta["etag"]`` is set
    to page 1's response ETag, and ``meta["not_modified"]`` to True on a 304.
    """
    releases: list[Release] = []
    page = 1
    while True:
        url = f"{_API_ROOT}/repos/{repository}/releases?per_page={per_page}&page={page}"
        status, batch, response_etag = _get_json(url, etag=etag if page == 1 else None)
        if meta is not None and page == 1:
            meta["etag"] = response_etag
            if status == 304:
                meta["not_modified"] = True
        if status == 304 or not batch:
            break
        for entry in batch:
            body = entry.get("body") or ""
            releases.append(
                Release(
                    entry["tag_name"],
                    body,
                    classify_richness(body),
                    published_at=entry.get("published_at") or None,
                    url=entry.get("html_url") or None,
                    target_commitish=entry.get("target_commitish") or None,
                    created_at=entry.get("created_at") or None,
                )
            )
        if len(batch) < per_page:
            break
        page += 1
    return releases


def fetch_tags(repository: str, *, per_page: int = 100) -> list[Release]:
    """The newest *per_page* git tags of *repository* ("owner/name") as tag-only ``Release`` entries.

    G3/TC-519: for a repository that publishes no GitHub Releases. One request, no pagination: the
    tags API lists the newest tag first, and each entry already carries the tagged commit's sha, so
    no further call is needed. A tag has no notes (richness ``none``, ``source`` ``tag``); an entry
    without a name is skipped.
    """
    url = f"{_API_ROOT}/repos/{repository}/tags?per_page={per_page}"
    _status, batch, _etag = _get_json(url)
    tags: list[Release] = []
    for entry in batch or []:
        name = entry.get("name")
        if not name:
            continue
        commit = entry.get("commit") or {}
        tags.append(
            Release(
                tag_name=name,
                body="",
                richness="none",
                published_at=None,
                url=f"https://github.com/{repository}/releases/tag/{name}",
                target_commitish=commit.get("sha") or None,
                source="tag",
            )
        )
    return tags


_TAG_VERSION_RUN = re.compile(r"\d+(?:\.\d+)+")


def _tag_version(tag_name: str) -> tuple[int, ...] | None:
    """The first dotted run of two or more integers in *tag_name* (``v26.9.0`` -> (26, 9, 0)), else
    None. Trailing zeros are dropped so ``26.9`` and ``26.9.0`` are the same version."""
    match = _TAG_VERSION_RUN.search(tag_name)
    if match is None:
        return None
    parts = [int(part) for part in match.group(0).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def merge_newer_tags(releases: list[Release], tags: list[Release]) -> list[Release]:
    """G3/TC-560: *releases* with every tag that is newer than all of them put in front, newest first.

    A tag is merged only when its name carries a version, that version is greater than the greatest
    version any release carries, and no release already has that version (a ``26.9.0`` tag beside a
    ``v26.9.0`` release is the same version). Tags that are older, unversioned or duplicates are left
    out, so the result is still "the newest first" and every added entry has ``source == "tag"``. With
    no release that carries a version nothing is added (the caller's tag-only path handles that).
    """
    release_versions = [version for release in releases if (version := _tag_version(release.tag_name))]
    if not release_versions:
        return list(releases)
    newest = max(release_versions)
    known = set(release_versions)
    newer: dict[tuple[int, ...], Release] = {}
    for tag in tags:
        version = _tag_version(tag.tag_name)
        if version is None or version <= newest or version in known or version in newer:
            continue
        newer[version] = tag
    return [newer[version] for version in sorted(newer, reverse=True)] + list(releases)


def fetch_drift(repository: str, tag: Release, indexed_commit: str) -> DriftRecord:
    """G3/TC-560: the ancestry of *indexed_commit* against *tag*'s commit, from ONE compare call.

    ``compare/<base>...<indexed_commit>`` with the tag's commit as base when the entry carries one
    (a tag-sourced entry does), else the tag name. ``ahead_by`` / ``behind_by`` / ``status`` are
    GitHub's own and ``base_commit.sha`` is the commit the tag points at. Any failure (no network, a
    commit GitHub does not know, a rate limit) is returned as a record with no counts and the failure's
    class name in ``error`` - never raised, so one pilot's drift cannot fail the whole releases fetch.
    """
    # A tag entry's commit is the tag's own; a Release's ``target_commitish`` is only where it was cut from.
    base = (tag.target_commit if tag.source == "tag" else None) or tag.tag_name
    url = f"{_API_ROOT}/repos/{repository}/compare/{quote(base, safe='/')}...{quote(indexed_commit, safe='')}?per_page=1"
    try:
        _status, body, _etag = _get_json(url)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure is "drift unknown".
        return DriftRecord(
            indexed_commit=indexed_commit,
            tag=tag.tag_name,
            tag_commit=tag.target_commit,
            error=type(exc).__name__,
        )

    def _count(key: str) -> int | None:
        value = body.get(key) if isinstance(body, dict) else None
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    base_commit = body.get("base_commit") if isinstance(body, dict) else None
    sha = base_commit.get("sha") if isinstance(base_commit, dict) else None
    status = body.get("status") if isinstance(body, dict) else None
    ahead, behind = _count("ahead_by"), _count("behind_by")
    error = None if ahead is not None and behind is not None else "UnexpectedCompareAnswer"
    return DriftRecord(
        indexed_commit=indexed_commit,
        tag=tag.tag_name,
        tag_commit=sha if isinstance(sha, str) and _FULL_SHA.fullmatch(sha) else None,
        ahead_by=ahead,
        behind_by=behind,
        status=status if isinstance(status, str) and status else None,
        error=error,
    )


def order_tags_newest_first(tags: list[Release]) -> list[Release]:
    """*tags* with the highest version first and the unversioned ones last; ties keep their order.

    G3/TC-560: the tags API does not list tags in version order, and a tag-only repository's "latest"
    is the one the drift comparison is made against, so the order is made explicit here."""

    def _key(tag: Release) -> tuple[bool, tuple[int, ...]]:
        version = _tag_version(tag.tag_name)
        return (version is not None, version or ())

    return sorted(tags, key=_key, reverse=True)
