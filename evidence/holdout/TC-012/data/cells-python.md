## Overview

Version 26.7.0 focuses on workbook round-trip reliability and Excel compatibility, especially for files that contain charts, chartsheets, preserved relationship parts, conditional formatting, and tables.

## What's New

- Improved chartsheet preservation during load/save round-trips.
- Added support for preserving chartsheet-related package parts and content types.
- Improved chart loading for `oneCellAnchor` drawings.
- Improved handling for multiple charts contained under a single drawing anchor.
- Added safer relationship target resolution for workbook and worksheet package parts.

## Enhancements

- Preserved chartsheets as original package parts when the library does not map them to a worksheet object model.
- Preserved dependent package parts referenced through relationship chains, including child `.rels` targets where required.
- Avoided writing dangling relationships for missing package parts, improving Excel openability after save.
- Improved handling of workbook-level preserved relationships such as externally referenced parts.
- Improved worksheet-level preserved relationships, including related child parts used by VML drawings and similar structures.
- Preserved source worksheet numbering more accurately when worksheet and chartsheet parts are interleaved.
- Improved conditional formatting DXF handling when source styles are preserved, avoiding invalid `dxfId` references.
- Synchronized preserved table column names with edited header-row cell text during save.

## Examples

- Refreshed and aligned the example set with the current behavior of the library.
- Updated examples covering worksheet formatting, comments, conditional formatting, charts, pictures, shapes, sparklines, data validation, encryption, hyperlinks, print settings, worksheet protection, JSON export, and Markdown export.

## Compatibility Notes

- This release improves compatibility for workbooks that contain chartsheets, embedded chart parts, preserved external relationships, VML-related content, and preserved table definitions.
- Files that previously risked losing chart-related parts or producing invalid package relationships should now round-trip more reliably.
