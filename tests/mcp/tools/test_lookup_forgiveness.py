"""G3/TC-516 (REQ-G3-008): lookup includes the symbol(s) a query names, and a table alone is not an answer.

Review findings RST-02 and D-08: on cells/net ``lookup("how do I use Workbook")`` and ``lookup("Workbook
methods")`` were misses although ``lookup("Workbook")`` returns the symbol, and 267 task questions found only a
README table 13.5 percent of the time as their sole evidence. Offline: the real cells/net pilot is COPIED to a
temporary directory (the repository is never written) and the synthetic cases are built in memory.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools import search_docs as search_docs_module
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, _is_table_dominated, lookup
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
CELLS_NET = Scope(family="cells", platform="net")
SYNTHETIC_SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc516-lookup"


class _NoGatewayEmbeddingProvider:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)


@pytest.fixture(scope="module")
def cells_net_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    root = tmp_path_factory.mktemp("lookup_forgiveness_cells_net")
    shutil.copytree(DEMO_MANIFESTS / "cells__net__self_extracted", root / "cells__net__self_extracted")
    return GenerationManifestStore(root)


class _InMemoryStore:
    """One active generation whose chunks are exactly the given texts (a chunk is never split or merged)."""

    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _type_chunk(fqn: str, member: str = "Run") -> str:
    return f"FQN: {fqn}\nKind: class_declaration\nMethods:\n  - {member}() -> void\n"


def _doc_chunk(title: str, *body_lines: str) -> str:
    return "\n".join([f"FQN: Doc: {title}", *body_lines]) + "\n"


def _table_chunk(title: str, *terms: str) -> str:
    return _doc_chunk(title, *[f"| `{term}` | does something with {term} |" for term in terms])


def _symbol_names(result: object) -> list[str]:
    if isinstance(result, TaskAnswer):
        matches: list[SymbolMatch] = list(result.symbols)
    elif isinstance(result, list):
        matches = [item for item in result if isinstance(item, SymbolMatch)]
    else:
        return []
    return [(extract_fqn(match.text) or "").split(".")[-1] for match in matches]


# ---- the real cells/net pilot -----------------------------------------------------------------------


def test_how_do_i_use_workbook_returns_the_workbook_symbol_on_cells_net(
    cells_net_store: GenerationManifestStore,
) -> None:
    result = lookup(cells_net_store, CELLS_NET, "how do I use Workbook")

    assert not isinstance(result, Miss)
    assert "Workbook" in _symbol_names(result)[:3]


def test_workbook_methods_returns_the_workbook_symbol(cells_net_store: GenerationManifestStore) -> None:
    result = lookup(cells_net_store, CELLS_NET, "Workbook methods")

    assert not isinstance(result, Miss)
    assert "Workbook" in _symbol_names(result)[:3]


def test_a_plural_type_name_still_names_the_type(cells_net_store: GenerationManifestStore) -> None:
    for query in ("workbooks", "workbooks methods"):
        assert "Workbook" in _symbol_names(lookup(cells_net_store, CELLS_NET, query))[:3], query


def test_the_bare_name_is_unchanged(cells_net_store: GenerationManifestStore) -> None:
    result = lookup(cells_net_store, CELLS_NET, "Workbook")

    assert isinstance(result, list)
    assert _symbol_names(result)[0] == "Workbook"


def test_a_generic_lowercase_word_alone_adds_no_symbol(cells_net_store: GenerationManifestStore) -> None:
    result = lookup(cells_net_store, CELLS_NET, "create a pivot table")

    # TC-562: [] -> no table-named type. The question's two words are also the two words of ONE declared member
    # (``PivotTables``, a protection flag), so the types that declare it may be offered; what must never happen
    # is that the generic word "table" alone selects a type named for tables.
    assert not any("Table" in name for name in _symbol_names(result))


def test_a_generic_lowercase_word_alone_adds_no_symbol_in_memory() -> None:
    store = _InMemoryStore(
        _type_chunk("Aspose.Cells.Table"),
        _doc_chunk("Guide", "Create a pivot table by hand, then format the table."),
    )

    result = lookup(store, SYNTHETIC_SCOPE, "create a pivot table")  # type: ignore[arg-type]

    assert isinstance(result, TaskAnswer)
    assert result.doc_matches
    assert result.symbols == ()


# ---- TaskAnswer.symbols -----------------------------------------------------------------------------


def test_taskanswer_symbols_default_is_empty_and_additive() -> None:
    answer = TaskAnswer(scope=SYNTHETIC_SCOPE, doc_matches=(), example=None)

    assert answer.symbols == ()
    # TC-562: the fields are declared in the order a reader meets them (symbols, example, doc_matches) and the
    # answer says whether it is "exact" or "partial": ["scope", "doc_matches", "example", "symbols"] -> below.
    assert [field.name for field in dataclasses.fields(TaskAnswer)] == [
        "scope",
        "symbols",
        "example",
        "doc_matches",
        "match_quality",
        "unmatched_terms",
    ]


def test_a_task_answer_carries_the_named_symbols() -> None:
    store = _InMemoryStore(
        _type_chunk("Aspose.Cells.Workbook"),
        _doc_chunk("Guide", "A workbook holds the worksheets you load and save."),
    )

    result = lookup(store, SYNTHETIC_SCOPE, "how do I use workbook")  # type: ignore[arg-type]

    assert isinstance(result, TaskAnswer)
    assert result.doc_matches
    assert _symbol_names(result) == ["Workbook"]


def test_symbols_without_docs_are_returned_as_a_symbol_list() -> None:
    store = _InMemoryStore(_type_chunk("Aspose.Cells.Widget"), _type_chunk("Aspose.Cells.Gadget"))

    # "widgets" is not an exact symbol hit (search_symbols is an honest miss for it), no document or
    # example answers, but the query names the Widget type: lookup returns the symbol list, not a miss.
    result = lookup(store, SYNTHETIC_SCOPE, "widgets")  # type: ignore[arg-type]

    assert isinstance(result, list)
    assert all(isinstance(item, SymbolMatch) for item in result)
    assert _symbol_names(result) == ["Widget"]


def test_a_query_naming_no_type_with_nothing_found_stays_the_lookup_miss() -> None:
    store = _InMemoryStore(_type_chunk("Aspose.Cells.Widget"))

    result = lookup(store, SYNTHETIC_SCOPE, "how do I teleport a sandwich")  # type: ignore[arg-type]

    assert isinstance(result, Miss)
    assert result.reason.startswith("nothing in the indexed documentation, verified examples or symbols")


def test_most_of_the_distinctive_words_must_name_types() -> None:
    store = _InMemoryStore(
        _type_chunk("Aspose.Cells.Widget"),
        _type_chunk("Aspose.Cells.Gadget"),
        _doc_chunk("Guide", "Nothing relevant to the question is written down here."),
    )

    two_of_two = lookup(store, SYNTHETIC_SCOPE, "widgets gadgets")  # type: ignore[arg-type]
    one_of_three = lookup(store, SYNTHETIC_SCOPE, "widgets teleport sandwich")  # type: ignore[arg-type]

    assert _symbol_names(two_of_two) == ["Widget", "Gadget"]
    assert _symbol_names(one_of_three) == []


def test_at_most_three_symbols_are_carried() -> None:
    store = _InMemoryStore(
        *[_type_chunk(f"Aspose.Cells.{name}") for name in ("Widget", "Gadget", "Gizmo", "Doodad")],
        _doc_chunk("Guide", "widget gadget gizmo doodad are all mentioned in this paragraph"),
    )

    result = lookup(store, SYNTHETIC_SCOPE, "widgets gadgets gizmos doodads")  # type: ignore[arg-type]

    assert len(_symbol_names(result)) == 3


# ---- table-only evidence ----------------------------------------------------------------------------


def test_a_table_only_answer_needs_two_distinctive_terms() -> None:
    store = _InMemoryStore(_table_chunk("API Reference", "alphafoo", "betabar"))

    # One distinctive term ("alphafoo") clears the relevance floor, but a table that merely lists it is
    # not an answer: the chunk is dropped and lookup's miss stands.
    result = lookup(store, SYNTHETIC_SCOPE, "alphafoo")  # type: ignore[arg-type]

    assert isinstance(result, Miss)


def test_a_table_chunk_with_two_distinctive_terms_is_kept() -> None:
    store = _InMemoryStore(_table_chunk("API Reference", "alphafoo", "betabar"))

    result = lookup(store, SYNTHETIC_SCOPE, "alphafoo betabar")  # type: ignore[arg-type]

    assert isinstance(result, TaskAnswer)
    assert [extract_fqn(match.text) for match in result.doc_matches] == ["Doc: API Reference"]


def test_prose_with_one_distinctive_term_is_still_an_answer() -> None:
    store = _InMemoryStore(_doc_chunk("Guide", "Call alphafoo first.", "Then you are done."))

    result = lookup(store, SYNTHETIC_SCOPE, "alphafoo")  # type: ignore[arg-type]

    assert isinstance(result, TaskAnswer)
    assert result.doc_matches


def test_a_long_chunk_over_8000_characters_counts_as_table_dominated() -> None:
    long_prose = "alphafoo " + "word " * 1700
    assert len(long_prose) > 8000
    store = _InMemoryStore(_doc_chunk("Dump", long_prose))

    assert _is_table_dominated(_doc_chunk("Dump", long_prose))
    assert isinstance(lookup(store, SYNTHETIC_SCOPE, "alphafoo"), Miss)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("body", "dominated"),
    [
        (["| a | b |", "| 1 | 2 |"], True),
        (["- `Save` - writes the file", "- `Open` - reads the file"], True),
        (["- `Save`: writes the file", "Some prose line."], True),
        (["Plain prose.", "More plain prose.", "| one stray row |"], False),
        (["- a bullet without a backticked identifier", "- another"], False),
    ],
)
def test_table_dominated_means_half_the_body_lines_look_like_table_rows(
    body: list[str], dominated: bool
) -> None:
    assert _is_table_dominated(_doc_chunk("Title", *body)) is dominated


# ---- documentation search: a query with no distinctive term ------------------------------------------


def test_documentation_search_misses_a_query_with_no_distinctive_term() -> None:
    store = _InMemoryStore(_doc_chunk("Guide", "This pdf example shows a sample of how to use the library."))

    for query in ("Example:", "pdf examples", "how to use samples"):
        result = search_docs_module.search_docs(store, SYNTHETIC_SCOPE, query, "developer_guide")  # type: ignore[arg-type]
        assert isinstance(result, search_docs_module.Miss), query
        assert result.reason == f"no developer_guide documents match {query!r}"


def test_documentation_search_keeps_the_bare_family_word_query() -> None:
    store = _InMemoryStore(_doc_chunk("Guide", "This pdf guide explains the library."))

    result = search_docs_module.search_docs(store, SYNTHETIC_SCOPE, "pdf", "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, list)
    assert result
