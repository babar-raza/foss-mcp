"""G3/TC-574 (RD3-080 follow-up, REQ-G3-008/REQ-G3-009): an FAQ-titled README chunk is real install content.

``get_product_reference('install')`` answered ``NotAvailable`` for cells/cpp in the real shipped data even
though its own README documents installation - as a 'Doc: How do I install the library?' chunk whose body
is a 'FAQ: ...' answer. ``readme_sections._parse_chunk`` excluded ANY FAQ-titled chunk from EVERY section,
including install, because a FAQ answer under LIMITATIONS or CAPABILITIES tends to be too narrow to serve as
the whole section - a reasonable rule for those two, but it also discarded cells/cpp's only install content.

The fix lets the install pattern specifically accept an FAQ-titled chunk whose body (after the "FAQ: ..."
question line) is real guidance, while LIMITATIONS and CAPABILITIES keep excluding FAQ answers exactly as
before. This file proves: (1) a synthetic FAQ install chunk is now real install content, (2) the same shape
of chunk is still excluded from LIMITATIONS/CAPABILITIES, (3) the real baked data of all 4 C++ pilots
(cells/cpp, pdf/cpp, slides/cpp, email/cpp), read through the exact call path ``foss_mcp.mcp.server`` wraps
for the ``get_product_reference`` tool, and (4) an ordinary non-FAQ install chunk for a non-C++ pilot still
parses exactly as before. Offline and deterministic: no network, no wall clock.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.server import _wrap_get_product_reference
from foss_mcp.mcp.tools.get_product_reference import NotAvailable, ReferenceContent
from foss_mcp.mcp.tools.readme_sections import sections_from_documents

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SOURCE_KIND = "self_extracted"

sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar, sidecar_name  # noqa: E402

CPP_PILOTS = ["cells_cpp", "pdf_cpp", "slides_cpp", "email_cpp"]


def _documents(*chunks: dict[str, str]) -> dict[str, dict[str, str]]:
    return {f"scope::doc::{index:04d}": chunk for index, chunk in enumerate(chunks)}


def _chunk(title: str, body: str) -> dict[str, str]:
    return {"text": f"FQN: Doc: {title}\n{body}\nSource-Commit: 0123456789abcdef0123456789abcdef01234567"}


# --- (1) a synthetic FAQ-titled install chunk is real install content -----------------------------------


def test_a_synthetic_faq_install_chunk_is_real_install_guidance() -> None:
    documents = _documents(
        _chunk(
            "How do I install the library?",
            "FAQ: How do I install the library?\n\nClone the repository and use CMake add_subdirectory "
            "or FetchContent. Requires a C++17 compiler.",
        )
    )

    install = sections_from_documents(documents).install

    assert install is not None
    assert install.text == (
        "Clone the repository and use CMake add_subdirectory or FetchContent. Requires a C++17 compiler."
    )
    assert not install.text.startswith("FAQ:")


# --- (2) the same chunk shape is still excluded from LIMITATIONS/CAPABILITIES ----------------------------


@pytest.mark.parametrize(
    ("title", "faq_question"),
    [
        ("What are the limitations?", "What are the limitations?"),
        ("What capabilities does it have?", "What capabilities does it have?"),
    ],
)
def test_the_same_faq_shape_is_still_excluded_from_limitations_and_capabilities(
    title: str, faq_question: str
) -> None:
    documents = _documents(_chunk(title, f"FAQ: {faq_question}\n\nSome narrow FAQ answer."))

    sections = sections_from_documents(documents)

    assert sections.limitations is None
    assert sections.capabilities is None


# --- (3) the real baked data of all 4 C++ pilots, through the real server call path -----------------------


def _real_install_answer(pilot: str) -> ReferenceContent | NotAvailable:
    family, platform = pilot.split("_", 1)
    sidecar = MANIFESTS / sidecar_name(family, platform)
    inputs = load_manifest_sidecar(sidecar)
    assert inputs is not None, pilot
    store = GenerationManifestStore(MANIFESTS)
    scope = Scope(family=family, platform=platform)
    tool = _wrap_get_product_reference(inputs, store, scope)
    return tool(section="install")  # type: ignore[no-any-return]


@pytest.mark.parametrize("pilot", CPP_PILOTS)
def test_cpp_pilots_get_real_install_guidance_from_the_real_baked_data(pilot: str) -> None:
    """The one live proof that matters: no C++ pilot's install answer is a guess, and none that has real
    install-shaped content in its README is left answering NotAvailable."""
    family, platform = pilot.split("_", 1)
    store = GenerationManifestStore(MANIFESTS)
    key = f"{family}::{platform}::{SOURCE_KIND}"
    generation_id = store.read_active(key)
    assert generation_id, pilot
    documents = store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
    has_install_chunk = any(
        "install" in text.split("\n", 1)[0].lower()
        for text in (d.get("text", "") for d in documents.values())
        if text.startswith("FQN: Doc: ")
    )

    result = _real_install_answer(pilot)

    if not has_install_chunk:
        assert isinstance(result, NotAvailable), (pilot, "no install-shaped README content exists for it")
        return
    assert isinstance(result, ReferenceContent), (pilot, getattr(result, "reason", None))
    assert "cmake" in result.text.lower(), pilot
    assert "github.com" in result.text, pilot


def test_cells_cpp_install_is_the_faq_fallback_specifically() -> None:
    """cells/cpp is the pilot RD3-080 names: its only install content is the FAQ answer this card
    unblocks. Before this fix it answered NotAvailable; now it answers with the real recipe."""
    result = _real_install_answer("cells_cpp")

    assert isinstance(result, ReferenceContent), getattr(result, "reason", None)
    assert "Clone the repository and use CMake add_subdirectory or FetchContent" in result.text
    assert "9f852d0ff1cfdad2d661556d6b87a8eff8c063a2" in result.text
    assert "github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp" in result.text


# --- (4) an ordinary non-FAQ install chunk for a non-C++ pilot is unchanged -------------------------------


def test_a_regular_non_faq_install_chunk_for_a_non_cpp_pilot_is_unchanged() -> None:
    documents = _documents(_chunk("Installation, Dependencies, and Quick Start", "pip install demo"))

    install = sections_from_documents(documents).install

    assert install is not None
    assert install.text == "pip install demo"
