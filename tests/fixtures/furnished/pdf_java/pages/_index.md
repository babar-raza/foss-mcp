---
layout: plugin
family_name: Aspose.PDF FOSS
plugin_description: Java library for creating and manipulating PDF documents. MIT-licensed.
  Requires Java 11+.
plugin_platform: Java
head_title: Aspose.PDF FOSS for Java | Free MIT-Licensed PDF Library
head_description: Aspose.PDF FOSS for Java is a free, MIT-licensed Java library for
  creating and working with PDF documents. Includes annotation, form field, and PDF/A
  compliance features. Requires Java 11+.
title: Aspose.PDF FOSS for Java
description: Create and manipulate PDF documents from Java — with annotations, form
  fields, page manipulation, and PDF/A compliance. MIT-licensed.
submenu:
  enable: true
github_url: https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-Java.git
provenance:
  content_origin: skill-generated
  last_mechanism: manual-edit-skill
  reviewed: false
  auto_updatable: true
  content_hash: e3b0c44298fc1c149afbf4c8996fb924
overview:
  enable: true
  title: Free MIT-Licensed Java Library for PDF Creation and Manipulation
  content: |-
    Aspose.PDF FOSS for Java is a MIT-licensed Java library for creating and working with PDF documents. It provides a comprehensive `Document` class as the central entry point, along with full coverage of annotations, interactive form fields, page manipulation, metadata, and PDF/A compliance validation.

    The library exposes 527 classes covering the full PDF specification: document structure via `Document`, `Page`, and `PageCollection`; annotations through the `Annotation` hierarchy (including `WidgetAnnotation`, `FreeTextAnnotation`, and `HighlightAnnotation`); interactive forms with `Form`, `ButtonField`, `CheckboxField`, and `RadioButtonField`; page rendering via `BmpDevice`; and PDF/A compliance checking through `ActionRules` and `ActionFixes`. Encryption is provided via `AESCipher` in CBC mode.

    Add Aspose.PDF FOSS to your Maven project using groupId `org.aspose` and artifactId `aspose-pdf-foss`. The library requires Java 11 or later and is fully MIT-licensed. For enterprise features and support, see [Aspose.PDF for Java — Enterprise Product](https://products.aspose.com/pdf/java/).
content:
  enable: true
  block:
  - title_left: Document Structure and Page Management
    content_left: |
      - **Document API:** Use the `Document` class to create new PDF documents. Existing documents can be accessed via `Document(filePath)` or `Document(stream)` constructors.
      - **Page management:** Add, access, and modify pages via `PageCollection`. Each `Page` exposes `MediaBox`, `CropBox`, rotation, resources, and content operators.
      - **Metadata:** Read and write document metadata via `DocumentInfo` and `XmpMetadata`.
      - **PDF structure:** Access underlying PDF objects through `getParser()`, `getCatalog()`, and `getTrailer()`.
      - **Embedded files:** Manage embedded file collections with `getEmbeddedFiles()`.
    title_right: Common Document Processing Scenarios
    content_right: |
      - **PDF generation:** Build documents programmatically by creating a `Document` and populating pages.
      - **Document analysis:** Access existing files for metadata inspection or form field enumeration.
      - **Page manipulation:** Resize, rotate, or reorder pages using `PdfFileEditor` and the `Page` API.
      - **Batch workflows:** Apply consistent transformations across multiple documents in server-side pipelines.
  - title_left: Annotations and Form Fields
    content_left: |
      - **Annotation hierarchy:** The abstract `Annotation` class is the base for all annotations; concrete types include `WidgetAnnotation`, `FreeTextAnnotation`, `HighlightAnnotation`, and `UnderlineAnnotation`.
      - **Interactive forms:** Access the document form via `Document.getForm()` to enumerate or create `ButtonField`, `CheckboxField`, and `RadioButtonField` instances.
      - **Widget appearance:** Control visual properties through `AppearanceCharacteristics` — border color, background, rotation, and captions.
      - **Form editing:** Use `FormEditor` and `FormElement` for higher-level form manipulation.
    title_right: Document Review and Collaboration
    content_right: |
      - **Markup annotations:** Add highlight, underline, or free-text annotations to documents for review workflows.
      - **Interactive forms:** Build fillable documents with radio buttons, checkboxes, and push buttons for data capture.
      - **Form data extraction:** Read submitted form field values for server-side pipelines.
      - **Archival compliance:** Validate and fix documents for long-term archiving using `ActionRules` and `ActionFixes`.
  - title_left: Security and Content Operations
    content_left: |
      - **AES encryption:** Encrypt and decrypt data byte arrays with `AESCipher` using CBC mode (`AESCipher.encrypt(key, data)` and `AESCipher.decrypt(key, data)`).
      - **Artifact management:** Identify and manipulate headers, footers, watermarks, and background images via the `Artifact` and `ArtifactCollection` APIs.
      - **Content resize:** Adjust page content layout with `PdfFileEditor.resizeContents()` and `ContentsResizeParameters`.
      - **Compliance validation:** Validate annotation and action rules against PDF/A standards using `AnnotationRules` and `ActionRules`.
    title_right: Document Security Use Cases
    content_right: |
      - **Encrypted payloads:** Use `AESCipher` to encrypt data sections embedded in document streams.
      - **Document protection:** Apply security settings for access control in compliance workflows.
      - **Watermarking:** Detect and manage `Artifact` objects representing watermarks and page decorations.
      - **Archival compliance:** Run `ActionFixes` and `AnnotationFixes` to make existing documents meet PDF/A-1b archival standards.
single:
  enable: true
  block:
  - title: Create a Document with Widget Annotation
    content: |
      Create a new document, add a page, and attach a widget annotation with styling.
      ```java
      try (Document doc = new Document()) {
          Page page = doc.getPages().add();
          WidgetAnnotation w = new WidgetAnnotation(page, new Rectangle(0, 0, 100, 50));
          w.getCharacteristics().setBorder(Color.fromRgb(1, 0, 0));
          w.getCharacteristics().setCaption("Submit");
          page.getAnnotations().add(w);
          doc.save("output.pdf");
      }
      ```
  - title: Create a Form with Radio Buttons
    content: |
      Add a radio button group to a document form and select a value programmatically.
      ```java
      try (Document doc = new Document()) {
          Page page = doc.getPages().add();
          RadioButtonField radio = new RadioButtonField(page);
          radio.setPartialName("choice");
          radio.addOption("Option1", new Rectangle(50, 50, 70, 70));
          radio.addOption("Option2", new Rectangle(50, 80, 70, 100));
          doc.getForm().add(radio, 1);
          radio.setValue("Option2");
          doc.save("form.pdf");
      }
      ```
  - title: Inspect Document Page Dimensions
    content: |
      Access an existing document and read the media box dimensions of the first page.
      ```java
      try (Document doc = new Document("input.pdf")) {
          double width = doc.getPages().get(1).getMediaBox().getWidth();
          double height = doc.getPages().get(1).getMediaBox().getHeight();
          System.out.println("Page size: " + width + " x " + height);
      }
      ```
faq:
  enable: false
supportandlearning:
  enable: true
more_formats:
  enable: true
back_to_top:
  enable: true
grade: A
graded_content_hash: "e3b0c44298fc1c149afbf4c8996fb924"
grade_reasons:
  - "No findings -> grade A"
evidence:
  model_sha: 099e70a8b309fdd6ce349607dd589dfde96f2989
  model_version: 26.8.0
  claims:
  - CLM-pdf-0a0b35
  - CLM-pdf-330722b7
  - CLM-pdf-49cf003b
  - CLM-pdf-8f1af75e
  - CLM-pdf-a62c6d72
  - CLM-pdf-bf333420
  - CLM-pdf-f066419c
  - ERC-pdf-java-2db01116
  apis:
  - AESCipher
  - Annotation
  - Artifact
  - BmpDevice
  - Document
  - Document.getForm
  - Document.getPages
  - Document.save
  - Form
  - Page
  - Page.getAnnotations
  - PageCollection
  - PdfFileEditor
  - RadioButtonField
  - RadioButtonField.addOption
  - RadioButtonField.setPartialName
  - RadioButtonField.setValue
  - Rectangle
  - WidgetAnnotation
  - WidgetAnnotation.getCharacteristics
  formats:
  - ext: pdf
    support: both
  - ext: doc
    support: both
---
