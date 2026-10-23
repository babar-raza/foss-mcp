"""G3/TC-518: the README sections a deployment's index already holds, recovered from its documentation chunks.

Synthetic ``FQN: Doc: <title>`` chunks shaped exactly like the ones ``infra/build_chunks.py`` publishes
(a ``Part: <i> of <n>`` marker as the last body line of a multi-part chunk, then the ``Source-Commit:``
line the builder appends). Offline and deterministic: no network, no wall clock, no real store.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import readme_sections
from foss_mcp.mcp.tools.readme_sections import (
    CAPABILITIES_TITLE_PATTERN,
    INSTALL_TITLE_PATTERN,
    LIMITATIONS_TITLE_PATTERN,
    ReadmeSection,
    ReadmeSections,
    merge_sections,
    read_readme_sections,
    sections_from_documents,
    sections_from_markdown,
)

COMMIT = "0123456789abcdef0123456789abcdef01234567"
LEAD_IN = (
    "Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for "
    "Aspose.Demo FOSS for Go, straight from its README at the pinned commit:"
)


def _chunk(
    title: str, body: str, *, part: tuple[int, int] | None = None, commit: str | None = COMMIT
) -> dict[str, str]:
    text = f"FQN: Doc: {title}\n{body}"
    if part is not None:
        text += f"\nPart: {part[0]} of {part[1]}"
    if commit is not None:
        text += f"\nSource-Commit: {commit}"
    return {"text": text}


def _documents(*chunks: dict[str, str]) -> dict[str, dict[str, str]]:
    return {f"scope::doc::{index:04d}": chunk for index, chunk in enumerate(chunks)}


def test_the_title_patterns_are_the_documented_substrings() -> None:
    assert LIMITATIONS_TITLE_PATTERN == "limitation"
    assert INSTALL_TITLE_PATTERN == "install"
    assert CAPABILITIES_TITLE_PATTERN == "capabilit"


@pytest.mark.parametrize(
    ("title", "member"),
    [
        ("Scope and Limitations", "limitations"),
        ("Known LIMITATIONS", "limitations"),
        ("Installation, Dependencies, and Quick Start", "install"),
        ("Installation and Quick Start", "install"),
        ("Key Capabilities and API Reference", "capabilities"),
        ("Key Capabilities", "capabilities"),
    ],
)
def test_sections_are_matched_by_title_pattern(title: str, member: str) -> None:
    sections = sections_from_documents(_documents(_chunk(title, "Some README text.")))

    section = getattr(sections, member)
    assert isinstance(section, ReadmeSection)
    assert section.title == title
    assert section.text == "Some README text."
    assert section.commit == COMMIT
    assert section.parts == 1
    for other in ("limitations", "install", "capabilities"):
        if other != member:
            assert getattr(sections, other) is None


def test_titles_that_match_no_pattern_and_chunks_that_are_not_doc_chunks_are_ignored() -> None:
    documents = _documents(
        _chunk("Where It's Used", "Used by many."),
        _chunk("Typical use cases", "Cases."),
        {"text": "FQN: Example: Scope and Limitations\nnot a doc chunk"},
        {"text": "FQN: Aspose.Demo.Workbook\nclass Workbook"},
        {"text": ""},
        {"no_text": "x"},
    )

    assert sections_from_documents(documents) == ReadmeSections(None, None, None)


def test_faq_chunks_are_never_a_section() -> None:
    documents = _documents(
        _chunk("How do I install the library?", "FAQ: How do I install the library?"),
        _chunk("What are the limitations?", "FAQ: What are the limitations?"),
    )

    assert sections_from_documents(documents) == ReadmeSections(None, None, None)


def test_a_faq_chunk_is_skipped_but_the_real_section_beside_it_is_kept() -> None:
    documents = _documents(
        _chunk("How do I install the library?", "FAQ: How do I install the library?"),
        _chunk("Installation", "pip install demo"),
    )

    install = sections_from_documents(documents).install
    assert install is not None
    assert install.text == "pip install demo"
    assert install.parts == 1


def test_an_faq_chunk_with_a_real_answer_is_install_content() -> None:
    """G3/TC-574: a C++ pilot's README may document installation only as an FAQ entry - cells/cpp's real
    README does exactly this. Unlike a bare FAQ question with no answer (above), one with a real answer
    behind it is real install guidance, not a question to skip."""
    documents = _documents(
        _chunk(
            "How do I install the library?",
            "FAQ: How do I install the library?\n\nClone the repository and use CMake add_subdirectory.",
        )
    )

    install = sections_from_documents(documents).install
    assert install is not None
    assert install.text == "Clone the repository and use CMake add_subdirectory."
    assert "FAQ:" not in install.text
    assert install.parts == 1


def test_an_faq_chunk_with_a_real_answer_is_still_excluded_from_limitations_and_capabilities() -> None:
    """The install pattern is the only one TC-574 lets an FAQ answer count for; matched against LIMITATIONS
    or CAPABILITIES instead, the very same shape of chunk (title and all) is still excluded."""
    documents = _documents(
        _chunk(
            "What are the limitations?",
            "FAQ: What are the limitations?\n\nCSV is not supported.",
        ),
        _chunk(
            "What capabilities does it have?",
            "FAQ: What capabilities does it have?\n\nReads and writes XLSX.",
        ),
    )

    sections = sections_from_documents(documents)
    assert sections.limitations is None
    assert sections.capabilities is None


def test_a_bare_faq_question_with_no_answer_is_still_not_install_content() -> None:
    """Promoting an FAQ answer to install content (TC-574) does not promote a bare, answerless question."""
    install = sections_from_documents(
        _documents(_chunk("How do I install the library?", "FAQ: How do I install the library?"))
    ).install

    assert install is None


def test_parts_are_assembled_in_marker_order() -> None:
    # Chunk order inside a generation is by hash: the markers, not the iteration order, give the order.
    documents = _documents(
        _chunk("Scope and Limitations", "third", part=(3, 3)),
        _chunk("Scope and Limitations", "first", part=(1, 3)),
        _chunk("Scope and Limitations", "second", part=(2, 3)),
    )

    limitations = sections_from_documents(documents).limitations
    assert limitations is not None
    assert limitations.text == "first\n\nsecond\n\nthird"
    assert limitations.ordered is True
    assert limitations.parts == 3


def test_chunks_without_markers_are_unordered_and_say_so() -> None:
    documents = _documents(
        _chunk("Scope and Limitations", "alpha"),
        _chunk("Scope and Limitations", "beta"),
        _chunk("Scope and Limitations", "gamma"),
    )

    limitations = sections_from_documents(documents).limitations
    assert limitations is not None
    assert limitations.text == "alpha\n\nbeta\n\ngamma"  # generation (iteration) order
    assert limitations.ordered is False
    assert limitations.parts == 3


def test_one_chunk_without_a_marker_is_trivially_in_order() -> None:
    limitations = sections_from_documents(_documents(_chunk("Scope and Limitations", "only"))).limitations
    assert limitations is not None
    assert limitations.ordered is True


def test_one_marker_missing_makes_the_whole_title_unordered() -> None:
    documents = _documents(
        _chunk("Scope and Limitations", "b", part=(2, 2)),
        _chunk("Scope and Limitations", "a"),
    )

    limitations = sections_from_documents(documents).limitations
    assert limitations is not None
    assert limitations.text == "b\n\na"
    assert limitations.ordered is False


def test_the_generated_lead_in_is_dropped() -> None:
    documents = _documents(
        _chunk("Scope and Limitations", LEAD_IN),
        _chunk("Scope and Limitations", "- CSV is not supported."),
    )

    limitations = sections_from_documents(documents).limitations
    assert limitations is not None
    assert limitations.text == "- CSV is not supported."
    assert "Known issues" not in limitations.text
    assert limitations.parts == 2  # the lead-in chunk is still one of the chunks it was assembled from


def test_the_lead_in_is_dropped_when_it_shares_a_chunk_with_its_list() -> None:
    body = f"{LEAD_IN}\n- CSV is not supported.\n- Formulas are not evaluated."
    limitations = sections_from_documents(_documents(_chunk("Scope and Limitations", body))).limitations

    assert limitations is not None
    assert limitations.text == "- CSV is not supported.\n- Formulas are not evaluated."


def test_a_limitations_section_that_is_only_the_lead_in_is_no_section() -> None:
    documents = _documents(_chunk("Scope and Limitations", LEAD_IN))

    assert sections_from_documents(documents).limitations is None


def test_the_lead_in_is_only_dropped_from_the_limitations_section() -> None:
    install = sections_from_documents(_documents(_chunk("Installation", LEAD_IN))).install
    assert install is not None
    assert install.text == LEAD_IN


def test_fqn_part_and_source_commit_lines_are_stripped_and_the_commit_is_kept() -> None:
    documents = _documents(
        _chunk("Scope and Limitations", "line one\nline two", part=(1, 2)),
        _chunk("Scope and Limitations", "line three", part=(2, 2)),
    )

    limitations = sections_from_documents(documents).limitations
    assert limitations is not None
    for forbidden in ("FQN:", "Part:", "Source-Commit:", COMMIT):
        assert forbidden not in limitations.text
    assert limitations.text == "line one\nline two\n\nline three"
    assert limitations.commit == COMMIT


def test_a_chunk_without_a_source_commit_line_leaves_the_commit_unknown() -> None:
    limitations = sections_from_documents(
        _documents(_chunk("Scope and Limitations", "text", commit=None))
    ).limitations

    assert limitations is not None
    assert limitations.commit is None


def test_a_part_looking_line_inside_the_body_is_readme_text_not_a_marker() -> None:
    body = "Part: 1 of 9 is how the tutorial numbers its steps\nthen more text"
    limitations = sections_from_documents(_documents(_chunk("Scope and Limitations", body))).limitations

    assert limitations is not None
    assert "Part: 1 of 9" in limitations.text


# --- read_readme_sections -------------------------------------------------------------------------


class _Store:
    def __init__(self, active: Any, generation: Any = None, *, boom: str | None = None) -> None:
        self._active = active
        self._generation = generation
        self._boom = boom
        self.read_keys: list[str] = []

    def read_active(self, scope: str) -> Any:
        if self._boom == "read_active":
            raise RuntimeError("active pointer unreadable")
        self.read_keys.append(scope)
        return self._active

    def read_generation(self, scope: str, generation_id: str) -> Any:
        if self._boom == "read_generation":
            raise RuntimeError("generation unreadable")
        return self._generation


_SCOPE = Scope(family="cells", platform="go")


def test_read_readme_sections_reads_the_active_self_extracted_generation() -> None:
    documents = _documents(_chunk("Scope and Limitations", "- CSV is not supported."))
    store = _Store("gen-1", SimpleNamespace(payload={"lexical_index": {"documents": documents}}))

    sections = read_readme_sections(store, _SCOPE)

    assert store.read_keys == ["cells::go::self_extracted"]
    assert sections.limitations is not None
    assert sections.limitations.text == "- CSV is not supported."


@pytest.mark.parametrize(
    "store",
    [
        pytest.param(_Store(None, boom="read_active"), id="read_active-raises"),
        pytest.param(_Store(None), id="no-active-generation"),
        pytest.param(_Store(""), id="empty-active-id"),
        pytest.param(_Store("gen-1", boom="read_generation"), id="read_generation-raises"),
        pytest.param(_Store("gen-1", SimpleNamespace(payload={})), id="payload-without-lexical-index"),
        pytest.param(
            _Store("gen-1", SimpleNamespace(payload={"lexical_index": {}})),
            id="lexical-index-without-documents",
        ),
        pytest.param(
            _Store("gen-1", SimpleNamespace(payload={"lexical_index": {"documents": None}})),
            id="documents-none",
        ),
        pytest.param(_Store("gen-1", SimpleNamespace(payload=None)), id="payload-none"),
        pytest.param(_Store("gen-1", SimpleNamespace()), id="generation-without-payload"),
        pytest.param(
            _Store("gen-1", SimpleNamespace(payload={"lexical_index": {"documents": 7}})),
            id="documents-not-a-mapping",
        ),
    ],
)
def test_read_readme_sections_never_raises(store: Any) -> None:
    assert read_readme_sections(store, _SCOPE) == ReadmeSections(None, None, None)


def test_read_readme_sections_never_raises_for_a_scope_it_cannot_read() -> None:
    assert read_readme_sections(_Store("gen-1"), object()) == ReadmeSections(None, None, None)  # type: ignore[arg-type]


def test_read_readme_sections_is_a_real_function_of_the_module() -> None:
    assert readme_sections.read_readme_sections is read_readme_sections


# --- G3/TC-561: the same sections from the repository README's markdown -------------------------------------------------

README = """# Demo FOSS for C++

Intro paragraph.

## Key Capabilities

- Reads .xlsx files.

## Installation

Build from source.

```bash
# not a heading: a comment inside a fence
cmake -S . -B build
```

### Build

Run cmake.

### Install the package

Use nuget.

## Dependencies

None.

## What it cannot do

No charts.

### Save formats

Only xlsx is written.

## Supported formats

| Format | Load |
|---|---|
| XLSX | yes |

## How do I install the library?

FAQ answer that must never be the installation section.
"""


def test_markdown_install_runs_to_the_next_heading_of_the_same_level_and_keeps_its_subsections() -> None:
    install = sections_from_markdown(README, commit=COMMIT).install

    assert install is not None
    assert install.title == "Installation"
    assert install.text.startswith("Build from source.")
    assert "### Build" in install.text and "### Install the package" in install.text
    assert "# not a heading: a comment inside a fence" in install.text
    assert "None." not in install.text  # the Dependencies section is the next one
    assert install.commit == COMMIT and install.ordered is True and install.parts == 1


def test_a_faq_question_heading_is_never_the_installation_section() -> None:
    install = sections_from_markdown(README).install

    assert install is not None and "FAQ answer" not in install.text


def test_markdown_limitations_and_formats_are_picked_by_their_headings() -> None:
    sections = sections_from_markdown(README)

    assert sections.limitations is not None and sections.limitations.title == "What it cannot do"
    assert "No charts." in sections.limitations.text
    assert sections.formats is not None and sections.formats.title == "Save formats; Supported formats"
    assert "Only xlsx is written." in sections.formats.text and "| XLSX | yes |" in sections.formats.text
    assert sections.capabilities is not None and sections.capabilities.text == "- Reads .xlsx files."


@pytest.mark.parametrize(
    ("heading", "member"),
    [
        ("Scope and Limitations", "limitations"),
        ("What's not included (and where to get it)", "limitations"),
        ("Known issues", "limitations"),
        ("Getting started", "install"),
        ("Install", "install"),
        ("What it can do", "capabilities"),
        ("Supported formats", "formats"),
        ("Load formats", "formats"),
        ("Format Readers", None),
        ("Quick Start", None),
    ],
)
def test_the_markdown_headings_that_select_each_section(heading: str, member: str | None) -> None:
    sections = sections_from_markdown(f"## {heading}\n\nSome text.\n")

    picked = [
        name for name in ("limitations", "install", "capabilities", "formats") if getattr(sections, name)
    ]
    assert picked == ([member] if member else [])


def test_a_heading_with_no_body_is_not_a_section() -> None:
    assert sections_from_markdown("## Installation\n\n## Next\n\ntext\n").install is None


def test_a_level_one_heading_is_the_document_title_not_a_section() -> None:
    assert sections_from_markdown("# Installation\n\ntext\n").install is None


def test_two_installation_headings_are_both_collected_in_order() -> None:
    sections = sections_from_markdown(
        "## Install\n\nfirst\n\n## Other\n\nx\n\n## Installation notes\n\nsecond\n"
    )

    assert sections.install is not None
    assert sections.install.text == "first\n\nsecond" and sections.install.parts == 2


def test_markdown_without_headings_yields_the_empty_result() -> None:
    assert sections_from_markdown("just text\nand more\n") == ReadmeSections()
    assert sections_from_markdown("") == ReadmeSections()


def test_the_index_wins_and_the_readme_fills_what_the_index_lacks() -> None:
    index = ReadmeSections(
        limitations=ReadmeSection("L", "from the index", COMMIT, True, 1), import_warning="cannot be imported"
    )
    readme = sections_from_markdown(README)

    merged = merge_sections(index, readme)

    assert merged is not None
    assert merged.limitations is index.limitations
    assert merged.install is readme.install and merged.formats is readme.formats
    assert merged.import_warning == "cannot be imported"


def test_merging_with_nothing_returns_the_other_side() -> None:
    sections = sections_from_markdown(README)

    assert merge_sections(None, None) is None
    assert merge_sections(sections, None) is sections
    assert merge_sections(None, sections) is sections


def test_the_import_warning_is_read_from_an_extraction_notice_chunk_only() -> None:
    statement = (
        "35 of 45 source files do not parse as written; the API was recovered by a textual repair of the lost "
        "indentation; the library cannot be imported at this commit."
    )
    notice = _chunk(
        "Extraction notice", f"{statement}\n11 of 45 source files could not be read.", commit=None
    )
    elsewhere = _chunk("Scope and Limitations", statement)

    assert sections_from_documents(_documents(notice)).import_warning == statement
    assert sections_from_documents(_documents(elsewhere)).import_warning is None
    assert (
        sections_from_documents(_documents(_chunk("Extraction notice", "Some other notice."))).import_warning
        is None
    )


def test_the_slides_net_readme_gives_both_installation_routes() -> None:
    root = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "round3_readmes" / "slides_net"
    sections = sections_from_markdown((root / "README.md").read_text(encoding="utf-8"))

    assert sections.install is not None
    assert "### Build from source" in sections.install.text
    assert "### Install the package CI built" in sections.install.text
    assert sections.limitations is not None and sections.limitations.title == "What it cannot do"
    assert sections.formats is not None and "Save formats" in sections.formats.title
