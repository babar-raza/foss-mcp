"""Split a normalized document into SECTION-granularity chunks - never one page-sized chunk.

Page-level chunking is explicitly rejected (plan 11.3): a whole page can be otherwise correct
and still carry one fabricated sentence, and a chunk the size of the page cannot localize that
defect any better than not chunking at all. A section is small enough that a citation failure
inside it means something specific.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from foss_mcp.normalization.document_schema import (
    NOT_CHECKED,
    NormalizedDocument,
    Provenance,
    ValidationResult,
)

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_BLANK_LINE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class Chunk:
    """One section of a document - small enough that one unresolved claim inside it is a
    specific, localizable defect, not a fact about the whole page.
    """

    section_title: str
    text: str
    source_kind: str
    content_type: str
    provenance: Provenance
    trust_tier: str
    evidence_refs: tuple[str, ...] = ()
    validation: ValidationResult = NOT_CHECKED


def _heading_sections(body: str) -> list[tuple[str, str]]:
    """(heading, section body) for each ``#``-style heading in *body*; ``[]`` if there are none."""
    matches = list(_HEADING.finditer(body))
    if not matches:
        return []
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        title = match.group(2).strip()
        text = body[start:end].strip()
        sections.append((title, text or title))
    return sections


def _paragraph_sections(body: str) -> list[tuple[str, str]]:
    """A body with no headings still splits at blank lines - never treated as one page chunk."""
    paragraphs = [p.strip() for p in _BLANK_LINE.split(body) if p.strip()]
    return [("", paragraph) for paragraph in paragraphs]


def _sections(body: str) -> list[tuple[str, str]]:
    stripped = body.strip()
    if not stripped:
        return []
    sections = _heading_sections(stripped)
    if sections:
        return sections
    sections = _paragraph_sections(stripped)
    return sections or [("", stripped)]


def chunk_document(doc: NormalizedDocument) -> list[Chunk]:
    """Every section of *doc* as its own chunk, carrying the provenance and trust tier the
    whole document has. A document with real internal structure (headings, or at minimum
    multiple paragraphs) always yields more than one chunk; a document that is genuinely one
    short fact (a manifest's field summary) may legitimately yield exactly one.
    """
    return [
        Chunk(
            section_title=title,
            text=text,
            source_kind=doc.source_kind,
            content_type=doc.content_type,
            provenance=doc.provenance,
            trust_tier=doc.trust_tier,
            evidence_refs=doc.evidence_refs,
        )
        for title, text in _sections(doc.body)
    ]


def with_validation(chunk: Chunk, validation: ValidationResult) -> Chunk:
    return replace(chunk, validation=validation)
