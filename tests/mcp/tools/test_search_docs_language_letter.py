"""G3/TC-549 (REQ-G3-001, REQ-G3-008): ``search_docs`` answers the C++ and C# product titles.

Release-candidate trial gate, 2026-10-16: five of 40 pilots (pdf, cells, slides, email, jmap for C++) failed the
workflow proof's check 5 - ``search_docs`` for the furnished page title returned nothing. TC-505 drops every
one-letter query term (the "c" of "C++") and TC-516 turns "no distinctive term" into an honest miss, so
"Aspose.PDF FOSS for C++" (terms: aspose, pdf, foss) named no topic and the documentation of every C++ pilot
was unreachable by its own product title.

Offline and deterministic: each real pilot is COPIED into a temporary directory (the repository is never
written) and the synthetic cases are built in memory.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import search_docs as search_docs_module
from foss_mcp.mcp.tools.relevance import names_language_letter
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, DocMatch, Miss, search_docs

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FURNISHED = REPO_ROOT / "tests" / "fixtures" / "furnished"
SEARCH_DOCS_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "search_docs.py"
RELEVANCE_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "relevance.py"

MIN_PILOTS = 40
CPP_FAMILIES = ("pdf", "cells", "slides", "email", "jmap")
_GENERATION_ID = "gen-tc549-language-letter"


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


def _furnished_title(family: str, platform: str) -> str:
    """The page title the workflow proof searches for: the ``title:`` line of the furnished page's front matter."""
    page = FURNISHED / f"{family}_{platform}" / "pages" / "_index.md"
    lines = page.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", f"{page} has no front matter"
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("title:"):
            title = line[len("title:") :].strip().strip("\"'")
            assert title, f"{page} has an empty title"
            return title
    raise AssertionError(f"{page} has no title line")


@pytest.fixture(scope="module")
def pilot_stores() -> dict[str, GenerationManifestStore]:
    """Lazily filled: pilot directory name -> a store over a COPY of that pilot's committed manifests."""
    return {}


def _store_for(
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    directory: str,
) -> GenerationManifestStore:
    if directory not in pilot_stores:
        root = tmp_path_factory.mktemp(f"language_letter_{directory}")
        shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
        pilot_stores[directory] = GenerationManifestStore(root)
    return pilot_stores[directory]


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


def _doc_chunk(title: str, *body_lines: str) -> str:
    return "\n".join([f"FQN: Doc: {title}", *body_lines]) + "\n"


def _search_every_type(store: object, scope: Scope, query: str) -> dict[str, list[DocMatch] | Miss]:
    return {
        content_type: search_docs(store, scope, query, content_type)  # type: ignore[arg-type]
        for content_type in CONTENT_TYPES
    }


# ---- the predicate -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["C++", "Aspose.PDF FOSS for C++", "C#", "learn c# basics", "c++ merge pages"],
)
def test_names_language_letter_is_true_for_cpp_and_csharp(query: str) -> None:
    assert names_language_letter(query) is True


@pytest.mark.parametrize(
    "query",
    ["TeX", "element's", "x", "C:\\temp", "pdf3", "Cpp", "cpp", "", "a b c"],
)
def test_names_language_letter_is_false_for_everything_else(query: str) -> None:
    assert names_language_letter(query) is False


# ---- the five C++ pilots, with their real committed generations ---------------------------------------


@pytest.mark.parametrize("family", CPP_FAMILIES)
def test_the_five_cpp_pilots_answer_their_own_product_title(
    family: str,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    directory = f"{family}__cpp__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    title = _furnished_title(family, "cpp")
    assert "C++" in title, title

    results = _search_every_type(store, _scope_of(directory), title)

    answered = {
        content_type: result
        for content_type, result in results.items()
        if isinstance(result, list) and result
    }
    assert answered, f"{directory}: search_docs({title!r}) missed under every content type: {results}"
    assert any(family in match.text.lower() for matches in answered.values() for match in matches), (
        f"{directory}: no answered chunk mentions {family!r}"
    )


# ---- the offline copy of the gate's check 5, for every committed generation --------------------------------


def test_every_pilot_is_discovered() -> None:
    assert len(PILOTS) >= MIN_PILOTS, f"only {len(PILOTS)} pilots found under {DEMO_MANIFESTS}"
    assert all(len(name.split("__")) == 3 for name in PILOTS)


@pytest.mark.parametrize("directory", PILOTS)
def test_every_pilots_product_title_is_answered_for_at_least_one_content_type(
    directory: str,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    scope = _scope_of(directory)
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    title = _furnished_title(scope.family, scope.platform)

    results = _search_every_type(store, scope, title)

    assert any(not isinstance(result, Miss) and result for result in results.values()), (
        f"{directory}: search_docs({title!r}) is a miss under every content type"
    )


# ---- synthetic scopes ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "query"),
    [
        ("cpp", "Aspose.PDF FOSS for C++"),
        ("net", "Aspose.PDF FOSS for C#"),
    ],
)
def test_a_cpp_or_csharp_product_title_is_answered(platform: str, query: str) -> None:
    store = _InMemoryStore(
        _doc_chunk("Guide", "Aspose.PDF FOSS is a free library for working with pdf files.")
    )

    result = search_docs(store, Scope(family="pdf", platform=platform), query, "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, list)
    assert result
    assert all(word in result[0].text.lower() for word in ("aspose", "pdf", "foss")), result[0].text


def test_a_python_product_title_is_still_answered() -> None:
    store = _InMemoryStore(_doc_chunk("Guide", "Aspose.PDF FOSS for Python is a free library."))

    scope = Scope(family="pdf", platform="python")
    result = search_docs(store, scope, "Aspose.PDF FOSS for Python", "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, list)
    assert result


@pytest.mark.parametrize("query", ["pdf examples", "Example:", "how to use samples"])
def test_the_deliberate_misses_stay_misses(query: str) -> None:
    store = _InMemoryStore(_doc_chunk("Guide", "This pdf example shows a sample of how to use the library."))

    result = search_docs(store, Scope(family="pdf", platform="net"), query, "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, Miss), query
    assert result.reason == f"no developer_guide documents match {query!r}"


@pytest.mark.parametrize("query", ["C:\\temp file", "c"])
def test_other_one_letter_queries_gain_no_special_treatment(
    query: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The result must be exactly what the gate gave before this card: with the language-letter clause forced
    # false, the answer is the same, so only C++ and C# queries changed.
    store = _InMemoryStore(
        _doc_chunk("Guide", "Copy the temp file to the c drive and read the file."),
        _doc_chunk("Other", "This pdf guide explains the library."),
    )
    scope = Scope(family="pdf", platform="net")

    assert names_language_letter(query) is False
    with_clause = search_docs(store, scope, query, "developer_guide")  # type: ignore[arg-type]
    monkeypatch.setattr(search_docs_module, "names_language_letter", lambda _query: False)
    without_clause = search_docs(store, scope, query, "developer_guide")  # type: ignore[arg-type]

    assert with_clause == without_clause


def test_the_clause_alone_is_what_lets_a_cpp_title_through(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _InMemoryStore(
        _doc_chunk("Guide", "Aspose.PDF FOSS is a free library for working with pdf files.")
    )
    scope = Scope(family="pdf", platform="cpp")
    query = "Aspose.PDF FOSS for C++"

    assert isinstance(search_docs(store, scope, query, "developer_guide"), list)  # type: ignore[arg-type]
    monkeypatch.setattr(search_docs_module, "names_language_letter", lambda _query: False)
    assert isinstance(search_docs(store, scope, query, "developer_guide"), Miss)  # type: ignore[arg-type]


# ---- wiring ---------------------------------------------------------------------------------------------


def test_rule_for_is_wired_to_names_language_letter() -> None:
    search_docs_text = SEARCH_DOCS_SOURCE.read_text(encoding="utf-8")
    relevance_text = RELEVANCE_SOURCE.read_text(encoding="utf-8")

    rule_for = search_docs_text.split("def _rule_for", 1)[1].split("\ndef ", 1)[0]
    assert "names_language_letter(query)" in rule_for
    assert "def names_language_letter(" in relevance_text
    assert "names_language_letter," in search_docs_text.split("def _rule_for", 1)[0]
