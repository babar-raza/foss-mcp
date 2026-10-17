"""TC-517 (REQ-G3-008): merging sections must not shrink the documentation that is actually published.

For every shipped pilot with a committed furnished page and a committed ``api_surface.json`` this
compares the documentation characters PUBLISHED with the NEW chunker against the OLD one-chunk-per-section
behaviour (``_old_chunks``, copied from ``infra/build_chunks.py`` as it was before TC-517).

"Published" means what ``infra/ingest.py`` publishes: ``select_publishable_chunks(chunks, fixture)``
(TC-425), which keeps furnished doc chunks unless a stated count contradicts the extraction and un-backticks
an unresolved anchor. The strict ``citable_chunks`` pass drops a whole chunk for one unsupported anchor, but it
is not the publish gate, so it is measured here for information only (printed, no floor). Offline and
deterministic.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
from functools import cache
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
import infra.ingest as ingest
from foss_mcp.indexing.doc_candidates import extract_doc_sections
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.citation import (
    citable_chunks,
    known_counts_from_fixture,
    symbol_index_from_api_surface,
    validate_document,
)
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INGEST_SOURCE = Path(__file__).resolve().parents[2] / "infra" / "ingest.py"
PILOTS = sorted(
    path.name
    for path in (FIXTURES / "furnished").iterdir()
    if path.is_dir()
    and (path / "pages" / "_index.md").is_file()
    and (FIXTURES / path.name / "api_surface.json").is_file()
)
SURVIVAL_FLOOR = 0.85
DOC_PREFIX = "FQN: Doc: "


def _args(page_path: Path) -> argparse.Namespace:
    return argparse.Namespace(library_repository="o/r", library_commit="0" * 40, furnished_page=page_path)


def _old_chunks(page: dict, args: argparse.Namespace) -> list[Chunk]:
    """The documentation chunking as it was BEFORE TC-517 (one chunk per ``chunk_document`` section),
    copied from ``infra/build_chunks.py`` ``_build_doc_chunks``. It is the baseline of the survival floor."""
    doc_chunks: list[Chunk] = []
    for candidate in extract_doc_sections(page):
        doc = make_document(
            source_kind=SourceKind.FURNISHED,
            content_type="doc",
            provenance=Provenance(
                repository=args.library_repository,
                commit=args.library_commit,
                path=str(args.furnished_page),
            ),
            evidence_refs=(f"{args.library_repository}@{args.library_commit}",),
            title=candidate.title,
            body=candidate.body,
        )
        for chunk in chunk_document(doc):
            doc_chunks.append(dataclasses.replace(chunk, text=f"{DOC_PREFIX}{candidate.title}\n{chunk.text}"))
    return doc_chunks


@dataclasses.dataclass(frozen=True)
class Survival:
    old_chars: int
    new_chars: int
    old_content: int
    new_content: int
    content_candidates: int

    @property
    def ratio(self) -> float:
        return self.new_chars / self.old_chars if self.old_chars else 1.0


def _title(chunk: Chunk) -> str:
    return chunk.text.split("\n", 1)[0][len(DOC_PREFIX) :]


def _load(pilot: str) -> tuple[dict, argparse.Namespace, dict, set[str]]:
    page_path = FIXTURES / "furnished" / pilot / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(page_path)
    fixture = json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))
    content_titles = {c.title for c in extract_doc_sections(page) if c.origin == "content"}
    return page, _args(page_path), fixture, content_titles


@cache
def measure_published(pilot: str) -> Survival:
    """Characters published by ``select_publishable_chunks``, exactly as ``infra/ingest.py`` calls it."""
    page, args, fixture, content_titles = _load(pilot)
    with contextlib.redirect_stdout(io.StringIO()):
        new, _ = ingest.select_publishable_chunks(list(build_chunks._build_doc_chunks(page, args)), fixture)
        old, _ = ingest.select_publishable_chunks(list(_old_chunks(page, args)), fixture)
    return Survival(
        old_chars=sum(len(chunk.text) for chunk in old),
        new_chars=sum(len(chunk.text) for chunk in new),
        old_content=sum(1 for chunk in old if _title(chunk) in content_titles),
        new_content=sum(1 for chunk in new if _title(chunk) in content_titles),
        content_candidates=len(content_titles),
    )


@cache
def measure_strict(pilot: str) -> Survival:
    """Characters that survive the strict ``citable_chunks`` pass (informational only)."""
    page, args, fixture, content_titles = _load(pilot)
    symbol_index = symbol_index_from_api_surface(fixture["types"])
    known_counts = known_counts_from_fixture(fixture)

    def surviving(chunks: list[Chunk]) -> list[Chunk]:
        return citable_chunks(validate_document(chunks, symbol_index, known_counts))

    with contextlib.redirect_stdout(io.StringIO()):
        new = surviving(build_chunks._build_doc_chunks(page, args))
        old = surviving(_old_chunks(page, args))
    return Survival(
        old_chars=sum(len(chunk.text) for chunk in old),
        new_chars=sum(len(chunk.text) for chunk in new),
        old_content=sum(1 for chunk in old if _title(chunk) in content_titles),
        new_content=sum(1 for chunk in new if _title(chunk) in content_titles),
        content_candidates=len(content_titles),
    )


def test_the_survival_check_covers_every_pilot_with_a_fixture() -> None:
    # re-pinned by TC-551: 40 -> 32
    assert len(PILOTS) >= 32
    assert "pdf_net" in PILOTS


def test_infra_ingest_publishes_through_select_publishable_chunks() -> None:
    """The metric below is only meaningful while it is the production one."""
    source = INGEST_SOURCE.read_text(encoding="utf-8")
    assert "chunks, report = select_publishable_chunks(chunks, fixture)" in source


@pytest.mark.parametrize("pilot", PILOTS)
def test_merged_chunks_publish_at_least_85_percent_of_the_old_documentation(pilot: str) -> None:
    result = measure_published(pilot)
    assert result.new_chars >= SURVIVAL_FLOOR * result.old_chars, (
        pilot,
        result.old_chars,
        result.new_chars,
        f"{result.ratio:.3f}",
    )


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_content_origin_still_publishes_a_chunk(pilot: str) -> None:
    result = measure_published(pilot)
    assert result.content_candidates, pilot
    assert result.new_content >= 1, (pilot, result)


def test_strict_citation_survival_table_is_printed_for_information() -> None:
    """Prints the strict ``citable_chunks`` survival per pilot. No floor: that pass is not the publish gate."""
    for pilot in PILOTS:
        result = measure_strict(pilot)
        print(
            f"strict {pilot}: old={result.old_chars} new={result.new_chars} ratio={result.ratio:.3f} "
            f"content old/new={result.old_content}/{result.new_content}"
        )
    # re-pinned by TC-551: 40 -> 32
    assert len(PILOTS) >= 32
