"""G3/TC-598 (REQ-G3-010): the reusable, offline, seeded loader over all 32 baked pilots.

This generalizes the loader pattern ``tests/mcp/tools/test_round3_retrieval_corpus.py`` already uses
(``Corpus.__init__``, lines ~268-283 at the time this card was written): copy
``infra/demo-manifests/*__self_extracted`` to a temp directory, build a ``GenerationManifestStore`` over
the copy, iterate the ``family__platform__source_kind`` directories, read each one's active generation,
and read its documents from ``payload["lexical_index"]["documents"]``. Nothing here is a new loading
mechanism - it is that same pattern, pulled out of a test-file-local class so F4-F9 and every later
program's closure card can import it instead of copying the code.

This module is a library, not a test file: it defines no pytest fixture of its own. A caller's own test
file supplies an ``autouse`` fixture that scrubs the embedding-gateway env vars (see
``no_embedding_gateway_env`` below for a plain context-manager version of that same scrub, for callers
that are not pytest fixtures).
"""

from __future__ import annotations

import contextlib
import os
import random
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, EXAMPLE, SYMBOL, chunk_kind
from foss_mcp.mcp.tools.search_symbols import scope_key

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

# G3/TC-598: the card's own check for a central marker registration found none in pyproject.toml (no
# existing ``[tool.pytest.ini_options] markers = [...]`` entry to extend), and ``pyproject.toml`` is
# outside this card's write_paths (so is any package-wide conftest.py other than this package's own,
# which is also not a declared write path). Per the card's own fallback instruction, F4-F9's full sweep
# is gated by this plain boolean env var instead of a ``@pytest.mark.exhaustive`` marker.
EXHAUSTIVE_ENV = "FOSS_MCP_CONFORMANCE_EXHAUSTIVE"


def exhaustive_enabled() -> bool:
    """Whether the full (unsampled) sweep should run, per ``EXHAUSTIVE_ENV``'s documented fallback."""
    return os.environ.get(EXHAUSTIVE_ENV) == "1"


@contextlib.contextmanager
def no_embedding_gateway_env() -> Iterator[None]:
    """A plain context-manager version of the ``_no_embedding_gateway`` autouse-fixture pattern used by
    ``test_round3_retrieval_corpus.py`` and ``test_forgiveness_acceptance.py``: temporarily unset the
    embedding-gateway endpoint/API-key env vars, then restore whatever was there before. This module is a
    library, not a test file, so it exposes the scrub this way; a caller's own pytest fixture (typically
    ``@pytest.fixture(autouse=True)`` doing ``monkeypatch.delenv(...)``) is the normal way to apply it in
    a test suite - this context manager exists for non-pytest callers of this module.
    """
    saved = {name: os.environ.pop(name, None) for name in (_ENDPOINT_ENV, _API_KEY_ENV)}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@dataclass
class ConformancePilot:
    """One baked pilot: its scope and the raw lexical-index documents loaded for it.

    ``documents`` holds the raw per-chunk dicts exactly as ``GenerationManifestStore`` returns them
    (each carries at least a ``"text"`` key); callers that want typed items build their own, the same
    way ``test_round3_retrieval_corpus.py``'s own ``make_item`` does from this same raw text.
    """

    name: str
    scope: Scope
    documents: list[dict[str, Any]] = field(default_factory=list)

    def chunks_of_kind(self, kind: str) -> list[dict[str, Any]]:
        """The raw documents whose chunk text classifies as *kind* (``relevance.chunk_kind``'s own
        ``SYMBOL`` | ``EXAMPLE`` | ``DOCUMENTATION`` constants)."""
        return [document for document in self.documents if chunk_kind(document["text"]) == kind]

    def doc_chunks(self) -> list[dict[str, Any]]:
        return self.chunks_of_kind(DOCUMENTATION)

    def example_chunks(self) -> list[dict[str, Any]]:
        return self.chunks_of_kind(EXAMPLE)

    def symbol_chunks(self) -> list[dict[str, Any]]:
        return self.chunks_of_kind(SYMBOL)


class ConformanceCorpus:
    """A loaded set of pilots over an on-disk root holding ``*__self_extracted`` directories.

    ``root`` must already hold the pilot directories (a copy of ``infra/demo-manifests``'s own, or a
    subset): this mirrors ``test_round3_retrieval_corpus.py``'s own ``Corpus`` class, whose ``__init__``
    likewise only loads from a root a caller already populated. Use ``copy_demo_manifests_to`` below (or
    ``load_demo_manifests_corpus``) to do the copy-then-load in one call against the real, committed
    ``infra/demo-manifests`` directory - this package never writes into that real directory itself.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = GenerationManifestStore(root)
        self.pilots: dict[str, ConformancePilot] = {}
        for directory in sorted(root.glob("*__self_extracted")):
            family, platform, source_kind = directory.name.split("__")
            scope = Scope(family=family, platform=platform, source_kind=source_kind)
            key = scope_key(scope, "self_extracted")
            generation_id = self.store.read_active(key)
            assert generation_id is not None, directory.name
            documents = self.store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
            pilot_name = f"{family}__{platform}"
            self.pilots[pilot_name] = ConformancePilot(pilot_name, scope, list(documents.values()))


def copy_demo_manifests_to(dest: Path, manifests_root: Path = DEMO_MANIFESTS) -> Path:
    """Copy every ``*__self_extracted`` pilot directory under *manifests_root* into *dest* via
    ``shutil.copytree`` - the exact copy step ``test_round3_retrieval_corpus.py``'s own ``corpus``
    fixture performs before constructing its ``Corpus`` - and return *dest*. Reading the real, committed
    ``infra/demo-manifests`` directory is always allowed; nothing is ever written back into it."""
    for directory in manifests_root.glob("*__self_extracted"):
        if directory.is_dir():
            shutil.copytree(directory, dest / directory.name)
    return dest


def load_demo_manifests_corpus(dest: Path, manifests_root: Path = DEMO_MANIFESTS) -> ConformanceCorpus:
    """``copy_demo_manifests_to`` then ``ConformanceCorpus`` in one call: the full copytree-and-load the
    real ``infra/demo-manifests`` directory, generalized out of ``test_round3_retrieval_corpus.py``'s own
    ``corpus`` fixture + ``Corpus`` class so other test files never have to copy that code again."""
    copy_demo_manifests_to(dest, manifests_root)
    return ConformanceCorpus(dest)


def _resolve_sample_count(total: int, fraction_or_count: int | float) -> int:
    """``fraction_or_count`` is an absolute count if it is an ``int``, or a fraction of *total* in
    ``[0.0, 1.0]`` if it is a ``float``. The result is always clamped to ``[0, total]``."""
    if isinstance(fraction_or_count, float):
        if not 0.0 <= fraction_or_count <= 1.0:
            raise ValueError(f"fraction must be within [0.0, 1.0], got {fraction_or_count!r}")
        count = round(total * fraction_or_count)
    else:
        count = fraction_or_count
    return max(0, min(total, count))


def sample_pilots(corpus: ConformanceCorpus, seed: int, fraction_or_count: int | float) -> list[str]:
    """A deterministic (same *seed* -> same list, every time) sample of pilot names from *corpus*, sized
    by *fraction_or_count* (a fraction of all pilots if a ``float``, an absolute count if an ``int``).
    Uses ``random.Random(seed)``, never the global ``random`` module, so this is pure in *corpus* and
    *seed* alone and never disturbs any other code's random state."""
    names = sorted(corpus.pilots)
    count = _resolve_sample_count(len(names), fraction_or_count)
    rng = random.Random(seed)
    return rng.sample(names, count)


def sample_items[T](items: Sequence[T], seed: int, n: int) -> list[T]:
    """A deterministic (same *seed* -> same list, every time) sample of *n* items from *items*, using
    ``random.Random(seed)`` - never the global ``random`` module. *n* is clamped to ``len(items)``."""
    pool = list(items)
    count = max(0, min(len(pool), n))
    rng = random.Random(seed)
    return rng.sample(pool, count)
