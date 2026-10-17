"""G3/TC-515 (REQ-G3-008): a miss is a verdict about the index, and says how partial the index is.

12 pilots index at most 300 types. ``get_symbol`` and ``list_members`` misses already carried the
API-coverage sentence; ``search_symbols`` and ``lookup`` misses did not, and lookup's miss claimed
nothing in "the documentation" matched. Now every miss from a generation that exists carries the
note when the index is partial, and a ``search_docs`` miss names the content types that do hold
matches. Offline and deterministic: every pilot's committed scope directory is COPIED into a
temporary directory; the expected note is computed here, by regex, from the generation's own
``API-Coverage:`` line - never from the code under test.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.get_symbol import NotFound, get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.mcp.tools.lookup import lookup
from foss_mcp.mcp.tools.search_docs import Miss as DocsMiss
from foss_mcp.mcp.tools.search_docs import search_docs
from foss_mcp.mcp.tools.search_symbols import Miss, search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

# re-pinned by TC-551: 40 -> 32
MIN_PILOTS = 32
NONSENSE_TOKEN = "Tc515NoSuchSymbolProbe"
NONSENSE_SENTENCE = "send a fax via modem"

_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_COVERAGE_LINE = re.compile(r"^API-Coverage:\s*(\d+)\s+of\s+(\d+)\s+types", re.MULTILINE)
_FULL_COVERAGE_LINE = re.compile(r"^API-Coverage:.*$", re.MULTILINE)


def _discover_pilots() -> list[str]:
    return sorted(
        directory.name
        for directory in DEMO_MANIFESTS.iterdir()
        if directory.is_dir() and (directory / "active_pointer.json").is_file()
    )


PILOTS = _discover_pilots()


def _scope_of(directory: str) -> Scope:
    family, platform, source_kind = directory.split("__")
    return Scope(family=family, platform=platform, source_kind=source_kind)


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
def pilot_stores() -> dict[str, GenerationManifestStore]:
    return {}


def _store_for(
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    directory: str,
) -> GenerationManifestStore:
    if directory not in pilot_stores:
        root = tmp_path_factory.mktemp(f"misscov_{directory}")
        shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
        pilot_stores[directory] = GenerationManifestStore(root)
    return pilot_stores[directory]


def _stated_coverage(store: GenerationManifestStore, scope: Scope) -> tuple[int, int] | None:
    """(indexed, total) from the first symbol chunk that carries an ``API-Coverage:`` line."""
    key = "::".join((scope.family, scope.platform, scope.source_kind))
    generation_id = store.read_active(key)
    assert generation_id is not None
    documents = store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
    for document in documents.values():
        text = document["text"]
        fqn_match = _FQN_LINE.search(text)
        if fqn_match is None or fqn_match.group(1).startswith(("Doc: ", "Example: ")):
            continue
        coverage_match = _COVERAGE_LINE.search(text)
        if coverage_match is not None:
            return int(coverage_match.group(1)), int(coverage_match.group(2))
    return None


def _states_unparsed_files(store: GenerationManifestStore, scope: Scope) -> bool:
    """True when the first symbol chunk's ``API-Coverage:`` line carries a source-files-could-not-be-parsed clause."""
    key = "::".join((scope.family, scope.platform, scope.source_kind))
    generation_id = store.read_active(key)
    assert generation_id is not None
    documents = store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
    for document in documents.values():
        text = document["text"]
        fqn_match = _FQN_LINE.search(text)
        if fqn_match is None or fqn_match.group(1).startswith(("Doc: ", "Example: ")):
            continue
        line = _FULL_COVERAGE_LINE.search(text)
        if line is not None:
            return "source files could not be parsed" in line.group(0)
    return False


# re-pinned by TC-553: a note was expected only when the line states N < M types -> it is also expected when the line states that
# source files could not be parsed (tex/python states "at least 2 source files could not be parsed" since the 2026-10-17 re-ingest;
# the upstream library does not parse, ISOPROV-01). The product's note for that clause names the clause; no check is loosened.
def _assert_note(note: str | None, stated: tuple[int, int] | None, unparsed: bool = False) -> None:
    if stated is not None and stated[0] < stated[1]:
        assert note is not None
        assert f"{stated[0]} of {stated[1]}" in note
    elif unparsed:
        assert note is not None
        assert "could not be parsed" in note
    else:
        assert note is None


def test_every_baked_pilot_is_discovered() -> None:
    assert len(PILOTS) >= MIN_PILOTS, f"only {len(PILOTS)} pilots found under {DEMO_MANIFESTS}"
    assert all(len(name.split("__")) == 3 for name in PILOTS)


@pytest.mark.parametrize("directory", PILOTS)
def test_search_symbols_miss_carries_the_coverage_note_when_the_index_is_partial(
    directory: str,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)
    stated = _stated_coverage(store, scope)
    for query in (NONSENSE_TOKEN, NONSENSE_SENTENCE):
        answer = search_symbols(store, scope, query)
        assert isinstance(answer, Miss), f"{directory}: {query!r} answered with a hit"
        assert answer.reason == f"no symbol matches {query!r}"
        _assert_note(answer.coverage_note, stated, _states_unparsed_files(store, scope))


@pytest.mark.parametrize("directory", PILOTS)
def test_lookup_miss_carries_the_coverage_note_when_the_index_is_partial(
    directory: str,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)
    stated = _stated_coverage(store, scope)
    for query in (NONSENSE_TOKEN, NONSENSE_SENTENCE):
        answer = lookup(store, scope, query)
        assert isinstance(answer, Miss), f"{directory}: {query!r} answered with {type(answer).__name__}"
        _assert_note(answer.coverage_note, stated, _states_unparsed_files(store, scope))


@pytest.mark.parametrize("directory", PILOTS)
def test_get_symbol_and_list_members_misses_carry_the_same_note(
    directory: str,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)
    stated = _stated_coverage(store, scope)
    symbol_miss = get_symbol(store, scope, NONSENSE_TOKEN)
    members_miss = list_members(store, scope, NONSENSE_TOKEN)
    assert isinstance(symbol_miss, NotFound) and isinstance(members_miss, NotFound)
    _assert_note(symbol_miss.coverage_note, stated, _states_unparsed_files(store, scope))
    _assert_note(members_miss.coverage_note, stated, _states_unparsed_files(store, scope))
    search_miss = search_symbols(store, scope, NONSENSE_TOKEN)
    assert isinstance(search_miss, Miss)
    assert search_miss.coverage_note == symbol_miss.coverage_note == members_miss.coverage_note


# re-pinned by TC-553: test_the_lookup_miss_no_longer_speaks_for_the_repository pinned `300 of 901` -> pdf/net is complete (901 of 901),
# so the miss speaks about the index only: its reason starts with "nothing in the indexed documentation" and it carries no coverage note.
def test_the_lookup_miss_speaks_about_the_index_only_and_pdf_net_is_complete(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)
    stated = _stated_coverage(store, scope)
    assert stated == (901, 901)  # re-pinned by TC-553: (300, 901) -> (901, 901)
    answer = lookup(store, scope, NONSENSE_SENTENCE)
    assert isinstance(answer, Miss)
    assert answer.reason.startswith("nothing in the indexed documentation")
    assert answer.coverage_note is None
    _assert_note(answer.coverage_note, stated, _states_unparsed_files(store, scope))


def test_no_baked_pilot_states_a_partial_index_any_more(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    # The day a partial pilot returns, the real-data branch of _assert_note is exercised on purpose, not by accident.
    partial: dict[str, tuple[int, int]] = {}
    for directory in PILOTS:
        stated = _stated_coverage(_store_for(pilot_stores, tmp_path_factory, directory), _scope_of(directory))
        assert stated is not None, f"{directory} states no API-Coverage line"
        if stated[0] != stated[1]:
            partial[directory] = stated
    assert partial == {}


# ---- the partial branch, proven on an in-memory generation (no baked pilot is partial any more) ----------


def _symbol_chunk(name: str, coverage: str) -> str:
    return f"## {name}\n\nFQN: {name}\nKind: class\nAPI-Coverage: {coverage}\nSource-Commit: tc553\n"


def _partial_store(coverage: str) -> _InMemoryStore:
    return _InMemoryStore((_symbol_chunk("Alpha", coverage), _symbol_chunk("Beta", coverage)))


def _every_miss_note(store: _InMemoryStore) -> list[str | None]:
    scope = Scope(family="pdf", platform="net")
    search = search_symbols(store, scope, NONSENSE_TOKEN)
    looked_up = lookup(store, scope, NONSENSE_TOKEN)
    symbol = get_symbol(store, scope, NONSENSE_TOKEN)
    members = list_members(store, scope, NONSENSE_TOKEN)
    assert isinstance(search, Miss) and isinstance(looked_up, Miss)
    assert isinstance(symbol, NotFound) and isinstance(members, NotFound)
    return [search.coverage_note, looked_up.coverage_note, symbol.coverage_note, members.coverage_note]


def test_every_miss_from_a_partial_types_only_generation_carries_the_note() -> None:
    notes = _every_miss_note(_partial_store("2 of 5 types"))
    assert all(note is not None and "2 of 5" in note for note in notes), notes
    assert len(set(notes)) == 1  # the four tools say it through one helper


def test_search_symbols_miss_from_a_partial_generation_carries_the_note() -> None:
    answer = search_symbols(
        _partial_store("2 of 5 types"), Scope(family="pdf", platform="net"), NONSENSE_TOKEN
    )
    assert isinstance(answer, Miss)
    assert answer.coverage_note is not None and "2 of 5" in answer.coverage_note


def test_lookup_miss_from_a_partial_generation_carries_the_note() -> None:
    answer = lookup(_partial_store("2 of 5 types"), Scope(family="pdf", platform="net"), NONSENSE_SENTENCE)
    assert isinstance(answer, Miss)
    assert answer.reason.startswith("nothing in the indexed documentation")
    assert answer.coverage_note is not None and "2 of 5" in answer.coverage_note


def test_get_symbol_and_list_members_misses_from_a_partial_generation_carry_the_note() -> None:
    scope = Scope(family="pdf", platform="net")
    store = _partial_store("2 of 5 types")
    symbol_miss = get_symbol(store, scope, NONSENSE_TOKEN)
    members_miss = list_members(store, scope, NONSENSE_TOKEN)
    assert isinstance(symbol_miss, NotFound) and isinstance(members_miss, NotFound)
    assert symbol_miss.coverage_note is not None and "2 of 5" in symbol_miss.coverage_note
    assert members_miss.coverage_note is not None and "2 of 5" in members_miss.coverage_note


def test_a_partial_types_and_functions_generation_names_both_in_every_miss() -> None:
    notes = _every_miss_note(_partial_store("2 of 5 types; 1 of 4 functions"))
    for note in notes:
        assert note is not None
        assert "2 of 5 types" in note and "1 of 4 functions" in note, note


def test_a_partial_functions_clause_alone_is_noted_by_every_miss() -> None:
    notes = _every_miss_note(_partial_store("5 of 5 types; 1 of 4 functions"))
    for note in notes:
        assert note is not None and "1 of 4 functions" in note, note


def test_a_complete_generation_with_unparsed_source_files_still_carries_a_note_on_every_miss() -> None:
    notes = _every_miss_note(_partial_store("4 of 4 types; at least 2 source files could not be parsed"))
    for note in notes:
        assert note is not None and "at least 2 source files could not be parsed" in note, note


def test_a_complete_generation_carries_no_note_on_any_miss() -> None:
    assert _every_miss_note(_partial_store("5 of 5 types")) == [None, None, None, None]
    assert _every_miss_note(_partial_store("5 of 5 types; 4 of 4 functions")) == [None, None, None, None]


# ---- search_docs: the miss says which other content types hold matches -----------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="tc515"),
                evidence_refs=(),
                title="pdf/net documentation",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, "gen-tc515")
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc515"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _doc_chunk(title: str, text: str) -> str:
    return f"## Doc: {title}\n\nFQN: Doc: {title}\n{text}\n"


def _docs_store() -> _InMemoryStore:
    return _InMemoryStore(
        (
            _doc_chunk(
                "Installation", "Getting started: install the package and import the watermark module."
            ),
            _doc_chunk("Page layout", "Rotate and crop each page before saving the document."),
        )
    )


def test_search_docs_miss_names_the_content_types_that_hold_matches() -> None:
    scope = Scope(family="pdf", platform="net")
    store = _docs_store()

    answer = search_docs(store, scope, "install the package", "developer_guide")  # type: ignore[arg-type]
    assert isinstance(answer, DocsMiss)
    assert answer.other_content_types == ("getting_started",)
    assert answer.hint is not None and "getting_started" in answer.hint
    assert answer.reason == "no developer_guide documents match 'install the package'"

    nothing = search_docs(store, scope, "send a fax via modem", "developer_guide")  # type: ignore[arg-type]
    assert isinstance(nothing, DocsMiss)
    assert nothing.other_content_types == ()
    assert nothing.hint is None

    hit = search_docs(store, scope, "rotate crop page", "developer_guide")  # type: ignore[arg-type]
    assert isinstance(hit, list) and hit


def test_the_miss_reason_texts_of_search_symbols_and_search_docs_are_unchanged(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)
    symbols = search_symbols(store, scope, NONSENSE_TOKEN)
    assert isinstance(symbols, Miss)
    assert symbols.reason == f"no symbol matches {NONSENSE_TOKEN!r}"

    docs = search_docs(store, scope, NONSENSE_SENTENCE, "faq")
    assert isinstance(docs, DocsMiss)
    assert docs.reason in (
        "no documents classified content_type='faq'",
        f"no faq documents match {NONSENSE_SENTENCE!r}",
    )
