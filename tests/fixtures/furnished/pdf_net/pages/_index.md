---
layout: plugin
family_name: Aspose.PDF FOSS
plugin_description: MIT-licensed .NET library for creating, reading, editing, securing,
  and annotating PDF documents — zero paid dependencies.
plugin_platform: .NET
head_title: Aspose.PDF FOSS for .NET | Free MIT-Licensed PDF Library
head_description: Aspose.PDF FOSS for .NET is a free, MIT-licensed library for creating
  and working with PDF documents in C# and .NET. Includes annotation, form fields,
  text extraction, document encryption, and HTML/SVG/Markdown import.
title: Aspose.PDF FOSS for .NET
description: Create and manipulate PDF documents from .NET — with annotations, form
  fields, text extraction, page management, encryption, and HTML/SVG/Markdown import.
  MIT-licensed.
submenu:
  enable: true
github_url: https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET.git
doc_source_commit: d10e2c829e1e41d3a9529057ad2ea7c7f53b1095
overview:
  enable: true
  title: Aspose.PDF FOSS for .NET
  content: |-
    ```mermaid
    flowchart TD
      PRODUCT["Aspose.PDF FOSS for .NET"]
      subgraph Capabilities["Core Capabilities"]
        direction LR
        subgraph capl[" "]
          direction TB
          c1["Create and edit PDF documents"]
          c2["Text extraction and manipulation"]
          c3["Form handling"]
          c4["Annotations and stamps"]
        end
        subgraph capr[" "]
          direction TB
          c5["Security features"]
          c6["Format conversion and rendering"]
          c7["Tagged PDF and accessibility"]
          c8["Table and image handling"]
        end
      end
      PRODUCT --> Capabilities
    ```
content:
  enable: true
  block:
  - title_left: Installation, Dependencies, and Quick Start
    content_left: |-
      ### Installation

      Install the published package from NuGet (`Aspose.PDF.FOSS`, version 26.9.0):

      ```bash
      dotnet add package Aspose.PDF.FOSS
      ```

      To work from a source checkout instead, build the clone with dotnet build:

      ```bash
      git clone https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET.git
      cd Aspose.PDF-FOSS-for-.NET
      dotnet build
      ```

      ### Required Package Dependencies

      - `System.Drawing.Common 8.0.0`

      ### Native and System Requirements

      - Requires .NET `net8.0` (`TargetFramework` in `src/Aspose.Pdf.Foss.csproj`).

      ### Development Dependencies

      - `SonarAnalyzer.CSharp 10.33.0.1635`

      ### Quick Start

      This example demonstrates creating a new PDF document with a text fragment and opening an existing PDF to extract its text content using `Aspose.PDF.FOSS` version 26.9.0 for net8.0.

      ```csharp
      using Aspose.Pdf;
      using Aspose.Pdf.Text;

      // Create a new PDF document
      using var doc = Document.Create();
      var page = doc.Pages.Add();
      var fragment = new TextFragment("Hello, World!");
      fragment.TextState.FontSize = 24;
      page.Paragraphs.Add(fragment);
      doc.Save("output.pdf");

      // Open an existing PDF and extract its text
      using var existing = Document.Open("input.pdf");
      var absorber = new TextAbsorber();
      absorber.Visit(existing.Pages[1]);
      Console.WriteLine(absorber.Text);
      ```
    title_right: Key Capabilities and API Reference
    content_right: |-
      ### Key Capabilities

      - **Create and edit PDF documents.** Create or open PDF documents using the `Aspose.Pdf.Document` class, then manipulate pages—add, delete, reorder, rotate, resize, or copy—through `Aspose.Pdf.Page` and `Aspose.Pdf.PageCollection`, preserving bookmarks, outlines, page labels, named destinations, document info, XMP metadata, and optional content layers on full round-trip or incremental save.
      - **Text extraction and manipulation.** Extract text with `Aspose.Pdf.Text` classes such as `TextAbsorber`, `TextFragmentAbsorber`, and `ParagraphAbsorber`, and build or modify text content using `Aspose.Pdf.TextStamp`, `TextFragment`, `TextSegment`, and `FormattedText`, supporting bidirectional text, ligature-aware spans, and cross-operator replacements.
      - **Form handling.** Read, fill, and build AcroForm fields—including text, checkbox, radio, choice, button, signature, and rich text—through `Aspose.Pdf.Forms` and `Aspose.Pdf.FieldValueType`, with field-level flatten and JSON/XFDF form-data import and export.
      - **Annotations and stamps.** Add and edit every standard annotation type—link, text, free-text, highlight, square/circle/line/polyline, ink, stamp, popup, file attachment, screen, redaction, watermark, pre-press marks, and rich media—using `Aspose.Pdf.Annotations` and `Aspose.Pdf.Stamps`, with appearance generation and flatten support.
      - **Security features.** Encrypt and decrypt documents with RC4-40, RC4-128, AES-128, and AES-256 using `Aspose.Pdf.Security`, and sign documents with PKCS#7/CMS detached signatures—including DocMDP certifying signatures—through `Aspose.Pdf.CryptoAlgorithm` and `Aspose.Pdf.Permissions`, with document permissions set directly on `Document`.
      - **Format conversion and rendering.** Render pages to PNG, JPEG, BMP, TIFF, or SVG using `Aspose.Pdf.Devices` such as `PngDevice` and `JpegDevice`, and convert PDF to and from HTML and SVG, load Markdown, XML, or plain text, and export to PDF/A (1A through 4F) with `Aspose.Pdf.Converters` and `Aspose.Pdf.HtmlLoadOptions`.
      - **Tagged PDF and accessibility.** Read an existing structural tree and walk its `Aspose.Pdf.LogicalStructure` element hierarchy, or build tagged content from scratch with `Aspose.Pdf.Tagged`'s 37 typed structure elements, setting document language, title, alternate text, and marked content for accessibility.
      - **Table and image handling.** Extract tabular data and build tables using `Aspose.Pdf.Table`, `Row`, `Cell`, and `TableAbsorber`, draw shapes with `Graph`, `Rectangle`, `Circle`, `Ellipse`, `Arc`, `Line`, and `Curve`, and extract or place images through `Aspose.Pdf.ImagePlacementAbsorber` and `Aspose.Pdf.XImageCollection`.

      ### API Reference

      Aspose.PDF FOSS for .NET provides the `Document` class as its primary entry point, exposing pages through `Document.Pages` (a `PageCollection` of `Page` objects), interactive forms through `Document.Form`, and security features through `PdfFileSecurity` and `PdfFileSignature`. The library supports PDF creation, manipulation, and conversion across .NET 8.0 with version 26.9.0.

      The verified public surface has 899 types.

      <details>
      <summary>View the Complete Public API Surface</summary>

      ### Core API

      | Class | Description |
      | `AcroFormData` | Form-level AcroForm dictionary data surfaced by the form JSON export. |
      | `ActionCollection` | Collection of actions associated with an annotation. |
      | `Annotation` | Represents a PDF annotation. |
      | `AnnotationActionCollection` | Per-event PDF action slots for a widget annotation (matches the public /AA-tree entry shape: 14 named events plus the direct /A activation action). |
      | `AnnotationCollection` | Collection of annotations on a page. |
      | `AnnotationSelector` | Visitor that filters annotations across one or more pages. |
      | `AppearanceDictionary` | Appearance-stream dictionary on an annotation (/AP entry): maps appearance-state name -> XForm. |
      | `BleedMarkAnnotation` | BleedMarkAnnotation represents a bleed mark annotation used to indicate the trim area in a PDF document. |
      | `Border` | Represents the border of an annotation or field widget. |
      | `CaretAnnotation` | CaretAnnotation represents a caret annotation used to indicate the insertion point in a PDF document. |
      | `Characteristics` | Annotation characteristics (border, rotation, etc.). |
      | `CircleAnnotation` | CircleAnnotation represents a circular annotation used to highlight or mark areas in a PDF document. |
      | `ColorBarAnnotation` | ColorBarAnnotation represents a color bar annotation used to display color information in a PDF document. |
      | `CommonFigureAnnotation` | Common base for square and circle annotations — a figure drawn inside a rectangle, optionally inset by /RD (PDF 32000 §12.5.6.8). |
      | `Dash` | Represents a dash pattern for borders. |
      | `DefaultAppearance` | Represents the default appearance of a free text annotation. |
      | `DocumentActionCollection` | Document-level /AA additional-action dictionary (PDF 32000-1 §12.6.3 Table 195). |
      | `ExplicitDestination` | Represents an explicit destination in a PDF document (e.g. |
      | `FileAttachmentAnnotation` | Represents a file attachment annotation. |
      | `FitBExplicitDestination` | Fit-bounding-box destination (/FitB). |
      | `FitBHExplicitDestination` | FitBH destination (/FitBH) — fit bounding box horizontally at Top. |
      | `FitBVExplicitDestination` | FitBV destination (/FitBV) — fit bounding box vertically at Left. |
      | `FitExplicitDestination` | Fit-the-page destination (/Fit). |
      | `FitHExplicitDestination` | Fit horizontally at /Top (/FitH). |
      | `FitRExplicitDestination` | FitR rectangle destination (/FitR). |
      | `FitVExplicitDestination` | Fit vertically at /Left (/FitV). |
      | `FixedPrint` | FixedPrint indicates whether an annotation should be printed at a fixed size regardless of zoom level. |
      | `FreeTextAnnotation` | FreeTextAnnotation represents a text annotation that allows free-form text placement in a PDF document. |
      | `GenericAnnotation` | Fallback annotation class for annotation subtypes that have no dedicated model (e.g. |
      | `GoToAction` | GoToAction represents an action that navigates to a specified destination within the PDF document. |
      | `GoToRemoteAction` | Go-to-remote action — jumps to a destination in a different PDF file . |
      | `GoToURIAction` | Alias for UriAction, matching the public API name. |
      | `HideAction` | Hide action (/S /Hide, PDF 32000 §12.6.4.10): sets the hidden flag of the named field(s) or annotation(s). |
      | `HighlightAnnotation` | Highlight text markup annotation. |
      | `IAppointment` | Marker interface for any object that can be stored in an outline item's Destination, a link annotation's Destination, Document.OpenAction, or a named-destination collection. |
      | `ImportDataAction` | An import-data action (/S /ImportData): imports field data from the FDF/XFDF file identified by the action's /F file specification. |
      | `InkAnnotation` | InkAnnotation represents a freehand drawing annotation used to annotate PDF documents. |
      | `JavascriptAction` | JavascriptAction represents an action that executes JavaScript code when triggered in a PDF document. |
      | `FieldDateTimeFormatter` | Formats a date/time value string according to an Acrobat-style date format. |
      | `FieldNumberCurrencyFormatter` | Formats a numeric string as a currency value. |
      | `FieldNumberPercentFormatter` | Formats a numeric string as a percentage (multiplies by 100 first). |
      | `LaunchAction` | LaunchAction represents an action that launches an external application or opens a file in a PDF document. |
      | `LineAnnotation` | LineAnnotation represents a line annotation used to draw lines or arrows in a PDF document. |
      | `LinkAnnotation` | LinkAnnotation represents a hyperlink annotation used to create clickable links in a PDF document. |
      | `MarkupAnnotation` | MarkupAnnotation is a base class for annotations that provide markup functionality in a PDF document. |
      | `Measure` | Measure-units metadata attached to a LineAnnotation (PDF 32000 §12.5.6.13 measure dictionaries). |
      | `MediaClip` | A media clip object (PDF §13.2.4) — the actual media data or section thereof. |
      | `MediaClipData` | A media clip data object (PDF §13.2.4.2) — full media data via a file specification. |
      | `MediaClipSection` | A media clip section object (PDF §13.2.4.3) — a temporal section of another clip. |
      | `MediaRendition` | A media rendition (PDF §13.2.3.2) — pairs a media clip with playback parameters. |
      | `MovieAnnotation` | MovieAnnotation represents a movie annotation used to embed and play video content in a PDF document. |
      | `NamedAction` | NamedAction represents an action that performs a predefined operation in a PDF document. |
      | `NumberFormat` | One number-format entry — describes how a measurement value is rendered (precision, separators, before/after text). |
      | `NumberFormatList` | Nested number-format list (public-API shape: Measure+NumberFormatList). |
      | `PDF3DAnnotation` | PDF3DAnnotation represents a three-dimensional annotation used to embed 3D content in a PDF document. |
      | `PDF3DArtwork` | A 3D artwork dictionary referenced by a PDF 3D annotation (public-API shape). |
      | `PDF3DContent` | Embedded-stream content for a 3D artwork (U3D or PRC). |
      | `PDF3DCrossSection` | Single cross-section through a PDF 3D model (public-API shape). |
      | `PDF3DCrossSectionArray` | Ordered collection of PDF3DCrossSection entries applied to a PDF 3D view (public-API shape). |
      | `PDF3DCuttingPlaneOrientation` | Orientation angles for the cutting plane of a 3D cross-section (public-API shape). |
      | `PDF3DLightingScheme` | Lighting scheme applied to a PDF 3D artwork (public-API shape). |
      | `PDF3DRenderMode` | Render mode applied to a 3D artwork view (public-API shape). |
      | `PDF3DStream` | PDF /3DD stream wrapper — pairs a PDF3DArtwork with its document-owned content stream. |
      | `PDF3DView` | Camera + render-state snapshot of a 3D artwork (public-API shape). |
      | `PDF3DViewArray` | Indexed collection of PDF3DView snapshots (public-API shape). |
      | `PageInformationAnnotation` | PageInformationAnnotation represents metadata or information associated with a specific page in a PDF document. |
      | `PdfAction` | Base class for PDF actions. |
      | `PdfActionCollection` | Collection of PdfAction entries attached to an annotation (or any other action-bearing PDF object). |
      | `PolyAnnotation` | Common base for polygon and polyline annotations — a chain of connected vertices (PDF 32000 §12.5.6.9). |
      | `PolygonAnnotation` | PolygonAnnotation represents a polygon annotation used to highlight or mark areas in a PDF document. |
      | `PolylineAnnotation` | PolylineAnnotation represents a polyline annotation used to draw connected line segments in a PDF document. |
      | `PopupAnnotation` | PopupAnnotation represents a popup window annotation that displays additional information when opened. |
      | `PrinterMarkAnnotation` | Aggregate printer's-mark generator. |
      | `RedactAnnotation` | Backward-compatible alias for RedactionAnnotation. |
      | `RedactionAnnotation` | Represents a redaction annotation. |
      | `RegistrationMarkAnnotation` | RegistrationMarkAnnotation represents a registration mark annotation used for alignment in printing workflows. |
      | `Rendition` | A rendition object (PDF §13.2.3) — describes a media object to be played by a rendition action. |
      | `RenditionAction` | A rendition action (PDF §12.6.4.14) — controls the playing of multimedia content. |
      | `RichMediaAnnotation` | RichMediaAnnotation represents an annotation that embeds rich media content such as audio or video in a PDF document. |
      | `RichTextToFlatStructureTransformer` | Transforms rich text annotation content (XHTML with arbitrarily nested spans) into a flat structure where every leaf text node becomes a single &lt;span style="."&gt; with the fully-merged CSS from all ancestor elements. |
      | `ScreenAnnotation` | ScreenAnnotation represents a screen annotation used to create interactive elements in a PDF document. |
      | `SelectorRendition` | A selector rendition (PDF §13.2.3.3) — chooses among alternative renditions. |
      | `SoundAnnotation` | SoundAnnotation represents a sound annotation used to embed and play audio content in a PDF document. |
      | `SoundData` | SoundData represents the audio data associated with a sound annotation in a PDF document. |
      | `SoundSampleData` | SoundSampleData represents the raw audio sample data used in a sound annotation. |
      | `SquareAnnotation` | SquareAnnotation represents a rectangular annotation used to highlight or mark areas in a PDF document. |
      | `SquigglyAnnotation` | Squiggly text markup annotation. |
      | `StampAnnotation` | StampAnnotation represents a stamp annotation used to apply predefined or custom stamps to a PDF document. |
      | `StrikeOutAnnotation` | StrikeOut text markup annotation. |
      | `SubmitFormAction` | Submit-form action — sends form field data to a URL or remote file (PDF 32000-1:2008 §12.7.5.2). |
      | `TextAnnotation` | TextAnnotation represents a sticky note annotation used to add comments or notes in a PDF document. |
      | `TextMarkupAnnotation` | Base class for the text-markup annotations (Highlight, Underline, StrikeOut, Squiggly) — those whose geometry is a set of QuadPoints over page text. |
      | `TextStyle` | Bundled font / colour / alignment style applied to a free-text annotation's rich text. |
      | `TrimMarkAnnotation` | TrimMarkAnnotation represents a trim mark annotation used to indicate the final trim size in a PDF document. |
      | `UnderlineAnnotation` | Underline text markup annotation. |
      | `UriAction` | UriAction represents an action that opens a Uniform Resource Identifier in a PDF document. |
      | `WatermarkAnnotation` | Represents a watermark annotation that can be added to a PDF page. |
      | `WidgetAnnotation` | WidgetAnnotation represents a widget annotation used to create interactive form fields in a PDF document. |
      | `XYZExplicitDestination` | XYZ explicit destination: display the page at position (left, top) with zoom factor. |
      | `AppearanceEntry` | One state of a widget appearance variant (the body of an /AP/N, AP/D, or /AP/R entry). |
      | `AppearanceImageData` | One image XObject of a widget appearance's /Resources, captured decoded (samples, not filtered bytes) for the JSON round-trip. |
      | `Artifact` | Represents a PDF artifact — a marked content sequence tagged as /Artifact (PDF 32000 §14.8.2.2). |
      | `ArtifactCollection` | Collection of artifacts on a page. |
      | `AutoTaggingSettings` | Settings controlling automatic structure-tree generation during conversion. |
      | `BackgroundArtifact` | Represents a background artifact (a coloured block or image painted behind page content). |
      | `BaseOperatorCollection` | Standalone operator-list class — surface mirrors OperatorCollection but is detached from any page. |
      | `BaseParagraph` | Base class for paragraph-level DOM content (text fragments, tables, images, header/footer fragments, floating boxes). |
      | `BatesNArtifact` | A Bates-numbering pagination artifact: a zero-padded running number with optional prefix and suffix. |
      | `BitmapInfo` | BitmapInfo provides information about bitmap images used in a PDF document. |
      | `BorderInfo` | Represents border information for table cells and rows. |
      | `BorderPartStyle` | BorderPartStyle defines the style of individual parts of an annotation's border in a PDF document. |
      | `BoundsCheckableList` | A list of T that optionally enforces container bounds on insertion: under ThrowExceptionIfDoesNotFit an item whose CheckBounds fails raises BoundsOutOfRangeException at add time. |
      | `BoundsOutOfRangeException` | Thrown when an element is inserted into a parent container whose bounds check mode is ThrowExceptionIfDoesNotFit and the element does not fit the container rectangle. |
      | `BuildVersionInfo` | Build and version information for the library. |
      | `Cell` | Represents a cell in a table row. |
      | `Cells` | A collection of cells in a row. |
      | `Collection` | PDF Portfolio collection. |
      | `CollectionField` | One column in a PDF Portfolio's collection schema (PDF spec §7.11.5 Table 75). |
      | `CollectionItem` | Per-file metadata entries declared by a portfolio /Collection's schema, exposed as a typed dictionary on CollectionItem. |
      | `CollectionSchema` | Schema of a PDF Portfolio collection (PDF spec §7.11.5 Table 74). |
      | `Pdf.Color` | Represents a color value used in PDF documents. |
      | `ColumnInfo` | ColumnInfo specifies layout properties for columns in a PDF document. |
      | `DiffOperation` | A single edit produced when diffing two texts: an Operation (Equal / Delete / Insert) together with the run of text it applies to. |
      | `IDiffOptimizationOperation` | A post-processing pass that normalises a diff — a mutable list of DiffOperations — in place, preserving the source and destination texts while producing a cleaner or more canonical sequence of edits. |
      | `MergingOptimizer` | Reduces a diff to its canonical form: coalesce adjacent runs of the same operation and factor the common prefix and suffix out of a mixed delete/insert run (OperationsMerger), then slide any single edit that is fenced by two equalities sideways to dissolve one of them (OperationsSlideMerger). |
      | `OperationsMerger` | Merges a diff into its canonical minimal form: coalesces adjacent runs of the same operation, and for a mixed delete/insert run factors any common prefix into the preceding equality and any common suffix into the following equality. |
      | `OperationsSlideMerger` | Shifts a single edit that is surrounded on both sides by equalities sideways to eliminate one of those equalities, canonicalising the diff. |
      | `DiffUtils` | Text helpers used by the diff engine: common prefix/suffix extraction and re-assembly of the source / destination text from a list of DiffOperations. |
      | `EditContainer` | A single change found by the side-by-side comparison, bound to the page rectangles it covers. |
      | `GraphicalPdfComparer` | Compares two PDF pages/documents graphically by rendering them to rasters and highlighting the pixels that differ. |
      | `ImagesDifference` | Result of a graphical comparison of two rendered PDF pages: the first (source) page rasterised to a 24bpp bitmap, plus a per-pixel record of where the second (destination) page differs from it. |
      | `SideBySideComparisonOptions` | Options for SideBySidePdfComparer: whitespace handling, comparison/exclusion areas and the marker colours used in the output document. |
      | `SideBySideDocsComparisonResult` | Result of a document-level side-by-side comparison: per-page change lists plus the full per-page edit sequences. |
      | `SideBySidePagesComparisonResult` | Result of a page-level side-by-side comparison. |
      | `SideBySidePdfComparer` | Compares the text of two pages or documents and produces a result PDF that shows both versions side by side with the changes highlighted (deletions on the left page, insertions on the right). |
      | `CompositingParameters` | Image-blending parameters consumed by Stream, int, float, float, float, float, CompositingParameters). |
      | `ContentStreamBuilder` | Builds PDF content stream bytes using the operator syntax from PDF32000 §8-9. |
      | `ExtGState` | Represents an Extended Graphics State dictionary (PDF32000 §8.4.5). |
      | `ExtractedPath` | Represents a complete path with its painting mode and color. |
      | `GraphicsState` | Tracks the PDF graphics state during content stream parsing (PDF32000 §8.4). |
      | `PathCommand` | A single path construction command with coordinates. |
      | `PathExtractor` | Extracts vector paths from PDF page content streams. |
      | `PathSegment` | Represents a segment of a path. |
      | `ContentsAppender` | Buffers operators to prepend to / append to a page's content stream. |
      | `MarkdownConverterOptions` | Options for PDF-to-Markdown conversion. |
      | `PdfToHtmlConverter` | Converts PDF pages to HTML markup. |
      | `PdfToMarkdownConverter` | Converts PDF pages to Markdown text. |
      | `PdfToSvgConverter` | Converts PDF pages to SVG format. |
      | `PdfToTextConverter` | Converts PDF pages to plain text. |
      | `CrashReportOptions` | Configures crash-report emission for GenerateCrashReport. |
      | `CssSavingInfo` | Context passed to a CssSavingStrategy callback. |
      | `CssUrlRequestInfo` | Context passed to a CssUrlMakingStrategy callback. |
      | `DefaultResourcesData` | Form-level default resources (/DR) surfaced by the form JSON export. |
      | `DeprecatedFeatureException` | Thrown when an operation requests a feature that the document's PDF version has deprecated — e.g. |
      | `DestinationArray` | Opaque wrapper around a PDF destination array, created by NamedDestination factory methods. |
      | `DestinationCollection` | Named-destination collection exposed as IEnumerable&lt;KeyValuePair&lt;string, object&gt;&gt; for public-API compatibility. |
      | `BmpDevice` | Renders PDF document pages into BMP image format. |
      | `DocumentDevice` | Abstract base for whole-document rendering devices. |
      | `GdiPlusPageRenderer` | PDF page renderer that rasterizes through GDI+ (Graphics). |
      | `GifDevice` | Renders PDF document pages into GIF image format. |
      | `IPageRenderer` | Renders a PDF page to an RGBA pixel buffer. |
      | `ImageDevice` | Abstract base class for image rendering devices (PNG, JPEG, BMP, TIFF). |
      | `ImagePageDevice` | Default PageDevice that delegates to any ImageDevice — PngDevice / JpegDevice / BmpDevice / TiffDevice. |
      | `IndexBitmapConverter` | Abstract base class for converting rendered images to indexed (1/4/8bpp) bitmaps. |
      | `JpegDevice` | Renders PDF document pages into JPEG image format. |
      | `Margins` | Page margins in points for TIFF rendering. |
      | `PageDevice` | Abstract base for single-page rendering devices. |
      | `Devices.PageSize` | Represents a physical page size in points. |
      | `PdfDocumentDevice` | Default DocumentDevice implementation that saves the document as a PDF (Document.Save round-trip). |
      | `PngDevice` | Renders PDF document pages into PNG image format. |
      | `Resolution` | Represents the resolution (DPI) for device rendering and image placement. |
      | `RgbaBuffer` | Raw RGBA pixel buffer returned by a IPageRenderer. |
      | `SoftwarePageRenderer` | Built-in software PDF page renderer. |
      | `SvgDevice` | Converts a PDF page to SVG markup. |
      | `TextDevice` | Extracts text from PDF pages. |
      | `ThumbnailDevice` | Renders PDF document pages into thumbnail PNG images. |
      | `TiffDevice` | Renders PDF document pages into TIFF image format. |
      | `TiffSettings` | Settings for TIFF image generation (color depth, compression). |
      | `Document` | Represents a PDF document. |
      | `DocumentCollection` | Aspose.Pdf.DocumentCollection represents a collection of PDF documents that can be managed and manipulated as a single unit. |
      | `DocumentInfo` | Represents the document information dictionary. |
      | `Arc` | An arc shape (portion of an ellipse). |
      | `Circle` | A circle shape. |
      | `Drawing.Color` | Color for drawing shapes. |
      | `Curve` | A Bézier curve shape. |
      | `DrawingPath` | A path shape composed of move, line, and curve segments. |
      | `DrawingRectangle` | A rectangle shape. |
      | `Ellipse` | An ellipse shape. |
      | `GradientAxialShading` | Defines a linear (axial) gradient between two colors for use as a fill pattern. |
      | `Graph` | A graph container that holds drawable shapes and renders them to a content stream. |
      | `Line` | A line shape. |
      | `Path` | A shape built out of OTHER shapes: the children's outlines go into one path and that path is painted once, with this shape's own GraphInfo. |
      | `PatternColorSpace` | Pattern colour-space marker (public surface). |
      | `Drawing.Point` | A 2D point (x, y). |
      | `Polygon` | A polygon shape (closed path with arbitrary vertices). |
      | `Drawing.Rectangle` | A rectangle shape in the Drawing namespace (distinct from Aspose.Pdf.Rectangle which is a page rectangle). |
      | `Shape` | Base class for drawable shapes. |
      | `EmbeddedFileCollection` | Collection of embedded files in a document. |
      | `EmptyValueException` | Thrown when an operation requires a value the caller left empty (e.g. |
      | `EncryptedPayload` | Wraps the /EP entry on an embedded-file file-spec — the encrypted-payload that signals a PDF 2.0 unencrypted-wrapper document. |
      | `ExportFieldsToJsonOptions` | Options passed to ExportToJson. |
      | `ExtGStateValue` | One /ExtGState entry — name plus stroke/fill alpha factors. |
      | `AlignmentType` | Horizontal-alignment selector for legacy facade APIs (e.g. |
      | `AutoFiller` | Repeatedly stamps a template PDF with rows from a DataTable: for each row, fills the form fields whose names match column names, flattens (except those listed in UnFlattenFields), and appends the resulting pages to the output. |
      | `BDCProperties` | Properties for a BDC / DP marked-content operator (/MCID and /Lang). |
      | `Bookmark` | A single bookmark extracted from a document's outline tree by ExtractBookmarks(). |
      | `Bookmarks` | An ordered, indexable collection of Bookmark entries returned by ExtractBookmarks(). |
      | `ContentsResizeParameters` | Parameters for ResizeContents describing margins, content size, and whether the page media box should change to match. |
      | `ContentsResizeValue` | Represents a content-resize value. |
      | `CorruptedItem` | One file the Concatenate path could not parse — recorded only when CorruptedFileAction is set to skip-and-continue. |
      | `DocumentPrivilege` | Represents document access permissions (P value in encryption dictionary). |
      | `FontColor` | Represents a font color using RGB components. |
      | `Facades.Form` | Facade for form field manipulation including XML import/export. |
      | `FormDataConverter` | Bridges PDF form-data formats (FDF / XFDF / XML) with DataTable and direct stream-to-stream transforms. |
      | `FormEditor` | Facade for form operations: fill fields, flatten forms, import/export data. |
      | `FormFieldFacade` | Represents the visual appearance attributes of a form field. |
      | `FormImportResult` | Per-field result of a form-data import operation. |
      | `FormattedText` | Represents formatted text used in stamp and mend operations. |
      | `FormattedTextFont` | Represents a font reference returned by FormattedText.getFont(). |
      | `IFacade` | Aspose.Pdf.Facades.IFacade provides a common interface for facade classes that wrap PDF document operations. |
      | `ISaveableFacade` | Aspose.Pdf.Facades.ISaveableFacade defines methods for binding, saving, and closing PDF documents through a facade pattern. |
      | `LineInfo` | Line drawing parameters for PdfContentEditor.DrawCurve. |
      | `PageBreak` | Describes a single horizontal cut on a source page: PageNumber (1-based) identifies the page in the source document, Position the PDF y-coordinate where the page is split. |
      | `PdfAnnotationEditor` | Facade for annotation manipulation: import/export XFDF, delete, flatten, redact. |
      | `PdfBookmarkEditor` | Facade for bookmark (outline) manipulation: create, extract, delete bookmarks. |
      | `PdfContentEditor` | Facade for content-level editing: text replacement, link creation, image operations. |
      | `PdfConverter` | Facade for converting PDF pages to images. |
      | `PdfExtractor` | Facade for extracting text and images from a PDF document. |
      | `PdfFileEditor` | Real-only additions to PdfFileEditor: exception-handling state, Try* wrappers around the existing working methods, and MemoryStream/file overloads for SplitToBulks/SplitToPages that wrap the real byte[] implementations already present in PdfFileEditor.cs. |
      | `PdfFileInfo` | Facade for accessing PDF document metadata and properties. |
      | `PdfFileMend` | Facade for adding text and images to existing PDF documents. |
      | `PdfFileSanitization` | Repairs damaged PDF files at the byte level so they open again: trims waste bytes before the %PDF- header and after the final %%EOF, and rebuilds a broken cross-reference table / trailer by re-scanning the file for its indirect objects. |
      | `PdfFileSecurity` | Facade for encrypting, decrypting, and changing passwords on PDF files. |
      | `PdfFileSignature` | Facade for signing and reading digital signatures in PDF documents. |
      | `PdfFileStamp` | Facade for adding stamps, page numbers, headers, footers, and watermarks. |
      | `PdfJavaScriptStripper` | Removes all JavaScript from a PDF document. |
      | `PdfPageEditor` | Facade for page-level editing: rotation, resizing, margin adjustment, and page box manipulation (CropBox, TrimBox, BleedBox, ArtBox). |
      | `PdfPrintPageInfo` | Aspose.Pdf.Facades.PdfPrintPageInfo holds information about a specific page to be printed in a PDF document. |
      | `PdfViewer` | Façade for viewing / printing a PDF document. |
      | `PdfXmpMetadata` | Dictionary-style facade over a PDF document's XMP metadata stream. |
      | `Facades.RenderingOptions` | Rendering options used by PdfConverter and other facade classes. |
      | `ReplaceTextStrategy` | Configures how ReplaceText(string, string) matches and substitutes text. |
      | `SignatureName` | Identifies a signature field by both its partial name (Name) and full hierarchical name (FullName), and reports whether the field currently carries a signature value via HasSignature. |
      | `Facades.Stamp` | Represents a stamp to be applied via PdfFileStamp facade. |
      | `StampInfo` | Contains information about a stamp on a page. |
      | `TextProperties` | Text-display properties consumed by the *TextOperator family. |
      | `VerticalAlignmentType` | Vertical-alignment selector for legacy facade APIs (e.g. |
      | `ViewerPreference` | Bag of const int bit-flags describing viewer preferences (page mode, page layout, fullscreen behaviour, duplex, print scaling, reading direction). |
      | `FieldExportingData` | Data-transfer object describing a single form field (or the form-level AcroForm dictionary) as produced by the form JSON export. |
      | `FieldSerializationResult` | Per-field outcome of an ExportToJson or ImportFromJson call. |
      | `FileHyperlink` | Hyperlink that launches an external file (e.g. |
      | `FileParams` | Wraps the /Params dict on an embedded-file stream (PDF §7.11.3 Table 46). |
      | `FileSpecification` | Represents an embedded file specification (PDF §7.11.3). |
      | `FloatingBox` | Represents a floating box that can be positioned absolutely or flowed on a page. |
      | `FontEmbeddingException` | Raised when a font marked for embedding may not be written into the document — the face's own licence permits printing and preview but not the editable copy a PDF amounts to. |
      | `FontEmbeddingOptions` | Font-embedding behaviour during conversion. |
      | `FontNotFoundException` | Thrown when a requested font cannot be located (no system font with that name, no matching custom-font source, etc.). |
      | `FontUtilities` | Provides font management utilities for a document. |
      | `BarcodeField` | A Tx form field whose widget carries a /PMD (PaperMetaData) dictionary — an Acrobat paper-barcode field. |
      | `ButtonField` | Push-button form field (FT=Btn with Pushbutton flag set). |
      | `CheckboxField` | Aspose.Pdf.Forms.CheckboxField represents a checkbox form field in a PDF document that allows users to select or clear an option. |
      | `ChoiceField` | Aspose.Pdf.Forms.ChoiceField represents a form field that lets users select one or more options from a list of choices. |
      | `ComboBoxField` | Combo box (drop-down) form field — a ChoiceField with the Combo flag set. |
      | `DateField` | A text field presenting a date with a popup JavaScript calendar. |
      | `DocMDPSignature` | Pairs a Signature (which carries the signing certificate) with the DocMDPAccessPermissions level a certifying signature will impose. |
      | `ExternalSignature` | A detached PKCS#7 signature configuration built around an already-loaded platform certificate — typically from the OS certificate store, a smartcard or an HSM — rather than a PFX file. |
      | `Field` | Represents a form field. |
      | `FlattenSettings` | Settings for Flatten(FlattenSettings). |
      | `Forms.Form` | Represents the interactive form (AcroForm) of a PDF document. |
      | `FormFieldBuilder` | Creates new interactive form fields and adds them to a PDF document. |
      | `IconFit` | PDF Annotation Handler "IF" entry — icon-fit dictionary for push-button widgets. |
      | `ListBoxField` | List box form field — a ChoiceField without the Combo flag. |
      | `Option` | Class represents option of choice field. |
      | `OptionCollection` | Mutable collection of Option values backed by the owning ChoiceField's /Opt entry. |
      | `PKCS1` | Aspose.Pdf.Forms.PKCS1 defines the PKCS#1 standard for RSA cryptographic signatures in PDF documents. |
      | `PKCS7` | Aspose.Pdf.Forms.PKCS7 defines the PKCS#7 standard for cryptographic message syntax used in digital signatures. |
      | `PKCS7Detached` | A detached PKCS#7 (CMS) signature configuration. |
      | `RadioButtonField` | Aspose.Pdf.Forms.RadioButtonField represents a radio button form field that allows users to select only one option from a group. |
      | `RadioButtonGroup` | A logical radio-button group — a set of mutually exclusive options sharing the same field name in the AcroForm hierarchy. |
      | `RadioButtonOption` | A single option within a radio button group. |
      | `RadioButtonOptionField` | One option of a RadioButtonField. |
      | `RichTextBoxField` | A Tx form field that carries the rich-text flag (bit 26 of /Ff). |
      | `Signature` | Represents a digital signature in a PDF document. |
      | `SignatureCustomAppearance` | Visual-layout knobs for a signature's appearance stream — font, size, padding and which signer metadata strings are rendered inside the widget annotation. |
      | `SignatureField` | Aspose.Pdf.Forms.SignatureField represents a digital signature field in a PDF document that enables secure signing. |
      | `TextBoxField` | Aspose.Pdf.Forms.TextBoxField represents a text box form field where users can enter or edit text content. |
      | `XFA` | XmlNode-based accessor for the document's XFA packets (template / datasets / config / form / xdp). |
      | `XfaAccessor` | Provides indexer access to XFA field values by path. |
      | `XfaField` | Represents a single field of an XFA form addressed by its template SOM path (e.g. |
      | `ExponentialFunction` | Exponential interpolation function (§7.10.3). |
      | `PdfFunction` | Abstract base for all PDF function types. |
      | `PostScriptEvaluator` | PostScript calculator evaluator for Type 4 PDF functions (PDF32000 §7.10.5). |
      | `PostScriptFunction` | PostScript calculator function (§7.10.5). |
      | `SampledFunction` | Sampled function (§7.10.2): lookup table with linear interpolation. |
      | `StitchingFunction` | Stitching function (§7.10.4): combines sub-functions over sub-domains. |
      | `GraphInfo` | Stroke / fill settings for drawable shapes and table-cell borders. |
      | `Group` | Group / blending colour space dictionary (PDF 32000 §11.6.5). |
      | `ObjectKey` | Identifies a PDF indirect object by its object number and generation number. |
      | `HeaderArtifact` | Represents a header artifact — a page-level running head tagged Artifact /Subtype /Header (PDF 32000 §14.8.2.2). |
      | `HeaderFooter` | Represents a header or footer that can be applied to PDF pages. |
      | `Heading` | Represents a TOC heading entry that links to a destination page. |
      | `HeadingLevels` | Heading-level recognition tuning for AutoTaggingSettings. |
      | `HtmlFragment` | Represents an HTML content fragment that can be added to PDF pages via HeaderFooter.Paragraphs or Page.Paragraphs. |
      | `HtmlImageSavingInfo` | Context passed to a per-image saving callback. |
      | `HtmlLoadOptions` | Options for loading HTML content into PDF. |
      | `HtmlPageMarkupSavingInfo` | Context passed to a HtmlPageMarkupSavingStrategy callback. |
      | `HtmlSaveOptions` | Options for saving a PDF document as HTML. |
      | `Hyperlink` | Base type for hyperlinks attached to text fragments or annotations. |
      | `IDocumentFontUtilities` | Per-document font helper contract — implemented by FontUtilities. |
      | `IIndexBitmapConverter` | Aspose.Pdf.IIndexBitmapConverter provides an interface for converting indexed bitmaps during PDF processing. |
      | `IOperatorSelector` | Visitor interface for Accept(IOperatorSelector). |
      | `IWarningCallback` | Aspose.Pdf.IWarningCallback defines a contract for handling warnings that occur during PDF processing operations. |
      | `Id` | Pair of byte strings that make up the /ID array in the PDF trailer. |
      | `Image` | Aspose.Pdf.Image represents an image object that can be embedded in a PDF document. |
      | `ImageCollection` | Collection of image XObjects on a page. |
      | `ImagePlacement` | Represents an image placement found on a PDF page — the position, size, and resolution of an image XObject as it appears on the page (after CTM transformation). |
      | `ImagePlacementAbsorber` | Absorbs image placement information from PDF pages. |
      | `ImagePlacementCollection` | A collection of ImagePlacement items. |
      | `ImageStamp` | Adds an image to a PDF page. |
      | `ImageXObject` | Represents an image XObject found in a PDF page's resources. |
      | `IncorrectFontUsageException` | Thrown during text extraction when the content stream issues a text-showing operator (Tj/TJ/'/") while no font is set in the current graphics state — i.e. |
      | `InterruptMonitor` | Aspose.Pdf.InterruptMonitor allows monitoring and interrupting long-running PDF processing operations. |
      | `InvalidFormTypeOperationException` | Thrown when an operation is attempted on the wrong form type (e.g. |
      | `InvalidPasswordException` | Thrown when a password-protected operation is attempted without supplying a valid password (e.g. |
      | `InvalidPdfFileFormatException` | Thrown when a stream cannot be opened as a PDF (bad header, truncated file, or otherwise unrecognisable as PDF). |
      | `JavaScriptCollection` | Document-level JavaScript scripts (PDF spec §12.6.4.16). |
      | `LaunchActionOperationConverter` | Aspose.Pdf.LaunchActionOperationConverter provides functionality to convert launch action operations during PDF processing. |
      | `Layer` | A PDF layer (Optional Content Group) as exposed through Layers. |
      | `LayerCollection` | Represents the collection of layers on a specific page. |
      | `LayerEntry` | Represents a layer being built by OptionalContentBuilder. |
      | `LevelFormat` | Per-heading-level TOC formatting descriptor (line-dash style, margins, indent, text state). |
      | `LoadOptions` | Aspose.Pdf.LoadOptions specifies settings that control how a PDF document is loaded and initialized. |
      | `LocalHyperlink` | Hyperlink that jumps to another paragraph or page in the same document. |
      | `AnnotElement` | Aspose.Pdf.LogicalStructure.AnnotElement represents an annotation element in the logical structure of a PDF document. |
      | `ArtElement` | Aspose.Pdf.LogicalStructure.ArtElement represents an art element in the logical structure of a PDF document. |
      | `AttributeName` | A typed value for a standard attribute name (the /Name-valued entries in an attribute object, e.g. |
      | `BibEntryElement` | Aspose.Pdf.LogicalStructure.BibEntryElement represents a bibliographic entry element in the logical structure of a PDF document. |
      | `BlockQuoteElement` | Aspose.Pdf.LogicalStructure.BlockQuoteElement represents a block quote element in the logical structure of a PDF document. |
      | `CaptionElement` | Aspose.Pdf.LogicalStructure.CaptionElement represents a caption element in the logical structure of a PDF document. |
      | `CodeElement` | Aspose.Pdf.LogicalStructure.CodeElement represents a code element in the logical structure of a PDF document. |
      | `DivElement` | Aspose.Pdf.LogicalStructure.DivElement represents a division element in the logical structure of a PDF document. |
      | `DocumentElement` | The document root structure element (/S Document). |
      | `LogicalStructure.Element` | Common base for nodes in the logical-structure tree. |
      | `ElementList` | An ordered list of structure elements, as returned by ChildElements. |
      | `LogicalStructure.FigureElement` | Aspose.Pdf.LogicalStructure.FigureElement represents a figure element in the logical structure of a PDF document. |
      | `FormElement` | Aspose.Pdf.LogicalStructure.FormElement represents a form element in the logical structure of a PDF document. |
      | `FormulaElement` | Aspose.Pdf.LogicalStructure.FormulaElement represents a formula element in the logical structure of a PDF document. |
      | `HeaderElement` | Aspose.Pdf.LogicalStructure.HeaderElement represents a header element in the logical structure of a PDF document. |
      | `HeaderElementTextConflictException` | Thrown at save when a header element linked via LinkTocPageTitleToHeaderElement carries its own text that differs from the TOC page's TocInfo title — the two would render conflicting navigation titles. |
      | `ITextElement` | A structure element that can carry inline text content (written to the element's /ActualText entry). |
      | `IllustrationElement` | Abstract base for illustration-kind structure elements (Figure, Formula). |
      | `IndexElement` | Aspose.Pdf.LogicalStructure.IndexElement represents an index element in the logical structure of a PDF document. |
      | `LinkElement` | Aspose.Pdf.LogicalStructure.LinkElement represents a link element in the logical structure of a PDF document. |
      | `ListElement` | Aspose.Pdf.LogicalStructure.ListElement represents a list element in the logical structure of a PDF document. |
      | `ListLBodyElement` | Aspose.Pdf.LogicalStructure.ListLBodyElement represents the body of a list item in the logical structure of a PDF document. |
      | `ListLIElement` | Aspose.Pdf.LogicalStructure.ListLIElement represents a list item element in the logical structure of a PDF document. |
      | `ListLblElement` | Aspose.Pdf.LogicalStructure.ListLblElement represents a list label element in the logical structure of a PDF document. |
      | `MCRElement` | A marked-content reference leaf (role "MCR") emitted by the auto-tagger to mark where a structure element's page content lives. |
      | `NonStructElement` | Aspose.Pdf.LogicalStructure.NonStructElement represents a non-structural element in the logical structure of a PDF document. |
      | `NoteElement` | Aspose.Pdf.LogicalStructure.NoteElement represents a note element in the logical structure of a PDF document. |
      | `OBJRElement` | An object reference (role "OBJR") — links a structure element to a PDF object on a page (typically an annotation, e.g. |
      | `ParagraphElement` | Aspose.Pdf.LogicalStructure.ParagraphElement represents a paragraph element in the logical structure of a PDF document. |
      | `PartElement` | PartElement represents a logical structure part element in a PDF document, providing methods to manage its children, attributes, and text content. |
      | `PrivateElement` | PrivateElement represents a private logical structure element in a PDF document, supporting standard structure element operations. |
      | `QuoteElement` | QuoteElement represents a quoted content element in a PDF document's logical structure. |
      | `ReferenceElement` | ReferenceElement represents a reference element in a PDF document's logical structure. |
      | `RubyChildElement` | Base for the ruby-annotation content elements (RB, RT, RP) that appear only inside a RubyElement. |
      | `RubyElement` | RubyElement represents a ruby annotation element in a PDF document's logical structure. |
      | `RubyRBElement` | RubyRBElement represents a ruby base element in a PDF document's logical structure. |
      | `RubyRPElement` | RubyRPElement represents a ruby parenthetical element in a PDF document's logical structure. |
      | `RubyRTElement` | RubyRTElement represents a ruby text element in a PDF document's logical structure. |
      | `SectElement` | SectElement represents a section element in a PDF document's logical structure. |
      | `SpanElement` | SpanElement represents a span element in a PDF document's logical structure. |
      | `StructTreeRootElement` | The /StructTreeRoot wrapper at the top of the logical- structure tree. |
      | `StructureAttribute` | A single tagged-PDF structure attribute (key plus one typed value). |
      | `StructureAttributes` | An owner-scoped set of StructureAttribute objects attached to a structure element (one PDF attribute dictionary with a fixed /O owner). |
      | `StructureElement` | Base class for tagged-PDF logical-structure elements. |
      | `StructureElementAttributes` | The attribute manager exposed by Attributes. |
      | `StructureTextState` | Text-state snapshot used to format inline structure-element runs. |
      | `StructureType` | A structure-type role (the /S entry value), exposed via S. |
      | `StructureTypeCategory` | Category of a standard structure type per ISO 32000-1 §14.8.4: grouping elements, block-level structure elements (BLSEs), inline-level structure elements (ILSEs) and illustration elements. |
      | `StructureTypeStandard` | The PDF standard structure types (ISO 32000-1 Tables 333–337), exposed as singletons so StructureType reads compare by identity. |
      | `TOCElement` | TOCElement represents a table of contents element in a PDF document's logical structure. |
      | `TOCIElement` | TOCIElement represents a table of contents item element in a PDF document's logical structure. |
      | `TOCpageHasNoTitleException` | Thrown by LinkTocPageTitleToHeaderElement when the TOC page's TocInfo carries no title — the tagged-TOC navigation header must mirror an existing page title (PDF/UA-1 tagged TOC support). |
      | `TableElement` | TableElement represents a table element in a PDF document's logical structure. |
      | `TableTBodyElement` | TableTBodyElement represents a table body element in a PDF document's logical structure. |
      | `TableTDElement` | TableTDElement represents a table data cell element in a PDF document's logical structure. |
      | `TableTFootElement` | TableTFootElement represents a table footer element in a PDF document's logical structure. |
      | `TableTHElement` | TableTHElement represents a table header cell element in a PDF document's logical structure. |
      | `TableTHeadElement` | TableTHeadElement represents a table header element in a PDF document's logical structure. |
      | `TableTRElement` | TableTRElement represents a table row element in a PDF document's logical structure. |
      | `WarichuChildElement` | Base for the warichu content elements (WT, WP) that appear only inside a WarichuElement. |
      | `WarichuElement` | WarichuElement represents a warichu (inline ruby) element in a PDF document's logical structure. |
      | `WarichuWPElement` | WarichuWPElement represents a warichu with parenthetical element in a PDF document's logical structure. |
      | `WarichuWTElement` | WarichuWTElement represents a warichu with text element in a PDF document's logical structure. |
      | `MarginInfo` | Represents margin information for page elements. |
      | `MarginPartStyle` | One side of a MarginInfo: either a fixed PDF-point value or an auto-fit hint. |
      | `Matrix` | Represents a 3x3 transformation matrix [a b 0; c d 0; e f 1]. |
      | `Matrix3D` | 4×3 affine 3D matrix (public-API shape for PDF 3D camera positioning). |
      | `MdLoadOptions` | Options for loading Markdown files as PDF documents. |
      | `MergeOptions` | Merge tuning knobs honored by Merge(MergeOptions, Document[]) and friends. |
      | `Metadata` | XMP metadata accessor exposed by per-resource members such as Metadata. |
      | `MissingOptionalDependencyException` | Thrown when a feature needs an optional dependency the consuming application has not referenced - a NuGet package the library deliberately does not force on every consumer, such as System.Drawing.Common for printing. |
      | `NamedDestination` | Represents a named destination in the document (PDF32000 §12.3.2.3). |
      | `NamedDestinationCollection` | Collection of named destinations in the document. |
      | `Note` | A footnote or endnote attached to a TextFragment. |
      | `OcspSettings` | OCSP (Online Certificate Status Protocol) settings for the signer's revocation-check / OCSP-stapling path. |
      | `Pdf.Operator` | Top-level base for all PDF content-stream operators. |
      | `OperatorCollection` | Collection of content stream operators for a page. |
      | `OperatorSelector` | Visitor that filters content-stream operators by concrete type. |
      | `BDC` | BDC — Begin marked content with properties. |
      | `BI` | BI — Begin inline-image object. |
      | `BMC` | BMC — Begin marked-content sequence (no properties). |
      | `BT` | BT — Begin text object. |
      | `BX` | BX — Begin compatibility section. |
      | `BasicSetColorAndPatternOperator` | Common base for SCN/scn (basic color or pattern). |
      | `BasicSetColorOperator` | Common base for the rg/RG/g/G/k/K family. |
      | `BlockTextOperator` | Common base for the BT / ET delimiters. |
      | `Clip` | W — Set clipping path (nonzero winding rule). |
      | `ClosePath` | h — Close subpath. |
      | `ClosePathEOFillStroke` | b* — Close, fill, and stroke path (even-odd rule). |
      | `ClosePathFillStroke` | b — Close, fill, and stroke path (nonzero winding rule). |
      | `ClosePathStroke` | s — Close and stroke path. |
      | `ConcatenateMatrix` | cm — Concatenate matrix to CTM. |
      | `CurveTo` | c — Append cubic Bézier curve with three control points. |
      | `CurveTo1` | v — Append cubic Bézier curve; first control point is current point. |
      | `CurveTo2` | y — Append cubic Bézier curve; second control point coincides with final point. |
      | `DP` | DP — Designate marked-content point with property list. |
      | `Do` | Do — Invoke named XObject. |
      | `EI` | EI — End inline-image object. |
      | `EMC` | EMC — End marked content. |
      | `EOClip` | W* — Set clipping path (even-odd rule). |
      | `EOFill` | f* — Fill path (even-odd rule). |
      | `EOFillStroke` | B* — Fill and stroke path (even-odd rule). |
      | `ET` | ET — End text object. |
      | `EX` | EX — End compatibility section. |
      | `EndPath` | n — End path without filling or stroking. |
      | `Fill` | f — Fill path (nonzero winding rule). |
      | `FillStroke` | B — Fill and stroke path (nonzero winding rule). |
      | `GRestore` | Q — Restore graphics state. |
      | `GS` | gs — Set parameters from named ExtGState resource. |
      | `GSave` | q — Save graphics state. |
      | `GlyphPosition` | One element of a TJ-style glyph-position array: a string with an optional preceding/following position adjustment (in 1/1000 text units). |
      | `ID` | ID — Begin image data (after the inline-image dictionary). |
      | `LineTo` | l — Append straight line segment to (X, Y). |
      | `MP` | MP — Designate marked-content point (no properties). |
      | `MoveTextPosition` | Td — Move text position: translate text origin by (X, Y). |
      | `MoveTextPositionSetLeading` | TD — Move text position and set leading: translate by (X, Y) and set leading to -Y. |
      | `MoveTo` | m — Begin new subpath at (X, Y). |
      | `MoveToNextLine` | T* — Move to next line using current leading. |
      | `MoveToNextLineShowText` | ' — Move to next line and show text. |
      | `ObsoleteFill` | F — Fill path (deprecated; equivalent to f). |
      | `Operators.Operator` | Back-compat alias for Operator kept so existing using Aspose.Pdf.Operators; code and the concrete operator subclasses (GSave, BT, ET, …) in this namespace continue to bind. |
      | `Re` | re — Append rectangle (X, Y, Width, Height) as a complete subpath. |
      | `SelectFont` | Tf — Select font and size. |
      | `SetAdvancedColor` | scn — Set color (with optional pattern name) in current color space (non-stroking). |
      | `SetAdvancedColorStroke` | SCN — Set color (with optional pattern name) in current color space (stroking). |
      | `SetCMYKColor` | k — Set CMYK fill color. |
      | `SetCMYKColorStroke` | K — Set CMYK stroke color. |
      | `SetCharWidth` | d0 — Set glyph width in a Type 3 font. |
      | `SetCharWidthBoundingBox` | d1 — Set glyph width and bounding box in a Type 3 font. |
      | `SetCharacterSpacing` | Tc — Set character spacing. |
      | `SetColor` | sc — Set color in current color space (non-stroking). |
      | `SetColorOperator` | Common base for the SC/SCN/sc/scn family. |
      | `SetColorRenderingIntent` | ri — Set color rendering intent. |
      | `SetColorSpace` | cs — Set color space for non-stroking operations. |
      | `SetColorSpaceStroke` | CS — Set color space for stroking operations. |
      | `SetColorStroke` | SC — Set color in current color space (stroking). |
      | `SetDash` | d — Set dash pattern. |
      | `SetFlat` | i — Set flatness tolerance. |
      | `SetGlyphsPositionShowText` | TJ — Show text with individual glyph positioning (array of strings and numeric adjustments). |
      | `SetGray` | g — Set gray fill color. |
      | `SetGrayStroke` | G — Set gray stroke color. |
      | `SetHorizontalTextScaling` | Tz — Set horizontal text scaling. |
      | `SetLineCap` | J — Set line cap style. |
      | `SetLineJoin` | j — Set line join style. |
      | `SetLineWidth` | w — Set line width. |
      | `SetMiterLimit` | M — Set miter limit. |
      | `SetRGBColor` | rg — Set RGB fill color. |
      | `SetRGBColorStroke` | RG — Set RGB stroke color. |
      | `SetSpacingMoveToNextLineShowText` | " — Set word/char spacing, move to next line, and show text. |
      | `SetTextLeading` | TL — Set text leading. |
      | `SetTextMatrix` | Tm — Set text matrix. |
      | `SetTextRenderingMode` | Tr — Set text rendering mode. |
      | `SetTextRise` | Ts — Set text rise. |
      | `SetWordSpacing` | Tw — Set word spacing. |
      | `ShFill` | sh — Paint shading specified by named resource. |
      | `ShowText` | Tj — Show text string. |
      | `Stroke` | S — Stroke path. |
      | `TextOperator` | Common base for all text-related operators (BT/ET, Tx state, text-show, text-place). |
      | `TextPlaceOperator` | Common base for text-positioning operators (Td, TD, T*, Tm). |
      | `TextShowOperator` | Common base for text-showing operators (Tj, TJ, ', "). |
      | `TextStateOperator` | Common base for text-state operators (Tc, Tw, Tz, TL, Tf, Tr, Ts). |
      | `Opi` | Open Prepress Interface (OPI) metadata wrapper for an XForm. |
      | `ImageCompressionOptions` | Image-compression sub-options for OptimizationOptions. |
      | `Optimization.OptimizationOptions` | Class which describes document optimization algorithm. |
      | `Pdf.OptimizationOptions` | Document-scoped alias for OptimizationOptions. |
      | `OptimizedMemoryStream` | A growable in-memory stream that can exceed the 2 GB single-array limit of MemoryStream by storing data in fixed-size chunks. |
      | `OptionalContentBuilder` | Creates optional content groups (layers) in a PDF document. |
      | `OptionalContentGroup` | Represents an Optional Content Group (layer) in a PDF document. |
      | `OptionalContentProperties` | Collection of Optional Content Groups (layers) in a document. |
      | `OutlineBuilder` | Builder for creating bookmarks (outlines) programmatically. |
      | `OutlineCollection` | OutlineCollection manages the collection of outlines in a PDF document. |
      | `OutlineItem` | Represents a bookmark (outline item) in the document outline hierarchy. |
      | `OutlineItemBuilder` | Builder for a single outline item with fluent API. |
      | `OutlineItemCollection` | The document outline (bookmark tree). |
      | `Outlines` | Base class for a bookmark (outline) collection. |
      | `OutputIntent` | Represents a PDF OutputIntent entry (PDF32000 §14.11.5). |
      | `OutputIntents` | Collection of OutputIntent entries on the document catalog's /OutputIntents array. |
      | `Page` | Represents a page in a PDF document. |
      | `PageActionCollection` | Per-page additional actions (PDF 32000 §12.6.3 /AA entries), backed by the page dictionary so assigned actions survive save → reload. |
      | `PageCollection` | Collection of pages in a PDF document. |
      | `PageCollectionExtensions` | Pagination stamping over a PageCollection (Bates numbering etc.). |
      | `PageExtensions` | Extension methods over Page that manipulate the page content stream. |
      | `PageInfo` | Container for page dimension and margin properties. |
      | `PageLabel` | Represents a page label range. |
      | `PageLabelBuilder` | Builder for creating page labels on a document. |
      | `PageLabelCollection` | Collection of page label ranges from the /PageLabels number tree. |
      | `PageNumberStamp` | A stamp that adds page numbers to PDF pages. |
      | `PageResources` | Provides access to a page's resource collections (fonts, images). |
      | `Pdf.PageSize` | Standard page size constants (dimensions in points, portrait orientation). |
      | `PageSizeInfo` | Page size configuration for document loading. |
      | `PageTransition` | Represents a page transition effect (PDF32000 §12.4.4). |
      | `PaginationArtifact` | Base class for artifacts stamped across a page range by AddPagination — e.g. |
      | `Paragraphs` | Container for paragraph-level content (text fragments, tables, images, header/footer fragments, etc.) attached to a Page, Cell, HeaderFooter or FloatingBox. |
      | `PdfANonSpecificationFlags` | PDF/A non-spec compliance toggles. |
      | `PdfASymbolicFontEncodingStrategy` | Strategy for re-encoding symbolic fonts during PDF/A conversion. |
      | `PdfException` | Represents errors that occur during PDF application execution. |
      | `PdfExceptionMessages` | Centralised message strings surfaced by the library's exceptions and security diagnostics. |
      | `PdfFormatConversionOptions` | Options for converting a PDF document to a specific PDF/A conformance level. |
      | `PdfPageStamp` | Stamps the content of one PDF page onto another page. |
      | `PdfSaveOptions` | PdfSaveOptions provides settings for saving a document to PDF format. |
      | `PdfTextDecodingException` | Thrown when text content cannot be decoded — e.g. |
      | `MarkdownSaveOptions` | Save options for the PDF → Markdown converter (doc.Save(path, new MarkdownSaveOptions())). |
      | `Pdf.Point` | Represents a point in 2D space. |
      | `Point3D` | Represents a point in 3D space (used by 3D annotations). |
      | `PolygonsHelper` | Polygon and rectangle geometry utilities — point/polygon containment, segment hit-testing and rectangle/polygon classification. |
      | `CustomPrintEventArgs` | Event args raised by PdfViewer.CustomPrint. |
      | `PageSettings` | Per-page print settings (paper size / orientation / margins). |
      | `PaperSize` | PaperSize represents the paper size used for printing a PDF document. |
      | `PdfQueryPageSettingsEventArgs` | Event args supplied to PdfViewer.PdfQueryPageSettings. |
      | `PrinterSettings` | Printer-side settings (printer name, copies, range). |
      | `PrintingOptionalDependencyGuard` | Guards the printing API's optional platform dependency: on modern .NET the printing pipeline needs System.Drawing.Common, which ships as a NuGet package the consuming application must reference itself. |
      | `StartEndPageEventArgs` | Event args raised by PdfViewer.StartPage / EndPage. |
      | `ProgressEventHandlerInfo` | ProgressEventHandlerInfo provides information about the progress of an operation. |
      | `QueueItem` | One entry in the priority queue — names a single (platformID, platformSpecificID) cmap subtable. |
      | `RawOperator` | An unparsed operator token from a content stream — used as a fallback for operators not covered by the typed Operators hierarchy. |
      | `Pdf.Rectangle` | Represents a rectangle defined by lower-left and upper-right coordinates (PDF user-space convention: Y increases upward). |
      | `Pdf.RenderingOptions` | Rendering options used by the page-to-image converters (PngDevice, JpegDevice, PdfConverter, …). |
      | `RepairOptions` | Options describing what repair is needed. |
      | `ResourceLoadingResult` | ResourceLoadingResult indicates the result of loading a resource during document processing. |
      | `ResourceSavingInfo` | ResourceSavingInfo provides information about saving a resource during document export. |
      | `Resources` | Type alias for PageResources, matching the Resources class name. |
      | `RgbToDeviceGrayConversionStrategy` | Converts all RGB color operators and image color spaces on a page to DeviceGray. |
      | `Row` | Row represents a row in a table within a PDF document. |
      | `Rows` | A collection of rows in a table. |
      | `SanitizationException` | Thrown when a document structure is recognised as a signature-forgery attack (e.g. |
      | `SaveOptions` | SaveOptions provides base settings for saving a document. |
      | `BitString` | A variable-length bit string supporting bitwise operations, concatenation, substring extraction, and conversion to/from bytes and hex. |
      | `CertificateEncryptionOptions` | Bundles the public certificate used to encrypt a document and the private-key store needed to decrypt it. |
      | `EncryptionInfo` | Information about a document's encryption. |
      | `EncryptionParameters` | Parameters parsed from the document's /Encrypt dictionary and passed to Initialize when a custom security handler takes over for an alternative /Filter. |
      | `HashAlgorithmFactory` | Creates a HashAlgorithm for a DigestHashAlgorithm. |
      | `HiddenDataSanitizationOptions` | Options controlling which categories of hidden or sensitive data HiddenDataSanitizer strips from a document. |
      | `HiddenDataSanitizer` | Strips hidden or sensitive data (annotations, JavaScript/actions, attachments, optional-content layers, form fields, metadata) from a document per the supplied HiddenDataSanitizationOptions. |
      | `ICustomSecurityHandler` | Pluggable custom security handler — implement this to handle non-Standard /Filter entries (e.g. |
      | `MathExtensions` | Small integer math helpers shared by the security primitives. |
      | `PdfCertificate` | Represents a certificate + private key for PDF signing. |
      | `PdfSigner` | Signs PDF documents with X.509 certificates using PKCS#7/CMS detached signatures. |
      | `Sha3_256` | SHA3-256 (FIPS 202) — own Keccak implementation. |
      | `Sha3_384` | SHA3-384 (FIPS 202) — own Keccak implementation. |
      | `Sha3_512` | SHA3-512 (FIPS 202) — own Keccak implementation. |
      | `SignatureAlgorithmInfo` | Algorithm + digest + envelope-standard triple extracted from a PDF signature value. |
      | `SignatureAppearance` | Defines the visual appearance for a digital signature annotation. |
      | `SignatureLengthMismatchException` | Thrown when a produced signature does not fit the space reserved for the Contents hex string because length estimation was skipped (AvoidEstimatingSignatureLength) and the actual signature exceeded the fixed reservation. |
      | `SignatureOptions` | Options for signing a PDF document. |
      | `TimestampAlgorithmInfo` | Algorithm info for an RFC 3161 document-timestamp signature (/SubFilter ETSI.RFC3161). |
      | `ValidationOptions` | Tunes the verification policy used by Verify(SignatureName, ValidationOptions, out ValidationResult). |
      | `ValidationResult` | Verification outcome produced by Verify(SignatureName, ValidationOptions, out ValidationResult). |
      | `VerificationResult` | Result of a non-throwing signature verification (SignatureName, out VerificationResult)). |
      | `AxialShading` | Axial (linear) shading (Type 2, §8.7.4.3). |
      | `CoonsPatchShading` | Coons patch mesh (Type 6, §8.7.4.5.7). |
      | `FreeFormGouraudShading` | Free-form Gouraud-shaded triangle mesh (Type 4, §8.7.4.5.5). |
      | `FunctionBasedShading` | Function-based shading (Type 1, §8.7.4.2). |
      | `LatticeFormGouraudShading` | Lattice-form Gouraud-shaded triangle mesh (Type 5, §8.7.4.5.6). |
      | `Pattern` | Abstract base for all PDF pattern types. |
      | `RadialShading` | Radial (circular) shading (Type 3, §8.7.4.4). |
      | `ShadingBase` | Abstract base for all PDF shading types (§8.7.4.1). |
      | `ShadingPattern` | Shading pattern (PatternType 2, §8.7.3.2): smooth gradient. |
      | `TensorPatchShading` | Tensor-product patch mesh (Type 7, §8.7.4.5.8). |
      | `TilingPattern` | Tiling pattern (PatternType 1, §8.7.3.1): repeating tile. |
      | `Pdf.Stamp` | Top-level public-API-shape abstract base for stamps applied to a Page via AddStamp(Stamp). |
      | `Stamps.Stamp` | Base class for stamps that can be applied to PDF pages. |
      | `Stamps.TextStamp` | A text stamp that can be applied to PDF pages. |
      | `WatermarkStamp` | A watermark stamp that draws diagonal text across the page. |
      | `Structure.Element` | A node in a PDF's logical-structure tree (the /StructTreeRoot /K subtree, plus their /K descendants). |
      | `ElementCollection` | A live, mutable collection of structure elements held under a parent's /K array. |
      | `Structure.FigureElement` | A figure structure element (/S = "Figure") that wraps a raster or vector picture. |
      | `RootElement` | Top-level wrapper for the PDF /StructTreeRoot dictionary. |
      | `StructElement` | A generic structure element (anything other than the recognised typed subclasses). |
      | `TextElement` | A text-bearing structure element (Span / P / Quote / Note / Reference / BibEntry). |
      | `SvgImageSavingInfo` | SvgImageSavingInfo provides information about saving an SVG image during document export. |
      | `SvgLoadOptions` | Options for loading SVG files as PDF documents. |
      | `SvgSaveOptions` | SvgSaveOptions provides settings for saving a document to SVG format. |
      | `Table` | Represents a table that can be added to a PDF page. |
      | `ITaggedContent` | The author-facing surface for a tagged PDF document. |
      | `MarkedContentInfo` | Information about a marked content sequence, used to wrap content in BDC/EMC operators. |
      | `PositionSettings` | Layout/position settings for a tagged structure element, passed to AdjustPosition. |
      | `StructTreeElement` | Read-only view of a single structure element in a tagged document's structure tree: its role, accessibility text, attributes, marked-content references, and child elements. |
      | `StructTreeRoot` | Read-only view of a tagged document's structure-tree root (/StructTreeRoot). |
      | `StructureElementBuilder` | Builder for creating a structure element with children and marked content. |
      | `StructureTreeBuilder` | Builds a logical structure tree for a tagged PDF document. |
      | `TaggedContent` | Provides access to tagged PDF metadata (title, language) and the logical structure tree. |
      | `TaggedException` | Exception thrown when a tagged PDF structure operation violates PDF spec constraints. |
      | `AbsorbedCell` | Represents a cell in an absorbed table. |
      | `AbsorbedRow` | Represents a row in an absorbed table. |
      | `AbsorbedTable` | Represents a table detected on a PDF page. |
      | `CharInfo` | Per-character layout information (glyph rectangle + page position). |
      | `CharInfoCollection` | Collection of CharInfo entries — supports the public surface used by TextSegment.Characters but stays empty by default. |
      | `CustomFontSubstitutionBase` | CustomFontSubstitutionBase provides a base class for implementing custom font substitution logic, allowing developers to define how fonts are replaced during document processing by overriding its TrySubstitute method. |
      | `ExternalFontCache` | Manages the set of folders searched for external (non-embedded) TrueType/OpenType faces during rendering and conversion. |
      | `FileFontSource` | A font source backed by a single font file on disk. |
      | `FolderFontSource` | A font source that searches fonts in a specific directory. |
      | `Font` | Type alias for FontInfo, matching the Font class name. |
      | `FontAbsorber` | Collects all fonts used in a PDF document or a single page. |
      | `FontCollection` | Collection of fonts referenced by a page. |
      | `FontData` | Represents a font with its name and type. |
      | `FontEmbedder` | Embeds TrueType fonts into PDF documents for custom text rendering. |
      | `FontInfo` | Information about a font used in a PDF. |
      | `FontRepository` | Provides access to fonts available for use in PDF documents. |
      | `FontSource` | Base class for font sources. |
      | `FontSourceCollection` | A collection of font sources used by FontRepository. |
      | `FontSubstitution` | Base type for font substitutions held in FontSubstitutionCollection. |
      | `FontSubstitutionCollection` | FontSubstitutionCollection manages a collection of font substitution rules, supporting operations such as adding, removing, and checking for specific substitutions via its Add, Remove, Contains, and other collection methods. |
      | `IFontOptions` | Per-font runtime options (currently only the font-embedding error toggle). |
      | `MarkupParagraph` | A paragraph within a markup section. |
      | `MarkupSection` | A section of text on a page — a spatially coherent group of lines. |
      | `MemoryFontSource` | A font source backed by an in-memory font byte buffer. |
      | `OneBasedList` | Read-only list with 1-based indexer, matching the public API. |
      | `OriginalFontSpecification` | OriginalFontSpecification describes the original font being substituted, exposing properties such as OriginalFontName, IsEmbedded, and IsSubstitutionUnavoidable to provide context for font replacement decisions. |
      | `PageMarkup` | Represents the text markup of a single page, organized into sections. |
      | `ParagraphAbsorber` | Absorbs text from PDF pages and organizes it into sections and paragraphs. |
      | `ParagraphAbsorberOptions` | Options for ParagraphAbsorber controlling section detection thresholds. |
      | `PdfFontView` | Thin engine-font view exposed for public-API compatibility (Font.iPdfFont). |
      | `PhysicalTextSegment` | Physical (page-space) projection of an absorbed TextSegment. |
      | `Position` | Position represents a coordinate in a document, storing horizontal and vertical offsets via XIndent and YIndent, and supports equality comparison and string representation through its Equals, GetHashCode, and ToString methods. |
      | `RegexManager` | Global configuration for regular-expression text search (for example via TextFragmentAbsorber). |
      | `SimpleFontSubstitution` | SimpleFontSubstitution defines a straightforward font replacement rule mapping an original font name to a substitution font name, with properties OriginalFontName and SubstitutionFontName, and a TrySubstitute method to apply the substitution. |
      | `SystemFontSource` | A font source that searches the system's installed fonts. |
      | `TabStop` | Represents a single tab stop position. |
      | `TabStops` | A collection of tab stop positions for text layout. |
      | `TableAbsorber` | Detects and extracts tables from PDF pages by analyzing text positions and line drawing operations. |
      | `TextAbsorber` | Extracts text from PDF pages by parsing content streams. |
      | `TextBuilder` | Appends text fragments to a PDF page by registering fonts in the page resources and writing content stream operators. |
      | `TextEditOptions` | Options that control how text is edited (replaced, font-substituted, language-transformed). |
      | `TextExtractionError` | Diagnostic produced by TextFragmentAbsorber when a page-level extraction fails or partially succeeds. |
      | `TextExtractionErrorLocation` | Structured location of a TextExtractionError within a page's content stream. |
      | `TextExtractionOptions` | Options for text extraction operations. |
      | `TextFormattingOptions` | Represents text formatting options for a TextParagraph. |
      | `TextFragment` | Represents a fragment of text on a page with position and style information. |
      | `TextFragmentAbsorber` | Searches for text fragments on PDF pages, optionally matching a search phrase. |
      | `TextFragmentCollection` | A 1-indexed collection of TextFragment objects, matching the public API. |
      | `TextFragmentState` | Fragment-level text formatting state. |
      | `TextOptions` | Base class for text-processing options. |
      | `TextParagraph` | Represents a text paragraph that can be placed on a page via AppendParagraph. |
      | `TextReplaceOptions` | Options for text replacement operations. |
      | `TextReplacer` | Finds and replaces text in PDF content streams. |
      | `TextSearchOptions` | Options for text search operations in PDF documents. |
      | `TextSegment` | A single segment within a TextFragment. |
      | `TextSegmentCollection` | A 1-indexed collection of TextSegment objects belonging to a TextFragment. |
      | `TextState` | Text formatting state. |
      | `Pdf.TextStamp` | Compat re-export so external tests that reference Aspose.Pdf.TextStamp (from before the type moved to Aspose.Pdf.Stamps) keep compiling. |
      | `TimestampSettings` | RFC 3161 Time-Stamp Authority (TSA) settings consumed by the signer when embedding a timestamp token into the PKCS#7 envelope. |
      | `ToUnicodeProcessingRules` | Rules applied when generating ToUnicode CMaps during conversion. |
      | `TocInfo` | Table of contents information for a page. |
      | `TxtLoadOptions` | Options for loading a plain-text (.txt) file as a PDF document. |
      | `UnifiedSaveOptions` | UnifiedSaveOptions controls how a document is saved in unified save mode, offering settings such as SaveFormat, SaveFullPath, CacheGlyphs, ExtractOcrSublayerOnly, and WarningHandler to customize the output and behavior. |
      | `UnsupportedFontTypeException` | Thrown when a file cannot be opened as a font because its format is not a supported font program (e.g. |
      | `ValidationIssue` | Represents a validation issue found in a PDF document. |
      | `Value` | One typed value pulled out of a CollectionItem dict by TryGet*Value. |
      | `GraphicElement` | Single vector-graphics element (path segment, image, text run) extracted from a PDF content stream. |
      | `GraphicElementCollection` | Mutable collection of GraphicElement entries. |
      | `GraphicsAbsorber` | Extracts the painted vector sub-paths of a page as SubPath elements, each carrying its page-space bounding Rectangle, and Form XObject invocations as XFormPlacement elements whose children are extracted in form space. |
      | `SubPath` | A single painted sub-path extracted from a content stream: its construction operators (in their original user-space coordinates), the CTM in effect when it was drawn, and the painting operator that closed it. |
      | `XFormPlacement` | A Form XObject invocation (Do) found in a content stream. |
      | `ViewerPreferences` | Represents the document's viewer preferences (PDF32000 §12.2). |
      | `WarningInfo` | WarningInfo captures information about a warning generated during document processing, including the WarningMessage, WarningType, and WarningTypeProperty to help diagnose issues encountered during operations. |
      | `Watermark` | Watermark applied to a page. |
      | `WatermarkArtifact` | Represents a watermark artifact that can be added to a PDF page. |
      | `WebHyperlink` | Hyperlink that opens an external URL. |
      | `XForm` | Represents a Form XObject (reusable content stream with its own resources). |
      | `XFormCollection` | Collection of XForm (Form XObject) resources on a page. |
      | `XFormResources` | Resources (fonts, xobjects) on an XForm's stream dict. |
      | `XImage` | Image XObject wrapper exposing the public XImage surface. |
      | `XImageCollection` | Collection of image XObjects on a page, with XImage-typed accessors and editing helpers. |
      | `XmpField` | One named XMP field — a (prefix, local-name) pair plus a typed value. |
      | `XmpMetadata` | Provides access to XMP metadata embedded in the PDF. |
      | `XmpPacketContainer` | The XMP packet exposed by getData; its WorkingPacket yields the live RDF/XML. |
      | `XmpPdfAExtensionField` | One field of a PDF/A XMP-extension structured value type. |
      | `XmpPdfAExtensionObject` | One PDF/A XMP-extension property/value pair. |
      | `XmpPdfAExtensionProperty` | One PDF/A XMP-extension property declaration (name, value, value type, category, description). |
      | `XmpPdfAExtensionSchema` | One PDF/A XMP-extension schema, holding a description and its associated XmpPdfAExtensionObjects. |
      | `XmpPdfAExtensionSchemaDescription` | Human-readable description of a PDF/A XMP extension schema. |
      | `XmpPdfAExtensionValueType` | One PDF/A XMP-extension structured value type, holding its fields. |
      | `XmpValue` | Represents a typed XMP metadata value. |
      | `XmpWorkingPacket` | A working XMP packet; GetXml returns the packet's RDF/XML with rdf:RDF as the document element so callers can XPath into it. |
      | `ZDeflaterOutputStream` | zlib (RFC 1950) deflating output stream — a public helper on the public API surface. |
      | `ZInflaterInputStream` | zlib (RFC 1950) inflating input stream — a public helper on the public API surface. |

      #### Enumerations

      | Enumeration | Description |
      | `AFRelationship` | Relationship between an embedded file and the document content that references it (/AFRelationship, PDF 2.0 §7.11.3). |
      | `ActionType` | The type of a PDF action. |
      | `ActivationEvent` | ActivationEvent represents an event that occurs when an annotation is activated in a PDF document. |
      | `AnnotationFlags` | Annotation flags as defined in PDF spec Table 165. |
      | `AnnotationState` | Review or marked-state of a markup annotation, as defined by PDF 32000 §12.5.6.3 (text annotations, /StateModel + /State entries). |
      | `AnnotationStateModel` | Which state model a AnnotationState belongs to. |
      | `AnnotationType` | Annotation subtype as defined in PDF spec Table 169. |
      | `BorderEffect` | Border effect applied to annotations. |
      | `BorderStyle` | Border style for annotations and fields. |
      | `CapStyle` | Cap style used by ink strokes (free-hand drawings). |
      | `CaptionPosition` | Caption-position within a LineAnnotation. |
      | `CaretSymbol` | Caret-symbol style for CaretAnnotation. |
      | `ColorsOfCMYK` | CMYK channel selector used by ColorBarAnnotation. |
      | `ContentType` | ContentType specifies the type of content associated with an annotation in a PDF document. |
      | `ExplicitDestinationType` | Explicit destination type, mirroring PDF 32000 §12.3.2.2 names. |
      | `FileIcon` | Named-icon style for FileAttachmentAnnotation (/Name entry). |
      | `FractionStyle` | How fractional values are rendered (decimal / fraction / round / truncate). |
      | `FreeTextIntent` | Intent (sub-purpose) of a free-text annotation. |
      | `HighlightingMode` | Highlighting mode for link annotations. |
      | `Justification` | Justification of a free-text annotation's content. |
      | `LightingSchemeType` | Lighting-scheme nominal type (PDF 32000-1 §13.6.4). |
      | `LineEnding` | Line annotation ending styles (/LE entry elements). |
      | `LineIntent` | Line annotation intents (/IT entry). |
      | `PDF3DActivation` | How the embedded 3D model becomes active (PDF 32000-1 §13.6.2, AAS entry of the 3D activation dictionary). |
      | `PolyIntent` | Intent of a polygon or polyline annotation (/IT entry). |
      | `PredefinedAction` | Named viewer commands targeted by a NamedAction (PDF 32000 §12.6.4.11). |
      | `PrinterMarkCornerPosition` | Corner a printer-mark annotation sits in (used by bleed-/trim-marks). |
      | `PrinterMarkSidePosition` | Side a printer-mark annotation sits on. |
      | `PrinterMarksKind` | The set of printer's-mark families AddPrinterMarks generates on a page (PDF 32000 pre-press marks). |
      | `RenderModeType` | Render-mode nominal type (PDF 32000-1 §13.6.5). |
      | `ReplyType` | Reply-relationship between a markup annotation and its InReplyTo target. |
      | `RichTextFontStyles` | Rich-text run styles applied via Color). |
      | `SoundEncoding` | SoundEncoding specifies the encoding format used for audio data in a sound annotation. |
      | `SoundIcon` | SoundIcon specifies the icon used to represent a sound annotation in a PDF document. |
      | `SoundSampleDataEncodingFormat` | SoundSampleDataEncodingFormat specifies the encoding format for audio sample data in a sound annotation. |
      | `StampIcon` | Named stamp-icon style for StampAnnotation (/Name entry, PDF 32000 §12.5.6.14). |
      | `TextAlignment` | Horizontal text alignment for annotation text boxes. |
      | `TextIcon` | Named-icon style for TextAnnotation (PDF 32000 §12.5.6.4 /Name entries). |
      | `AntialiasingProcessingType` | Antialiasing pass applied to the resulting HTML. |
      | `ArtifactSubtype` | Enumerates artifact subtypes. |
      | `ArtifactType` | Enumerates artifact types as defined by PDF 32000 §14.8.2.2. |
      | `BlendMode` | PDF blend modes (Table 136 of PDF 32000-1:2008). |
      | `BorderCornerStyle` | Corner-rounding style for a Table's border box. |
      | `BorderSide` | Specifies which sides of a border to draw. |
      | `BoundsCheckMode` | How a BoundsCheckableList{T} reacts when an item lies outside its container. |
      | `CMapEncodingTableType` | OpenType cmap subtable selector. |
      | `CollectionFieldSubtype` | Predefined collection field subtypes (PDF spec §7.11.5 Table 75). |
      | `ColorSpace` | Top-level device colour-space selector (public surface). |
      | `ColorType` | Represents the color type of a page. |
      | `ColumnAdjustment` | How a Table sizes its columns (API compatibility). |
      | `ComparisonMode` | How the side-by-side comparer treats whitespace when extracting the texts to diff. |
      | `EditOperationsOrder` | When a delete and an insert are emitted for the same position, which one an optimizer places first in the resulting operation sequence. |
      | `Operation` | Lives in Aspose.Pdf.Comparison (not ...Comparison.Diff) to match the reference surface: a caller with both namespaces in scope would otherwise see an ambiguous Operation. |
      | `PathOp` | Path construction operator type. |
      | `PathOperationType` | Represents a type of path operation. |
      | `PathPaintMode` | Represents a single path painting operation. |
      | `ConversionEngines` | Available conversion engines. |
      | `ConvertErrorAction` | Action to take when a conversion error is found. |
      | `ConvertSoftMaskAction` | Soft-mask handling strategy during PDF/A conversion. |
      | `ConvertTransparencyAction` | Action to take when transparency is encountered during PDF/A conversion. |
      | `CryptoAlgorithm` | PDF encryption algorithm. |
      | `DefaultState` | Default visibility state of an Optional Content Group (layer). |
      | `ColorDepth` | Color depth options for TIFF encoding. |
      | `CompressionType` | TIFF compression algorithm options. |
      | `FormPresentationMode` | How form fields are rendered (canonical Production / Editor split). |
      | `ShapeType` | Shape type for TIFF rendering. |
      | `DigestHashAlgorithm` | Digest hash algorithm used by a PDF signature (PKCS#7 messageDigest attribute). |
      | `Direction` | Predominant text-flow direction recorded in the viewer-preferences dictionary. |
      | `ImageFormat` | Specifies the encoding format of an image. |
      | `ExtendedBoolean` | A tri-state Boolean — True/False plus an Undefined sentinel used at deserialization time when a value isn't carried on the PDF dictionary entry. |
      | `ExtractImageMode` | Image extraction mode — matches the public ExtractImageMode surface. |
      | `ExtractTextMode` | Text extraction mode — matches docs.aspose.com "Pure" (0) / "Raw" (1). |
      | `Algorithm` | Encryption algorithm family used by PdfFileSecurity. |
      | `AutoRotateMode` | Auto-rotation behaviour for the PDF viewer. |
      | `BlendingColorSpace` | Blend-mode colour-space hint passed to a Stamp. |
      | `ConcatenateCorruptedFileAction` | How the facade reacts to corrupted input during Concatenate. |
      | `DataType` | Form-data source / destination kind consumed by FormDataConverter. |
      | `DefaultMetadataProperties` | Well-known XMP-basic-schema property keys exposed by PdfXmpMetadata as a typed alternative to raw "xmp:&lt;name&gt;" strings. |
      | `EncodingType` | Encoding type enumeration for FormattedText. |
      | `Facades.FieldType` | The type of an AcroForm field, as used by AddField and related facade operations. |
      | `FontStyle` | Font style enumeration for FormattedText. |
      | `ImageMergeMode` | Layout strategy used by MergeImages when combining multiple input images into a single output image. |
      | `ImportStatus` | Outcome of importing a single field's value. |
      | `KeySize` | Key strength selector paired with Algorithm to pick the concrete RC4/AES variant used by PdfFileSecurity. |
      | `Facades.NoCharacterAction` | What to do when the replacement font lacks a glyph for a character. |
      | `PdfConverterImageFormat` | Image format for PdfConverter output. |
      | `PositioningMode` | Text positioning mode for PdfFileMend. |
      | `PropertyFlag` | Attribute flags applied to a form field via SetFieldAttribute. |
      | `Facades.Scope` | How many matches to replace per call. |
      | `StampType` | Stamp kind exposed by StampInfo. |
      | `SubmitFormFlag` | Format of the data posted by a Submit form action — set per field via SetSubmitFlag. |
      | `Facades.WordWrapMode` | Word wrap mode for PdfFileMend text operations. |
      | `FieldSerializationStatus` | Outcome of serializing a single form field through ExportToJson or ImportFromJson. |
      | `FieldValueType` | Logical value type carried by a CollectionField. |
      | `FileEncoding` | Compression / encoding used for the embedded file stream's data (consumed by Encoding). |
      | `Fixup` | Pre-defined PDF fixup operations accepted by Stream, bool, object[]). |
      | `FontEncodingRules` | Backing-byte enum: the public reflection signature is byte-typed. |
      | `FontSavingModes` | How fonts are emitted into the output HTML. |
      | `FontSubsetStrategy` | Strategy for font subsetting when saving a document. |
      | `BoxShape` | Outer shape of a checkbox / radio-button widget border. |
      | `BoxStyle` | Visual style of the checkbox glyph (/MK /CA mapping). |
      | `DocMDPAccessPermissions` | /DocMDP /TransformParams /P access-permission levels per PDF 32000-1 §12.8.2.2. |
      | `Forms.FieldType` | The type of a form field. |
      | `FormType` | Represents the type of a PDF form. |
      | `IconCaptionPosition` | PDF Annotation Handler "TP" entry — caption / icon layout for push-button widgets. |
      | `ScalingMode` | How a button icon is scaled to fit its rectangle (/MK /IF /SW). |
      | `ScalingReason` | When to scale the icon to fit its rectangle (/MK /IF /S). |
      | `SignDependentElementsRenderingModes` | How widgets dependent on signature appearance are rendered when the form is converted. |
      | `SubjectNameElements` | X.500 distinguished-name components rendered inside the visible signature appearance when UseDigitalSubjectFormat is set. |
      | `Symbology` | Barcode symbology carried by a barcode form field's /PMD (PaperMetaData) dictionary. |
      | `HeadingRecognitionStrategy` | Algorithm used to detect headings during auto-tagging. |
      | `HorizontalAlignment` | Describes horizontal alignment. |
      | `HtmlBorderLineType` | Aspose.Pdf.HtmlBorderLineType specifies the line style used for borders when converting HTML content to PDF. |
      | `HtmlDocumentType` | HTML document flavour produced by HtmlSaveOptions. |
      | `HtmlImageType` | Image type produced for embedded raster content. |
      | `HtmlMarkupGenerationModes` | Granularity of HTML markup produced. |
      | `HtmlMediaType` | Specifies the media type for HTML rendering. |
      | `HtmlPageLayoutOption` | How the generated PDF page layout reacts to wide HTML content. |
      | `ImageDeleteAction` | Action taken by Delete(int, ImageDeleteAction) when the image being removed is still referenced from the page content. |
      | `ImageFileType` | Recognised image-source file types reported by Image.FileType. |
      | `ImageFilterType` | Aspose.Pdf.ImageFilterType specifies the compression algorithm used for images within a PDF document. |
      | `ImageParentTypes` | Container element that hosts an image. |
      | `LaunchActionOperation` | Aspose.Pdf.LaunchActionOperation represents an action that launches an external application or file from a PDF document. |
      | `LettersPositioningMethods` | How letter positions are encoded in the output CSS. |
      | `LoadFormat` | Source format selector used by LoadOptions. |
      | `AttributeKey` | Standard tagged-PDF attribute keys (ISO 32000-1 §14.8.5). |
      | `AttributeOwnerStandard` | Standard owners of a tagged-PDF attribute set (the /O entry of an attribute object, ISO 32000-1 Table 348). |
      | `Pdf.NoCharacterAction` | Action taken when the configured font does not have a glyph for a character in the stamp text. |
      | `NodeLevelResourceType` | NodeLevelResourceType specifies the type of resource associated with a node in a PDF document. |
      | `NumberingStyle` | Numbering style for page labels and auto-sequenced headings. |
      | `LineCap` | Line cap style. |
      | `LineJoin` | Line join style. |
      | `ImageCompressionVersion` | Strategy version for image compression. |
      | `ImageEncoding` | How image streams are re-encoded during optimization. |
      | `PageCoordinateType` | Which page-box the rendered image extents come from. |
      | `PageLayout` | Page-layout entry written to the catalog (PDF32000 Table 28). |
      | `PageLayoutMode` | Page layout mode for the document (PDF32000 §12.2, Table 28). |
      | `PageMode` | Page-mode entry written to the catalog (PDF32000 Table 28). |
      | `PageModeValue` | Page mode specifying how the document should be displayed on opening (PDF32000 §12.2, Table 28). |
      | `ParagraphPositioningMode` | How Left / Top are interpreted. |
      | `PartsEmbeddingModes` | Whether and how secondary parts (CSS / images / fonts) are embedded. |
      | `PasswordType` | Identifies which password (if any) is in effect on an encrypted PDF. |
      | `PdfAStandardVersion` | PdfAStandardVersion indicates the PDF/A standard version used for a document. |
      | `PdfFormat` | PDF format/conformance level for validation and conversion. |
      | `PdfVersion` | PDF specification version targeted by the document header. |
      | `Permissions` | Permissions defines the permission settings for a PDF document. |
      | `PixelFormat` | PixelFormat specifies the pixel format used for images in a PDF document. |
      | `PrintDuplex` | Print-duplex value recorded in viewer preferences (PDF32000 Table 150). |
      | `PrintScaling` | PrintScaling specifies the print scaling behavior for a PDF document. |
      | `ProgressEventType` | Conversion-progress milestones reported via EventType. |
      | `PuaProcessingStrategy` | Strategy for processing Private-Use-Area Unicode characters. |
      | `RasterImagesSavingModes` | How raster images are saved alongside the HTML. |
      | `RectanglePosition` | Position of a rectangle relative to a polygon, as returned by GetRectanglePositionRelativePolygon. |
      | `RemoveFontsStrategy` | Strategy for removing or excluding fonts during conversion. |
      | `ReturnAction` | ReturnAction represents an action that returns to a previous location in a PDF document. |
      | `Rotation` | Rotation specifies the rotation angle applied to a page or element in a PDF document. |
      | `SaveFormat` | Save format enumeration. |
      | `CryptographicStandard` | Cryptographic envelope standard reported by a PDF signature SubFilter entry. |
      | `SignatureAlgorithmType` | Signing-algorithm family extracted from a signature's PKCS#7 signatureAlgorithm OID. |
      | `ValidationMethod` | Revocation-check protocol selector used by ValidationMethod. |
      | `ValidationMode` | How aggressively VerifySignature(string, ValidationOptions, out ValidationResult) reports problems. |
      | `ValidationStatus` | Outcome categories reported by Status. |
      | `VerificationState` | Outcome of a signature verification. |
      | `SegmentAlignStrategy` | Strategy for collapsing adjacent text segments during PDF/A conversion. |
      | `ShadingType` | Shading type — /ShadingType entry. |
      | `Subset` | Which pages of a document a pagination artifact applies to. |
      | `SvgExternalImageType` | Image format used by the SVG embedded-image saver. |
      | `TabOrder` | Tab order applied to widget annotations on a page (PDF 32000 /Tabs entry). |
      | `TableBroken` | TableBroken indicates whether a table is broken across pages in a PDF document. |
      | `ClippingPathsProcessingMode` | How clipping paths are processed when text edits affect clipped regions. |
      | `CoordinateOrigin` | How the lowest Y coordinate of a text fragment is interpreted in positioning APIs (CoordinateOrigin and per-segment SetPosition overloads). |
      | `FontReplace` | Font-replacement strategy when the original font cannot encode replacement text. |
      | `FontSizeAdjustment` | Font size adjustment strategy when replaced text has a different size. |
      | `FontStyles` | Represents a position on a page. |
      | `FontType` | Font type classification. |
      | `FontTypes` | Stream-loadable font formats accepted by Stream, FontTypes). |
      | `LanguageTransformation` | Language transformation behavior (RTL, ligatures, etc). |
      | `LineSpacingMode` | How line spacing is interpreted (font-derived vs full glyph extent). |
      | `Text.NoCharacterAction` | Action to perform if font does not contain required character. |
      | `ReplaceAdjustment` | Specifies how text replacement adjustments are handled. |
      | `Text.Scope` | Replace-scope selector (canonical screaming-snake naming). |
      | `TabAlignmentType` | Tab alignment type for tab stops. |
      | `TabLeaderType` | Tab leader character type. |
      | `TextFormattingMode` | Specifies how extracted text is formatted. |
      | `TextRenderingMode` | PDF text rendering mode (Tr operator). |
      | `Text.WordWrapMode` | Word wrap mode that controls how text wraps within a paragraph rectangle. |
      | `VerticalAlignment` | Describes vertical alignment. |
      | `WarningType` | Categories of warnings emitted during save/load. |
      | `XmpFieldType` | XMP field categories (matches the public reflection signature exactly — the underlying integer values are not load-bearing because callers compare against the named members). |
      | `XmpPdfAExtensionCategoryType` | Whether a PDF/A XMP-extension property is for external (publicly visible) or internal (private) use. |

      #### Detailed Member Reference

      ### Document

      The `Document` class serves as the core container for PDF content, providing access to pages via `Document.Pages`, form fields through `Document.Form`, embedded files via `Document.EmbeddedFiles`, and security controls via `Document.CryptoAlgorithm` and `Document.Encrypt`.

      - `Actions`: Catalog /AA additional-action dictionary.
      - `AddEmbeddedFile`: Add an embedded file to the document.
      - `AddNamedDestination`: Add a named destination to the document using the /Names → /Dests name tree.
      - `AllowReusePageContent`: Whether the saver may reuse identical page-content streams.
      - `Background`: Document-wide background colour painted on every page before content during Save.
      - `BindXml`: Bind an Aspose.Pdf XML template to this document.
      - `CenterWindow`: /CenterWindow viewer preference: position document window in the centre of the screen.
      - `ChangePasswords`: Change the password on the bound document.
      - `Check`: Validate / repair the document.
      - `Collection`: PDF Portfolio (collection) wrapper — returns the catalog's Collection dictionary as a Collection, or null if the document is not a portfolio.
      - `Convert`: Render each page to an image, hand it to the OCR callback, then overlay the returned hOCR text on the page as an invisible text layer (text rendering mode 3, /Tr 3) so the PDF becomes searchable / copy-pasteable.
      - `ConvertPageToPNGMemoryStream`: Convert one page to a PNG memory stream.
      - `Create`: Create a new empty PDF document.
      - `CryptoAlgorithm`: The encryption algorithm in use, or null when the document is not encrypted.
      - `CustomSecurityHandler`: Stored custom security handler when set via the ICustomSecurityHandler Encrypt overloads.
      - `Decrypt`: Remove encryption from the document.
      - `DefaultNodesNumInSubtrees`: Default page-tree branching factor (PDF table 30 /Count vs /Kids ratio).
      - `Destinations`: Provides destination lookup methods (e.g., GetPageNumber by destination name).
      - `Direction`: The /Direction viewer preference (text-flow direction).
      - `DisableFontLicenseVerifications`: Whether the face licence gate is lifted for this document: with it set, a face whose OS/2 fsType forbids embedding is embedded anyway and no FontEmbeddingException is raised.
      - `DisplayDocTitle`: /DisplayDocTitle viewer preference: show the document title in the window's title bar instead of the file name.
      - `Dispose`: Defined as `void Dispose()`.
      - `Document`: Create a new empty PDF document (parameterless constructor, matches public API).
      - `Duplex`: The /Duplex viewer preference (default Simplex when unset).
      - `EmbedStandardFonts`: When true, the standard 14 PostScript fonts (Helvetica / Times / Courier × 4 styles + Symbol + ZapfDingbats) are embedded into the saved document so the output renders identically without relying on viewer-side font fallbacks.
      - `EmbeddedFiles`: Embedded files collection.
      - `EnableNotificationLogging`: Whether the document emits notification log entries.
      - `EnableObjectUnload`: Whether to unload large objects after use to reduce memory pressure.
      - `EnableSignatureSanitization`: When true (default), the writer scrubs invalid signature references during save.
      - `Encrypt`: Encrypt the document with the specified algorithm, passwords, and permissions.
      - `EncryptionInfo`: Encryption details, or null if not encrypted.
      - `ExportAnnotationsToXfdf`: Export every annotation in the document to an XFDF stream.
      - `FileName`: The file name/path this document was loaded from, or null if loaded from a stream.
      - `FileSizeLimitToMemoryLoading`: Maximum file size (bytes) loaded entirely into memory.
      - `FitWindow`: /FitWindow viewer preference: resize the window to fit the first displayed page.
      - `Flatten`: Flatten all form fields — renders their visual appearance into page content and removes the interactive form.
      - `FlattenTransparency`: Flatten transparency to opaque graphics.
      - `FontUtilities`: Font utilities for subsetting and font management.
      - `Form`: The interactive form (AcroForm).
      - `FreeMemory`: Clears memory.
      - `GetCatalogValue`: Reads a value from the document's /Catalog by name.
      - `GetObjectById`: Resolve a PDF object by string id.
      - `GetOrCreateMetadata`: Get or create XMP metadata for this document.
      - `GetOrCreateViewerPreferences`: Get or create viewer preferences for this document.
      - `GetPdfACompliance`: Returns the PDF/A compliance level detected from XMP metadata, or null if not a PDF/A document.
      - `GetXmpMetadata`: Write the document's XMP /Metadata packet to stream.
      - `HandleSignatureChange`: Whether signature fields fire change-handlers when their dict mutates.
      - `HasCollection`: Whether the document is a PDF Portfolio (has a /Collection dictionary in the catalog).
      - `HasDestinations`: Whether the document has named destinations.
      - `HasEmbeddedFiles`: Whether the document has embedded files.
      - `HasForm`: Whether the document has an interactive form.
      - `HasIncrementalUpdate`: Returns true if this PDF uses incremental updates (has multiple %%EOF markers).
      - `HasLayers`: Whether the document has optional content (layers).
      - `HasMetadata`: Whether the document has XMP metadata.
      - `HasOutlines`: Whether the document has bookmarks.
      - `HasPageLabels`: Whether the document has page labels.
      - `HasStructTree`: Whether the document has a structure tree.
      - `HideMenubar`: Viewer preference: hide the menu bar (/ViewerPreferences /HideMenubar).
      - `HideToolBar`: Viewer preference: hide the toolbar (/ViewerPreferences /HideToolbar).
      - `HideWindowUI`: Viewer preference: hide window UI chrome (/ViewerPreferences /HideWindowUI).
      - `Id`: The /ID array from the trailer, or null when no /ID entry is present.
      - `IgnoreCorruptedObjects`: When true, the parser tolerates corrupted indirect-object declarations (extra/garbled bytes between objects) instead of throwing.
      - `ImportAnnotationsFromXfdf`: Import annotations from an XFDF stream into the document.
      - `ImportPage`: Import a single page from another document into this document.
      - `ImportPages`: Import specified pages from another document into this document.
      - `Info`: The document information dictionary (title, author, etc.).
      - `IsDecrypted`: Whether the document has been successfully decrypted (i.e., the decryptor was initialised).
      - `IsEncrypted`: Whether the document is encrypted.
      - `IsLicensed`: Whether a license is currently applied.
      - `IsLinearized`: Defined as `bool`.
      - `IsPdfUaCompliant`: True when the document's XMP metadata carries a pdfuaid:part entry (PDF/UA-1 identifier).
      - `IsPdfaCompliant`: True if the document declares itself PDF/A compliant.
      - `IsRepairNeeded`: Check whether the document needs repair.
      - `IsTagged`: Whether the document is tagged PDF.
      - `IsXrefGapsAllowed`: Allow gaps in the xref table during parse.
      - `JavaScript`: Document-level JavaScript (PDF spec §12.6.4.16 — /Names/JavaScript name tree in the catalog).
      - `Language`: The natural language of the document (BCP 47).
      - `LinearizeDocument`: Linearize the document for fast web viewing.
      - `LoadFrom`: Async file load wrapper (currently synchronous).
      - `LogicalStructure`: The root of the document's logical-structure tree.
      - `Merge`: Merge every page of each documents entry into this document, preserving source order.
      - `MergeDocuments`: Defined as `MergeDocuments(documents)`.
      - `Metadata`: XMP metadata for the document.
      - `NamedDestinations`: Named destinations in the document.
      - `NonFullScreenPageMode`: The /NonFullScreenPageMode viewer preference (which page mode to revert to when leaving full-screen).
      - `Open`: Open a PDF document from a byte array.
      - `OpenAction`: The document open action (action or destination executed when opening the PDF), or null.
      - `Optimize`: Optimize document resources by removing unused objects and deduplicating streams.
      - `OptimizeResources`: Defined as `OptimizeResources()`.
      - `OptimizeSize`: Gets or sets a flag indicating whether the document should be optimized for size on save.
      - `OptionalContent`: Optional content properties (layers), or null if none.
      - `Outlines`: The document outline (bookmarks), or null if none exists.
      - `OutputIntents`: Output intents declared in the document catalog.
      - `PageCount`: Shortcut for Pages.Count.
      - `PageInfo`: Default page dimensions/margins applied to pages added after this is set.
      - `PageLabels`: Page labels, or null.
      - `PageLayout`: The /PageLayout entry as a typed enum.
      - `PageLayoutName`: Raw /PageLayout name read from the catalog (e.g., "SinglePage", "TwoColumnLeft").
      - `PageMode`: The /PageMode entry as a typed enum.
      - `PageModeName`: Raw /PageMode name read from the catalog (e.g., "UseNone", "UseOutlines").
      - `PageNodesToBalancedTree`: Rebuild the page tree so each subtree has nodesNumInSubtrees children.
      - `Pages`: The collection of pages in this document.
      - `PdfFormat`: Returns the PDF format declared by this document.
      - `PdfVersion`: The PDF version from the file header (e.g., "1.4", "1.7", "2.0").
      - `Permissions`: Raw /P permissions bitmask from the encryption dictionary (PDF 32000-1:2008 Table 22).
      - `PickTrayByPdfSize`: Viewer preference: pick the paper tray by PDF page size (/ViewerPreferences /PickTrayByPDFSize).
      - `PrintScaling`: The /PrintScaling viewer preference.
      - `ProcessParagraphs`: Process paragraphs in the document (layout step before save).
      - `RemoveFormField`: Remove a form field by name from all pages and the AcroForm.
      - `RemoveMetadata`: Remove all metadata.
      - `RemoveNamedDestination`: Remove a named destination from the document's /Names → /Dests name tree.
      - `RemovePdfUaCompliance`: Remove PDF/UA compliance markers.
      - `RemovePdfaCompliance`: Remove PDF/A compliance identification from XMP metadata.
      - `Repair`: Repair the document by re-serializing it.
      - `Save`: Save the document in-place: writes back to the file the document was opened from (FileName), or performs an incremental save to the original source stream when the document was opened from a writable FileStream.
      - `SaveAsync`: Async wrapper around Save().
      - `SaveXml`: Write the document's generator DOM as Aspose.Pdf template XML — the counterpart of BindXml(string), so a document can be saved and read back.
      - `SendTo`: Render this document through device into output — delegates to Stream).
      - `SetDefaultFileSizeLimitToMemoryLoading`: Reset FileSizeLimitToMemoryLoading to its built-in default.
      - `SetTitle`: Update the /Info /Title entry.
      - `SetVersion`: Set the PDF version for the output header (e.g., "1.7", "2.0").
      - `SetXmpMetadata`: Replace the document's XMP /Metadata packet from stream.
      - `SplitSharedImages`: Give each of the passed pages a private copy of the image XObjects they share, so an in-place edit reached through one page (e.g.
      - `StructTreeRoot`: The structure-tree root (/StructTreeRoot) for reading a tagged document's logical structure, or null when the document is not tagged.
      - `TaggedContent`: Tagged-content surface for accessibility metadata and the logical-structure tree.
      - `ToArray`: Serialize the document into a fresh byte array.
      - `Validate`: Validate the document structure and return any issues found.
      - `Version`: Alias for PdfVersion.
      - `ViewerPreferences`: Viewer preferences that control how the document is displayed.

      ### Text

      The Text class enables programmatic text manipulation within PDF documents, supporting operations such as text extraction, replacement, and positioning alongside the `TextStamp` class for adding stamped text content.

      ### Forms

      The Forms class provides access to interactive form fields and their properties, working with `FieldValueType` to define and validate field data types across form controls.

      ### Annotations

      The Annotations class supports managing annotation collections on PDF pages, while the Stamps class provides functionality for adding and manipulating stamp elements on document pages.

      ### Security

      The Security class offers encryption and permission controls, utilizing `CryptoAlgorithm` for encryption methods and `Permissions` to define user access rights for PDF documents.

      ### Devices

      The Devices class provides rendering capabilities for converting PDF pages to images and other formats, supporting operations through Converters and `HtmlLoadOptions` for web content integration.

      ### LogicalStructure

      The `LogicalStructure` class enables working with the logical structure tree of tagged PDF documents, supporting accessibility and reflow capabilities through the Tagged module.

      ### Table

      The `Table` class provides functionality for creating and manipulating tabular content in PDF documents, supporting image placement through `ImagePlacementAbsorber` and `XImageCollection` for complex layouts.

      - `Alignment`: Defined as `HorizontalAlignment`.
      - `BackgroundColor`: Table background color.
      - `Border`: Table border.
      - `BreakText`: Optional indicator drawn at the page break when a row is split across pages.
      - `Broken`: How the table breaks across pages.
      - `Build`: Build the table content stream bytes for the given page.
      - `BuildMultiPage`: Build the table across multiple pages.
      - `Clone`: Shallow clone of the table.
      - `ColumnAdjustment`: How columns are sized when the table is rendered (API compatibility).
      - `ColumnWidths`: Space-separated column widths (e.g.
      - `CornerStyle`: Corner-rounding style applied to the table's border box.
      - `DefaultCellBorder`: Default cell border applied to all cells unless overridden.
      - `DefaultCellPadding`: Default cell padding.
      - `DefaultCellTextState`: Default text state for cells.
      - `DefaultColumnWidth`: Default column-width used when ColumnWidths is empty.
      - `GetHeight`: Approximate rendered height of the table, independent of any hosting page.
      - `GetWidth`: Sum of column widths from ColumnWidths.
      - `HorizontalAlignment`: Horizontal alignment applied to this paragraph.
      - `Hyperlink`: Typed hyperlink decoration applied to the paragraph.
      - `HyperlinkText`: Legacy string-typed hyperlink target.
      - `ImportArray`: Import a one-dimensional object array into the table, wrapping the values into rows by the table's column count.
      - `ImportDataTable`: Import all rows of a DataTable.
      - `ImportDataView`: Import a DataView.
      - `IsBordersIncluded`: When true, cell-border widths count against cell padding and row-height calculations.
      - `IsBroken`: Whether the table is allowed to break across pages.
      - `IsFirstParagraphInColumn`: Force the paragraph to start a new column.
      - `IsInLineParagraph`: Inline paragraph flag (does not start a new line).
      - `IsInNewPage`: Force the paragraph to start on a new page.
      - `IsKeptWithNext`: Keep this paragraph on the same page as the next one.
      - `Left`: Left position of the table on the page.
      - `Margin`: Table margin.
      - `RepeatingColumnsCount`: Maximum columns count for the table.
      - `RepeatingRowsCount`: Number of rows to repeat at the top of each page when the table spans pages.
      - `RepeatingRowsStyle`: Default text state for the rows repeated on continuation pages (see RepeatingRowsCount).
      - `Rows`: The collection of rows in this table.
      - `SetColumnTextState`: Apply textState to every cell in the given (0-based) column number.
      - `Table`: Defined as `Table()`.
      - `Top`: Top position of the table on the page (distance from page top).
      - `VerticalAlignment`: Vertical alignment applied to this paragraph.
      - `ZIndex`: Z-order index used by the DOM renderer.

      ### PageCollection

      The `PageCollection` class manages the collection of pages within a `Document`, providing indexed access to individual `Page` objects and supporting page manipulation operations.

      - `Accept`: Accept a TextAbsorber visitor; iterates every page.
      - `Add`: Add a new blank page.
      - `AddUnrestricted`: Add a new blank page bypassing licensing page-count restrictions.
      - `At`: Alias for indexer — get page by 1-based page number.
      - `BeginUpdate`: Suspend page-tree maintenance during a batch of mutations.
      - `Clear`: Remove every page from the collection.
      - `Contains`: Whether the supplied page belongs to this collection.
      - `CopyTo`: Copy the collection contents into an array starting at index.
      - `Count`: Total number of pages.
      - `Delete`: Delete a page by 1-based page number.
      - `EndUpdate`: Resume page-tree maintenance after a BeginUpdate batch.
      - `Flatten`: Flatten every form and annotation on every page.
      - `FreeMemory`: Clears cached data on every page.
      - `GetEnumerator`: Defined as `GetEnumerator()`.
      - `IndexOf`: Returns the 1-based index of the given page, or -1 if not found.
      - `Insert`: Insert a new blank page before the given 1-based index.
      - `IsReadOnly`: Whether the collection is read-only.
      - `IsSynchronized`: Whether the collection is thread-safe.
      - `Remove`: Remove the supplied page and report whether it was present.
      - `SyncRoot`: Synchronization root for IsSynchronized; returns this collection.

      ### Pdf.TextStamp

      The `TextStamp` class allows adding and positioning text stamps on PDF pages, supporting customization of font, color, rotation, and alignment for stamped content.

      - `AutoAdjustFontSizePrecision`: When auto-adjusting font size to fit the stamp rectangle, the precision (in points).
      - `AutoAdjustFontSizeToFitStampRectangle`: When true, the renderer shrinks the font size until the text fits the stamp's Width/Height.
      - `Draw`: When false, the stamp records intent but skips drawing.
      - `FontSize`: Font size in points.
      - `Height`: Defined as `double`.
      - `Justify`: When true, the stamp's text is full-justified within the stamp width.
      - `MaxRowWidth`: Maximum row width before wrapping.
      - `NoCharacterBehavior`: Strategy used when a character has no glyph in the configured font.
      - `Put`: Add this stamp to a page.
      - `ReplacementFont`: Fallback font used when the main font lacks a required glyph; consulted only when NoCharacterBehavior is UseReplacementFont.
      - `Scale`: Defined as `bool`.
      - `TextAlignment`: Defined as `Aspose.Pdf.HorizontalAlignment`.
      - `TextStamp`: Defined as `TextStamp(string value)`.
      - `TextState`: Text-state snapshot.
      - `TreatYIndentAsBaseLine`: When true, the stamp's Y-indent is treated as the text baseline rather than the bounding-box top.
      - `Value`: Defined as `string`.
      - `Width`: Defined as `double`.
      - `WordWrap`: Defined as `bool`.
      - `WordWrapMode`: Defined as `Aspose.Pdf.Text.TextFormattingOptions.WordWrapMode`.

      ### FieldValueType

      The `FieldValueType` class defines the data types supported for interactive form fields, enabling validation and type-specific handling of user input in PDF forms.

      ### Stamps

      The Stamps class provides functionality for adding and manipulating stamp elements on PDF pages, supporting both text and image-based stamp operations.


      The primary entry point is `Document`, which exposes its pages through `Document.Pages` (a
      `PageCollection` of `Page` objects), its interactive form through `Document.Form`, and its
      security surface through `PdfFileSecurity` and `PdfFileSignature`. The public API surface includes
      881 public types across 37 modules, summarized in the module-grouped tables below.

      </details>
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.PDF FOSS for .NET, straight from its own sealed, reviewed documentation candidate:

      Aspose.PDF FOSS for .NET is a free, open-source subset of Aspose.PDF for .NET that supports PDF creation, reading, editing, and conversion to HTML, SVG, Markdown, and XML, targeting .NET 8.0 and running on Windows, Linux, and macOS.

      - The library depends on `System.Drawing.Common` 8.0.0, which is Windows-only for certain image-interop members such as `ImageDevice.GetBitmap` and `StampInfo.Image`, causing `PlatformNotSupportedException` on Linux and macOS, while all other functionality is pure-managed and cross-platform.
      - These limitations do not apply to Aspose.PDF for .NET — Enterprise Edition, which adds broader format support and full feature coverage including AI / `LowCode` workflows, complete `XFA` authoring, advanced digital-signature validation, multithreading, and conversion to many additional formats.
      - AI / `LowCode` workflows, multithreading APIs, and advanced format converters such as DOCX, EPUB, MHT, XPS, PCL, `LaTeX`, DJVU, OFD, `PostScript`, and CGM are out of scope, and `PdfViewer`'s Print* methods throw `PlatformNotSupportedException`.
      - `ToUnicodeProcessingRules` is available but advanced features like `ToUnicodeProcessingRules.MapNonLinkedSymbolsOnSpace` and `ToUnicodeProcessingRules.RemoveSpacesFromCMapNames` are not active, and the `ToUnicodeProcessingRules` constructor is present but limited in effect.
      - `OcspSettings` is present but not active; its properties such as `OcspSettings.RequestTimeout` and `OcspSettings.ServerUrl` are stored but not used during digital signature validation.
      - `PdfVersion` is supported for reading and writing standard PDF versions, but advanced PDF/A conversion fix-ups such as transparency flattening, embedded-file constraints, and ICC profile downgrade are stored but not applied.

      These limitations don't apply to [Aspose.PDF for .NET — Enterprise Edition](https://products.aspose.com/pdf/net/). Aspose.PDF FOSS for .NET is the open-source variant of Aspose.PDF, and the commercial edition extends it with additional features, advanced rendering capabilities, and enterprise support.
single:
  enable: true
  block:
  - title: Open a PDF, Add a Link Annotation, and Save
    content: |
      Open an existing PDF, attach a URI link annotation to page 1, and persist the result.

      ```csharp
      using Aspose.Pdf;

      var data = System.IO.File.ReadAllBytes("input.pdf");
      using var doc = Document.Open(data);
      var page = doc.Pages[1];
      var action = PdfAction.CreateUri("https://aspose.com");
      page.Annotations.AddLinkAnnotation(
          new Rectangle(50, 700, 200, 720), action);
      using var ms = new System.IO.MemoryStream();
      doc.Save(ms);
      System.IO.File.WriteAllBytes("output.pdf", ms.ToArray());
      ```
  - title: Add a Watermark Annotation
    content: |
      Stamp a watermark onto page 1 of an existing document and round-trip through serialization.

      ```csharp
      using Aspose.Pdf;
      var input = System.IO.File.ReadAllBytes("report.pdf");
      using var doc = Document.Open(input);
      doc.Pages[1].Annotations.AddWatermarkAnnotation(
          new Rectangle(0, 0, 612, 792), "CONFIDENTIAL");
      var saved = doc.ToArray();
      System.IO.File.WriteAllBytes("watermarked.pdf", saved);
      ```
  - title: Extract Text Fragments from a Page
    content: |
      Use `TextFragmentAbsorber` to enumerate all text fragments on the first page.

      ```csharp
      using Aspose.Pdf;
      using Aspose.Pdf.Text;

      var data = System.IO.File.ReadAllBytes("document.pdf");
      using var doc = Document.Open(data);
      var absorber = new TextFragmentAbsorber();
      absorber.Visit(doc.Pages[1]);
      foreach (var fragment in absorber.TextFragments)
      {
          Console.WriteLine(fragment.Text);
      }
      ```
faq:
  enable: false
  list: []
supportandlearning:
  enable: true
more_formats:
  enable: true
back_to_top:
  enable: true
provenance:
  content_origin: skill-generated
  last_mechanism: skill
  reviewed: false
  auto_updatable: true
  content_hash: ac7f61c36f0d02b2bf15784f354d2a4b
grade: A
graded_content_hash: "e3b0c44298fc1c149afbf4c8996fb924"
grade_reasons:
  - "1 WARN finding(s) [audit] -> base grade A"
evidence:
  model_sha: 663783d18ec1c00efbd9b56a0a6ea20e4671d92b
  model_version: 26.8.0
  claims:
  - CLM-pdf-04f590
  - CLM-pdf-1ea40d
  - CLM-pdf-505153
  - CLM-pdf-539a9f
  - CLM-pdf-666802
  - CLM-pdf-9713d292
  - CLM-pdf-b1e72dde
  apis:
  - AbsorbedCell
  - AbsorbedRow
  - AbsorbedTable
  - Algorithm
  - AnnotationCollection
  - AnnotationFlags
  - AnnotationSelector
  - CheckboxField
  - ChoiceField
  - Document
  - Document.Open
  - DocumentInfo
  - DocumentPrivilege
  - Page
  - PageCollection
  - PdfAction
  - PdfAction.CreateUri
  - RadioButtonField
  - Rectangle
  - TextFragment
  - TextFragmentAbsorber
  - TextFragmentAbsorber.TextFragments
  - TextFragmentAbsorber.Visit
  formats:
  - ext: pdf
    support: both
  - ext: html
    support: both
  - ext: svg
    support: both
  - ext: md
    support: import
  - ext: tiff
    support: both
  - ext: markdown
    support: export
  - ext: bmp
    support: both
  - ext: jpeg
    support: both
  - ext: text
    support: export
  - ext: doc
    support: import
---

