"""The README sections a deployment's index already holds, recovered for ``get_product_reference``.

``get_product_reference`` used to answer ``formats`` and ``limitations`` "has no data source in this
project yet" although the same deployment's index holds the README section "Scope and Limitations": the
ingestion job indexes the committed furnished page as ``FQN: Doc: <title>`` chunks. This module reads
those chunks back out of the deployment's active generation instead of fetching the README again, so the
tool and ``search_docs`` can never disagree about what the README says.

Three facts about those chunks drive the code below.

* A chunk's first line is ``FQN: Doc: <title>``; its last line is ``Source-Commit: <sha>``, appended by
  ``infra/build_chunks.py``. A multi-part chunk (card TC-517) carries ``Part: <i> of <n>`` as the last
  body line, before the ``Source-Commit:`` line.
* Chunk order inside a generation is by hash, not by README order. The ``Part:`` marker is the only way
  to restore the original order; a generation indexed before TC-517 has no markers, its chunks are
  assembled in generation order, and the section says so (``ordered`` is False).
* A FAQ answer is chunked under the question as its title ("How do I install ...?"), its first body line
  starts with ``FAQ:``, and it is never the installation section.

The module is pure apart from ``read_readme_sections``, which touches the generation store and never
raises: any failure yields the empty ``ReadmeSections``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from foss_mcp.mcp.routing import Scope

# Case-insensitive substrings of the ``Doc:`` title that select each section.
LIMITATIONS_TITLE_PATTERN = "limitation"
INSTALL_TITLE_PATTERN = "install"
CAPABILITIES_TITLE_PATTERN = "capabilit"

_SOURCE_KIND = "self_extracted"
_DOC_PREFIX = "FQN: Doc: "
_FAQ_PREFIX = "FAQ:"
_PART_LINE = re.compile(r"^Part:\s*(\d+)\s+of\s+(\d+)\s*$")
_SOURCE_COMMIT_LINE = re.compile(r"^Source-Commit:\s*(\S+)\s*$")
# The page generator's own lead-in paragraph of the limitations page - not README text.
_LIMITATIONS_LEAD_IN = (
    "Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries"
)


@dataclass(frozen=True)
class ReadmeSection:
    """One README section as indexed: its ``title``, its assembled ``text``, the source ``commit`` the
    chunks were indexed from (None when no chunk records one), whether the chunks could be put back in
    ``ordered`` README order, and how many chunks (``parts``) it was assembled from."""

    title: str
    text: str
    commit: str | None
    ordered: bool
    parts: int


@dataclass(frozen=True)
class ReadmeSections:
    limitations: ReadmeSection | None = None
    install: ReadmeSection | None = None
    capabilities: ReadmeSection | None = None


@dataclass(frozen=True)
class _Chunk:
    title: str
    text: str
    commit: str | None
    part: int | None


def _strip_lead_in(lines: list[str]) -> list[str]:
    """Drop the generated limitations lead-in sentence (it may wrap over several lines, ending at the
    first line that ends a sentence)."""
    for index, line in enumerate(lines):
        if not line.startswith(_LIMITATIONS_LEAD_IN):
            continue
        end = index
        while end < len(lines) - 1 and not lines[end].rstrip().endswith((":", ".")):
            end += 1
        return lines[:index] + lines[end + 1 :]
    return lines


def _parse_chunk(text: str, pattern: str) -> _Chunk | None:
    """The chunk behind *text* when it is a non-FAQ ``Doc:`` chunk whose title contains *pattern*."""
    lines = text.split("\n")
    if not lines or not lines[0].startswith(_DOC_PREFIX):
        return None
    title = lines[0][len(_DOC_PREFIX) :].strip()
    if pattern.lower() not in title.lower():
        return None
    body = lines[1:]
    if body and body[0].startswith(_FAQ_PREFIX):
        return None
    commit: str | None = None
    part: int | None = None
    kept: list[str] = []
    for line in body:
        commit_match = _SOURCE_COMMIT_LINE.match(line)
        if commit_match is not None:
            commit = commit_match.group(1)
        else:
            kept.append(line)
    # The part marker is the LAST body line (before the Source-Commit line the builder appends).
    while kept and not kept[-1].strip():
        kept.pop()
    part_match = _PART_LINE.match(kept[-1]) if kept else None
    if part_match is not None:
        part = int(part_match.group(1))
        kept.pop()
    if pattern == LIMITATIONS_TITLE_PATTERN:
        kept = _strip_lead_in(kept)
    return _Chunk(title=title, text="\n".join(kept).strip("\n"), commit=commit, part=part)


def _assemble(chunks: list[_Chunk]) -> ReadmeSection | None:
    """One section from the collected chunks of a pattern. Chunks are grouped by title (first-seen
    order); a group is put in ``Part:`` order when every chunk of it carries a marker."""
    if not chunks:
        return None
    titles: list[str] = []
    groups: dict[str, list[_Chunk]] = {}
    for chunk in chunks:
        if chunk.title not in groups:
            titles.append(chunk.title)
            groups[chunk.title] = []
        groups[chunk.title].append(chunk)
    ordered = True
    pieces: list[str] = []
    for title in titles:
        group = groups[title]
        if len(group) == 1:
            pass  # one chunk is trivially in order
        elif all(chunk.part is not None for chunk in group):
            group = sorted(group, key=lambda chunk: chunk.part or 0)
        else:
            ordered = False
        pieces.extend(chunk.text for chunk in group if chunk.text.strip())
    text = "\n\n".join(pieces)
    commit = next((chunk.commit for chunk in chunks if chunk.commit), None)
    if not text.strip():
        return None
    return ReadmeSection(
        title="; ".join(titles), text=text, commit=commit, ordered=ordered, parts=len(chunks)
    )


def _documents_text(documents: Mapping[str, Mapping[str, str]]) -> list[str]:
    texts: list[str] = []
    for record in documents.values():
        text = record.get("text") if isinstance(record, Mapping) else None
        if isinstance(text, str):
            texts.append(text)
    return texts


def sections_from_documents(documents: Mapping[str, Mapping[str, str]]) -> ReadmeSections:
    """The limitations, installation and capabilities sections held by *documents* (a generation's
    ``lexical_index.documents``: id -> ``{"text": ...}``). Pure."""
    texts = _documents_text(documents)

    def section(pattern: str) -> ReadmeSection | None:
        chunks = [chunk for chunk in (_parse_chunk(text, pattern) for text in texts) if chunk is not None]
        return _assemble(chunks)

    return ReadmeSections(
        limitations=section(LIMITATIONS_TITLE_PATTERN),
        install=section(INSTALL_TITLE_PATTERN),
        capabilities=section(CAPABILITIES_TITLE_PATTERN),
    )


def read_readme_sections(store: Any, scope: Scope) -> ReadmeSections:
    """The README sections of the active ``self_extracted`` generation of *scope*. Never raises: a store
    that fails, no active generation, or a generation without documents all yield the empty result."""
    try:
        key = "::".join((scope.family, scope.platform, _SOURCE_KIND))
        generation_id = store.read_active(key)
        if not generation_id:
            return ReadmeSections()
        manifest = store.read_generation(key, generation_id)
        lexical = manifest.payload.get("lexical_index") or {}
        documents = lexical.get("documents") or {}
        return sections_from_documents(documents)
    except Exception:  # noqa: BLE001 - README text is a best-effort extra; it must never fail the tool
        return ReadmeSections()
