"""G3/TC-852: cohort C5 (the seven Go, Rust and C++ pilots: pdf/go, cells/go, cells/rust, pdf/cpp, cells/cpp, slides/cpp, email/cpp) serves freshly regenerated, gateway-dimension data (REQ-G3-017).

The committed infra/demo-manifests data of these pilots was baked before the one-chunk verified examples, the honest
verification levels, the authored examples and the stub disclosures, and carried 64-dim hashing vectors, which makes
find_examples permanently lexical-only. This test pins, offline from the committed files only:

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

COHORT_C5 = (
    "pdf_go",
    "cells_go",
    "cells_rust",
    "pdf_cpp",
    "cells_cpp",
    "slides_cpp",
    "email_cpp",
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


def test_the_cohort_is_the_seven_go_rust_and_cpp_pilots() -> None:
    assert len(COHORT_C5) == 7
    assert {key.split("_", 1)[1] for key in COHORT_C5} == {"go", "rust", "cpp"}


@pytest.mark.parametrize("key", COHORT_C5)
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


@pytest.mark.parametrize("key", COHORT_C5)
def test_the_pilot_is_not_listed_stale(key: str) -> None:
    assert key not in STALE_BAKED_PILOTS, f"{key} is regenerated: it must not be in STALE_BAKED_PILOTS"
    assert key not in STALE_BY_MEMBERS, f"{key} is regenerated: it must not be in STALE_BY_MEMBERS"


@pytest.mark.parametrize("key", COHORT_C5)
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


@pytest.mark.parametrize("key", COHORT_C5)
def test_the_verified_examples_cache_file_exists(key: str) -> None:
    path = VERIFIED_EXAMPLES / f"{key}.json"
    assert path.is_file(), f"{key}: {path} is missing"
    assert json.loads(path.read_text(encoding="utf-8")), f"{key}: the verified-examples cache is empty"


# TC-802 publishes no example whose run failed or printed nothing as a verified example. An example that ran to exit 0 and
# wrote nothing is published at ``ran-exit0`` (TC-805). email/cpp is the one pilot whose verifier (the TC-361 consumer
# project) builds and links an example against the library but does not run it: every one of its chunks is labelled
# ``built`` (served as "built against the library; not run"), exactly as honest as the "compiled only" label it replaces.
RAN_LEVELS = ("ran-produced-output", "ran-exit0")
LEVELS_BY_PILOT: dict[str, tuple[str, ...]] = {
    key: ("built",) if key == "email_cpp" else RAN_LEVELS for key in COHORT_C5
}


@pytest.mark.parametrize("key", COHORT_C5)
def test_every_example_chunk_carries_the_honest_level_of_its_pilot(key: str) -> None:
    """No published chunk is only type-checked unless its pilot's verifier cannot run examples: every chunk names its level."""
    for chunk_id, text in _example_chunks(_active_generation(key)).items():
        level = re.search(r"^Verification-Level: (\S+)$", text, re.MULTILINE)
        assert level is not None and level.group(1) in LEVELS_BY_PILOT[key], (
            key,
            chunk_id,
            level and level.group(1),
        )


# TC-852: the example counts the regenerated cohort publishes. README examples whose run failed or printed nothing are no longer
# published (TC-802): cells/go "Quick Start (2)" and pdf/go "Fill AcroForm Fields" (it opens template.pdf, which the verifier sandbox
# does not have) and pdf/cpp "Open a Password-Protected Document" (it opens locked.pdf). Two authored examples win back the answers
# the last two gave (see below), so pdf/go (11) and pdf/cpp (8) keep their old counts; cells/go goes from 13 to 12.
EXPECTED_EXAMPLE_COUNTS = {
    "pdf_go": 11,
    "cells_go": 12,
    "cells_rust": 6,
    "pdf_cpp": 8,
    "cells_cpp": 5,
    "slides_cpp": 7,
    "email_cpp": 7,
}


@pytest.mark.parametrize(("key", "count"), sorted(EXPECTED_EXAMPLE_COUNTS.items()))
def test_the_published_example_count_is_the_regenerated_one(key: str, count: int) -> None:
    assert len(_example_chunks(_active_generation(key))) == count, key


# TC-852: the authored examples that win back the answers two dropped README examples gave. The forgiveness row
# pdf/go "Fill AcroForm" / "fill pdf form fields" and the generic one-word query "open" (which needs an example whose title is
# anchored on "open" somewhere in the 32 pilots) depended on them. Each runs for real at the pilot's own honest level.
TC852_AUTHORED_EXAMPLES = (
    (
        "pdf_go",
        "Fill AcroForm fields by name in an existing form",
        "ran-produced-output",
        ("Fill AcroForm", "Form().Field(", "SetValue", "SetChecked", "SetSelected"),
    ),
    (
        "pdf_cpp",
        "Open a Password-Protected Document With the Owner Password",
        "ran-exit0",
        ("Encrypt", "owner-secret", "IsEncrypted", "Pages().Count()"),
    ),
)


@pytest.mark.parametrize(("key", "title", "level", "words"), TC852_AUTHORED_EXAMPLES)
def test_the_tc852_authored_examples_are_published_authored_and_ran(
    key: str, title: str, level: str, words: tuple[str, ...]
) -> None:
    chunks = [
        text
        for text in _example_chunks(_active_generation(key)).values()
        if text.startswith(f"FQN: Example: {title}\n")
    ]
    assert len(chunks) == 1, (key, title)
    assert re.search(r"^Provenance: authored$", chunks[0], re.MULTILINE)
    assert re.search(rf"^Verification-Level: {level}$", chunks[0], re.MULTILINE)
    lowered = chunks[0].lower()
    for word in words:
        assert word.lower() in lowered, (key, word)
