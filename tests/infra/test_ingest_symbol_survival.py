"""TC-384: ingestion must never silently delete a real, self-extracted API symbol.

Before this card, ``infra.ingest --api-surface`` ran citation validation over EVERY loaded
chunk, so a symbol chunk whose upstream docstring held an inline code span that did not
resolve (or resolved ambiguously) was excluded as a whole - 142 real symbols across eight
pilots (words/python ``SaveFormat`` and ``LoadFormat``, jmap/python ``Email`` and
``MethodError`` among them) - and the only trace was a per-anchor "excluded:" line.

``select_publishable_chunks`` keeps every API-symbol chunk (its text IS the extraction, its
docstring is upstream's own words) and still validates and filters every other chunk. These
tests pin that on the eight committed real fixtures, offline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.ingest as ingest
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
PILOTS = (
    "jmap_python",
    "words_python",
    "pdf_python",
    "pdf_typescript",
    "html_python",
    "jmap_rust",
    "jmap_typescript",
    "page_python",
)
EMBEDDING_PROVIDER_SPEC = "tests.indexing.test_index_writers:DeterministicEmbeddingProvider"
SCOPE = "pdf::net::self_extracted"

_PROVENANCE = Provenance(
    repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit="b717287" * 5, path="api_surface.json"
)


def _load_fixture(pilot: str) -> dict:
    return json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


def _symbol_fqns(chunks: list[Chunk]) -> set[str]:
    """FQNs of the API-symbol chunks, by the card's rule, independent of the code under test."""
    fqns = set()
    for chunk in chunks:
        fqn = extract_fqn(chunk.text)
        if chunk.content_type == "api_surface" and fqn and not fqn.startswith(("Example: ", "Doc: ")):
            fqns.add(fqn)
    return fqns


def _synthetic_chunk(section_title: str, text: str, content_type: str = "api_surface") -> Chunk:
    return Chunk(
        section_title=section_title,
        text=text,
        source_kind="self_extracted",
        content_type=content_type,
        provenance=_PROVENANCE,
        trust_tier="highest",
        evidence_refs=(f"{_PROVENANCE.repository}@{_PROVENANCE.commit}:api_surface.json",),
    )


def _small_fixture() -> dict:
    return {
        "types": [
            {
                "name": "Document",
                "kind": "class_declaration",
                "methods": [{"name": "Open"}],
                "properties": [{"name": "PageCount"}],
            }
        ],
        "type_count": 1,
        "truncated": False,
    }


BAD_ANCHOR_SYMBOL = "FQN: Document\nKind: class\nSee `Document.Nope` for details."
GOOD_SYMBOL = "FQN: Document\nKind: class\nThe root object."


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_api_symbol_chunk_of_a_real_fixture_is_published(pilot: str) -> None:
    fixture = _load_fixture(pilot)
    chunks = build_chunks_from_api_surface(fixture, title=pilot)
    symbols_in = _symbol_fqns(chunks)
    assert symbols_in, f"{pilot}: the fixture produced no API-symbol chunk at all"

    published, report = ingest.select_publishable_chunks(chunks, fixture)

    assert _symbol_fqns(published) == symbols_in
    assert report.api_symbols_kept == report.api_symbols
    assert report.total == len(chunks)
    assert report.published == len(published)
    assert report.published + report.excluded_other == report.total


@pytest.mark.parametrize(
    ("pilot", "names"),
    [
        ("words_python", ("SaveFormat", "LoadFormat")),
        ("jmap_python", ("Email", "MethodError")),
    ],
)
def test_named_symbols_that_used_to_be_dropped_are_published(pilot: str, names: tuple[str, ...]) -> None:
    fixture = _load_fixture(pilot)
    chunks = build_chunks_from_api_surface(fixture, title=pilot)

    published, _ = ingest.select_publishable_chunks(chunks, fixture)

    final_segments = {fqn.replace("::", ".").split(".")[-1] for fqn in _symbol_fqns(published)}
    for name in names:
        assert name in final_segments


def test_published_chunks_keep_their_input_order() -> None:
    fixture = _small_fixture()
    chunks = [
        _synthetic_chunk("a", GOOD_SYMBOL),
        _synthetic_chunk("b", "`Document.Nope` does not exist.", content_type="api_surface"),
        _synthetic_chunk("c", BAD_ANCHOR_SYMBOL),
        _synthetic_chunk("d", "Document is the root object of a PDF file."),
    ]

    published, _ = ingest.select_publishable_chunks(chunks, fixture)

    assert [chunk.section_title for chunk in published] == ["a", "c", "d"]


def test_a_non_symbol_chunk_with_an_unresolvable_anchor_is_still_excluded(capsys) -> None:
    chunks = [
        _synthetic_chunk("Document.Open", "`Document.Open` opens a file."),
        _synthetic_chunk("Document.Nope", "`Document.Nope` does not exist."),
    ]

    published, report = ingest.select_publishable_chunks(chunks, _small_fixture())

    assert [chunk.section_title for chunk in published] == ["Document.Open"]
    assert report.excluded_other == 1
    assert report.api_symbols == 0
    out = capsys.readouterr().out
    assert "excluded: 'Document.Nope' anchor 'Document.Nope' -> unsupported (match_count=0)" in out
    assert "kept api symbol" not in out


def test_a_numeric_claim_contradicting_the_fixture_is_still_excluded() -> None:
    chunks = [
        _synthetic_chunk("Count", "The API exposes 999 classes."),
        _synthetic_chunk("Plain", "Document is the root object of a PDF file."),
    ]

    published, report = ingest.select_publishable_chunks(chunks, _small_fixture())

    assert [chunk.section_title for chunk in published] == ["Plain"]
    assert report.excluded_other == 1


@pytest.mark.parametrize("prefix", ["Doc: ", "Example: "])
def test_doc_and_example_pseudo_fqn_chunks_keep_todays_treatment(prefix: str, capsys) -> None:
    unresolved = _synthetic_chunk("Page", f"FQN: {prefix}Intro\nSee `Document.Nope` for details.")
    resolved = _synthetic_chunk("Page2", f"FQN: {prefix}Intro\nSee `Document.Open` for details.")

    published, report = ingest.select_publishable_chunks([unresolved, resolved], _small_fixture())

    assert [chunk.section_title for chunk in published] == ["Page2"]
    assert report.api_symbols == 0
    assert report.excluded_other == 1
    out = capsys.readouterr().out
    assert "excluded: 'Page' anchor 'Document.Nope'" in out
    assert "kept api symbol" not in out


def test_an_api_surface_chunk_without_an_fqn_line_is_not_a_symbol_chunk() -> None:
    chunk = _synthetic_chunk("Document.Nope", "`Document.Nope` does not exist.")

    published, report = ingest.select_publishable_chunks([chunk], _small_fixture())

    assert published == []
    assert report.api_symbols == 0
    assert report.excluded_other == 1


def test_a_symbol_chunk_that_is_not_api_surface_content_is_validated_as_usual() -> None:
    chunk = _synthetic_chunk("Document", BAD_ANCHOR_SYMBOL, content_type="developer_guide")

    published, report = ingest.select_publishable_chunks([chunk], _small_fixture())

    assert published == []
    assert report.excluded_other == 1


def test_summary_line_states_what_was_published_and_excluded() -> None:
    chunks = [
        _synthetic_chunk("a", GOOD_SYMBOL),
        _synthetic_chunk("c", BAD_ANCHOR_SYMBOL),
        _synthetic_chunk("b", "`Document.Nope` does not exist."),
        _synthetic_chunk("d", "Document is the root object of a PDF file."),
    ]

    _, report = ingest.select_publishable_chunks(chunks, _small_fixture())

    assert report.summary_line() == (
        "citation: published 3 of 4 chunks; api symbols kept 2 of 2; excluded 1 other chunks"
    )


def _chunk_entry(section_title: str, text: str) -> dict:
    return {
        "section_title": section_title,
        "text": text,
        "source_kind": "self_extracted",
        "content_type": "api_surface",
        "provenance": {
            "repository": _PROVENANCE.repository,
            "commit": _PROVENANCE.commit,
            "path": _PROVENANCE.path,
        },
        "trust_tier": "highest",
        "evidence_refs": [f"{_PROVENANCE.repository}@{_PROVENANCE.commit}:api_surface.json"],
    }


def _run_main(tmp_path: Path, monkeypatch, entries: list[dict], *, with_api_surface: bool = True):
    chunks_path = tmp_path / "chunks.json"
    api_surface_path = tmp_path / "api_surface.json"
    manifest_store_path = tmp_path / "manifests"
    chunks_path.write_text(json.dumps({"chunks": entries}), encoding="utf-8")
    api_surface_path.write_text(json.dumps(_small_fixture()), encoding="utf-8")
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


def test_main_publishes_a_symbol_whose_docstring_has_a_bad_anchor_and_says_so(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    published_texts = _run_main(
        tmp_path,
        monkeypatch,
        [
            _chunk_entry("Document", BAD_ANCHOR_SYMBOL),
            _chunk_entry("Document.Nope", "`Document.Nope` does not exist."),
            _chunk_entry("Plain", "Document is the root object of a PDF file."),
        ],
    )

    assert published_texts == {BAD_ANCHOR_SYMBOL, "Document is the root object of a PDF file."}
    out = capsys.readouterr().out
    assert "kept api symbol: 'Document' anchor 'Document.Nope' -> unsupported (match_count=0)" in out
    assert "excluded: 'Document.Nope' anchor 'Document.Nope' -> unsupported (match_count=0)" in out
    assert "citation: published 2 of 3 chunks; api symbols kept 1 of 1; excluded 1 other chunks" in out


def test_main_prints_the_summary_even_when_nothing_is_dropped(tmp_path: Path, monkeypatch, capsys) -> None:
    _run_main(tmp_path, monkeypatch, [_chunk_entry("Document", GOOD_SYMBOL)])

    out = capsys.readouterr().out
    assert "citation: published 1 of 1 chunks; api symbols kept 1 of 1; excluded 0 other chunks" in out


def test_main_without_api_surface_prints_no_citation_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    published_texts = _run_main(
        tmp_path,
        monkeypatch,
        [_chunk_entry("Document", BAD_ANCHOR_SYMBOL)],
        with_api_surface=False,
    )

    assert published_texts == {BAD_ANCHOR_SYMBOL}
    assert "citation:" not in capsys.readouterr().out
