"""G3/TC-670 (REQ-G3-011): find_examples's code anchor widened from "a query term that LOOKS
syntactically like an identifier" to "a query term that IS a real symbol name in this pilot's own
index" - closing the dominant share of round-2's #1 cross-cutting finding
(``docs/round2/HANDOFF.md``) that Q10 (TC-635, ``relevance.code_anchored``) left untouched.

Measured directly before writing any fix (reproduced here as
``test_required_terms_is_empty_for_every_plain_class_name``, the card's own instruction to see the
gap empirically before touching anything): ``relevance.required_terms`` returns an EMPTY set for
every plain word - "Workbook", "Document", "Scene", "watermark", "Box" - the overwhelming majority
of real class/concept names in this portfolio, so the old compound-identifier-only mechanism never
admitted a single one of them.

``relevance.known_symbol_names(documents)`` is the new, per-pilot, CASE-SENSITIVE lookup this card
adds: this scope's own real short symbol names, built fresh every call from the SAME
``lexical_index.documents`` dict ``search_symbols.py``'s own Miss-path already iterates for its own
``known_fqns`` list. It is additive beside the existing compound-identifier check
(``relevance.required_terms``), never instead of it, and the match against a query's own words is
deliberately case-sensitive: the common English word "workbook" must never be treated as naming the
real class "Workbook".

The real, primary proof (``test_real_plain_word_class_name_now_anchors_through_the_server_wrapper``)
runs against the real, committed ``infra/demo-manifests`` cells/net generation - NOT a synthetic
fixture - through ``foss_mcp.mcp.server``'s own ``_wrap_find_examples`` closure, the exact callable a
real deployed server registers as its ``find_examples`` tool. "Workbook" is confirmed, directly
against that real data, to be a real short symbol name (``Aspose.Cells_FOSS.Workbook`` and others)
before the query is ever run, and the resulting matches are confirmed to rest on the NEW code-anchor
path alone (neither ``title_anchored`` nor ``title_prefix_anchored`` would have admitted any of them).
A companion test proves the "before" side directly: disabling ``known_symbol_names`` the same way the
supervisor's own negative control does turns the identical query back into the honest miss it was on
``main`` before this card.

Offline and deterministic: the embedding gateway is always monkeypatched to a stand-in whose
``embed`` raises, as the standalone image behaves with no gateway configured; nothing in the real
repository is ever written to (``infra/demo-manifests`` is copied to a temp directory first).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import MATCH_BASIS_CODE_WORD, NoExampleFound
from foss_mcp.mcp.tools.relevance import (
    known_symbol_names,
    required_terms,
    title_anchored,
    title_prefix_anchored,
)
from foss_mcp.mcp.tools.search_symbols import scope_key

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

CELLS_NET = Scope(family="cells", platform="net")
PDF_NET = Scope(family="pdf", platform="net")
_EXAMPLE_FQN_PREFIX = "Example: "


class _NoGatewayEmbeddingProvider:
    """What the standalone image has when no embedding gateway is configured: embedding fails."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    """The real, committed ``infra/demo-manifests`` cells/net and pdf/net generations, copied to a
    temp directory - the repository itself is never written to."""
    root = tmp_path_factory.mktemp("tc670-symbol-aware")
    for name in ("cells__net__self_extracted", "pdf__net__self_extracted"):
        shutil.copytree(DEMO_MANIFESTS / name, root / name)
    return GenerationManifestStore(root)


def _documents(store: GenerationManifestStore, scope: Scope) -> dict:
    key = scope_key(scope, "self_extracted")
    generation_id = store.read_active(key)
    assert generation_id is not None
    return store.read_generation(key, generation_id).payload["lexical_index"]["documents"]


def _tool(store: GenerationManifestStore, scope: Scope):
    """The real server wrapper - ``foss_mcp.mcp.server._wrap_find_examples`` - not the bare
    ``find_examples`` function, so this module also proves the real server wiring."""
    return server_module._wrap_find_examples(store, scope)


# ---------------------------------------------------------------------------------------------
# (1) the gap itself, confirmed empirically, exactly as the card instructs
# ---------------------------------------------------------------------------------------------


def test_required_terms_is_empty_for_every_plain_class_name() -> None:
    for word in ("Workbook", "Document", "Scene", "watermark", "Box"):
        assert required_terms(word) == frozenset(), word


# ---------------------------------------------------------------------------------------------
# (2) relevance.known_symbol_names itself: a pure-function unit contract
# ---------------------------------------------------------------------------------------------


def test_known_symbol_names_short_name_for_dotted_fqn() -> None:
    documents = {"d1": {"text": "FQN: Aspose.Cells_FOSS.Workbook\nKind: class_declaration\n"}}
    assert known_symbol_names(documents) == frozenset({"Workbook"})


def test_known_symbol_names_short_name_for_double_colon_fqn() -> None:
    documents = {"d1": {"text": "FQN: Aspose::Pdf::Document\nKind: class_declaration\n"}}
    assert known_symbol_names(documents) == frozenset({"Document"})


def test_known_symbol_names_excludes_example_and_doc_pseudo_fqns_and_fqn_less_chunks() -> None:
    documents = {
        "d1": {"text": "FQN: Example: Save a Workbook\nKind: verified_example\nExample:\nworkbook.Save()\n"},
        "d2": {"text": "FQN: Doc: Installation\nSome prose about Workbook.\n"},
        "d3": {"text": "No FQN line at all, mentions Workbook."},
    }
    assert known_symbol_names(documents) == frozenset()


def test_known_symbol_names_preserves_case_never_folds_it() -> None:
    documents = {"d1": {"text": "FQN: Aspose.Cells_FOSS.Workbook\nKind: class_declaration\n"}}
    names = known_symbol_names(documents)
    assert "Workbook" in names
    assert "workbook" not in names


# ---------------------------------------------------------------------------------------------
# (3) the real, primary proof: a plain-word real class name now anchors, through the real server
#     wrapper, against real infra/demo-manifests data, where it did not before this card.
# ---------------------------------------------------------------------------------------------


def test_workbook_is_a_real_symbol_short_name_in_cells_net(store: GenerationManifestStore) -> None:
    """Verified FIRST, directly against the real data - never assumed."""
    documents = _documents(store, CELLS_NET)
    assert "Workbook" in known_symbol_names(documents)


def test_real_plain_word_class_name_now_anchors_through_the_server_wrapper(
    store: GenerationManifestStore,
) -> None:
    # TC-849: this was "Workbook" until cohort C2 regenerated cells/net. The regenerated examples carry the word
    # "workbook" (and, once TC-867 made the PDF-export examples publishable, "worksheet") in their own titles, so those
    # now answer through the title path as well; "Column" is a real class that still rests on the code-anchor path
    # alone (no example title holds the word, the lookup-disabled run below is an honest miss and the lower-case word
    # misses).
    assert "Column" in known_symbol_names(_documents(store, CELLS_NET))
    tool = _tool(store, CELLS_NET)
    result = tool(query="Column")

    assert isinstance(result, list) and result, f"expected real matches, got {result!r}"
    assert {match.match_basis for match in result} == {MATCH_BASIS_CODE_WORD}
    for match in result:
        title = match.fqn[len(_EXAMPLE_FQN_PREFIX) :]
        # Neither existing mechanism (title anchor, bounded prefix title match) admits any of
        # these - the admission rests on the new code-anchor path alone.
        assert title_anchored("Column", "cells", title) is False
        assert title_prefix_anchored("Column", "cells", title) is False
    # And the OLD compound-identifier mechanism alone would never have reached code_anchored for
    # this query at all - "Column" is a plain word, not an identifier-shaped run.
    assert required_terms("Column") == frozenset()


def test_before_this_card_the_identical_query_was_an_honest_miss(
    store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct proof of the "before" side: disabling the new lookup the same way the supervisor's own
    negative control does (``known_symbol_names`` always empty) turns the exact same query back into
    the honest ``NoExampleFound`` it was on ``main`` before this card - never a confident wrong guess."""
    monkeypatch.setattr(find_examples_module, "known_symbol_names", lambda documents: frozenset())
    tool = _tool(store, CELLS_NET)
    result = tool(query="Column")
    assert isinstance(result, NoExampleFound), result


# ---------------------------------------------------------------------------------------------
# (4) precision is not weakened: a plain word naming no real symbol still misses
# ---------------------------------------------------------------------------------------------


def test_a_plain_word_naming_no_real_symbol_still_misses(store: GenerationManifestStore) -> None:
    documents = _documents(store, CELLS_NET)
    assert "render" not in known_symbol_names(documents)
    assert "Render" not in known_symbol_names(documents)
    tool = _tool(store, CELLS_NET)
    result = tool(query="render")
    assert isinstance(result, NoExampleFound), result


def test_lowercase_column_the_common_word_does_not_anchor_on_the_real_class(
    store: GenerationManifestStore,
) -> None:
    """The design's own case-sensitivity choice: a query typed in lower case is the common English
    word, not the real class "Column" - it must not anchor through this mechanism."""
    tool = _tool(store, CELLS_NET)
    result = tool(query="column")
    assert isinstance(result, NoExampleFound), result


# ---------------------------------------------------------------------------------------------
# (5) per-pilot scoping: a symbol known only in a DIFFERENT pilot's own index must never anchor
# ---------------------------------------------------------------------------------------------


def test_symbol_known_only_in_a_different_pilot_does_not_anchor(
    store: GenerationManifestStore,
) -> None:
    pdf_documents = _documents(store, PDF_NET)
    assert "Workbook" not in known_symbol_names(pdf_documents)
    tool = _tool(store, PDF_NET)
    result = tool(query="Workbook")
    assert isinstance(result, NoExampleFound), result


# ---------------------------------------------------------------------------------------------
# (6) the existing Q10 compound-identifier path is completely unaffected
# ---------------------------------------------------------------------------------------------


def test_the_existing_q10_compound_identifier_path_still_works_unaffected(
    store: GenerationManifestStore,
) -> None:
    assert required_terms("PutValue") == frozenset({"putvalue"})
    tool = _tool(store, CELLS_NET)
    result = tool(query="PutValue")
    assert isinstance(result, list) and result, f"expected real matches, got {result!r}"
    assert {match.match_basis for match in result} == {MATCH_BASIS_CODE_WORD}


def test_disabling_the_new_lookup_leaves_the_q10_compound_path_untouched(
    store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the two mechanisms are independent: the negative control for THIS card's own
    addition must never also break Q10's own, separate, pre-existing mechanism."""
    monkeypatch.setattr(find_examples_module, "known_symbol_names", lambda documents: frozenset())
    tool = _tool(store, CELLS_NET)
    result = tool(query="PutValue")
    assert isinstance(result, list) and result, f"expected real matches, got {result!r}"


# ---------------------------------------------------------------------------------------------
# (7) the shared relevance.required_terms function itself is never widened by this card - only
#     find_examples.py's own call site is. search_docs.py also calls required_terms directly.
# ---------------------------------------------------------------------------------------------


def test_required_terms_itself_stays_empty_for_plain_words_after_this_card() -> None:
    assert required_terms("Workbook") == frozenset()
    assert required_terms("Document") == frozenset()
