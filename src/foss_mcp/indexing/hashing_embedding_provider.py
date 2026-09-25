"""A real, production hashing-trick bag-of-words ``EmbeddingProvider``.

This is explicitly a v1: a real, honest, deterministic, offline, non-ML hashing-trick
bag-of-words vectorizer - NOT a semantic embedding model. Unlike
``tests/indexing/test_index_writers.py``'s ``DeterministicEmbeddingProvider`` (which
hashes the WHOLE text into one opaque digest and admits in its own docstring it proves
"nothing about embedding quality" - two texts sharing every word still map to unrelated
vectors), ``HashingEmbeddingProvider`` has real, meaningful structure: two texts that
share more vocabulary produce vectors with higher cosine similarity than two texts
sharing none. That is the actual, load-bearing difference between a wiring-test double
and a real (if simple) v1 production embedding.

A future card may replace this with a real semantic embedding model once one is
actually needed; until then, this is what ``infra/ingest.py``'s
``--embedding-provider module.path:ClassName`` should point at for a real ingestion run.

Implementation notes:

- Tokenization is a simple lowercase split on runs of non-alphanumeric characters.
- Each token is hashed to a bucket index via ``hashlib.sha256`` (never Python's
  built-in, per-process-randomized ``hash()``), so the mapping is stable across
  processes and across runs.
- Term-frequency counts are accumulated per bucket (the classic "hashing trick"
  bag-of-words vectorizer), then the resulting vector is L2-normalized so cosine
  similarity between vectors is well-behaved.
- Zero ML/network dependencies: only ``hashlib`` and ``re`` from the standard library.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

Vector = tuple[float, ...]

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class HashingEmbeddingProvider:
    """A hashing-trick bag-of-words ``EmbeddingProvider``.

    Satisfies the ``EmbeddingProvider`` protocol declared in
    ``foss_mcp.indexing.embedding_provider``: it exposes a ``dimension`` attribute and
    an ``embed`` method returning one L2-normalized vector of length ``dimension`` per
    input text, in the same order.
    """

    def __init__(self, dimension: int = 64) -> None:
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self.dimension = dimension

    def _bucket(self, token: str) -> int:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % self.dimension

    def _tokenize(self, text: str) -> list[str]:
        return _TOKEN_PATTERN.findall(text.lower())

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for text in texts:
            counts = [0.0] * self.dimension
            for token in self._tokenize(text):
                counts[self._bucket(token)] += 1.0
            norm = math.sqrt(sum(value * value for value in counts))
            if norm > 0.0:
                counts = [value / norm for value in counts]
            vectors.append(tuple(counts))
        return vectors
