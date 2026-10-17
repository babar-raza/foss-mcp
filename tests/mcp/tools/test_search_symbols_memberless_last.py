"""TC-541 (G3/REQ-G3-008): ``search_symbols`` ranks a chunk that lists members before an empty one.

Within one match tier a chunk that lists at least one method, property or enum member comes before a
chunk that lists none; the declaring-before-inheriting and BM25 keys are unchanged behind it. Pinned on
in-memory generations and on a COPY of the committed generations of the 12 Python pilots (the committed
data is only re-ingested later, so the empty aliases are still there and the ranking has to cope).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index, query_lexical_index_scored
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import (
    Miss,
    SymbolMatch,
    _has_listed_members,
    scope_key,
    search_symbols,
)
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SCOPE = Scope(family="pkg", platform="python")
_GENERATION_ID = "gen-tc541"
_PSEUDO_PREFIXES = ("Example: ", "Doc: ")


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, *bodies: str) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="org/repo", commit="tc541"),
                evidence_refs=(),
                title="pkg/python API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        self.lexical = build_lexical_index(chunks, chunk_ids, _GENERATION_ID)
        self._manifest = SimpleNamespace(payload={"lexical_index": self.lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _chunk(fqn: str, *, methods: tuple[str, ...] = (), filler: str = "") -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", "Kind: class"]
    if filler:
        lines.append(filler)
    if methods:
        lines += ["", "Methods:", *(f"  - {method}" for method in methods)]
    return "\n".join(lines) + "\n"


def _fqns(result: list[SymbolMatch] | Miss) -> list[str]:
    assert not isinstance(result, Miss), result
    return [str(extract_fqn(match.text)) for match in result]


def _search(store: _InMemoryStore, query: str) -> list[SymbolMatch] | Miss:
    return search_symbols(store, SCOPE, query)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ synthetic


def test_a_chunk_with_members_ranks_before_an_empty_chunk_of_the_same_name_even_when_the_empty_one_scores_higher_bm25() -> (
    None
):
    empty = _chunk("pkg.generated.Document")
    real = _chunk(
        "pkg.model.Document",
        methods=("save(path: str) -> None", "load(path: str) -> None", "close() -> None"),
        filler="A long description of the class that dilutes the term density of its chunk text.",
    )
    store = _InMemoryStore(empty, real)

    scores = dict(query_lexical_index_scored(store.lexical, "Document", top_k=10))
    documents = store.lexical["documents"]
    score_of = {extract_fqn(documents[doc_id]["text"]): score for doc_id, score in scores.items()}
    # Guard against a vacuous pass: the empty chunk really does outscore the real one on BM25 alone.
    assert score_of["pkg.generated.Document"] > score_of["pkg.model.Document"]

    assert _fqns(_search(store, "Document")) == ["pkg.model.Document", "pkg.generated.Document"]


def test_the_empty_chunk_stays_listed_behind_the_real_one() -> None:
    store = _InMemoryStore(
        _chunk("pkg.generated.Document"), _chunk("pkg.model.Document", methods=("save() -> None",))
    )

    assert len(_fqns(_search(store, "Document"))) == 2


def test_the_tier_still_decides_first() -> None:
    # The exact full-FQN chunk is empty (tier 0); the real class matches only by final segment (tier 1).
    store = _InMemoryStore(
        _chunk("Document"),
        _chunk("pkg.model.Document", methods=("save() -> None",)),
    )

    assert _fqns(_search(store, "Document")) == ["Document", "pkg.model.Document"]


def test_declaring_before_inheriting_is_unchanged_behind_the_new_key() -> None:
    store = _InMemoryStore(
        _chunk("pkg.Child", methods=("render() -> None (inherited from pkg.Base)",)),
        _chunk("pkg.Base", methods=("render() -> None",)),
        _chunk("pkg.Empty"),
    )

    result = _search(store, "render")

    assert _fqns(result) == ["pkg.Base", "pkg.Child"]


def test_two_empty_chunks_keep_the_bm25_order() -> None:
    store = _InMemoryStore(
        _chunk(
            "pkg.a.Widget", filler="a long filler sentence that makes this chunk much longer than the other"
        ),
        _chunk("pkg.b.Widget"),
    )
    documents = store.lexical["documents"]
    scores = {
        extract_fqn(documents[doc_id]["text"]): score
        for doc_id, score in query_lexical_index_scored(store.lexical, "Widget", top_k=10)
    }
    expected = sorted(scores, key=lambda fqn: -scores[str(fqn)])

    assert _fqns(_search(store, "Widget")) == expected


def test_a_miss_is_unchanged() -> None:
    store = _InMemoryStore(_chunk("pkg.Widget", methods=("run() -> None",)))

    result = _search(store, "teleportation")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'teleportation'"
    assert result.suggestions == ()
    assert result.coverage_note is None


def test_an_empty_only_answer_is_still_returned() -> None:
    store = _InMemoryStore(_chunk("pkg.generated.Document"))

    assert _fqns(_search(store, "Document")) == ["pkg.generated.Document"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FQN: a.B\nKind: class\n\nMethods:\n  - run() -> None\n", True),
        ("FQN: a.B\nKind: class\n\nProperties:\n  - Title: str (writable)\n", True),
        ("FQN: a.B\nKind: enum\n\nMembers:\n  - ALPHA = 1\n", True),
        ("FQN: a.B\nKind: class\n", False),
        ("FQN: a.B\nKind: class\n\nMethods:\n", False),
        ("FQN: a.B\nKind: class\nMethods: run is mentioned in prose\n", False),
        ("FQN: a.B\nKind: class\nBases:\n  - object\n", False),
    ],
)
def test_the_helper_counts_properties_and_enum_members_as_members(text: str, expected: bool) -> None:
    assert _has_listed_members(text) is expected


# ------------------------------------------------------------------ committed generations


PYTHON_PILOTS = sorted(
    directory.name
    for directory in DEMO_MANIFESTS.iterdir()
    if directory.is_dir()
    and directory.name.endswith("__python__self_extracted")
    and (directory / "active_pointer.json").is_file()
)


@pytest.fixture(scope="module")
def committed_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("memberless_last")
    for directory in PYTHON_PILOTS:
        shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
    return root


def _scope_of(directory: str) -> Scope:
    family, platform, source_kind = directory.split("__")
    return Scope(family=family, platform=platform, source_kind=source_kind)


def _documents(store: GenerationManifestStore, scope: Scope) -> dict[str, dict]:
    key = scope_key(scope, "self_extracted")
    active = store.read_active(key)
    assert active is not None
    return store.read_generation(key, active).payload["lexical_index"]["documents"]


def _final_segment(fqn: str) -> str:
    return fqn.replace("::", ".").rsplit(".", 1)[-1]


def test_the_eleven_python_pilots_are_present() -> None:
    # re-pinned by TC-551: 12 -> 11
    assert len(PYTHON_PILOTS) >= 11


def test_the_first_result_has_members_for_every_name_that_has_both_an_empty_and_a_real_chunk(
    committed_root: Path,
) -> None:
    # Measured on the committed data of main d7b594fd: 59 names have both an empty and a real chunk in the
    # 12 Python generations, and the first result had members for only 27 of them (46 percent) before this
    # card; 59 of 59 now. After the data refresh pairs the shells there will be none left, so the property is
    # asserted for however many such names exist; the synthetic tests above guard against a vacuous pass.
    store = GenerationManifestStore(committed_root)
    contested = 0
    failures: list[str] = []
    for directory in PYTHON_PILOTS:
        scope = _scope_of(directory)
        documents = _documents(store, scope)
        with_members: set[str] = set()
        without_members: set[str] = set()
        for document in documents.values():
            text = document["text"]
            fqn = extract_fqn(text)
            if fqn is None or fqn.startswith(_PSEUDO_PREFIXES):
                continue
            (with_members if _has_listed_members(text) else without_members).add(_final_segment(fqn))
        for name in sorted(with_members & without_members):
            contested += 1
            result = search_symbols(store, scope, name)
            if isinstance(result, Miss) or not _has_listed_members(result[0].text):
                failures.append(f"{directory}:{name}")
    assert failures == [], f"{len(failures)} of {contested} contested names answer with an empty chunk first"


@pytest.mark.parametrize("directory", ["pdf__python__self_extracted", "words__python__self_extracted"])
def test_search_symbols_document_on_pdf_python_and_words_python_answers_with_the_real_class(
    committed_root: Path, directory: str
) -> None:
    store = GenerationManifestStore(committed_root)
    result = search_symbols(store, _scope_of(directory), "Document")

    assert not isinstance(result, Miss)
    first = result[0].text
    fqn = extract_fqn(first)
    assert fqn is not None
    assert _final_segment(fqn) == "Document"
    assert _has_listed_members(first)
