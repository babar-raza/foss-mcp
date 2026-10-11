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
  enable: false
  block: []
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
