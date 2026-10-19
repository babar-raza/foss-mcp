"""release_alignment: does the indexed API include a given release? (G3/TC-519)

The release notes ``list_recent_changes`` serves and the API the index describes can be different
versions, and nothing used to say which is which. This module answers, for each served release,
with one deterministic rule and a stated basis:

1. ``commit_equal``: the release's tag commit (the 40-hex ``Release.target_commit``) equals a source
   commit the index was built from. The release IS in the index (``True``).
2. ``version_not_newer`` / ``version_newer``: otherwise, when both the release tag and the package
   manifest declare a version and the two have the SAME major number, the release is in the index
   unless it is newer than the manifest's version. Calendar-style tags (``v26.09``) against a
   ``0.1.0`` manifest have different majors and are NOT compared.
3. ``unknown``: anything else (``None``). Never guessed.

G3/TC-560 adds the drift verdict (``drift_verdict`` / ``drift_from_record``): whether the indexed commit is
AHEAD of the latest published release/tag (``index_ahead_of_package``), BEHIND it (``package_ahead_of_index``),
diverged, in sync, or ``unknown``, decided from git ancestry counts that ingestion recorded. Every emitter
(``report_index_freshness``, the ``get_product_reference`` install notice, ``list_recent_changes``) words the
fact through this one function.

The module is pure: no I/O, no network.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from foss_mcp.extraction.github_release_reader import DriftRecord, Release
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs, _manifest_version

_VERSION_RUN = re.compile(r"\d+(?:\.\d+)+")
_GENERATION_STAMP = re.compile(r"(\d{8})T(\d{6})\d*Z")

BASIS_COMMIT_EQUAL = "commit_equal"
BASIS_VERSION_NOT_NEWER = "version_not_newer"
BASIS_VERSION_NEWER = "version_newer"
BASIS_UNKNOWN = "unknown"


def parse_release_version(tag: str) -> tuple[int, ...] | None:
    """The first dotted run of at least two integers in *tag* (``V26.10.0`` -> (26, 10, 0);
    ``v26.09`` -> (26, 9)), else None. A tag such as ``latest`` or ``v1`` carries no version."""
    match = _VERSION_RUN.search(tag)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def package_version(inputs: ProductReferenceInputs | None) -> tuple[int, ...] | None:
    """The version the platform's packaging manifest declares, parsed like a release tag.

    None for go (go.mod has no version), for no manifest, and for an empty or unparseable value.
    """
    if inputs is None or inputs.manifest_text is None:
        return None
    declared = _manifest_version(inputs.platform, inputs.manifest_text)
    return parse_release_version(declared) if declared else None


def _same_commit(indexed_commit: str, release_commit: str) -> bool:
    return indexed_commit.strip().lower() == release_commit.strip().lower()


def release_in_index(
    release: Release,
    *,
    indexed_commits: Sequence[str],
    indexed_version: tuple[int, ...] | None,
) -> tuple[bool | None, str]:
    """``(in_index, basis)`` for *release*: True when the indexed API includes it, False when it is
    newer than the index, None when that cannot be told. Tag entries follow the same rules."""
    release_commit = release.target_commit
    if release_commit is not None and any(_same_commit(commit, release_commit) for commit in indexed_commits):
        return True, BASIS_COMMIT_EQUAL
    release_version = parse_release_version(release.tag_name)
    if release_version is not None and indexed_version is not None:
        if release_version[0] == indexed_version[0]:
            width = max(len(release_version), len(indexed_version))
            release_version = release_version + (0,) * (width - len(release_version))
            indexed_version = indexed_version + (0,) * (width - len(indexed_version))
            if release_version > indexed_version:
                return False, BASIS_VERSION_NEWER
            return True, BASIS_VERSION_NOT_NEWER
    return None, BASIS_UNKNOWN


def align_releases(
    releases: Sequence[Release],
    *,
    indexed_commits: Sequence[str],
    indexed_version: tuple[int, ...] | None,
) -> dict[str, tuple[bool | None, str]]:
    """``release_in_index`` for every release, keyed by tag name (the first occurrence wins)."""
    aligned: dict[str, tuple[bool | None, str]] = {}
    for release in releases:
        if release.tag_name not in aligned:
            aligned[release.tag_name] = release_in_index(
                release, indexed_commits=indexed_commits, indexed_version=indexed_version
            )
    return aligned


INDEX_AHEAD = "index_ahead_of_package"
PACKAGE_AHEAD = "package_ahead_of_index"
DRIFT_IN_SYNC = "in_sync"
DRIFT_DIVERGED = "diverged"
DRIFT_UNKNOWN = "unknown"

_SHORT = 12


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _version_text(version: str | tuple[int, ...] | None) -> str | None:
    if version is None:
        return None
    if isinstance(version, tuple):
        return ".".join(str(part) for part in version)
    return version or None


def drift_verdict(
    indexed_commit: str | None,
    latest_release_tag: str | None,
    commits_ahead: int | None,
    commits_behind: int | None,
    published_version: str | tuple[int, ...] | None,
    indexed_version: str | tuple[int, ...] | None,
) -> dict:
    """G3/TC-560: the one place the index-versus-package drift is decided and worded.

    *commits_ahead* is how many commits the indexed commit has that the latest release tag's commit
    lacks, *commits_behind* how many the tag's commit has that the indexed commit lacks (git ancestry,
    GitHub's compare ``ahead_by`` / ``behind_by`` with the tag as base). The verdict is
    ``index_ahead_of_package`` (ahead only), ``package_ahead_of_index`` (behind only), ``diverged`` (both),
    ``in_sync`` (neither) or ``unknown`` (a count is missing: absent data is never read as no drift).
    The result always carries the same keys: ``drift``, ``index_ahead_of_package`` /
    ``package_ahead_of_index`` (bool, or None when unknown), ``commit_distance`` (the commits the
    leading side is ahead; None when unknown or diverged), ``commits_ahead``, ``commits_behind``,
    ``published_version``, ``indexed_version``, ``latest_release_tag``, ``indexed_commit`` and a
    ``message`` sentence (None when there is nothing to say). Pure: no I/O.
    """
    published = _version_text(published_version)
    declared = _version_text(indexed_version)
    result: dict = {
        "drift": DRIFT_UNKNOWN,
        "index_ahead_of_package": None,
        "package_ahead_of_index": None,
        "commit_distance": None,
        "commits_ahead": commits_ahead,
        "commits_behind": commits_behind,
        "published_version": published,
        "indexed_version": declared,
        "latest_release_tag": latest_release_tag,
        "indexed_commit": indexed_commit,
        "message": None,
    }
    if (
        not isinstance(commits_ahead, int)
        or isinstance(commits_ahead, bool)
        or not isinstance(commits_behind, int)
        or isinstance(commits_behind, bool)
        or commits_ahead < 0
        or commits_behind < 0
    ):
        result["commits_ahead"] = result["commits_behind"] = None
        return result
    release = f"the latest release {latest_release_tag}" if latest_release_tag else "the latest release"
    if published and published != latest_release_tag:
        release += f" (version {published})"
    indexed = f"the indexed commit {indexed_commit[:_SHORT]}" if indexed_commit else "the indexed commit"
    index_ahead = commits_ahead > 0 and commits_behind == 0
    package_ahead = commits_behind > 0 and commits_ahead == 0
    result["index_ahead_of_package"] = index_ahead
    result["package_ahead_of_index"] = package_ahead
    if index_ahead:
        result["drift"] = INDEX_AHEAD
        result["commit_distance"] = commits_ahead
        result["message"] = (
            f"{indexed.capitalize()} is {_plural(commits_ahead, 'commit')} ahead of {release}: the index "
            "describes source that is not in the published package, which can lack types and members "
            "documented here."
        )
    elif package_ahead:
        result["drift"] = PACKAGE_AHEAD
        result["commit_distance"] = commits_behind
        result["message"] = (
            f"{release.capitalize()} is {_plural(commits_behind, 'commit')} ahead of {indexed}: the "
            "published package can have types and members this index does not describe."
        )
    elif commits_ahead > 0:
        result["drift"] = DRIFT_DIVERGED
        result["message"] = (
            f"{indexed.capitalize()} and {release} have diverged ({_plural(commits_ahead, 'commit')} only "
            f"in the index, {_plural(commits_behind, 'commit')} only in the release): the published "
            "package and this index each have members the other lacks."
        )
    else:
        result["drift"] = DRIFT_IN_SYNC
        result["commit_distance"] = 0
        result["message"] = f"{indexed.capitalize()} is the commit of {release}."
    return result


def drift_from_record(
    record: DriftRecord | None,
    *,
    indexed_commit: str | None = None,
    indexed_version: str | tuple[int, ...] | None = None,
) -> dict:
    """``drift_verdict`` for a sidecar's recorded ancestry. No record is ``unknown``; so is a record taken
    for a different commit than *indexed_commit* (the one the serving index was built from), because
    counts for another commit say nothing about this index. The published version is the tag's own
    version number (``v26.8.0`` -> ``26.8.0``), else the tag name."""
    if record is None:
        return drift_verdict(indexed_commit, None, None, None, None, indexed_version)
    if indexed_commit is not None and not _commits_agree(indexed_commit, record.indexed_commit):
        return drift_verdict(indexed_commit, record.tag, None, None, None, indexed_version)
    parsed = parse_release_version(record.tag)
    published = ".".join(str(part) for part in parsed) if parsed else record.tag
    return drift_verdict(
        record.indexed_commit, record.tag, record.ahead_by, record.behind_by, published, indexed_version
    )


def _commits_agree(first: str, second: str) -> bool:
    a, b = first.strip().lower(), second.strip().lower()
    shortest = min(len(a), len(b))
    return shortest >= 7 and a[:shortest] == b[:shortest]


def generation_indexed_at(generation_id: str | None) -> str | None:
    """The UTC time a generation was built, read from its id: the ``YYYYMMDDTHHMMSS`` stamp (with
    optional fraction digits and a ``Z``) as ``YYYY-MM-DDTHH:MM:SSZ``; None when there is none."""
    if not generation_id:
        return None
    match = _GENERATION_STAMP.search(generation_id)
    if match is None:
        return None
    try:
        stamp = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
