"""G3/TC-799 (REQ-G3-011): did-you-mean ranking (RD2-023/067/090) and camel-fragment prefixes (RD2-161).

Two defects of ``search_symbols.py``, reproduced against the real committed pilots first:

* ``suggest_similar_fqns`` (shared by ``search_symbols`` and ``get_symbol`` misses) tried a fuzzy
  whole-FQN comparison first and returned it whenever it was non-empty, so the real type whose FINAL
  segment equals the requested one (``aspose.cells_foss.conditional_format.ConditionalFormat`` for
  ``aspose.cells_foss.ConditionalFormat``, ``Aspose.ThreeD.Property`` for
  ``Aspose.ThreeD.Utilities.Property``) was missing or ranked behind unrelated near-spellings; and a bare
  member name (``ExportToCSV``) never suggested ``Workbook.ExportToCSV``.
* the prefix tier demanded EVERY camel word of a fragment be at least 3 characters, so ``TableCe``,
  ``NoteT``, ``PageIn`` and ``TextFr`` missed while ``TableCel`` and ``TextFrag`` hit.

Offline and deterministic: real pilots are COPIED into a temporary directory (the repository is never
written) and the embedding gateway is stubbed out.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import NotFound, extract_fqn, get_symbol
from foss_mcp.mcp.tools.search_symbols import (
    Miss,
    SymbolMatch,
    member_suggestion_index,
    scope_key,
    search_symbols,
    suggest_similar_fqns,
)
from tests.mcp.tools.test_round3_retrieval_corpus import DEMO_MANIFESTS

_NON_SYMBOL_FQN_PREFIXES = ("Example: ", "Doc: ")
_GENERATION_ID = "gen-tc799"


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV

    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


class _Pilot:
    def __init__(self, root: Path, name: str) -> None:
        family, platform = name.split("__")
        self.scope = Scope(family=family, platform=platform, source_kind="self_extracted")
        self.store = GenerationManifestStore(root)
        key = scope_key(self.scope, "self_extracted")
        generation = self.store.read_active(key)
        assert generation is not None
        self.documents = self.store.read_generation(key, generation).payload["lexical_index"]["documents"]
        self.known = [
            fqn
            for fqn in (extract_fqn(document["text"]) for document in self.documents.values())
            if fqn is not None and not fqn.startswith(_NON_SYMBOL_FQN_PREFIXES)
        ]

    def search(self, query: str) -> list[SymbolMatch] | Miss:
        return search_symbols(self.store, self.scope, query, top_k=10)

    def hit_fqns(self, query: str) -> list[str]:
        result = self.search(query)
        assert isinstance(result, list), f"{query!r} should hit, got {result!r}"
        return [extract_fqn(match.text) or "" for match in result]


@pytest.fixture(scope="module")
def pilots(tmp_path_factory: pytest.TempPathFactory) -> dict[str, _Pilot]:
    root = tmp_path_factory.mktemp("tc799-pilots")
    names = ["3d__net", "cells__go", "cells__python", "note__python", "pdf__net", "cells__net"]
    for name in names:
        shutil.copytree(DEMO_MANIFESTS / f"{name}__self_extracted", root / f"{name}__self_extracted")
    return {name: _Pilot(root, name) for name in names}


# ---- RD2-023 / RD2-090: equal final segment first ----------------------------------------------------


def test_equal_final_segment_ranks_first_in_a_synthetic_list() -> None:
    known = [
        "Aspose.ThreeD.Utilities.Vertex",
        "Aspose.ThreeD.Utilities.Rect",
        "Aspose.ThreeD.Utilities.Vector4",
        "Aspose.ThreeD.Property",
    ]

    assert suggest_similar_fqns(known, "Aspose.ThreeD.Utilities.Property")[0] == "Aspose.ThreeD.Property"


def test_equal_final_segment_is_case_sensitive_first_then_case_insensitive() -> None:
    known = ["pkg.property", "pkg.Property", "pkg.Propertyy"]

    assert suggest_similar_fqns(known, "other.Property")[:2] == ("pkg.Property", "pkg.property")


def test_equal_final_segment_candidates_precede_the_fuzzy_whole_fqn_matches() -> None:
    known = ["Aspose.Cells.Style", "Aspose.Cells.Styles", "Aspose.Cells.Drawing.Chart.Style"]

    assert suggest_similar_fqns(known, "Aspose.Cells.Chart.Style")[:2] == (
        "Aspose.Cells.Drawing.Chart.Style",
        "Aspose.Cells.Style",
    )


def test_real_3d_net_property_suggests_the_real_type_first(pilots: dict[str, _Pilot]) -> None:
    pilot = pilots["3d__net"]

    assert (
        suggest_similar_fqns(pilot.known, "Aspose.ThreeD.Utilities.Property")[0] == "Aspose.ThreeD.Property"
    )


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("aspose.cells_foss.ConditionalFormat", "aspose.cells_foss.conditional_format.ConditionalFormat"),
        ("aspose.cells_foss.Hyperlink", "aspose.cells_foss.hyperlink.Hyperlink"),
        ("aspose.cells_foss.AutoFilter", "aspose.cells_foss.auto_filter.AutoFilter"),
    ],
)
def test_real_cells_python_suggests_the_real_type_first(
    pilots: dict[str, _Pilot], typed: str, expected: str
) -> None:
    pilot = pilots["cells__python"]

    assert suggest_similar_fqns(pilot.known, typed)[0] == expected


def test_a_search_symbols_miss_carries_the_equal_final_segment_suggestion_first(
    pilots: dict[str, _Pilot],
) -> None:
    pilot = pilots["3d__net"]

    result = pilot.search("Aspose.ThreeD.Utilities.Property")

    assert isinstance(result, Miss)
    assert result.suggestions[0] == "Aspose.ThreeD.Property"


# ---- RD2-067: a bare member name suggests Owner.member -----------------------------------------------


def _member_chunk(fqn: str, *methods: str) -> dict[str, str]:
    lines = [f"FQN: {fqn}", "Kind: class_declaration", "Methods:"]
    lines.extend(f"  - {method}" for method in methods)
    return {"text": "\n".join(lines) + "\n"}


def test_member_suggestion_index_lists_declaring_owners_only_and_sorted() -> None:
    documents = {
        "a": _member_chunk("Zeta.Sheet", "Dispose() -> void"),
        "b": _member_chunk("Alpha.Book", "Dispose() -> void", "Save(path: str) -> void"),
        "c": _member_chunk("Mid.Child", "Dispose() -> void (inherited from Alpha.Book)"),
        "d": {"text": "FQN: Example: Dispose\nMethods:\n  - Dispose() -> void\n"},
    }

    index = member_suggestion_index(documents)

    assert index["dispose"] == ("Alpha.Book.Dispose", "Zeta.Sheet.Dispose")
    assert index["save"] == ("Alpha.Book.Save",)


def test_bare_member_name_suggests_owner_dot_member_in_a_synthetic_index() -> None:
    documents = {
        "a": _member_chunk("Lib.Workbook", "ExportToCSV(path: str) -> void"),
        "b": _member_chunk("Lib.Picture"),
    }
    known = ["Lib.Workbook", "Lib.Picture"]

    assert suggest_similar_fqns(known, "ExportToCSV", member_owners=member_suggestion_index(documents)) == (
        "Lib.Workbook.ExportToCSV",
    )
    # without the index nothing is known about members: the old behaviour, unchanged
    assert suggest_similar_fqns(known, "ExportToCSV") == ()


def test_a_dotted_target_never_gets_member_owner_suggestions() -> None:
    documents = {"a": _member_chunk("Lib.Workbook", "ExportToCSV(path: str) -> void")}

    result = suggest_similar_fqns(
        ["Lib.Workbook"], "Lib.Other.ExportToCSV", member_owners=member_suggestion_index(documents)
    )

    assert "Lib.Workbook.ExportToCSV" not in result


@pytest.mark.parametrize(
    ("bare", "owner_member"),
    [
        ("ExportToCSV", "Workbook.ExportToCSV"),
        ("ImportFromCSV", "Workbook.ImportFromCSV"),
        ("ProcessRows", "StreamingReader.ProcessRows"),
        ("SetPassword", "Workbook.SetPassword"),
        ("AddPicture", "Worksheet.AddPicture"),
    ],
)
def test_real_cells_go_bare_member_suggests_its_owner(
    pilots: dict[str, _Pilot], bare: str, owner_member: str
) -> None:
    pilot = pilots["cells__go"]

    result = suggest_similar_fqns(pilot.known, bare, member_owners=member_suggestion_index(pilot.documents))

    assert result[0] == owner_member


@pytest.mark.parametrize(
    ("bare", "owner_member"),
    [
        ("ExportToCSV", "Workbook.ExportToCSV"),
        ("ImportFromCSV", "Workbook.ImportFromCSV"),
        ("ProcessRows", "StreamingReader.ProcessRows"),
        ("SetPassword", "Workbook.SetPassword"),
        ("AddPicture", "Worksheet.AddPicture"),
    ],
)
def test_get_symbol_bare_member_notfound_suggests_owner_member_on_real_cells_go(
    pilots: dict[str, _Pilot], bare: str, owner_member: str
) -> None:
    pilot = pilots["cells__go"]

    result = get_symbol(pilot.store, pilot.scope, bare)

    assert isinstance(result, NotFound)
    assert result.suggestions[0] == owner_member


# ---- determinism --------------------------------------------------------------------------------------


def test_suggestions_do_not_depend_on_input_order() -> None:
    known = [f"ns{n}.Property" for n in range(8)] + [f"ns{n}.Properties" for n in range(5)]
    documents = {f"d{n}": _member_chunk(f"Owner{n}", "Property() -> void") for n in range(6)}
    index = member_suggestion_index(documents)
    expected = suggest_similar_fqns(known, "Property", member_owners=index)
    expected_short = suggest_similar_fqns(known, "x.Property", limit=5)

    rng = random.Random(799)
    for _ in range(12):
        shuffled = list(known)
        rng.shuffle(shuffled)
        assert suggest_similar_fqns(shuffled, "Property", member_owners=index) == expected
        assert suggest_similar_fqns(shuffled, "x.Property", limit=5) == expected_short


def test_the_member_index_does_not_depend_on_document_order() -> None:
    documents = {f"d{n}": _member_chunk(f"Owner{n}", "Run() -> void") for n in range(6)}
    reordered = dict(reversed(list(documents.items())))

    assert member_suggestion_index(documents) == member_suggestion_index(reordered)


# ---- RD2-161: camel fragments ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pilot", "query", "expected_suffix"),
    [
        ("note__python", "TableCe", "TableCell"),
        ("note__python", "TableC", "TableCell"),
        ("note__python", "NoteT", "NoteTag"),
        ("note__python", "NoteTa", "NoteTag"),
        ("note__python", "RichTe", "RichText"),
        ("pdf__net", "PageIn", "Aspose.Pdf.PageInfo"),
        ("pdf__net", "TextFr", "Aspose.Pdf.Text.TextFragment"),
        ("pdf__net", "TextF", "Aspose.Pdf.Text.TextFragment"),
        ("cells__net", "WorksheetC", "WorksheetCollection"),
    ],
)
def test_a_short_trailing_camel_fragment_now_resolves(
    pilots: dict[str, _Pilot], pilot: str, query: str, expected_suffix: str
) -> None:
    fqns = pilots[pilot].hit_fqns(query)

    assert any(fqn.endswith(expected_suffix) for fqn in fqns), (query, fqns)


@pytest.mark.parametrize(
    ("pilot", "query"),
    [("note__python", "TableCel"), ("pdf__net", "TextFrag"), ("note__python", "Tab")],
)
def test_the_fragments_that_already_hit_still_hit(pilots: dict[str, _Pilot], pilot: str, query: str) -> None:
    assert pilots[pilot].hit_fqns(query)


def test_a_fragment_hit_is_partial_and_never_a_namespace_word_collision(pilots: dict[str, _Pilot]) -> None:
    result = pilots["note__python"].search("NoteT")

    assert isinstance(result, list)
    assert all(match.match_quality == "partial" for match in result)
    # `note` is a namespace word of every aspose.note.* FQN: `NoteT` must not match `aspose.note.Title`
    assert [extract_fqn(match.text) for match in result] == ["aspose.note.model.NoteTag"]


@pytest.mark.parametrize("query", ["a", "Ta", "No", "Te"])
def test_a_query_shorter_than_three_characters_still_misses_honestly(
    pilots: dict[str, _Pilot], query: str
) -> None:
    result = pilots["note__python"].search(query)

    assert isinstance(result, Miss)
    assert result.reason == "query must be at least 3 characters to match a name fragment"


def test_a_spaced_query_does_not_get_the_one_letter_trailing_relaxation(pilots: dict[str, _Pilot]) -> None:
    # "table c" is words, not a name fragment: it keeps the per-word 3-character floor
    result = pilots["note__python"].search("table c")

    assert not isinstance(result, list) or not any(
        (extract_fqn(match.text) or "").endswith("TableCell") and match.match_kind == "prefix"
        for match in result
    )


def test_fragment_resolution_is_deterministic(pilots: dict[str, _Pilot]) -> None:
    first = pilots["pdf__net"].hit_fqns("TextFr")

    assert pilots["pdf__net"].hit_fqns("TextFr") == first


def test_in_memory_generation_fragment_and_floor() -> None:
    chunks = [
        SimpleNamespace(text="FQN: Lib.TableCell\nKind: class_declaration\n"),
        SimpleNamespace(text="FQN: Lib.Title\nKind: class_declaration\n"),
    ]
    lexical = build_lexical_index(chunks, ["c0", "c1"], _GENERATION_ID)  # type: ignore[arg-type]

    class _Store:
        def read_active(self, scope: str) -> str:
            return _GENERATION_ID

        def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
            return SimpleNamespace(payload={"lexical_index": lexical})

    scope = Scope(family="sample", platform="python")
    hit = search_symbols(_Store(), scope, "TableCe")  # type: ignore[arg-type]
    assert isinstance(hit, list)
    assert [extract_fqn(match.text) for match in hit] == ["Lib.TableCell"]
    assert isinstance(search_symbols(_Store(), scope, "Ta"), Miss)  # type: ignore[arg-type]
