"""G3/TC-849: cohort C2 (the six .NET pilots: pdf, slides, words, cells, email and 3d) serves freshly regenerated, gateway-dimension data (REQ-G3-017).

The committed infra/demo-manifests data of these pilots was baked before the one-chunk verified examples, the honest
verification levels, the authored examples and the stub disclosures, and for most of them carried 64-dim hashing
vectors, which makes find_examples permanently lexical-only. This test pins, offline from the committed files only:

* every vector index of the active generation is 4096-dimensional and every Example chunk has a vector point;
* the pilot is not listed in the stale tables of tests/deploy/test_fleet_consistency.py;
* every Example chunk carries ``Verification-Level:`` and ``Verified-With:`` lines and no chunk holds two examples;
* the pilot's tests/fixtures/verified_examples cache file exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.deploy.test_fleet_consistency import STALE_BAKED_PILOTS, STALE_BY_MEMBERS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
VERIFIED_EXAMPLES = REPO_ROOT / "tests" / "fixtures" / "verified_examples"

COHORT_C2 = (
    "pdf_net",
    "slides_net",
    "words_net",
    "cells_net",
    "email_net",
    "3d_net",
)
GATEWAY_DIMENSION = 4096
_EXAMPLE_FQN_LINE = re.compile(r"^FQN: Example: ", re.MULTILINE)


def _scope_dir(key: str) -> Path:
    family, platform = key.split("_", 1)
    return DEMO_MANIFESTS / f"{family}__{platform}__self_extracted"


def _active_generation(key: str) -> dict[str, Any]:
    scope = _scope_dir(key)
    pointer = json.loads((scope / "active_pointer.json").read_text(encoding="utf-8"))
    version = pointer["generation_id"].rsplit("::", 1)[-1]
    return json.loads((scope / f"generation__{version}.json").read_text(encoding="utf-8"))


def _example_chunks(generation: dict[str, Any]) -> dict[str, str]:
    documents = generation["payload"]["lexical_index"]["documents"]
    return {
        chunk_id: str(chunk.get("text", ""))
        for chunk_id, chunk in documents.items()
        if _EXAMPLE_FQN_LINE.search(str(chunk.get("text", "")))
    }


def test_the_cohort_is_the_six_dotnet_pilots() -> None:
    assert len(COHORT_C2) == 6
    assert all(key.endswith("_net") for key in COHORT_C2)


@pytest.mark.parametrize("key", COHORT_C2)
def test_every_vector_is_gateway_dimension_and_every_example_has_a_point(key: str) -> None:
    generation = _active_generation(key)
    vector_index = generation["payload"]["vector_index"]
    assert vector_index["dimension"] == GATEWAY_DIMENSION, f"{key}: vector index dimension"
    points = vector_index["points"]
    assert points, f"{key}: the vector index has no points"
    assert {len(point["vector"]) for point in points} == {GATEWAY_DIMENSION}, (
        f"{key}: a point is not 4096-dim"
    )
    examples = _example_chunks(generation)
    assert examples, f"{key}: no Example chunk"
    point_texts = {str(point["text"]) for point in points}
    missing = sorted(chunk_id for chunk_id, text in examples.items() if text not in point_texts)
    assert not missing, f"{key}: Example chunks without a vector point: {missing}"


@pytest.mark.parametrize("key", COHORT_C2)
def test_the_pilot_is_not_listed_stale(key: str) -> None:
    assert key not in STALE_BAKED_PILOTS, f"{key} is regenerated: it must not be in STALE_BAKED_PILOTS"
    assert key not in STALE_BY_MEMBERS, f"{key} is regenerated: it must not be in STALE_BY_MEMBERS"


@pytest.mark.parametrize("key", COHORT_C2)
def test_example_chunks_are_verified_and_hold_one_example_each(key: str) -> None:
    examples = _example_chunks(_active_generation(key))
    assert examples, f"{key}: no Example chunk"
    for chunk_id, text in examples.items():
        assert re.search(r"^Verification-Level: \S", text, re.MULTILINE), (
            f"{key} {chunk_id}: no Verification-Level"
        )
        assert re.search(r"^Verified-With: \S", text, re.MULTILINE), f"{key} {chunk_id}: no Verified-With"
        assert len(_EXAMPLE_FQN_LINE.findall(text)) == 1, (
            f"{key} {chunk_id}: more than one example in one chunk"
        )


@pytest.mark.parametrize("key", COHORT_C2)
def test_the_verified_examples_cache_file_exists(key: str) -> None:
    path = VERIFIED_EXAMPLES / f"{key}.json"
    assert path.is_file(), f"{key}: {path} is missing"
    assert json.loads(path.read_text(encoding="utf-8")), f"{key}: the verified-examples cache is empty"


@pytest.mark.parametrize("key", COHORT_C2)
def test_every_example_chunk_ran_and_produced_output(key: str) -> None:
    """TC-802 publishes no example whose run failed or printed nothing, so every published chunk is at the top level."""
    for chunk_id, text in _example_chunks(_active_generation(key)).items():
        assert re.search(r"^Verification-Level: ran-produced-output$", text, re.MULTILINE), (key, chunk_id)


# TC-849: the authored examples that win back answers the README examples (which crashed or printed nothing) lost.
TC849_AUTHORED_EXAMPLES = (
    ("pdf_net", "Extract Text Fragments from a Saved Page"),
    ("3d_net", "Save a Box Mesh as an STL File"),
    ("3d_net", "Save a Scene as glTF and Count Its Nodes and Meshes"),
)


@pytest.mark.parametrize(("key", "title"), TC849_AUTHORED_EXAMPLES)
def test_the_tc849_authored_examples_are_published_authored_and_ran(key: str, title: str) -> None:
    chunks = [
        text
        for text in _example_chunks(_active_generation(key)).values()
        if text.startswith(f"FQN: Example: {title}\n")
    ]
    assert len(chunks) == 1, (key, title)
    assert re.search(r"^Provenance: authored$", chunks[0], re.MULTILINE)
    assert re.search(r"^Verification-Level: ran-produced-output$", chunks[0], re.MULTILINE)
