---
layout: plugin
family_name: Aspose.Cells FOSS
plugin_description: Aspose.Cells FOSS for Go is a pure-Go library for creating, reading, and writing Excel workbooks — ECMA-376 Office Open XML .xlsx spreadsheets — with no third-party dependency. It covers cell data and A1-style addressing, visual formatting, a built-in formula engine, data validation, structured tables, embedded pictures, CSV import and export, low-memory row streaming for large files, and password-based workbook encryption.
plugin_platform: Go
head_title: Aspose.Cells FOSS for Go | Open-Source Library
head_description: Aspose.Cells FOSS for Go is a pure-Go library for creating, reading, and writing Excel workbooks — ECMA-376 Office Open XML .xlsx spreadsheets — with no...
title: Aspose.Cells FOSS for Go
description: Aspose.Cells FOSS for Go is a pure-Go library for creating, reading, and writing Excel workbooks — ECMA-376 Office Open XML .xlsx spreadsheets — with no third-party dependency. It covers cell data and A1-style addressing, visual formatting, a built-in formula engine, data validation, structured tables, embedded pictures, CSV import and export, low-memory row streaming for large files, and password-based workbook encryption.
submenu:
  enable: true
github_url: https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go
doc_source: github_readme
doc_source_commit: fa4e890e46c509efd22d09b97411202c2b0b8a67
overview:
  enable: true
  title: Aspose.Cells FOSS for Go
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction LR
        i1["An existing XLSX workbook (including password-protected)"]
        i2["CSV files"]
      end
      PRODUCT["Aspose.Cells FOSS for Go"]
      subgraph Capabilities["Core Capabilities"]
        direction LR
        subgraph capl[" "]
          direction TB
          c1["Cell read and write (A1-style references)"]
          c2["Font, fill, alignment, and border styling"]
          c3["Formulas with a built-in SUM/AVERAGE/MAX/MIN engine"]
          c4["Data validation, tables, and picture embedding"]
        end
        subgraph capr[" "]
          direction TB
          c5["CSV import and export"]
          c6["Streaming row-by-row reading"]
          c7["Password protection and encryption"]
        end
      end
      subgraph Outputs["Outputs"]
        direction TB
        o1["XLSX workbooks (including password-protected)"]
        o2["CSV files"]
      end
      StartingPoints --> PRODUCT --> Capabilities --> Outputs
    ```
content:
  enable: true
  block:
  - title_left: Installation, Dependencies, and Quick Start
    content_left: "### Installation\n\n```bash\ngo get github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26\n```\n\nThe module supports Go 1.24.5 or later and has zero third-party dependencies — encryption and\neverything else builds on the Go standard library alone. Because the package name (`cells_foss`)\ndiffers from the module path's final segment (`v26`), import it with an explicit alias:\n\n```go\nimport cells_foss \"github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26/aspose/cells_foss\"\n```\n\n### Required Package Dependencies\n\nNo required third-party package dependencies.\n\n### Native and System Requirements\n\n- Go 1.24 or later (`go.mod`'s own `go 1.24.5` directive).\n\n### Quick Start\n\nCreate a workbook, write a couple of cells, and save it to disk:\n\n```go\npackage main\n\nimport cells_foss \"github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26/aspose/cells_foss\"\n\nfunc main() {\n\t// Create.\n\twb := cells_foss.NewWorkbook()\n\tws := wb.Worksheets[0]\n\n\t// Write.\n\tws.Cells().Set(\"A1\", \"Hello, World!\")\n\tws.Cells().Set(\"B1\", 42)\n\n\t// Save.\n\twb.Save(\"hello.xlsx\")\n}\n```\n\nRun `go run main.go` to produce `hello.xlsx`.\n\nLoad an existing workbook, read and update a cell, then save the result:\n\n```go\nwb, _ := cells_foss.LoadWorkbook(\"input.xlsx\")\nws := wb.Worksheets[0]\n\ncell, _ := ws.Cells().Get(\"A1\")\nfmt.Println(\"Current value:\", cell.Value)\n\nws.Cells().Set(\"A1\", \"Updated value\")\nwb.Save(\"output.xlsx\")\n```"
    title_right: Key Capabilities and API Reference
    content_right: |-
      ### Key Capabilities

      - Cell values are read and written through `Cells.Get`/`Cells.Set`, addressed by A1-style
        references (`"A1"`, `"B2"`) and covering `string`, `float64`, `int`, and `bool` values.
      - Font, fill, alignment, and borders combine into a `Style` applied to any cell via
        `Cell.SetStyle`, with automatic deduplication of identical styles during save.
      - Formulas are stored with `Cell.SetFormula` and evaluated by the built-in `CalculateFormula`
        engine, which supports `SUM`, `AVERAGE`, `MAX`, and `MIN` over single cells or A1-style ranges.
      - Data validation rules (`DataValidation`) restrict a range to dropdown lists or whole/decimal
        ranges with custom error messages and error styles; structured `Table` ranges add header rows
        and auto-filters; `Picture` objects (PNG or JPEG) embed images. All three attach to a
        `Worksheet` via `AddDataValidation`, `AddTable`, and `AddPicture`.
      - `Workbook.ExportToCSV` and `Workbook.ImportFromCSV` move data to and from CSV with a
        caller-supplied delimiter; export converts typed cell values to their CSV string form via
        `CellToString`, while import stores every field as a plain `string`.
      - The streaming reader (`StreamingReader.ProcessRows`) handles very large files with row-by-row
        processing, reading directly from the source path without first building the shared
        `Workbook`/`Worksheet` model, so peak memory stays proportional to the width of one row
        rather than the whole file.
      - `Workbook.SetPassword` and `Workbook.VerifyPassword` add ECMA-376 Agile Encryption (SHA-512
        key derivation, AES-256-CBC) to the save path, producing a password-protected XLSX file.

      ### API Reference

      The library's public surface is centered on `Workbook` and `Worksheet` — created with
      `NewWorkbook`/`LoadWorkbook` and exposing a `*Cells` collection through `Worksheet.Cells()` — with
      supporting types (`Style`, `DataValidation`, `Table`, `Picture`, `StreamingReader`) for styling,
      validation, structured ranges, images, and low-memory row access.

      <details>
      <summary>View the Supported Public API Surface</summary>

      ### Cells Foss

      | Class | Description |
      | `Alignment` | Alignment controls how cell content is positioned within the cell bounds. |
      | `Border` | Border defines which sides of a cell have a visible rule and the colour of those rules. |
      | `Cell` | Cell represents a single cell in a worksheet grid. |
      | `Cells` | Cells is a collection of Cell values indexed by A1-style string references (e.g. "A1", "B2", "Z100"). |
      | `DataValidation` | DataValidation represents a single data-validation rule applied to a range of cells on a worksheet. |
      | `Fill` | Fill describes the background appearance of a cell. |
      | `Font` | Font describes the typographic properties applied to cell text. |
      | `Picture` | Picture represents an image embedded in a worksheet. |
      | `RowCallback` | RowCallback is invoked by StreamingReader.ProcessRows once for every row in the worksheet. |
      | `StreamingReader` | StreamingReader reads an .xlsx workbook row by row without loading the entire sheet XML into memory. |
      | `Style` | Style groups font, fill, alignment, and border settings into a named formatting record. |
      | `Table` | Table represents a structured range of data (a "table" in Excel terminology) with optional header row and built-in auto-filter. |
      | `Workbook` | Workbook is the top-level object representing an Excel workbook. |
      | `Worksheet` | Worksheet represents a single sheet within a workbook. |

      </details>
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.Cells FOSS for Go, straight from its README at the pinned commit:

      - Cell references are A1-style strings only (`"A1"`, `"B2"`); tuple/array indices (e.g. `[0, 0]`)
        are not supported.
      - `CalculateFormula` supports only `SUM`, `AVERAGE`, `MAX`, and `MIN`; any other formula returns
        an error rather than being evaluated.
      - CSV import (`ImportFromCSV`/`FromCSV`) stores every field as a Go `string` — it does not infer
        numeric or boolean types the way `Cells().Set` does when called directly with typed values.
      - On save, unmodified content is reused verbatim from the original XML byte-for-byte, and only
        content touched since load is regenerated — round-trip fidelity depends on this split, not a
        full re-serialization of every part.
      - Generated XML follows ECMA-376's own element ordering; this preserves round-trip compatibility
        but isn't independently configurable.

      These limitations don't apply to
      [Aspose.Cells for Go Enterprise Edition](https://products.aspose.com/cells/go-cpp/), which adds
      the complete Excel formula function library, additional file-format coverage, and commercial
      support.
single:
  enable: true
  block:
  - title: Installation
    content: |
      The module supports Go 1.24.5 or later and has zero third-party dependencies — encryption and
      everything else builds on the Go standard library alone. Because the package name (`cells_foss`)
      differs from the module path's final segment (`v26`), import it with an explicit alias:

      ```go
      import cells_foss "github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26/aspose/cells_foss"
      ```
  - title: Quick Start
    content: "Create a workbook, write a couple of cells, and save it to disk:\n\n```go\npackage main\n\nimport cells_foss \"github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go/v26/aspose/cells_foss\"\n\nfunc main() {\n\t// Create.\n\twb := cells_foss.NewWorkbook()\n\tws := wb.Worksheets[0]\n\n\t// Write.\n\tws.Cells().Set(\"A1\", \"Hello, World!\")\n\tws.Cells().Set(\"B1\", 42)\n\n\t// Save.\n\twb.Save(\"hello.xlsx\")\n}\n```\n"
  - title: Quick Start (2)
    content: |
      Load an existing workbook, read and update a cell, then save the result:

      ```go
      wb, _ := cells_foss.LoadWorkbook("input.xlsx")
      ws := wb.Worksheets[0]

      cell, _ := ws.Cells().Get("A1")
      fmt.Println("Current value:", cell.Value)

      ws.Cells().Set("A1", "Updated value")
      wb.Save("output.xlsx")
      ```
  - title: Formulas and the Calculation Engine
    content: "```go\nfunc main() {\n\twb := cells_foss.NewWorkbook()\n\tws := wb.Worksheets[0]\n\n\t// \x2D\x2D-- Populate sales data \x2D\x2D--\n\tdata := []float64{1200, 850, 1400, 960, 1780}\n\theaders := []string{\"Month\", \"Sales\"}\n\n\tws.Cells().Set(\"A1\", headers[0])\n\tws.Cells().Set(\"B1\", headers[1])\n\n\tmonths := []string{\"Jan\", \"Feb\", \"Mar\", \"Apr\", \"May\"}\n\tfor i, m := range months {\n\t\trow := i + 2\n\t\tws.Cells().Set(fmt.Sprintf(\"A%d\", row), m)\n\t\tws.Cells().Set(fmt.Sprintf(\"B%d\", row), data[i])\n\t}\n\n\t// \x2D\x2D-- Add formula cells \x2D\x2D--\n\tlastDataRow := len(data) + 1\n\ttotalRef := fmt.Sprintf(\"B2:B%d\", lastDataRow)\n\n\t// SUM formula.\n\tws.Cells().Set(\"B7\", nil)\n\tsumCell, _ := ws.Cells().Get(\"B7\")\n\tsumCell.SetFormula(fmt.Sprintf(\"SUM(%s)\", totalRef))\n\n\t// AVERAGE formula.\n\tws.Cells().Set(\"B8\", nil)\n\tavgCell, _ := ws.Cells().Get(\"B8\")\n\tavgCell.SetFormula(fmt.Sprintf(\"AVERAGE(%s)\", totalRef))\n\n\t// Labels.\n\tws.Cells().Set(\"A7\", \"TOTAL\")\n\tws.Cells().Set(\"A8\", \"AVERAGE\")\n\n\t// \x2D\x2D-- Evaluate formulas with the engine \x2D\x2D--\n\tfor _, row := range []int{7, 8} {\n\t\tcell, _ := ws.Cells().Get(fmt.Sprintf(\"B%d\", row))\n\t\tformula := cell.GetFormula()\n\t\tresult, err := cells_foss.CalculateFormula(formula, ws)\n\t\tif err != nil {\n\t\t\tfmt.Printf(\"  %s = ERROR: %v\\n\", formula, err)\n\t\t} else {\n\t\t\tfmt.Printf(\"  %s = %v\\n\", formula, result)\n\t\t}\n\t}\n\n\twb.Save(\"outputfiles/formula.xlsx\")\n}\n```\n"
  - title: Data Validation
    content: "```go\nfunc main() {\n\twb := cells_foss.NewWorkbook()\n\tws := wb.Worksheets[0]\n\n\tws.Cells().Set(\"A1\", \"Fruit\")\n\tws.Cells().Set(\"A2\", \"Apple\")\n\tws.Cells().Set(\"A3\", \"Banana\")\n\n\t// Create a list-type data validation.\n\tdv := &cells_foss.DataValidation{\n\t\tType:             cells_foss.DataValidationTypeList,\n\t\tFormula1:         `\"Apple,Banana,Cherry,Dragonfruit\"`,\n\t\tAllowBlank:       true,\n\t\tShowErrorMessage: true,\n\t\tErrorTitle:       \"Invalid Fruit\",\n\t\tErrorMessage:     \"Please pick a fruit from the list.\",\n\t\tErrorStyle:       cells_foss.ErrorStyleStop,\n\t}\n\n\tif err := ws.AddDataValidation(\"A2:A10\", dv); err != nil {\n\t\tfmt.Printf(\"Error: %v\\n\", err)\n\t\treturn\n\t}\n\n\twb.Save(\"outputfiles/data_validation.xlsx\")\n}\n```\n"
  - title: Picture Embedding
    content: "```go\nfunc main() {\n\twb := cells_foss.NewWorkbook()\n\tws := wb.Worksheets[0]\n\n\tws.Cells().Set(\"A1\", \"Product Catalog\")\n\n\t// generateSmallPNG returns a minimal PNG for the example; see\n\t// examples/picture/main.go for the full helper.\n\tpic := cells_foss.NewPicture(generateSmallPNG(), \"png\")\n\tpic.Width = 100\n\tpic.Height = 80\n\tpic.SetAnchor(5, 1) // row 5, column B\n\n\tif err := ws.AddPicture(pic); err != nil {\n\t\tfmt.Printf(\"Error adding picture: %v\\n\", err)\n\t\treturn\n\t}\n\n\twb.Save(\"outputfiles/picture.xlsx\")\n}\n```\n"
  - title: Streaming Large Files
    content: "```go\nfunc main() {\n\tsr := cells_foss.NewStreamingReader(\"outputfiles/streaming_data.xlsx\")\n\trowCount := 0\n\tvar totalScore float64\n\n\terr := sr.ProcessRows(\"Sheet1\", func(rowIdx int, cells map[string]string) error {\n\t\trowCount++\n\t\tif score, ok := cells[\"C\"+fmt.Sprint(rowIdx)]; ok {\n\t\t\tvar s float64\n\t\t\tfmt.Sscanf(score, \"%f\", &s)\n\t\t\ttotalScore += s\n\t\t}\n\t\treturn nil\n\t})\n\tif err != nil {\n\t\tfmt.Fprintf(os.Stderr, \"Error streaming: %v\\n\", err)\n\t\tos.Exit(1)\n\t}\n\tfmt.Printf(\"Streamed %d rows, total score %.0f\\n\", rowCount, totalScore)\n}\n```\n"
  - title: Styling
    content: "```go\nfunc main() {\n\twb := cells_foss.NewWorkbook()\n\tws := wb.Worksheets[0]\n\n\tboldStyle := cells_foss.NewStyle()\n\tboldStyle.Font.Bold = true\n\tboldStyle.Font.Size = 12\n\n\thighlightStyle := cells_foss.NewStyle()\n\thighlightStyle.Font.Color = \"FFFFFFFF\"\n\thighlightStyle.Font.Bold = true\n\thighlightStyle.Fill = &cells_foss.Fill{\n\t\tType:  cells_foss.FillTypeSolid,\n\t\tColor: \"FF4472C4\",\n\t}\n\n\theaders := []string{\"Item\", \"Category\", \"Price\", \"In Stock\"}\n\tfor i, h := range headers {\n\t\tref := string(rune('A'+i)) + \"1\"\n\t\tws.Cells().Set(ref, h)\n\t\tcell, _ := ws.Cells().Get(ref)\n\t\tcell.SetStyle(boldStyle)\n\t}\n\n\twb.Save(\"outputfiles/style.xlsx\")\n}\n```\n"
  - title: Structured Tables
    content: "```go\nfunc main() {\n\twb := cells_foss.NewWorkbook()\n\tws := wb.Worksheets[0]\n\n\theaders := []string{\"Product\", \"Q1\", \"Q2\", \"Q3\", \"Q4\", \"Total\"}\n\tfor i, h := range headers {\n\t\tref := string(rune('A'+i)) + \"1\"\n\t\tws.Cells().Set(ref, h)\n\t}\n\n\t// ... populate data rows (see examples/table/main.go) ...\n\n\ttbl := ws.AddTable(\"A1:F6\")\n\ttbl.HasHeaderRow = true\n\ttbl.StyleName = \"TableStyleMedium6\"\n\n\twb.Save(\"outputfiles/table.xlsx\")\n}\n```\n"
  - title: CSV Import
    content: "```go\nfunc main() {\n\tcsvPath := \"outputfiles/employees.csv\"\n\n\twb := cells_foss.NewWorkbook()\n\tif err := wb.ImportFromCSV(csvPath, \"Employees\", ','); err != nil {\n\t\tfmt.Fprintf(os.Stderr, \"Error importing CSV: %v\\n\", err)\n\t\tos.Exit(1)\n\t}\n\n\tws := wb.Worksheets[1] // second sheet; index 0 is the default \"Sheet1\"\n\tfmt.Printf(\"Imported sheet: %q\\n\", ws.Name)\n\n\twb.Save(\"outputfiles/csv_imported.xlsx\")\n}\n```\n"
faq:
  enable: false
  list: []
---
