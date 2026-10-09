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

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import query_lexical_index
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import ExampleMatch, NoExampleFound, find_examples
from foss_mcp.mcp.tools.get_symbol import NotFound, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.lookup import TaskAnswer, _looks_like_a_task_question, lookup
from foss_mcp.mcp.tools.search_docs import DocMatch, search_docs
from foss_mcp.mcp.tools.search_docs import Miss as DocsMiss
from foss_mcp.mcp.tools.search_symbols import SOURCE_KIND as SYMBOLS_SOURCE_KIND
from foss_mcp.mcp.tools.search_symbols import Miss as SymbolsMiss
from foss_mcp.mcp.tools.search_symbols import SymbolMatch, scope_key, search_symbols
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
        sections.append(f"## {name}\n\nFQN: {name}\n{text}")
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

    TC-202 (G2/REQ-G2-047) closes that gap: search_symbols now accepts only an exact symbol hit,
    so this query is an honest Miss and lookup composes the doc-fallback TaskAnswer (line 555).
    """
    store = _store(tmp_path)
    _publish_pdf_net_docs(store)

    result = lookup(store, PDF_NET_SCOPE, "licensed")

    # TC-202 restatement (line 555): "licensed" is not a whole word of any symbol FQN, so
    # search_symbols returns an honest Miss and lookup composes the doc-fallback TaskAnswer; the
    # furnished FAQ chunk is in its doc_matches.
    assert isinstance(result, TaskAnswer)
    assert isinstance(result.doc_matches, tuple) and result.doc_matches
    assert any("licensed" in match.text.lower() for match in result.doc_matches)


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

    TC-202 (G2/REQ-G2-047) closes that gap: the query is an honest Miss in search_symbols, and the
    furnished guide chunk reaches the answer as a doc match (restated at line 654).
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

    # TC-202 restatement (line 654): "watermark" is a whole word of no symbol FQN here (the real
    # symbol is PdfDocument, and the furnished chunk has no FQN line), so search_symbols returns an
    # honest Miss and lookup composes a TaskAnswer; the furnished guide chunk is in its doc_matches.
    assert isinstance(result, TaskAnswer)
    assert isinstance(result.doc_matches, tuple) and result.doc_matches
    assert all(isinstance(match, DocMatch) for match in result.doc_matches)
    assert any("watermark" in match.text.lower() for match in result.doc_matches)
    # TC-068's pseudo-symbol is never reported as a symbol: it reaches the answer only as the example.
    assert result.example is not None
    assert result.example.fqn == "Example: Add a Watermark Annotation"


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


def test_an_example_answer_survives_zero_documentation_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-218: forces ``lookup``'s example-only branch (``if not doc_matches and example is
    None``) to be reached. TC-109 made ``search_docs`` classify the Example pseudo-symbol chunk
    as developer_guide, so the TC-075 test above no longer sees zero doc matches and cannot
    catch a regression that narrows the condition. Here ``search_docs`` is forced to miss for
    every content type, so the verified example must still answer on its own.
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

    def _forced_miss(
        store_arg: GenerationManifestStore,
        scope: Scope,
        query: str,
        content_type: str,
        *args: object,
        **kwargs: object,
    ) -> DocsMiss:
        return DocsMiss(scope=scope, query=query, content_type=content_type, reason="forced miss")

    monkeypatch.setattr("foss_mcp.mcp.tools.lookup.search_docs", _forced_miss)

    result = lookup(store, PDF_NET_SCOPE, "how do I add a watermark to a PDF")

    assert isinstance(result, TaskAnswer)
    assert result.doc_matches == ()
    assert isinstance(result.example, ExampleMatch)
    assert result.example.fqn == "Example: Add a Watermark Annotation"


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

    # TC-202 restatement (line 785): the Merge symbol's FQN shares only the word "pdf" with the
    # question. The exact-hit predicate needs every query word to be a whole word of the FQN, so
    # the collision is rejected and search_symbols returns a Miss for the question.
    raw_symbol_result = search_symbols(store, PDF_NET_SCOPE, task_question)
    assert isinstance(raw_symbol_result, SymbolsMiss)

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
                body="## Workbook\n\nFQN: Workbook\nWorkbook is a class. Methods: Save, Open.",
            )
        ),
    )

    pdf_result = search_symbols(store, PDF_NET_SCOPE, "Workbook")
    cells_result = search_symbols(store, CELLS_PYTHON_SCOPE, "Workbook")

    assert isinstance(pdf_result, SymbolsMiss), "Workbook belongs to a different product entirely"
    assert isinstance(cells_result, list) and cells_result
    assert all(match.scope == CELLS_PYTHON_SCOPE for match in cells_result)
    assert all(match.generation_id == cells_generation_id for match in cells_result)


# --- TC-273: find_examples' semantic fallback gets a real, measured relevance floor ---------
#
# The audit's own invariant: "a search system that returns something for every query is worse
# than one that honestly misses." Before this card, the semantic (BM25) fallback treated ANY
# chunk with a strictly-positive score as a confident match - a single shared common word
# between the query and an otherwise-unrelated chunk was enough. The three tests below prove
# the three properties the audit requires tested separately, using this file's own existing
# per-test synthetic-manifest-construction convention (make_document + chunk_document, one
# combined publish) rather than a different fixture style.

_WATERMARK_EXAMPLE_BODY = (
    "# Example: Add a Watermark Annotation\n\n"
    "FQN: Example: Add a Watermark Annotation\n"
    "Kind: verified_example\n"
    "Adds a watermark annotation to a document using a real, verified snippet.\n\n"
    'Example:\ndocument.AddWatermarkAnnotation("Confidential")'
)

_EXTRACT_TEXT_EXAMPLE_BODY = (
    "# Example: Extract Text Fragments from a Page\n\n"
    "FQN: Example: Extract Text Fragments from a Page\n"
    "Kind: verified_example\n"
    "Extracts every text fragment from the first page of a document using a real, verified\n"
    "snippet.\n\n"
    "Example:\nvar absorber = new TextFragmentAbsorber();\ndoc.Pages[1].Accept(absorber);"
)


def _publish_two_example_chunks(store: GenerationManifestStore) -> str:
    watermark_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="example",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="Add a Watermark Annotation",
        body=_WATERMARK_EXAMPLE_BODY,
    )
    extract_doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="example",
        provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="z"),
        evidence_refs=(),
        title="Extract Text Fragments from a Page",
        body=_EXTRACT_TEXT_EXAMPLE_BODY,
    )
    chunks = chunk_document(watermark_doc) + chunk_document(extract_doc)
    return _publish(store, PDF_NET_SCOPE, "self_extracted", chunks)


def test_find_examples_still_finds_a_genuinely_relevant_example(tmp_path: Path) -> None:
    """TC-273 property (1): a genuinely relevant example, when one exists, is still found once
    the semantic fallback requires coverage - the query and the right chunk share enough of the
    query's own distinct terms ("add", "watermark", "annotation" - all three are real tokens of
    the watermark example chunk below, confirmed directly against ``tokenize`` before writing
    this test) to clear ``_COVERAGE_THRESHOLD`` (0.6) with coverage to spare (1.0 here).
    """
    store = _store(tmp_path)
    _publish_two_example_chunks(store)

    result = find_examples(store, PDF_NET_SCOPE, "add a watermark annotation")

    assert isinstance(result, list) and result
    assert all(isinstance(match, ExampleMatch) for match in result)
    assert any('document.AddWatermarkAnnotation("Confidential")' in match.snippet for match in result)
    assert all(match.coverage >= 0.6 for match in result)


def test_find_examples_rejects_a_common_word_only_overlap_the_old_score_above_zero_bar_missed(
    tmp_path: Path,
) -> None:
    """TC-273 property (2): the real regression this card closes, proven as a direct
    before/after comparison rather than asserted in prose.

    The query "page rotation" shares exactly one real token ("page") with the "Extract Text
    Fragments from a Page" example chunk below and nothing else - that chunk never discusses
    rotation at all. Confirmed directly: the OLD bar (``query_lexical_index``'s own plain
    strictly-positive-BM25-score ranking, unchanged by this card) still returns this chunk, so
    before TC-273 this query would have manufactured a full-confidence, wrong ``ExampleMatch``.
    The chunk's coverage against this query is exactly 0.5 (one of the query's two distinct
    tokens, "page", appears in it; "rotation" does not) - below ``_COVERAGE_THRESHOLD`` (0.6) -
    so ``find_examples`` now honestly misses instead.
    """
    store = _store(tmp_path)
    generation_id = _publish_two_example_chunks(store)

    key = scope_key(PDF_NET_SCOPE, SYMBOLS_SOURCE_KIND)
    manifest = store.read_generation(key, generation_id)
    lexical_payload = manifest.payload["lexical_index"]
    old_bar_ranked = query_lexical_index(lexical_payload, "page rotation", top_k=5)
    assert old_bar_ranked, (
        "the OLD plain-ranking bar must still return at least one doc for this query - "
        "otherwise this is not a reproduction of the real regression at all"
    )

    result = find_examples(store, PDF_NET_SCOPE, "page rotation")

    assert isinstance(result, NoExampleFound)
    assert result.scope == PDF_NET_SCOPE


def test_find_examples_honestly_misses_a_deliberately_unrelated_nonsense_query(
    tmp_path: Path,
) -> None:
    """TC-273 property (3): a query naming a concept this corpus has nothing to do with gets an
    honest ``NoExampleFound``, both before and after this card's change - this is NOT what this
    card fixes, and this test confirms it was already correct and stays correct. Shares zero
    tokens with anything indexed (confirmed directly against ``tokenize``), so even the OLD
    plain-ranking bar already returns nothing for it.
    """
    store = _store(tmp_path)
    generation_id = _publish_two_example_chunks(store)

    key = scope_key(PDF_NET_SCOPE, SYMBOLS_SOURCE_KIND)
    manifest = store.read_generation(key, generation_id)
    lexical_payload = manifest.payload["lexical_index"]
    old_bar_ranked = query_lexical_index(lexical_payload, "quantum teleportation breakfast recipe", top_k=5)
    assert old_bar_ranked == [], "a genuinely unrelated query must already score 0.0 everywhere"

    result = find_examples(store, PDF_NET_SCOPE, "quantum teleportation breakfast recipe")

    assert isinstance(result, NoExampleFound)
    assert result.scope == PDF_NET_SCOPE


# --- TC-278 (C7, G2/REQ-G2-043): tier-ordered ranking + a measured bag-of-words precision ------
# floor, both proved against the REAL production chunking path - build_chunks_from_api_surface
# over the real committed pdf/net fixture - never a hand-shaped one-chunk-per-method synthetic
# fixture. This differs deliberately from this file's own ``_pdf_net_symbol_chunks()`` helper
# above (a 20-type hand-rolled slice that bypasses chunk_builder.py entirely): the card is
# explicit that both the live reproduction and these regression tests must go through the real
# extraction->chunking pipeline, so the real FQN/Kind/Methods formatting and the real sibling
# chunks (every other type in the fixture) are genuinely present, not approximated.


def _publish_real_pdf_net_surface(store: GenerationManifestStore) -> str:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title="pdf/net API surface")
    return _publish(store, PDF_NET_SCOPE, "self_extracted", chunks)


def test_search_symbols_ranks_the_real_base_class_ahead_of_its_own_subclasses(tmp_path: Path) -> None:
    """FIX A regression: before this card, ``Aspose.Pdf.Annotations.Annotation`` (a real,
    final-segment-equality hit - tier 1, the strongest evidence short of exact-FQN) ranked 9th
    of 14 admitted candidates for the query "Annotation", behind 8 of its own subclasses (each
    only a weaker bag-of-words/tier-2 hit, e.g. AnnotationSelector, PopupAnnotation,
    MarkupAnnotation) - purely because each subclass's own chunk is shorter and so scores a
    higher raw BM25 term density, never because any subclass is stronger EVIDENCE of a real
    match for "Annotation" than the base class itself is. Confirmed live against this exact
    fixture before this card's fix landed. search_symbols now sorts admitted candidates by tier
    first, so the base class - still a tier-1 hit - ranks ahead of every tier-2 subclass,
    regardless of raw BM25 score.
    """
    store = _store(tmp_path)
    _publish_real_pdf_net_surface(store)

    result = search_symbols(store, PDF_NET_SCOPE, "Annotation", top_k=10)

    assert isinstance(result, list) and result
    assert "FQN: Aspose.Pdf.Annotations.Annotation\n" in result[0].text, (
        "the real base class must rank FIRST, ahead of every one of its own subclasses"
    )

    # It must also survive find_examples' own, stricter default (top_k=5) - before this card's
    # fix, the base class did not even reach the real default top_k=10, let alone top_k=5.
    narrow_result = search_symbols(store, PDF_NET_SCOPE, "Annotation", top_k=5)
    assert isinstance(narrow_result, list)
    assert any("FQN: Aspose.Pdf.Annotations.Annotation\n" in match.text for match in narrow_result)


def test_search_symbols_honestly_misses_a_scattered_reordered_bag_of_words_query(
    tmp_path: Path,
) -> None:
    """FIX B regression: before this card, the bag-of-words tier admitted any query whose
    words were ALL present somewhere in an FQN's own word SET, with no check on order,
    adjacency, or how much of the FQN's structure the query actually covered. Live-confirmed
    against this exact fixture: "set pattern color" and "set color pattern" each returned a
    confident match on ``Aspose.Pdf.Operators.BasicSetColorAndPatternOperator`` - an internal,
    abstract content-stream-operator base class for parsing raw SCN/scn operators, not a public
    "set pattern color" feature (no such feature exists in this corpus; the honest answer is a
    Miss). All 3 of the query's words really do appear somewhere among the FQN's own 9 words
    ("aspose", "pdf", "operators", "basic", "set", "color", "and", "pattern", "operator"), but
    scattered and reordered - "set" and "color" are adjacent in the FQN, "pattern" is not next
    to either. search_symbols' new contiguous-run precision floor (measured empirically against
    3 real pilots - see search_symbols.py's own module docstring) requires the query's own
    words, in the query's own order, to appear as one unbroken run in the FQN's own word
    sequence; neither phrasing's words do, so both are now an honest Miss.
    """
    store = _store(tmp_path)
    _publish_real_pdf_net_surface(store)

    for query in ("set pattern color", "set color pattern"):
        result = search_symbols(store, PDF_NET_SCOPE, query)

        assert isinstance(result, SymbolsMiss), f"{query!r} must be an honest Miss, got {result!r}"
        assert "BasicSetColorAndPatternOperator" not in result.reason


def test_search_symbols_fix_b_precision_floor_does_not_regress_a_whole_word_true_positive(
    tmp_path: Path,
) -> None:
    """FIX B non-regression, against the real fixture: the card's own prose names
    ``PdfDocument.AddWatermarkAnnotation`` as the already-required true positive "watermark"
    must keep matching - but that exact FQN does not exist anywhere in the real, committed
    ``tests/fixtures/pdf_net/api_surface.json`` (confirmed directly: no "AddWatermarkAnnotation"
    substring anywhere in the fixture file). The real fixture's genuinely equivalent symbol -
    same shape of regression, same single-word bag-of-words query - is
    ``Aspose.Pdf.Annotations.WatermarkAnnotation``. This test uses that real symbol instead of
    fabricating the nonexistent one the card's prose named, and proves the same property: Fix
    B's new contiguous-run floor is trivially satisfied by any single-word query (a run of
    length 1 is always contiguous), so it does not regress this already-required whole-word
    true positive.
    """
    store = _store(tmp_path)
    _publish_real_pdf_net_surface(store)

    result = search_symbols(store, PDF_NET_SCOPE, "watermark")

    assert isinstance(result, list) and result
    assert any("FQN: Aspose.Pdf.Annotations.WatermarkAnnotation\n" in match.text for match in result)


# --- TC-279 (G2/REQ-G2-043): a query naming a real METHOD or PROPERTY (not just a class) now ---
# matches, by recognizing the member inside its containing chunk's own rendered Methods:/
# Properties: blocks - verified against the REAL production chunking path
# (build_chunks_from_api_surface -> build_lexical_index -> search_symbols) over the real
# committed pdf/net fixture, never a hand-shaped one-chunk-per-method synthetic fixture.
#
# Live-confirmed real facts about this exact fixture (see the worker report for the full
# measurement): ``Aspose.Pdf.Page.SetRotation`` genuinely exists, with the real rendered method
# line ``"  - SetRotation(degrees: int) -> void"`` inside ``Aspose.Pdf.Page``'s own chunk.
# ``Dispose`` is genuinely declared on 9 distinct real types in this fixture (Page, Document,
# XForm, FileSpecification, Artifact, Facades.Form, Facades.IFacade, Facades.ISaveableFacade,
# OperatorCollection) - the real multi-declaration ambiguity case, confirmed directly rather than
# assumed, used below to prove this tier admits ALL of them rather than guessing one "winner".


def test_search_symbols_finds_the_real_setrotation_method_by_its_bare_name(tmp_path: Path) -> None:
    """The exact live-confirmed defect this card fixes: before this card, querying the bare
    method name "SetRotation" returned an honest-LOOKING but WRONG Miss (with a misleading "did
    you mean" suggestion pointing at the unrelated ``Aspose.Pdf.Rotation`` enum), even though
    BM25 ranked the real containing chunk (``Aspose.Pdf.Page``) #1 by a wide margin - because
    ``_match_tier`` only ever compared the query against the chunk's own type-level FQN, never
    against a method name rendered as prose inside its Methods: block. This is now a real hit.
    """
    store = _store(tmp_path)
    _publish_real_pdf_net_surface(store)

    result = search_symbols(store, PDF_NET_SCOPE, "SetRotation")

    assert isinstance(result, list) and result
    assert any("FQN: Aspose.Pdf.Page\n" in match.text for match in result)
    assert any("SetRotation(degrees: int) -> void" in match.text for match in result)


def test_search_symbols_finds_setrotation_qualified_by_its_real_type_name(tmp_path: Path) -> None:
    """ "Type.Member" (using the chunk's own FQN's final segment) is also recognized, not just
    the bare member name alone."""
    store = _store(tmp_path)
    _publish_real_pdf_net_surface(store)

    result = search_symbols(store, PDF_NET_SCOPE, "Page.SetRotation")

    assert isinstance(result, list) and result
    assert any("FQN: Aspose.Pdf.Page\n" in match.text for match in result)


def test_search_symbols_still_honestly_misses_a_partial_truncated_member_name(tmp_path: Path) -> None:
    """Mirrors ``test_search_symbols_honest_miss.py``'s own "Watermar"/"Annotat" partial-word
    Miss tests, at the member level: a truncated real method name must never be loosened into a
    match merely because it is a prefix of one. "SetRotatio" is a dropped-trailing-character
    prefix of the real method name "SetRotation" above - never a whole member name itself - so
    this must stay an honest Miss, exactly like a partial CLASS name already does.
    """
    store = _store(tmp_path)
    _publish_real_pdf_net_surface(store)

    result = search_symbols(store, PDF_NET_SCOPE, "SetRotatio")

    assert isinstance(result, SymbolsMiss)
    assert "SetRotation" not in result.reason


def test_search_symbols_member_tier_admits_every_real_type_sharing_an_ambiguous_member_name(
    tmp_path: Path,
) -> None:
    """The real ambiguity case, verified against the real fixture rather than assumed: "Dispose"
    is genuinely declared on 9 distinct real types in ``tests/fixtures/pdf_net/api_surface.json``
    (confirmed directly before writing this test). The decided-and-verified behavior: this tier
    admits ALL of them, letting BM25/existing ranking sort among them - the same way TC-278's own
    bag-of-words tier already lets several same-tier class candidates coexist (see
    ``test_search_symbols_ranks_the_real_base_class_ahead_of_its_own_subclasses`` above) - rather
    than silently picking one "winner" and hiding the rest.
    """
    store = _store(tmp_path)
    _publish_real_pdf_net_surface(store)

    result = search_symbols(store, PDF_NET_SCOPE, "Dispose", top_k=20)

    assert isinstance(result, list) and result
    matched_fqns = {
        line[len("FQN: ") :]
        for match in result
        for line in match.text.splitlines()
        if line.startswith("FQN: ")
    }
    expected = {
        "Aspose.Pdf.Page",
        "Aspose.Pdf.Document",
        "Aspose.Pdf.XForm",
        "Aspose.Pdf.FileSpecification",
        "Aspose.Pdf.Artifact",
        "Aspose.Pdf.Facades.Form",
        "Aspose.Pdf.Facades.IFacade",
        "Aspose.Pdf.Facades.ISaveableFacade",
        "Aspose.Pdf.OperatorCollection",
    }
    assert expected <= matched_fqns, f"missing: {expected - matched_fqns}"
