---
layout: plugin
family_name: Aspose.Cells FOSS
plugin_description: Aspose.Cells FOSS for TypeScript is a free, open-source TypeScript library for creating, loading, editing, and saving Excel .xlsx (Office Open XML / SpreadsheetML) workbooks without requiring Microsoft Excel. It exposes an Aspose.Cells-compatible API surface — Workbook, Worksheet, Cell, Style — and additionally reads and writes HTML, and exports to CSV, JSON, and Markdown for reporting and data-interchange scenarios.
plugin_platform: TypeScript
head_title: Aspose.Cells FOSS for TypeScript | Open-Source Library
head_description: Aspose.Cells FOSS for TypeScript is a free, open-source TypeScript library for creating, loading, editing, and saving Excel .xlsx (Office Open XML /...
title: Aspose.Cells FOSS for TypeScript
description: Aspose.Cells FOSS for TypeScript is a free, open-source TypeScript library for creating, loading, editing, and saving Excel .xlsx (Office Open XML / SpreadsheetML) workbooks without requiring Microsoft Excel. It exposes an Aspose.Cells-compatible API surface — Workbook, Worksheet, Cell, Style — and additionally reads and writes HTML, and exports to CSV, JSON, and Markdown for reporting and data-interchange scenarios.
submenu:
  enable: true
github_url: https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript
doc_source: github_readme
doc_source_commit: fc186507e5b7124f4664aa6035f25cfd3112367d
overview:
  enable: true
  title: Aspose.Cells FOSS for TypeScript
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction TB
        i1["An existing .xlsx workbook"]
        i2["An existing HTML document"]
      end
      PRODUCT["Aspose.Cells FOSS for TypeScript"]
      subgraph Capabilities["Core Capabilities"]
        direction LR
        subgraph capl[" "]
          direction TB
          c1["Cell values, formulas, and shared strings"]
          c2["Style formatting (font, fill, border, alignment, number formats)"]
          c3["Worksheet settings (column widths, row heights, merges, auto filter)"]
          c4["Data validation and conditional formatting"]
        end
        subgraph capr[" "]
          direction TB
          c5["Hyperlinks and defined names"]
          c6["Embedded pictures and shapes"]
          c7["HTML round-trip import and export"]
        end
      end
      subgraph Outputs["Outputs"]
        direction TB
        o1["A workbook document"]
        o2["An HTML document"]
        o3["Plain text files"]
      end
      StartingPoints --> PRODUCT --> Capabilities --> Outputs
    ```
content:
  enable: true
  block:
  - title_left: Installation, Dependencies, and Quick Start
    content_left: |-
      ### Installation

      A npm package has not been published yet. Install from source:

      ```bash
      git clone https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript.git
      cd Aspose.Cells-FOSS-for-TypeScript
      npm install
      ```

      ### Required Package Dependencies

      - `@zip.js/zip.js` ^2.8.21 — reads and writes the XLSX ZIP container.
      - `@xmldom/xmldom` ^0.8.10 — parses and serializes the OOXML part XML.
      - `adm-zip` ^0.5.16 — used alongside `@zip.js/zip.js` for ZIP entry handling.

      ### Native and System Requirements

      - Requires Node.js 18 or later and TypeScript 5 or later.

      ### Development Dependencies

      - `@types/node` ^25.3.3, `typescript` ^5.9.3 — TypeScript compilation and type checking.
      - `ts-node` ^10.9.2, `tsx` ^4.21.0 — running TypeScript examples/tests directly.

      ### Quick Start

      Create a workbook, write typed values and a formula, style a header row, and save it as
      XLSX:

      ```typescript
      import { Workbook, Style } from "./aspose_cells";

      const workbook = new Workbook();
      const sheet = workbook.worksheets.get(0)!;

      sheet.name = "Products";
      sheet.putValue("A1", "Product");
      sheet.putValue("B1", "Price");
      sheet.putValue("A2", "Apple");
      sheet.putValue("B2", 2.99);
      sheet.putValue("A3", "Orange");
      sheet.putValue("B3", 1.99);
      const cellB4 = sheet.getCell2("B4");
      cellB4.setFormula("=SUM(B2:B3)");

      const headerStyle = new Style();
      headerStyle.setBold(true);
      headerStyle.setFontColor("FFFFFFFF");
      headerStyle.setForegroundColor("FF2278D4");

      sheet.getCell2("A1").setStyle(headerStyle);
      sheet.getCell2("B1").setStyle(headerStyle);

      await workbook.save("products.xlsx");
      ```

      Load an existing workbook, update a cell, and save it back:

      ```typescript
      import { Workbook } from "./aspose_cells";

      const workbook = await Workbook.load("input.xlsx");
      const sheet = workbook.worksheets.get(0)!;

      sheet.getCell2("A1").putValue("Updated");

      await workbook.save("updated.xlsx");
      ```
    title_right: Key Capabilities and API Reference
    content_right: |-
      ### Key Capabilities

      - Load and save `.xlsx` workbooks — `Workbook.load(filePath)` reads an existing file
        (auto-detecting HTML too), and `Workbook.save(filePath, options)` writes it back out.
      - Read and write cell values, formulas, and shared strings via `Cell.putValue()`/
        `Cell.setFormula()`, with `Worksheet.getCell2()`/`getCell()` lookups by reference or
        coordinate.
      - Apply full style formatting — font, fill, border, alignment, and number formats — through
        `Style`, `Font`, `Fill`, `Border`, and `Alignment`, set per cell via `Cell.setStyle()`.
      - Configure worksheet-level settings: column widths, row heights, merged cells, auto filter,
        and hidden rows, all through `Worksheet` methods.
      - Add hyperlinks and defined names, and apply data validation and conditional formatting
        rules to a range.
      - Access embedded pictures and shapes through `Workbook.images` (`ImageInfo[]`) and
        `Worksheet.shapes` (`ShapeInfo[]`).
      - Import and export HTML — `Workbook.load()` recognizes `.html`/`.htm` files and converts
        them into a workbook object, and `toHtml()` renders a workbook to a styled HTML document.
      - Export a workbook as text — `toCsv()`, `toJson()`, and `toMarkdown()` each return the
        workbook's data as a string, ready to write to a file or hand to another process directly.

      ### API Reference

      The library ships a real class surface centered on `Workbook`, `Worksheet`, `Cell`, and
      `Style` — `Workbook.worksheets` is a `WorksheetCollection` of `Worksheet` objects, each
      exposing a `Cells` collection for reading and writing values, formulas, and styles.

      <details>
      <summary>View the Full API Surface</summary>

      ### Core API

      | Class | Description |
      | `AllConnectorShapeInfo` | Class in the Cells TYPESCRIPT API. |
      | `AllShapeInfo` | Class in the Cells TYPESCRIPT API. |
      | `ArrowShapeType` | Class in the Cells TYPESCRIPT API. |
      | `AutoFilter` | Class with 5 methods and 2 properties. |
      | `BasicShapeInfoType` | Class with 1 property. |
      | `BasicShapeSubType` | Class in the Cells TYPESCRIPT API. |
      | `BentConnectorShapeType` | Class in the Cells TYPESCRIPT API. |
      | `BraceShapeType` | Class in the Cells TYPESCRIPT API. |
      | `Cell` | A single cell — value, formula, style, and hyperlink. |
      | `CellValue` | Class in the Cells TYPESCRIPT API. |
      | `CellValue-util` | Class in the Cells TYPESCRIPT API. |
      | `Cells` | Per-sheet collection for reading and writing cell values, formulas, and styles. |
      | `Chart` | Class with 7 methods and 33 properties. |
      | `ChartCollection` | Class with 11 methods and 2 properties. |
      | `ChartLoader` | Class with 1 method and 7 properties. |
      | `ChartRenderer` | Class with 1 method. |
      | `ChartType` | Class in the Cells TYPESCRIPT API. |
      | `CircleVariantShapeType` | Class in the Cells TYPESCRIPT API. |
      | `Comment` | Class with 2 methods and 7 properties. |
      | `CommentCollection` | Class with 6 methods and 1 property. |
      | `ConditionalFormat` | Class with 5 methods. |
      | `ConditionalFormatCollection` | Class with 5 methods and 1 property. |
      | `CurvedConnectorShapeType` | Class in the Cells TYPESCRIPT API. |
      | `DOMParser` | Class with 2 methods. |
      | `DataRelationship` | Class with 3 methods and 1 property. |
      | `DataValidation-validation` | Class with 3 methods and 12 properties. |
      | `DataValidationCollection` | Class with 5 methods and 1 property. |
      | `EllipseShapeType` | Class in the Cells TYPESCRIPT API. |
      | `ExpDrawing` | Class with 9 methods. |
      | `HtmlDocument` | Loads and represents an HTML document for conversion into a `Workbook`. |
      | `HtmlExporter` | Renders a `Workbook` to a styled HTML document. |
      | `HtmlReader` | Reads and parses raw HTML markup into a `HtmlDocument`. |
      | `HtmlTable` | Class with 10 methods and 14 properties. |
      | `HtmlWriter` | Serializes a `HtmlDocument` back to HTML markup. |
      | `Hyperlink` | Class with 2 methods and 8 properties. |
      | `HyperlinkCollection` | Class with 6 methods and 1 property. |
      | `ImpDrawing` | Class with 1 method and 5 properties. |
      | `LineShapeType` | Class in the Cells TYPESCRIPT API. |
      | `MathSymbolShapeType` | Class in the Cells TYPESCRIPT API. |
      | `PolygonShapeType` | Class in the Cells TYPESCRIPT API. |
      | `QuadrilateralShapeType` | Class in the Cells TYPESCRIPT API. |
      | `RectangleShapeType` | Class in the Cells TYPESCRIPT API. |
      | `RectangleVariantShapeType` | Class in the Cells TYPESCRIPT API. |
      | `SealShapeType` | Class in the Cells TYPESCRIPT API. |
      | `Shape3DType` | Class in the Cells TYPESCRIPT API. |
      | `SpecialShapeType` | Class in the Cells TYPESCRIPT API. |
      | `StarShapeType` | Class in the Cells TYPESCRIPT API. |
      | `StraightConnectorShapeType` | Class in the Cells TYPESCRIPT API. |
      | `Style` | Font, fill, border, alignment, and number-format settings for a cell. |
      | `TriangleShapeType` | Class in the Cells TYPESCRIPT API. |
      | `Workbook` | The top-level container — `load()`/`save()`, `worksheets`, `images`, and `toHtml()`/`toCsv()`/`toJson()`/`toMarkdown()`. |
      | `Worksheet` | A single sheet — cell access via `Cells`, styling, images/shapes, and layout settings. |
      | `WorksheetCollection` | Iterable, array-like collection of `Worksheet` objects. |
      | `Alignment` | Interface with 3 properties. |
      | `ArrowShapeInfo` | Interface with 1 property. |
      | `AxisInfo` | Interface with 8 properties. |
      | `BentConnectorShapeInfo` | Interface with 3 properties. |
      | `Border` | Interface with 4 properties. |
      | `BorderLine` | Interface with 2 properties. |
      | `BraceShapeInfo` | Interface with 1 property. |
      | `CellCoordinates` | Interface with 2 properties. |
      | `CellCoordinates-util` | Interface with 2 properties. |
      | `CellRange` | Interface with 4 properties. |
      | `CellRange-util` | Interface with 4 properties. |
      | `CellStyle` | Interface with 23 properties. |
      | `ChartArea` | Interface with 4 properties. |
      | `ChartInfo` | Interface with 21 properties. |
      | `ChartSeries` | Interface with 3 properties. |
      | `CircleVariantShapeInfo` | Interface with 1 property. |
      | `ColorScaleRule` | Interface with 3 properties. |
      | `Comment-types` | Interface with 4 properties. |
      | `ConditionalFormatRule` | Interface with 8 properties. |
      | `CurvedConnectorShapeInfo` | Interface with 3 properties. |
      | `DataBarRule` | Interface with 4 properties. |
      | `DataValidation` | Interface with 12 properties. |
      | `EllipseShapeInfo` | Interface with 1 property. |
      | `Fill` | Interface with 3 properties. |
      | `FilterColumn` | Interface with 3 properties. |
      | `Font` | Interface with 7 properties. |
      | `HtmlParseOptions` | Interface with 2 properties. |
      | `HtmlSaveOptions` | Interface with 2 properties. |
      | `Hyperlink-types` | Interface with 5 properties. |
      | `IconSetRule` | Interface with 2 properties. |
      | `ImageInfo` | Interface with 5 properties. |
      | `LegendInfo` | Interface with 2 properties. |
      | `LineShapeInfo` | Interface with 3 properties. |
      | `MathSymbolShapeInfo` | Interface with 1 property. |
      | `PictureInfo` | Interface with 9 properties. |
      | `PictureShapeInfo` | Interface with 2 properties. |
      | `PlotArea` | Interface with 4 properties. |
      | `PolygonShapeInfo` | Interface with 1 property. |
      | `Protection` | Interface with 2 properties. |
      | `QuadrilateralShapeInfo` | Interface with 1 property. |
      | `RectangleShapeInfo` | Interface with 1 property. |
      | `RectangleVariantShapeInfo` | Interface with 1 property. |
      | `SealShapeInfo` | Interface with 1 property. |
      | `SeriesInfo` | Interface with 3 properties. |
      | `Shape3DInfo` | Interface with 1 property. |
      | `ShapeAnchor` | Interface with 10 properties. |
      | `ShapeFill` | Interface with 10 properties. |
      | `ShapeInfo` | Interface with 26 properties. |
      | `SpecialShapeInfo` | Interface with 1 property. |
      | `StarShapeInfo` | Interface with 1 property. |
      | `StraightConnectorShapeInfo` | Interface with 3 properties. |
      | `Style-types` | Interface with 6 properties. |
      | `TriangleShapeInfo` | Interface with 1 property. |
      | `EncryptionType` | Enum with 1 member. |
      | `SaveFormat` | Enum with 5 members. |

      </details>
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.Cells FOSS for TypeScript, straight from its README at the pinned commit:

      - Text export is available as a returned string, not as a direct file-write helper — pass
        the result of `toCsv()`, `toJson()`, or `toMarkdown()` to your own file-writing code.
      - Text export is one-way — there is no corresponding import path back into a `Workbook`.

      This limitation does not apply to
      [Aspose.Cells for .NET — Enterprise Edition](https://products.aspose.com/cells/), which adds
      a full commercial spreadsheet engine, broader format coverage, and active support beyond this
      FOSS TypeScript edition's own scope.
single:
  enable: true
  block:
  - title: Quick Start
    content: |
      Create a workbook, write typed values and a formula, style a header row, and save it as
      XLSX:

      ```typescript
      import { Workbook, Style } from "./aspose_cells";

      const workbook = new Workbook();
      const sheet = workbook.worksheets.get(0)!;

      sheet.name = "Products";
      sheet.putValue("A1", "Product");
      sheet.putValue("B1", "Price");
      sheet.putValue("A2", "Apple");
      sheet.putValue("B2", 2.99);
      sheet.putValue("A3", "Orange");
      sheet.putValue("B3", 1.99);
      const cellB4 = sheet.getCell2("B4");
      cellB4.setFormula("=SUM(B2:B3)");

      const headerStyle = new Style();
      headerStyle.setBold(true);
      headerStyle.setFontColor("FFFFFFFF");
      headerStyle.setForegroundColor("FF2278D4");

      sheet.getCell2("A1").setStyle(headerStyle);
      sheet.getCell2("B1").setStyle(headerStyle);

      await workbook.save("products.xlsx");
      ```
  - title: Quick Start (2)
    content: |
      Load an existing workbook, update a cell, and save it back:

      ```typescript
      import { Workbook } from "./aspose_cells";

      const workbook = await Workbook.load("input.xlsx");
      const sheet = workbook.worksheets.get(0)!;

      sheet.getCell2("A1").putValue("Updated");

      await workbook.save("updated.xlsx");
      ```
  - title: Export a Workbook to HTML
    content: |
      ```typescript
      import { Workbook } from "./aspose_cells";

      const workbook = await Workbook.load("input.xlsx");
      await workbook.save("output.html", { saveFormat: undefined });
      ```
faq:
  enable: false
  list: []
---
