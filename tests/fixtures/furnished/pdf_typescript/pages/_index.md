---
layout: plugin
family_name: Aspose.PDF FOSS
plugin_description: Open-source TypeScript library for creating, editing, converting,
  annotating, and securing PDF documents — MIT licensed, zero dependencies.
plugin_platform: TypeScript
head_title: Aspose.PDF FOSS for TypeScript | Open-Source PDF Document Library
head_description: MIT-licensed TypeScript library for PDF creation, editing,
  HTML/Markdown/DOCX conversion, annotations, AcroForms, encryption, and
  redaction.
title: Aspose.PDF FOSS for TypeScript
description: |
  Open-source TypeScript library for creating, editing, converting, annotating, and securing PDF documents — MIT licensed, zero dependencies.
submenu:
  enable: true
github_url: https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-TypeScript
overview:
  enable: true
  title: Aspose.PDF FOSS — Open Source TypeScript PDF Library
  content: |-
    Aspose.PDF FOSS for TypeScript is an open-source library for creating, editing, converting,
    and securing PDF documents in Node.js (>=22) applications. The `Document` and `Page` classes
    are the central entry points: `Document.OpenFile()` / `Document.Open()` load existing files
    or in-memory bytes, `Document.New()` starts a blank document, and `WriteTo()` / `Save()`
    write the result back out, optionally with xref-stream compression. The project is a
    zero-dependency package (Node.js is the only runtime requirement) and is under active
    early-stage development (currently version 0.1.0).

    Beyond core document and page editing — splitting, merging, extracting, reordering, and
    appending pages with `Document.Split()`, `Document.Merge()`, `Document.ExtractPages()`, and
    `Document.Append()` — the library converts pages and whole documents to other formats with
    `ToHtml()`, `ToMarkdown()`, `ToDocx()`, `ToSvg()`, `ToImage()`, and `ToEpub()`. Annotation
    support covers highlight, underline, squiggly, strikeout, link, text-note, free-text, and
    stamp markup through methods like `Page.AddHighlight()` and `Page.AddStamp()`. AcroForm
    fields (text, checkbox, radio, combo box, list box, and push button) are built with the
    `Form` class and its typed `Field` subclasses, and documents can be encrypted with AES-128,
    AES-256, or RC4, digitally signed with `Document.Sign()`, and redacted with
    `Document.Redact()` / `Page.ApplyRedactions()`.

    Aspose.PDF FOSS for TypeScript is released under the MIT license with no runtime fees or
    usage restrictions (see the install section below). For the enterprise product family, see
    [Aspose.PDF — Enterprise Product Family](https://products.aspose.com/pdf/).
content:
  enable: true
  block:
  - title_left: Document and Page Editing
    content_left: |
      - Open PDFs from disk or in-memory bytes with `Document.OpenFile()` / `Document.Open()`, or start blank with `Document.New()`
      - Split, merge, extract, reorder, and append pages with `Document.Split()`, `Document.Merge()`, `Document.ExtractPages()`, `Document.Reorder()`, and `Document.Append()`
      - Read and update document metadata with `Document.SetMetadata()` / `Document.GetMetadata()`
      - Write output with `Document.WriteTo()`, optionally compressed via xref streams and object streams
    title_right: Where It's Used
    content_right: |
      - Backend PDF assembly pipelines that split, merge, and reorder incoming files
      - Page-extraction services for chapter or invoice splitting
      - Document metadata management for compliance and archiving
      - Storage-optimized output generation for high-volume document stores
  - title_left: Format Conversion
    content_left: |
      - Render whole documents to HTML with `Document.ToHtml()` and to GitHub-flavored Markdown with `Document.ToMarkdown()`
      - Produce reflowed or fixed-layout `.docx` output with `Document.ToDocx()` / `Page.ToDocx()`
      - Render individual pages to standalone SVG with `Page.ToSvg()` or raster PNG/TIFF bytes with `Page.ToImage()`
      - Export to EPUB with `Document.ToEpub()` for e-reader distribution
    title_right: Where It's Used
    content_right: |
      - Publishing pipelines that turn PDFs into web-ready HTML or Markdown
      - Thumbnail and preview generation for document management systems
      - Archiving PDFs as editable Word documents
      - E-book distribution workflows
  - title_left: Annotations and Markup
    content_left: |
      - Add text markup with `Page.AddHighlight()`, `Page.AddUnderline()`, `Page.AddSquiggly()`, and `Page.AddStrikeOut()`
      - Attach sticky notes and free-standing text with `Page.AddTextNote()` and `Page.AddFreeText()`
      - Place rubber-stamp annotations with `Page.AddStamp()` and clickable links with `Page.AddLink()`
      - Flatten any annotation into page content with the `Annotation.Flatten()` method exposed on typed subtypes like `MarkupAnnotation` and `StampAnnotation`
    title_right: Where It's Used
    content_right: |
      - Document review and redlining tools
      - Collaborative markup and commenting workflows
      - Approval-stamp placement in signing workflows
      - Read-only publishing of previously interactive markup
  - title_left: AcroForms
    content_left: |
      - Add text, checkbox, radio, combo box, list box, and push-button fields with the `Form` class (`AddTextField()`, `AddCheckbox()`, `AddRadioGroup()`, `AddComboBox()`, `AddListBox()`, `AddPushButton()`)
      - Style and refresh field appearances with `SetStyle()` and `GenerateAppearance()` on typed field classes such as `TextField`, `ChoiceField`, and `ButtonField`
      - Wire field behavior with `SetActions()` on any `Field`
      - Flatten a completed form into static page content with `Document.FlattenForm()`
    title_right: Where It's Used
    content_right: |
      - Fillable government, tax, and enrollment forms
      - Programmatic pre-filling of forms from database records
      - Flattening submitted forms before long-term archival
      - Interactive surveys and intake documents
  - title_left: Security, Signing, and Redaction
    content_left: |
      - Password-protect and encrypt output with AES-128, AES-256, or RC4 via `Document.WriteTo()`'s `encrypt` option, with granular `Permissions`
      - Digitally sign documents with `Document.Sign()` and verify signatures with `Document.VerifySignatures()`
      - Add trusted timestamps with `Document.AddDocumentTimestamp()`
      - Permanently redact sensitive regions or matched text with `Document.Redact()`, `Document.RedactText()`, and `Page.ApplyRedactions()`
    title_right: Where It's Used
    content_right: |
      - Distributing confidential reports with per-recipient passwords
      - Digitally signed and timestamped contracts
      - Compliance redaction of PII before public release
      - Tamper-evident audit trails for regulated industries
single:
  enable: true
  block:
  - title: Convert a PDF to HTML, Markdown, and DOCX
    content: |
      Open a PDF and render it out to several downstream formats in one pass.

      {{< package-install "pdf" "typescript" >}}

      ```typescript
      import { Document } from '@asposefoss/pdf';
      const doc = Document.OpenFile('in.pdf');
      const svg = doc.Pages[0].ToSvg();          // standalone <svg> string
      // fs.writeFileSync('page1.svg', svg);
      const png = doc.Pages[0].ToImage({ scale: 2 }); // Uint8Array of PNG bytes @144 DPI
      // fs.writeFileSync('page1.png', png);
      const html = doc.ToHtml();                 // standalone semantic HTML, all pages
      // fs.writeFileSync('out.html', html);
      const md = doc.ToMarkdown();               // GFM Markdown, all pages
      // fs.writeFileSync('out.md', md);
      const docx = doc.ToDocx();                 // .docx bytes, reflowed, images in the package
      // fs.writeFileSync('out.docx', docx);
      const fixed = doc.ToDocx({ mode: 'textbox' });  // .docx keeping each page's own geometry
      // fs.writeFileSync('fixed.docx', fixed);
      ```
  - title: Edit Pages and Metadata, Then Save
    content: |
      Load a PDF, edit its pages and metadata, and write it back out with compression.

      ```typescript
      import { Document } from '@asposefoss/pdf';

      // Open from disk (or Document.Open(uint8array) for in-memory data)
      const doc = Document.OpenFile('input.pdf');

      // Inspect and edit pages
      console.log(doc.Pages.length);
      doc.Pages[0].Rotate = 90;
      doc.RemovePage(2);                 // 1-based page number
      doc.Reorder([3, 1, 2]);

      // Metadata
      doc.SetMetadata({ title: 'Report', author: 'Jane', custom: { Dept: 'R&D' } });

      // Save
      doc.WriteTo('output.pdf');         // or: const bytes = doc.Save();
      doc.WriteTo('small.pdf', { compressed: true }); // xref stream + object streams
      ```
  - title: Create a New PDF from Scratch
    content: |
      Start a blank document, add positioned text, and append another page.

      ```typescript
      import { Document, PageFormat } from '@asposefoss/pdf';
      const doc = Document.New(PageFormat.A4);          // one blank A4 page
      doc.Pages[0].AddText('Hello', 72, 720, { fontSize: 14 });
      doc.AddPage(PageFormat.A4.landscape());           // append more as you go
      doc.WriteTo('scratch.pdf');
      ```
  - title: Password-Protect and Encrypt a PDF
    content: |
      Encrypt an existing PDF with a user/owner password pair and AES-256, restricting copying and modification.

      ```typescript
      import { Document } from '@asposefoss/pdf';
      const doc = Document.OpenFile('in.pdf');
      doc.WriteTo('locked.pdf', {
        encrypt: {
          userPassword: 'open-me',       // required to open (default '')
          ownerPassword: 'full-rights',  // default: same as userPassword
          algorithm: 'aes256',           // 'aes256' (default) | 'aes128' | 'rc4'
          permissions: { copying: false, modifying: false },
          encryptMetadata: true,
        },
      });
      ```
faq:
  enable: true
  list:
  - question: What is the licensing model for Aspose.PDF FOSS for TypeScript?
    answer: Aspose.PDF FOSS for TypeScript is released under the MIT License, allowing
      free use in commercial products with no runtime fees. The source code is
      publicly available on GitHub.
  - question: How do I install Aspose.PDF FOSS for TypeScript?
    answer: The package targets Node.js 22 and later and has no other runtime
      dependencies. See the install section above for the current install command.
  - question: Which file formats does Aspose.PDF FOSS for TypeScript support?
    answer: The library reads and writes PDF, Markdown, SVG, and TIFF, and can
      generate DOCX, HTML, PNG, and EPUB output.
  - question: How do I create a new PDF document?
    answer: Use `Document.New(PageFormat.A4)` to produce a blank document, then
      add pages and save with `Document.WriteTo()`.
  - question: Is a license key required to use Aspose.PDF FOSS for TypeScript?
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
  last_mechanism: manual-edit-skill
  auto_updatable: true
  reviewed: false
  content_hash: e1259f33730788f8aa5aa897fcfe9769
evidence:
  model_sha: 04e7b50a3dc37427e5e7d72147dd0baf62ccbb9f
  model_version: 0.1.0
  claims:
  - CLM-pdf-1ab3e469
  - CLM-pdf-6455848b
  - CLM-pdf-9968dba5
  apis:
  - Document.New
  - Document.OpenFile
  - PageFormat.A4
  formats:
  - ext: markdown
    support: both
  - ext: tiff
    support: both
  - ext: svg
    support: both
  - ext: doc
    support: import
  - ext: pdf
    support: both
  - ext: docx
    support: export
  - ext: epub
    support: export
  - ext: html
    support: export
  - ext: md
    support: export
  - ext: png
    support: export
grade: A
graded_content_hash: "e3b0c44298fc1c149afbf4c8996fb924"
grade_reasons:
  - "No findings -> grade A"
---
