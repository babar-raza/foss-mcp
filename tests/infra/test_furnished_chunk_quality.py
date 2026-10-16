"""TC-517 (REQ-G3-008): the documentation chunks of EVERY committed furnished page are bounded,
complete and categorised by their section titles.

Data-driven over each directory of ``tests/fixtures/furnished`` (except ``sealed_current``): the real
``infra/build_chunks.py`` ``_build_doc_chunks``, then ``_sanitize_chunk_text`` and
``_with_source_commit_line`` exactly as ``main`` applies them. Offline and deterministic.

Measured by the reviewers on the shipped image: 753 documentation chunks, 123 shorter than 200 characters,
20 longer than 8,000 and 5 longer than 24,000 (the README class tables, which the 24,000-character guard
cut, so the classes after the cut were in neither the symbol index nor the documentation).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import re
from functools import cache
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing.doc_candidates import DocCandidate, extract_doc_sections
from foss_mcp.mcp.tools.search_docs import classify_content_type
from foss_mcp.normalization.chunker import Chunk

FURNISHED = Path(__file__).resolve().parents[1] / "fixtures" / "furnished"
BUILD_CHUNKS_SOURCE = Path(__file__).resolve().parents[2] / "infra" / "build_chunks.py"
PILOTS = sorted(path.name for path in FURNISHED.iterdir() if path.is_dir() and path.name != "sealed_current")
LONGEST_PAGES = ["pdf_java", "pdf_net", "words_net", "slides_python", "3d_python"]

DOC_PREFIX = "FQN: Doc: "
MAX_CHARS = 8000
HEADING_RE = re.compile(r"^#{1,6}[ \t]+\S")
TRAILER_RE = re.compile(r"^(Part: \d+ of \d+|Source-Commit: .*)$")

# The title rules, restated here on purpose: a change to the production table must show up as a diff in
# this file too.
TROUBLESHOOTING_TITLE = re.compile(
    r"troubleshoot|known issue|limitation|unsupported|error|problem", re.IGNORECASE
)
GETTING_STARTED_TITLE = re.compile(
    r"install|quick ?start|getting started|prerequisite|setup|dependenc|requirement", re.IGNORECASE
)


@dataclasses.dataclass(frozen=True)
class Built:
    candidates: tuple[DocCandidate, ...]
    chunks: tuple[Chunk, ...]


@cache
def _built(pilot: str) -> Built:
    page_path = FURNISHED / pilot / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(page_path)
    args = argparse.Namespace(library_repository="o/r", library_commit="0" * 40, furnished_page=page_path)
    with contextlib.redirect_stdout(io.StringIO()):
        raw = build_chunks._build_doc_chunks(page, args)
    chunks = tuple(
        build_chunks._with_source_commit_line(
            dataclasses.replace(chunk, text=build_chunks._sanitize_chunk_text(chunk.text))
        )
        for chunk in raw
    )
    return Built(tuple(extract_doc_sections(page)), chunks)


def _title(chunk: Chunk) -> str:
    first_line = chunk.text.split("\n", 1)[0]
    assert first_line.startswith(DOC_PREFIX), first_line
    return first_line[len(DOC_PREFIX) :]


def _body_lines(chunk: Chunk) -> list[str]:
    """The lines between the FQN line and the trailing Part / Source-Commit lines."""
    lines = chunk.text.split("\n")[1:]
    while lines and TRAILER_RE.fullmatch(lines[-1]):
        lines.pop()
    return lines


def _chunks_titled(built: Built, title: str) -> list[Chunk]:
    return [chunk for chunk in built.chunks if _title(chunk) == title]


def test_every_shipped_pilot_has_a_furnished_page() -> None:
    assert len(PILOTS) >= 40
    for pilot in PILOTS:
        assert (FURNISHED / pilot / "pages" / "_index.md").is_file(), pilot
        assert _built(pilot).chunks, f"{pilot}: the furnished page produced no documentation chunk"


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_doc_chunk_is_over_8000_characters_or_truncated(pilot: str) -> None:
    for chunk in _built(pilot).chunks:
        assert len(chunk.text) <= MAX_CHARS, (pilot, _title(chunk), len(chunk.text))
        assert "Truncated:" not in chunk.text, (pilot, _title(chunk))


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_candidate_line_is_lost(pilot: str) -> None:
    built = _built(pilot)
    for candidate in built.candidates:
        produced = {
            line.strip() for chunk in _chunks_titled(built, candidate.title) for line in _body_lines(chunk)
        }
        for line in candidate.body.split("\n"):
            stripped = line.strip()
            if not stripped or HEADING_RE.match(stripped):
                continue  # chunk_document has always moved a heading into the section title
            expected = build_chunks._sanitize_chunk_text(stripped).strip()
            assert expected in produced, (pilot, candidate.title, stripped[:100])


@pytest.mark.parametrize("pilot", PILOTS)
def test_troubleshooting_chunks_have_a_real_body(pilot: str) -> None:
    for chunk in _built(pilot).chunks:
        if classify_content_type(chunk.text) == "troubleshooting":
            body = "\n".join(_body_lines(chunk))
            assert len(body) >= 200, (pilot, _title(chunk), body)


@pytest.mark.parametrize("pilot", PILOTS)
def test_faq_chunks_keep_question_and_answer_together(pilot: str) -> None:
    built = _built(pilot)
    faq_chunks = [chunk for chunk in built.chunks if classify_content_type(chunk.text) == "faq"]
    assert len(faq_chunks) == sum(1 for candidate in built.candidates if candidate.origin == "faq")
    for chunk in faq_chunks:
        lines = _body_lines(chunk)
        assert lines[0].startswith("FAQ: "), (pilot, lines[:1])
        assert lines[0] == f"FAQ: {_title(chunk)}"
        answer = "\n".join(lines[1:]).strip()
        assert len(answer) >= 10, (pilot, _title(chunk), answer)


def _expected_category(chunk: Chunk) -> str:
    lines = _body_lines(chunk)
    if lines and lines[0].startswith("FAQ:"):
        return "faq"
    title = _title(chunk)
    if TROUBLESHOOTING_TITLE.search(title):
        return "troubleshooting"
    if GETTING_STARTED_TITLE.search(title):
        return "getting_started"
    return "developer_guide"


@pytest.mark.parametrize("pilot", PILOTS)
def test_categories_derive_from_titles(pilot: str) -> None:
    for chunk in _built(pilot).chunks:
        assert classify_content_type(chunk.text) == _expected_category(chunk), (pilot, _title(chunk))


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_chunk_ends_with_a_dangling_colon(pilot: str) -> None:
    for chunk in _built(pilot).chunks:
        lines = [line for line in _body_lines(chunk) if line.strip()]
        assert lines, (pilot, _title(chunk))
        assert not lines[-1].rstrip().endswith(":"), (pilot, _title(chunk), lines[-1][-100:])


@pytest.mark.parametrize("pilot", LONGEST_PAGES)
def test_the_class_table_of_the_longest_pages_is_split_not_cut(pilot: str) -> None:
    built = _built(pilot)
    longest = max(built.candidates, key=lambda candidate: len(candidate.body))
    assert len(longest.body) > build_chunks.MAX_CHUNK_TEXT_CHARS, "this page no longer has an oversized table"
    rows = [line.strip() for line in longest.body.split("\n") if line.lstrip().startswith("|")]
    assert rows, pilot
    parts = _chunks_titled(built, longest.title)
    assert len(parts) > 1
    assert not any("Truncated:" in chunk.text for chunk in built.chunks)
    joined = "\n".join(chunk.text for chunk in parts)
    assert build_chunks._sanitize_chunk_text(rows[-1]).strip() in joined
    for position, chunk in enumerate(parts, start=1):
        assert f"\nPart: {position} of {len(parts)}\n" in chunk.text


def test_build_chunks_calls_the_new_chunker() -> None:
    source = BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    assert "from foss_mcp.indexing.doc_chunking import" in source
    assert "chunk_doc_candidate(" in source
