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


def _cache_file_entries(pilot: str) -> tuple[int, dict[str, list[float]]]:
    """The (dimension, entries) of one pilot's real committed cache file."""
    raw = json.loads((_CACHE_ROOT / f"{pilot}.json").read_text(encoding="utf-8"))
    return raw["dimension"], raw["entries"]


def _all_cache_pilots() -> list[str]:
    return sorted(path.stem for path in _CACHE_ROOT.glob("*.json"))


def _pilots_whose_baked_dimension_equals_the_cache_dimension() -> list[str]:
    """Every pilot whose real baked vector index has the same dimension as its real cache file.

    The cache holds 4096-dimension gateway vectors. Since the 2026-11 regression 31 of the 32
    committed generations are 64-dimension hashing vectors (see ``_PILOTS_WITH_64_DIM_BAKED_VECTORS``),
    so a cache-vs-baked comparison is only meaningful for the pilots that still agree.
    """
    matching = []
    for pilot in _all_cache_pilots():
        cache_dimension, _ = _cache_file_entries(pilot)
        baked_dimension, _ = _active_generation_points(f"{pilot}__self_extracted")
        if baked_dimension == cache_dimension:
            matching.append(pilot)
    return matching


# The pilots whose CURRENT committed generation carries 64-dimension hashing vectors instead of the
# 4096-dimension gateway vectors the cache holds (the 2026-11 regression; imaging__net is the only
# 4096-dimension pilot). The regeneration cards (REGEN-1) shrink this set to empty; when the last
# one lands, replace the explicit list with the empty set and flip the assertion below to "no
# pilot has 64-dimension baked vectors" - do not delete the test.
_PILOTS_WITH_64_DIM_BAKED_VECTORS = frozenset(
    {
        "3d__java",
        "3d__net",
        "3d__python",
        "3d__typescript",
        "barcode__python",
        "cells__cpp",
        "cells__go",
        "cells__java",
        "cells__net",
        "cells__python",
        "cells__rust",
        "cells__typescript",
        "email__cpp",
        "email__net",
        "email__python",
        "html__python",
        "note__python",
        "page__python",
        "pdf__cpp",
        "pdf__go",
        "pdf__java",
        "pdf__net",
        "pdf__python",
        "pdf__typescript",
        "slides__cpp",
        "slides__java",
        "slides__net",
        "slides__python",
        "tex__python",
        "words__net",
        "words__python",
    }
)


def test_current_set_of_pilots_with_64_dim_baked_vectors_is_pinned_explicitly() -> None:
    """Companion to the cache-vs-baked comparisons below: pins the CURRENT regression set so the
    comparison tests cannot silently stop covering pilots. When the regeneration cards bring a pilot
    back to 4096-dimension vectors this fails until the pin is updated, and when the set reaches
    empty the assertion becomes "no pilot has 64-dimension baked vectors"."""
    cache_pilots = _all_cache_pilots()
    sixty_four = {
        pilot for pilot in cache_pilots if _active_generation_points(f"{pilot}__self_extracted")[0] == 64
    }

    assert len(_PILOTS_WITH_64_DIM_BAKED_VECTORS) == 31
    assert sixty_four == _PILOTS_WITH_64_DIM_BAKED_VECTORS
    # every pilot is either 64-dimension or agrees with the cache (nothing is a third dimension)
    assert set(cache_pilots) - sixty_four == set(_pilots_whose_baked_dimension_equals_the_cache_dimension())


def test_scoped_provider_matches_real_baked_vectors_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) Scoped to one real pilot, embed() on that pilot's own real texts returns vectors
    byte-identical (exact float-list equality) to the real baked vectors for those exact texts.

    Re-pinned (TC-830): checked for EVERY pilot whose baked dimension equals the cache dimension
    (``imaging__net`` today) instead of the hard-coded ``pdf__net``, whose baked vectors are now
    64-dimension hashing vectors that can never equal the cached 4096-dimension ones. The set of
    such pilots must be non-empty so this cannot pass vacuously."""
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(_CACHE_ROOT))

    matching = _pilots_whose_baked_dimension_equals_the_cache_dimension()
    assert matching, "no pilot has baked vectors of the cache's dimension: nothing to compare against"

    for pilot in matching:
        monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", pilot)
        dimension, points = _active_generation_points(f"{pilot}__self_extracted")
        assert points, f"{pilot} has no real example-chunk points to test against"

        provider = CachedEmbeddingProvider()
        assert provider.dimension == dimension

        texts = [point["text"] for point in points]
        expected = [tuple(point["vector"]) for point in points]
        actual = provider.embed(texts)

        assert actual == expected, pilot


def test_unscoped_provider_merges_two_real_pilots(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) With no scope set (loads everything), the merged lookup still resolves pilot A's
    own texts correctly, PLUS at least one text from a different real pilot (B) - proving the
    merge actually merges rather than loading only one file.

    Re-pinned (TC-830): pilot A's and B's baked vectors are 64-dimension hashing vectors today, so
    the expected vectors are read from the two pilots' real committed CACHE files (the data this
    provider replays), keyed by the real baked texts' hashes; ``provider.dimension`` is compared
    with both cache files' declared dimension. The baked-vector comparison itself lives in (a)."""
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(_CACHE_ROOT))
    monkeypatch.delenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", raising=False)

    dimension_a, entries_a = _cache_file_entries(_PILOT_A)
    dimension_b, entries_b = _cache_file_entries(_PILOT_B)
    _, points_a = _active_generation_points(f"{_PILOT_A}__self_extracted")
    _, points_b = _active_generation_points(f"{_PILOT_B}__self_extracted")

    def cached_text(points: list[dict], entries: dict[str, list[float]]) -> str:
        return next(
            point["text"]
            for point in points
            if hashlib.sha256(point["text"].encode("utf-8")).hexdigest() in entries
        )

    text_a = cached_text(points_a, entries_a)
    text_b = cached_text(points_b, entries_b)

    provider = CachedEmbeddingProvider()
    assert provider.dimension == dimension_a
    assert provider.dimension == dimension_b

    expected = [
        tuple(entries_a[hashlib.sha256(text_a.encode("utf-8")).hexdigest()]),
        tuple(entries_b[hashlib.sha256(text_b.encode("utf-8")).hexdigest()]),
    ]
    actual = provider.embed([text_a, text_b])

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
    itself - never hardcoded as a literal in this assertion.

    Re-pinned (TC-830): asserted for every pilot whose baked dimension equals its cache file's
    dimension (non-empty set), and for EVERY pilot ``provider.dimension`` equals the dimension
    declared by that pilot's own cache file (the cache is what this provider replays)."""
    monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_ROOT", str(_CACHE_ROOT))

    matching = _pilots_whose_baked_dimension_equals_the_cache_dimension()
    assert matching, "no pilot has baked vectors of the cache's dimension: nothing to compare against"

    for pilot in _all_cache_pilots():
        monkeypatch.setenv("FOSS_MCP_CACHED_EMBEDDING_SCOPE", pilot)
        provider = CachedEmbeddingProvider()
        cache_dimension, _ = _cache_file_entries(pilot)
        assert provider.dimension == cache_dimension, pilot
        if pilot in matching:
            baked_dimension, _ = _active_generation_points(f"{pilot}__self_extracted")
            assert provider.dimension == baked_dimension, pilot


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
