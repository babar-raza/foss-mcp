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

``FallbackEmbeddingProvider`` (REQ-G3-017/TC-830) is the cache-then-gateway sibling used by a
regeneration: it looks every text up in the same cache first, calls the configured
``GatewayEmbeddingProvider`` ONLY for the texts that miss, and records each gateway vector back
into the cache (write-back), so the next run of the same inputs is served entirely from the
cache and is byte-stable. It is selected on ``infra/ingest.py`` with
``--embedding-provider cached-gateway``; see its own docstring for its extra environment
variables and counters.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from foss_mcp.indexing.embedding_provider import EmbeddingProvider
from foss_mcp.indexing.gateway_embedding_provider import GatewayEmbeddingProvider
from foss_mcp.indexing.vector_index_writer import quantize_vector

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


def _read_cache_files(cache_paths: Sequence[Path]) -> tuple[int | None, dict[str, Vector]]:
    """Merge every cache file into one lookup (last file wins on a key collision).

    Returns ``(dimension, lookup)`` where ``dimension`` is ``None`` only when ``cache_paths`` is
    empty. Raises ``CachedEmbeddingDimensionConflictError`` when two files disagree on dimension.
    """
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
    return dimension, lookup


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

        dimension, lookup = _read_cache_files(cache_paths)
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


_WRITEBACK_FILE_ENV = "FOSS_MCP_CACHED_EMBEDDING_WRITEBACK_FILE"
_DECIMALS_ENV = "FOSS_MCP_CACHED_EMBEDDING_DECIMALS"

# The committed caches under infra/demo-inputs/embeddings hold vectors rounded to 5 decimals
# (the baked generations were published with ``--vector-decimals 5``), so write-back rounds the
# same way by default.
_DEFAULT_DECIMALS = 5


class FallbackEmbeddingProvider:
    """A cache-then-gateway ``EmbeddingProvider`` with write-back (REQ-G3-017/TC-830).

    ``embed()`` looks every text up in the cache first (the same files, scope and key as
    ``CachedEmbeddingProvider``: ``sha256(text)`` -> vector). Only the texts that MISS are sent,
    once and de-duplicated, to the configured ``GatewayEmbeddingProvider``; each returned vector
    is rounded to ``FOSS_MCP_CACHED_EMBEDDING_DECIMALS`` decimals (default 5, the committed
    caches' precision, via the same ``quantize_vector`` the vector index writer uses), recorded
    in the in-memory lookup, and - when a write-back target exists - persisted to that cache
    file. The vector RETURNED for a miss is the rounded one that was recorded, so a first
    (gateway) run and a second (pure cache) run hand the index writer identical vectors, whether
    or not ``--vector-decimals`` is also set, and the second run makes no gateway call and writes
    nothing.

    Configuration (zero-argument ``__init__``, like every provider ``infra/ingest.py`` loads;
    only environment variable NAMES appear here, never a value):

    - ``FOSS_MCP_CACHED_EMBEDDING_ROOT`` / ``FOSS_MCP_CACHED_EMBEDDING_SCOPE`` - exactly as for
      ``CachedEmbeddingProvider``, except that a scope whose file does not exist yet is simply an
      empty cache (the first regeneration of a new pilot) instead of an error.
    - ``FOSS_MCP_CACHED_EMBEDDING_WRITEBACK_FILE`` (optional) - the file to persist misses into
      when no scope is set. With a scope set, the write-back target is ``<root>/<scope>.json``.
      With neither, misses are recorded in memory only (still counted, still reused within the
      run); ``stats()["persisted"]`` says which.
    - ``FOSS_MCP_CACHED_EMBEDDING_DECIMALS`` (default 5, integer 1..12).
    - Everything ``GatewayEmbeddingProvider`` reads (endpoint, key, model, dimension, batch size,
      request budget). ``dimension`` is the gateway's (4096 by default) and is this provider's.

    Dimension handling is identical to the gateway provider and never pads or truncates: a cache
    file whose declared dimension differs from the gateway's raises
    ``CachedEmbeddingDimensionConflictError``, a cached vector whose length differs from the
    dimension raises ``ValueError`` when loaded, and a gateway vector of the wrong length raises
    ``ValueError`` before anything is recorded.

    Persisted files use the cache writer's own canonical JSON (``sort_keys``, 2-space indent,
    ``ensure_ascii=False``, LF newlines, trailing newline) and are written atomically; existing
    entries are preserved exactly, so only the new keys are added.

    ``stats()`` / ``summary_line()`` report the run's counters: ``hits`` and ``misses`` count input
    texts (a text missing at the time of the call is a miss), ``gateway_calls`` counts gateway
    ``embed()`` invocations and ``gateway_texts`` the distinct texts sent in them.
    """

    def __init__(self, *, _gateway: EmbeddingProvider | None = None) -> None:
        root = Path(os.environ.get(_ROOT_ENV, "").strip() or _DEFAULT_ROOT)
        scope = os.environ.get(_SCOPE_ENV, "").strip()
        writeback = os.environ.get(_WRITEBACK_FILE_ENV, "").strip()

        decimals = _DEFAULT_DECIMALS
        raw_decimals = os.environ.get(_DECIMALS_ENV, "").strip()
        if raw_decimals:
            try:
                decimals = int(raw_decimals)
            except ValueError:
                raise ValueError(f"{_DECIMALS_ENV} must be an integer, got {raw_decimals!r}") from None
        if not 1 <= decimals <= 12:
            raise ValueError(f"{_DECIMALS_ENV} must be between 1 and 12, got {decimals}")
        self._decimals = decimals

        self._gateway: EmbeddingProvider = _gateway if _gateway is not None else GatewayEmbeddingProvider()
        self.dimension = self._gateway.dimension

        if scope:
            candidate_paths = [root / f"{scope}.json"]
            self._target: Path | None = candidate_paths[0]
        else:
            candidate_paths = sorted(root.glob("*.json")) if root.is_dir() else []
            self._target = Path(writeback) if writeback else None
        existing_paths = [path for path in candidate_paths if path.is_file()]

        cache_dimension, lookup = _read_cache_files(existing_paths)
        if cache_dimension is not None and cache_dimension != self.dimension:
            raise CachedEmbeddingDimensionConflictError(
                f"cached-embedding dimension {cache_dimension} (files under {root}) does not match the "
                f"gateway embedding dimension {self.dimension}"
            )
        for text_hash, vector in lookup.items():
            if len(vector) != self.dimension:
                raise ValueError(
                    f"cached vector for text hash {text_hash} has length {len(vector)}, "
                    f"expected the embedding dimension {self.dimension}"
                )
        self._lookup = lookup
        self._new_entries: dict[str, Vector] = {}
        self.hits = 0
        self.misses = 0
        self.gateway_calls = 0
        self.gateway_texts = 0

    def stats(self) -> dict[str, Any]:
        """The run's counters (see the class docstring)."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "gateway_calls": self.gateway_calls,
            "gateway_texts": self.gateway_texts,
            "recorded": len(self._new_entries),
            "persisted": self._target is not None,
        }

    def summary_line(self) -> str:
        return (
            f"embedding cache: hits={self.hits} misses={self.misses} "
            f"gateway_calls={self.gateway_calls} gateway_texts={self.gateway_texts} "
            f"recorded={len(self._new_entries)} persisted={'yes' if self._target is not None else 'no'}"
        )

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One vector of length ``self.dimension`` per input text, in the same order: cache first,
        the gateway only for the distinct texts that miss, each of whose vectors is written back."""
        materialized = list(texts)
        hashes = [_text_hash(text) for text in materialized]
        missing: dict[str, str] = {}
        for text_hash, text in zip(hashes, materialized, strict=True):
            if text_hash in self._lookup:
                self.hits += 1
            else:
                self.misses += 1
                missing.setdefault(text_hash, text)

        if missing:
            fetched = self._gateway.embed(list(missing.values()))
            self.gateway_calls += 1
            self.gateway_texts += len(missing)
            if len(fetched) != len(missing):
                raise RuntimeError(
                    f"the gateway returned {len(fetched)} vector(s) for {len(missing)} text(s) sent"
                )
            recorded: dict[str, Vector] = {}
            for text_hash, vector in zip(missing, fetched, strict=True):
                if len(vector) != self.dimension:
                    raise ValueError(
                        f"the gateway returned a vector of length {len(vector)} for text hash "
                        f"{text_hash}, expected the embedding dimension {self.dimension}"
                    )
                recorded[text_hash] = quantize_vector(vector, self._decimals)
            self._lookup.update(recorded)
            self._new_entries.update(recorded)
            self._persist()

        return [self._lookup[text_hash] for text_hash in hashes]

    def _persist(self) -> None:
        if self._target is None:
            return
        target = self._target
        entries: dict[str, list[float]] = {}
        if target.is_file():
            existing = _load_cache_file(target)
            if existing["dimension"] != self.dimension:
                raise CachedEmbeddingDimensionConflictError(
                    f"{target} has dimension {existing['dimension']}, the embedding dimension is "
                    f"{self.dimension}"
                )
            entries = dict(existing["entries"])
        for text_hash, vector in self._new_entries.items():
            entries[text_hash] = list(vector)
        document = json.dumps(
            {"dimension": self.dimension, "entries": entries}, sort_keys=True, ensure_ascii=False, indent=2
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(document + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, target)
