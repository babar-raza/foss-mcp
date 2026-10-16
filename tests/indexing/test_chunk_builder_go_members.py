"""TC-525 (REQ-G3-005): what the Go extraction now records is what the symbol chunks state.

Offline. The committed ``go_sample`` package is extracted and wrapped as a fixture, then chunked
through ``build_chunks_from_api_surface`` unchanged: constants render as ``Members:`` lines, a
pointer-receiver method renders with its signature, an unattached exported method renders its
receiver, and a method of an unexported type has no chunk at all.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

GO_SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "go_sample"


def _texts() -> list[str]:
    types, *_ = api_surface.extract_api_surface(get_parser("go"), "go", GO_SAMPLE, GO_SAMPLE, "pdf")
    fixture = {
        "source_repository": "acme/pdf-go",
        "source_commit": "c0ffee",
        "language": "go",
        "types": types,
    }
    return [chunk.text for chunk in build_chunks_from_api_surface(fixture, title="pdf/go", max_types=10_000)]


def _chunk_for(texts: list[str], name: str) -> str:
    matches = [text for text in texts if text.splitlines()[0] == f"FQN: {name}"]
    assert len(matches) == 1, [text.splitlines()[:3] for text in texts]
    return matches[0]


def test_enum_constants_render_as_members_lines() -> None:
    texts = _texts()
    halign = _chunk_for(texts, "HAlign")
    assert "Members:" in halign
    assert "  - HAlignCenter = 1" in halign.splitlines()
    assert '  - SaveFormatPDF = "pdf"' in _chunk_for(texts, "SaveFormat").splitlines()


def test_pointer_receiver_method_renders_with_its_signature() -> None:
    document = _chunk_for(_texts(), "Document")
    assert "  - Save(w: io.Writer, format: SaveFormat) -> error" in document.splitlines()


def test_unattached_method_renders_its_receiver() -> None:
    run = _chunk_for(_texts(), "Run")
    assert "[receiver *Missing]" in run


def test_unexported_receiver_method_has_no_chunk() -> None:
    assert not any("Flatten" in text for text in _texts())
