"""list_recent_changes: recent releases, each carrying its own honest richness verdict.

A release whose body is a real migration note must not be reported the same way as one that
is a filled-in template or an empty body (``foss_mcp.extraction.github_release_reader.
classify_richness``); this tool exists to carry that distinction through to a caller, not
collapse it back into "has release notes: yes/no".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foss_mcp.extraction.github_release_reader import Release, RichnessVerdict
from foss_mcp.mcp.sidecar_state import (
    RELEASES_FETCH_FAILED_MESSAGE,
    SIDECAR_FETCH_FAILED,
    SIDECAR_PRESENT,
    SidecarFetchFailedError,
)


@dataclass(frozen=True)
class ChangeEntry:
    tag_name: str
    richness: RichnessVerdict
    body: str
    published_at: str | None = None
    url: str | None = None
    # G3/TC-519: true when the indexed API includes this release, false when it is newer than the index,
    # None when that cannot be told; `in_index_basis` says why (see `release_alignment`). `source` is
    # `release` for a GitHub Release and `tag` for a tag of a repository that has no Releases.
    in_index: bool | None = None
    in_index_basis: str = "unknown"
    source: str = "release"


def list_recent_changes(
    releases: Sequence[Release],
    *,
    limit: int = 10,
    state: str = SIDECAR_PRESENT,
    alignment: Mapping[str, tuple[bool | None, str]] | None = None,
) -> list[ChangeEntry]:
    """The most recent *limit* releases (assumed already ordered newest-first, as
    ``github_release_reader.fetch_releases`` returns them), each carrying the SAME richness
    verdict ``classify_richness`` gave it - never dropped, never upgraded, never guessed at.

    G3/TC-507: when the releases data could not be fetched (*state* is ``fetch_failed``) this
    raises ``SidecarFetchFailedError`` rather than returning an empty list, which a caller would
    read as "this product has no releases". A good list and a truthfully empty list keep the
    bare-list shape.

    G3/TC-519: *alignment* (tag -> (in_index, basis), from `release_alignment.align_releases`) fills
    each entry's `in_index` and `in_index_basis`; without it the entries carry the defaults. The
    result is still a plain list in the same order. An entry whose ``source`` is ``tag`` came from
    ``github_release_reader.fetch_tags`` (written to the sidecar by ``infra/fetch_recent_releases.py``
    for a repository that publishes no GitHub Releases) and has no notes.
    """
    if state == SIDECAR_FETCH_FAILED:
        raise SidecarFetchFailedError(RELEASES_FETCH_FAILED_MESSAGE)
    aligned = alignment or {}
    return [_entry(release, aligned.get(release.tag_name, (None, "unknown"))) for release in releases[:limit]]


def _entry(release: Release, aligned: tuple[bool | None, str]) -> ChangeEntry:
    return ChangeEntry(
        tag_name=release.tag_name,
        richness=release.richness,
        body=release.body,
        published_at=release.published_at,
        url=release.url,
        in_index=aligned[0],
        in_index_basis=aligned[1],
        source=release.source,
    )
