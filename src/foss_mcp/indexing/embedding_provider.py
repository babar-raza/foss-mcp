"""The embedding provider interface ``vector_index_writer`` depends on.

No concrete provider ships here: a real embedding backend is out of this gate's scope. The
deterministic, offline provider the test suite uses instead lives only under ``tests/``
(``tests/indexing/test_index_writers.py``) and is never imported from a ``src/`` production
path - shipping a test double in a production import path is how a fake embedder quietly
becomes the production behavior.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

Vector = tuple[float, ...]


class EmbeddingProvider(Protocol):
    """Anything ``vector_index_writer`` can turn chunk text into vectors through."""

    dimension: int

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One vector of length ``dimension`` per input text, in the same order."""
        ...
