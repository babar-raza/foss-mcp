"""TC-517 (REQ-G3-008): ``chunk_doc_candidate`` regroups the sections ``chunk_document`` yields.

Synthetic candidates, offline and deterministic. The behaviour under test: a FAQ question stays with
its answer, a lead-in ending with a colon stays with its list, small neighbours merge up to about
3,000 characters but never inside a code fence, anything over 7,600 characters is split at table-row
or line boundaries (never silently cut), and a candidate with more than one chunk numbers them with
a trailing ``Part: <i> of <n>`` line. No line is lost or reordered.
"""

from __future__ import annotations

import re

import pytest

from foss_mcp.indexing.doc_candidates import DocCandidate
from foss_mcp.indexing.doc_chunking import (
    MAX_DOC_CHUNK_CHARS,
    MIN_DOC_CHUNK_CHARS,
    TARGET_DOC_CHUNK_CHARS,
    chunk_doc_candidate,
)
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance, SourceKind

PROVENANCE = Provenance(repository="o/r", commit="0" * 40, path="page.md")
PART_RE = re.compile(r"Part: (\d+) of (\d+)")


def _chunks(body: str, *, origin: str = "content", title: str = "Some section") -> list[Chunk]:
    candidate = DocCandidate(title=title, body=body, origin=origin)
    return chunk_doc_candidate(
        candidate,
        source_kind=SourceKind.FURNISHED,
        content_type="doc",
        provenance=PROVENANCE,
        evidence_refs=("o/r@" + "0" * 40,),
    )


def _body_lines(chunk: Chunk) -> list[str]:
    """The chunk's lines without the trailing Part marker."""
    lines = chunk.text.split("\n")
    if PART_RE.fullmatch(lines[-1]):
        lines = lines[:-1]
    return lines


def _paragraph(label: str, length: int) -> str:
    text = f"{label} " + ("word " * (length // 5))
    return text[:length].rstrip() + "."


def test_the_constants_are_the_card_values() -> None:
    assert (MIN_DOC_CHUNK_CHARS, TARGET_DOC_CHUNK_CHARS, MAX_DOC_CHUNK_CHARS) == (400, 3000, 7600)


def test_a_faq_question_and_answer_are_one_chunk() -> None:
    question = "Does it need other runtime dependencies?"
    answer = "No. It has no other runtime dependencies."
    chunks = _chunks(f"FAQ: {question}\n\n{answer}", origin="faq", title=question)
    assert len(chunks) == 1
    assert chunks[0].text == f"FAQ: {question}\n\n{answer}"


def test_a_lead_in_ending_with_a_colon_joins_the_list_after_it() -> None:
    lead_in = _paragraph("Known issues and unsupported functionality are listed below", 500)
    lead_in = lead_in.rstrip(".") + ":"
    items = "\n".join(f"- item {index} " + "detail " * 60 for index in range(8))
    assert len(lead_in) >= MIN_DOC_CHUNK_CHARS
    chunks = _chunks(f"{lead_in}\n\n{items}")
    assert len(chunks) == 1
    assert chunks[0].text.startswith(lead_in)
    assert "- item 0 " in chunks[0].text
    assert "- item 7 " in chunks[0].text


def test_small_sections_merge_up_to_about_3000_characters() -> None:
    paragraphs = [_paragraph(f"Paragraph {index}", 500) for index in range(10)]
    chunks = _chunks("\n\n".join(paragraphs))
    assert len(chunks) == 2
    for chunk in chunks:
        assert len(_body_lines(chunk)) > 1
        assert len("\n".join(_body_lines(chunk))) <= TARGET_DOC_CHUNK_CHARS
    assert [chunk.text.count("Paragraph ") for chunk in chunks] == [5, 5]


def test_tiny_neighbours_merge_even_below_the_minimum() -> None:
    chunks = _chunks("one short line\n\nanother short line\n\nand a third")
    assert len(chunks) == 1
    assert chunks[0].text == "one short line\n\nanother short line\n\nand a third"


def test_a_code_fence_is_never_split_by_a_merge() -> None:
    filler = _paragraph("Preface", 2900)
    code = "\n".join(f"value_{index} = compute({index})" for index in range(25))
    body = (
        f"{filler}\n\n```python\n{code}\n\nprint('after a blank line')\n```\n\n{_paragraph('Epilogue', 600)}"
    )
    chunks = _chunks(body)
    assert len(chunks) >= 2
    for chunk in chunks:
        fences = [line for line in _body_lines(chunk) if line.strip().startswith("```")]
        assert len(fences) % 2 == 0, chunk.text
    fenced = next(chunk for chunk in chunks if "value_0" in chunk.text)
    assert "print('after a blank line')" in fenced.text


def _table(rows: int) -> list[str]:
    return ["| Class | Description |", "| --- | --- |"] + [
        f"| `Class{index:04d}` | Description of class number {index} with filler text |"
        for index in range(rows)
    ]


def test_a_long_pipe_table_splits_at_row_boundaries_and_repeats_its_header() -> None:
    table = _table(400)
    chunks = _chunks("\n".join(table))
    assert len(chunks) > 1
    for position, chunk in enumerate(chunks):
        lines = _body_lines(chunk)
        assert len("\n".join(lines)) <= MAX_DOC_CHUNK_CHARS
        assert all(line.startswith("|") for line in lines), "a row was cut in the middle"
        if position:
            assert lines[:2] == table[:2]
    rows = [line for chunk in chunks for line in _body_lines(chunk) if line.startswith("| `Class")]
    assert rows == table[2:]


def test_a_long_bullet_list_splits_at_line_boundaries() -> None:
    bullets = [f"- bullet number {index} " + "text " * 19 + "text" for index in range(300)]
    chunks = _chunks("\n".join(bullets))
    assert len(chunks) > 1
    for chunk in chunks:
        lines = _body_lines(chunk)
        assert len("\n".join(lines)) <= MAX_DOC_CHUNK_CHARS
        assert all(line in bullets for line in lines), "a bullet was cut or rewritten"
    assert [line for chunk in chunks for line in _body_lines(chunk)] == bullets


def test_a_single_line_over_the_limit_is_cut_at_the_limit() -> None:
    line = "x" * (MAX_DOC_CHUNK_CHARS * 2 + 100)
    chunks = _chunks(line)
    assert [len("\n".join(_body_lines(chunk))) for chunk in chunks] == [
        MAX_DOC_CHUNK_CHARS,
        MAX_DOC_CHUNK_CHARS,
        100,
    ]
    assert "".join("".join(_body_lines(chunk)) for chunk in chunks) == line


def test_part_markers_number_the_chunks_in_order_and_a_single_chunk_has_none() -> None:
    single = _chunks("just one short paragraph")
    assert len(single) == 1
    assert "Part:" not in single[0].text

    chunks = _chunks("\n".join(f"- bullet {index} " + "text " * 20 for index in range(300)))
    assert len(chunks) > 1
    for position, chunk in enumerate(chunks, start=1):
        assert chunk.text.endswith(f"\nPart: {position} of {len(chunks)}")
        assert len(PART_RE.findall(chunk.text)) == 1


def _mixed_body() -> str:
    parts = [
        _paragraph("Opening paragraph", 450),
        "Highlights:",
        "\n".join(f"- highlight {index} " + "x" * 40 for index in range(40)),
        '```go\nfunc main() {\n\tfmt.Println("hi")\n\n\tfmt.Println("bye")\n}\n```',
        _paragraph("Middle paragraph", 2800),
        _paragraph("Another paragraph", 3500),
        "\n".join(f"- later bullet {index} " + "y" * 60 for index in range(120)),
        "closing line",
    ]
    return "\n\n".join(parts)


def test_no_line_is_lost_or_reordered() -> None:
    body = _mixed_body()
    chunks = _chunks(body)
    assert len(chunks) > 1
    original = [line.strip() for line in body.split("\n") if line.strip()]
    produced = [line.strip() for chunk in chunks for line in _body_lines(chunk) if line.strip()]
    assert produced == original


def test_chunking_is_deterministic() -> None:
    body = _mixed_body() + "\n\n" + "\n".join(_table(300))
    first = _chunks(body)
    second = _chunks(body)
    assert [chunk.text for chunk in first] == [chunk.text for chunk in second]
    assert first == second


@pytest.mark.parametrize(
    "body",
    [
        _mixed_body(),
        "\n".join(_table(1000)),
        "z" * 50_000,
        "\n\n".join(_paragraph(f"P{index}", 7000) for index in range(6)),
    ],
    ids=["mixed", "table", "one-long-line", "big-paragraphs"],
)
def test_every_chunk_is_at_most_max_chars(body: str) -> None:
    chunks = _chunks(body)
    assert chunks
    for chunk in chunks:
        assert len("\n".join(_body_lines(chunk))) <= MAX_DOC_CHUNK_CHARS


def test_the_chunks_keep_the_document_arguments() -> None:
    chunks = _chunks(_mixed_body())
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.source_kind == "furnished"
        assert chunk.content_type == "doc"
        assert chunk.provenance == PROVENANCE
        assert chunk.evidence_refs == ("o/r@" + "0" * 40,)


def test_an_empty_body_yields_no_chunk() -> None:
    assert _chunks("   \n\n  ") == []


def test_a_final_tiny_buffer_joins_the_previous_chunk() -> None:
    big = _paragraph("Big", 3000)
    chunks = _chunks(f"{big}\n\n{_paragraph('Second', 3000)}\n\ntail")
    assert len(chunks) == 2
    assert chunks[1].text.endswith("tail\nPart: 2 of 2")
