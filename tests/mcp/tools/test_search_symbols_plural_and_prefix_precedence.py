"""G3/TC-838 (REQ-G3-011): a plural or inflected EXACT type name outranks prefix-only matches of longer names.

TC-799 let a whole single-token fragment match a type by PREFIX of its final segment, so ``workbooks`` (a prefix of
``workbooksettings``) began to admit ``WorkbookSettings`` and ``WorkbookSaveException`` as prefix hits. The
inflected-name stage that names ``Workbook`` for that plural only ran when nothing else was admitted, so ``Workbook``
was dropped from the answer entirely. Offline: real pilots are COPIED to a temporary directory (the repository is
never written) and the synthetic scopes are built in memory.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import lookup
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
CELLS_NET = Scope(family="cells", platform="net")
SLIDES_NET = Scope(family="slides", platform="net")
SYNTHETIC_SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc838-plural"


class _NoGatewayEmbeddingProvider:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)


def _pilot_store(tmp_path_factory: pytest.TempPathFactory, *pilots: str) -> GenerationManifestStore:
    root = tmp_path_factory.mktemp("plural_precedence")
    for pilot in pilots:
        shutil.copytree(DEMO_MANIFESTS / f"{pilot}__self_extracted", root / f"{pilot}__self_extracted")
    return GenerationManifestStore(root)


@pytest.fixture(scope="module")
def real_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    return _pilot_store(tmp_path_factory, "cells__net", "slides__net")


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


def _workbook_store() -> _InMemoryStore:
    return _InMemoryStore(
        _type_chunk("Aspose.Cells.WorkbookSettings", "Apply"),
        _type_chunk("Aspose.Cells.WorkbookSaveException", "Raise"),
        _type_chunk("Aspose.Cells.Workbook", "Open"),
    )


def _names(result: list[SymbolMatch] | Miss) -> list[str]:
    assert not isinstance(result, Miss), result
    return [(extract_fqn(match.text) or "").split(".")[-1] for match in result]


def _lookup_names(result: object) -> list[str]:
    assert not isinstance(result, Miss), result
    symbols = getattr(result, "symbols", result)
    return [(extract_fqn(match.text) or "").split(".")[-1] for match in symbols]  # type: ignore[attr-defined]


# ---- synthetic scope ------------------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["workbooks", "Workbooks", "WORKBOOKS"])
def test_a_plural_type_name_ranks_the_type_before_longer_prefix_matches(query: str) -> None:
    names = _names(search_symbols(_workbook_store(), SYNTHETIC_SCOPE, query))  # type: ignore[arg-type]

    assert names[0] == "Workbook", names


def test_the_plural_hit_is_labelled_an_inflected_exact_name_and_prefix_hits_stay_partial() -> None:
    result = search_symbols(_workbook_store(), SYNTHETIC_SCOPE, "workbooks")  # type: ignore[arg-type]

    assert not isinstance(result, Miss)
    by_name = {(extract_fqn(match.text) or "").split(".")[-1]: match for match in result}
    assert by_name["Workbook"].match_kind == "inflected_name"
    assert by_name["Workbook"].match_quality == "exact"
    assert {by_name["WorkbookSettings"].match_kind, by_name["WorkbookSaveException"].match_kind} == {"prefix"}
    assert by_name["WorkbookSettings"].match_quality == "partial"


def test_the_longer_prefix_matches_are_kept_after_the_plural_hit() -> None:
    names = _names(search_symbols(_workbook_store(), SYNTHETIC_SCOPE, "workbooks"))  # type: ignore[arg-type]

    assert set(names) == {"Workbook", "WorkbookSettings", "WorkbookSaveException"}


def test_the_singular_name_is_still_an_exact_final_segment_match() -> None:
    result = search_symbols(_workbook_store(), SYNTHETIC_SCOPE, "workbook")  # type: ignore[arg-type]

    assert not isinstance(result, Miss)
    assert _names(result)[0] == "Workbook"
    assert result[0].match_kind == "final_segment"


def test_a_genuine_prefix_still_hits_all_three_prefix_matches() -> None:
    result = search_symbols(_workbook_store(), SYNTHETIC_SCOPE, "Workbo")  # type: ignore[arg-type]

    assert not isinstance(result, Miss)
    assert set(_names(result)) == {"Workbook", "WorkbookSettings", "WorkbookSaveException"}
    assert {match.match_kind for match in result} == {"prefix"}


def test_a_plural_with_no_prefix_competitor_is_unchanged() -> None:
    store = _InMemoryStore(_type_chunk("Aspose.Cells.Style", "Apply"), _type_chunk("Aspose.Cells.Cell"))

    result = search_symbols(store, SYNTHETIC_SCOPE, "styles")  # type: ignore[arg-type]

    assert _names(result) == ["Style"]
    assert not isinstance(result, Miss)
    assert result[0].match_kind == "inflected_name"


def test_a_es_plural_names_the_type_before_prefix_matches() -> None:
    store = _InMemoryStore(
        _type_chunk("Aspose.Cells.Boxesque"),
        _type_chunk("Aspose.Cells.Box"),
    )

    names = _names(search_symbols(store, SYNTHETIC_SCOPE, "boxes"))  # type: ignore[arg-type]

    assert names[0] == "Box", names


def test_a_stronger_tier_hit_still_outranks_the_plural_name() -> None:
    # A type literally named for the plural is a final-segment (tier 1) match and must stay first.
    store = _InMemoryStore(
        _type_chunk("Aspose.Cells.WorkbookSettings"),
        _type_chunk("Aspose.Cells.Workbook"),
        _type_chunk("Aspose.Cells.Workbooks"),
    )

    result = search_symbols(store, SYNTHETIC_SCOPE, "workbooks")  # type: ignore[arg-type]

    assert _names(result)[0] == "Workbooks"
    assert not isinstance(result, Miss)
    assert result[0].match_kind == "final_segment"


# ---- real pilots ----------------------------------------------------------------------------------------


def test_workbooks_names_workbook_on_cells_net(real_store: GenerationManifestStore) -> None:
    names = _names(search_symbols(real_store, CELLS_NET, "workbooks"))

    assert names[0] == "Workbook", names
    assert {"WorkbookSettings", "WorkbookSaveException"} <= set(names)


def test_workbooks_through_lookup_names_workbook_on_cells_net(real_store: GenerationManifestStore) -> None:
    for query in ("workbooks", "Workbooks", "workbooks methods"):
        assert "Workbook" in _lookup_names(lookup(real_store, CELLS_NET, query))[:3], query


@pytest.mark.parametrize(
    ("scope", "query", "type_name"),
    [
        (CELLS_NET, "Workbooks", "Workbook"),
        (CELLS_NET, "AutoFilters", "AutoFilter"),
        (CELLS_NET, "styles", "Style"),
        (SLIDES_NET, "presentations", "Presentation"),
        (SLIDES_NET, "Tables", "Table"),
        (SLIDES_NET, "IShapes", "IShape"),
    ],
)
def test_other_plural_type_names_on_real_pilots_name_the_type(
    real_store: GenerationManifestStore, scope: Scope, query: str, type_name: str
) -> None:
    result = search_symbols(real_store, scope, query)

    assert type_name in _names(result)[:3], (query, _names(result))
