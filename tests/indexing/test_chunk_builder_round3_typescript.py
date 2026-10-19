"""TC-533 (Round 3, rows RD3-010 to RD3-013): symbol chunks of the regenerated TypeScript fixtures say what the source says.

The expected lines are the upstream source's own declarations, read from the clone at the chart pin
(``aspose_cells/types.ts:16``, ``src/aspose/threed/Scene.ts:386``, ``src/flow.ts:1256``, ``src/stamp.ts:396``),
never the renderer's output. The synthetic tests pin the rendering rules the regenerated data relies on.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@cache
def _fixture(pilot: str) -> dict[str, Any]:
    return json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


def _chunk_text(fixture: dict[str, Any], class_import: str) -> str:
    """The text of the one chunk whose ``FQN:`` line is *class_import*."""
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=100_000)
    found = [chunk.text for chunk in chunks if f"FQN: {class_import}\n" in chunk.text + "\n"]
    assert found, class_import
    return found[0]


def _synthetic(types: list[dict[str, Any]]) -> list[str]:
    fixture = {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types}
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=10_000)
    return "\n".join(chunk.text for chunk in chunks).splitlines()


# -- the regenerated cells, 3d and pdf fixtures --------------------------------------------------------------------------


def test_a_cells_interface_property_is_optional_and_writable_with_a_clean_type() -> None:
    # aspose_cells/types.ts:16 -- `font?: Font;` in `interface Style`; the interface has no readonly member.
    text = _chunk_text(_fixture("cells_typescript"), "aspose_cells.types.Style")
    assert "  - font: Font [optional] (writable)" in text.splitlines()
    assert "(read-only)" not in text
    assert ": : " not in text


def test_a_cells_type_alias_prints_its_right_hand_side() -> None:
    # aspose_cells/types.ts:289 -- `export type ArrowShapeType = "arrow" | "rightArrow" | ...;`
    text = _chunk_text(_fixture("cells_typescript"), "aspose_cells.types.ArrowShapeType")
    (alias_line,) = [line for line in text.splitlines() if line.startswith("Alias-Of:")]
    assert alias_line.startswith('Alias-Of: "arrow" | "rightArrow"')


def test_a_3d_static_factory_and_a_rest_parameter_are_rendered() -> None:
    fixture = _fixture("3d_typescript")
    # src/aspose/threed/Scene.ts:386 -- `static fromFile(fileName: string): Scene`
    scene = _chunk_text(fixture, "aspose.threed.Scene.Scene")
    assert "  - fromFile(fileName: string) -> Scene [static]" in scene.splitlines()
    # src/aspose/threed/entities/Mesh.ts:35 -- `createPolygon(...args: any[]): void`
    mesh = _chunk_text(fixture, "aspose.threed.entities.Mesh.Mesh")
    assert "  - createPolygon(...args: any[]) -> void" in mesh.splitlines()


def test_a_pdf_this_return_a_default_and_a_one_item_generic_base_are_rendered() -> None:
    fixture = _fixture("pdf_typescript")
    # src/flow.ts:1256 -- `AddParagraph(text: FlowText, options: FlowParagraphOptions = {}): this`
    flow = _chunk_text(fixture, "flow.Flow")
    assert "  - AddParagraph(text: FlowText, options: FlowParagraphOptions = {}) -> this" in flow.splitlines()
    # src/stamp.ts:396 -- `export interface TextBlockOptions extends Omit<StampOptions, 'align' | 'rotate'> {`
    options = _chunk_text(fixture, "stamp.TextBlockOptions")
    lines = options.splitlines()
    start = lines.index("Bases:")
    assert lines[start + 1] == "  - Omit<StampOptions, 'align' | 'rotate'>"
    assert lines[start + 2] == ""


def test_no_regenerated_chunk_prints_a_stray_colon_or_an_unbalanced_base() -> None:
    for pilot in ("cells_typescript", "3d_typescript", "pdf_typescript"):
        chunks = build_chunks_from_api_surface(_fixture(pilot), title="t", max_types=100_000)
        for chunk in chunks:
            for line in chunk.text.splitlines():
                assert not line.startswith("  - ") or ": :" not in line, (pilot, line)
                assert not (line.startswith("  - ") and line.rstrip().endswith(":")), (pilot, line)


# -- the rendering rules the regenerated data relies on ------------------------------------------------------------------


def test_a_leading_colon_in_an_older_fixtures_property_type_is_still_stripped() -> None:
    interface = {
        "name": "I",
        "kind": "interface_declaration",
        "methods": [],
        "properties": [{"name": "a", "type": ": string", "writable": True}],
    }
    assert "  - a: string (writable)" in _synthetic([interface])


def test_a_static_readonly_constant_property_renders_static_and_read_only() -> None:
    cls = {
        "name": "V",
        "kind": "class_declaration",
        "methods": [],
        "properties": [
            {
                "name": "VERSION",
                "type": "",
                "writable": False,
                "modifiers": ["static"],
                "kind": "constant",
                "value": "'1.0'",
            }
        ],
    }
    assert "  - VERSION = '1.0' [static] (read-only)" in _synthetic([cls])


def test_a_rest_parameter_an_optional_parameter_and_a_default_render_in_order() -> None:
    method = {
        "name": "make",
        "params": [
            {"name": "first", "type": "number"},
            {"name": "label", "type": "string", "optional": True},
            {"name": "mode", "type": "Mode", "default": "Mode.A"},
            {"name": "...rest", "type": "number[]"},
        ],
        "return_type": "this",
    }
    cls = {"name": "M", "kind": "class_declaration", "methods": [method], "properties": []}
    assert (
        "  - make(first: number, label?: string, mode: Mode = Mode.A, ...rest: number[]) -> this"
        in _synthetic([cls])
    )


def test_a_generic_base_with_a_comma_inside_its_brackets_is_one_bases_line() -> None:
    interface = {
        "name": "C",
        "kind": "interface_declaration",
        "bases": ["Omit<A, 'x' | 'y'>", "B"],
        "methods": [],
        "properties": [],
    }
    lines = _synthetic([interface])
    start = lines.index("Bases:")
    assert lines[start + 1 : start + 3] == ["  - Omit<A, 'x' | 'y'>", "  - B"]
