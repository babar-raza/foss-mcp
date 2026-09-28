"""Tests for src/foss_mcp/indexing/example_candidates.py (REQ-G2-048).

This card is pure extraction only: no verification, compilation, or
execution of extracted code happens here or in the module under test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    extract_candidate_examples,
)

REAL_FIXTURE = Path("tests/fixtures/furnished/pdf_net/pages/_index.md")
FIXTURE_ROOT = Path("tests/fixtures/furnished")


def _load_page(fixture_path: Path) -> dict:
    text = fixture_path.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


def _load_real_page() -> dict:
    return _load_page(REAL_FIXTURE)


def test_extracts_title_description_language_and_code_from_synthetic_block() -> None:
    page = {
        "single": {
            "block": [
                {
                    "title": "Open a Widget and Frobnicate It",
                    "content": (
                        "Open an existing widget and frobnicate its first gear.\n"
                        "\n"
                        "```python\n"
                        "widget = Widget.open('input.widget')\n"
                        "widget.frobnicate()\n"
                        "```\n"
                    ),
                }
            ]
        }
    }

    result = extract_candidate_examples(page)

    assert result == [
        CandidateExample(
            title="Open a Widget and Frobnicate It",
            description="Open an existing widget and frobnicate its first gear.",
            language="python",
            code="widget = Widget.open('input.widget')\nwidget.frobnicate()",
        )
    ]


def test_block_with_no_code_fence_is_skipped_not_an_error() -> None:
    page = {
        "single": {
            "block": [
                {
                    "title": "Just Prose, No Code",
                    "content": "This block describes something but never shows any code.",
                },
                {
                    "title": "Has Code",
                    "content": ("Actually shows code this time.\n\n```csharp\nvar x = 1;\n```\n"),
                },
            ]
        }
    }

    result = extract_candidate_examples(page)

    assert len(result) == 1
    assert result[0].title == "Has Code"
    assert result[0].language == "csharp"
    assert result[0].code == "var x = 1;"


def test_real_fixture_extracts_three_candidates_including_real_watermark_example() -> None:
    page = _load_real_page()

    result = extract_candidate_examples(page)

    assert len(result) == 3

    titles = [c.title for c in result]
    assert "Add a Watermark Annotation" in titles

    watermark = next(c for c in result if c.title == "Add a Watermark Annotation")
    assert watermark.language == "csharp"
    assert "AddWatermarkAnnotation" in watermark.code
    assert all(isinstance(c.description, str) and c.description for c in result)
    assert all(isinstance(c.code, str) and c.code for c in result)


def test_real_fixture_pdf_java_extracts_three_java_candidates() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_java" / "pages" / "_index.md")

    result = extract_candidate_examples(page)

    assert len(result) == 3
    titles = [c.title for c in result]
    assert titles == [
        "Create a Document with Widget Annotation",
        "Create a Form with Radio Buttons",
        "Inspect Document Page Dimensions",
    ]
    assert all(c.language == "java" for c in result)
    assert all(isinstance(c.description, str) and c.description for c in result)
    assert all(isinstance(c.code, str) and c.code for c in result)

    widget = next(c for c in result if c.title == "Create a Document with Widget Annotation")
    assert "WidgetAnnotation w = new WidgetAnnotation(page, new Rectangle(0, 0, 100, 50));" in widget.code

    radio = next(c for c in result if c.title == "Create a Form with Radio Buttons")
    assert 'radio.setValue("Option2");' in radio.code

    dimensions = next(c for c in result if c.title == "Inspect Document Page Dimensions")
    assert "getMediaBox()" in dimensions.code


def test_real_fixture_pdf_typescript_extracts_four_typescript_candidates() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_typescript" / "pages" / "_index.md")

    result = extract_candidate_examples(page)

    assert len(result) == 4
    titles = [c.title for c in result]
    assert titles == [
        "Convert a PDF to HTML, Markdown, and DOCX",
        "Edit Pages and Metadata, Then Save",
        "Create a New PDF from Scratch",
        "Password-Protect and Encrypt a PDF",
    ]
    assert all(c.language == "typescript" for c in result)
    assert all(isinstance(c.description, str) and c.description for c in result)
    assert all(isinstance(c.code, str) and c.code for c in result)

    encrypt = next(c for c in result if c.title == "Password-Protect and Encrypt a PDF")
    assert "doc.WriteTo('locked.pdf'" in encrypt.code
    assert "algorithm: 'aes256'" in encrypt.code


def test_real_fixture_pdf_go_extracts_four_go_candidates() -> None:
    page = _load_page(FIXTURE_ROOT / "pdf_go" / "pages" / "_index.md")

    result = extract_candidate_examples(page)

    assert len(result) == 4
    titles = [c.title for c in result]
    assert titles == [
        "Split and Merge PDFs",
        "Encrypt with Permissions",
        "Fill AcroForm Fields",
        "Create Hierarchical Bookmarks",
    ]
    assert all(c.language == "go" for c in result)
    assert all(isinstance(c.description, str) and c.description for c in result)
    assert all(isinstance(c.code, str) and c.code for c in result)

    forms = next(c for c in result if c.title == "Fill AcroForm Fields")
    assert "(*pdf.TextBoxField)" in forms.code
    assert "(*pdf.CheckboxField)" in forms.code


def test_real_fixture_cells_rust_extracts_three_rust_candidates() -> None:
    page = _load_page(FIXTURE_ROOT / "cells_rust" / "pages" / "_index.md")

    result = extract_candidate_examples(page)

    assert len(result) == 3
    titles = [c.title for c in result]
    assert titles == [
        "Create, Save, and Reload a Workbook",
        "Load XLSX Files with Repair Options",
        "Add Charts from Data Ranges",
    ]
    assert all(c.language == "rust" for c in result)
    assert all(isinstance(c.description, str) and c.description for c in result)
    assert all(isinstance(c.code, str) and c.code for c in result)

    workbook = next(c for c in result if c.title == "Create, Save, and Reload a Workbook")
    assert "let mut workbook = Workbook::new();" in workbook.code
    assert 'put_formula_with_cached_value("=F1*2", CellValue::Number(20.0))?;' in workbook.code


def test_real_fixture_slides_python_extracts_two_python_candidates() -> None:
    page = _load_page(FIXTURE_ROOT / "slides_python" / "pages" / "_index.md")

    result = extract_candidate_examples(page)

    assert len(result) == 2
    titles = [c.title for c in result]
    assert titles == [
        "Create a Presentation and Add a Shape",
        "Format Text and Apply a Fill Effect",
    ]
    assert all(c.language == "python" for c in result)
    assert all(isinstance(c.description, str) and c.description for c in result)
    assert all(isinstance(c.code, str) and c.code for c in result)

    shape = next(c for c in result if c.title == "Create a Presentation and Add a Shape")
    assert "slides.ShapeType.RECTANGLE, 50, 50, 400, 150" in shape.code

    formatting = next(c for c in result if c.title == "Format Text and Apply a Fill Effect")
    assert "shape.fill_format.solid_fill_color.color = Color.alice_blue" in formatting.code
