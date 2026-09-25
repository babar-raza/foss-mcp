"""Tests for the real, production hashing-trick ``HashingEmbeddingProvider``.

Every assertion here works from real, computed vectors returned by ``embed`` - nothing
is mocked and nothing asserts on internal implementation details (bucket indices, token
lists, etc). The point of this suite is to prove the two things that actually matter for
a production embedding provider: it is genuinely deterministic, and it has real
similarity structure - texts that share vocabulary must come out measurably closer
(by cosine similarity) than texts that share none, unlike the whole-string-hash
``DeterministicEmbeddingProvider`` confined to ``tests/indexing/test_index_writers.py``.
"""

from __future__ import annotations

import math

from foss_mcp.indexing.hashing_embedding_provider import HashingEmbeddingProvider


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def test_the_same_text_always_embeds_to_the_identical_vector() -> None:
    provider = HashingEmbeddingProvider()
    text = "The Document class opens and saves PDF files."
    assert provider.embed([text]) == provider.embed([text])

    # Also true across a fresh provider instance - determinism must not depend on any
    # per-instance state, only on the text and the (hashlib-based) hash function.
    assert HashingEmbeddingProvider().embed([text]) == provider.embed([text])


def test_every_vector_has_length_equal_to_dimension() -> None:
    default_provider = HashingEmbeddingProvider()
    vectors = default_provider.embed(["hello world", "a second, different text!"])
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == default_provider.dimension == 64

    small_provider = HashingEmbeddingProvider(dimension=16)
    [small_vector] = small_provider.embed(["hello world"])
    assert len(small_vector) == 16


def test_shared_vocabulary_yields_higher_cosine_similarity_than_disjoint_vocabulary() -> None:
    provider = HashingEmbeddingProvider()

    text_a = "The Document class opens and saves PDF files quickly and safely."
    text_b = "A Document instance also opens and saves PDF files reliably."
    text_c = "Bananas trains oceans mountains guitars velvet clouds whisper silently."

    vector_a, vector_b, vector_c = provider.embed([text_a, text_b, text_c])

    similarity_ab = _cosine_similarity(vector_a, vector_b)
    similarity_ac = _cosine_similarity(vector_a, vector_c)
    similarity_bc = _cosine_similarity(vector_b, vector_c)

    # text_a and text_b share real vocabulary (document, opens, and, saves, pdf, files);
    # text_c shares none of it with either. The shared-vocabulary pair must be strictly
    # closer than either disjoint-vocabulary pairing - this is the real similarity
    # structure a whole-string-hash provider cannot have.
    assert similarity_ab > similarity_ac
    assert similarity_ab > similarity_bc


def test_each_vector_is_l2_normalized() -> None:
    provider = HashingEmbeddingProvider()
    [vector] = provider.embed(["repeated repeated repeated words words distinct"])
    norm = math.sqrt(sum(component * component for component in vector))
    assert math.isclose(norm, 1.0, rel_tol=1e-9, abs_tol=1e-9)


def test_empty_text_embeds_to_the_zero_vector_without_error() -> None:
    provider = HashingEmbeddingProvider()
    [vector] = provider.embed([""])
    assert len(vector) == provider.dimension
    assert all(component == 0.0 for component in vector)
