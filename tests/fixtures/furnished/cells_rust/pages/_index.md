---
layout: plugin
family_name: Aspose.Cells FOSS
plugin_description: Open-source Rust crate for creating, reading, editing, and saving Excel XLSX spreadsheets with no Office dependencies.
plugin_platform: Rust
head_title: Aspose.Cells FOSS for Rust | Open-Source Excel XLSX Spreadsheet Library
head_description: Open-source Rust crate to create, load, edit, and save Excel XLSX workbooks — cells, formulas, charts, styles, and validation. MIT licensed.
title: Aspose.Cells FOSS for Rust
description: |
  Open-source Rust crate for creating, reading, editing, and saving Excel XLSX spreadsheets with no Office dependencies.
submenu:
  enable: true
github_url: https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Rust
overview:
  enable: true
  title: Aspose.Cells FOSS — Open Source Rust Library
  content: |-
    Aspose.Cells FOSS for Rust is an open-source library for working with Excel
    XLSX spreadsheets in Rust applications. It gives systems programmers a
    native, memory-safe way to create workbooks from scratch, load existing
    XLSX files, modify their contents, and save the result — without Microsoft
    Office, COM automation, or any external runtime.

    The crate models the full spreadsheet object tree: `Workbook`, `Worksheet`,
    and cell collections with typed value setters (`put_value_string`,
    `put_value_i32`, `put_value_bool`, `put_value_decimal`), formulas with
    cached values via `put_formula_with_cached_value`, charts (`Chart`,
    `ChartType`), conditional formatting (`FormatCondition`), data validation
    (`Validation`), auto-filters (`AutoFilter`), cell styling (`CellStyle`,
    `Font`, `Fill`, `Borders`), page setup (`PageSetup`), pictures, shapes,
    comments, hyperlinks, tables (`ListObject`), sparklines
    (`SparklineGroup`), defined names, and workbook/worksheet protection.

    Aspose.Cells FOSS for Rust is MIT licensed and targets Rust edition 2021.
    It installs as a standard Cargo dependency from the GitHub repository —
    see the installation guide for the exact `Cargo.toml` entry. Errors are
    surfaced through conventional `Result` types (`CellsError`), and XLSX
    loading offers repair options (`LoadOptions`) with structured diagnostics
    (`LoadDiagnostics`). For the enterprise product family, see [Aspose.Cells — Enterprise Product Family](https://products.aspose.com/cells/).
content:
  enable: true
  block:
  - title_left: Workbook and Worksheet Management
    content_left: |
      - Create empty workbooks with `Workbook::new` or load existing files with `Workbook::load_xlsx`
      - Add and access sheets via `add_worksheet`, `worksheet`, and mutable worksheet collections
      - Save to disk with `save`, `save_xlsx`, or format-explicit `save_with_format`
      - Control workbook views, settings, and document properties
    title_right: Where It Fits
    content_right: |
      - Backend services generating XLSX reports without Office installed
      - CLI tools that batch-produce or post-process spreadsheets
      - Rust data pipelines exporting tabular results for business users
  - title_left: Cell Data, Formulas, and Types
    content_left: |
      - Typed setters: `put_value_string`, `put_value_i32`, `put_value_bool`, `put_value_decimal`, `put_value_date_time`
      - Write formulas with cached results via `put_formula_with_cached_value`
      - Read values back with `display_string_value` and `value_type`
      - Address cells by A1 name or row/column index (`get`, `get_by_index`)
    title_right: Practical Uses
    content_right: |
      - Financial exports where formulas must open with correct cached values
      - Data-entry templates with typed columns
      - Round-trip editing of user-supplied workbooks
  - title_left: Styling and Layout
    content_left: |
      - Cell styling through `CellStyle` with `Font`, `Fill`, and `Borders`
      - Apply targeted style updates with `StyleFlag`
      - Number formatting via `NumberFormat`
      - Print and page layout via `PageSetup` (paper size, orientation, margins)
      - Freeze panes with `FreezePane`; control row/column visibility
    title_right: Why It Matters
    content_right: |
      - Branded, print-ready reports straight from Rust services
      - Readable exports with proper number and date formats
      - Large sheets that stay navigable with frozen header rows
  - title_left: Charts, Pictures, and Shapes
    content_left: |
      - Add column, line, and other chart types via `ChartType` on a sheet's chart collection
      - Read chart anchors and types back from loaded files (`Chart`, `ChartCollection`)
      - Embed images with `Picture` and drawing shapes with `Shape`
      - Compact in-cell trends with `SparklineGroup`
    title_right: Use Cases
    content_right: |
      - Dashboards and KPI workbooks generated on a schedule
      - Visual summaries embedded next to raw data
      - Automated chart placement from data ranges
  - title_left: Data Analysis and Protection
    content_left: |
      - Conditional formatting rules via `FormatCondition`
      - Data validation rules via `Validation` with `OperatorType`
      - Auto-filters via `AutoFilter`; structured tables via `ListObject`
      - Defined names via `DefinedName`; protection via `WorkbookProtection` and `WorksheetProtection`
      - Robust XLSX loading with `LoadOptions` repair flags and `LoadDiagnostics`
    title_right: Who Benefits
    content_right: |
      - Teams shipping validated data-entry workbooks
      - Services ingesting third-party XLSX files that may need repair
      - Analysts receiving pre-filtered, protected reports
single:
  enable: true
  block:
  - title: Create, Save, and Reload a Workbook
    content: |
      Create a workbook, write typed values and a formula with a cached result, save it as XLSX, and load it back:

      ```rust
      use aspose_cells_foss_rust::{CellValue, Workbook};
      use std::error::Error;

      fn main() -> Result<(), Box<dyn Error>> {
          // Create a new workbook (starts with one sheet, "Sheet1").
          let mut workbook = Workbook::new();
          {
              let mut worksheets = workbook.get_worksheets_mut();
              let sheet = worksheets.get(0)?;
              let mut cells = sheet.get_cells_mut();

              cells.get("A1")?.put_value_string("Hello")?;
              cells.get("B1")?.put_value_i32(123)?;
              cells.get("C1")?.put_value_bool(true)?;
              cells.get("D1")?.put_value_decimal(12.5)?;
              cells.get("F1")?.put_value_i32(10)?;
              cells.get("G1")?
                  .put_formula_with_cached_value("=F1*2", CellValue::Number(20.0))?;
          }
          workbook.save("hello.xlsx")?;

          // Load it back.
          let loaded = Workbook::load_xlsx("hello.xlsx")?;
          let sheet = loaded.worksheet("Sheet1")?;
          let cells = sheet.get_cells();
          println!("A1 = {}", cells.get("A1")?.display_string_value());

          Ok(())
      }
      ```
  - title: Load XLSX Files with Repair Options
    content: |
      Open workbooks defensively with `LoadOptions` repair flags and inspect structured load diagnostics:

      ```rust
      let options = LoadOptions {
          try_repair_package: true,
          try_repair_xml: true,
          ..LoadOptions::default()
      };

      let loaded = Workbook::load_xlsx_with_options(&valid_path, &options)?;
      let sheet = loaded.worksheet("Sheet1")?;
      let cells = sheet.get_cells();

      println!("Saved: {}", valid_path.display());
      println!(
          "Loaded workbook with {} worksheet(s) and {} diagnostic issue(s).",
          loaded.get_worksheets().count(),
          loaded.get_load_diagnostics().issues().len()
      );
      ```
  - title: Add Charts from Data Ranges
    content: |
      Fill a data range, then anchor column and line charts to it:

      ```rust
      let mut charts = sheet.get_charts();
      charts.add(
          ChartType::Column,
          "Charts!$B$1:$B$13".to_string(),
          0,
          4,
          18,
          8,
      )?;
      charts.add(
          ChartType::Line,
          "Charts!$C$1:$C$13".to_string(),
          0,
          9,
          18,
          13,
      )?;
      ```
faq:
  enable: true
  list:
  - question: "What license does Aspose.Cells FOSS for Rust use?"
    answer: "The crate is released under the MIT License; the full text ships in the repository as LICENSE.txt."
  - question: "Can I use it in commercial products?"
    answer: "Yes. The MIT License permits commercial use, modification, and redistribution, provided the copyright and license notice is preserved."
  - question: "How do I install the crate?"
    answer: "Add aspose-cells-foss-rust to your Cargo.toml as a git dependency from the GitHub repository."
  - question: "Which formats can be read and written?"
    answer: "XLSX, with full round-trip read and write via Workbook::load_xlsx and save. Other spreadsheet formats are not supported in the current release."
  - question: "Does it require Microsoft Office?"
    answer: "No. The library reads and writes the XLSX package format directly — no Office installation, COM automation, or external runtime is involved."
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
  content_hash: 26ad12912459ea57f4178d14695fb180
evidence:
  model_sha: 1a6004af47b1ef15385f9d36d381a8172428cc7e
  model_version: 26.7.0
  claims:
  - ERC-cells-rust-cb645226
  apis:
  - CellValue.Number
  - ChartType.Column
  - ChartType.Line
  formats:
  - ext: xlsx
    support: both
enterprise_backlink_injected: https://products.aspose.com/cells/
grade: A
graded_content_hash: "e3b0c44298fc1c149afbf4c8996fb924"
grade_reasons:
  - "No findings -> grade A"
---
