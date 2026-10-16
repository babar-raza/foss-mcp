"""TC-517 (REQ-G3-008): a documentation chunk is categorised by its section title.

``classify_content_type`` used to look for keyword hints in the chunk TEXT, so only the generated
lead-in sentence of the "Scope and Limitations" block landed in troubleshooting while the list it
introduces landed in developer_guide. A chunk with a ``FQN: Doc: <title>`` first line is now decided by
the title (and a first body line starting ``FAQ:``); a text without that line keeps the keyword rule.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.search_docs import DocMatch, classify_content_type, search_docs
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

FURNISHED = Path(__file__).resolve().parents[2] / "fixtures" / "furnished"


def _text(title: str, first_body_line: str) -> str:
    return f"FQN: Doc: {title}\n{first_body_line}\nmore body text\nSource-Commit: {'0' * 40}"


@pytest.mark.parametrize(
    ("title", "first_body_line", "expected"),
    [
        ("Scope and Limitations", "Known issues, unsupported functionality:", "troubleshooting"),
        ("Scope and Limitations", "A plain list item with no hint word", "troubleshooting"),
        ("Troubleshooting", "Nothing special here", "troubleshooting"),
        ("Known Issues", "Nothing special here", "troubleshooting"),
        ("Common Errors", "Nothing special here", "troubleshooting"),
        ("Installation, Dependencies, and Quick Start", "Plain prose", "getting_started"),
        ("Quick Start", "Plain prose", "getting_started"),
        ("Quickstart", "Plain prose", "getting_started"),
        ("Getting Started", "Plain prose", "getting_started"),
        ("Prerequisites", "Plain prose", "getting_started"),
        ("Environment Setup", "Plain prose", "getting_started"),
        ("System Requirements", "Plain prose", "getting_started"),
        ("Key Capabilities and API Reference", "Plain prose", "developer_guide"),
        ("Aspose.PDF FOSS for .NET", "Install it with the troubleshooting guide", "developer_guide"),
        (
            "Does the install need other dependencies?",
            "FAQ: Does the install need other dependencies?",
            "faq",
        ),
        ("What does the error mean?", "FAQ: What does the error mean?", "faq"),
        ("INSTALLATION", "Plain prose", "getting_started"),
    ],
    ids=lambda value: str(value)[:40],
)
def test_the_section_title_decides_the_category(title: str, first_body_line: str, expected: str) -> None:
    assert classify_content_type(_text(title, first_body_line)) == expected


def test_a_title_rule_is_checked_in_the_documented_order() -> None:
    # "limitation" (troubleshooting) is checked before "install" (getting_started).
    assert classify_content_type(_text("Installation Limitations", "Plain prose")) == "troubleshooting"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A getting started guide for the first run.", "getting_started"),
        ("If you hit a known issue, check the font settings.", "troubleshooting"),
        ("Frequently asked questions about licensing.", "faq"),
        ("Plain reference prose about the document model.", "developer_guide"),
    ],
)
def test_a_text_without_a_doc_title_keeps_the_keyword_rule(text: str, expected: str) -> None:
    assert classify_content_type(text) == expected


def test_a_symbol_or_example_fqn_line_is_not_a_doc_title() -> None:
    assert classify_content_type("FQN: Example: Install the package\nplain") == "getting_started"
    assert classify_content_type("FQN: Aspose.Pdf.Document\nOpens a file.") == "developer_guide"


def test_the_part_marker_does_not_change_the_category() -> None:
    base = _text("Scope and Limitations", "Some list line")
    marked = base.replace("more body text", "more body text\nPart: 2 of 3")
    assert classify_content_type(base) == classify_content_type(marked) == "troubleshooting"
    faq = "FQN: Doc: What is it?\nFAQ: What is it?\n\nAn answer.\nPart: 1 of 2\nSource-Commit: " + "0" * 40
    assert classify_content_type(faq) == "faq"


def _real_pdf_java_chunks() -> list:
    page_path = FURNISHED / "pdf_java" / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(page_path)
    args = argparse.Namespace(library_repository="o/r", library_commit="0" * 40, furnished_page=page_path)
    chunks = build_chunks._build_doc_chunks(page, args)
    return [build_chunks._with_source_commit_line(chunk) for chunk in chunks]


def test_search_docs_troubleshooting_now_returns_the_scope_and_limitations_list(tmp_path: Path) -> None:
    store = GenerationManifestStore(tmp_path / "manifests")
    scope = Scope(family="pdf", platform="java")
    lease = store.acquire_lease("pdf::java::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family=scope.family,
        platform=scope.platform,
        source_kind="self_extracted",
        expected_active=None,
        chunks=_real_pdf_java_chunks(),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )

    result = search_docs(store, scope, "known issues", "troubleshooting")

    assert isinstance(result, list) and result
    assert all(isinstance(match, DocMatch) for match in result)
    top = result[0]
    assert top.content_type == "troubleshooting"
    assert "troubleshooting-relevant scope boundaries" in top.text
    assert "is not thread-safe" in top.text, "the list the lead-in introduces must be in the same chunk"
