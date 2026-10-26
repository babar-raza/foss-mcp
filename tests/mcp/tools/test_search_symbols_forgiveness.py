"""G3/TC-516 (REQ-G3-008): search_symbols ranks declaring types first and has a gated per-token fallback.

Review finding RST-14: on pdf/go ``search_symbols("SetPassword")`` ranked ``Document``, the type that
declares the useful method, behind six field types that merely INHERIT ``SetPassword(v: bool)`` from
``TextBoxField``; and "Workbook methods" found nothing on cells/net although the type exists. The fallback is
gated (the query must contain whitespace) and adds no prefix tier: ``tests/mcp/test_search_symbols_honest_miss.py``
(unchanged) pins "AFRelation" and "Watermar" as honest misses. Offline: real pilots are COPIED to a temporary
directory, synthetic generations are built in memory.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
PDF_GO = Scope(family="pdf", platform="go")
CELLS_NET = Scope(family="cells", platform="net")
SYNTHETIC_SCOPE = Scope(family="sample", platform="python")
_GENERATION_ID = "gen-tc516-symbols"

DECLARING = {"TextBoxField", "Document"}
INHERITING = {
    "RichTextBoxField",
    "NumberField",
    "DateField",
    "FileSelectBoxField",
    "BarcodeField",
    "PasswordBoxField",
}


def _copied_store(tmp_path_factory: pytest.TempPathFactory, directory: str) -> GenerationManifestStore:
    root = tmp_path_factory.mktemp(f"symbols_forgiveness_{directory}")
    shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
    return GenerationManifestStore(root)


@pytest.fixture(scope="module")
def pdf_go_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    return _copied_store(tmp_path_factory, "pdf__go__self_extracted")


@pytest.fixture(scope="module")
def cells_net_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    return _copied_store(tmp_path_factory, "cells__net__self_extracted")


class _InMemoryStore:
    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _type_chunk(fqn: str, *members: str) -> str:
    lines = [f"FQN: {fqn}", "Kind: class_declaration"]
    if members:
        lines.append("Methods:")
        lines.extend(f"  - {member}" for member in members)
    return "\n".join(lines) + "\n"


def _names(result: object) -> list[str]:
    assert isinstance(result, list), f"expected symbols, got {result!r}"
    assert all(isinstance(item, SymbolMatch) for item in result)
    return [(extract_fqn(item.text) or "").split(".")[-1] for item in result]


def _search(store: object, query: str, scope: Scope = SYNTHETIC_SCOPE) -> object:
    return search_symbols(store, scope, query)  # type: ignore[arg-type]


# ---- declaring types before inheriting types ---------------------------------------------------------


def test_set_password_ranks_the_declaring_types_before_the_inheriting_ones_on_pdf_go(
    pdf_go_store: GenerationManifestStore,
) -> None:
    names = _names(search_symbols(pdf_go_store, PDF_GO, "SetPassword"))

    assert set(names) == DECLARING | INHERITING
    assert names.index("Document") < 2
    last_declaring = max(names.index(name) for name in DECLARING)
    assert all(names.index(name) > last_declaring for name in INHERITING), names


def test_a_declaring_type_outranks_inheriting_types_with_a_better_bm25_score() -> None:
    store = _InMemoryStore(
        _type_chunk("Pkg.Base", "Reset()"),
        _type_chunk("Pkg.Child", "Reset()  (inherited from Base)"),
        _type_chunk("Pkg.Other", "Reset()  (inherited from Base)", "Other filler words here"),
    )

    assert _names(_search(store, "Reset"))[0] == "Base"


# ---- the gated per-token fallback --------------------------------------------------------------------


def test_plural_workbooks_finds_workbook(cells_net_store: GenerationManifestStore) -> None:
    # A plural is read as the type (the query has whitespace: the fallback is gated on it).
    assert _names(search_symbols(cells_net_store, CELLS_NET, "workbooks methods"))[0] == "Workbook"


def test_workbook_methods_finds_workbook_by_token(cells_net_store: GenerationManifestStore) -> None:
    result = search_symbols(cells_net_store, CELLS_NET, "Workbook methods")

    assert _names(result)[0] == "Workbook"
    # The exact bare name was and is the strongest evidence.
    assert _names(search_symbols(cells_net_store, CELLS_NET, "Workbook"))[0] == "Workbook"


def test_send_email_finds_the_type_that_declares_the_member() -> None:
    store = _InMemoryStore(
        _type_chunk("Sample.Email", "Send()"),
        _type_chunk("Sample.Mailbox", "Open()"),
    )

    assert _names(_search(store, "send email")) == ["Email"]


def test_most_tokens_must_match_for_the_fallback_to_admit_a_type() -> None:
    store = _InMemoryStore(_type_chunk("Sample.Email", "Send()"))

    assert isinstance(_search(store, "send email teleport sandwich"), Miss)
    assert _names(_search(store, "send email please")) == ["Email"]


def test_dotted_unknown_member_stays_a_miss() -> None:
    store = _InMemoryStore(_type_chunk("Pdf.PdfDocument", "Merge()"))

    for query in ("PdfDocument.Rotate", "PdfDocument::Rotate"):
        assert isinstance(_search(store, query), Miss), query
    # With whitespace the member must still be a member the type declares.
    assert isinstance(_search(store, "PdfDocument Rotate"), Miss)


def test_partial_words_stay_misses() -> None:
    store = _InMemoryStore(
        _type_chunk("Aspose.Pdf.AFRelationship"),
        _type_chunk("Pdf.PdfDocument", "AddWatermarkAnnotation()"),
    )

    for query in ("Watermar", "AFRelation", "Watermar annotation", "AFRelation type"):
        result = _search(store, query)
        assert isinstance(result, Miss), (query, result)
        assert result.reason == f"no symbol matches {query!r}"


def test_the_fallback_only_runs_for_queries_with_whitespace() -> None:
    store = _InMemoryStore(_type_chunk("Pkg.Widget", "Run()"))

    assert _names(_search(store, "Widget methods")) == ["Widget"]
    # TC-562: Miss -> Widget. A plural of a whole type name is that type (plural normalisation); a dotted or
    # underscored token, a partial word and a sentence that explains nothing are still honest misses.
    assert _names(_search(store, "Widgets")) == ["Widget"]
    assert isinstance(_search(store, "Widgetz"), Miss)
    assert isinstance(_search(store, "Widget.methods"), Miss)
    assert isinstance(_search(store, "Widget_methods"), Miss)


def test_the_miss_keeps_its_reason_suggestions_and_coverage_note() -> None:
    store = _InMemoryStore(_type_chunk("Pkg.Widget", "Run()"))

    result = _search(store, "teleport sandwich")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'teleport sandwich'"
    assert result.suggestions == ()
    assert result.coverage_note is None


# ---- TC-585 (G3/REQ-G3-009): a per-family ubiquitous word no longer buries the correct answer ---------
#
# Independent reproduction (RST round-2, RD-5) found three real patterns: "file" (pdf), a literal
# type-name match, buries "Document" (which never shares the word); "color" (slides) likewise buries
# "FillFormat"/"Background"; and a type whose own name merely STEMS like the family's word ("Cell" for
# "cells") collects a free fit bonus (the family-stem light-term rescue) that lets it outscore a type
# that genuinely declares the member asked for. Measuring every fix against the full real retrieval
# corpus (``test_round3_retrieval_corpus.py``) found that letting a per-family-generic word ALSO exclude
# itself as a REQUIRED concept in ``_evidence_ranking`` regressed unrelated families whose own most
# common words ("document", "page", "shape", "text") are also their most frequent correct answers; the
# two fixes below are what measured safe without any such regression (hit@3 and wrong_conf numbers are
# in this card's own worker report).

WIDGETS_SCOPE = Scope(family="widgets", platform="python")


def _widgets_payload(documents: list[list[str]]) -> dict:
    """A minimal lexical payload shaped like ``build_lexical_index``'s, for ``_safe_extra_generic`` /
    ``family_generic_stems`` tested as pure functions - its "documents" carry no ``FQN:`` line, so the
    real-bare-type-name protection never engages (irrelevant to what these tests measure)."""
    doc_freq: dict[str, int] = {}
    for tokens in documents:
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1
    return {
        "documents": {f"doc-{index}": {"text": ""} for index in range(len(documents))},
        "doc_freq": doc_freq,
    }


def test_a_word_ubiquitous_for_one_family_is_dropped_from_the_fallback_stage_there_only() -> None:
    from foss_mcp.mcp.tools.search_symbols import _safe_extra_generic, naming_tokens

    # "file" on pdf (TC-585's own reproduction): present in 18 of 20 of pdf's own indexed chunks (well
    # above both FAMILY_UBIQUITY_FRACTION and MIN_SCOPE_DOCUMENTS_FOR_UBIQUITY); "open" itself stays
    # rare (one chunk) so it is never swept up by the same measurement.
    pdf_payload = _widgets_payload([["file"]] * 18 + [["open"]] * 2)
    pdf_extra = _safe_extra_generic(["open", "a", "file"], "pdf", pdf_payload)
    assert pdf_extra == frozenset({"file"})
    assert naming_tokens("open a file", "pdf", extra_generic=pdf_extra) == ["open"]

    # The SAME word, genuinely rare on a different family: additive, per scope, never global.
    other_payload = _widgets_payload([["unrelated"]] * 19 + [["open", "file"]])
    other_extra = _safe_extra_generic(["open", "a", "file"], "other", other_payload)
    assert "file" not in other_extra
    assert naming_tokens("open a file", "other", extra_generic=other_extra) == ["open", "file"]


def test_excluding_every_ubiquitous_candidate_at_once_is_refused_when_one_was_already_enough() -> None:
    from foss_mcp.mcp.tools.search_symbols import _safe_extra_generic

    # "cell", "workbook" and "value" (TC-585's own reproduction, "change a workbook cell value") are all
    # ubiquitous for this synthetic family; excluding all three would leave no real word for the fallback
    # stage's own naming_tokens to match against at all.
    payload = _widgets_payload([["cell", "workbook", "value"]] * 19 + [["unrelated"]])
    extra = _safe_extra_generic(["change", "a", "workbook", "cell", "value"], "cells", payload)
    assert extra, "at least the family's own ubiquitous word should still be excluded"
    assert extra != {"cell", "workbook", "value"}, "excluding every candidate loses the whole question"


def test_a_real_bare_type_name_is_never_treated_as_family_generic() -> None:
    from foss_mcp.mcp.tools.search_symbols import _safe_extra_generic

    # "Document" is ubiquitous AND a real, central type (TC-585's own measurement against the full
    # corpus: excluding it broke "create a new word document" on multiple platforms) - never excluded,
    # however high its measured fraction.
    payload = {
        "documents": {
            "doc-document": {"text": _type_chunk("Pkg.Document", "Open()")},
            **{f"doc-filler-{i}": {"text": ""} for i in range(19)},
        },
        "doc_freq": {"open": 1, "document": 18, "file": 17},
    }
    assert _safe_extra_generic(["open", "a", "document"], "pdf", payload) == frozenset()


def test_a_type_merely_stemmed_like_the_family_word_is_not_rescued_by_a_synonym_only_match() -> None:
    """The fix for "wrap text in a cell" (cells): ``Widget`` here, like ``Cell``/``Cells`` there, STEMS
    like the family's own word ("widgets"); it explains "text" only through its OWN member's "string"
    synonym, never literally - the family-stem rescue must not then also credit it with a full
    NAME_FIT_WEIGHT bonus for a name nobody asked about, letting it outrank ``Panel``, whose own member
    ``WrapText`` genuinely, literally explains the whole question.
    """
    store = _InMemoryStore(
        _type_chunk("Pkg.Widget", "GetStringValue()"),
        _type_chunk("Pkg.Panel", "WrapText()"),
    )

    names = _names(_search(store, "wrap text in a widget", scope=WIDGETS_SCOPE))

    assert names == ["Panel"]


def test_the_family_stem_rescue_still_applies_when_the_name_is_genuinely_asked_for() -> None:
    """The gate above must not go too far: TC-562's own promotion rule ("add a slide" on the slides
    pilots) turns a lone family-stem word into a real, LITERALLY-explained concept when every other word
    of the query is a light verb - that case is untouched, so the type whose name was genuinely asked
    about still answers.
    """
    store = _InMemoryStore(
        _type_chunk("Pkg.Widget", "DoSomething()"),
        _type_chunk("Pkg.Other", "DoSomethingElse()"),
    )

    names = _names(_search(store, "add a widget", scope=WIDGETS_SCOPE))

    assert names[0] == "Widget"
