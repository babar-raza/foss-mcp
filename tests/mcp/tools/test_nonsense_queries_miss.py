"""G3/TC-399: on every baked pilot a nonsense question gets an honest miss; a real one still answers.

An independent reviewer found that "send a fax via modem" or "how do I bake a chocolate cake"
returned a confident documentation or example answer. TC-381's shared relevance rule (query terms
minus the family word, light stemming, coverage floor over documentation and example chunks only)
fixes it; these tests pin that fix across ALL committed demo-manifest pilots, so a later relevance
change cannot quietly reintroduce it.

Offline and deterministic: every pilot's scope directory is COPIED into a temporary directory (the
repository is never written), and the embedding gateway is replaced by a stub whose ``embed`` raises,
exactly as the standalone image behaves with no gateway (the vector stage then degrades to no match
by design).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import ExampleMatch, NoExampleFound, find_examples
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.search_docs import DocMatch, search_docs
from foss_mcp.mcp.tools.search_symbols import Miss

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

# re-pinned by TC-551: 40 -> 32
MIN_PILOTS = 32

NONSENSE_QUERIES = (
    "send a fax via modem",
    "how do I bake a chocolate cake",
    "teleport the cat to mars",
    "quantum entanglement of spreadsheets",
)


def _discover_pilots() -> list[str]:
    """Names of every demo-manifest scope directory that holds an ``active_pointer.json``."""
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
        root = tmp_path_factory.mktemp(f"nonsense_{directory}")
        shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
        pilot_stores[directory] = GenerationManifestStore(root)
    return pilot_stores[directory]


def test_every_pilot_is_discovered() -> None:
    assert len(PILOTS) >= MIN_PILOTS, f"only {len(PILOTS)} pilots found under {DEMO_MANIFESTS}"
    assert all(len(name.split("__")) == 3 for name in PILOTS)


@pytest.mark.parametrize("query", NONSENSE_QUERIES)
@pytest.mark.parametrize("directory", PILOTS)
def test_nonsense_query_is_an_honest_miss(
    directory: str,
    query: str,
    pilot_stores: dict[str, GenerationManifestStore],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, directory)
    scope = _scope_of(directory)

    answer = lookup(store, scope, query)
    assert isinstance(answer, Miss), f"{directory}: lookup({query!r}) answered with {type(answer).__name__}"
    assert not isinstance(answer, (TaskAnswer, list))

    examples = find_examples(store, scope, query)
    assert isinstance(examples, NoExampleFound), (
        f"{directory}: find_examples({query!r}) matched "
        f"{[match.fqn for match in examples] if isinstance(examples, list) else examples!r}"
    )
    assert examples.found is False


# ---- controls: a REAL question still answers where the content exists ----------------------------


def test_control_pdf_net_watermark_example_still_answers(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, "pdf__net__self_extracted")
    result = find_examples(store, Scope(family="pdf", platform="net"), "add a watermark to a PDF")
    assert isinstance(result, list) and result
    assert all(isinstance(match, ExampleMatch) and match.found for match in result)
    assert all(match.snippet.strip() for match in result)


def test_control_slides_python_presentation_example_still_answers(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, "slides__python__self_extracted")
    result = find_examples(
        store, Scope(family="slides", platform="python"), "create a presentation and add a shape"
    )
    assert isinstance(result, list) and result
    assert all(isinstance(match, ExampleMatch) and match.found for match in result)
    assert all(match.snippet.strip() for match in result)


def test_control_pdf_net_install_getting_started_still_answers(
    pilot_stores: dict[str, GenerationManifestStore], tmp_path_factory: pytest.TempPathFactory
) -> None:
    store = _store_for(pilot_stores, tmp_path_factory, "pdf__net__self_extracted")
    result = search_docs(store, Scope(family="pdf", platform="net"), "install", "getting_started")
    assert isinstance(result, list) and result
    assert all(isinstance(match, DocMatch) and match.text.strip() for match in result)
