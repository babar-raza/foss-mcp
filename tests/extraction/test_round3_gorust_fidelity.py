"""G3/TC-535 (REQ-G3-009, REQ-G3-008; Round-3 rows RD3-025 to RD3-033): the three regenerated Go and Rust fixtures say what the sources say.

Offline and deterministic. Nothing here asks the extractor: every expectation comes from an INDEPENDENT oracle, read at
the chart pins when this card was written, and each is a fact about the UPSTREAM source:

* ``go doc`` / ``go/doc`` and ``go/types`` (a Go program run over the clones, not part of this suite) for the exported
  names of every package, the 26 + 20 package-level constants and variables that no type owns, and the full method set
  of each exported type, promoted methods included: pdf/go 1,202 exported methods, cells/go 23;
* the sources read directly: the five tagged fields of ``Cell`` (``cell.go:14-18``), ``type RowCallback func(...)``
  (``streaming_reader.go:16``), ``type SVGFontResolver func(...)`` (``svg.go:95``), the ``pub use`` statements of the
  crate root (``lib.rs``, reproduced below) and the variants of ``CellValue``, ``CellsError`` and ``CellObjectValue``
  (``api.rs``).

Where the round-3 audit's figure differed from the source it is stated: the card text says ``WorkbookModel`` is importable at
the crate root of cells/rust; it is NOT (``Core/WorkbookModel.rs`` is a file no ``mod`` declares, so
``use aspose_cells_foss_rust::WorkbookModel`` fails E0432, as the audit row RD3-030 itself says).
"""

from __future__ import annotations

import json
import os
import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
VALUES = Path(__file__).resolve().parents[2] / "infra" / "helm" / "foss-mcp" / "values.yaml"

# -- the pins ---------------------------------------------------------------------------------------------------------


@cache
def _pilots() -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return {f"{p['family']}_{p['platform']}": p for p in doc["ingestion"]["pilots"]}


@cache
def _fixture(pilot: str) -> dict[str, Any]:
    return json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


def _entries(pilot: str) -> list[dict[str, Any]]:
    return _fixture(pilot)["types"]


def _by_name(pilot: str) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in _entries(pilot)}


# -- every regenerated fixture ----------------------------------------------------------------------------------------


def test_each_fixture_is_the_whole_surface_at_the_chart_pin_by_the_current_extractor() -> None:
    for pilot in ("pdf_go", "cells_go", "cells_rust"):
        data = _fixture(pilot)
        assert data["source_commit"] == _pilots()[pilot]["library"]["commit"], pilot
        assert data["extractor_version"] == 5, pilot
        assert data["truncated"] is False, pilot
        assert data["reduced_type_count"] == data["type_count"] == len(data["types"]), pilot
        assert set(data) >= {"symbol_counts", "counts", "parse_failures"}, pilot
        functions = sum(1 for e in data["types"] if e["kind"] == "function")
        assert data["symbol_counts"] == {"types": len(data["types"]) - functions, "functions": functions}, (
            pilot
        )
        assert data["parse_failures"]["failed"] == 0, pilot
        assert data["counts"]["methods"] == sum(len(e["methods"]) for e in data["types"]), pilot


def test_no_fixture_is_larger_than_its_chart_cap() -> None:
    for pilot in ("pdf_go", "cells_go", "cells_rust"):
        cap = _pilots()[pilot]["maxTypes"]
        assert len(_entries(pilot)) <= cap, (pilot, len(_entries(pilot)), cap)
    assert _pilots()["pdf_go"]["maxTypes"] == len(
        _entries("pdf_go")
    )  # one number: chart, compose and extractor


# -- pdf/go -----------------------------------------------------------------------------------------------------------

PDF_GO_MODULE = "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"
# `go doc -all ./ai` at the pin: 25 types and 6 constructors, all `package ai`, directory ai/
PDF_GO_AI_NAMES = {
    "AIClient",
    "APIError",
    "ChatCopilot",
    "ChatOptions",
    "CompletionRequest",
    "CompletionResponse",
    "ImageDescriptionCopilot",
    "ImageDescriptionOptions",
    "LLMOCREngine",
    "LLMOCROptions",
    "Message",
    "MessageImage",
    "NewChatCopilot",
    "NewImageDescriptionCopilot",
    "NewLLMOCREngine",
    "NewOcrCopilot",
    "NewOpenAIClient",
    "NewSummaryCopilot",
    "OCRBox",
    "OCREngine",
    "OCRLine",
    "OCRResult",
    "OCRWord",
    "OcrCopilot",
    "OcrOptions",
    "OpenAIClient",
    "OpenAIClientOptions",
    "SummaryCopilot",
    "SummaryOptions",
    "TextRecognitionResult",
    "Usage",
}
# the package-level const / var declarations no type owns (go/ast over every non-test file at the pin)
PDF_GO_UNOWNED_CONSTANTS = {
    "BorderSideAll",
    "DefaultBaseURL",
    "DefaultDPI",
    "RoleAssistant",
    "RoleSystem",
    "RoleUser",
}
PDF_GO_UNOWNED_VARIABLES = {
    "ErrEncrypted",
    "ErrInvalidPassword",
    "FontCourier",
    "FontCourierBold",
    "FontCourierBoldOblique",
    "FontCourierOblique",
    "FontHelvetica",
    "FontHelveticaBold",
    "FontHelveticaBoldOblique",
    "FontHelveticaOblique",
    "FontSymbol",
    "FontTimesBold",
    "FontTimesBoldItalic",
    "FontTimesItalic",
    "FontTimesRoman",
    "FontZapfDingbats",
    "PageFormatA3",
    "PageFormatA4",
    "PageFormatLegal",
    "PageFormatLetter",
}
# go/types: the method set of *BarcodeField (form_fields.go:166 `TextBoxField struct{ fieldBase }`, barcode.go:127)
BARCODE_FIELD_METHODS = {
    "Symbology",
    "SetSymbology",
    "SetValue",  # its own
    "Value",
    "MaxLen",
    "IsMultiline",
    "IsPassword",
    "SetReadOnly",
    "SetRequired",
    "SetMultiline",
    "SetPassword",
    "SetMaxLen",  # TextBoxField
    "Flatten",
    "PartialName",
    "FullName",
    "IsReadOnly",
    "IsRequired",
    "PageIndex",
    "Rect",
    "SetStyle",
    "Style",  # the unexported fieldBase
}


def test_the_ai_sub_package_symbols_carry_the_ai_import_path_and_are_not_the_root_package() -> None:
    entries = _entries("pdf_go")
    ai = {
        e["name"]: e
        for e in entries
        if e["file"].startswith("ai/") and e["kind"] in ("type_spec", "function")
    }
    assert set(ai) == PDF_GO_AI_NAMES  # 31 names, RD3-025
    for name, entry in ai.items():
        assert entry["package"] == "ai", name
        assert entry["import_path"] == f"{PDF_GO_MODULE}/ai", name
    assert ai["NewChatCopilot"]["kind"] == "function"
    root = [e for e in entries if not e["file"].startswith("ai/")]
    assert {e["import_path"] for e in root} == {PDF_GO_MODULE}
    assert {e["package"] for e in root} == {"asposepdf"}  # the root package is not called `pdf`
    assert not PDF_GO_AI_NAMES & {e["name"] for e in root if e["kind"] in ("type_spec", "function")}


def test_a_tree_laid_out_like_pdf_go_extracts_with_the_ai_import_path(tmp_path: Path) -> None:
    # the layout of the pinned repository: go.mod of the chart's module path, the root package `asposepdf` and `ai/`
    module = _pilots()["pdf_go"]["library"]["modulePath"]
    (tmp_path / "ai").mkdir()
    (tmp_path / "go.mod").write_text(f"module {module}\n\ngo 1.24\n", encoding="utf-8")
    (tmp_path / "doc.go").write_text(
        "package asposepdf\n\nfunc NewDocument() int { return 0 }\n", encoding="utf-8"
    )
    (tmp_path / "ai" / "chat.go").write_text(
        "package ai\n\ntype ChatCopilot struct{}\n\nfunc NewChatCopilot() *ChatCopilot { return nil }\n",
        encoding="utf-8",
    )
    types, *_ = api_surface.extract_api_surface(get_parser("go"), "go", tmp_path, tmp_path, "pdf")
    by_name = {e["name"]: e for e in types}
    assert by_name["NewChatCopilot"]["import_path"] == f"{module}/ai"
    assert by_name["NewChatCopilot"]["package"] == "ai"
    assert by_name["NewDocument"]["import_path"] == module
    assert by_name["NewDocument"]["package"] == "asposepdf"


def test_every_entry_import_path_is_the_module_path_plus_its_directory() -> None:
    module = _pilots()["pdf_go"]["library"]["modulePath"]
    assert module == PDF_GO_MODULE
    for entry in _entries("pdf_go"):
        directory = os.path.dirname(entry["file"])
        expected = f"{module}/{directory}" if directory else module
        assert entry["import_path"] == expected, entry["name"]


def test_the_package_constants_and_variables_no_type_owns_are_indexed() -> None:
    entries = _entries("pdf_go")
    constants = {e["name"] for e in entries if e["kind"] == "constant"}
    variables = {e["name"] for e in entries if e["kind"] == "variable"}
    assert constants == PDF_GO_UNOWNED_CONSTANTS  # RD3-026: 26 in all
    assert variables == PDF_GO_UNOWNED_VARIABLES
    assert len(constants) + len(variables) == 26
    by_name = _by_name("pdf_go")
    assert by_name["PageFormatA4"]["value"] == "PageFormat{Width: 595, Height: 842}"
    assert by_name["FontHelvetica"]["type"] == "Font"
    assert by_name["DefaultDPI"]["value"] == "150"
    assert by_name["RoleSystem"]["package"] == "ai"
    assert by_name["RoleSystem"]["value"] == '"system"'


def test_pdf_go_has_every_exported_method_go_types_reports_and_no_other() -> None:
    entries = _entries("pdf_go")
    assert (
        sum(len(e["methods"]) for e in entries) == 1202
    )  # go/types: the method sets of the 259 exported types
    assert {m["name"] for m in _by_name("pdf_go")["BarcodeField"]["methods"]} == BARCODE_FIELD_METHODS


def test_promoted_methods_name_the_type_that_declares_them() -> None:
    methods = {m["name"]: m for m in _by_name("pdf_go")["BarcodeField"]["methods"]}
    assert {n for n, m in methods.items() if "promoted_from" not in m} == {
        "Symbology",
        "SetSymbology",
        "SetValue",
    }
    assert methods["Value"]["promoted_from"] == "TextBoxField"
    assert methods["PartialName"]["promoted_from"] == "fieldBase"
    assert methods["Rect"]["promoted_from"] == "fieldBase"


def test_pdf_go_has_no_phantom_function_of_an_unexported_type() -> None:
    functions = [e for e in _entries("pdf_go") if e["kind"] == "function"]
    assert (
        len(functions) == 98
    )  # go/doc: 7 functions of the root package, the constructors of its types and of ai
    assert not [e for e in functions if e["name"] in ("Close", "ReadAt")]
    assert not [e for e in functions if e.get("receiver_type")]


def test_a_function_typed_declaration_has_its_signature() -> None:
    entry = _by_name("pdf_go")["SVGFontResolver"]  # svg.go:95
    assert entry["underlying"] == "func(family string, bold, italic bool) Font"


# -- cells/go ---------------------------------------------------------------------------------------------------------

CELLS_GO_MODULE = "github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26"
CELLS_GO_CONSTANTS = {
    "AlignBottom",
    "AlignCenter",
    "AlignLeft",
    "AlignMiddle",
    "AlignRight",
    "AlignTop",
    "DataValidationTypeCustom",
    "DataValidationTypeDate",
    "DataValidationTypeDecimal",
    "DataValidationTypeList",
    "DataValidationTypeNone",
    "DataValidationTypeTextLength",
    "DataValidationTypeTime",
    "DataValidationTypeWhole",
    "ErrorStyleInformation",
    "ErrorStyleStop",
    "ErrorStyleWarning",
    "FillTypeGray125",
    "FillTypeNone",
    "FillTypeSolid",
}
# go/doc: the 6 package functions and the 9 constructors of the 14 exported types of aspose/cells_foss (`package cells_foss`)
CELLS_GO_FUNCTIONS = {
    "CalculateFormula",
    "CellToString",
    "DefaultStyle",
    "Load",
    "LoadWithPassword",
    "LoadWorkbook",
    "MinimalPNG",
    "NewPicture",
    "NewStreamingReader",
    "NewStyle",
    "NewTable",
    "NewWorkbook",
    "ReadTestZipEntry",
    "SaveWorkbook",
    "WriteTestXLSX",
}


def test_cells_go_serves_the_twenty_untyped_constants() -> None:
    constants = {e["name"]: e for e in _entries("cells_go") if e["kind"] == "constant"}
    assert set(constants) == CELLS_GO_CONSTANTS  # RD3-026
    assert constants["AlignCenter"]["value"] == '"center"'
    assert constants["AlignMiddle"]["value"] == '"center"'  # style.go: two names, one value


def test_cells_go_symbols_are_called_through_cells_foss() -> None:
    for entry in _entries("cells_go"):
        assert entry["package"] == "cells_foss", entry["name"]
        assert entry["import_path"] == f"{CELLS_GO_MODULE}/aspose/cells_foss", entry["name"]
    assert _pilots()["cells_go"]["library"]["modulePath"] == CELLS_GO_MODULE


def test_cells_go_tagged_fields_show_their_real_go_type() -> None:
    # cell.go:14-18 -- XMLName xml.Name `xml:"c"`, Ref string `xml:"r,attr"`, StyleID int `xml:"s,attr,omitempty"`,
    # Value interface{} `xml:"v,omitempty"`, Formula string `xml:"f,omitempty"`
    fields = {p["name"]: p["type"] for p in _by_name("cells_go")["Cell"]["properties"]}
    assert fields == {
        "XMLName": "xml.Name",
        "Ref": "string",
        "StyleID": "int",
        "Value": "interface{}",
        "Formula": "string",
    }
    for entry in _entries("cells_go"):
        for prop in entry["properties"]:
            assert "`" not in prop["type"], (entry["name"], prop["name"])


def test_row_callback_has_a_signature() -> None:
    entry = _by_name("cells_go")["RowCallback"]  # streaming_reader.go:16
    assert entry["underlying"] == "func(rowIndex int, cells map[string]string) error"


def test_cells_go_functions_are_exactly_the_exported_functions_and_constructors() -> None:
    functions = {e["name"] for e in _entries("cells_go") if e["kind"] == "function"}
    assert functions == CELLS_GO_FUNCTIONS  # the old fixture held 2 more: methods of unexported types
    assert sum(len(e["methods"]) for e in _entries("cells_go")) == 23  # go/types


# -- cells/rust -------------------------------------------------------------------------------------------------------

# lib.rs at the pin: the `pub use` statements of the crate root, the whole importable surface of the crate
UPSTREAM_LIB_RS_PUB_USE = """
pub use api::{
    AutoFilter, AutoFilterColorFilter, AutoFilterCustomFilter, AutoFilterCustomFilterCollection,
    AutoFilterDynamicFilter, AutoFilterSortCondition, AutoFilterSortConditionCollection,
    AutoFilterSortState, AutoFilterTop10, AutoShapeType, Border, BorderLineStyle, BorderStyleType,
    Borders, CalculationProperties, Cell, CellArea, CellMut, CellObjectValue, CellStyle, CellValue,
    CellValueType, Cells, CellsError, CellsException, CellsMut, Chart, ChartCollection, ChartType,
    Color, Column, ColumnCollection, ColumnCollectionMut, ColumnMut, ColumnProperties, Columns,
    ColumnsMut, Comment, CommentCollection, ConditionalFormattingCollection,
    CoreDocumentProperties, DateSystem, DefinedName, DefinedNameCollection, DiagnosticSeverity,
    DocumentProperties, ExtendedDocumentProperties, Fill, FillPattern, FilterColumn,
    FilterColumnCollection, FilterOperatorType, FilterValueCollection, Font, FontUnderlineType,
    FormatCondition, FormatConditionCollection, FormatConditionType, FormulaException, FreezePane,
    HorizontalAlignment, Hyperlink, HyperlinkCollection, IWarningCallback, ImageType,
    InvalidFileFormatException, ListColumn, ListColumnCollection, ListObject, ListObjectCollection,
    LoadDiagnostics, LoadFormat, LoadIssue, LoadOptions, NumberFormat, OperatorType,
    PageOrientationType, PageSetup, PaperSizeType, Picture, PictureCollection, Row, RowCollection,
    RowCollectionMut, RowMut, RowProperties, Rows, RowsMut, SaveFormat, SaveOptions, Shape,
    ShapeCollection, SparklineGroup, SparklineGroupCollection, SparklineType, Style,
    StyleException, StyleFlag, TableStyleType, TargetModeType, TotalsCalculation,
    UnsupportedFeatureException, Validation, ValidationAlertType, ValidationCollection,
    ValidationType, VerticalAlignment, VisibilityType, WarningInfo, Workbook,
    WorkbookLoadException, WorkbookProperties, WorkbookProtection, WorkbookSaveException,
    WorkbookSettings, WorkbookView, Worksheet, WorksheetCollection, WorksheetCollectionMut,
    WorksheetProtection, Worksheets, WorksheetsMut,
};

pub use api::{
    HorizontalAlignment as HorizontalAlignmentType, VerticalAlignment as VerticalAlignmentType,
};
"""


def _lib_rs_exported_names() -> set[str]:
    """The declared names `pub use api::{...}` lists (the part before ` as `), counted on the upstream text."""
    names: set[str] = set()
    for group in re.findall(r"pub use api::\{(.*?)\};", UPSTREAM_LIB_RS_PUB_USE, re.DOTALL):
        for item in group.split(","):
            item = item.strip()
            if item:
                names.add(item.split(" as ")[0].strip())
    return names


def test_the_oracle_counts_124_listed_items_and_122_distinct_declared_names() -> None:
    items = [
        item.strip()
        for group in re.findall(r"pub use api::\{(.*?)\};", UPSTREAM_LIB_RS_PUB_USE, re.DOTALL)
        for item in group.split(",")
        if item.strip()
    ]
    assert len(items) == 124
    assert len(_lib_rs_exported_names()) == 122


def test_cells_rust_serves_exactly_what_the_crate_root_lets_other_crates_import() -> None:
    served = {e["name"] for e in _entries("cells_rust")}
    assert served == _lib_rs_exported_names()  # RD3-030: 221 -> 122
    assert len(_entries("cells_rust")) == 122


def test_a_private_model_the_readme_names_is_not_served() -> None:
    # the README lists WorkbookModel (Core/WorkbookModel.rs); no `mod` declares that file, so it is not importable
    served = {e["name"] for e in _entries("cells_rust")}
    assert "WorkbookModel" not in served
    assert "Workbook" in served
    assert not [
        e for e in _entries("cells_rust") if e["kind"] == "function"
    ]  # no free function is importable


def test_cells_rust_methods_render_their_receivers() -> None:
    workbook = {m["name"]: m for m in _by_name("cells_rust")["Workbook"]["methods"]}
    assert workbook["add_worksheet"]["receiver"] == "&mut self"  # Workbook.rs:216
    assert workbook["worksheet"]["receiver"] == "&self"  # Workbook.rs:236
    assert "receiver" not in workbook["new"]
    assert workbook["new"]["modifiers"] == ["static"]
    receivers = [
        m.get("receiver")
        for e in _entries("cells_rust")
        for m in e["methods"]
        if "static" not in m.get("modifiers", [])
    ]
    assert receivers and all(receivers)  # a method that is not an associated function always has its receiver
    assert {"&self", "&mut self"} <= set(receivers)


def test_cells_rust_generic_bounds_are_recorded() -> None:
    methods = {m["name"]: m for m in _by_name("cells_rust")["Workbook"]["methods"]}
    assert methods["save_xlsx_to_stream"]["generics"] == "<W: Write + Seek>"  # Workbook.rs:565
    assert methods["load_xlsx_from_stream"]["generics"] == "<R: Read + Seek>"  # Workbook.rs:429


def test_cells_rust_enum_payloads_are_kept() -> None:
    def payloads(name: str) -> dict[str, str]:
        return {m["name"]: m.get("payload", "") for m in _by_name("cells_rust")[name]["enum_members"]}

    assert payloads("CellValue") == {  # api.rs `pub enum CellValue`
        "String": "(String)",
        "Number": "(f64)",
        "Bool": "(bool)",
        "DateTime": "(DateTime<Utc>)",
        "Error": "(String)",
        "Formula": "(String)",
        "Empty": "",
    }
    assert payloads("CellsError") == {  # api.rs `pub enum CellsError`
        "SheetNotFound": "(String)",
        "WorksheetIndexOutOfRange": "(usize)",
        "InvalidSheetName": "",
        "InvalidZoom": "(i32)",
        "InvalidCellReference": "(String)",
        "InvalidInput": "(String)",
        "IndexOutOfRange": "(String)",
        "Io": "(String)",
        "OverlappingMergedRange": "",
        "Unsupported": "(&'static str)",
    }
    assert payloads("CellObjectValue") == {  # api.rs `pub enum CellObjectValue`
        "String": "(String)",
        "Number": "(f64)",
        "Bool": "(bool)",
        "DateTime": "(DateTime<Utc>)",
        "Empty": "",
    }


def test_cells_rust_type_aliases_are_indexed_with_their_targets() -> None:
    aliases = {e["name"]: e["alias_of"] for e in _entries("cells_rust") if e["kind"] == "type_item"}
    assert (
        aliases
        == {  # the seven `pub type` of the crate (Style.rs:437, WorksheetCollection.rs:8,15, RowCollection.rs:8,41, ColumnCollection.rs:8,41)
            "Style": "CellStyle",
            "WorksheetCollection": "Worksheets<'a>",
            "WorksheetCollectionMut": "WorksheetsMut<'a>",
            "RowCollection": "Rows<'a>",
            "RowCollectionMut": "RowsMut<'a>",
            "ColumnCollection": "Columns<'a>",
            "ColumnCollectionMut": "ColumnsMut<'a>",
        }
    )


def test_derive_traits_are_not_bases() -> None:
    for entry in _entries("cells_rust"):
        for derived in entry.get("derives", []):
            assert derived not in entry["bases"], (entry["name"], derived)
    error = _by_name("cells_rust")["CellsError"]
    assert error["derives"] == ["Debug", "Clone", "PartialEq"]
    assert error["bases"] == ["Display"]  # api.rs `impl Display for CellsError`
