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

The module is pure: no I/O, no network.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from foss_mcp.extraction.github_release_reader import Release
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
