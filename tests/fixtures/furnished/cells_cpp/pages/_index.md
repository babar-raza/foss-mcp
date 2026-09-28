---
layout: plugin
family_name: Aspose.Cells FOSS
plugin_description: Open-source C++ library for creating, loading, editing, and saving
  Excel .xlsx workbooks without Microsoft Excel.
plugin_platform: C++
head_title: Aspose.Cells FOSS for C++ | Open-Source Excel Spreadsheet Library
head_description: Open-source C++ library for Excel .xlsx workbooks. Create, edit,
  style, and save spreadsheets with cells, formulas, and formatting. MIT licensed.
title: Aspose.Cells FOSS for C++
description: |
  Open-source C++ library for creating, loading, editing, and saving Excel .xlsx workbooks without Microsoft Excel.
submenu:
  enable: true
github_url: https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp
overview:
  enable: true
  title: Aspose.Cells FOSS — Open Source C++ Spreadsheet Library
  content: |-
    Aspose.Cells FOSS for C++ is an open-source library that enables developers to programmatically
    create, load, edit, and save Excel `.xlsx` workbooks without requiring Microsoft Excel or any
    COM interop. The library provides a native C++ API that integrates directly into CMake-based
    build systems with no external runtime dependencies.

    The core API covers `Workbook` creation and persistence, `Worksheet` and cell manipulation via
    `Worksheet.GetCells()`, cell value assignment using `Cell.PutValue()`, formula entry with
    `Cell.SetFormula()`, and rich styling through the `Style`, `Font`, and `Color` classes. Number
    format display is handled by `DisplayTextFormatter` with full locale support. Named ranges are
    managed through `DefinedNameUtility` and related collection classes.

    Aspose.Cells FOSS for C++ is released under the MIT license with no runtime fees or
    usage restrictions. Build from source using CMake and integrate directly into your
    project as a header-and-source library. For enterprise features and support, see [Aspose.Cells for C++ — Enterprise Product](https://products.aspose.com/cells/cpp/).
content:
  enable: true
  block:
  - title_left: Workbook and Worksheet Management
    content_left: |
      - Create new `Workbook` instances or load existing `.xlsx` files
      - Access and iterate worksheets via `WorksheetCollection`
      - Rename worksheets with `Worksheet.SetName()`
      - Retrieve and manipulate cells through `Worksheet.GetCells()`
      - Save workbooks to file path with `Workbook.Save()`
    title_right: Typical use cases
    content_right: |
      - Generate Excel reports from application data
      - Transform and reformat existing spreadsheets
      - Produce template-driven output files in server environments
      - Build automated data export pipelines without Excel installed
  - title_left: Cell Values, Formulas, and Number Formats
    content_left: |
      - Write text and numeric values with `Cell.PutValue()`
      - Enter Excel formulas using `Cell.SetFormula()`
      - Read formatted cell text via `DisplayTextFormatter`
      - Apply number format patterns using `Style.SetNumber()`
      - Parse numeric format strings with `ParsedNumericFormat`
    title_right: Typical use cases
    content_right: |
      - Build financial spreadsheets with SUM, AVERAGE, and IF formulas
      - Format currency, percentage, and date columns automatically
      - Extract display-ready text from cells for reporting
      - Validate and normalize numeric data before export
  - title_left: Cell Styling with Fonts, Colors, and Borders
    content_left: |
      - Retrieve and modify cell styles via `Cell.GetStyle()` and `Cell.SetStyle()`
      - Configure fonts using `Style.SetFont()` with `Font.SetBold()` and `Font.SetColor()`
      - Set background fill patterns with `Style.SetPattern()` and `Style.SetForegroundColor()`
      - Construct ARGB colors with `Color.FromArgb()`
      - Apply consistent styles across header rows and data ranges
    title_right: Typical use cases
    content_right: |
      - Add branded header rows with colored backgrounds and bold text
      - Highlight summary rows with fill patterns and custom fonts
      - Generate consistently styled output for business dashboards
      - Apply table-level formatting in a single pass
  - title_left: Page Setup and Print Configuration
    content_left: |
      - Configure paper size, orientation, and scaling via `PageSetup`
      - Set fit-to-page and print area options with `PageSetup` methods
      - Define print headers, footers, and margins
      - Control row and column repeat groups for multi-page printing
    title_right: Typical use cases
    content_right: |
      - Prepare printable invoices and formatted reports
      - Define consistent print margins for regulatory documents
      - Configure landscape layout for wide financial tables
      - Set up repeating header rows for multi-page output
single:
  enable: true
  block:
  - title: Create a Styled Workbook and Save as .xlsx
    content: |
      Create a workbook, populate cells with values and a formula, apply header styling, and save:

      ```cpp
      #include "aspose/cells_foss/Workbook.h"
      #include "aspose/cells_foss/Worksheet.h"
      #include "aspose/cells_foss/Cell.h"
      #include "aspose/cells_foss/Style.h"
      #include "aspose/cells_foss/Color.h"
      #include "aspose/cells_foss/Font.h"
      using namespace Aspose::Cells_FOSS;
      int main() {
          Workbook workbook;
          Worksheet& sheet = workbook.GetWorksheets()[0];
          sheet.SetName("Products");
          sheet.GetCells()["A1"].PutValue("Product");
          sheet.GetCells()["B1"].PutValue("Price");
          sheet.GetCells()["A2"].PutValue("Apple");
          sheet.GetCells()["B2"].PutValue(2.99);
          sheet.GetCells()["B4"].SetFormula("=SUM(B2:B3)");
          Style headerStyle = sheet.GetCells()["A1"].GetStyle();
          Font font;
          font.SetBold(true);
          font.SetColor(Color::FromArgb(255, 255, 255, 255));
          headerStyle.SetFont(font);
          headerStyle.SetForegroundColor(Color::FromArgb(255, 34, 120, 212));
          sheet.GetCells()["A1"].SetStyle(headerStyle);
          workbook.Save("products.xlsx");
          return 0;
      }
      ```
faq:
  enable: true
  list:
  - question: What license does Aspose.Cells FOSS for C++ use?
    answer: MIT License — free for commercial and open-source use. Include the `LICENSE.txt`
      file when distributing.
  - question: Which file formats are supported?
    answer: '`.xlsx` for both load and save. `.csv` load is supported; `.csv` save
      is not available in v0.1.'
  - question: How do I install the library?
    answer: Clone the repository and use CMake `add_subdirectory` or `FetchContent`.
      Requires a `C++17` compiler and `CMake 3.15+`. No external dependencies.
  - question: Does the library require Microsoft Excel?
    answer: No. Aspose.Cells FOSS for C++ is fully self-contained and does not require
      Microsoft Excel or any COM interop.
  - question: Are all API classes fully implemented?
    answer: Several classes in v0.1 are declared but stub-only, including `ConditionalFormattingCollection`
      and `AutoFilter`. Core `Workbook`, `Worksheet`, `Cell`, and `Style` operations
      are fully functional.
supportandlearning:
  enable: true
more_formats:
  enable: true
back_to_top:
  enable: true
provenance:
  content_origin: skill-generated
  last_mechanism: skill
  auto_updatable: true
  content_hash: e3b0c44298fc1c149afbf4c8996fb924
grade: A
graded_content_hash: "e3b0c44298fc1c149afbf4c8996fb924"
grade_reasons:
  - "No findings -> grade A"
evidence:
  model_sha: 9f852d0ff1cfdad2d661556d6b87a8eff8c063a2
  model_version: ''
  claims: []
  apis: []
  formats:
  - ext: xlsx
    support: both
---
