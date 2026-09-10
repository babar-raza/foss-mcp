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
overview:
  enable: true
  title: Free MIT-Licensed .NET Library for PDF Creation and Manipulation
  content: |-
    Aspose.PDF FOSS for .NET is a MIT-licensed library for creating and working with PDF documents in C# and .NET. The central entry point is the `Document` class, which supports construction from file path, byte array, stream, or password-protected sources, and exposes `Open`, `Save`, `Merge`, `Encrypt`, `Decrypt`, and `Convert` operations across the full PDF specification.

    The library exposes 805 classes covering document structure via `Document`, `Page`, and `PageCollection`; rich annotations through `AnnotationCollection` (including `AddTextAnnotation`, `AddLinkAnnotation`, `AddHighlightAnnotation`, `AddWatermarkAnnotation`, and `AddRedactAnnotation`); interactive forms with `Form`, `ButtonField`, `CheckboxField`, `RadioButtonField`, and `ChoiceField`; text extraction and search via `TextFragmentAbsorber` and `TextFragment`; table detection with `AbsorbedTable`, `AbsorbedRow`, and `AbsorbedCell`; and document security via `Document.Encrypt` and `Document.Decrypt`. Format conversion supports HTML, SVG, and Markdown import alongside PDF export.

    The library is fully MIT-licensed with no runtime fees or usage restrictions. For enterprise features and support, see [Aspose.PDF for .NET — Enterprise Product](https://products.aspose.com/pdf/net/).
content:
  enable: true
  block:
  - title_left: Document Structure and Page Management
    content_left: |
      - **Document API:** Create new PDFs with `Document()` or open existing files via `Document.Open(path)`, `Document.Open(data)`, or `Document.Open(stream)`.
      - **Page management:** Add, access, and reorder pages via `PageCollection`. Each `Page` exposes `Width`, `Height`, `MediaBox`, rotation, and `Annotations`.
      - **Merge and split:** Combine multiple files with `Document.Merge(documents)` or `Document.MergeDocuments(files)`. Import specific pages with `Document.ImportPage` and `Document.ImportPages`.
      - **Metadata:** Read and write document metadata via `DocumentInfo` and `Document.GetOrCreateMetadata()`.
      - **Serialization:** Persist documents with `Document.Save(filename)`, `Document.Save(stream, format)`, or `Document.ToArray()`.
    title_right: Common Document Processing Scenarios
    content_right: |
      - **PDF generation:** Build documents programmatically by creating a `Document` instance and populating pages with content.
      - **Document analysis:** Open existing files for metadata inspection, page dimension measurement, or form field enumeration.
      - **Page manipulation:** Resize, rotate, or reorder pages using `Document.ImportPage` and the `Page` API.
      - **Batch merging:** Assemble report sections or invoice pages into a single PDF using `Document.MergeDocuments`.
  - title_left: Annotations and Form Fields
    content_left: |
      - **Rich annotation API:** `AnnotationCollection` exposes methods for text, link, highlight, underline, strikeout, square, circle, line, ink, stamp, caret, redact, watermark, polygon, and polyline annotations.
      - **Link actions:** Add URI links with `AddLinkAnnotation(rect, uri)` or internal page-jump links with `AddLinkAnnotation(rect, destinationPage, destRect)`.
      - **Interactive forms:** Access the document form via `Document.Form` to enumerate or create `ButtonField`, `CheckboxField`, `RadioButtonField`, and `ChoiceField` instances.
      - **Annotation selection:** Filter annotations by type across pages using `AnnotationSelector` with per-type `Visit` overloads.
      - **Annotation flags:** Control visibility, print, and lock behaviour via `AnnotationFlags`.
    title_right: Document Review and Collaboration Scenarios
    content_right: |
      - **Markup review:** Add highlight, underline, or free-text annotations for document review workflows.
      - **Redaction:** Permanently remove sensitive content regions using `AddRedactAnnotation`.
      - **Watermarking:** Stamp CONFIDENTIAL or APPROVED text onto pages with `AddWatermarkAnnotation` and `AddStampAnnotation`.
      - **Interactive forms:** Build fillable documents with radio buttons, checkboxes, and choice lists for data capture pipelines.
  - title_left: Text Extraction and Search
    content_left: |
      - **Text search:** Use `TextFragmentAbsorber` to locate all text fragments on a page or across a document, with optional phrase matching.
      - **Fragment properties:** Each `TextFragment` exposes `Text`, `Position`, `TextState` (font, size, colour), and bounding rectangle.
      - **Text state control:** `TextFragmentState` provides access to `Font`, `FontSize`, `ForegroundColor`, `BackgroundColor`, and rendering mode.
      - **Table detection:** Identify and extract tabular content via `AbsorbedTable`, `AbsorbedRow`, and `AbsorbedCell`, each providing `Rect` and `TextFragments`.
    title_right: Text and Data Extraction Scenarios
    content_right: |
      - **Content indexing:** Extract all page text for full-text search pipelines.
      - **Form data extraction:** Read submitted field values for server-side processing.
      - **Invoice parsing:** Use `AbsorbedTable` to parse tabular row-and-column data from financial PDFs.
      - **Redline comparison:** Locate specific text fragments for precise position-based annotation or replacement.
  - title_left: Security and Encryption
    content_left: |
      - **AES and RC4 encryption:** Encrypt documents with `Document.Encrypt(userPassword, ownerPassword, permissions, algorithm)` using `Algorithm.AESx128`, `AESx256`, or `RC4x128`.
      - **Permission control:** Restrict print, copy, and accessibility via `DocumentPrivilege`.
      - **Password management:** Change user and owner passwords with `Document.ChangePasswords`.
      - **Decryption:** Remove protection with `Document.Decrypt()`.
      - **Digital signatures:** Access signature fields and PKCS#7 data through the `Signature` and `Algorithm` types.
    title_right: Document Security Scenarios
    content_right: |
      - **Confidential distribution:** Encrypt PDFs with per-recipient passwords before delivery.
      - **Print-only contracts:** Apply owner passwords with print-only `DocumentPrivilege` to prevent copying.
      - **Compliance archiving:** Strip encryption from internal archives after security review.
      - **Signature verification:** Inspect embedded digital signatures in received PDFs for authenticity checks.
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
  enable: true
  list:
  - question: What is the licensing model for Aspose.PDF FOSS for .NET?
    answer: Aspose.PDF FOSS for .NET is released under the MIT License, allowing free
      use in commercial products with no runtime fees. The source code is publicly
      available on GitHub.
  - question: How do I install Aspose.PDF FOSS for .NET?
    answer: Install from NuGet. The library targets .NET 8 and later with no native
      dependencies.
  - question: Which file formats does Aspose.PDF FOSS for .NET support?
    answer: The library reads and writes PDF and supports import of HTML, SVG, and
      Markdown. Raster export is also available — PDF pages can be rendered to BMP,
      JPEG, PNG, and TIFF.
  - question: How do I create a new PDF document?
    answer: Use `Document.Create()` to produce an empty document, then add pages and
      save with `Document.Save(stream)` or `Document.ToArray()`.
  - question: Is a license key required to use Aspose.PDF FOSS for .NET?
    answer: No license key or activation step is required. The library runs fully
      unlicensed at no cost under the MIT License.
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

