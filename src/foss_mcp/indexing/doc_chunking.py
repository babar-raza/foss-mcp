"""TC-517 (REQ-G3-008): chunk ONE documentation candidate into retrievable, bounded chunks.

``chunk_document`` splits a heading-free body at EVERY blank line, which leaves a FAQ question apart
from its answer, a "...:" lead-in apart from the list it introduces, and a long README class table as
one chunk that the 24,000-character guard then silently cuts. This module takes the sections
``chunk_document`` already yields and regroups them:

1. a FAQ candidate (``origin == "faq"``) is ONE chunk (question, blank line, answer);
2. otherwise neighbouring sections merge into one chunk while the chunk is shorter than
   ``MIN_DOC_CHUNK_CHARS``, or its last non-empty line ends with ``:``, or the next section still fits
   within ``TARGET_DOC_CHUNK_CHARS``; a chunk is never ended inside a fenced code block;
3. a chunk longer than ``MAX_DOC_CHUNK_CHARS`` is split at table-row boundaries (every later part
   repeats the table's header and separator rows) or, for any other text, at line boundaries, and as
   a last resort a single over-long line is cut at the limit;
4. when a candidate yields more than one chunk, each carries ``Part: <i> of <n>`` as its last line so
   the original order is recoverable.

No line is dropped, reordered or rewritten; the only added lines are the ``Part`` marker and the
repeated table header. The module is pure and deterministic.
"""

from __future__ import annotations

from foss_mcp.indexing.doc_candidates import DocCandidate
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

MIN_DOC_CHUNK_CHARS = 400
TARGET_DOC_CHUNK_CHARS = 3000
MAX_DOC_CHUNK_CHARS = 7600

_FENCE = "```"
_SECTION_SEPARATOR = "\n\n"


def _fence_count(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.strip().startswith(_FENCE))


def _inside_fence(text: str) -> bool:
    return _fence_count(text) % 2 == 1


def _ends_with_colon(text: str) -> bool:
    for line in reversed(text.split("\n")):
        if line.strip():
            return line.rstrip().endswith(":")
    return False


def _merge(sections: list[str]) -> list[str]:
    """Regroup *sections* into chunks of about ``TARGET_DOC_CHUNK_CHARS``."""
    merged: list[str] = []
    buffer = ""
    for section in sections:
        if not buffer:
            buffer = section
            continue
        joined_length = len(buffer) + len(_SECTION_SEPARATOR) + len(section)
        must_join = (
            _inside_fence(buffer)
            or len(buffer) < MIN_DOC_CHUNK_CHARS
            or _ends_with_colon(buffer)
            or joined_length <= TARGET_DOC_CHUNK_CHARS
        )
        if must_join:
            buffer = f"{buffer}{_SECTION_SEPARATOR}{section}"
        else:
            merged.append(buffer)
            buffer = section
    if buffer:
        if merged and len(buffer) < MIN_DOC_CHUNK_CHARS and not _inside_fence(buffer):
            merged[-1] = f"{merged[-1]}{_SECTION_SEPARATOR}{buffer}"
        else:
            merged.append(buffer)
    return merged


def _is_table(lines: list[str]) -> bool:
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return False
    pipe_lines = sum(1 for line in non_empty if line.lstrip().startswith("|"))
    return pipe_lines * 2 >= len(non_empty)


def _cut_long_line(line: str, limit: int) -> list[str]:
    return [line[start : start + limit] for start in range(0, len(line), limit)]


def _split_lines(lines: list[str], limit: int, repeated: list[str]) -> list[str]:
    """Pack *lines* into parts of at most *limit* characters each. Every part after the first starts
    with the *repeated* lines (a table's header and separator rows; empty for plain text)."""
    parts: list[str] = []
    current: list[str] = []
    current_length = 0
    carried = 0  # how many leading lines of ``current`` are the repeated header, not new content
    for line in lines:
        pieces = _cut_long_line(line, limit) if len(line) > limit else [line]
        for piece in pieces:
            added = len(piece) + (1 if current else 0)
            if len(current) > carried and current_length + added > limit:
                parts.append("\n".join(current))
                current = list(repeated)
                carried = len(current)
                current_length = len("\n".join(current))
                added = len(piece) + (1 if current else 0)
            current.append(piece)
            current_length += added
    if len(current) > carried:
        parts.append("\n".join(current))
    return parts


def _split_oversized(text: str) -> list[str]:
    """*text* unchanged when it fits; otherwise its parts, none longer than ``MAX_DOC_CHUNK_CHARS``
    (a repeated table header counts toward that length)."""
    if len(text) <= MAX_DOC_CHUNK_CHARS:
        return [text]
    lines = text.split("\n")
    repeated: list[str] = []
    if _is_table(lines):
        repeated = [line for line in lines if line.lstrip().startswith("|")][:2]
        # A header longer than half the limit cannot be repeated without starving the rows.
        if len("\n".join(repeated)) * 2 > MAX_DOC_CHUNK_CHARS:
            repeated = []
    return _split_lines(lines, MAX_DOC_CHUNK_CHARS, repeated)


def chunk_doc_candidate(
    candidate: DocCandidate,
    *,
    source_kind: SourceKind | str,
    content_type: str,
    provenance: Provenance,
    evidence_refs: tuple[str, ...],
) -> list[Chunk]:
    """The chunks of one documentation *candidate*, built exactly as ``_build_doc_chunks`` built them
    before (same ``make_document`` arguments), then regrouped as the module docstring describes."""
    doc = make_document(
        source_kind=source_kind,
        content_type=content_type,
        provenance=provenance,
        evidence_refs=evidence_refs,
        title=candidate.title,
        body=candidate.body,
    )
    sections = chunk_document(doc)
    if not sections:
        return []

    if candidate.origin == "faq":
        texts = [_SECTION_SEPARATOR.join(section.text for section in sections)]
    else:
        texts = _merge([section.text for section in sections])

    pieces: list[str] = []
    for text in texts:
        pieces.extend(_split_oversized(text))

    if len(pieces) > 1:
        pieces = [f"{piece}\nPart: {index} of {len(pieces)}" for index, piece in enumerate(pieces, start=1)]
    template = sections[0]
    return [
        Chunk(
            section_title=template.section_title,
            text=piece,
            source_kind=template.source_kind,
            content_type=template.content_type,
            provenance=template.provenance,
            trust_tier=template.trust_tier,
            evidence_refs=template.evidence_refs,
        )
        for piece in pieces
    ]
