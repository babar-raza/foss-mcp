"""Build the vector-index payload for one generation, over the embedded topology TC-004 chose.

Index identity is generation-qualified: ``point_id`` folds ``generation_id`` into every point's
identity. A content-only point id is the confirmed defect that made rollback self-admittedly
broken in the reference system - two generations built from byte-identical chunk content would
collide on the same points, so activating one generation's index would silently also touch
points the other generation believes are its own.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from foss_mcp.indexing.embedding_provider import EmbeddingProvider, Vector
from foss_mcp.normalization.chunker import Chunk

# The marker that makes a chunk "example-bearing". ``foss_mcp.mcp.tools.find_examples`` keeps
# its own private copy (``_EXAMPLE_MARKER``) and only ever returns vector points whose text
# contains it; tests/indexing/test_vector_scope.py pins the two copies equal so neither can
# drift. This module does not import ``foss_mcp.mcp`` - the dependency would run the wrong way.
EXAMPLE_MARKER = "Example:"


def is_example_chunk(chunk: Chunk) -> bool:
    """True when ``chunk`` carries an ``Example:`` block - the only chunks find_examples'
    vector fallback can ever return, and so the only ones an examples-only vector scope embeds.
    """
    return EXAMPLE_MARKER in chunk.text


def point_id(generation_id: str, chunk_id: str) -> str:
    """A vector point's identity - always generation-qualified, never chunk_id alone."""
    return f"{generation_id}::point::{chunk_id}"


@dataclass(frozen=True)
class VectorPoint:
    point_id: str
    chunk_id: str
    vector: Vector
    text: str


def build_vector_index(
    chunks: Sequence[Chunk],
    chunk_ids: Sequence[str],
    embedding_provider: EmbeddingProvider,
    generation_id: str,
    *,
    include: Callable[[Chunk], bool] | None = None,
) -> dict:
    """The vector-index payload for ``generation_id``: one point per chunk.

    ``chunk_ids`` is content-derived and stable across generations (see
    ``foss_mcp.indexing.publisher.content_chunk_id``); only the resulting ``point_id`` differs
    between two generations built from identical content.

    ``include`` is an opt-in scope. ``None`` (the default) keeps every chunk, exactly as before.
    A predicate keeps ONLY the chunks it accepts: selection happens first and only the selected
    chunks' texts are passed to ``embedding_provider.embed`` (never embed everything and discard),
    each kept chunk stays paired with its own ``chunk_id``, and when nothing is selected ``embed``
    is not called at all and ``points`` is ``[]``. ``dimension`` and ``generation_id`` keep their
    meaning either way.
    """
    if len(chunks) != len(chunk_ids):
        raise ValueError("chunks and chunk_ids must be the same length, pairwise")
    if include is None:
        selected_chunks = list(chunks)
        selected_chunk_ids = list(chunk_ids)
    else:
        selected = [
            (chunk_id, chunk) for chunk_id, chunk in zip(chunk_ids, chunks, strict=True) if include(chunk)
        ]
        selected_chunk_ids = [chunk_id for chunk_id, _ in selected]
        selected_chunks = [chunk for _, chunk in selected]
    if include is not None and not selected_chunks:
        vectors: list[Vector] = []
    else:
        vectors = embedding_provider.embed([chunk.text for chunk in selected_chunks])
    points = [
        VectorPoint(
            point_id=point_id(generation_id, chunk_id),
            chunk_id=chunk_id,
            vector=vector,
            text=chunk.text,
        )
        for chunk_id, chunk, vector in zip(selected_chunk_ids, selected_chunks, vectors, strict=True)
    ]
    return {
        "generation_id": generation_id,
        "dimension": embedding_provider.dimension,
        "points": [
            {"point_id": p.point_id, "chunk_id": p.chunk_id, "vector": list(p.vector), "text": p.text}
            for p in points
        ],
    }


def _cosine_scores(query_vector: Vector, points: Sequence[dict]) -> list[float]:
    """Cosine similarity between ``query_vector`` and every point's own ``"vector"`` field, in
    one vectorized NumPy pass over all ``n_points`` at once - never a Python-level loop over
    points (TC-336: the old per-point pure-Python loop measured ~103ms-to-several-seconds at
    real pilot-corpus scale; see ``docs/DECISION_LOG.md``'s 2026-10-10 "NumPy added" entry).

    A point (or the query) with a zero L2 norm scores exactly ``0.0`` for that pairing - the
    same tolerance the old pure-Python ``_cosine`` formula gave, preserved exactly. NumPy's own
    division by zero would otherwise silently produce ``inf``/``nan`` instead of raising, so the
    zero-norm case is masked out explicitly with ``numpy.where`` under ``numpy.errstate`` rather
    than left to NumPy's default behavior.
    """
    if not points:
        return []
    matrix = np.asarray([point["vector"] for point in points], dtype=np.float64)
    query = np.asarray(query_vector, dtype=np.float64)
    point_norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(query)
    denominator = point_norms * query_norm
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_scores = (matrix @ query) / denominator
    scores = np.where(denominator != 0.0, raw_scores, 0.0)
    return [float(score) for score in scores]


def query_vector_index(payload: dict, query_vector: Vector, top_k: int = 5) -> list[str]:
    """The ``top_k`` point ids most similar to ``query_vector`` by cosine similarity."""
    points = payload["points"]
    scores = _cosine_scores(query_vector, points)
    scored = [(score, point["point_id"]) for score, point in zip(scores, points, strict=True)]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [pid for _, pid in scored[:top_k]]


def query_vector_index_scored(payload: dict, query_vector: Vector, top_k: int = 5) -> list[tuple[str, float]]:
    """Like ``query_vector_index()``, but pairs each returned point id with its own cosine
    similarity score instead of discarding it - same body as ``query_vector_index()``, in the
    same ``(identifier, score)`` field order ``query_lexical_index_scored()`` uses.

    Added by TC-331 for ``find_examples.py``'s vector-based fallback stage alone: that stage
    needs the real score to enforce ``_VECTOR_SIMILARITY_THRESHOLD``, which
    ``query_vector_index()``'s plain id list cannot carry. ``query_vector_index()``'s own
    signature, behavior, and both existing call sites are untouched by this addition.
    """
    points = payload["points"]
    scores = _cosine_scores(query_vector, points)
    scored = [(score, point["point_id"]) for score, point in zip(scores, points, strict=True)]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [(pid, score) for score, pid in scored[:top_k]]
