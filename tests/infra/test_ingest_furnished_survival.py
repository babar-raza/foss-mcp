"""TC-425: furnished documentation must survive ingestion.

Before this card ``infra.ingest --api-surface`` ran citation validation over every non-symbol
chunk and dropped a whole chunk for one unresolved inline anchor or one uncorroborated per-class
count, so 90-98% of every pilot's authored documentation (Installation and Quick Start among it)
never reached the published generation. ``select_publishable_chunks`` now keeps furnished "doc"
and "example" chunks unless a stated count CONTRADICTS the extraction, resolves qualified
anchors, stops treating package ids / target frameworks / versions / file names as symbols, and
un-backticks an anchor that still does not resolve instead of deleting the prose.

The survival tests run offline on the REAL committed furnished pages and the REAL committed
api_surface fixtures of eight pilots, building the doc chunks exactly as infra/build_chunks.py
does. Nothing here is timed.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from functools import cache
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
import infra.ingest as ingest
from foss_mcp.extraction.claim_id_bridge import SymbolIndex
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.citation import (
    classify_anchor,
    namespaces_from_api_surface,
    neutralise_unresolved_anchors,
    qualified_symbol_index_from_api_surface,
)
from foss_mcp.normalization.document_schema import Provenance

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
PILOTS = (
    "pdf_net",
    "pdf_java",
    "pdf_python",
    "slides_python",
    "pdf_go",
    "3d_python",
    "html_python",
    "words_net",
)
EMBEDDING_PROVIDER_SPEC = "tests.indexing.test_index_writers:DeterministicEmbeddingProvider"
SCOPE = "pdf::net::self_extracted"
NOTE_LINE = (
    "Note: counts stated in this section come from the upstream documentation and were not "
    "corroborated against the extracted API."
)

_PROVENANCE = Provenance(
    repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5, path="_index.md"
)


def _strip(text: str) -> str:
    return text.replace("`", "")


@cache
def _real_doc_chunks(pilot: str) -> tuple[Chunk, ...]:
    """The doc chunks infra/build_chunks.py builds from the pilot's real furnished page."""
    page_path = FIXTURES / "furnished" / pilot / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(page_path)
    args = argparse.Namespace(
        library_repository="example/library", library_commit="c" * 40, furnished_page=page_path
    )
    with contextlib.redirect_stdout(io.StringIO()):
        return tuple(build_chunks._build_doc_chunks(page, args))


@cache
def _real_fixture(pilot: str) -> dict:
    return json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


@cache
def _real_selection(pilot: str) -> tuple[tuple[Chunk, ...], ingest.SelectionReport]:
    with contextlib.redirect_stdout(io.StringIO()):
        published, report = ingest.select_publishable_chunks(
            list(_real_doc_chunks(pilot)), _real_fixture(pilot)
        )
    return tuple(published), report


def _is_key_chunk(chunk: Chunk) -> bool:
    first_line = chunk.text.split("\n", 1)[0]
    return chunk.section_title in ("Installation", "Quick Start") or first_line == (
        "FQN: Doc: Scope and Limitations"
    )


def _published_counterparts(
    inputs: tuple[Chunk, ...], published: tuple[Chunk, ...]
) -> list[tuple[Chunk, Chunk | None]]:
    """Each input chunk with the published chunk standing for it (order is preserved)."""
    pairs: list[tuple[Chunk, Chunk | None]] = []
    cursor = 0
    for chunk in inputs:
        match = None
        for position in range(cursor, len(published)):
            candidate = published[position]
            if candidate.section_title == chunk.section_title and _strip(candidate.text).startswith(
                _strip(chunk.text)
            ):
                match = candidate
                cursor = position + 1
                break
        pairs.append((chunk, match))
    return pairs


# --- (1) the share of furnished text that survives ---------------------------------------------


@pytest.mark.parametrize("pilot", PILOTS)
def test_at_least_ninety_percent_of_the_furnished_text_is_published(pilot: str) -> None:
    inputs = _real_doc_chunks(pilot)
    published, report = _real_selection(pilot)
    assert inputs, f"{pilot}: the furnished page produced no doc chunk"
    assert all(chunk.content_type == "doc" for chunk in inputs)

    characters_in = sum(len(_strip(chunk.text)) for chunk in inputs)
    characters_out = sum(len(_strip(chunk.text)) for chunk in published)

    assert characters_out >= 0.9 * characters_in, (
        f"{pilot}: only {characters_out / characters_in:.1%} of the furnished text survived"
    )
    assert report.furnished_total == len(inputs)
    assert report.furnished_published == len(published)


# --- (2) Installation, Quick Start and Scope and Limitations are published verbatim ------------


@pytest.mark.parametrize("pilot", PILOTS)
def test_installation_quick_start_and_scope_chunks_are_published_unchanged_but_for_backticks(
    pilot: str,
) -> None:
    inputs = _real_doc_chunks(pilot)
    published, _ = _real_selection(pilot)
    key_inputs = [chunk for chunk in inputs if _is_key_chunk(chunk)]
    assert key_inputs, f"{pilot}: no Installation / Quick Start / Scope and Limitations chunk at all"

    titles = {chunk.section_title for chunk in key_inputs}
    first_lines = {chunk.text.split("\n", 1)[0] for chunk in key_inputs}
    assert "Installation" in titles
    if pilot != "words_net":
        assert "Quick Start" in titles
        assert "FQN: Doc: Scope and Limitations" in first_lines
    else:
        assert "Quick Start" not in titles
        assert "FQN: Doc: Scope and Limitations" not in first_lines

    pairs = _published_counterparts(inputs, published)
    for chunk, counterpart in pairs:
        if not _is_key_chunk(chunk):
            continue
        label = f"{pilot}: {chunk.section_title!r} {chunk.text.splitlines()[0]!r}"
        assert counterpart is not None, f"{label} was not published"
        assert _strip(counterpart.text) == _strip(chunk.text), f"{label} was altered beyond backticks"


# --- (3) pdf_net specifics ---------------------------------------------------------------------


def _pdf_net_published_text() -> str:
    published, _ = _real_selection("pdf_net")
    return "\n".join(chunk.text for chunk in published)


@pytest.mark.parametrize("anchor", ["Aspose.Pdf.Document", "net8.0", "Aspose.PDF.FOSS"])
def test_pdf_net_resolvable_and_non_symbol_anchors_keep_their_backticks(anchor: str) -> None:
    written = f"`{anchor}`"
    assert any(written in chunk.text for chunk in _real_doc_chunks("pdf_net")), (
        f"{written} is not in the furnished page any more; this test needs a new anchor"
    )
    assert written in _pdf_net_published_text()


def test_pdf_net_core_api_chunk_is_published() -> None:
    inputs = [chunk for chunk in _real_doc_chunks("pdf_net") if chunk.section_title == "Core API"]
    published = [chunk for chunk in _real_selection("pdf_net")[0] if chunk.section_title == "Core API"]
    assert len(inputs) == 1
    assert len(inputs[0].text) > 70000
    assert len(published) == 1
    assert len(_strip(published[0].text)) == len(_strip(inputs[0].text))


# --- classify_anchor ---------------------------------------------------------------------------


def _types() -> list[dict]:
    return [
        {
            "name": "Document",
            "kind": "class_declaration",
            "class_import": "Aspose.Pdf.Document",
            "canonical_namespace": "Aspose.Pdf",
            "methods": [{"name": "Save"}, {"name": "Save"}, {"name": "Open"}],
            "properties": [{"name": "PageCount"}],
        },
        {
            "name": "TextFragment",
            "kind": "class_declaration",
            "class_import": "Aspose.Pdf.Text.TextFragment",
            "canonical_namespace": "Aspose.Pdf.Text",
            "methods": [{"name": "Save"}],
            "properties": [],
        },
        {
            "name": "SaveFormat",
            "kind": "enum_declaration",
            "class_import": "Aspose.Pdf.SaveFormat",
            "canonical_namespace": "Aspose.Pdf",
            "methods": [],
            "properties": [],
            "enum_members": [{"name": "Pptx"}],
        },
        {
            "name": "Element",
            "kind": "class_declaration",
            "class_package": "org.aspose.pdf.elements",
            "methods": [{"name": "setText"}],
            "properties": [],
        },
    ]


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        ("Document", "symbol"),
        ("Document.Save", "symbol"),
        ("Aspose.Pdf.Document", "symbol"),
        ("Aspose.Pdf.Document.Save", "symbol"),
        ("Aspose.Pdf.Document.PageCount", "symbol"),
        ("SaveFormat.Pptx", "symbol"),
        ("Aspose.Pdf.SaveFormat.Pptx", "symbol"),
        ("Element.setText", "symbol"),
        ("org.aspose.pdf.elements.Element.setText", "symbol"),
        ("Aspose.Pdf.Text", "symbol"),
        ("Aspose.Pdf", "symbol"),
        ("Aspose", "symbol"),
        ("org.aspose", "symbol"),
        ("net8.0", "non_symbol"),
        ("net10.0", "non_symbol"),
        ("netstandard2.0", "non_symbol"),
        ("netcoreapp3.1", "non_symbol"),
        ("v1.2.3", "non_symbol"),
        ("Aspose.PDF.FOSS", "non_symbol"),
        ("Aspose.Pdf.foss", "non_symbol"),
        ("Program.cs", "non_symbol"),
        ("Aspose.Pdf.Foss.csproj", "non_symbol"),
        ("pyproject.toml", "non_symbol"),
        ("main.py", "non_symbol"),
        ("SaveFormat.PPTX", "unresolved"),
        ("Document.Nope", "unresolved"),
        ("Aspose.Pdf.Nope", "unresolved"),
        ("NoSuchThing", "unresolved"),
        ("Aspose.Pdf.Documentation", "unresolved"),
    ],
)
def test_classify_anchor(anchor: str, expected: str) -> None:
    index = qualified_symbol_index_from_api_surface(_types())
    namespaces = namespaces_from_api_surface(_types())
    assert classify_anchor(anchor, index, namespaces) == expected


def test_a_bare_member_name_is_a_symbol_only_when_it_is_globally_unambiguous() -> None:
    index = SymbolIndex.from_anchors(["Document.Open", "Document.Save", "TextFragment.Save"])
    assert classify_anchor("Open", index, ()) == "symbol"
    assert classify_anchor("Save", index, ()) == "unresolved"
    assert classify_anchor("Close", index, ()) == "unresolved"


def test_qualified_index_registers_each_anchor_once_per_type_and_adds_namespaces() -> None:
    index = qualified_symbol_index_from_api_surface(_types())
    assert len(index.declarations_for("Document.Save")) == 1, "overloads of one member are one symbol"
    assert len(index.declarations_for("Aspose.Pdf.Document.Save")) == 1
    assert len(index.declarations_for("Aspose.Pdf.Document")) == 1
    assert namespaces_from_api_surface(_types()) == {
        "Aspose.Pdf",
        "Aspose.Pdf.Text",
        "org.aspose.pdf.elements",
    }


# --- neutralise_unresolved_anchors -------------------------------------------------------------


def _neutralise(text: str) -> tuple[str, int]:
    return neutralise_unresolved_anchors(
        text, qualified_symbol_index_from_api_surface(_types()), namespaces_from_api_surface(_types())
    )


def test_neutralise_leaves_a_span_inside_a_code_fence_untouched() -> None:
    text = "Intro\n```python\ndoc = `Nope.Missing`\nadd_table()\n```\nAfter `Nope.Missing` here."
    result, count = _neutralise(text)
    assert result == "Intro\n```python\ndoc = `Nope.Missing`\nadd_table()\n```\nAfter Nope.Missing here."
    assert count == 1


def test_neutralise_strips_only_the_backticks_of_a_call_span() -> None:
    result, count = _neutralise("Use `add_table()` and `Document.Save()` to build it.")
    assert result == "Use add_table() and `Document.Save()` to build it."
    assert count == 1


def test_neutralise_keeps_symbol_and_non_symbol_spans_and_counts_the_rest() -> None:
    text = "`Aspose.Pdf.Document` on `net8.0` via `Aspose.PDF.FOSS`; `Gone.One`, `Gone.Two`, `x = 1`."
    result, count = _neutralise(text)
    assert result == "`Aspose.Pdf.Document` on `net8.0` via `Aspose.PDF.FOSS`; Gone.One, Gone.Two, `x = 1`."
    assert count == 2


def test_neutralise_returns_the_text_unchanged_when_nothing_is_unresolved() -> None:
    text = "Plain prose with `Document.Open` only."
    assert _neutralise(text) == (text, 0)


# --- synthetic furnished chunks through select_publishable_chunks ------------------------------


def _fixture() -> dict:
    return {"types": _types(), "type_count": 4, "truncated": False}


def _chunk(section_title: str, text: str, content_type: str = "doc") -> Chunk:
    return Chunk(
        section_title=section_title,
        text=text,
        source_kind="furnished",
        content_type=content_type,
        provenance=_PROVENANCE,
        trust_tier="high",
        evidence_refs=(f"{_PROVENANCE.repository}@{_PROVENANCE.commit}",),
    )


def test_a_contradicted_count_excludes_the_furnished_chunk_and_prints_why(capsys) -> None:
    chunks = [
        _chunk("Keep", "FQN: Doc: Keep\nA plain section."),
        _chunk("Overview", "FQN: Doc: Overview\nThe API exposes 999 classes."),
        _chunk("Also keep", "FQN: Doc: Also keep\nAnother plain section."),
    ]

    published, report = ingest.select_publishable_chunks(chunks, _fixture())

    assert [chunk.section_title for chunk in published] == ["Keep", "Also keep"]
    out = capsys.readouterr().out
    assert "excluded: Overview count 999 classes -> claims 999 classes; raw knowledge counts 3" in out
    assert (report.furnished_total, report.furnished_published, report.furnished_excluded) == (3, 2, 1)


def test_an_uncorroborated_count_is_published_with_the_note_line(capsys) -> None:
    text = "FQN: Doc: Classes\nDocument (4 properties, 2 methods)."
    published, report = ingest.select_publishable_chunks([_chunk("Classes", text)], _fixture())

    assert [chunk.text for chunk in published] == [f"{text}\n{NOTE_LINE}"]
    assert report.counts_qualified == 1
    assert report.furnished_published == 1
    assert "excluded" not in capsys.readouterr().out


def test_a_corroborated_count_is_published_without_a_note() -> None:
    text = "FQN: Doc: Types\nThe library has 4 types."
    published, report = ingest.select_publishable_chunks([_chunk("Types", text)], _fixture())

    assert [chunk.text for chunk in published] == [text]
    assert report.counts_qualified == 0


def test_a_doc_chunk_with_only_unresolved_anchors_is_published_never_excluded() -> None:
    text = "FQN: Doc: Notes\nSee `Nope.One` and `Nope.Two` and `Aspose.Pdf.Document`."
    published, report = ingest.select_publishable_chunks([_chunk("Notes", text)], _fixture())

    assert [chunk.text for chunk in published] == [
        "FQN: Doc: Notes\nSee Nope.One and Nope.Two and `Aspose.Pdf.Document`."
    ]
    assert report.anchors_neutralised == 2
    assert report.furnished_excluded == 0


def test_an_example_chunk_keeps_its_text_exactly() -> None:
    text = "FQN: Example: Open\nKind: verified_example\nCall `add_auto_shape()` then `Nope.Two`.\n\nExample:\nx = 1"
    published, report = ingest.select_publishable_chunks([_chunk("Open", text, "example")], _fixture())

    assert [chunk.text for chunk in published] == [text]
    assert report.anchors_neutralised == 0
    assert report.furnished_published == 1


def test_an_example_chunk_with_a_contradicted_count_is_excluded() -> None:
    text = "FQN: Example: Open\nKind: verified_example\nAll 999 classes are used.\n\nExample:\nx = 1"
    published, report = ingest.select_publishable_chunks([_chunk("Open", text, "example")], _fixture())

    assert published == []
    assert report.furnished_excluded == 1


def test_an_api_surface_chunk_with_a_doc_pseudo_fqn_and_a_bad_anchor_is_still_excluded(capsys) -> None:
    pseudo = _chunk("Pseudo", "FQN: Doc: Pseudo\nSee `Nope.One`.", content_type="api_surface")
    published, report = ingest.select_publishable_chunks([pseudo], _fixture())

    assert published == []
    assert report.excluded_other == 1
    assert (report.furnished_total, report.anchors_neutralised) == (0, 0)
    assert "excluded: 'Pseudo' anchor 'Nope.One' -> unsupported (match_count=0)" in capsys.readouterr().out


def test_chunk_order_is_preserved_across_furnished_and_other_chunks() -> None:
    chunks = [
        _chunk("doc one", "FQN: Doc: one\nFirst `Nope.One`."),
        _chunk("symbol", "FQN: Document\nKind: class\nThe root.", content_type="api_surface"),
        _chunk("doc two", "FQN: Doc: two\nSecond."),
    ]
    published, report = ingest.select_publishable_chunks(chunks, _fixture())

    assert [chunk.section_title for chunk in published] == ["doc one", "symbol", "doc two"]
    assert report.summary_line() == (
        "citation: published 3 of 3 chunks; api symbols kept 1 of 1; excluded 0 other chunks"
    )
    assert report.furnished_summary_line() == (
        "citation: furnished chunks published 2 of 2; anchors neutralised 1; "
        "counts qualified in 0 chunks; excluded 0 for contradicted counts"
    )


def test_selection_report_new_fields_default_to_zero() -> None:
    report = ingest.SelectionReport(total=1, published=1, api_symbols=0, api_symbols_kept=0, excluded_other=0)
    assert (
        report.furnished_total,
        report.furnished_published,
        report.anchors_neutralised,
        report.counts_qualified,
    ) == (0, 0, 0, 0)
    assert report.summary_line() == (
        "citation: published 1 of 1 chunks; api symbols kept 0 of 0; excluded 0 other chunks"
    )


# --- main end to end ---------------------------------------------------------------------------


def _entry(section_title: str, text: str, content_type: str) -> dict:
    return {
        "section_title": section_title,
        "text": text,
        "source_kind": "furnished",
        "content_type": content_type,
        "provenance": {
            "repository": _PROVENANCE.repository,
            "commit": _PROVENANCE.commit,
            "path": _PROVENANCE.path,
        },
        "trust_tier": "high",
        "evidence_refs": [f"{_PROVENANCE.repository}@{_PROVENANCE.commit}"],
    }


def _run_main(tmp_path: Path, monkeypatch, entries: list[dict], *, with_api_surface: bool) -> set[str]:
    chunks_path = tmp_path / "chunks.json"
    api_surface_path = tmp_path / "api_surface.json"
    manifest_store_path = tmp_path / "manifests"
    chunks_path.write_text(json.dumps({"chunks": entries}), encoding="utf-8")
    api_surface_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    argv = [
        "--family",
        "pdf",
        "--platform",
        "net",
        "--source-kind",
        "self_extracted",
        "--chunks",
        str(chunks_path),
        "--manifest-store",
        str(manifest_store_path),
        "--embedding-provider",
        EMBEDDING_PROVIDER_SPEC,
        "--min-symbols",
        "0",
        "--min-docs",
        "0",
    ]
    if with_api_surface:
        argv += ["--api-surface", str(api_surface_path)]
    monkeypatch.setattr(sys, "argv", ["ingest.py", *argv])
    ingest.main()
    store = GenerationManifestStore(manifest_store_path)
    generation_id = store.read_active(SCOPE)
    assert generation_id is not None
    manifest = store.read_generation(SCOPE, generation_id)
    return {doc["text"] for doc in manifest.payload["lexical_index"]["documents"].values()}


def test_main_prints_both_summary_lines_and_publishes_the_furnished_text(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    entries = [
        _entry("Notes", "FQN: Doc: Notes\nSee `Nope.One` and `Aspose.Pdf.Document`.", "doc"),
        _entry("Counts", "FQN: Doc: Counts\nThere are 4 properties.", "doc"),
        _entry("Bad", "FQN: Doc: Bad\nThere are 999 classes.", "doc"),
        _entry("Example", "FQN: Example: Open\nKind: verified_example\nExample:\nx = 1", "example"),
    ]

    published_texts = _run_main(tmp_path, monkeypatch, entries, with_api_surface=True)

    assert published_texts == {
        "FQN: Doc: Notes\nSee Nope.One and `Aspose.Pdf.Document`.",
        f"FQN: Doc: Counts\nThere are 4 properties.\n{NOTE_LINE}",
        "FQN: Example: Open\nKind: verified_example\nExample:\nx = 1",
    }
    out = capsys.readouterr().out
    assert "citation: published 3 of 4 chunks; api symbols kept 0 of 0; excluded 0 other chunks" in out
    assert (
        "citation: furnished chunks published 3 of 4; anchors neutralised 1; "
        "counts qualified in 1 chunks; excluded 1 for contradicted counts"
    ) in out
    assert "excluded: Bad count 999 classes -> " in out


def test_main_without_api_surface_changes_nothing_and_prints_no_citation_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    entries = [_entry("Bad", "FQN: Doc: Bad\nThere are 999 classes and `Nope.One`.", "doc")]

    published_texts = _run_main(tmp_path, monkeypatch, entries, with_api_surface=False)

    assert published_texts == {"FQN: Doc: Bad\nThere are 999 classes and `Nope.One`."}
    assert "citation:" not in capsys.readouterr().out
