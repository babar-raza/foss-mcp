"""HOLDOUT oracle for TC-017a — supervisor-authored, not named by the card.

This card carries the deepest lesson in the whole plan. The reference system's
philosophy is "always return something": on a miss it silently retries without
the language filter — its own source comments say this happens *even when
language was explicitly provided* — and it maps any requested version onto
whichever snapshot happens to be active. This project's philosophy must be the
exact opposite, and that is easy to claim and hard to prove.

Two failure modes a card's own suite tends to miss:

1. **Cross-scope leakage.** A single-product test fixture cannot detect it at
   all, because there is nothing to leak from. So this oracle publishes TWO real
   products into one store and asks each for content only the other has.

2. **Silent breadth on an explicit filter.** `lookup` without `content_type` is
   *allowed* to try several categories — that is documented dispatch, not
   widening. The line is that an EXPLICIT `content_type` must never be widened
   past. That precise boundary is what is tested below.

And an anti-vacuity guard throughout: a tool that returns `Miss` for everything
passes every isolation test ever written while being useless.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

WT = pathlib.Path(__file__).resolve().parents[3]
for p in (str(WT), str(WT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from foss_mcp.indexing.generation_manifest import GenerationManifestStore  # noqa: E402
from foss_mcp.indexing.publisher import publish_generation  # noqa: E402
from foss_mcp.mcp.routing import Scope  # noqa: E402
from foss_mcp.mcp.tools import search_docs as sd  # noqa: E402
from foss_mcp.mcp.tools import search_symbols as ss  # noqa: E402
from foss_mcp.mcp.tools.lookup import lookup  # noqa: E402
from foss_mcp.normalization.chunker import chunk_document  # noqa: E402
from foss_mcp.normalization.document_schema import (  # noqa: E402
    Provenance,
    SourceKind,
    make_document,
)

FIXTURE = WT / "tests" / "fixtures" / "pdf_net" / "api_surface.json"

PDF_NET = Scope(family="pdf", platform="net")
CELLS_PY = Scope(family="cells", platform="python")

# A term that exists ONLY in the cells/python corpus below. If a pdf/net query
# ever returns it, scope isolation has failed.
CELLS_ONLY_TERM = "Chartsheet"
PDF_ONLY_TERM = "AFRelationship"


class _Provider:
    dimension = 8

    def embed(self, texts):
        return [[float((len(t) + i) % 5) for i in range(self.dimension)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


def _doc(title, body, source_kind):
    return make_document(
        source_kind=source_kind,
        content_type="api_surface" if source_kind == SourceKind.SELF_EXTRACTED else "prose",
        provenance=Provenance(repository="holdout/oracle", commit="0" * 40, path=title),
        title=title,
        body=body,
    )


def _publish(store, scope, source_kind_str, chunks):
    key = "::".join((scope.family, scope.platform, source_kind_str))
    lease = store.acquire_lease(key, "holdout", "pending")
    return publish_generation(
        store,
        family=scope.family,
        platform=scope.platform,
        source_kind=source_kind_str,
        expected_active=None,
        chunks=chunks,
        embedding_provider=_Provider(),
        lease=lease,
    )


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """Two real products in one store - the only way cross-scope leakage is visible."""
    st = GenerationManifestStore(tmp_path_factory.mktemp("m") / "manifests")

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pdf_sections = [
        f"## {e.get('class_import') or e.get('name', '')}\n\n"
        f"{e.get('class_import') or e.get('name', '')} is a {e.get('kind', '')}."
        for e in fixture["types"][:20]
    ]
    _publish(st, PDF_NET, "self_extracted",
             chunk_document(_doc("pdf surface", "\n\n".join(pdf_sections), SourceKind.SELF_EXTRACTED)))

    cells_sections = [
        f"## {CELLS_ONLY_TERM}\n\n{CELLS_ONLY_TERM} is a class in the cells python surface.",
        "## Workbook\n\nWorkbook is a class in the cells python surface.",
    ]
    _publish(st, CELLS_PY, "self_extracted",
             chunk_document(_doc("cells surface", "\n\n".join(cells_sections), SourceKind.SELF_EXTRACTED)))

    # Furnished prose for pdf/net only, one category each so partitioning is real.
    # Category keywords sit in the BODY on purpose: chunking moves a heading into
    # section_title and the lexical index stores chunk.text alone, so a section
    # headed 'Troubleshooting' whose body never says so classifies as
    # developer_guide. Measured against the real furnished page that changes
    # nothing - 0 of 8 sections shift - so it is a latent limitation recorded in
    # DECISION_LOG, not grounds to fail this card.
    prose = (
        "## Getting Started\n\nInstall the package and open your first document to get started.\n\n"
        "## Troubleshooting\n\nTo troubleshoot a PdfCorruptedException, check the file header; "
        "this common error means the document could not be parsed.\n"
    )
    _publish(st, PDF_NET, "furnished",
             chunk_document(_doc("pdf prose", prose, SourceKind.FURNISHED)))
    return st


def _is_miss(result):
    return isinstance(result, (sd.Miss, ss.Miss))


def _texts(result):
    """Rendered RESULTS only.

    A Miss deliberately echoes the query back so a caller can see what was asked
    - that echo is not leakage, and asserting the term is absent from a Miss's
    repr flags correct behaviour as a defect. Leakage can only live in returned
    matches.
    """
    return " ".join(repr(r) for r in result) if isinstance(result, list) else ""


# ------------------------------------------------------------ anti-vacuity first
def test_the_tools_actually_find_things(store):
    """Everything below is worthless if these tools just always miss."""
    hit = ss.search_symbols(store, PDF_NET, PDF_ONLY_TERM)
    assert isinstance(hit, list) and hit, f"search_symbols found nothing for a term that is present: {hit}"

    doc = sd.search_docs(store, PDF_NET, "install", "getting_started")
    assert isinstance(doc, list) and doc, f"search_docs found nothing in a populated category: {doc}"


# ------------------------------------------------------------- cross-scope leaks
def test_a_pdf_query_never_returns_cells_content(store):
    """The isolation property. A single-product fixture cannot see this at all."""
    result = ss.search_symbols(store, PDF_NET, CELLS_ONLY_TERM)
    assert _is_miss(result), (
        f"pdf/net returned a result for a symbol that exists only in cells/python: {_texts(result)}"
    )
    assert CELLS_ONLY_TERM not in _texts(result)


def test_a_cells_query_never_returns_pdf_content(store):
    """Leakage is directional - prove it both ways."""
    result = ss.search_symbols(store, CELLS_PY, PDF_ONLY_TERM)
    assert _is_miss(result), (
        f"cells/python returned a result that exists only in pdf/net: {_texts(result)}"
    )
    assert PDF_ONLY_TERM not in _texts(result)


def test_lookup_does_not_cross_scope_either(store):
    """lookup tries several categories, so it is the most likely place for a
    fallback to quietly reach past the scope it was given."""
    result = lookup(store, PDF_NET, CELLS_ONLY_TERM)
    assert _is_miss(result), f"lookup leaked across scope: {_texts(result)}"
    assert CELLS_ONLY_TERM not in _texts(result)


def test_a_scope_with_nothing_published_misses_rather_than_borrowing(store):
    """The reference system substituted whatever snapshot was active. An
    unpublished scope must be a miss, not somebody else's data."""
    empty = Scope(family="slides", platform="java")
    result = ss.search_symbols(store, empty, PDF_ONLY_TERM)
    assert _is_miss(result), f"an unpublished scope borrowed another product's generation: {_texts(result)}"


# --------------------------------------------------- explicit filters are honoured
def test_an_explicit_content_type_is_never_widened_past(store):
    """THE defect, stated precisely: the reference system retried without the
    filter even when the filter was explicitly provided."""
    result = sd.search_docs(store, PDF_NET, "PdfCorruptedException", "getting_started")
    assert _is_miss(result), (
        f"a term present only under troubleshooting was returned for an explicit "
        f"content_type=getting_started: {_texts(result)}"
    )
    assert "PdfCorruptedException" not in _texts(result)

    # ...and it really is findable under its own category, so the miss above is a
    # filter being honoured rather than the corpus being empty.
    found = sd.search_docs(store, PDF_NET, "PdfCorruptedException", "troubleshooting")
    assert isinstance(found, list) and found, "the term is not findable under its own content_type either"


def test_lookup_with_an_explicit_content_type_delegates_and_does_not_fall_back(store):
    """The precise boundary: breadth is allowed only when no filter was given."""
    result = lookup(store, PDF_NET, "PdfCorruptedException", content_type="getting_started")
    assert _is_miss(result), (
        f"lookup widened past an explicitly supplied content_type: {_texts(result)}"
    )


def test_lookup_without_a_filter_may_still_search_broadly(store):
    """Anti-vacuity for the rule itself: 'no widening' must not have been
    implemented as 'never search more than one place', or lookup is pointless."""
    result = lookup(store, PDF_NET, "PdfCorruptedException")
    assert isinstance(result, list) and result, (
        "lookup with no content_type found nothing; documented dispatch across categories is "
        "allowed and expected when the caller supplied no filter"
    )
