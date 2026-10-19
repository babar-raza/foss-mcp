"""TC-534 (Round 3, rows RD3-020 to RD3-023): symbol chunks of the regenerated C++ fixtures say what the headers say.

The expected lines are the upstream headers' own declarations, read from the clones at the chart pins
(slides ``include/Aspose/Slides/Foss/drawing/color.h:42``, cells ``DefinedNameUtility.h:13``, pdf
``include/aspose/pdf/facades/pdf_file_editor.hpp:110``, slides ``slide_collection.h:111``), never the renderer's
output. The synthetic tests run a hand-written header through the extractor and the renderer.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@cache
def _chunks(pilot: str) -> tuple[str, ...]:
    fixture = json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))
    return tuple(c.text for c in build_chunks_from_api_surface(fixture, title="t", max_types=100_000))


def _chunk_text(pilot: str, fqn: str) -> str:
    found = [text for text in _chunks(pilot) if text.splitlines()[0] == f"FQN: {fqn}"]
    assert len(found) == 1, (fqn, len(found))
    return found[0]


def _render(tmp_path: Path, header: str) -> list[str]:
    package = tmp_path / "src"
    package.mkdir()
    (package / "widget.h").write_text(header, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", package, tmp_path, "widget")
    fixture = {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types}
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=10_000)
    return "\n".join(c.text for c in chunks).splitlines()


# -- the regenerated slides, cells and pdf fixtures ------------------------------------------------------------------


def test_a_slides_named_color_constant_is_static_const_and_read_only() -> None:
    # color.h:42 -- `static const Color alice_blue;` inside `class Color`
    text = _chunk_text("slides_cpp", "Aspose::Slides::Foss::Drawing::Color")
    lines = text.splitlines()
    assert "  - alice_blue: Color [static, const] (read-only)" in lines
    # color.h declares 141 such constants (a hand count of `static const Color <name>;` lines): none is writable
    constants = [line for line in lines if line.endswith("[static, const] (read-only)")]
    assert len(constants) == 141
    assert not [line for line in lines if line.startswith("  - ") and line.endswith("(writable)")]


def test_a_cells_constexpr_string_constant_is_static_constexpr_const_and_read_only() -> None:
    # DefinedNameUtility.h:13 -- `static constexpr const char* PrintAreaDefinedName = "_xlnm.Print_Area";`
    text = _chunk_text("cells_cpp", "Aspose::Cells_FOSS::DefinedNameUtility")
    assert "  - PrintAreaDefinedName: const char* [static, constexpr, const] (read-only)" in text.splitlines()


def test_a_pdf_nested_class_is_a_chunk_of_its_own_with_its_constructor_and_accessors() -> None:
    # pdf_file_editor.hpp:110 -- `class ContentsResizeParameters { public: ContentsResizeParameters() noexcept = default; ...`
    # and :120 `ContentsResizeValue NewPageWidth() const noexcept;` / `void NewPageWidth(ContentsResizeValue value) noexcept;`
    text = _chunk_text("pdf_cpp", "Aspose::Pdf::Facades::PdfFileEditor::ContentsResizeParameters")
    lines = text.splitlines()
    assert lines[1] == "Kind: class_specifier"
    assert "  - ContentsResizeParameters() [noexcept, defaulted]" in lines
    assert "  - NewPageWidth() -> ContentsResizeValue [const, noexcept]" in lines
    assert "  - NewPageWidth(value: ContentsResizeValue) -> void [noexcept]" in lines


def test_a_slides_iterator_alias_is_a_chunk_that_names_its_target() -> None:
    # slide_collection.h:111 -- `using iterator = std::vector<std::unique_ptr<Slide>>::iterator;`
    text = _chunk_text("slides_cpp", "Aspose::Slides::Foss::SlideCollection::iterator")
    lines = text.splitlines()
    assert lines[1] == "Kind: alias_declaration"
    assert lines[2] == "Alias-Of: std::vector<std::unique_ptr<Slide>>::iterator"


def test_no_slides_chunk_is_an_internal_declaration() -> None:
    assert [t.splitlines()[0] for t in _chunks("slides_cpp") if "::Internal::" in t.splitlines()[0]] == []
    assert any(t.splitlines()[0] == "FQN: Aspose::Slides::Foss::Presentation" for t in _chunks("slides_cpp"))


# -- the extractor and the renderer together on a hand-written header ----------------------------------------------


HEADER = """
struct Palette {
    static const Palette Red;
    static constexpr int Size = 3;
    const char* label;
    char* const tag;
    int shade = 0;
    struct Entry { int index = 0; };
    using iterator = std::vector<int>::iterator;
};
"""


def test_static_const_members_render_with_their_qualifiers_and_read_only(tmp_path: Path) -> None:
    lines = _render(tmp_path, HEADER)
    assert "  - Red: Palette [static, const] (read-only)" in lines
    assert "  - Size: int [static, constexpr, const] (read-only)" in lines
    assert "  - label: const char* (writable)" in lines  # only the pointee is const
    assert "  - tag: char* const [const] (read-only)" in lines
    assert "  - shade: int (writable)" in lines


def test_nested_types_and_iterator_aliases_render_as_chunks_of_their_own(tmp_path: Path) -> None:
    lines = _render(tmp_path, HEADER)
    assert "FQN: Palette::Entry" in lines
    assert "FQN: Palette::iterator" in lines
    assert "Alias-Of: std::vector<int>::iterator" in lines


def test_the_switch_changes_what_the_chunk_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_surface, "CPP_KEEP_STATIC_CONST_QUALIFIERS", False)
    lines = _render(tmp_path, HEADER)
    assert "  - Red: Palette (writable)" in lines
    assert "  - Size: int (writable)" in lines
    assert not [line for line in lines if "[static" in line]


def test_an_unnamed_default_and_deleted_member_rendering_is_unchanged(tmp_path: Path) -> None:
    header = "class Box {\npublic:\n    Box(int w = 2);\n    Box(const Box&) = delete;\n    bool operator==(const Box& o) const;\n};\n"
    lines = _render(tmp_path, header)
    assert "  - Box(w: int = 2)" in lines
    assert "  - Box(const Box&) [deleted]" in lines
    assert "  - operator==(o: const Box&) -> bool [const]" in lines
