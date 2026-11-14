"""G3/TC-836 (CELLS-CPP-010 follow-up, REQ-G3-013): every C++ pilot's ``install`` answer states the real CMake floor.

The round-3 honesty fix for CELLS-CPP-010 (G3/TC-659) made ``get_product_reference(install)`` for cells/cpp state
the REAL CMake floor ("CMake 3.16", the pinned CMakeLists.txt's ``cmake_minimum_required``) instead of a
fabricated "3.15+". It stayed true only by accident: the floor lived in an index FAQ chunk ("requires
`CMake 3.16+`"). When G3/TC-786 backfilled ``readme_document`` and ``manifest_path`` into the sidecars, the
README's own Installation section (which never states a floor) replaced that FAQ chunk and the floor vanished
from the served answer of cells/cpp, pdf/cpp and email/cpp - only slides/cpp still stated it, because ITS README
says "CMake 3.20+". The generated source-build recipe now carries the floor from the pinned CMakeLists.txt
itself, for every C++ pilot that declares one.

Real pinned values, each read from the pilot's own CMakeLists.txt at the commit the sidecar pins (GitHub contents
API at that SHA, 2026-11-14), written down here as literals so a drifting sidecar cannot move the expectation:

====== ======================================================== =========================
cells  Aspose.Cells.Foss.Cpp/CMakeLists.txt @ 9f852d0ff1cf...   cmake_minimum_required 3.16
pdf    CMakeLists.txt @ 4b83c9fec1e3...                          cmake_minimum_required 3.20
slides CMakeLists.txt @ 469ce77a07b9...                          cmake_minimum_required 3.20
email  CMakeLists.txt @ c844a467cf7f...                          cmake_minimum_required 3.26
====== ======================================================== =========================

Offline and deterministic: no network, no wall clock.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.server import _wrap_get_product_reference
from foss_mcp.mcp.tools.get_product_reference import ReferenceContent, parse_cmake_facts

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar, sidecar_name  # noqa: E402

# family -> the cmake_minimum_required VERSION the pilot's real pinned CMakeLists.txt declares (verified literal).
REAL_FLOORS = {"cells": "3.16", "pdf": "3.20", "slides": "3.20", "email": "3.26"}

# The sentence the generated recipe adds; no other part of any answer says this.
_GENERATED_MARKER = "the CMakeLists.txt at the indexed commit declares cmake_minimum_required"


def _install(family: str, platform: str, *, manifest_text: str | None = None) -> ReferenceContent:
    inputs = load_manifest_sidecar(MANIFESTS / sidecar_name(family, platform))
    assert inputs is not None, (family, platform)
    if manifest_text is not None:
        inputs = dataclasses.replace(inputs, manifest_text=manifest_text)
    tool = _wrap_get_product_reference(
        inputs, GenerationManifestStore(MANIFESTS), Scope(family=family, platform=platform)
    )
    result = tool(section="install")
    assert isinstance(result, ReferenceContent), (family, getattr(result, "reason", None))
    return result


@pytest.mark.parametrize(("family", "floor"), sorted(REAL_FLOORS.items()))
def test_the_sidecar_manifest_really_declares_the_pinned_floor(family: str, floor: str) -> None:
    """The literal this file pins agrees with the committed sidecar's own CMakeLists text."""
    inputs = load_manifest_sidecar(MANIFESTS / sidecar_name(family, "cpp"))
    assert inputs is not None and inputs.manifest_text is not None
    declared = re.findall(r"^\s*cmake_minimum_required\s*\(\s*VERSION\s+([\d.]+)", inputs.manifest_text, re.M)

    assert declared[0] == floor, (family, declared)
    assert parse_cmake_facts(inputs.manifest_text).minimum_version == floor


@pytest.mark.parametrize(("family", "floor"), sorted(REAL_FLOORS.items()))
def test_each_cpp_install_answer_states_the_real_cmake_floor(family: str, floor: str) -> None:
    result = _install(family, "cpp")

    assert f"Requires CMake {floor} or newer" in result.text, family
    assert f"cmake_minimum_required(VERSION {floor})" in result.text, family


@pytest.mark.parametrize(("family", "floor"), sorted(REAL_FLOORS.items()))
def test_the_floor_sentence_leads_the_answer_before_the_readme_text(family: str, floor: str) -> None:
    """It belongs to the generated recipe, so it precedes the README section the answer appends."""
    text = _install(family, "cpp").text

    assert text.index(f"Requires CMake {floor} or newer") < text.index(
        "The repository README's installation section"
    )


def test_no_other_floor_than_the_declared_one_is_generated() -> None:
    """The wrong cells/cpp floor stays gone: a 3.15 appears nowhere, the generated sentence names exactly one
    version, and it is the real one."""
    text = _install("cells", "cpp").text

    assert "3.15" not in text
    assert re.findall(r"Requires CMake (\d+(?:\.\d+)*) or newer", text) == ["3.16"]


def test_a_pilot_whose_cmakelists_declares_no_floor_gets_none_invented() -> None:
    inputs = load_manifest_sidecar(MANIFESTS / sidecar_name("email", "cpp"))
    assert inputs is not None and inputs.manifest_text is not None
    stripped = re.sub(r"(?im)^\s*cmake_minimum_required\s*\([^)]*\)\s*$", "", inputs.manifest_text)
    assert "cmake_minimum_required" not in stripped.lower()

    result = _install("email", "cpp", manifest_text=stripped)

    assert _GENERATED_MARKER not in result.text
    assert "Requires CMake" not in result.text.split("The repository README's installation section")[0]


def test_a_commented_out_or_range_floor_is_read_correctly() -> None:
    assert parse_cmake_facts("# cmake_minimum_required(VERSION 2.8)\nproject(x)\n").minimum_version == ""
    assert (
        parse_cmake_facts("cmake_minimum_required(VERSION 3.16...3.27)\nproject(x)\n").minimum_version
        == "3.16"
    )
    assert parse_cmake_facts("CMAKE_MINIMUM_REQUIRED( VERSION 3.5 FATAL_ERROR )\n").minimum_version == "3.5"
    assert parse_cmake_facts("project(x)\n").minimum_version == ""


def _non_cpp_pilots() -> list[tuple[str, str]]:
    pilots = []
    for sidecar in sorted(MANIFESTS.glob("product_reference_*.json")):
        family, platform = sidecar.stem[len("product_reference_") :].rsplit("_", 1)
        if platform != "cpp":
            pilots.append((family, platform))
    return pilots


def test_the_corpus_is_32_pilots_four_of_them_cpp() -> None:
    """The scoping test below is only meaningful against the whole corpus: 32 sidecars, 4 of them C++."""
    assert len(_non_cpp_pilots()) == 28
    assert len(list(MANIFESTS.glob("product_reference_*_cpp.json"))) == len(REAL_FLOORS) == 4


@pytest.mark.parametrize(("family", "platform"), _non_cpp_pilots())
def test_no_non_cpp_install_answer_carries_the_generated_floor_sentence(family: str, platform: str) -> None:
    """The change is scoped to the C++ build recipe: no other pilot's install answer is touched."""
    inputs = load_manifest_sidecar(MANIFESTS / sidecar_name(family, platform))
    assert inputs is not None, (family, platform)
    tool = _wrap_get_product_reference(
        inputs, GenerationManifestStore(MANIFESTS), Scope(family=family, platform=platform)
    )
    result = tool(section="install")
    text = result.text if isinstance(result, ReferenceContent) else ""

    assert _GENERATED_MARKER not in text, (family, platform)


@pytest.mark.parametrize("family", sorted(REAL_FLOORS))
def test_the_floor_is_stated_exactly_once(family: str) -> None:
    """Additive: one generated sentence, not repeated by the README merge or the cap."""
    assert _install(family, "cpp").text.count(_GENERATED_MARKER) == 1


@pytest.mark.parametrize("family", sorted(REAL_FLOORS))
def test_the_existing_recipe_and_readme_text_are_kept(family: str) -> None:
    """The sentence adds to the answer; the clone URL, the indexed commit and the README section stay."""
    inputs = load_manifest_sidecar(MANIFESTS / sidecar_name(family, "cpp"))
    assert inputs is not None and inputs.source_ref is not None
    text = _install(family, "cpp").text

    assert text.startswith("Build from source with CMake. Repository: ")
    assert inputs.source_ref in text
    assert "The repository README's installation section" in text


@pytest.mark.parametrize("family", sorted(REAL_FLOORS))
def test_the_floor_does_not_leak_into_the_notice(family: str) -> None:
    """The notice (provenance and disclosure) is unchanged: the floor is answer text only."""
    assert _GENERATED_MARKER not in (_install(family, "cpp").notice or "")
