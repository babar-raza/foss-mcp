---
layout: plugin
family_name: Aspose.Slides FOSS
plugin_description: Open-source Python library for creating, reading, and editing
  PowerPoint .pptx presentations. MIT licensed, pure Python, no Microsoft Office required.
plugin_platform: Python
head_title: Aspose.Slides FOSS for Python | Open-Source PowerPoint Library
head_description: Aspose.Slides FOSS for Python is a free, MIT-licensed pure-Python
  library for creating and editing PowerPoint .pptx files in Python scripts and automation
  workflows. No Microsoft Office required. Install with pip. Requires Python 3.10+.
title: Aspose.Slides FOSS for Python
description: Create, read, and edit PowerPoint presentations from Python — free and
  open-source, no Office dependency required.
submenu:
  enable: true
github_url: https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-Python
overview:
  enable: true
  title: Open-Source Python Library for PowerPoint Presentations
  content: 'Aspose.Slides FOSS for Python is a MIT-licensed pure-Python library for working with PowerPoint `.pptx` files. Install it with a single pip command and immediately start creating, reading, and editing presentations without installing Microsoft Office or any proprietary runtime. The library exposes a Presentation API built around `Presentation`, `Slide`, `Shape`, `TextFrame`, `Paragraph`, and `Portion`, the conceptual model used by PowerPoint itself. Add and remove slides, insert AutoShapes, Tables, and Connectors, format text at character level with bold, italic, font size and color, apply solid or gradient fills, and add visual effects (shadow, glow, blur, reflection). The context manager pattern ensures reliable resource cleanup: always open a `Presentation` with `with slides.Presentation(...) as prs:`. Unknown XML parts encountered during load are preserved verbatim on save, so round‑tripping never destroys content the library does not yet understand. The library requires Python 3.10 or later and depends only on `lxml`, installed automatically. Developers requiring enterprise features and production support can use [Aspose.Slides for Python — Enterprise Product](https://products.aspose.com/slides/python-net/) alongside these open-source libraries.'
content:
  enable: true
  block:
  - title_left: Presentation and Slide API
    content_left: |
      - **Create and open PPTX:** Create new presentations or open existing `.pptx` files.
      - **Add and remove slides:** Programmatically manage the slide collection.
      - **AutoShapes:** Insert rectangles, ellipses, lines, and other AutoShape types.
      - **Tables and Connectors:** Add structured table shapes and connector lines between shapes.
      - **Speaker notes:** Read and write per-slide speaker notes.
      - **Threaded comments:** Access slide-level comment threads.
    title_right: Where Aspose.Slides FOSS Can Be Used
    content_right: |
      - **Report generation:** Build branded slide decks from data sources without Office.
      - **Template automation:** Fill PPTX templates with dynamic content in CI/CD pipelines.
      - **Content migration:** Read existing presentations and restructure or re-style slides.
      - **Serverless backends:** Process PPTX files inside Docker containers or Lambda functions.
      - **Batch processing:** Apply uniform formatting changes across large slide deck libraries.
  - title_left: Text Formatting and Visual Effects
    content_left: |
      - **Character-level formatting:** Apply bold, italic, font size, and color to individual `Portion` objects.
      - **Solid and gradient fills:** Set shape fill to a solid color or multi-stop linear gradient.
      - **Shadow and glow effects:** Apply outer shadow, glow, blur, and reflection to any shape.
      - **Paragraph alignment:** Set left, center, right, or justify alignment per paragraph.
      - **Round-trip safe:** Unknown XML parts are preserved verbatim on re-save.
    title_right: Developer Experience
    content_right: |
      Aspose.Slides FOSS installs with a single pip command (see the Quick Start example above). The only runtime dependency is `lxml`, installed automatically. There are no native extensions to compile.

      The API mirrors PowerPoint's own object model (`Presentation`, `Slide`, `Shape`, `TextFrame`, `Paragraph`, `Portion`), so anyone familiar with the PowerPoint object model can use the library immediately. It is MIT-licensed, open-source on GitHub, and requires Python 3.10 or later.
single:
  enable: true
  block:
  - title: Create a Presentation and Add a Shape
    content: |
      Use the context manager (`with slides.Presentation() as prs:`) to ensure the PPTX is always closed and resources are freed. `add_auto_shape()` takes a `ShapeType` enum, then x/y position and width/height in points — call `add_text_frame()` on the shape to attach a text frame and set the label in one line.

      {{< package-install "slides" "python" >}}

      ```python
      import aspose.slides_foss as slides
      from aspose.slides_foss.export import SaveFormat

      with slides.Presentation() as prs:
          slide = prs.slides[0]

          # Add a rectangle AutoShape
          shape = slide.shapes.add_auto_shape(
              slides.ShapeType.RECTANGLE, 50, 50, 400, 150
          )
          shape.add_text_frame("Hello, Aspose.Slides!")

          prs.save("output.pptx", SaveFormat.PPTX)
       ```
  - title: Format Text and Apply a Fill Effect
    content: |
      Text formatting works at the `Portion` level — the smallest unit of a run of characters. Open the saved file, navigate to the first portion of the first paragraph, and set font properties directly. Shape fill is independent: set `fill_type` to `SOLID` and assign a color to `solid_fill_color.color`.

      ```python
      import aspose.slides_foss as slides
      from aspose.slides_foss import NullableBool, FillType
      from aspose.slides_foss.drawing import Color
      from aspose.slides_foss.export import SaveFormat

      with slides.Presentation("output.pptx") as prs:
          shape = prs.slides[0].shapes[0]
          portion = shape.text_frame.paragraphs[0].portions[0]

          # Bold, 18pt, dark-blue text
          portion.portion_format.font_bold = NullableBool.TRUE
          portion.portion_format.font_height = 18
          portion.portion_format.fill_format.fill_type = FillType.SOLID
          portion.portion_format.fill_format.solid_fill_color.color = Color.dark_blue

          # Solid background fill on the shape
          shape.fill_format.fill_type = FillType.SOLID
          shape.fill_format.solid_fill_color.color = Color.alice_blue

          prs.save("formatted.pptx", SaveFormat.PPTX)
       ```
faq:
  enable: true
  list:
  - question: What is Aspose.Slides FOSS for Python?
    answer: It is a free, MIT-licensed pure-Python library for creating, reading,
      and editing PowerPoint `.pptx` presentations without requiring Microsoft Office.
  - question: Which file formats are supported?
    answer: '`.pptx` is the supported read/write format. Export to PDF, HTML, SVG,
      or images is not available in this edition.'
  - question: Does it require Microsoft Office or PowerPoint?
    answer: No. Aspose.Slides FOSS is a pure-Python library with no dependency on
      Microsoft Office, COM automation, or any proprietary runtime.
  - question: How do I install it?
    answer: See the install command in the Quick Start example above. The only dependency
      is `lxml`, installed automatically. Python 3.10 or later is required.
  - question: Can I apply visual effects like shadow and glow?
    answer: Yes. The library supports outer shadow, glow, blur, and reflection effects
      on any shape object.
  - question: Is the context manager pattern recommended?
    answer: Yes. Always open a `Presentation` with `with slides.Presentation(...)
      as prs:` to ensure reliable resource cleanup.
  - question: Will round-tripping a PPTX destroy unknown content?
    answer: No. Unknown XML parts encountered during load are preserved verbatim on
      save, so content the library does not yet understand is never lost.
  - question: Where can I find the source code?
    answer: The library is MIT-licensed and hosted on GitHub. Bug reports and pull
      requests are welcome.
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
provenance:
  content_origin: skill-generated
  last_mechanism: manual-edit-skill
  reviewed: false
  auto_updatable: true
  content_hash: e3b0c44298fc1c149afbf4c8996fb924
  content_created_at: '2026-03-13T01:44:54+05:00'
evidence:
  model_sha: ffaf6355fdc7f0b7a66680d742051e809a9d8c5f
  model_version: 26.8.0
  claims:
  - CLM-slides-6dd43b
  - ERC-slides-python-865b4ce8
  apis:
  - FillType.SOLID
  - NullableBool.TRUE
  - Presentation
  - SaveFormat.PPTX
  - ShapeType.RECTANGLE
  formats:
  - ext: pptx
    support: both
enterprise_backlink_injected: https://products.aspose.com/slides/python-net/
---
