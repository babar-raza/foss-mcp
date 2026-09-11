"""list_recent_changes: recent releases, each carrying its own honest richness verdict.

A release whose body is a real migration note must not be reported the same way as one that
is a filled-in template or an empty body (``foss_mcp.extraction.github_release_reader.
classify_richness``); this tool exists to carry that distinction through to a caller, not
collapse it back into "has release notes: yes/no".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from foss_mcp.extraction.github_release_reader import Release, RichnessVerdict


@dataclass(frozen=True)
class ChangeEntry:
    tag_name: str
    richness: RichnessVerdict
    body: str


def list_recent_changes(releases: Sequence[Release], *, limit: int = 10) -> list[ChangeEntry]:
    """The most recent *limit* releases (assumed already ordered newest-first, as
    ``github_release_reader.fetch_releases`` returns them), each carrying the SAME richness
    verdict ``classify_richness`` gave it - never dropped, never upgraded, never guessed at.
    """
    return [
        ChangeEntry(tag_name=release.tag_name, richness=release.richness, body=release.body)
        for release in releases[:limit]
    ]
