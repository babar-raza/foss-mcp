"""Extraction of candidate example code blocks from a furnished-content page.

This module performs pure extraction only: it turns an already-parsed
furnished page (a Hugo page-bundle dict) into structured
``CandidateExample`` records. It does not verify, compile, or execute any
extracted code — that is deliberately deferred to a later stage of the
verified-example pipeline (REQ-G2-048).

Extraction is confirmed, by real per-pilot tests against real extraction
fixtures, across all 6 pilots: ``pdf_net``, ``pdf_java``, ``pdf_typescript``,
``pdf_go``, ``cells_rust``, and ``slides_python`` (see
``tests/fixtures/furnished/<pilot>/pages/_index.md`` and
``tests/indexing/test_example_candidates.py``) — not just ``pdf_net``.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = ["CandidateExample", "extract_candidate_examples"]

_FENCE_RE = re.compile(
    r"```[ \t]*([^\n`]*)\n(.*?)```",
    re.DOTALL,
)


@dataclass(frozen=True)
class CandidateExample:
    """A single, not-yet-verified example extracted from furnished content.

    ``page_code`` is the code of EVERY candidate on the same page, in document order, including
    this candidate's own. A verifier may use it to supply what a README writes once for the whole
    page (for example the ``using`` directives of a .NET page). It is excluded from equality,
    hashing and repr, so two candidates compare equal whenever their four content fields match.
    """

    title: str
    description: str
    language: str
    code: str
    page_code: tuple[str, ...] = field(default=(), compare=False, repr=False)


def extract_candidate_examples(page: Mapping[str, object]) -> list[CandidateExample]:
    """Extract candidate examples from an already-parsed furnished page dict.

    ``page`` is the dict produced by ``yaml.safe_load`` on the front-matter
    block of a furnished-content page bundle (never a file path — callers
    load and parse the page themselves). Each entry of
    ``page["single"]["block"]`` is inspected: the markdown text before the
    first fenced code block becomes ``description``, the fence's language
    tag becomes ``language``, and the fence's exact body becomes ``code``.
    A block with no fenced code at all is skipped, not treated as an error.

    Every returned candidate carries ``page_code``: the code of all returned candidates, in
    document order, including its own.
    """
    single = page.get("single", {})
    if not isinstance(single, Mapping):
        return []
    blocks = single.get("block", [])
    if not isinstance(blocks, list):
        return []

    candidates: list[CandidateExample] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        title = block.get("title")
        content = block.get("content")
        if not isinstance(title, str) or not isinstance(content, str):
            continue

        match = _FENCE_RE.search(content)
        if match is None:
            # No code fence at all: an honest partial extraction, skip it.
            continue

        description = content[: match.start()].strip()
        language = match.group(1).strip()
        code = match.group(2).strip()

        candidates.append(
            CandidateExample(
                title=title,
                description=description,
                language=language,
                code=code,
            )
        )

    page_code = tuple(candidate.code for candidate in candidates)
    return [dataclasses.replace(candidate, page_code=page_code) for candidate in candidates]
