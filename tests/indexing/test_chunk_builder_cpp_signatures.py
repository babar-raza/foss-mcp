"""TC-524: a C++ symbol chunk serves the signature a caller needs to compile (REQ-G3-008, finding RST-06).

The fixture is the REAL extraction of the committed `tests/fixtures/cpp_sample` header sample, wrapped as an
`api_surface.json` document and chunked through `build_chunks_from_api_surface`. Offline and deterministic.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cpp_sample"


@cache
def _chunk_text() -> str:
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", SAMPLE_DIR, SAMPLE_DIR, "cells")
    fixture = {"source_repository": "org/cells-cpp", "source_commit": "c0ffee", "types": types}
    chunks = build_chunks_from_api_surface(fixture, title="cells cpp", max_types=10_000)
    return "\n".join(chunk.text for chunk in chunks)


def _lines() -> list[str]:
    return _chunk_text().splitlines()


def test_chunk_renders_reference_const_and_static_for_cpp() -> None:
    lines = _lines()
    assert "  - Save(stream: std::vector<std::uint8_t>&, format: SaveFormat) -> void [const]" in lines
    assert "  - GetWorksheets() -> WorksheetCollection&" in lines
    assert "  - GetWorksheets() -> const WorksheetCollection& [const]" in lines
    assert (
        "  - Open(path: const std::string&, password: const char* = nullptr) -> Workbook [static, noexcept]"
        in lines
    )


def test_chunk_renders_explicit_constructor_without_arrow() -> None:
    lines = _lines()
    assert "  - Workbook(path: std::string_view) [explicit]" in lines
    assert "  - Workbook()" in lines
    assert "  - Workbook(data: const std::vector<std::uint8_t>&, flags: int = 0)" in lines


def test_chunk_renders_unnamed_parameter_as_type_only() -> None:
    lines = _lines()
    assert "  - Workbook(const Workbook&) [deleted]" in lines
    assert "  - Workbook(Workbook&&) [noexcept, defaulted]" in lines
    assert not [line for line in lines if "(: " in line or ", : " in line]


def test_chunk_for_a_cpp_helper_never_exists() -> None:
    text = _chunk_text()
    assert "GetElapsedMinutes" not in text
    assert "IsNumericFormatChar" not in text
    assert "EscapeTextContent" not in text
    assert "HiddenHelper" not in text
    assert "FileLocal" not in text
    assert "AnonymousHelper" not in text
    assert "IsLeapYear" in text
