"""G3/TC-659 (CELLS-CPP-010, REQ-G3-013): cells/cpp's ``install`` answer is no longer a fabrication.

CELLS-CPP-010 (round-3 audit, P1, honesty): ``get_product_reference(section="install")`` for
cells/cpp served a sentence labelled "the repository README's installation section, verbatim" -
"Clone the repository and use CMake `add_subdirectory` or `FetchContent`. Requires a C++17
compiler and CMake 3.15+. No external dependencies." - that does not appear anywhere in the real,
committed README (``tests/fixtures/round3_readmes/cells_cpp/README.md``), whose real Installation
section is NuGet-first (Windows/MSVC-only prebuilt static libraries), with a CMake source build
only as the Linux/macOS/non-MSVC fallback, and whose real ``CMakeLists.txt`` requires CMake 3.16,
not 3.15.

Root cause (confirmed by direct git archaeology, not a guess): the furnished page's own FAQ
answer for "How do I install the library?" (``tests/fixtures/furnished/cells_cpp/pages/
_index.md``) was authored 2026-09-28 (G2/TC-095), BEFORE the real round-3 README was ever pinned
(2026-10-19, G3/TC-561) - a stale documentation generation, not an extraction bug. It was carried
forward, unchanged, into every baked generation rebuilt since, including the one TC-574
(2026-10-23) stamped with the pinned commit's own SHA, because nothing re-checked the FAQ text
against the README it was supposedly quoting.

This file proves, against the REAL committed production data (``infra/demo-manifests``, read
through the exact call path ``foss_mcp.mcp.server`` wraps for the real tool - the same path
``tests/mcp/tools/test_cpp_install_faq_fallback.py`` already uses for the sibling C++ pilots):
the fabricated sentence is gone, the real NuGet-first guidance and the real CMake 3.16 bound
are now served, and the baked FAQ chunk itself (not just the answer text wrapping it) was really
re-ingested, not hand-patched. Offline and deterministic: no network, no wall clock.
"""

from __future__ import annotations

import sys
from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.server import _wrap_get_product_reference
from foss_mcp.mcp.tools.get_product_reference import ReferenceContent
from foss_mcp.mcp.tools.readme_sections import read_readme_sections

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SOURCE_KIND = "self_extracted"

sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar, sidecar_name  # noqa: E402

# The exact fabricated sentence CELLS-CPP-010 reported - it must never be served again.
_FABRICATED_SENTENCE = (
    "Clone the repository and use CMake add_subdirectory or FetchContent. "
    "Requires a C++17 compiler and CMake 3.15+. No external dependencies."
)


def _real_cells_cpp_install() -> ReferenceContent:
    sidecar = MANIFESTS / sidecar_name("cells", "cpp")
    inputs = load_manifest_sidecar(sidecar)
    assert inputs is not None
    store = GenerationManifestStore(MANIFESTS)
    scope = Scope(family="cells", platform="cpp")
    tool = _wrap_get_product_reference(inputs, store, scope)
    result = tool(section="install")
    assert isinstance(result, ReferenceContent), getattr(result, "reason", None)
    return result


def test_the_fabricated_sentence_is_gone_from_the_real_served_answer() -> None:
    result = _real_cells_cpp_install()

    assert _FABRICATED_SENTENCE not in result.text
    assert "Clone the repository and use CMake add_subdirectory or FetchContent" not in result.text


def test_the_wrong_cmake_floor_is_gone_and_the_real_one_is_served() -> None:
    result = _real_cells_cpp_install()

    # G3/TC-836: "3.16" is the pinned CMakeLists.txt's cmake_minimum_required, stated by the generated
    # source-build recipe ("Requires CMake 3.16 or newer ..."); it no longer rides in on an FAQ chunk.
    assert "3.15" not in result.text
    assert "3.16" in result.text
    assert "Requires CMake 3.16 or newer" in result.text


def test_the_real_nuget_guidance_is_now_served() -> None:
    result = _real_cells_cpp_install()

    assert "Aspose.Cells.Cpp.FOSS" in result.text
    assert "MSVC" in result.text


def test_the_served_text_is_traceable_to_the_real_committed_readme() -> None:
    """The real README's Installation section (line ~91) names the source-build fallback as
    ``add_subdirectory`` on Linux/macOS/non-MSVC, never ``FetchContent`` - the fabricated
    sentence's own second claimed route. The corrected answer keeps the real route only."""
    readme = (REPO_ROOT / "tests" / "fixtures" / "round3_readmes" / "cells_cpp" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "Windows only" in readme and "MSVC" in readme  # the real file really documents this

    result = _real_cells_cpp_install()

    assert "add_subdirectory" in result.text
    assert "non-MSVC" in result.text


def test_the_baked_faq_chunk_itself_was_really_re_ingested_not_hand_patched() -> None:
    """The regression this card's own closeout demands: not just a corrected answer text, but a
    really re-published generation whose indexed FAQ chunk itself carries the corrected text -
    proof the fix lives in the baked data TC-659 diagnosed, not a patch layered on top of it."""
    store = GenerationManifestStore(MANIFESTS)
    readme = read_readme_sections(store, Scope(family="cells", platform="cpp"))

    assert readme.install is not None
    assert "Clone the repository and use CMake add_subdirectory or FetchContent" not in readme.install.text
    assert "Aspose.Cells.Cpp.FOSS" in readme.install.text
    assert "3.16" in readme.install.text


def test_the_generation_was_really_republished_not_just_the_fixture_edited() -> None:
    """CELLS-CPP-001's own earlier fix (G3/TC-648) corrected this exact furnished page's CSV FAQ
    answer but never re-published the baked generation - the raw indexed chunk stayed wrong,
    byte-identical to before TC-648's own fixture edit. This pins that TC-659's re-ingest did not
    repeat that gap: the active generation's raw documents (read directly, the same way
    ``tests/mcp/tools/test_product_reference_baked_contract.py``'s own ``_Baked.load`` does, since
    ``get_product_reference`` itself never surfaces this FAQ's answer under any section name) now
    carry the real, corrected CSV answer, not the stale one."""
    store = GenerationManifestStore(MANIFESTS)
    key = f"cells::cpp::{SOURCE_KIND}"
    generation_id = store.read_active(key)
    assert generation_id
    documents = store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
    formats_chunk = next(
        rec["text"] for rec in documents.values() if rec["text"].startswith("FQN: Doc: Which file formats")
    )

    assert "`.csv` load is supported" not in formats_chunk
    assert "`.csv` is not supported for load or save" in formats_chunk


def test_the_repository_url_and_indexed_commit_still_lead_the_answer() -> None:
    """Unaffected by this card: the generated build-guidance preamble (G3/TC-561) still leads,
    naming the real repository and the real pinned commit, before the README section is appended."""
    result = _real_cells_cpp_install()

    assert "https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp" in result.text
    assert "9f852d0ff1cfdad2d661556d6b87a8eff8c063a2" in result.text
    assert result.text.index("9f852d0ff1cfdad2d661556d6b87a8eff8c063a2") < result.text.index(
        "The repository README's installation section"
    )
