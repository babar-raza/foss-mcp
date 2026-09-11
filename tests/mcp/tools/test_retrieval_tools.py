"""The three retrieval tools (lookup, search_docs, search_symbols) against real published
pdf/net generations.

CRITICAL, per the card: the reference system silently retried without an explicit filter, and
substituted the active generation for a requested one, whenever the strict search came up
empty. Every test here proves the opposite - a miss stays a miss, content_type genuinely
partitions, and a result never carries a scope other than the one passed in.

``Scope`` names the PRODUCT (family, platform) a deployment serves; self-extracted symbols and
furnished documentation are two independently-published generations for that same product
(``search_symbols.SOURCE_KIND`` / ``search_docs.SOURCE_KIND`` fix which), so one ``Scope`` per
product is published under both source kinds below and used for every tool.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.lookup import lookup
from foss_mcp.mcp.tools.search_docs import DocMatch, search_docs
from foss_mcp.mcp.tools.search_docs import Miss as DocsMiss
from foss_mcp.mcp.tools.search_symbols import Miss as SymbolsMiss
from foss_mcp.mcp.tools.search_symbols import SymbolMatch, search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

FIXTURE = Path(__file__).parents[2] / "fixtures" / "pdf_net" / "api_surface.json"

PDF_NET_SCOPE = Scope(family="pdf", platform="net")
CELLS_PYTHON_SCOPE = Scope(family="cells", platform="python")

USER_GUIDE_BODY = """## Getting Started

Install the package via NuGet with `dotnet add package Aspose.PDF.FOSS`. This getting started
guide covers opening your first PDF document and saving it back to disk.

## Advanced Document Manipulation

The Document class supports low-level manipulation of the page tree structure for advanced
developers building custom PDF pipelines and automated report generators.

## Troubleshooting

A common error is a FileNotFoundException when the input path is wrong. If you hit a known
issue with garbled text output, check the font embedding settings in your PDF viewer.

## Frequently Asked Questions

Is this library free to use in commercial projects? Yes, it is MIT licensed with no royalty
fees. This FAQ section addresses the most common licensing and support questions.
"""


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def _publish(store: GenerationManifestStore, scope: Scope, source_kind: str, chunks) -> str:
    lease = store.acquire_lease("::".join((scope.family, scope.platform, source_kind)), "worker-1", "pending")
    return publish_generation(
        store,
        family=scope.family,
        platform=scope.platform,
        source_kind=source_kind,
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )


def _publish_pdf_net_symbols(store: GenerationManifestStore) -> str:
    """A real, bounded slice of TC-011's actual pdf/net extraction."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sections = []
    for entry in fixture["types"][:20]:
        name = entry.get("class_import") or entry.get("name", "")
        methods = ", ".join(m.get("name", "") for m in entry.get("methods") or [])
        text = f"{name} is a {entry.get('kind', '')}."
        if methods:
            text += f" Methods: {methods}."
        sections.append(f"## {name}\n\n{text}")
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(
            repository=fixture["source_repository"], commit=fixture["source_commit"], path="api_surface.json"
        ),
        evidence_refs=(),
        title="pdf/net API surface",
        body="\n\n".join(sections),
    )
    return _publish(store, PDF_NET_SCOPE, "self_extracted", chunk_document(doc))


def _publish_pdf_net_docs(store: GenerationManifestStore) -> str:
    doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="product_page",
        provenance=Provenance(repository="Aspose/aspose.org", commit="x", path="content/pdf/net"),
        evidence_refs=(),
        title="pdf/net user guide",
        body=USER_GUIDE_BODY,
    )
    return _publish(store, PDF_NET_SCOPE, "furnished", chunk_document(doc))


def test_search_symbols_finds_a_real_published_symbol(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    real_name = fixture["types"][0]["name"]

    result = search_symbols(store, PDF_NET_SCOPE, real_name)

    assert isinstance(result, list) and result
    assert all(isinstance(match, SymbolMatch) for match in result)
    assert any(real_name in match.text for match in result)


def test_search_symbols_returns_an_explicit_miss_never_a_widened_result(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)

    result = search_symbols(store, PDF_NET_SCOPE, "ZzzTotallyNonexistentSymbolQuery")

    assert isinstance(result, SymbolsMiss)
    assert result.scope == PDF_NET_SCOPE


def test_search_docs_rejects_an_invalid_content_type(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        search_docs(store, PDF_NET_SCOPE, "install", "not_a_real_category")


def test_content_type_genuinely_partitions_results(tmp_path: Path) -> None:
    """The same term, filtered to the WRONG category, misses even though it would match if
    the filter were dropped - this is what proves genuine partitioning, not just "no match
    anywhere"."""
    store = _store(tmp_path)
    _publish_pdf_net_docs(store)

    hit = search_docs(store, PDF_NET_SCOPE, "document", "developer_guide")
    miss = search_docs(store, PDF_NET_SCOPE, "document", "faq")

    assert isinstance(hit, list) and hit
    assert all(match.content_type == "developer_guide" for match in hit)
    assert isinstance(miss, DocsMiss)
    assert miss.content_type == "faq"


def test_getting_started_and_troubleshooting_are_genuinely_different_partitions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish_pdf_net_docs(store)

    getting_started = search_docs(store, PDF_NET_SCOPE, "install", "getting_started")
    wrong_category = search_docs(store, PDF_NET_SCOPE, "install", "troubleshooting")

    assert isinstance(getting_started, list) and getting_started
    assert isinstance(wrong_category, DocsMiss)


def test_lookup_dispatches_to_search_symbols_for_a_symbol_query(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    real_name = fixture["types"][0]["name"]

    result = lookup(store, PDF_NET_SCOPE, real_name)

    assert isinstance(result, list) and all(isinstance(match, SymbolMatch) for match in result)


def test_lookup_dispatches_to_search_docs_when_content_type_is_given(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _publish_pdf_net_docs(store)

    result = lookup(store, PDF_NET_SCOPE, "install", content_type="getting_started")

    assert isinstance(result, list) and all(isinstance(match, DocMatch) for match in result)
    assert all(match.content_type == "getting_started" for match in result)


def test_lookup_falls_back_to_docs_when_nothing_resolves_as_a_symbol(tmp_path: Path) -> None:
    """No self_extracted generation is published in this test at all: the symbol search
    must miss cleanly (no generation for that source kind), and lookup falls through to
    docs - never inventing a symbol match out of documentation content.
    """
    store = _store(tmp_path)
    _publish_pdf_net_docs(store)

    result = lookup(store, PDF_NET_SCOPE, "licensed")

    assert isinstance(result, list) and result
    assert all(isinstance(match, DocMatch) for match in result)


def test_every_result_carries_the_scope_that_was_passed_in_never_another(tmp_path: Path) -> None:
    """The generation-isolation regression the card names: two different products, published
    separately, and a search in one must never surface or be attributed to the other."""
    store = _store(tmp_path)
    _publish_pdf_net_symbols(store)

    cells_generation_id = _publish(
        store,
        CELLS_PYTHON_SCOPE,
        "self_extracted",
        chunk_document(
            make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(
                    repository="aspose-cells-foss/Aspose.Cells-FOSS-for-Python", commit="y"
                ),
                evidence_refs=(),
                title="cells/python API surface",
                body="## Workbook\n\nWorkbook is a class. Methods: Save, Open.",
            )
        ),
    )

    pdf_result = search_symbols(store, PDF_NET_SCOPE, "Workbook")
    cells_result = search_symbols(store, CELLS_PYTHON_SCOPE, "Workbook")

    assert isinstance(pdf_result, SymbolsMiss), "Workbook belongs to a different product entirely"
    assert isinstance(cells_result, list) and cells_result
    assert all(match.scope == CELLS_PYTHON_SCOPE for match in cells_result)
    assert all(match.generation_id == cells_generation_id for match in cells_result)
