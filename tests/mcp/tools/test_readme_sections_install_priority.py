"""G3/TC-659 (CELLS-CPP-010 structural fix): a real heading-based ``## Installation`` section
beats a stale FAQ-promoted one.

``readme_sections.py``'s G3/TC-574 rule lets an FAQ-titled index chunk ("How do I install the
library?") count as the whole ``install`` section for a C++ pilot - meant for a pilot whose
README documents installation ONLY as an FAQ entry. ``merge_sections``' general rule ("the index
wins wherever it has a section") then let that FAQ-promoted chunk permanently shadow a live,
heading-based ``## Installation`` section the sidecar README actually has - even when the FAQ
text goes stale (CELLS-CPP-010: authored before a README repin, never refreshed). This file pins
the structural fix: ``ReadmeSection.all_faq`` and ``merge_sections``' new priority rule for
``install`` specifically.

Pure and offline: builds every ``ReadmeSections``/``ReadmeSection`` directly, no store, no
network, no wall clock.
"""

from __future__ import annotations

from foss_mcp.mcp.tools.readme_sections import (
    ReadmeSection,
    ReadmeSections,
    merge_sections,
    sections_from_documents,
    sections_from_markdown,
)

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _faq_install_documents(answer: str) -> dict[str, dict[str, str]]:
    return {
        "scope::doc::0": {
            "text": (
                "FQN: Doc: How do I install the library?\n"
                f"FAQ: How do I install the library?\n\n{answer}\n"
                f"Source-Commit: {COMMIT}"
            )
        }
    }


def _heading_install_documents(body: str) -> dict[str, dict[str, str]]:
    return {
        "scope::doc::0": {
            "text": f"FQN: Doc: Installation, Dependencies, and Quick Start\n{body}\nSource-Commit: {COMMIT}"
        }
    }


# --- (1) the original TC-574 case: FAQ-only, no heading fallback at all -----------------------


def test_an_faq_only_index_install_section_still_wins_with_no_sidecar_readme() -> None:
    index = sections_from_documents(_faq_install_documents("Clone and build with CMake."))
    assert index.install is not None and index.install.all_faq is True

    merged = merge_sections(index, None)

    assert merged is index


def test_an_faq_only_index_install_section_still_wins_when_the_readme_has_no_heading_either() -> None:
    index = sections_from_documents(_faq_install_documents("Clone and build with CMake."))
    readme_without_install = sections_from_markdown("## Some Other Section\n\ntext\n")
    assert readme_without_install.install is None

    merged = merge_sections(index, readme_without_install)

    assert merged is not None
    assert merged.install is index.install


# --- (2) the regression this card closes: a real heading beats a stale FAQ --------------------


def test_a_real_heading_based_readme_install_section_now_beats_a_stale_faq_chunk() -> None:
    index = sections_from_documents(
        _faq_install_documents("Clone the repository and use CMake add_subdirectory. CMake 3.15+.")
    )
    assert index.install is not None and index.install.all_faq is True
    live_readme = sections_from_markdown(
        "## Installation\n\nInstall the prebuilt NuGet package. CMake 3.16 for a source build.\n"
    )
    assert live_readme.install is not None

    merged = merge_sections(index, live_readme)

    assert merged is not None
    assert merged.install is live_readme.install
    assert "NuGet" in merged.install.text
    assert "3.15" not in merged.install.text


def test_the_cells_cpp_shape_directly_reproducing_cells_cpp_010() -> None:
    """The exact shape CELLS-CPP-010 was: the furnished page's FAQ answer is stale and the real
    README documents a completely different install route. Pinned with the real wording this
    card found, not a paraphrase."""
    index = sections_from_documents(
        _faq_install_documents(
            "Clone the repository and use CMake add_subdirectory or FetchContent. Requires a "
            "C++17 compiler and CMake 3.15+. No external dependencies."
        )
    )
    live_readme = sections_from_markdown(
        "## Installation\n\n"
        "Published on NuGet, but the package ships prebuilt static libraries for Windows only.\n\n"
        "```bash\nnuget install Aspose.Cells.Cpp.FOSS\n```\n\n"
        "On Linux, macOS, or a non-MSVC toolchain, build from source instead.\n"
    )

    merged = merge_sections(index, live_readme)

    assert merged is not None and merged.install is not None
    assert merged.install is live_readme.install
    assert "nuget install Aspose.Cells.Cpp.FOSS" in merged.install.text
    assert "CMake 3.15" not in merged.install.text


# --- (3) a non-FAQ (heading-based) index install section is NOT demoted -------------------------


def test_a_genuine_heading_based_index_install_section_still_beats_the_sidecar_readme() -> None:
    """TC-561's general rule ("the index wins") is untouched for the common case: when the
    index's own install chunk came from a real ``### Installation`` heading on the furnished
    page (not an FAQ promotion), it is NOT ``all_faq`` and still wins, even though the sidecar
    README also has its own heading section."""
    index = sections_from_documents(_heading_install_documents("pip install demo"))
    assert index.install is not None and index.install.all_faq is False
    live_readme = sections_from_markdown("## Installation\n\nsome other, fuller recipe\n")
    assert live_readme.install is not None

    merged = merge_sections(index, live_readme)

    assert merged is not None
    assert merged.install is index.install
    assert merged.install.text == "pip install demo"


# --- (4) a mixed section (one real heading chunk plus an FAQ chunk) is not all_faq ---------------


def test_a_section_assembled_from_both_a_heading_chunk_and_an_faq_chunk_is_not_all_faq() -> None:
    documents = {
        **_heading_install_documents("pip install demo"),
        "scope::doc::1": {
            "text": (
                "FQN: Doc: How do I install it differently?\n"
                f"FAQ: How do I install it differently?\n\nAlso works with conda.\n"
                f"Source-Commit: {COMMIT}"
            )
        },
    }

    section = sections_from_documents(documents).install

    assert section is not None
    assert section.all_faq is False  # at least one real, non-FAQ chunk contributed


# --- (5) nothing else regresses: limitations/capabilities/formats merge exactly as before --------


def test_limitations_and_capabilities_merge_priority_is_unaffected_by_this_card() -> None:
    index = ReadmeSections(
        limitations=ReadmeSection("L", "from the index", COMMIT, True, 1),
    )
    readme = sections_from_markdown(
        "## Limitations\n\nfrom the readme\n\n## Capabilities\n\nfrom the readme too\n"
    )

    merged = merge_sections(index, readme)

    assert merged is not None
    assert merged.limitations is index.limitations  # the index still wins here, unchanged
    assert merged.capabilities is readme.capabilities  # unchanged fallback, the index lacked it


def test_merging_with_nothing_on_either_side_is_unaffected() -> None:
    assert merge_sections(None, None) is None
    sections = sections_from_markdown("## Installation\n\ntext\n")
    assert merge_sections(sections, None) is sections
    assert merge_sections(None, sections) is sections
