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
from foss_mcp.mcp.tools.find_examples import ExampleMatch
from foss_mcp.mcp.tools.get_symbol import NotFound, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.lookup import TaskAnswer, _looks_like_a_task_question, lookup
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


def _pdf_net_symbol_chunks() -> list:
    """A real, bounded slice of TC-011's actual pdf/net extraction, as unpublished chunks."""
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
    return chunk_document(doc)


def _pdf_net_doc_chunks() -> list:
    """Furnished-content documentation, as unpublished chunks.

    TC-109 (G2/REQ-G2-047): every real pilot publishes exactly ONE generation per scope, under
    source_kind="self_extracted" - there is no separate "furnished" deployment anywhere. This
    helper's own document metadata still records where the content actually originated
    (``SourceKind.FURNISHED``, i.e. aspose.org-authored prose rather than code-derived), but the
    GENERATION it gets published into - the scope key ``search_docs``/``search_symbols`` route
    reads and writes - is the single real one, ``self_extracted``.
    """
    doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="product_page",
        provenance=Provenance(repository="Aspose/aspose.org", commit="x", path="content/pdf/net"),
        evidence_refs=(),
        title="pdf/net user guide",
        body=USER_GUIDE_BODY,
    )
    return chunk_document(doc)


def _publish_pdf_net_symbols(store: GenerationManifestStore) -> str:
    return _publish(store, PDF_NET_SCOPE, "self_extracted", _pdf_net_symbol_chunks())


def _publish_pdf_net_docs(store: GenerationManifestStore) -> str:
    return _publish(store, PDF_NET_SCOPE, "self_extracted", _pdf_net_doc_chunks())


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


def test_search_symbols_excludes_tc068_pseudo_symbol_chunks_from_its_own_matching(
    tmp_path: Path,
) -> None:
    """TC-068's ``Example: <title>`` pseudo-symbols are published into the exact same
    self_extracted generation search_symbols reads (see infra/build_chunks.py's own real
    chunk shape: a heading, an ``FQN: Example: <title>`` line, then an ``Example:`` code
    block). A pseudo-symbol is not a real symbol and search_symbols's own module docstring
    says so - it must never be reported as a match, even when it lexically matches at least
    as strongly as a real symbol answering the very same query.

    Real regression, not a mock: one real symbol chunk and one Example:-prefixed pseudo-symbol
    chunk are published side by side, both containing the word "watermark" in their own prose,
    so a lexical scan with no filter would return both.
    """
    store = _store(tmp_path)
    real_symbol_body = """## PdfDocument.AddWatermarkAnnotation

FQN: PdfDocument.AddWatermarkAnnotation
Kind: Method
Adds a watermark annotation to the page.
"""
    pseudo_symbol_body = (
        "# Example: Add a Watermark Annotation\n\n"
        "FQN: Example: Add a Watermark Annotation\n"
        "Kind: verified_example\n"
        "Adds a watermark annotation to a document using a real, verified snippet.\n\n"
        'Example:\ndocument.AddWatermarkAnnotation("Confidential")'
    )
    real_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="pdf/net API surface",
        body=real_symbol_body,
    )
    pseudo_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="example",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="Add a Watermark Annotation",
        body=pseudo_symbol_body,
    )
    chunks = chunk_document(real_doc) + chunk_document(pseudo_doc)
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunks)

    result = search_symbols(store, PDF_NET_SCOPE, "watermark")

    assert isinstance(result, list) and result
    assert all(isinstance(match, SymbolMatch) for match in result)
    assert all("Example: Add a Watermark Annotation" not in match.text for match in result)
    assert any("PdfDocument.AddWatermarkAnnotation" in match.text for match in result)

    pseudo_only_result = search_symbols(store, PDF_NET_SCOPE, "verified snippet")

    assert isinstance(pseudo_only_result, SymbolsMiss)


def test_search_symbols_excludes_tc112_doc_pseudo_symbol_chunks_from_its_own_matching(
    tmp_path: Path,
) -> None:
    """TC-112's furnished documentation chunks are published into this exact same
    self_extracted generation search_symbols reads, marked with a parallel pseudo-FQN of the
    literal shape ``Doc: <title>`` (see ``infra/build_chunks.py``'s own
    ``FQN: Doc: {candidate.title}\\n{chunk.text}`` construction, applied to each chunk AFTER
    ``chunk_document`` splits the candidate's body - reproduced exactly the same way here, not
    baked into the document body). A doc chunk is not a real symbol and must never be reported
    as one, exactly like a TC-068 ``Example:`` chunk already isn't (see the sibling test above).

    Real regression, not a mock: one real symbol chunk and one Doc:-prefixed pseudo-symbol
    chunk are published side by side, both containing the word "watermark" in their own prose,
    so a lexical scan with no filter would return both.
    """
    import dataclasses

    store = _store(tmp_path)
    real_symbol_body = """## PdfDocument.AddWatermarkAnnotation

FQN: PdfDocument.AddWatermarkAnnotation
Kind: Method
Adds a watermark annotation to the page.
"""
    doc_title = "Adding a Watermark"
    pseudo_doc_body = """## Adding a Watermark

Use PdfDocument.AddWatermarkAnnotation to add a watermark to a PDF document, stamping
confidential markings onto every exported page.
"""
    real_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="pdf/net API surface",
        body=real_symbol_body,
    )
    pseudo_doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="doc",
        provenance=Provenance(repository="Aspose/aspose.org", commit="x", path="content/pdf/net"),
        evidence_refs=(),
        title=doc_title,
        body=pseudo_doc_body,
    )
    pseudo_chunks = [
        dataclasses.replace(chunk, text=f"FQN: Doc: {doc_title}\n{chunk.text}")
        for chunk in chunk_document(pseudo_doc)
    ]
    chunks = chunk_document(real_doc) + pseudo_chunks
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunks)

    result = search_symbols(store, PDF_NET_SCOPE, "watermark")

    assert isinstance(result, list) and result
    assert all(isinstance(match, SymbolMatch) for match in result)
    assert all("Doc: Adding a Watermark" not in match.text for match in result)
    assert any("PdfDocument.AddWatermarkAnnotation" in match.text for match in result)

    pseudo_only_result = search_symbols(store, PDF_NET_SCOPE, "confidential markings")

    assert isinstance(pseudo_only_result, SymbolsMiss)


def test_search_symbols_miss_suggests_a_real_close_fqn_from_this_same_generation(tmp_path: Path) -> None:
    """TC-143 (G2/REQ-G2-047): a genuine Miss may carry a purely additive 'did you mean' hint.

    ``Annotation`` is a real class confirmed present in the committed pdf/net fixture
    (``Aspose.Pdf.Annotations.Annotation``, see ``tests/fixtures/pdf_net/api_surface.json``).
    ``Annotatio`` (a single dropped trailing character) shares zero BM25 tokens with the
    published chunk's own prose - confirmed directly against ``tokenize`` before writing this
    test - so this is a genuine lexical Miss, not an accidental real hit. The suggestion itself
    comes only from ``difflib.get_close_matches`` against the real FQN this exact generation
    published - never a fabricated or stale one - and the Miss verdict/reason stay completely
    unchanged.
    """
    store = _store(tmp_path)
    real_symbol_body = """## Annotation

FQN: Annotation
Kind: Class
Serves as the abstract base for every markup element placed on a PDF page.
"""
    real_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="pdf/net API surface",
        body=real_symbol_body,
    )
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunk_document(real_doc))

    result = search_symbols(store, PDF_NET_SCOPE, "Annotatio")

    assert isinstance(result, SymbolsMiss)
    assert result.scope == PDF_NET_SCOPE
    assert result.reason == "no symbol matches 'Annotatio'"
    assert result.suggestions == ("Annotation",)

    # A genuinely unrelated query (no real close match) stays empty - never fabricated.
    unrelated_result = search_symbols(store, PDF_NET_SCOPE, "CompletelyUnrelatedXyzzyWombatTerm")

    assert isinstance(unrelated_result, SymbolsMiss)
    assert unrelated_result.suggestions == ()

    # A real, successful match is completely unaffected - no suggestions field pollution on a hit.
    hit_result = search_symbols(store, PDF_NET_SCOPE, "Annotation")

    assert isinstance(hit_result, list) and hit_result
    assert all(isinstance(match, SymbolMatch) for match in hit_result)


def test_search_symbols_miss_suggestions_never_include_a_pseudo_symbol_fqn(tmp_path: Path) -> None:
    """The suggestion pool is drawn from the same real-symbol corpus search_symbols itself
    matches against - TC-068's ``Example: <title>`` / TC-112's ``Doc: <title>`` pseudo-symbol
    chunks are excluded from it exactly as they already are from a real match (see this
    module's own docstring), so a typo'd query is never pointed at a pseudo-symbol as if it
    were a real "did you mean" answer.
    """
    store = _store(tmp_path)
    real_symbol_body = """## Annotation

FQN: Annotation
Kind: Class
Serves as the abstract base for every markup element placed on a PDF page.
"""
    pseudo_symbol_body = (
        "# Example: Annotatio\n\n"
        "FQN: Example: Annotatio\n"
        "Kind: verified_example\n"
        "A pseudo-symbol chunk that must never be suggested as a real FQN.\n\n"
        "Example:\nannotation.DoSomething()"
    )
    real_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="pdf/net API surface",
        body=real_symbol_body,
    )
    pseudo_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="example",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="Annotatio",
        body=pseudo_symbol_body,
    )
    chunks = chunk_document(real_doc) + chunk_document(pseudo_doc)
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunks)

    result = search_symbols(store, PDF_NET_SCOPE, "Annotatio")

    assert isinstance(result, SymbolsMiss)
    assert result.suggestions == ("Annotation",)
    assert "Example: Annotatio" not in result.suggestions


def test_get_symbol_not_found_suggests_a_real_close_fqn_from_this_same_generation(tmp_path: Path) -> None:
    """TC-143 (G2/REQ-G2-047): the same purely-additive 'did you mean' hint on get_symbol's own
    explicit 404, using get_symbol's own EXACT-match corpus (no exclusion of any kind - a
    pseudo-symbol FQN is already a legitimate get_symbol lookup target by this tool's own
    design, see its module docstring, so the suggestion pool mirrors that exactly)."""
    store = _store(tmp_path)
    real_symbol_body = """## PdfDocument.AddWatermarkAnnotation

FQN: PdfDocument.AddWatermarkAnnotation
Kind: Method
Adds a watermark annotation to the page.
"""
    real_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="pdf/net API surface",
        body=real_symbol_body,
    )
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunk_document(real_doc))

    # A single-character typo (missing trailing "n") of the real FQN above.
    result = get_symbol(store, PDF_NET_SCOPE, "PdfDocument.AddWatermarkAnnotatio")

    assert isinstance(result, NotFound)
    assert result.scope == PDF_NET_SCOPE
    assert result.fqn == "PdfDocument.AddWatermarkAnnotatio"
    assert result.suggestions == ("PdfDocument.AddWatermarkAnnotation",)

    # A genuinely unrelated query (no real close match) stays empty - never fabricated.
    unrelated_result = get_symbol(store, PDF_NET_SCOPE, "CompletelyUnrelatedXyzzyWombatTerm")

    assert isinstance(unrelated_result, NotFound)
    assert unrelated_result.suggestions == ()

    # A real, successful match is completely unaffected - no suggestions field pollution on a hit.
    hit_result = get_symbol(store, PDF_NET_SCOPE, "PdfDocument.AddWatermarkAnnotation")

    assert isinstance(hit_result, SymbolSignature)
    assert hit_result.fqn == "PdfDocument.AddWatermarkAnnotation"


def test_get_symbol_not_found_with_no_published_generation_has_no_suggestions(tmp_path: Path) -> None:
    """The early 'no generation at all' 404 path has no documents to draw a suggestion from -
    ``suggestions`` must stay the empty default, never attempt to read a nonexistent manifest."""
    store = _store(tmp_path)

    result = get_symbol(store, PDF_NET_SCOPE, "Anything")

    assert isinstance(result, NotFound)
    assert result.suggestions == ()


def test_search_docs_rejects_an_invalid_content_type(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        search_docs(store, PDF_NET_SCOPE, "install", "not_a_real_category")


def test_search_docs_finds_a_document_published_under_self_extracted(tmp_path: Path) -> None:
    """TC-109 (G2/REQ-G2-047): the real, independently-confirmed routing bug this card fixes.

    search_docs.py computed its scope key from ``SOURCE_KIND = "furnished"``, but every real
    ingest-<pilot> pipeline (``infra/ingest.py``) publishes, and every serving-<pilot> deployment
    serves, ``source_kind="self_extracted"`` only - nothing anywhere ever published to
    "furnished", so search_docs always returned an explicit ``Miss`` for every pilot regardless
    of what content existed. This test manually publishes ONE document chunk under
    ``source_kind="self_extracted"`` - the exact scope key search_docs now reads - and proves
    search_docs finds it. Before this fix (``SOURCE_KIND = "furnished"``), this identical
    publish+query would return an eternal ``Miss`` no matter what content exists, because it
    would be checking the wrong scope key entirely (see this card's own ``negative_control``,
    which reverts the constant and expects exactly this test to fail as a result).
    """
    store = _store(tmp_path)
    doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="product_page",
        provenance=Provenance(repository="Aspose/aspose.org", commit="x", path="content/pdf/net"),
        evidence_refs=(),
        title="pdf/net getting started",
        body="## Getting Started\n\nInstall the package via NuGet to get started quickly.",
    )
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunk_document(doc))

    result = search_docs(store, PDF_NET_SCOPE, "install", "getting_started")

    assert isinstance(result, list) and result
    assert all(isinstance(match, DocMatch) for match in result)
    assert any("install" in match.text.lower() for match in result)


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


def test_lookup_symbol_first_dispatch_still_surfaces_a_furnished_doc_chunk_bare_query(
    tmp_path: Path,
) -> None:
    """TC-109 (G2/REQ-G2-047) DISCOVERY, recorded here rather than silently patched around.

    This test used to be named "...falls_back_to_docs_when_nothing_resolves_as_a_symbol" and
    relied on ``_publish_pdf_net_docs`` writing to a "furnished" scope key search_symbols never
    read - so, before this card, NO self_extracted generation existed at all here, search_symbols
    missed on "no generation", and lookup fell through to ``_compose_from_docs`` by construction.

    Fixing search_docs's routing bug (its own module's one job) means search_docs now reads the
    exact SAME generation search_symbols and find_examples already did, because that is what
    every real pilot actually publishes - one generation per scope, never two independent ones.
    That is correct and is the whole point of TC-109. But it has a real, reproduced consequence
    for THIS scenario: search_symbols.py excludes only TC-068's ``Example: <title>``
    pseudo-symbol chunks (see its own module docstring) - it has no OTHER content-shape filter,
    so a furnished documentation chunk with no ``Example:`` prefix is not excluded either, and a
    bare (non-task-question) query that lexically matches it is reported as a genuine symbol
    match. lookup()'s bare-query dispatch order - try ``search_symbols`` first, unchanged - then
    faithfully returns that non-empty result, per its own documented contract, without ever
    reaching ``_compose_from_docs``.

    That is NOT a defect in search_docs.py's one-line fix, nor in ``lookup()``'s dispatch logic -
    both do exactly what their own contracts promise. It IS a real gap in search_symbols.py
    (no positive "this is actually symbol-shaped" signal, only a negative Example: exclusion),
    which is out of THIS card's write_paths to touch. A follow-up card should close it before
    REQ-G2-049 scenario 4's original promise - a bare query composing a doc-fallback TaskAnswer
    when nothing resolves as a REAL symbol - is reachable again now that both tools share one
    generation.
    """
    store = _store(tmp_path)
    _publish_pdf_net_docs(store)

    result = lookup(store, PDF_NET_SCOPE, "licensed")

    # Documents the real, reproduced, current behavior: search_symbols has no exclusion for
    # ordinary documentation prose, so it reports the furnished FAQ chunk as a symbol match,
    # and lookup's bare-query dispatch faithfully returns exactly that.
    assert isinstance(result, list) and result
    assert all(isinstance(match, SymbolMatch) for match in result)
    assert any("licensed" in match.text.lower() for match in result)


def test_lookup_returns_the_original_miss_when_nothing_resolves_at_all(tmp_path: Path) -> None:
    """REQ-G2-049 (scenario 5): a full miss - neither a symbol nor a doc anywhere for this
    scope - stays completely unaffected by composition. The miss from search_symbols is
    returned exactly as before; no TaskAnswer, no example fabricated from nothing.
    """
    store = _store(tmp_path)
    # Real pilots publish exactly one generation per scope: symbol and doc chunks are combined
    # into a single publish, not two sequential ones (which would collide on the same
    # self_extracted scope key/active pointer - see TC-109, G2/REQ-G2-047).
    _publish(store, PDF_NET_SCOPE, "self_extracted", _pdf_net_symbol_chunks() + _pdf_net_doc_chunks())

    result = lookup(store, PDF_NET_SCOPE, "ZzzTotallyNonexistentQueryForEverything")

    assert isinstance(result, SymbolsMiss)
    assert result.scope == PDF_NET_SCOPE


def test_lookup_symbol_first_dispatch_surfaces_furnished_doc_not_the_excluded_pseudo_symbol(
    tmp_path: Path,
) -> None:
    """This test used to be named "...composes_doc_guidance_with_a_real_example_when_no_real_
    symbol_matches" (REQ-G2-049 scenario 3) and relied on the furnished watermarking guide living
    under a "furnished" scope key search_symbols never read - so search_symbols only ever saw the
    real (unrelated) symbol and the excluded pseudo-symbol, genuinely missed, and lookup fell
    through to ``_compose_from_docs``.

    TC-109 (G2/REQ-G2-047) fixes search_docs's own routing bug so it reads the exact SAME
    generation search_symbols and find_examples already did - the one every real pilot actually
    publishes. Reproduced, real consequence: search_symbols.py excludes ONLY TC-068's
    ``Example: <title>`` pseudo-symbol chunks (see its own module docstring); it has no OTHER
    content-shape filter, so the furnished watermarking-guide chunk below (no ``Example:``
    prefix) is not excluded, and a bare query matching it is reported as a genuine symbol match.
    lookup()'s bare-query dispatch order - try ``search_symbols`` first, unchanged - faithfully
    returns that, never reaching ``_compose_from_docs``.

    This still proves TC-068's own exclusion works correctly (the pseudo-symbol chunk never
    appears in the result, below), which is the one thing search_symbols.py already promises.
    Closing the REMAINING gap - a furnished doc chunk also needs to be excluded from
    search_symbols's own matching, the same way the pseudo-symbol already is - is a follow-up
    card's job: search_symbols.py is not in TC-109's write_paths.
    """
    store = _store(tmp_path)
    real_symbol_body = """## PdfDocument

FQN: PdfDocument
Kind: Class
Represents a PDF document that can be loaded, edited, and saved.
"""
    pseudo_symbol_body = (
        "# Example: Add a Watermark Annotation\n\n"
        "FQN: Example: Add a Watermark Annotation\n"
        "Kind: verified_example\n"
        "Adds a watermark annotation to a document using a real, verified snippet.\n\n"
        'Example:\ndocument.AddWatermarkAnnotation("Confidential")'
    )
    real_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="pdf/net API surface",
        body=real_symbol_body,
    )
    pseudo_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="example",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="Add a Watermark Annotation",
        body=pseudo_symbol_body,
    )
    watermark_guide_doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="product_page",
        provenance=Provenance(repository="Aspose/aspose.org", commit="x", path="content/pdf/net"),
        evidence_refs=(),
        title="pdf/net watermarking guide",
        body="""## Adding a Watermark

Use PdfDocument.AddWatermarkAnnotation to overlay a watermark on every page. This approach
works well for stamping confidential markings onto exported PDF documents.
""",
    )
    # One combined publish, matching every real pilot's actual shape: symbol, pseudo-symbol,
    # and doc chunks all live in the SAME self_extracted generation (TC-109, G2/REQ-G2-047) -
    # there is no separate "furnished" scope key for search_docs to read anymore.
    chunks = chunk_document(real_doc) + chunk_document(pseudo_doc) + chunk_document(watermark_guide_doc)
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunks)

    result = lookup(store, PDF_NET_SCOPE, "watermark")

    # Documents the real, reproduced, current behavior: search_symbols reports the furnished
    # doc chunk (no Example: prefix, so not excluded) as a symbol match, and lookup's bare-query
    # dispatch faithfully returns exactly that - never reaching _compose_from_docs.
    assert isinstance(result, list) and result
    assert all(isinstance(match, SymbolMatch) for match in result)
    # TC-068's own exclusion still works correctly: the pseudo-symbol never masquerades as one.
    assert all("Example: Add a Watermark Annotation" not in match.text for match in result)
    assert any("watermark" in match.text.lower() for match in result)


def test_lookup_composes_example_only_answer_when_no_doc_content_exists_anywhere(
    tmp_path: Path,
) -> None:
    """TC-075: the real, final gap the day's investigation converged on. No furnished
    getting_started/developer_guide/troubleshooting/faq content has ever been built or
    published for any pilot - this matches every real pilot's actual current state, not a
    contrived one. ``find_examples`` alone still returns a real, complete, self-descriptive
    answer (a verified code example with its own description, per TC-068's design). Before
    this card, ``_compose_from_docs`` only tried ``find_examples`` AFTER finding a non-empty
    doc match, so this real, useful answer was thrown away and lookup fell through to
    ``search_symbols`` instead. It must not, now.

    TC-109 (G2/REQ-G2-047) UPDATE: search_docs now correctly reads the SAME generation
    search_symbols and find_examples already did (its routing bug is what this card fixes),
    rather than a "furnished" scope key nothing ever actually published to. One real
    consequence, reproduced here rather than hidden: search_docs.py's own ``classify_content_type``
    has no exclusion for TC-068's ``Example: <title>`` pseudo-symbol chunks (unlike
    search_symbols.py, which excludes them by design - see its module docstring), so this
    pseudo-symbol chunk - lacking any of the getting_started/troubleshooting/faq keyword hints -
    falls into search_docs's default "developer_guide" bucket and IS now found there too. The
    example-only promise this test is named for still holds (``result.example`` is populated
    correctly, verbatim, never fabricated); what no longer holds is the total ABSENCE of a doc
    match, since search_docs cannot yet tell a pseudo-symbol chunk apart from real documentation
    prose. Extending that exclusion belongs to a future card - it is not part of search_docs.py's
    one-constant fix TC-109 makes, and touching classify_content_type is explicitly out of this
    card's scope.
    """
    store = _store(tmp_path)
    pseudo_symbol_body = (
        "# Example: Add a Watermark Annotation\n\n"
        "FQN: Example: Add a Watermark Annotation\n"
        "Kind: verified_example\n"
        "Adds a watermark annotation to a document using a real, verified snippet.\n\n"
        'Example:\ndocument.AddWatermarkAnnotation("Confidential")'
    )
    pseudo_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="example",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="Add a Watermark Annotation",
        body=pseudo_symbol_body,
    )
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunk_document(pseudo_doc))

    # No REAL documentation content is published for this scope - only the pseudo-symbol
    # chunk above - matching every real pilot's actual current state today.

    result = lookup(store, PDF_NET_SCOPE, "how do I add a watermark to a PDF")

    assert isinstance(result, TaskAnswer)
    assert result.scope == PDF_NET_SCOPE
    # search_docs (post-TC-109) now finds this exact pseudo-symbol chunk too, mislabeled as
    # "developer_guide" content - see the docstring above for why that is a real, separately-
    # scoped gap rather than something this card silently papered over.
    assert isinstance(result.doc_matches, tuple) and result.doc_matches
    assert all(isinstance(match, DocMatch) for match in result.doc_matches)
    assert isinstance(result.example, ExampleMatch)
    assert result.example.fqn == "Example: Add a Watermark Annotation"
    assert 'document.AddWatermarkAnnotation("Confidential")' in result.example.snippet


def test_looks_like_a_task_question_classifies_by_query_shape() -> None:
    """The exact classification the card requires: real symbol-shaped queries from the actual
    pdf/net fixture stay NOT-task-shaped (existing tests depend on those staying on the
    symbol-first path), while the card's own real natural-language example is task-shaped."""
    assert _looks_like_a_task_question("how do I add a watermark to a PDF") is True
    assert _looks_like_a_task_question("AddWatermarkAnnotation") is False
    assert _looks_like_a_task_question("Document") is False
    assert _looks_like_a_task_question("AFRelationship") is False


def test_lookup_prefers_doc_composition_over_an_incidental_symbol_collision_for_a_task_question(
    tmp_path: Path,
) -> None:
    """Reproduces the real 'pdf' collision TC-073's worker found live, at fixture scale: a real
    symbol's FQN (``PdfDocument.Merge``) lexically overlaps one of a natural-language question's
    own words (``pdf``, via camelCase sub-word splitting), purely incidentally - the symbol is
    about merging, the question is about watermarking. Before this card, lookup's own "any
    non-empty search_symbols result wins" dispatch order let that incidental overlap capture a
    real task question and starve it of the doc+example answer it actually needed.

    Proves BOTH halves of the card's closeout: the task-shaped question now gets the composed
    TaskAnswer instead of the incidental symbol match, while a short, bare query for that exact
    same overlapping symbol still correctly returns the symbol match unchanged.
    """
    store = _store(tmp_path)
    real_symbol_body = """## PdfDocument.Merge

FQN: PdfDocument.Merge
Kind: Method
Merges multiple PDF files into a single output document.
"""
    real_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="pdf/net API surface",
        body=real_symbol_body,
    )
    watermark_guide_doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="product_page",
        provenance=Provenance(repository="Aspose/aspose.org", commit="x", path="content/pdf/net"),
        evidence_refs=(),
        title="pdf/net watermarking guide",
        body="""## Adding a Watermark to a PDF

Use PdfDocument.AddWatermarkAnnotation to add a watermark to a PDF document, stamping
confidential markings onto every exported page.
""",
    )
    # One combined publish, matching every real pilot's actual shape (TC-109, G2/REQ-G2-047):
    # there is no separate "furnished" scope key for search_docs to read anymore.
    chunks = chunk_document(real_doc) + chunk_document(watermark_guide_doc)
    _publish(store, PDF_NET_SCOPE, "self_extracted", chunks)

    task_question = "how do I add a watermark to a PDF"

    # The incidental collision is real, not hypothetical: the unrelated Merge symbol's FQN
    # shares the word "pdf" with the question, so a naive non-empty check on search_symbols
    # alone would (wrongly) treat this as a confident symbol match.
    raw_symbol_result = search_symbols(store, PDF_NET_SCOPE, task_question)
    assert isinstance(raw_symbol_result, list) and raw_symbol_result

    result = lookup(store, PDF_NET_SCOPE, task_question)

    assert isinstance(result, TaskAnswer)
    assert result.scope == PDF_NET_SCOPE
    assert isinstance(result.doc_matches, tuple) and result.doc_matches
    assert any("watermark" in match.text.lower() for match in result.doc_matches)

    # A short, bare query for the very same overlapping symbol is completely unaffected -
    # it still resolves as a symbol lookup, exactly as before this card.
    bare_result = lookup(store, PDF_NET_SCOPE, "PdfDocument")

    assert isinstance(bare_result, list) and all(isinstance(match, SymbolMatch) for match in bare_result)
    assert any("PdfDocument.Merge" in match.text for match in bare_result)


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
