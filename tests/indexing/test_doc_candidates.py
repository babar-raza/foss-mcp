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
#
# TC-119 regenerated 5 of the 6 pilots' furnished fixtures from
# repository-presenter's own sealed candidates (pdf_typescript's own fixture
# was deliberately NOT regenerated - see its own test below, unchanged).
# repository-presenter's README template has no FAQ section at all, so every
# one of these 5 pilots now has faq.enable: false and yields zero FAQ-origin
# candidates - not just pdf_java's, as before TC-119.
# ---------------------------------------------------------------------------


def _real_doc_candidates_for_pilot(pilot_dir_name: str) -> list[DocCandidate]:
    """Load a pilot's real, committed furnished fixture and extract its real
    documentation candidates via ``extract_doc_sections``, for real.

    Every real-fixture test below calls this instead of loading and
    extracting its own fixture inline, so the fix is robust to exactly which
    real values each test ends up asserting.
    """
    page = _load_page(FIXTURE_ROOT / pilot_dir_name / "pages" / "_index.md")
    return extract_doc_sections(page)


def test_real_fixture_pdf_net_extracts_overview_content_and_faq() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_net" / "pages" / "_index.md")
    assert page["faq"]["enable"] is False

    result = _real_doc_candidates_for_pilot("pdf_net")

    # 1 overview + 3 content candidates (one left/right pair, one
    # title_left-only "Scope and Limitations" block) + 0 faq = 4
    assert len(result) == 4

    overview = [c for c in result if c.origin == "overview"]
    assert len(overview) == 1
    assert overview[0].title == "Aspose.PDF FOSS for .NET"
    assert not overview[0].body.lstrip().startswith("#")

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 3
    content_titles = [c.title for c in content]
    assert content_titles == [
        "Installation, Dependencies, and Quick Start",
        "Key Capabilities and API Reference",
        "Scope and Limitations",
    ]
    api_reference = next(c for c in content if c.title == "Key Capabilities and API Reference")
    assert "899 types" in api_reference.body
    scope = next(c for c in content if c.title == "Scope and Limitations")
    assert "System.Drawing.Common" in scope.body

    faq = [c for c in result if c.origin == "faq"]
    assert faq == []


def test_real_fixture_pdf_java_faq_disabled_yields_zero_faq_candidates() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_java" / "pages" / "_index.md")

    # Confirm the real fixture really does have faq.enable: false before
    # trusting the assertion below.
    assert page["faq"]["enable"] is False

    result = _real_doc_candidates_for_pilot("pdf_java")

    # 1 overview + 3 content candidates + 0 faq = 4
    assert len(result) == 4
    assert [c for c in result if c.origin == "faq"] == []

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Aspose.PDF FOSS for Java"

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 3
    content_titles = [c.title for c in content]
    assert content_titles == [
        "Installation, Dependencies, and Quick Start",
        "Key Capabilities and API Reference",
        "Scope and Limitations",
    ]
    api_reference = next(c for c in content if c.title == "Key Capabilities and API Reference")
    assert "1158 types" in api_reference.body
    scope = next(c for c in content if c.title == "Scope and Limitations")
    assert "org.aspose:aspose-pdf-foss" in scope.body


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

    # pdf_go used to have faq.enable: true with 5 real FAQ entries; TC-119's
    # regeneration replaced it with repository-presenter's own README
    # content, which has no FAQ section at all.
    assert page["faq"]["enable"] is False

    result = _real_doc_candidates_for_pilot("pdf_go")

    # 1 overview + 3 content candidates + 0 faq = 4
    assert len(result) == 4

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Aspose PDF FOSS for Go"

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 3
    content_titles = [c.title for c in content]
    assert content_titles == [
        "Installation, Dependencies, and Quick Start",
        "Key Capabilities and API Reference",
        "Scope and Limitations",
    ]
    api_reference = next(c for c in content if c.title == "Key Capabilities and API Reference")
    assert "244 types" in api_reference.body
    scope = next(c for c in content if c.title == "Scope and Limitations")
    assert "Aspose PDF FOSS for Go provides a Go API" in scope.body

    faq = [c for c in result if c.origin == "faq"]
    assert faq == []


def test_real_fixture_slides_python_extracts_overview_content_and_zero_faq_entries() -> None:
    page = _load_page(FIXTURE_ROOT / "slides_python" / "pages" / "_index.md")

    # slides_python used to have faq.enable: true with 8 real FAQ entries;
    # TC-119's regeneration replaced it with repository-presenter's own
    # README content, which has no FAQ section at all.
    assert page["faq"]["enable"] is False

    result = _real_doc_candidates_for_pilot("slides_python")

    # 1 overview + 3 content candidates + 0 faq = 4
    assert len(result) == 4

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Aspose.Slides FOSS for Python"

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 3
    content_titles = [c.title for c in content]
    assert content_titles == [
        "Installation, Dependencies, and Quick Start",
        "Key Capabilities and API Reference",
        "Scope and Limitations",
    ]
    api_reference = next(c for c in content if c.title == "Key Capabilities and API Reference")
    assert "516 types" in api_reference.body
    scope = next(c for c in content if c.title == "Scope and Limitations")
    assert "OOXML" in scope.body

    faq = [c for c in result if c.origin == "faq"]
    assert faq == []


def test_real_fixture_cells_rust_extracts_overview_content_and_faq() -> None:
    page = _load_page(FIXTURE_ROOT / "cells_rust" / "pages" / "_index.md")

    # cells_rust used to have faq.enable: true with 5 real FAQ entries;
    # TC-119's regeneration replaced it with repository-presenter's own
    # README content, which has no FAQ section at all.
    assert page["faq"]["enable"] is False

    result = _real_doc_candidates_for_pilot("cells_rust")

    # 1 overview + 3 content candidates + 0 faq = 4
    assert len(result) == 4

    overview = next(c for c in result if c.origin == "overview")
    assert overview.title == "Aspose.Cells FOSS for Rust"

    content = [c for c in result if c.origin == "content"]
    assert len(content) == 3
    content_titles = [c.title for c in content]
    assert content_titles == [
        "Installation, Dependencies, and Quick Start",
        "Key Capabilities and API Reference",
        "Scope and Limitations",
    ]
    api_reference = next(c for c in content if c.title == "Key Capabilities and API Reference")
    assert "213 types" in api_reference.body
    scope = next(c for c in content if c.title == "Scope and Limitations")
    assert "Rust 2021 edition" in scope.body

    faq = [c for c in result if c.origin == "faq"]
    assert faq == []
