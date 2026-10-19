"""TC-535 (Round 3, rows RD3-025 to RD3-033): symbol chunks of the regenerated Go and Rust fixtures say what the sources say.

The expected lines are the upstream declarations, read from the clones at the chart pins (pdf/go ``ai/chat.go:54``,
``decrypt.go:14``, cells/go ``streaming_reader.go:16``, cells/rust ``Workbook.rs:216``, ``api.rs``), never the renderer's
output. The synthetic tests render hand-made entries: the renderer must say a Go symbol's package and import path, mark a
promoted member as promoted, print a variable's value, a function type's signature, a Rust method's receiver and generic
bounds, an enum variant's payload and a type alias's target, without breaking the member-name convention
(``get_symbol`` reads a member's name up to its first ``(`` or ``:``).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.mcp.tools.search_symbols import _member_name

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@cache
def _chunks(pilot: str) -> tuple[str, ...]:
    fixture = json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))
    return tuple(c.text for c in build_chunks_from_api_surface(fixture, title="t", max_types=100_000))


def _chunk_text(pilot: str, fqn: str) -> str:
    found = [text for text in _chunks(pilot) if text.splitlines()[0] == f"FQN: {fqn}"]
    assert len(found) == 1, (fqn, len(found))
    return found[0]


def _render(entries: list[dict[str, Any]]) -> list[str]:
    fixture = {"source_repository": "org/repo", "source_commit": "c0ffee", "types": entries}
    return [c.text for c in build_chunks_from_api_surface(fixture, title="t", max_types=10_000)]


def _entry(name: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "kind": "type_spec",
        "file": "x.go",
        "line": 1,
        "doc": "",
        "bases": [],
        "methods": [],
        "properties": [],
        "reachable": True,
    }
    base.update(fields)
    return base


# -- the regenerated fixtures ---------------------------------------------------------------------------------------


def test_a_pdf_go_ai_function_says_its_package_its_import_path_and_its_call_form() -> None:
    # ai/chat.go:54 -- `func NewChatCopilot(client AIClient, opts ChatOptions) *ChatCopilot` in `package ai`
    lines = _chunk_text("pdf_go", "NewChatCopilot").splitlines()
    assert "Package: ai" in lines
    assert "Import: github.com/aspose-pdf-foss/aspose-pdf-foss-for-go/ai" in lines
    assert "Qualified: ai.NewChatCopilot" in lines
    assert "  - NewChatCopilot(client: AIClient, opts: ChatOptions) -> *ChatCopilot" in lines


def test_a_pdf_go_root_symbol_is_called_through_the_root_package_name() -> None:
    # go.mod: module github.com/aspose-pdf-foss/aspose-pdf-foss-for-go; the root files declare `package asposepdf`
    lines = _chunk_text("pdf_go", "Document").splitlines()
    assert "Package: asposepdf" in lines
    assert "Import: github.com/aspose-pdf-foss/aspose-pdf-foss-for-go" in lines
    assert "Qualified: asposepdf.Document" in lines


def test_a_pdf_go_package_variable_prints_its_initialiser() -> None:
    # decrypt.go:14 -- `ErrEncrypted = errors.New("PDF is encrypted; use OpenWithPassword")` in a `var (...)` group
    lines = _chunk_text("pdf_go", "ErrEncrypted").splitlines()
    assert "Kind: variable" in lines
    assert 'Value: = errors.New("PDF is encrypted; use OpenWithPassword")' in lines


def test_a_pdf_go_typed_variable_prints_its_declared_type() -> None:
    # font_api.go -- `FontHelvetica Font = standardFont{name: "Helvetica"}`
    lines = _chunk_text("pdf_go", "FontHelvetica").splitlines()
    assert 'Value: Font = standardFont{name: "Helvetica"}' in lines


def test_a_cells_go_function_type_prints_its_signature() -> None:
    # streaming_reader.go:16 -- `type RowCallback func(rowIndex int, cells map[string]string) error`
    lines = _chunk_text("cells_go", "RowCallback").splitlines()
    assert "Underlying: func(rowIndex int, cells map[string]string) error" in lines


def test_a_cells_go_untyped_constant_prints_its_value() -> None:
    # style.go -- `AlignCenter = "center"` in the alignment constant group
    lines = _chunk_text("cells_go", "AlignCenter").splitlines()
    assert "Kind: constant" in lines
    assert 'Value: = "center"' in lines


def test_a_pdf_go_embedding_type_lists_the_members_it_gets_by_promotion() -> None:
    # barcode.go:127 -- `type BarcodeField struct{ TextBoxField }`; form_fields.go:166 -- `type TextBoxField struct{ fieldBase }`;
    # form_fields.go:98 -- `func (b *fieldBase) PartialName() string`; :168 -- `func (f *TextBoxField) Value() string`
    lines = _chunk_text("pdf_go", "BarcodeField").splitlines()
    assert "  - Value() -> string  (promoted from TextBoxField)" in lines
    assert "  - PartialName() -> string  (promoted from fieldBase)" in lines
    assert not [line for line in lines if "(inherited from" in line]


def test_a_cells_rust_method_says_its_receiver_and_its_generic_bounds() -> None:
    # Workbook.rs:216 -- `pub fn add_worksheet(&mut self, name: impl Into<String>) -> Result<usize, CellsError>`
    # Workbook.rs:565 -- `pub fn save_xlsx_to_stream<W: Write + Seek>(&self, stream: W) -> Result<(), CellsError>`
    lines = _chunk_text("cells_rust", "Workbook").splitlines()
    assert "  - add_worksheet(&mut self, name: impl Into<String>) -> Result<usize, CellsError>" in lines
    assert (
        "  - save_xlsx_to_stream(&self, stream: W) -> Result<(), CellsError>  [generic <W: Write + Seek>]"
        in lines
    )


def test_a_cells_rust_enum_lists_its_variants_with_their_payloads() -> None:
    # api.rs -- `pub enum CellsError { SheetNotFound(String), WorksheetIndexOutOfRange(usize), InvalidSheetName, ... }`
    lines = _chunk_text("cells_rust", "CellsError").splitlines()
    assert "  - SheetNotFound(String)" in lines
    assert "  - WorksheetIndexOutOfRange(usize)" in lines
    assert "  - InvalidSheetName" in lines
    assert "  - Unsupported(&'static str)" in lines


def test_a_cells_rust_alias_is_a_chunk_with_its_target() -> None:
    # Style.rs:437 -- `pub type Style = CellStyle;`; WorksheetCollection.rs:8 -- `pub type WorksheetCollection<'a> = Worksheets<'a>;`
    style = _chunk_text("cells_rust", "Style").splitlines()
    assert "Alias-Of: CellStyle" in style
    collection = _chunk_text("cells_rust", "WorksheetCollection").splitlines()
    assert "Alias-Of: Worksheets<'a>" in collection
    assert "Generics: <'a>" in collection


def test_a_cells_rust_error_enum_lists_its_derives_apart_from_its_bases() -> None:
    lines = _chunk_text("cells_rust", "CellsError").splitlines()
    assert "Derives: Debug, Clone, PartialEq" in lines
    bases = lines[lines.index("Bases:") + 1 : lines.index("Methods:") - 1]
    assert bases == ["  - Display"]


# -- hand-made entries -----------------------------------------------------------------------------------------------


def test_a_promoted_member_says_promoted_and_an_inherited_one_still_says_inherited() -> None:
    entry = _entry(
        "Box",
        methods=[
            {
                "name": "Rename",
                "params": [],
                "return_type": "",
                "inherited_from": "base",
                "promoted_from": "base",
            },
            {"name": "Walk", "params": [], "return_type": "", "inherited_from": "Mover"},
        ],
        properties=[
            {"name": "Name", "type": "string", "inherited_from": "base", "promoted_from": "base"},
        ],
    )
    (text,) = _render([entry])
    lines = text.splitlines()
    assert "  - Rename()  (promoted from base)" in lines
    assert "  - Walk()  (inherited from Mover)" in lines
    assert "  - Name: string  (promoted from base)" in lines


def test_a_package_less_entry_renders_no_package_lines() -> None:
    (text,) = _render([_entry("Plain")])
    assert not [line for line in text.splitlines() if line.startswith(("Package:", "Import:", "Qualified:"))]


def test_a_variable_without_an_initialiser_prints_only_its_type() -> None:
    entry = _entry(
        "Limit", kind="variable", type="int", value="", package="lib", import_path="example.com/lib"
    )
    (text,) = _render([entry])
    lines = text.splitlines()
    assert "Value: int" in lines
    assert "Qualified: lib.Limit" in lines


def test_rust_receivers_and_generics_stay_clear_of_the_member_name() -> None:
    method = {
        "name": "put",
        "params": [{"name": "p", "type": "P"}],
        "return_type": "bool",
        "receiver": "&mut self",
        "generics": "<P: AsRef<str>>",
        "where": "where P: Send",
        "modifiers": ["impl Store"],
    }
    (text,) = _render([_entry("Store", kind="struct_item", methods=[method])])
    line = next(row for row in text.splitlines() if row.startswith("  - put("))
    assert line == "  - put(&mut self, p: P) -> bool [impl Store]  [generic <P: AsRef<str>> where P: Send]"
    assert _member_name(line[len("  - ") :]) == "put"


def test_a_receiver_without_other_parameters_is_the_whole_parameter_list() -> None:
    method = {"name": "len", "params": [], "return_type": "usize", "receiver": "&self"}
    (text,) = _render([_entry("List", kind="struct_item", methods=[method])])
    assert "  - len(&self) -> usize" in text.splitlines()


def test_struct_variants_keep_their_braces_and_a_discriminant_its_value() -> None:
    members = [
        {"name": "A", "value": "", "payload": "(i32)"},
        {"name": "B", "value": "", "payload": "{ x: u8 }"},
        {"name": "C", "value": "5"},
    ]
    (text,) = _render([_entry("V", kind="enum_item", enum_members=members)])
    lines = text.splitlines()
    assert "  - A(i32)" in lines
    assert "  - B { x: u8 }" in lines
    assert "  - C = 5" in lines
    assert [_member_name(line[len("  - ") :]).split()[0] for line in lines if line.startswith("  - ")] == [
        "A",
        "B",
        "C",
    ]
