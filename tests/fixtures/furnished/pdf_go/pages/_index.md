---
layout: plugin
family_name: Aspose.PDF FOSS
plugin_description: Pure-Go library for creating, editing, merging, and securing PDF
  documents — open source under MIT, zero non-Go dependencies.
plugin_platform: Go
head_title: Aspose.PDF FOSS for Go | Open-Source PDF Library
head_description: Open-source Go library for PDF creation, editing, merging, splitting,
  encryption, AcroForms, tables, and bookmarks. MIT licensed.
title: Aspose.PDF FOSS for Go
description: |
  Pure-Go library for creating, editing, merging, and securing PDF documents — open source under MIT, zero non-Go dependencies.
submenu:
  enable: true
github_url: https://github.com/aspose-pdf-foss/aspose-pdf-foss-for-go
overview:
  enable: true
  title: Aspose.PDF FOSS — Open Source Go PDF Library
  content: |-
    Aspose.PDF FOSS for Go is a pure-Go library that enables developers to create, read, edit, merge,
    split, and secure PDF documents without any external native dependencies. Built for server-side
    Go applications, it integrates into any Go module project with a single command
    (see below).

    The library exposes a comprehensive API surface through the `Document`, `Page`, `Form`, `Table`,
    and `AnnotationCollection` types, covering document management, AcroForm filling, structured table
    layout, bookmark navigation, image handling, and structural integrity validation. Security features include
    AES-128, AES-256, and RC4-128 encryption via `EncryptionOptions` and `Permissions`, with
    granular per-operation access control. Aspose.PDF FOSS for Go is released under the MIT license with no runtime fees or usage restrictions. Requires Go 1.24 or later. For enterprise features and support, see [Aspose.PDF for Go — Enterprise Product](https://products.aspose.com/pdf/go-cpp/).
content:
  enable: true
  block:
  - title_left: Document Operations
    content_left: |
      - Open, create, save, and close PDF files via `Document`
      - Split a document into per-page `Document` instances
      - Append multiple PDFs in-place using `Append`
      - Extract page subsets using `PageRange` for partial exports
      - Read and write XMP document metadata through the `XMPMetadata` type
    title_right: Practical scenarios
    content_right: |
      - Batch-split incoming PDFs into individual page archives
      - Assemble monthly reports from section PDFs
      - Extract page ranges for redacted-copy workflows
      - Update document author and creation date programmatically
  - title_left: Security and Permissions
    content_left: |
      - Encrypt PDFs with AES-128, AES-256, or RC4-128 via `EncryptionOptions`
      - Set user and owner passwords in a single `SetEncryption` call
      - Restrict print, copy, and accessibility using `Permissions`
      - Open password-protected files with `OpenWithPassword`
      - Remove encryption and produce decrypted copies when needed
    title_right: Practical scenarios
    content_right: |
      - Deliver confidential reports with per-recipient passwords
      - Enforce print-only permissions on distributed contracts
      - Re-encrypt documents after watermarking in-place
      - Produce plaintext archive copies from protected originals
  - title_left: AcroForm Processing
    content_left: |
      - Enumerate all fields in a form using `Form` and `Field`
      - Set text values on `TextBoxField` instances by field name
      - Toggle `CheckboxField` and select `RadioButtonField` options
      - Choose items in `ComboBoxField` and `ListBoxField` controls
      - Trigger `ButtonField` actions in interactive form documents
    title_right: Practical scenarios
    content_right: |
      - Automate invoice and contract form filling pipelines
      - Pre-populate application templates before PDF distribution
      - Extract filled field values for downstream data processing
      - Validate required field presence before archival
  - title_left: Tables and Structured Layout
    content_left: |
      - Build grid layouts with `Table`, `Row`, and `Cell` types
      - Apply border styles through `BorderInfo` per cell or table
      - Set padding and spacing with `MarginInfo`
      - Style text with `TextStyle`, `Font`, `Color`, `HAlign`, and `VAlign`
      - Embed images inside table cells using `Image`
    title_right: Practical scenarios
    content_right: |
      - Generate invoices and financial statements with tabular data
      - Render schedule or comparison tables in generated reports
      - Produce data-driven PDFs from database query results
      - Create accessible tables with consistent border and spacing
  - title_left: Bookmarks and Navigation
    content_left: |
      - Create hierarchical bookmark trees via `OutlineItemCollection`
      - Set bold/italic styles and custom colors on outline items
      - Link bookmarks to exact coordinates using `DestinationXYZ`
      - Use page-fit destinations with `DestinationFit`
      - Register named jump targets through `NamedDestinations`
    title_right: Practical scenarios
    content_right: |
      - Add chapter and section bookmarks to long-form documents
      - Build interactive tables of contents for technical manuals
      - Enable deep-link navigation from external hyperlinks
      - Preserve bookmark structure when merging multiple documents
  - title_left: Annotations and Stamps
    content_left: |
      - Add comments via `Annotation` and `AnnotationCollection`
      - Create text callouts with `TextAnnotation` and `FreeTextAnnotation`
      - Apply approval stamps via `StampAnnotation` and `StampName`
      - Highlight content with `HighlightAnnotation`
      - Redact sensitive regions using `RedactAnnotation`
    title_right: Practical scenarios
    content_right: |
      - Add reviewer comments to draft documents for approval
      - Stamp APPROVED or CONFIDENTIAL on finalized PDFs
      - Permanently redact personal data for GDPR-compliant archives
      - Embed hyperlinks and URI actions via `LinkAnnotation`
single:
  enable: true
  block:
  - title: Split and Merge PDFs
    content: |
      Open a PDF, split it into per-page documents, or merge multiple files into one.

      {{< package-install "pdf" "go" >}}

      ```go
      doc, _ := pdf.Open("input.pdf")
      pages, _ := doc.Split()
      for i, p := range pages {
          p.Save(fmt.Sprintf("page%03d.pdf", i+1))
      }
      doc2, _ := pdf.Open("file2.pdf")
      doc.Append(doc2)
      doc.Save("merged.pdf")
      ```
  - title: Encrypt with Permissions
    content: |
      Apply AES-128 encryption with granular print and copy permissions in one call.

      ```go
      doc, _ := pdf.Open("input.pdf")
      doc.SetEncryption(pdf.EncryptionOptions{
          UserPassword:  "userpass",
          OwnerPassword: "ownerpass",
          Permissions:   &pdf.Permissions{AllowPrint: true, AllowCopy: true},
      })
      doc.Save("restricted.pdf")
      ```
  - title: Fill AcroForm Fields
    content: |
      Target form fields by name and set values for all widget types.

      ```go
      doc, _ := pdf.Open("template.pdf")
      text := doc.Form().Field("name").(*pdf.TextBoxField)
      text.SetValue("Jane Doe")
      check := doc.Form().Field("subscribe").(*pdf.CheckboxField)
      check.SetChecked(true)
      radio := doc.Form().Field("plan").(*pdf.RadioButtonField)
      radio.Options()[1].SetSelected(true)
      doc.Save("filled.pdf")
      ```
  - title: Create Hierarchical Bookmarks
    content: |
      Build a nested bookmark tree with styled entries and precise page destinations.

      ```go
      doc, _ := pdf.Open("input.pdf")
      page, _ := doc.Page(1)
      chapter := pdf.NewOutlineItemCollection(doc)
      chapter.SetTitle("Chapter 1")
      chapter.SetBold(true)
      chapter.SetDestination(pdf.NewDestinationXYZ(page, 0, 800, 1.0))
      doc.Outlines().Add(chapter)
      doc.Save("with_bookmarks.pdf")
      ```
faq:
  enable: true
  list:
  - question: What is the license for Aspose.PDF FOSS for Go?
    answer: Aspose.PDF FOSS for Go is released under the **MIT License**. The full
      license text is included in the repository at [github.com/aspose-pdf-foss/aspose-pdf-foss-for-go](https://github.com/aspose-pdf-foss/aspose-pdf-foss-for-go).
  - question: Can I use Aspose.PDF FOSS for Go in a commercial product?
    answer: Yes. The MIT license permits unrestricted commercial use, modification,
      distribution, and private use.
  - question: Is there an enterprise version?
    answer: Aspose.PDF FOSS is the open-source, MIT-licensed edition. A separate commercial
      Aspose.PDF product is available as the [Aspose.PDF — Enterprise Product Family](https://products.aspose.com/pdf/)
      with enterprise support, extended format coverage, and cloud integration.
  - question: How do I install Aspose.PDF FOSS for Go?
    answer: See the install command in the code sample above to add the module to
      your Go project. Import it with an alias in your source files using `import
      pdf "github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"`.
  - question: What Go version is required?
    answer: Go 1.24 or later is required. Verify your version with `go version`.
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
  model_sha: 4a8d9cd1df39dce3f05d6ede8fb06c4ce39f2096
  model_version: ''
  claims:
  - ERC-pdf-go-0277e654
  - ERC-pdf-go-0c2f9115
  - ERC-pdf-go-11d28a36
  - ERC-pdf-go-396612ca
  - ERC-pdf-go-3a0cbf23
  - ERC-pdf-go-4001f382
  - ERC-pdf-go-4aec60fe
  - ERC-pdf-go-4e73205e
  - ERC-pdf-go-4eb0d69c
  - ERC-pdf-go-bcf1d57a
  - ERC-pdf-go-cbfb78ac
  apis:
  - AnnotationCollection.Add
  - Document.Append
  - Document.Extract
  - Document.RemoveEncryption
  - Document.Save
  - Document.SetEncryption
  - Document.Split
  - Field
  - Form
  - Form.Fields
  - NewDestinationXYZ
  - NewOutlineItemCollection
  - Open
  - Page
  - Page.AddTable
  - Page.Size
  - SetTitle
  - Table.SetColumnWidths
  - Validate
  formats:
  - ext: doc
    support: export
  - ext: pdf
    support: import
---
