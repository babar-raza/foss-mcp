"""Tests for ``foss_mcp.indexing.cached_embedding_provider.CachedEmbeddingProvider`` (TC-601).

Cases (a)-(d) use NO mocks and NO synthetic cache files: they read the REAL extracted
``infra/demo-inputs/embeddings/`` files (committed by TC-601's own extraction run) and the REAL
``infra/demo-manifests`` generation payloads they were extracted from, so a regression that
breaks the actual replay (not merely the shape of ``embed()``'s return value) is caught. Only
case (e) - a dimension conflict, which cannot be provoked from the real committed data because
every real pilot agrees on dimension - uses small synthetic cache files written to ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from foss_mcp.indexing.cached_embedding_provider import (
    CachedEmbeddingDimensionConflictError,
    CachedEmbeddingMissError,
    CachedEmbeddingProvider,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CACHE_ROOT = _REPO_ROOT / "infra" / "demo-inputs" / "embeddings"
_MANIFESTS_ROOT = _REPO_ROOT / "infra" / "demo-manifests"

_SEP = "::"

# Two real, distinct pilots used across the tests below.
_PILOT_A = "pdf__net"
_PILOT_B = "cells__go"


def _active_generation_points(scope_dir_name: str) -> tuple[int, list[dict]]:
    """The real (dimension, points) for one ``infra/demo-manifests/<scope_dir_name>`` scope,
    read from its own active generation - exactly what TC-601's extraction script reads."""
    scope_dir = _MANIFESTS_ROOT / scope_dir_name
    pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
    generation_id = pointer["generation_id"]
    version = generation_id.rsplit(_SEP, 1)[-1]
    gen_path = scope_dir / f"generation__{version}.json"
    doc = json.loads(gen_path.read_text(encoding="utf-8"))
    vector_index = doc["payload"]["vector_index"]
    return vector_index["dimension"], vector_index["points"]


def test_scoped_provider_matches_real_baked_vectors_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) Scoped to one real pilot, embed() on that pilot's own real texts returns vectors
    byte-identical (exact float-list equality) to the real baked vectors for those exact texts."""
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(_CACHE_ROOT))
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", _PILOT_A)

    dimension, points = _active_generation_points(f"{_PILOT_A}__self_extracted")
    assert points, f"{_PILOT_A} has no real example-chunk points to test against"

    provider = CachedEmbeddingProvider()
    assert provider.dimension == dimension

    texts = [point["text"] for point in points]
    expected = [tuple(point["vector"]) for point in points]
    actual = provider.embed(texts)

    assert actual == expected


def test_unscoped_provider_merges_two_real_pilots(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) With no scope set (loads everything), the merged lookup still resolves pilot A's
    own texts correctly, PLUS at least one text from a different real pilot (B) - proving the
    merge actually merges rather than loading only one file."""
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(_CACHE_ROOT))
    monkeypatch.delenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", raising=False)

    dimension_a, points_a = _active_generation_points(f"{_PILOT_A}__self_extracted")
    dimension_b, points_b = _active_generation_points(f"{_PILOT_B}__self_extracted")
    assert points_a and points_b

    provider = CachedEmbeddingProvider()
    assert provider.dimension == dimension_a
    assert provider.dimension == dimension_b

    texts = [points_a[0]["text"], points_b[0]["text"]]
    expected = [tuple(points_a[0]["vector"]), tuple(points_b[0]["vector"])]
    actual = provider.embed(texts)

    assert actual == expected


def test_missing_text_raises_error_naming_its_real_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """(c) embed() with one real text plus one fabricated text raises the missing-hash error,
    and the error message contains the fabricated text's exact real sha256 hash."""
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(_CACHE_ROOT))
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", _PILOT_A)

    _, points = _active_generation_points(f"{_PILOT_A}__self_extracted")
    real_text = points[0]["text"]
    fabricated_text = "TC-601 negative-control fabricated text, definitely not in any cache"
    fabricated_hash = hashlib.sha256(fabricated_text.encode("utf-8")).hexdigest()

    provider = CachedEmbeddingProvider()

    with pytest.raises(CachedEmbeddingMissError) as excinfo:
        provider.embed([real_text, fabricated_text])

    assert fabricated_hash in str(excinfo.value)


def test_dimension_matches_real_baked_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """(d) self.dimension matches the real baked vector_index["dimension"], read from the data
    itself - never hardcoded as a literal in this assertion."""
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(_CACHE_ROOT))
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", _PILOT_A)

    expected_dimension, _ = _active_generation_points(f"{_PILOT_A}__self_extracted")

    provider = CachedEmbeddingProvider()

    assert provider.dimension == expected_dimension


def test_conflicting_dimensions_raise_value_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(e) Two small synthetic cache files with different ``dimension`` values raise a clear
    ValueError naming the conflicting files. A real dimension conflict cannot be provoked from
    the committed data (every real pilot agrees on dimension), so this one case is synthetic."""
    (tmp_path / "fake_a__one.json").write_text(
        json.dumps({"dimension": 2, "entries": {"a" * 64: [0.1, 0.2]}}),
        encoding="utf-8",
    )
    (tmp_path / "fake_b__two.json").write_text(
        json.dumps({"dimension": 3, "entries": {"b" * 64: [0.1, 0.2, 0.3]}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(tmp_path))
    monkeypatch.delenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", raising=False)

    with pytest.raises(CachedEmbeddingDimensionConflictError) as excinfo:
        CachedEmbeddingProvider()

    message = str(excinfo.value)
    assert "fake_a__one.json" in message
    assert "fake_b__two.json" in message
