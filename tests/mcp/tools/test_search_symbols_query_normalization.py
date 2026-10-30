"""G3/TC-618 (REQ-G3-011): Q4 - wire Q1's ``relevance.normalize_query`` into ``search_symbols``.

A literal "Aspose.<family>" dotted product-name mention in a sentence query (a user naming the
product, not an exact symbol) used to defeat ``search_symbols._plain_words``'s "any dotted word
disables the whole evidence stage" rule, conflating a real exact-name query (``PdfDocument.Rotate``)
with an ordinary sentence that merely names the product. ``search_symbols`` now calls
``query = normalize_query(query, scope.family)`` immediately after the existing
``query = fold_query(query)`` line - the one functional change this card makes. Threading the
``platform`` parameter into ``relevance.evidence_terms`` or ``search_symbols.naming_tokens`` is
explicitly OUT OF SCOPE for this card (see its own ``purpose``).

Real-pilot measurements here read the real, committed ``infra/demo-manifests/pdf__net__self_extracted``
pilot, COPIED to a temporary directory (nothing in the repo is ever written to) - the same pattern
``tests/mcp/tools/test_search_symbols_forgiveness.py`` already uses. No mocks stand in for the real
pilot data; the only thing ever stubbed is ``relevance.normalize_query`` itself, back to identity, to
reconstruct the "before this card" baseline for an honest A/B comparison (TC-610 already proved
``normalize_query`` is a no-op for a query with no "Aspose.<family>" mention, so this stub changes
nothing else).

(c) the full existing search_symbols ratchet suite (``test_search_symbols_forgiveness.py``,
``test_search_symbols_memberless_last.py``, ``tests/conformance/test_symbol_self_retrieval.py``,
``test_round3_retrieval_corpus.py``) is proved UNCHANGED by this card not by duplicating its
assertions here, but by this card's own second check command, which runs every one of those files
UNMODIFIED alongside this new file in the same session - a pinned match, miss or tier value that
shifted anywhere in any of them would fail that command.

(d) platform-exclusion finding: no real case was found, while building this card's own measurements
below, where the scope's *platform* word (as opposed to its *family* word) mattered to
``_plain_words`` or the evidence stage - every dotted-mention case measured here turns on the
*family* word ("pdf") naming the product, never the *platform* word ("net"). Nothing is left for a
separate card; this is a clean negative finding, not a deferral.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import foss_mcp.mcp.tools.relevance as relevance
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, Miss, SymbolMatch, _plain_words, search_symbols

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
PDF_NET = Scope(family="pdf", platform="net")


@pytest.fixture(scope="module")
def pdf_net_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    root = tmp_path_factory.mktemp("search_symbols_query_normalization_pdf_net")
    shutil.copytree(DEMO_MANIFESTS / "pdf__net__self_extracted", root / "pdf__net__self_extracted")
    return GenerationManifestStore(root)


def _without_normalize_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs ``relevance.normalize_query`` back to identity, reconstructing the exact behaviour
    ``search_symbols`` had before this card's wiring (the local import inside ``search_symbols`` does
    a fresh attribute lookup on ``relevance`` every call, so patching the module attribute here is
    enough - no import-time binding to work around)."""
    monkeypatch.setattr(relevance, "normalize_query", lambda query, family: query)


# --- (a) a real dotted product-name mention revives the evidence stage -------------------------


# A real pdf/net sentence query: a user naming the product ("Aspose.PDF"), never an exact symbol.
DOTTED_MENTION_QUERY = "how can I add a watermark using Aspose.PDF"


def test_dotted_mention_query_disables_plain_words_before_normalization() -> None:
    # Confirms concretely, by calling the real _plain_words directly, that the ORIGINAL query is
    # exactly the case the card's purpose names: a dotted word present anywhere in the query disables
    # the whole evidence stage, regardless of how harmless that dotted word's surrounding sentence is.
    assert _plain_words(DOTTED_MENTION_QUERY) is None


def test_dotted_mention_query_was_a_real_miss_before_this_card(
    pdf_net_store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_normalize_query(monkeypatch)
    before = search_symbols(pdf_net_store, PDF_NET, DOTTED_MENTION_QUERY)  # type: ignore[arg-type]
    assert isinstance(before, Miss), before


def test_dotted_mention_query_now_reaches_the_real_evidence_stage_and_finds_a_real_answer(
    pdf_net_store: GenerationManifestStore,
) -> None:
    # No monkeypatch here: this is the real wired search_symbols, exactly as a caller reaches it.
    after = search_symbols(pdf_net_store, PDF_NET, DOTTED_MENTION_QUERY)  # type: ignore[arg-type]
    assert isinstance(after, list) and after, after
    fqns = [extract_fqn(match.text) for match in after]
    assert "Aspose.Pdf.Watermark" in fqns
    # The real top hit is a genuine, confident "exact" match - not a weak, partial scrape.
    assert after[0].match_quality == MATCH_EXACT
    assert extract_fqn(after[0].text) == "Aspose.Pdf.Watermark"


def test_dotted_mention_ab_comparison_is_miss_before_and_a_real_hit_after(
    pdf_net_store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one A/B comparison this card's own measurement rests on, in a single test: identical
    store, scope and query, the only variable being whether ``normalize_query`` runs for real or is
    stubbed back to identity."""
    after = search_symbols(pdf_net_store, PDF_NET, DOTTED_MENTION_QUERY)  # type: ignore[arg-type]
    _without_normalize_query(monkeypatch)
    before = search_symbols(pdf_net_store, PDF_NET, DOTTED_MENTION_QUERY)  # type: ignore[arg-type]

    assert isinstance(before, Miss)
    assert isinstance(after, list) and len(after) >= 1


# --- (b) a genuine exact-name dotted query is completely unaffected -----------------------------


# Identifier-style member-qualified queries ("PdfDocument.Rotate"-shaped) and, per TC-619's own fix,
# a FULL multi-segment FQN - the exact case TC-619 corrected (previously corrupted into a broken,
# space-separated prefix by normalize_query's own earlier bug).
EXACT_NAME_QUERIES = (
    "Document.Save",
    "Page.Rotate",
    "Aspose.Pdf.Watermark",
    "Aspose.Pdf.Annotations.Measure",
)


@pytest.mark.parametrize("query", EXACT_NAME_QUERIES)
def test_exact_name_dotted_query_disables_plain_words_both_before_and_after(query: str) -> None:
    # A real identifier-style/FQN query must still disable the evidence stage exactly as before -
    # normalize_query must never touch it, so _plain_words sees the same dotted word either way.
    assert _plain_words(query) is None
    assert _plain_words(relevance.normalize_query(query, PDF_NET.family)) is None


@pytest.mark.parametrize("query", EXACT_NAME_QUERIES)
def test_exact_name_dotted_query_result_is_byte_identical_before_and_after_this_card(
    pdf_net_store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    after = search_symbols(pdf_net_store, PDF_NET, query)  # type: ignore[arg-type]
    _without_normalize_query(monkeypatch)
    before = search_symbols(pdf_net_store, PDF_NET, query)  # type: ignore[arg-type]

    # SymbolMatch and Miss are both frozen dataclasses: structural equality is byte-identical equality.
    assert before == after, (query, before, after)


def test_exact_name_dotted_query_resolves_tier0_exact_on_the_real_wired_search_symbols(
    pdf_net_store: GenerationManifestStore,
) -> None:
    # The specific regression TC-619 fixed, proven end to end through THIS card's real wiring (not
    # just normalize_query's own unit tests): a full FQN still ranks itself exact at rank 1.
    result = search_symbols(pdf_net_store, PDF_NET, "Aspose.Pdf.Annotations.Measure")  # type: ignore[arg-type]
    assert isinstance(result, list) and result
    assert isinstance(result[0], SymbolMatch)
    assert extract_fqn(result[0].text) == "Aspose.Pdf.Annotations.Measure"
    assert result[0].match_quality == MATCH_EXACT
