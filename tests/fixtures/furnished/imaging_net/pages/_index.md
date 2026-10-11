---
layout: plugin
family_name: Aspose.Imaging FOSS
plugin_description: Zero-dependency image format detection and header probing for .NET.
plugin_platform: .NET
head_title: Aspose.Imaging FOSS for .NET | Open-Source Library
head_description: Zero-dependency image format detection and header probing for .NET.
title: Aspose.Imaging FOSS for .NET
description: Zero-dependency image format detection and header probing for .NET.
submenu:
  enable: true
github_url: https://github.com/aspose-imaging-foss/Aspose.Imaging-FOSS-for-.NET
doc_source: github_readme
doc_source_commit: c5e77bdc7b4a5c19190b6dfae5ae9d420b5886fa
overview:
  enable: false
content:
  enable: true
  block:
  - title_left: Installation and Quick Start
    content_left: |-
      ### Installation

      ```
      dotnet add package Aspose.Imaging.Foss
      ```

      ### Quick Start

      ```csharp
      using Aspose.Imaging.Foss;

      // From a file path
      var info = ImageProbe.ProbeFile("photo.jpg");
      Console.WriteLine($"{info.Format}: {info.Width}x{info.Height}, {info.BitDepth}-bit");

      // From a stream (seekable or not) or a byte array
      var format = ImageProbe.DetectFormat(bytes);
      var info2 = ImageProbe.Probe(stream);
      ```

      `Probe` never throws on malformed or truncated input — if a file matches a
      format's signature but its header can't be fully parsed, you get back an
      `ImageInfo` with just the `Format` set rather than an exception.
    title_right: Key Capabilities
    content_right: |-
      ### Key Capabilities

      Most .NET image libraries make you fully decode an image just to answer "is
      this a JPEG, and how big is it?". `ImageSharp.Image.Identify` covers common
      raster formats well, but nothing in the .NET FOSS ecosystem does the same for
      PSD, EMF/WMF, or DICOM. This library covers that full range in one place.

      If you need to actually **decode, edit, or convert** any of these formats,
      that's a different job — see [Aspose.Imaging](https://products.aspose.com/imaging/net/)
      for a full-featured commercial SDK covering all of these formats and more.

      ### Supported formats

      | Format | Detect | Width / Height | Bit depth | Frame count |
      | PNG    | ✅ | ✅ | ✅ | ✅ |
      | JPEG   | ✅ | ✅ | ✅ | — |
      | GIF    | ✅ | ✅ | — | ✅ |
      | BMP    | ✅ | ✅ | ✅ | — |
      | WebP (lossy / lossless / extended) | ✅ | ✅ | — | — |
      | ICO    | ✅ | ✅ | ✅ | ✅ |
      | TIFF   | ✅ | ✅ | ✅ | ✅ |
      | PSD    | ✅ | ✅ | ✅ | — |
      | EMF    | ✅ | ✅ (from bounds) | — | — |
      | WMF (placeable) | ✅ | ✅ (assumes 96 DPI) | — | — |
      | DICOM  | ✅ | ✅ (Rows/Columns) | ✅ (BitsAllocated) | — |

      DICOM parsing covers Implicit VR Little Endian and Explicit VR Little/Big Endian transfer
      syntaxes (including the common compressed ones, whose non-pixel elements are Explicit VR
      Little Endian); it stops rather than guesses if it hits a sequence or encapsulated pixel data
      with an undefined length before finding Rows/Columns/BitsAllocated.

      Planned: CDR, DjVu.
single:
  enable: true
  block:
  - title: Usage
    content: |
      ```csharp
      using Aspose.Imaging.Foss;

      // From a file path
      var info = ImageProbe.ProbeFile("photo.jpg");
      Console.WriteLine($"{info.Format}: {info.Width}x{info.Height}, {info.BitDepth}-bit");

      // From a stream (seekable or not) or a byte array
      var format = ImageProbe.DetectFormat(bytes);
      var info2 = ImageProbe.Probe(stream);
      ```
faq:
  enable: false
  list: []
---
