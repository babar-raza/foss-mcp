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

# A markdown fence opener: up to 3 leading spaces/tabs (CommonMark's own indentation allowance),
# then 3+ backticks or 3+ tildes, with anything (a language tag, nothing at all) after it on the
# same line. Mixing fence characters is not allowed by real markdown semantics - a backtick fence
# is only closed by backticks, a tilde fence only by tildes - so the character and run length seen
# here are what a later candidate closing line is checked against.
_FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _fence_spans(body: str) -> list[tuple[int, int]]:
    """``[start, end)`` character ranges of *body* that sit inside an OPEN markdown fence.

    A span runs from its opening delimiter line through its matching closing delimiter line - one
    whose stripped text is nothing but the SAME fence character, repeated AT LEAST as many times as
    the opener (real markdown lets a closing fence be longer than its opener, never shorter, and
    never a different character - this is why four-or-more-backtick fences exist at all, as an
    escape for code that itself contains a run of three backticks). A fence opened but never closed
    before the body ends is treated as closing at end-of-body: the chosen fallback for an unclosed
    fence, so one unterminated fence can never silently swallow every heading in the rest of a real
    document - it can only swallow the rest of its own, already-malformed block.
    """
    spans: list[tuple[int, int]] = []
    fence_char = ""
    fence_len = 0
    open_start: int | None = None
    length = len(body)
    pos = 0
    while pos <= length:
        newline = body.find("\n", pos)
        line_end = newline if newline != -1 else length
        line = body[pos:line_end]
        if open_start is None:
            opener = _FENCE_OPEN.match(line)
            if opener is not None:
                run = opener.group(1)
                fence_char, fence_len = run[0], len(run)
                open_start = pos
        else:
            stripped = line.strip()
            if len(stripped) >= fence_len and stripped == fence_char * len(stripped):
                spans.append((open_start, line_end))
                open_start = None
        if newline == -1:
            break
        pos = newline + 1
    if open_start is not None:
        spans.append((open_start, length))
    return spans


def _inside_any_span(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


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
    """(heading, section body) for each ``#``-style heading in *body*; ``[]`` if there are none.

    A line that would otherwise match as a heading is never treated as one while it sits inside an
    OPEN fenced code block (``_fence_spans``) - a column-0 ``#`` comment in a fenced Python or shell
    snippet is exactly such a line, and was previously cutting the chunk there and orphaning the
    real code after it.
    """
    spans = _fence_spans(body)
    matches = [match for match in _HEADING.finditer(body) if not _inside_any_span(match.start(), spans)]
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
