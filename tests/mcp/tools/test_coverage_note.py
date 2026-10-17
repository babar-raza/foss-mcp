"""TC-520 (REQ-G3-005, REQ-G3-008): one helper states the API-coverage caveat everywhere.

``coverage_note_from_text`` turns a symbol chunk's ``API-Coverage:`` line into the sentence a miss
(``get_symbol``, ``list_members``) and ``report_index_freshness`` give. Offline: the stores are in
memory, built from the real chunk builder over the committed ``tex_python`` fixture (35 of its 45
package files do not parse at the pinned commit) and over small synthetic fixtures.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import get_symbol as get_symbol_module
from foss_mcp.mcp.tools import report_index_freshness as freshness_module
from foss_mcp.mcp.tools.coverage_note import ApiCoverage, coverage_note_from_text, parse_api_coverage
from foss_mcp.mcp.tools.get_symbol import NotFound, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from foss_mcp.mcp.tools.search_symbols import SOURCE_KIND

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SCOPE = Scope(family="tex", platform="python")
_GENERATION_ID = "gen-tc520"
_COMMIT = "181015d2eee3e19f8af3d84e4a932ac84ad47c7c"

LEGACY_SENTENCE = "the API index holds {p} of {t} types; a type missing from it may still exist upstream"


def _line(value: str) -> str:
    return f"FQN: A\nKind: class\n\nAPI-Coverage: {value}"


# ---------------------------------------------------------------- the helper


def test_a_partial_line_gives_the_exact_legacy_sentence() -> None:
    assert coverage_note_from_text(_line("300 of 901 types")) == LEGACY_SENTENCE.format(p=300, t=901)


def test_a_complete_line_gives_none() -> None:
    assert coverage_note_from_text(_line("42 of 42 types")) is None
    assert coverage_note_from_text(_line("42 of 42 types; 7 of 7 functions")) is None


def test_no_line_gives_none() -> None:
    assert coverage_note_from_text("FQN: A\nKind: class\n") is None
    assert coverage_note_from_text("") is None


def test_only_the_functions_partial_has_no_legacy_tail() -> None:
    note = coverage_note_from_text(_line("259 of 259 types; 45 of 2054 functions"))
    assert note == "the API index holds 259 of 259 types and 45 of 2054 functions"
    assert "may still exist upstream" not in (note or "")


def test_types_and_functions_both_partial() -> None:
    note = coverage_note_from_text(_line("216 of 259 types; 45 of 2054 functions"))
    assert note is not None
    assert note.startswith("the API index holds 216 of 259 types and 45 of 2054 functions")
    assert "may still exist upstream" in note


def test_the_unparsed_files_clause() -> None:
    note = coverage_note_from_text(_line("4 of 4 types; 35 of 45 source files could not be parsed"))
    assert note == (
        "35 of 45 source files could not be parsed, so the API index is incomplete and a type missing "
        "from it may still exist upstream (the Extraction notice documentation chunk lists the files)"
    )


def test_at_least_k_unparsed_files() -> None:
    note = coverage_note_from_text(_line("4 of 4 types; at least 2 source files could not be parsed"))
    assert note is not None
    assert note.startswith("at least 2 source files could not be parsed, so the API index is incomplete")
    assert "Extraction notice" in note


def test_partial_types_and_unparsed_files_are_joined() -> None:
    note = coverage_note_from_text(_line("300 of 901 types; 40 of 100 source files could not be parsed"))
    assert note is not None
    first, second = note.split("; 40 of 100 source files")
    assert first == LEGACY_SENTENCE.format(p=300, t=901)
    assert second.startswith(" could not be parsed, so the API index is incomplete")


def test_parse_api_coverage_reads_every_clause() -> None:
    parsed = parse_api_coverage(
        _line("216 of 259 types; 45 of 2054 functions; 3 of 9 source files could not be parsed")
    )
    assert parsed == ApiCoverage(216, 259, (45, 2054), "3 of 9 source files could not be parsed")
    assert parse_api_coverage(_line("1 of 2 types")) == ApiCoverage(1, 2, None, None)
    assert parse_api_coverage("no line here") is None


def test_only_the_first_coverage_line_counts() -> None:
    text = "API-Coverage: 1 of 2 types\nAPI-Coverage: 9 of 9 types\n"
    assert coverage_note_from_text(text) == LEGACY_SENTENCE.format(p=1, t=2)


# ---------------------------------------------------------------- the tools, on in-memory stores


class _Store:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, texts: list[str]) -> None:
        chunk_ids = [f"chunk-{index}" for index in range(len(texts))]
        chunks = [SimpleNamespace(text=text) for text in texts]
        lexical = build_lexical_index(chunks, chunk_ids, _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _texts(fixture: dict[str, object]) -> list[str]:
    commit = fixture["source_commit"]
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=10_000)
    return [
        dataclasses.replace(chunk, text=f"{chunk.text}\nSource-Commit: {commit}").text for chunk in chunks
    ]


def _tex_store() -> _Store:
    fixture = json.loads((FIXTURES / "tex_python" / "api_surface.json").read_text(encoding="utf-8"))
    return _Store(_texts(fixture))


def _complete_store() -> _Store:
    fixture = {
        "source_repository": "org/repo",
        "source_commit": _COMMIT,
        "type_count": 2,
        "types": [
            {"name": "Alpha", "kind": "class", "file": "a.py", "class_import": "pkg.Alpha"},
            {"name": "Beta", "kind": "class", "file": "b.py", "class_import": "pkg.Beta"},
        ],
    }
    return _Store(_texts(fixture))


def test_get_symbol_and_list_members_misses_state_the_unparsed_files() -> None:
    store = _tex_store()
    # TC-555: "aspose_tex.TeXJob" was the missing symbol; it is indexed now, so a name tex never had stands in.
    miss = get_symbol(store, SCOPE, "aspose_tex.NoSuchClass")  # type: ignore[arg-type]
    assert isinstance(miss, NotFound)
    assert miss.found is False
    assert miss.coverage_note is not None
    assert "could not be parsed" in miss.coverage_note
    assert "Extraction notice" in miss.coverage_note
    members = list_members(store, SCOPE, "aspose_tex.NoSuchClass")  # type: ignore[arg-type]
    assert isinstance(members, NotFound) and members.coverage_note == miss.coverage_note


def test_a_real_symbol_of_the_tex_store_is_still_answered() -> None:
    signature = get_symbol(_tex_store(), SCOPE, "aspose_tex.exceptions.AsposeTeXError")  # type: ignore[arg-type]
    assert isinstance(signature, SymbolSignature)
    assert signature.fqn == "aspose_tex.exceptions.AsposeTeXError"


def test_a_recovered_symbol_of_the_tex_store_is_answered_not_missed() -> None:
    # TC-555: TeXJob lived in presentation/__init__.py, which did not parse; it is recovered now.
    signature = get_symbol(_tex_store(), SCOPE, "aspose_tex.TeXJob")  # type: ignore[arg-type]
    assert isinstance(signature, SymbolSignature)
    assert signature.fqn == "aspose_tex.TeXJob"


def test_report_index_freshness_states_the_same_note() -> None:
    store = _tex_store()
    report = report_index_freshness(store, SCOPE, SOURCE_KIND, _COMMIT)  # type: ignore[arg-type]
    assert (report.indexed_types, report.total_types) == (21, 21)  # TC-555: (4, 4) -> (21, 21)
    assert report.coverage_note is not None
    assert "could not be parsed" in report.coverage_note
    assert "Extraction notice" in report.coverage_note
    miss = get_symbol(store, SCOPE, "aspose_tex.NoSuchClass")  # type: ignore[arg-type]
    assert isinstance(miss, NotFound) and miss.coverage_note == report.coverage_note


def test_a_complete_store_with_no_failure_has_no_note() -> None:
    store = _complete_store()
    report = report_index_freshness(store, SCOPE, SOURCE_KIND, _COMMIT)  # type: ignore[arg-type]
    assert (report.indexed_types, report.total_types) == (2, 2)
    assert report.coverage_note is None
    miss = get_symbol(store, SCOPE, "pkg.Gamma")  # type: ignore[arg-type]
    assert isinstance(miss, NotFound) and miss.coverage_note is None


@pytest.mark.parametrize("module", [get_symbol_module, freshness_module])
def test_both_tools_import_the_one_helper(module: ModuleType) -> None:
    """AGENTS.md: a helper nobody calls is not a fix. The committed source of each tool names it."""
    source = Path(str(module.__file__)).read_text(encoding="utf-8")
    assert "coverage_note_from_text" in source
    assert "from foss_mcp.mcp.tools.coverage_note import" in source
