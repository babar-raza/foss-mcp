"""Tests for src/foss_mcp/indexing/doc_candidates.py (REQ-G2-047).

This card is pure extraction only: no chunking, classification, or indexing
happens here or in the module under test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from foss_mcp.indexing.doc_candidates import DocCandidate, extract_doc_sections

FIXTURE_ROOT = Path("tests/fixtures/furnished")


def _load_page(fixture_path: Path) -> dict:
    text = fixture_path.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


# ---------------------------------------------------------------------------
# Synthetic-dict tests: exercise each section's rules and edge cases in
# isolation, before touching any real fixture.
# ---------------------------------------------------------------------------


def test_overview_enabled_yields_one_candidate_with_real_title_and_body() -> None:
    page = {
        "overview": {
            "enable": True,
            "title": "Overview Title",
            "content": "Overview body prose, no heading syntax at all.",
        }
    }

    result = extract_doc_sections(page)

    assert result == [
        DocCandidate(
            title="Overview Title",
            body="Overview body prose, no heading syntax at all.",
            origin="overview",
        )
    ]


def test_overview_disabled_yields_nothing() -> None:
    page = {
        "overview": {
            "enable": False,
            "title": "Should Not Appear",
            "content": "Should not appear either.",
        }
    }

    assert extract_doc_sections(page) == []


def test_overview_missing_entirely_is_skipped_not_an_error() -> None:
    page: dict = {}

    assert extract_doc_sections(page) == []


def test_content_block_yields_two_candidates_left_and_right() -> None:
    page = {
        "content": {
            "enable": True,
            "block": [
                {
                    "title_left": "Left Title",
                    "content_left": "Left body.",
                    "title_right": "Right Title",
                    "content_right": "Right body.",
                }
            ],
        }
    }

    result = extract_doc_sections(page)

    assert result == [
        DocCandidate(title="Left Title", body="Left body.", origin="content"),
        DocCandidate(title="Right Title", body="Right body.", origin="content"),
    ]


def test_content_disabled_yields_nothing() -> None:
    page = {
        "content": {
            "enable": False,
            "block": [
                {
                    "title_left": "Left Title",
                    "content_left": "Left body.",
                    "title_right": "Right Title",
                    "content_right": "Right body.",
                }
            ],
        }
    }

    assert extract_doc_sections(page) == []


def test_faq_entry_body_carries_the_load_bearing_faq_prefix() -> None:
    page = {
        "faq": {
            "enable": True,
            "list": [
                {
                    "question": "Is this thing on?",
                    "answer": "Yes, this thing is on.",
                }
            ],
        }
    }

    result = extract_doc_sections(page)

    assert result == [
        DocCandidate(
            title="Is this thing on?",
            body="FAQ: Is this thing on?\n\nYes, this thing is on.",
            origin="faq",
        )
    ]
    # The literal substring the downstream classifier requires must be present.
    assert "faq" in result[0].body.lower()


def test_faq_disabled_yields_nothing_gracefully_not_an_error() -> None:
    page = {
        "faq": {
            "enable": False,
        }
    }

    assert extract_doc_sections(page) == []


def test_no_synthetic_markdown_heading_is_ever_added_to_any_body() -> None:
    page = {
        "overview": {"enable": True, "title": "T1", "content": "C1"},
        "content": {
            "enable": True,
            "block": [
                {
                    "title_left": "T2",
                    "content_left": "C2",
                    "title_right": "T3",
                    "content_right": "C3",
                }
            ],
        },
        "faq": {"enable": True, "list": [{"question": "Q1", "answer": "A1"}]},
    }

    result = extract_doc_sections(page)

    for candidate in result:
        assert not candidate.body.lstrip().startswith("#")


def test_malformed_entries_are_skipped_not_an_error() -> None:
    page = {
        "overview": {"enable": True, "title": None, "content": "C1"},
        "content": {
            "enable": True,
            "block": [
                "not a mapping",
                {"title_left": "Only Left", "content_left": "Left body."},
            ],
        },
        "faq": {
            "enable": True,
            "list": [
                {"question": "Q1"},  # missing answer
                {"question": "Q2", "answer": "A2"},
            ],
        },
    }

    result = extract_doc_sections(page)

    # overview skipped (title is None), one content candidate (title_left only,
    # no title_right/content_right present), one faq candidate (Q2 only).
    assert result == [
        DocCandidate(title="Only Left", body="Left body.", origin="content"),
        DocCandidate(
            title="Q2",
            body="FAQ: Q2\n\nA2",
            origin="faq",
        ),
    ]


# ---------------------------------------------------------------------------
# Real per-pilot fixture tests. Titles/substrings below were read directly
# from each pilot's own committed tests/fixtures/furnished/<pilot>/pages/_index.md.
# ---------------------------------------------------------------------------


def test_real_fixture_pdf_net_extracts_overview_content_and_faq() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_net" / "pages" / "_index.md")

    result = extract_doc_sections(page)

    # 1 overview + 4 content blocks * 2 + 5 faq entries = 14
    assert len(result) == 14

    overview = [c for c in result if c.origin == "overview"]
    assert len(overview) == 1
    assert overview[0].title == "Free MIT-Licensed .NET Library for PDF Creation and Manipulation"
    # The confirmed-uncorroborated numeric claim OQ-001 is about; must survive
    # extraction verbatim (citation gating is a different card's job).
    assert "805 classes" in overview[0].body
    assert not overview[0].body.lstrip().startswith("#")

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 8
    content_titles = [c.title for c in content]
    assert "Document Structure and Page Management" in content_titles
    assert "Common Document Processing Scenarios" in content_titles
    watermark_block = next(c for c in content if c.title == "Document Review and Collaboration Scenarios")
    assert "AddWatermarkAnnotation" in watermark_block.body

    faq = [c for c in result if c.origin == "faq"]
    assert len(faq) == 5
    faq_titles = [c.title for c in faq]
    assert "What is the licensing model for Aspose.PDF FOSS for .NET?" in faq_titles
    licensing = next(c for c in faq if c.title == "What is the licensing model for Aspose.PDF FOSS for .NET?")
    assert licensing.body.startswith("FAQ: What is the licensing model for Aspose.PDF FOSS for .NET?\n\n")
    assert "MIT License" in licensing.body


def test_real_fixture_pdf_java_faq_disabled_yields_zero_faq_candidates() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_java" / "pages" / "_index.md")

    # Confirm the real fixture really does have faq.enable: false before
    # trusting the assertion below.
    assert page["faq"]["enable"] is False

    result = extract_doc_sections(page)

    # 1 overview + 3 content blocks * 2 + 0 faq = 7
    assert len(result) == 7
    assert [c for c in result if c.origin == "faq"] == []

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Free MIT-Licensed Java Library for PDF Creation and Manipulation"
    assert "527 classes" in overview.body

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 6
    content_titles = [c.title for c in content]
    assert "Annotations and Form Fields" in content_titles
    assert "Document Review and Collaboration" in content_titles


def test_real_fixture_pdf_typescript_extracts_overview_content_and_faq() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_typescript" / "pages" / "_index.md")

    assert page["faq"]["enable"] is True

    result = extract_doc_sections(page)

    # 1 overview + 5 content blocks * 2 + 5 faq entries = 16
    assert len(result) == 16

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Aspose.PDF FOSS — Open Source TypeScript PDF Library"

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 10
    content_titles = [c.title for c in content]
    assert "AcroForms" in content_titles
    assert "Security, Signing, and Redaction" in content_titles

    faq = [c for c in result if c.origin == "faq"]
    assert len(faq) == 5
    installs = next(c for c in faq if c.title == "How do I install Aspose.PDF FOSS for TypeScript?")
    assert installs.body.startswith("FAQ: How do I install Aspose.PDF FOSS for TypeScript?\n\n")
    assert "Node.js 22" in installs.body


def test_real_fixture_pdf_go_extracts_overview_content_and_faq() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_go" / "pages" / "_index.md")

    assert page["faq"]["enable"] is True

    result = extract_doc_sections(page)

    # 1 overview + 6 content blocks * 2 + 5 faq entries = 18
    assert len(result) == 18

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Aspose.PDF FOSS — Open Source Go PDF Library"

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 12
    content_titles = [c.title for c in content]
    assert "Bookmarks and Navigation" in content_titles
    assert "Tables and Structured Layout" in content_titles

    faq = [c for c in result if c.origin == "faq"]
    assert len(faq) == 5
    go_version = next(c for c in faq if c.title == "What Go version is required?")
    assert go_version.body.startswith("FAQ: What Go version is required?\n\n")
    assert "Go 1.24" in go_version.body


def test_real_fixture_slides_python_extracts_overview_content_and_eight_faq_entries() -> None:
    page = _load_page(FIXTURE_ROOT / "slides_python" / "pages" / "_index.md")

    assert page["faq"]["enable"] is True

    result = extract_doc_sections(page)

    # 1 overview + 2 content blocks * 2 + 8 faq entries = 13
    assert len(result) == 13

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Open-Source Python Library for PowerPoint Presentations"
    assert "lxml" in overview.body

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 4
    content_titles = [c.title for c in content]
    assert "Presentation and Slide API" in content_titles
    assert "Developer Experience" in content_titles

    faq = [c for c in result if c.origin == "faq"]
    assert len(faq) == 8
    roundtrip = next(c for c in faq if c.title == "Will round-tripping a PPTX destroy unknown content?")
    assert roundtrip.body.startswith("FAQ: Will round-tripping a PPTX destroy unknown content?\n\n")
    assert "never lost" in roundtrip.body


def test_real_fixture_cells_rust_extracts_overview_content_and_faq() -> None:
    page = _load_page(FIXTURE_ROOT / "cells_rust" / "pages" / "_index.md")

    assert page["faq"]["enable"] is True

    result = extract_doc_sections(page)

    # 1 overview + 5 content blocks * 2 + 5 faq entries = 16
    assert len(result) == 16

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Aspose.Cells FOSS — Open Source Rust Library"
    assert "Workbook" in overview.body

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 10
    content_titles = [c.title for c in content]
    assert "Charts, Pictures, and Shapes" in content_titles
    assert "Data Analysis and Protection" in content_titles

    faq = [c for c in result if c.origin == "faq"]
    assert len(faq) == 5
    cargo = next(c for c in faq if c.title == "How do I install the crate?")
    assert cargo.body.startswith("FAQ: How do I install the crate?\n\n")
    assert "Cargo.toml" in cargo.body
