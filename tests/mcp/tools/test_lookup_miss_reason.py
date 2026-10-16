"""G3/TC-493 (REQ-G3-005): lookup's own miss says what lookup searched.

Review finding L-059: ``lookup`` also searches the documentation and the verified examples, yet its
miss repeated the symbol search's narrower "no symbol matches '<query>'". lookup's miss now reads
"nothing in the indexed documentation, verified examples or symbols of this pilot matches '<query>'"
(TC-515 added "indexed"), with the
same ``Miss`` type, scope, query and did-you-mean suggestions; ``search_symbols`` is unchanged.

Offline and deterministic: every pilot's committed scope directory is COPIED into a temporary
directory (the repository is never written), and the embedding gateway is replaced by a stub whose
``embed`` raises, as the standalone image behaves with no gateway.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import server
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.search_docs import search_docs
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SERVER_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "server.py"

MIN_PILOTS = 40

NONSENSE_QUERY = "send a fax via modem"
TASK_QUERY = "how do I set a password on a presentation"
UNANSWERABLE_TASK_QUERY = "how do I send a fax via modem"


def _expected_reason(query: str) -> str:
    """The literal expected text, written out here and never imported from lookup.py."""
    # TC-515: the miss no longer speaks for the repository, only for the index.
    return "nothing in the indexed documentation, verified examples or symbols of this pilot matches " + repr(
        query
    )


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
    """Lazily filled: pilot directory name -> a store over a COPY of that pilot's committed manifests."""
    return {}


def _store_for(
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
    directory: str,
) -> GenerationManifestStore:
    if directory not in pilot_stores:
        root = tmp_path_factory.mktemp(f"missreason_{directory}")
        shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
        pilot_stores[directory] = GenerationManifestStore(root)
    return pilot_stores[directory]


def test_every_pilot_is_discovered() -> None:
    assert len(PILOTS) >= MIN_PILOTS, f"only {len(PILOTS)} pilots found under {DEMO_MANIFESTS}"
    assert all(len(name.split("__")) == 3 for name in PILOTS)


# ---- every pilot: the miss names everything lookup searched ---------------------------------------


@pytest.mark.parametrize("directory", PILOTS)
def test_nonsense_question_miss_names_everything_searched(
    directory: str,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)

    answer = lookup(store, scope, NONSENSE_QUERY)

    assert isinstance(answer, Miss), f"{directory}: answered with {type(answer).__name__}"
    assert answer.reason == _expected_reason(NONSENSE_QUERY)
    assert "no symbol matches" not in answer.reason
    assert answer.scope == scope
    assert answer.query == NONSENSE_QUERY


# ---- the reviewer's reproductions -----------------------------------------------------------------


# slides/java now holds verified README examples that answer the password question, so its reproduction
# uses a task question that is genuinely absent from its committed content (UNANSWERABLE_TASK_QUERY).
@pytest.mark.parametrize(
    ("directory", "query"),
    [
        ("slides__java__self_extracted", UNANSWERABLE_TASK_QUERY),
        ("cells__cpp__self_extracted", TASK_QUERY),
    ],
    ids=["slides__java__self_extracted", "cells__cpp__self_extracted"],
)
def test_reviewer_reproduction_task_question_miss(
    directory: str,
    query: str,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    answer = lookup(store, _scope_of(directory), query)
    assert isinstance(answer, Miss)
    assert answer.reason == _expected_reason(query)
    assert "no symbol matches" not in answer.reason


def test_identifier_path_miss_names_everything_searched_while_search_symbols_is_unchanged(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)

    answer = lookup(store, scope, "Zzqxv")
    assert isinstance(answer, Miss)
    assert answer.reason == _expected_reason("Zzqxv")

    symbol_miss = search_symbols(store, scope, "Zzqxv")
    assert isinstance(symbol_miss, Miss)
    assert symbol_miss.reason == "no symbol matches 'Zzqxv'"


def test_did_you_mean_suggestions_are_kept(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)

    answer = lookup(store, scope, "Annotatio")
    symbol_miss = search_symbols(store, scope, "Annotatio")

    assert isinstance(answer, Miss)
    assert isinstance(symbol_miss, Miss)
    assert answer.reason == _expected_reason("Annotatio")
    assert symbol_miss.suggestions, "control: search_symbols itself offers suggestions for this query"
    assert answer.suggestions == symbol_miss.suggestions
    assert answer.suggestions


def test_miss_is_the_search_symbols_miss_type_with_same_scope_and_query(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)

    answer = lookup(store, scope, "Zzqxv")

    assert type(answer) is Miss
    assert answer.scope == scope
    assert answer.query == "Zzqxv"


def test_content_type_path_miss_equals_search_docs_miss(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)

    answer = lookup(store, scope, "Zzqxv", content_type="faq")
    docs_miss = search_docs(store, scope, "Zzqxv", "faq")

    assert not isinstance(answer, (list, TaskAnswer))
    assert not isinstance(docs_miss, list)
    assert answer.reason == docs_miss.reason
    assert answer.reason != _expected_reason("Zzqxv")


def test_empty_store_still_answers_no_published_generation(tmp_path: Path) -> None:
    scope = Scope(family="pdf", platform="net")
    for query in ("Zzqxv", TASK_QUERY):
        answer = lookup(GenerationManifestStore(tmp_path), scope, query)
        assert isinstance(answer, Miss)
        assert answer.reason == "no published generation for this scope"


# ---- controls: a real question and a real identifier still answer ---------------------------------


def test_control_task_question_still_answers(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    answer = lookup(store, _scope_of(directory), "how do I add a watermark to a PDF")
    assert isinstance(answer, TaskAnswer)
    assert answer.doc_matches or answer.example is not None


def test_control_identifier_still_returns_symbols(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    answer = lookup(store, _scope_of(directory), "AFRelationship")
    assert isinstance(answer, list) and answer
    assert all(isinstance(match, SymbolMatch) for match in answer)


# ---- through the server, and the production call site ---------------------------------------------


def test_server_wrapper_carries_the_new_reason(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    directory = "pdf__net__self_extracted"
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)

    payload = server._to_jsonable(server._wrap_lookup(store, scope)(query="Zzqxv"))

    assert payload["reason"] == _expected_reason("Zzqxv")
    assert payload["query"] == "Zzqxv"


def test_production_entrypoint_imports_lookup_from_the_lookup_module() -> None:
    source = SERVER_SOURCE.read_text(encoding="utf-8")
    assert "from foss_mcp.mcp.tools.lookup import lookup" in source
    assert "lookup(store, scope, query, content_type=content_type, top_k=top_k)" in source
