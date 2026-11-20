---
layout: plugin
family_name: Aspose.TeX FOSS
plugin_description: Aspose.TeX FOSS for Python is a free, open-source, pure-Python library for TeX/LaTeX processing. It parses real TeX/LaTeX source through its own from-scratch interpreter and produces PDF, DVI, and SVG output, with no external TeX installation required — Computer Modern fonts ship bundled inside the package.
plugin_platform: Python
head_title: Aspose.TeX FOSS for Python | Open-Source Library
head_description: Aspose.TeX FOSS for Python is a free, open-source, pure-Python library for TeX/LaTeX processing. It parses real TeX/LaTeX source through its own...
title: Aspose.TeX FOSS for Python
description: Aspose.TeX FOSS for Python is a free, open-source, pure-Python library for TeX/LaTeX processing. It parses real TeX/LaTeX source through its own from-scratch interpreter and produces PDF, DVI, and SVG output, with no external TeX installation required — Computer Modern fonts ship bundled inside the package.
submenu:
  enable: true
github_url: https://github.com/aspose-tex-foss/Aspose.TeX-FOSS-for-Python
doc_source: github_readme
doc_source_commit: 07db320334a0a58e11107b6ca8092b2e5047c1f8
overview:
  enable: true
  title: Aspose.TeX FOSS for Python
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction TB
        i1["A plain-text document (TeX/LaTeX source, from a path, string, or bytes)"]
      end
      PRODUCT["Aspose.TeX FOSS for Python"]
      subgraph Capabilities["Core Capabilities"]
        direction TB
        c1["Tokenize and interpret TeX/LaTeX input"]
        c2["Resolve fonts and glyph metrics"]
        c3["Build lines and pages from typeset boxes"]
        c4["Write PDF, DVI, or SVG output"]
      end
      subgraph Outputs["Outputs"]
        direction TB
        o1["PDF document (file path or in-memory bytes)"]
        o2["DVI document (file path or in-memory bytes)"]
        o3["SVG document (file path or in-memory bytes, multi-page)"]
      end
      StartingPoints --> PRODUCT --> Capabilities --> Outputs
    ```
content:
  enable: true
  block:
  - title_left: Installation
    content_left: |-
      **Requirements**: Python 3.10+.

      Install the package from PyPI:

      ```bash
      pip install aspose-tex-foss
      ```

      The package is imported as `aspose_tex`. To run the MCP server, install the `mcp` extra and start
      it with `python -m aspose_tex.mcp`:

      ```bash
      pip install "aspose-tex-foss[mcp]"
      ```

      ### From source (contributors)

      ```bash
      git clone https://github.com/aspose-tex-foss/Aspose.TeX-FOSS-for-Python.git
      cd Aspose.TeX-FOSS-for-Python
      pip install -e ".[dev]"
      ```

      The `dev` extra adds `pytest`, `ruff`, `build`, and `twine`.
  - title_left: Dependencies
    content_left: |-
      ### Required Package Dependencies

      No required third-party package dependencies. The published `aspose-tex-foss` package declares
      no runtime dependencies; the optional `mcp` extra installs the `mcp` package.

      ### Native and System Requirements

      - Requires Python 3.10 or later (`requires-python = ">=3.10"`).

      ### Development Dependencies

      - `pytest` (>=7.4), `ruff` (>=0.4), `build` (>=1.2), and `twine` (>=5.0) — the test runner,
        linter, and packaging/publishing tools used for development; `tomli` (>=2.0) is an additional
        dev dependency on Python versions before 3.11. None of these are required to use the library
        itself.
  - title_left: Quick Start
    content_left: |-
      ```python
      from aspose_tex import TeXJob, TeXOptions, PdfDevice, create_input_source

      source = create_input_source("Hello World\n\\bye")
      device = PdfDevice()
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      pdf_bytes = job.run()
      ```

      `create_input_source` accepts a `Path`, a `str`, or `bytes`. Pass a `Path` to `PdfDevice` instead
      of leaving it empty to write straight to disk — `run()` then returns `None` and the file holds the
      output:

      ```python
      from pathlib import Path
      from aspose_tex import TeXJob, TeXOptions, PdfDevice, create_input_source

      source = create_input_source("Hello World\n\\bye")
      device = PdfDevice(Path("hello.pdf"))
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      job.run() # hello.pdf is written to disk
      ```
  - title_left: Key Capabilities
    content_left: |-
      - Parse and interpret real TeX/LaTeX source through `TeXJob`, driving the tokenizer, macro
        expander, and mode-machine dispatch that process control sequences, groups, and paragraph text.
      - Resolve font metrics and glyph outlines from the bundled Computer Modern `.tfm` / `.pfb` data,
        independent of the output format a job targets.
      - Build lines and pages from typeset boxes with a real line-breaking and page-building pipeline,
        then hand each finished page to an `OutputDevice`.
      - Write finished pages through `PdfDevice`, `DviDevice`, or `SvgDevice` — pass a `Path` for file
        output, or leave the destination empty for in-memory `bytes`.
      - `SvgDevice` embeds glyph outlines straight from the bundled Computer Modern PFB fonts as
        `<path>` elements, so the rendered SVG needs no external fonts; `get_all_pages()` returns every
        page of a multi-page document as a list of byte strings.
      - Configure a job with `TeXOptions` — job name, magnification, and `extra_format_paths`, which is
        searched after the bundled format data and before the current working directory for `\input`
        files.
      - Collect run diagnostics via `TeXJob.messages`, which returns a copy of the messages collected
        during the most recent run: `\message` and `\write 16` append plain-text entries,
        `\errmessage` prefixes them with `! `, and `\write -1` logs through the standard `aspose_tex`
        logger without appearing in the returned list.
  - title_left: API Reference
    content_left: |-
      The primary entry point is `TeXJob`, which combines an `InputSource`, an `OutputDevice`
      (`PdfDevice`, `DviDevice`, or `SvgDevice`), and `TeXOptions` into one runnable job.

      <details>
      <summary>View the Core API Surface</summary>

      ### Core API

      | Class | Description |
      | `AsposeTeXError` | Base exception for all aspose_tex errors. |
      | `EngineError` | Raised for TeX engine errors (infinite recursion, undefined register, etc.). |
      | `FontError` | Raised for font-related failures (TFM not found, corrupt file, invalid char, etc.). |
      | `InputError` | Raised for input reading failures (file not found, stack underflow, etc.). |

      ### Presentation

      | Class | Description |
      | `DviDevice` | DVI output device — wraps `DviWriter`. |
      | `OutputDevice` | Abstract base class for all output devices. |
      | `PdfDevice` | PDF output device — wraps `PdfWriter`. |
      | `SvgDevice` | SVG output device — wraps `SvgWriter`. |
      | `TeXJob` | Main entry point for processing TeX input. |
      | `TeXOptions` | Configuration for a TeX processing job — job name, magnification, and font/format search paths. |

      #### Enumerations

      | Enumeration | Description |
      | `OutputFormat` | Supported output formats: `DVI`, `PDF`, `SVG`. |


      #### Detailed Member Reference

      ### Processing

      - `TeXJob`
        - `TeXJob(source, device, options=None)`
        - `run() -> bytes | None`
        - `messages -> list[str]`
      - `TeXOptions`
        - `job_name: str = "texput"`
        - `magnification: int = 1000`
        - `extra_font_paths: list[Path]`
        - `load_format: bool | str | None = "plain"`
        - `extra_format_paths: list[Path]`

      ### Output Devices

      - `OutputDevice`
        - `destination -> Path | io.BytesIO`
        - `finalize()`
        - `get_bytes() -> bytes | None`
      - `PdfDevice(destination=None)`
      - `DviDevice(destination=None)`
      - `SvgDevice(destination=None)`
        - `get_all_pages() -> list[bytes] | None`

      ### Input Sources

      - `create_input_source(source)` — factory returning a `FileInputSource` or `StringInputSource`
        from a `Path`, `str`, or `bytes`.

      ### Exceptions

      - `AsposeTeXError` — base exception for the package.
      - `InputError`
      - `EngineError`
      - `FontError`

      </details>
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.TeX FOSS for Python, straight from its README at the pinned commit:

      - This is a Pre-Alpha release: the core engine and public API are under active development, and
        interfaces may change between releases.
      - Plain TeX is the supported format. The default `load_format="plain"` option (`TeXOptions`) loads
        Knuth's macros from the bundled `plain.tex`, with its output routine, footnotes, and inserts.
        The examples above pass `load_format=False` to run a document from primitives alone; leave it
        out to use the plain macros.
      - LaTeX and the e-TeX and pdfTeX extensions are not implemented yet. `\documentclass`,
        `\numexpr`, `\pdfoutput`, and similar commands raise `EngineError`.
      - `\special` is not implemented and raises `EngineError` as an undefined control sequence.
      - Automatic hyphenation needs the plain format, which loads the bundled US-English patterns from
        `format/hyphen.tex`. With `load_format=False` no patterns are loaded. Other languages are added
        the TeX way: a format file on `extra_format_paths` sets `\language` and runs `\patterns` and
        `\hyphenation`. The bundled `hyphenation/` directory is reserved and empty.
      - Named file-stream I/O (`\openin`, `\openout`, `\closein`, `\closeout`, `\read`, and `\write` to
        streams 0 through 15) raises `NotImplementedError` — only `\write 16` (message log), `\write -1`
        (silent log), `\message`, and `\errmessage` are implemented; `\ifeof` always evaluates false.
        `\write` takes effect at once, not at shipout.

      These limitations don't apply to
      [Aspose.TeX for Python — Enterprise Edition](https://products.aspose.com/tex/python-net/), which
      adds full feature completeness, broader format coverage, and commercial support.
single:
  enable: true
  block:
  - title: Quick Start
    content: |
      ```python
      from aspose_tex import TeXJob, TeXOptions, PdfDevice, create_input_source

      source = create_input_source("Hello World\n\\bye")
      device = PdfDevice()
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      pdf_bytes = job.run()
      ```
  - title: Quick Start (2)
    content: |
      `create_input_source` accepts a `Path`, a `str`, or `bytes`. Pass a `Path` to `PdfDevice` instead
      of leaving it empty to write straight to disk — `run()` then returns `None` and the file holds the
      output:

      ```python
      from pathlib import Path
      from aspose_tex import TeXJob, TeXOptions, PdfDevice, create_input_source

      source = create_input_source("Hello World\n\\bye")
      device = PdfDevice(Path("hello.pdf"))
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      job.run() # hello.pdf is written to disk
      ```
  - title: DVI Output
    content: |
      ```python
      from aspose_tex import TeXJob, TeXOptions, DviDevice, create_input_source

      source = create_input_source("Hello World\n\\bye")
      device = DviDevice()
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      dvi_bytes = job.run()
      ```
  - title: DVI Output (2)
    content: |
      DVI can also be written directly to a file instead of into memory:

      ```python
      from pathlib import Path
      from aspose_tex import TeXJob, TeXOptions, DviDevice, create_input_source

      source = create_input_source("Hello World\n\\bye")
      device = DviDevice(Path("hello.dvi"))
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      job.run()  # returns None; output is on disk
      ```
  - title: SVG Output
    content: |
      ```python
      from aspose_tex import TeXJob, TeXOptions, SvgDevice, create_input_source

      source = create_input_source("Hello World\n\\bye")
      device = SvgDevice()
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      svg_bytes = job.run()  # UTF-8 encoded SVG 1.1 document
      ```
  - title: SVG Output (2)
    content: |
      Writing to a file path picks the file name automatically (`hello.svg`, or `hello-1.svg` /
      `hello-2.svg` for a multi-page document):

      ```python
      from pathlib import Path
      from aspose_tex import TeXJob, TeXOptions, SvgDevice, create_input_source

      source = create_input_source("Hello World\n\\bye")
      device = SvgDevice(Path("output/hello"))
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      job.run()  # writes output/hello.svg
      ```
  - title: SVG Output (3)
    content: |
      A multi-page document run in memory returns one SVG byte string per page:

      ```python
      from aspose_tex import TeXJob, TeXOptions, SvgDevice, create_input_source

      source = create_input_source("Page one\n\\eject\nPage two\n\\bye")
      device = SvgDevice()
      job = TeXJob(source, device, options=TeXOptions(load_format=False))
      job.run()
      pages = device.get_all_pages()  # list[bytes], one SVG per page
      ```
  - title: Job Options
    content: |
      ```python
      from aspose_tex import TeXJob, TeXOptions, PdfDevice, create_input_source

      opts = TeXOptions(job_name="hello", magnification=1200, load_format=False)
      source = create_input_source("Hello World\n\\bye")
      device = PdfDevice()
      job = TeXJob(source, device, options=opts)
      pdf_bytes = job.run()
      ```
  - title: Messages and Logging
    content: |
      ```python
      from pathlib import Path
      from aspose_tex import TeXJob, TeXOptions, DviDevice, create_input_source

      opts = TeXOptions(load_format=False, extra_format_paths=[Path("tex-inputs")])
      source = create_input_source("\\input chapter1\n\\bye")
      job = TeXJob(source, DviDevice(), options=opts)
      dvi_bytes = job.run()
      messages = job.messages
      ```
  - title: Messages and Logging (2)
    content: |
      The `extra_format_paths` option above is searched after the bundled format data and before the
      current working directory when resolving `\input` files. To see log output from the engine (the
      package attaches only a `logging.NullHandler` by default):

      ```python
      import logging

      logging.basicConfig(level=logging.INFO)
      logging.getLogger("aspose_tex").setLevel(logging.INFO)
      ```
faq:
  enable: false
  list: []
---
