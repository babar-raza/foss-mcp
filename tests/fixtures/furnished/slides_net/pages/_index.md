---
layout: plugin
family_name: Aspose.Slides FOSS
plugin_description: An MIT-licensed .NET library that creates, reads and edits PowerPoint .pptx presentations by building the Office Open XML package itself — no PowerPoint installation, no COM interop, no native dependency and no NuGet dependencies at all.
plugin_platform: .NET
head_title: Aspose.Slides FOSS for .NET | Open-Source Library
head_description: An MIT-licensed .NET library that creates, reads and edits PowerPoint .pptx presentations by building the Office Open XML package itself — no PowerPoint...
title: Aspose.Slides FOSS for .NET
description: An MIT-licensed .NET library that creates, reads and edits PowerPoint .pptx presentations by building the Office Open XML package itself — no PowerPoint installation, no COM interop, no native dependency and no NuGet dependencies at all.
submenu:
  enable: true
github_url: https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-.NET
doc_source: github_readme
doc_source_commit: 86c441b5d81e707f2ea9e01b197dbb6ff7a3859b
overview:
  enable: true
  title: Aspose.Slides FOSS for .NET
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction TB
        i1["An existing PPTX presentation"]
      end
      PRODUCT["Aspose.Slides FOSS for .NET"]
      subgraph Capabilities["Core Capabilities"]
        direction LR
        subgraph capl[" "]
          direction TB
          c1["Presentation, slide, and section creation and editing"]
          c2["Shape creation (AutoShape, Table, Connector, PictureFrame)"]
          c3["Text formatting (TextFrame, Paragraph, Portion)"]
          c4["Fill, line, and 3D shape styling"]
        end
        subgraph capr[" "]
          direction TB
          c5["Visual effects (shadow, glow, blur, reflection)"]
          c6["Document properties (core, app, custom)"]
          c7["Speaker notes and classic comments"]
          c8["Image embedding"]
        end
      end
      subgraph Outputs["Outputs"]
        direction TB
        o1["PPTX, PPSX or POTX package"]
      end
      StartingPoints --> PRODUCT --> Capabilities --> Outputs
    ```

    The diagram is an overview, not a contract. Each box is expanded, with the XML it produces, under
    [What it can do](#what-it-can-do); the boundaries are under
    [What it cannot do](#what-it-cannot-do).
content:
  enable: true
  block:
  - title_left: Installation and Quick Start
    content_left: |-
      ### Installation

      ```bash
      dotnet add package Aspose.Slides.FOSS
      ```

      The package targets `net8.0` and `net10.0` and has no dependencies. Note the casing: the package id
      is `Aspose.Slides.FOSS`, while the assembly and the namespaces are `Aspose.Slides.Foss`.

      Two other routes work as well — building from source, and the package every CI run produces.

      ### Build from source

      ```bash
      git clone https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-.NET.git
      cd Aspose.Slides-FOSS-for-.NET
      dotnet build src/Aspose.Slides.Foss/Aspose.Slides.Foss.csproj --configuration Release
      ```

      Then reference the project from your own:

      ```xml
      <ItemGroup>
        <ProjectReference Include="../Aspose.Slides-FOSS-for-.NET/src/Aspose.Slides.Foss/Aspose.Slides.Foss.csproj" />
      </ItemGroup>
      ```

      ### Install the package CI built

      Every push, on any branch, whose tests pass produces a `.nupkg` and a symbol `.snupkg` as workflow
      artefacts. Download them from the
      [Actions](https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-.NET/actions) tab — GitHub
      hands you a `.zip`, so extract it first; NuGet reads a folder of `.nupkg` files, not a zip of them —
      put the extracted files in a folder, and point NuGet at that folder:

      ```xml
      <!-- nuget.config next to your solution -->
      <configuration>
        <packageSources>
          <add key="local" value="./local-packages" />
        </packageSources>
      </configuration>
      ```

      ```bash
      dotnet add package Aspose.Slides.FOSS --version 26.9.0
      ```

      Use whatever version the artefact you downloaded is called; a CI build carries the version in
      `Directory.Build.props` at that commit, which may be ahead of the last release.

      ### Quick Start

      ```csharp
      using Aspose.Slides.Foss;
      using Aspose.Slides.Foss.Export;

      using (var presentation = new Presentation())
      {
          ISlide slide = presentation.Slides[0];
          IAutoShape shape = slide.Shapes!.AddAutoShape(ShapeType.Rectangle, 50, 50, 400, 100);
          shape.AddTextFrame("Hello from Aspose.Slides FOSS");

          presentation.Save("hello.pptx", SaveFormat.Pptx);
      }

      using var reopened = new Presentation("hello.pptx");
      var reread = (IAutoShape)reopened.Slides[0].Shapes![0];

      Console.WriteLine($"slides: {reopened.Slides.Count}");
      Console.WriteLine($"shapes: {reopened.Slides[0].Shapes!.Count}");
      Console.WriteLine($"text:   {reread.TextFrame?.Text}");
      ```

      Output:

      ```
      slides: 1
      shapes: 1
      text:   Hello from Aspose.Slides FOSS
      ```

      A new `Presentation` is a 13-part package with one slide, one master, one layout and a slide size of
      `9144000 × 6858000` EMU — 4:3, `type="screen4x3"`. There is no API to change the slide size.

      ### Why the `!`

      `IBaseSlide.Shapes` is declared `IShapeCollection?` and returns `null` for a slide that is not backed
      by a package part; a few other members are annotated the same way. The samples here use the
      null-forgiving operator so that they compile without warnings in a project with
      `<Nullable>enable</Nullable>`, which is the default for new .NET projects. Every sample on this page
      was compiled with nullable enabled and produced zero warnings.
single:
  enable: true
  block:
  - title: Quick start
    content: |
      ```csharp
      using Aspose.Slides.Foss;
      using Aspose.Slides.Foss.Export;

      using (var presentation = new Presentation())
      {
          ISlide slide = presentation.Slides[0];
          IAutoShape shape = slide.Shapes!.AddAutoShape(ShapeType.Rectangle, 50, 50, 400, 100);
          shape.AddTextFrame("Hello from Aspose.Slides FOSS");

          presentation.Save("hello.pptx", SaveFormat.Pptx);
      }

      using var reopened = new Presentation("hello.pptx");
      var reread = (IAutoShape)reopened.Slides[0].Shapes![0];

      Console.WriteLine($"slides: {reopened.Slides.Count}");
      Console.WriteLine($"shapes: {reopened.Slides[0].Shapes!.Count}");
      Console.WriteLine($"text:   {reread.TextFrame?.Text}");
      ```
  - title: Examples
    content: |
      Three namespaces cover every sample on this page, and each sample below assumes all three:

      ```csharp
      using Aspose.Slides.Foss;           // Presentation, the I* interfaces, ShapeType, FontData
      using Aspose.Slides.Foss.Drawing;   // Color, PointF
      using Aspose.Slides.Foss.Export;    // SaveFormat
      ```
  - title: Text and formatting
    content: |
      ```csharp
      using var presentation = new Presentation();
      IAutoShape shape = presentation.Slides[0].Shapes!.AddAutoShape(
          ShapeType.Rectangle, 50, 50, 400, 150);
      ITextFrame textFrame = shape.AddTextFrame("Formatted text")!;

      IParagraph paragraph = textFrame.Paragraphs[0];
      paragraph.ParagraphFormat.Alignment = TextAlignment.Center;

      IBasePortionFormat format = paragraph.Portions[0].PortionFormat!;
      format.FontHeight = 24;
      format.FontBold = NullableBool.True;
      format.LatinFont = new FontData("Verdana");
      format.FillFormat!.FillType = FillType.Solid;
      format.FillFormat.SolidFillColor.Color = Color.FromArgb(255, 0, 70, 127);

      presentation.Save("text.pptx", SaveFormat.Pptx);
      ```
  - title: Tables
    content: |
      ```csharp
      using var presentation = new Presentation();
      ITable table = presentation.Slides[0].Shapes!.AddTable(
          50, 50, [150.0, 150.0], [40.0, 40.0]);

      table.Rows[0][0].TextFrame!.Text = "Region";
      table.Rows[0][1].TextFrame!.Text = "Revenue";
      table.Rows[1][0].TextFrame!.Text = "EMEA";
      table.Rows[1][1].TextFrame!.Text = "1,240";

      table.MergeCells(table.Rows[0][0], table.Rows[0][1], allowSplitting: false);

      presentation.Save("table.pptx", SaveFormat.Pptx);
      ```
  - title: Pictures
    content: |
      ```csharp
      using var presentation = new Presentation();

      // from a byte array
      IPPImage image = presentation.Images.AddImage(File.ReadAllBytes("photo.png"));
      presentation.Slides[0].Shapes!.AddPictureFrame(
          ShapeType.Rectangle, 50, 50, 200, 200, image);

      // or from a stream
      using FileStream stream = File.OpenRead("photo.png");
      IPPImage streamed = presentation.Images.AddImage(stream);

      presentation.Save("picture.pptx", SaveFormat.Pptx);
      ```
  - title: Effects
    content: |
      ```csharp
      using var presentation = new Presentation();
      IAutoShape shape = presentation.Slides[0].Shapes!.AddAutoShape(
          ShapeType.Rectangle, 50, 50, 300, 120);

      shape.FillFormat.FillType = FillType.Solid;
      shape.FillFormat.SolidFillColor.Color = Color.FromArgb(255, 30, 120, 200);

      IEffectFormat effects = shape.EffectFormat;
      effects.EnableOuterShadowEffect();
      effects.OuterShadowEffect!.BlurRadius = 10;
      effects.OuterShadowEffect.Distance = 5;

      presentation.Save("effects.pptx", SaveFormat.Pptx);
      ```
  - title: Notes and comments
    content: |
      ```csharp
      using var presentation = new Presentation();
      ISlide slide = presentation.Slides[0];

      INotesSlide notes = slide.NotesSlideManager.AddNotesSlide();
      notes.NotesTextFrame.Text = "Open with the revenue figures.";

      ICommentAuthor author = presentation.CommentAuthors.AddAuthor("Jane Smith", "JS");
      author.Comments.AddComment("Check these numbers.", slide, new PointF(2.0f, 2.0f), DateTime.UtcNow);

      presentation.Save("notes-comments.pptx", SaveFormat.Pptx);
      ```
  - title: Sections
    content: |
      ```csharp
      using var presentation = new Presentation();
      presentation.Slides.AddEmptySlide(presentation.LayoutSlides[0]);
      presentation.Slides.AddEmptySlide(presentation.LayoutSlides[0]);

      presentation.Sections.AddSection("Introduction", presentation.Slides[0]);
      presentation.Sections.AddSection("Detail", presentation.Slides[1]);

      presentation.Save("sections.pptx", SaveFormat.Pptx);
      ```
  - title: Streams
    content: |
      ```csharp
      using var presentation = new Presentation();
      presentation.Slides[0].Shapes!.AddAutoShape(ShapeType.Rectangle, 10, 10, 100, 50);

      using var buffer = new MemoryStream();
      presentation.Save(buffer, SaveFormat.Pptx);

      buffer.Position = 0;
      using var reopened = new Presentation(buffer);
      ```
faq:
  enable: false
  list: []
---
