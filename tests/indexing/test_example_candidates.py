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


def _load_real_page() -> dict:
    text = REAL_FIXTURE.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


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
