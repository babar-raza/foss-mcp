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
    # The same single token without whitespace and without an exact name is an honest miss.
    assert isinstance(_search(store, "Widgets"), Miss)
    assert isinstance(_search(store, "Widget.methods"), Miss)
    assert isinstance(_search(store, "Widget_methods"), Miss)


def test_the_miss_keeps_its_reason_suggestions_and_coverage_note() -> None:
    store = _InMemoryStore(_type_chunk("Pkg.Widget", "Run()"))

    result = _search(store, "teleport sandwich")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'teleport sandwich'"
    assert result.suggestions == ()
    assert result.coverage_note is None
