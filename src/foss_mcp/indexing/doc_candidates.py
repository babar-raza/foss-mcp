"""Extraction of documentation candidates from a furnished-content page's
own overview/content/faq front matter.

This module performs pure extraction only: it turns an already-parsed
furnished page (a Hugo page-bundle dict) into structured ``DocCandidate``
records, mirroring ``example_candidates.py``'s own already-accepted
``extract_candidate_examples``/``CandidateExample`` shape (TC-066) exactly.
It does not verify, classify, chunk, or index anything — that happens
downstream, in ``normalization``/``indexing`` stages this card does not own.

Extraction is confirmed, by real per-pilot tests against real furnished
fixtures, across all 6 pilots: ``pdf_net``, ``pdf_java``, ``pdf_typescript``,
``pdf_go``, ``cells_rust``, and ``slides_python`` (see
``tests/fixtures/furnished/<pilot>/pages/_index.md`` and
``tests/indexing/test_doc_candidates.py``).

Two conventions here are load-bearing, not decorative:

* **The FAQ prefix.** ``search_docs.classify_content_type`` only buckets a
  chunk's ``content_type`` as ``"faq"`` when the chunk's own persisted text
  contains the literal substring ``"faq"`` or ``"frequently asked"`` (see
  ``foss_mcp.mcp.tools.search_docs._CONTENT_TYPE_HINTS``). A real FAQ
  answer's own prose typically contains neither word. Without an explicit
  marker, every FAQ candidate would silently misclassify as
  ``developer_guide`` (the classifier's own default) and the ``faq``
  content_type would stay permanently empty even where real FAQ content
  exists. So every FAQ-origin candidate's body is formatted EXACTLY as
  ``f"FAQ: {question}\\n\\n{answer}"`` — this prefix supplies the literal
  substring ``"faq"`` the classifier requires.

* **No synthetic heading.** ``foss_mcp.normalization.chunker.chunk_document``
  splits a body on markdown ``#``-style heading syntax and MOVES the heading
  text into ``Chunk.section_title``, DROPPING it from ``Chunk.text`` — and
  ``classify_content_type`` only ever inspects the persisted ``text`` field
  (``build_lexical_index`` never persists ``content_type`` or
  ``section_title``, only ``text``/``tokens``/``chunk_id``). Adding a
  synthetic ``#`` heading to a candidate's body would therefore silently
  remove real classification signal by moving it somewhere the classifier
  never looks. Every ``DocCandidate.body`` here is left as raw,
  heading-free prose so ``chunk_document``'s own paragraph-splitting path
  handles it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["DocCandidate", "extract_doc_sections"]


@dataclass(frozen=True)
class DocCandidate:
    """A single, not-yet-indexed documentation candidate extracted from a
    furnished page's own overview/content/faq front matter.

    ``origin`` is one of ``"overview"``, ``"content"``, or ``"faq"``, for
    traceability only — it is not consulted by any downstream classification
    logic.
    """

    title: str
    body: str
    origin: str


def _is_enabled(section: object) -> bool:
    return isinstance(section, Mapping) and section.get("enable") is True


def _extract_overview(page: Mapping[str, object]) -> list[DocCandidate]:
    overview = page.get("overview")
    if not _is_enabled(overview):
        return []
    assert isinstance(overview, Mapping)  # narrowed by _is_enabled
    title = overview.get("title")
    content = overview.get("content")
    if not isinstance(title, str) or not isinstance(content, str):
        return []
    return [DocCandidate(title=title, body=content, origin="overview")]


def _extract_content_blocks(page: Mapping[str, object]) -> list[DocCandidate]:
    content_section = page.get("content")
    if not _is_enabled(content_section):
        return []
    assert isinstance(content_section, Mapping)  # narrowed by _is_enabled
    blocks = content_section.get("block", [])
    if not isinstance(blocks, list):
        return []

    candidates: list[DocCandidate] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        title_left = block.get("title_left")
        content_left = block.get("content_left")
        if isinstance(title_left, str) and isinstance(content_left, str):
            candidates.append(DocCandidate(title=title_left, body=content_left, origin="content"))

        title_right = block.get("title_right")
        content_right = block.get("content_right")
        if isinstance(title_right, str) and isinstance(content_right, str):
            candidates.append(DocCandidate(title=title_right, body=content_right, origin="content"))

    return candidates


def _extract_faq(page: Mapping[str, object]) -> list[DocCandidate]:
    faq = page.get("faq")
    if not _is_enabled(faq):
        return []
    assert isinstance(faq, Mapping)  # narrowed by _is_enabled
    entries = faq.get("list", [])
    if not isinstance(entries, list):
        return []

    candidates: list[DocCandidate] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        question = entry.get("question")
        answer = entry.get("answer")
        if not isinstance(question, str) or not isinstance(answer, str):
            continue
        candidates.append(
            DocCandidate(
                title=question,
                body=f"FAQ: {question}\n\n{answer}",
                origin="faq",
            )
        )

    return candidates


def extract_doc_sections(page: Mapping[str, object]) -> list[DocCandidate]:
    """Extract documentation candidates from an already-parsed furnished page dict.

    ``page`` is the dict produced by ``yaml.safe_load`` on the front-matter
    block of a furnished-content page bundle (never a file path — callers
    load and parse the page themselves).

    Three sections are inspected, each independently and gracefully skipped
    when absent, disabled, or malformed (mirroring
    ``extract_candidate_examples``'s own defensive ``isinstance``/missing-key
    handling — never an error):

    * ``overview`` — one candidate when ``overview.enable`` is ``True``,
      with ``title=overview["title"]`` and ``body=overview["content"]``.
    * ``content`` — two candidates per entry of ``content.block[]`` when
      ``content.enable`` is ``True``: one from
      ``title_left``/``content_left``, one from
      ``title_right``/``content_right``.
    * ``faq`` — one candidate per entry of ``faq.list[]`` when
      ``faq.enable`` is ``True``, with ``body`` formatted as
      ``f"FAQ: {question}\\n\\n{answer}"`` (see module docstring for why).
    """
    candidates: list[DocCandidate] = []
    candidates.extend(_extract_overview(page))
    candidates.extend(_extract_content_blocks(page))
    candidates.extend(_extract_faq(page))
    return candidates
