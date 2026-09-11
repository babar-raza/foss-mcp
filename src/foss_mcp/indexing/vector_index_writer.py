"""Build the vector-index payload for one generation, over the embedded topology TC-004 chose.

Index identity is generation-qualified: ``point_id`` folds ``generation_id`` into every point's
identity. A content-only point id is the confirmed defect that made rollback self-admittedly
broken in the reference system - two generations built from byte-identical chunk content would
collide on the same points, so activating one generation's index would silently also touch
points the other generation believes are its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from foss_mcp.indexing.embedding_provider import EmbeddingProvider, Vector
from foss_mcp.normalization.chunker import Chunk


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
) -> dict:
    """The vector-index payload for ``generation_id``: one point per chunk.

    ``chunk_ids`` is content-derived and stable across generations (see
    ``foss_mcp.indexing.publisher.content_chunk_id``); only the resulting ``point_id`` differs
    between two generations built from identical content.
    """
    if len(chunks) != len(chunk_ids):
        raise ValueError("chunks and chunk_ids must be the same length, pairwise")
    vectors = embedding_provider.embed([chunk.text for chunk in chunks])
    points = [
        VectorPoint(
            point_id=point_id(generation_id, chunk_id),
            chunk_id=chunk_id,
            vector=vector,
            text=chunk.text,
        )
        for chunk_id, chunk, vector in zip(chunk_ids, chunks, vectors, strict=True)
    ]
    return {
        "generation_id": generation_id,
        "dimension": embedding_provider.dimension,
        "points": [
            {"point_id": p.point_id, "chunk_id": p.chunk_id, "vector": list(p.vector), "text": p.text}
            for p in points
        ],
    }


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def query_vector_index(payload: dict, query_vector: Vector, top_k: int = 5) -> list[str]:
    """The ``top_k`` point ids most similar to ``query_vector`` by cosine similarity."""
    scored = [(_cosine(query_vector, point["vector"]), point["point_id"]) for point in payload["points"]]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [pid for _, pid in scored[:top_k]]
