"""A replay-only ``EmbeddingProvider`` seeded from vectors already baked into
``infra/demo-manifests`` (the mission plan's H3, REQ-G3-017/TC-601).

This provider makes NO network call and calls no embedding model at all: it is a pure
offline lookup from already-computed text -> vector pairs, extracted once (by TC-601's own
closeout-documented extraction run) into committed cache files under
``infra/demo-inputs/embeddings/<family>__<platform>.json``
(``{"dimension": <int>, "entries": {<sha256 of text>: [<float>, ...], ...}}``). It exists so a
future hermetic regeneration command (H6, not yet built) can reproduce today's example-chunk
vectors offline instead of needing a live embedding gateway
(``foss_mcp.indexing.gateway_embedding_provider.GatewayEmbeddingProvider``).

Configuration is read from the environment inside a ZERO-ARGUMENT ``__init__`` - the same
convention ``GatewayEmbeddingProvider`` and ``infra/ingest.py``'s
``_load_embedding_provider`` require (``getattr(module, class_name)()``, no arguments):

- ``FOSS_MCP_CACHED_EMBEDDING_ROOT`` (optional) - the directory holding
  ``<family>__<platform>.json`` cache files. Defaults to ``infra/demo-inputs/embeddings/``
  resolved relative to this module's own location (mirroring how
  ``scripts/release/compact_demo_manifests.py`` locates ``infra/demo-manifests`` relative to
  itself), so this provider works whether or not the package is installed.
- ``FOSS_MCP_CACHED_EMBEDDING_SCOPE`` (optional) - a value like ``pdf__net`` naming exactly one
  pilot's cache file (``<scope>.json``) to load. When unset, every ``*.json`` file directly
  under the root is loaded and merged into one combined lookup (last file wins on a key
  collision - sha256 collisions do not happen in practice, so this is never exercised, but no
  special case is added to guard against it either).

Text is looked up by ``sha256(text.encode("utf-8")).hexdigest()`` alone: there is exactly one
embedding configuration in play in this repository (see the baked payload's own shape - no
"model name" field is stored there), so the text itself is already fully
model-and-decimals-specific by construction.

A cache miss NEVER substitutes a placeholder vector: ``embed()`` raises
``CachedEmbeddingMissError`` naming every missing text's hash, so a caller knows exactly what to
add via a future ``refresh-inputs`` step (H5, not yet built).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

Vector = tuple[float, ...]

_ROOT_ENV = "FOSS_MCP_CACHED_EMBEDDING_ROOT"
_SCOPE_ENV = "FOSS_MCP_CACHED_EMBEDDING_SCOPE"

# infra/demo-inputs/embeddings/ resolved relative to this module's own location:
# src/foss_mcp/indexing/cached_embedding_provider.py -> parents[3] is the repository root.
_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "infra" / "demo-inputs" / "embeddings"


class CachedEmbeddingMissError(KeyError):
    """Raised by ``embed()`` when one or more texts have no entry in the loaded cache.

    The message lists every missing text's sha256 hash (never the raw text itself, which could
    be large) so a caller knows exactly what to add to the committed cache via a future
    ``refresh-inputs`` step (H5).
    """


class CachedEmbeddingDimensionConflictError(ValueError):
    """Raised when two loaded cache files disagree on ``dimension``.

    The message names both conflicting files and their dimensions.
    """


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_cache_file(path: Path) -> dict[str, Any]:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "dimension" not in raw or "entries" not in raw:
        raise ValueError(f"{path}: not a valid cached-embedding file (expects dimension/entries)")
    dimension = raw["dimension"]
    entries = raw["entries"]
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
        raise ValueError(f"{path}: dimension must be a positive integer, got {dimension!r}")
    if not isinstance(entries, dict):
        raise ValueError(f"{path}: entries must be an object")
    return raw


class CachedEmbeddingProvider:
    """A replay-only ``EmbeddingProvider`` (see ``foss_mcp.indexing.embedding_provider``) over
    a committed cache of already-computed text -> vector pairs. See this module's own
    docstring for the full configuration and miss-handling contract.
    """

    def __init__(self) -> None:
        root = Path(os.environ.get(_ROOT_ENV, "").strip() or _DEFAULT_ROOT)
        scope = os.environ.get(_SCOPE_ENV, "").strip()

        if scope:
            cache_paths = [root / f"{scope}.json"]
        else:
            if not root.is_dir():
                raise ValueError(f"{_ROOT_ENV} directory does not exist: {root}")
            cache_paths = sorted(root.glob("*.json"))

        if not cache_paths:
            raise ValueError(f"no cached-embedding files found under {root} (scope={scope!r})")

        lookup: dict[str, Vector] = {}
        dimension: int | None = None
        dimension_source: Path | None = None
        for path in cache_paths:
            if not path.is_file():
                raise ValueError(f"cached-embedding file does not exist: {path}")
            loaded = _load_cache_file(path)
            file_dimension = loaded["dimension"]
            if dimension is None:
                dimension = file_dimension
                dimension_source = path
            elif file_dimension != dimension:
                raise CachedEmbeddingDimensionConflictError(
                    f"conflicting cached-embedding dimensions: {dimension_source} has "
                    f"dimension {dimension}, {path} has dimension {file_dimension}"
                )
            for text_hash, vector in loaded["entries"].items():
                lookup[text_hash] = tuple(float(value) for value in vector)

        assert dimension is not None  # at least one cache_path is always loaded above
        self.dimension = dimension
        self._lookup = lookup

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One vector of length ``self.dimension`` per input text, in the same order.

        Raises ``CachedEmbeddingMissError`` naming every missing text's hash if ANY text has
        no entry in the loaded cache - never substitutes a placeholder vector.
        """
        materialized = list(texts)
        hashes = [_text_hash(text) for text in materialized]
        missing = sorted({h for h in hashes if h not in self._lookup})
        if missing:
            raise CachedEmbeddingMissError(
                f"CachedEmbeddingProvider has no entry for {len(missing)} text hash(es): {', '.join(missing)}"
            )
        return [self._lookup[h] for h in hashes]
