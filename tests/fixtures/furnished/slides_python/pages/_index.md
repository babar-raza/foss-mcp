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
doc_source_commit: becd199a776682785768c60c778558d975b0c2f9
overview:
  enable: true
  title: Aspose.Slides FOSS for Python
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction LR
        i1["An existing PPTX file"]
      end
      PRODUCT["Aspose.Slides FOSS for Python"]
      subgraph Capabilities["Core Capabilities"]
        direction LR
        subgraph capl[" "]
          direction TB
          c1["Presentation I/O"]
          c2["Slide and shape management"]
          c3["Text formatting"]
          c4["Charts and data"]
        end
        subgraph capr[" "]
          direction TB
          c5["Slide transitions and animations"]
          c6["Comments and notes"]
          c7["Document properties"]
          c8["Image embedding"]
        end
      end
      subgraph Outputs["Outputs"]
        direction TB
        o1["PPTX file"]
      end
      StartingPoints --> PRODUCT --> Capabilities --> Outputs
    ```
content:
  enable: true
  block:
  - title_left: Installation, Dependencies, and Quick Start
    content_left: |-
      ### Installation

      Install the published package from PyPI (`aspose-slides-foss`, version 26.8.0):

      ```bash
      pip install aspose-slides-foss
      ```

      To work from a source checkout instead, install the clone with pip:

      ```bash
      git clone https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-Python.git
      cd Aspose.Slides-FOSS-for-Python
      pip install .
      ```

      Verify the install:

      ```bash
      python -c "import aspose.slides_foss"
      ```

      The package declares `python_requires` as `>=3.10`.

      ### Required Package Dependencies

      - `lxml>=4.9`

      ### Native and System Requirements

      - Requires Python 3.10 or later (`python_requires=">=3.10"` in `pyproject.toml`).

      ### Development Dependencies

      - `pytest>=7` (extra `test`)
      - `python-pptx>=1.0` (extra `test`)

      ### Quick Start

      The example creates a new presentation from scratch and saves it as PPTX, then opens that file to read its slide count and save a modified copy, demonstrating the core workflow with the aspose-slides-foss package.

      ```python
      import aspose.slides_foss as slides
      from aspose.slides_foss.export import SaveFormat

      # Create a new presentation — needs no input file, so this runs as it stands
      with slides.Presentation() as prs:
          slide = prs.slides[0]
          prs.save("new.pptx", SaveFormat.PPTX)

      # Open an existing presentation
      with slides.Presentation("new.pptx") as prs:
          print(f"Slides: {len(prs.slides)}")
          prs.save("output.pptx", SaveFormat.PPTX)
      ```
    title_right: Key Capabilities and API Reference
    content_right: |-
      ### Key Capabilities

      - **Presentation I/O.** Create new presentations from scratch or open existing `.pptx` files and save them back to `.pptx` format using the `Presentation` class.
      - **Slide and shape management.** Add rectangles, tables, connectors, group shapes, and picture frames to slides using the `ShapeCollection` methods `add_auto_shape`, `add_table`, `add_connector`, `add_group_shape`, and `add_picture_frame`.
      - **Text formatting.** Set font height and bold formatting on text portions and apply solid fill colors to shapes using the `BasePortionFormat.font_height`, `BasePortionFormat.font_bold`, and `Shape.fill_format` properties.
      - **Charts and data.** Insert clustered column charts and configure their data using an embedded workbook, categories, and series via the `ChartType.CLUSTERED_COLUMN` chart type and related chart data members.
      - **Slide transitions and animations.** Apply circle transitions to slides and configure them to advance on click or after a specified time in milliseconds using `TransitionType.CIRCLE`, `ISlideShowTransition.advance_on_click`, and `ISlideShowTransition.advance_after_time`.
      - **Comments and notes.** Add speaker notes slides and comments with author information and timestamps using `NotesSlideManager.add_notes_slide`, `CommentAuthorCollection.add_author`, and `CommentCollection.add_comment`.
      - **Document properties.** Set built-in properties like title and author and define custom properties such as Version using `DocumentProperties.set_custom_property_value` and `Comment.author`.
      - **Image embedding.** Embed images from `.png` files into presentations by loading them with `ImageCollection.add_image` and placing them as picture frames on slides.

      ### API Reference

      Aspose.Slides FOSS for Python provides the `slides_foss` module for working with presentation files, where the `Presentation` class serves as the main entry point for creating, loading, and manipulating presentations.

      The verified public surface has 516 types.

      <details>
      <summary>View the Complete Public API Surface</summary>

      ### Core API

      | Class | Description |
      | `AdjustValue` | Represents a geometry shape's adjustment value. These values affect shape's form. |
      | `AdjustValueCollection` | Reprasents a collection of shape's adjustments. |
      | `AutoShape` | Represents an AutoShape. |
      | `Background` | Represents background of a slide. |
      | `BaseHandoutNotesSlideHeaderFooterManager` | The BaseHandoutNotesSlideHeaderFooterManager class provides header and footer management for handout and notes slides in a presentation. |
      | `BasePortionFormat` | Common text portion formatting properties. |
      | `BaseShapeLock` | The BaseShapeLock class defines base locking behavior for shape properties to prevent unintended modifications. |
      | `BaseSlide` | Represents common data for all slide types. |
      | `BulletFormat` | Represents paragraph bullet formatting properties. |
      | `Camera` | Represents Camera. |
      | `Cell` | Represents a cell of a table. |
      | `CellCollection` | Represents a collection of cells. |
      | `CellFormat` | Represents format of a table cell. |
      | `ColorFormat` | Represents a color used in a presentation. |
      | `Column` | Represents a column in a table. |
      | `ColumnCollection` | Represents collection of columns in a table. |
      | `Comment` | Represents a comment on a slide. |
      | `CommentAuthor` | Represents an author of comments. |
      | `CommentAuthorCollection` | Represents a collection of comment authors. |
      | `CommentCollection` | Represents a collection of comments of one author. |
      | `Connector` | Represents a connector. |
      | `DocumentProperties` | Represents properties of a presentation. |
      | `EffectFormat` | Represents effect properties of shape. |
      | `FillFormat` | Represents a fill formatting options. |
      | `FontData` | Represents a font definition. Immutable. |
      | `Fonts` | Fonts collection. |
      | `GeometryShape` | The GeometryShape class represents a shape with geometric properties such as BENT_CONNECTOR3, CIRCLE, and RECTANGLE. |
      | `GlobalLayoutSlideCollection` | Represents a collection of all layout slides in presentation. Extends LayoutSlideCollection class with methods for adding/cloning layout slides in context of uniting of the individual collections of master's layout slides. |
      | `GradientFormat` | Represent a gradient format. |
      | `GradientStop` | Represents a gradient format. |
      | `GradientStopCollection` | Represnts a collection of gradient stops. |
      | `GraphicalObject` | The GraphicalObject class represents a graphical element in a slide that supports fill, line, and 3D formatting. |
      | `GraphicalObjectLock` | The GraphicalObjectLock class extends BaseShapeLock to lock specific properties of graphical objects. |
      | `GroupShape` | Represents a group of shapes on a slide. |
      | `GroupShapeLock` | Determines which operations are disabled on the parent GroupShape. |
      | `HeadingPair` | Represents a 'Heading pair' property of the document. It indicates the group name of document parts and the number of parts in group. |
      | `Hyperlink` | Represents a hyperlink to an external target. |
      | `IAdjustValue` | Represents a geometry shape's adjustment value. These values affect shape's form. |
      | `IAdjustValueCollection` | Reprasents a collection of shape's adjustments. |
      | `IAnimationTimeLine` | Represents timeline of animation. |
      | `IAutoShape` | Represents an AutoShape. |
      | `IBackground` | Represents background of a slide. |
      | `IBackgroundEffectiveData` | Immutable object which contains effective background properties. |
      | `IBasePortionFormat` | This class contains the text portion formatting properties. Unlike , all properties of this class are writeable. |
      | `IBaseShapeLock` | Represents Shape lock (disabled operation). |
      | `IBaseSlide` | Represents common data for all slide types. |
      | `IBulkTextFormattable` | Represents an object with possibility of bulk setting child text elements' formats. |
      | `IBulletFormat` | Represents paragraph bullet formatting properties. |
      | `ICamera` | Represents Camera. |
      | `ICell` | Represents a cell in a table. |
      | `ICellCollection` | Represents a collection of cells. |
      | `ICellFormat` | Represents format of a table cell. |
      | `IColorFormat` | Represents a color used in a presentation. |
      | `IColumn` | Represents a column in a table. |
      | `IColumnCollection` | Represents collection of columns in a table. |
      | `IComment` | Represents a comment on a slide. |
      | `ICommentAuthor` | Represents an author of comments. |
      | `ICommentAuthorCollection` | Represents a collection of comment authors. |
      | `ICommentCollection` | Represents a collection of comments of one author. |
      | `IConnector` | Represents a connector. |
      | `IDocumentProperties` | Represents properties of a presentation. |
      | `IEffectFormat` | Represents effect properties of shape. |
      | `IEffectParamSource` | The IEffectParamSource interface defines a source for effect parameters used in slide animations. |
      | `IFillFormat` | Represents a fill formatting options. |
      | `IFillParamSource` | The IFillParamSource interface defines a source for fill parameters used in shape rendering. |
      | `IFontData` | Represents a font definition. |
      | `IFonts` | Represents fonts collection. |
      | `IGeometryShape` | Represents the parent class for all geometric shapes. |
      | `IGlobalLayoutSlideCollection` | Represents a collection of all layout slides in presentation. Extends ILayoutSlideCollection interface with methods for adding/cloning layout slides in context of uniting of the individual collections of master's layout slides. |
      | `IGradientFormat` | Represent a gradient format. |
      | `IGradientStop` | Represents a gradient format. |
      | `IGradientStopCollection` | Represnts a collection of gradient stops. |
      | `IGraphicalObject` | Represents abstract graphical object. |
      | `IGroupShape` | Represents a group of shapes on a slide. |
      | `IGroupShapeLock` | Determines which operations are disabled on the parent GroupShape. |
      | `IHeadingPair` | Represents a 'Heading pair' property of the document. It indicates the group name of document parts and the number of parts in group. |
      | `IHyperlink` | Represents a hyperlink. |
      | `IHyperlinkContainer` | Represents an object that can carry hyperlinks. |
      | `IImage` | Represents a raster or vector image. |
      | `IImageCollection` | Represents collection of PPImage. |
      | `ILayoutSlide` | Represents a layout slide. |
      | `ILayoutSlideCollection` | Represents a base class for collection of a layout slides. |
      | `ILightRig` | Represents LightRig. |
      | `ILineFillFormat` | Represents properties for lines filling. |
      | `ILineFormat` | Represents format of a line. |
      | `ILineParamSource` | The ILineParamSource interface defines a source for line parameters used in shape rendering. |
      | `ILoadOptions` | The ILoadOptions interface specifies options for loading a presentation file. |
      | `IMasterLayoutSlideCollection` | The IMasterLayoutSlideCollection interface represents a collection of master layout slides in a presentation. |
      | `IMasterSlide` | Represents a master slide in a presentation. |
      | `IMasterSlideCollection` | Represents a collection of master slides. |
      | `INotesSize` | Represents a size of notes slide. |
      | `INotesSlide` | Represents a notes slide in a presentation. |
      | `INotesSlideHeaderFooterManager` | Represents manager which holds behavior of the notes slide placeholders, including header placeholder. |
      | `INotesSlideManager` | Notes slide manager. |
      | `IPPImage` | Represents an image in a presentation. |
      | `IParagraph` | Represents a paragraph of a text. |
      | `IParagraphCollection` | Represents a collection of a paragraphs. |
      | `IParagraphFormat` | This class contains the paragraph formatting properties. Unlike , all properties of this class are writeable. |
      | `IPatternFormat` | Represents a pattern to fill a shape. |
      | `IPictureFillFormat` | Represents a picture fill style. |
      | `IPictureFrame` | Represents a frame with a picture inside. |
      | `IPictureFrameLock` | Determines which operations are disabled on the parent PictureFrameEx. |
      | `IPortion` | Represents a portion of text inside a text paragraph. |
      | `IPortionCollection` | Represents a collection of a portions. |
      | `IPortionFormat` | The IPortionFormat interface defines formatting properties for text portions including font, color, and hyperlinks. |
      | `IPresentation` | Presentation document |
      | `IPresentationComponent` | Represents a component of a presentation. |
      | `IRow` | Represents a row in a table. |
      | `IRowCollection` | Represents table row collection. |
      | `ISection` | The ISection interface represents a section in a presentation that groups related slides. |
      | `IShape` | Represents a shape on a slide. |
      | `IShapeBevel` | Represents properties of shape's main face relief. |
      | `IShapeCollection` | Represents a collection of shapes. |
      | `IShapeFrame` | Represents shape frame's properties. |
      | `ISlide` | Represents a slide in a presentation. |
      | `ISlideCollection` | Represents a collection of a slides. |
      | `ISlideComponent` | Represents a component of a slide. |
      | `ISlideShowTransition` | Represents slide show transition. |
      | `ISlidesPicture` | Represents a picture in a presentation. |
      | `ITable` | Represents a table on a slide. |
      | `ITableFormat` | Represents format of a table. |
      | `ITextFrame` | Represents a TextFrame. |
      | `ITextFrameFormat` | Contains the TextFrame's formatting properties. |
      | `IThreeDFormat` | Represents 3-D properties. |
      | `IThreeDParamSource` | The IThreeDParamSource interface defines a source for 3D parameters used in shape rendering. |
      | `Image` | Represents a raster or vector image. |
      | `ImageCollection` | Represents collection of PPImage. |
      | `Images` | Methods to instantiate and work with . |
      | `LayoutSlide` | Represents a layout slide. |
      | `LayoutSlideCollection` | Represents a base class for collection of a layout slides. |
      | `LightRig` | Represents LightRig. |
      | `LineFillFormat` | Represents properties for lines filling. |
      | `LineFormat` | Represents format of a line. |
      | `MasterLayoutSlideCollection` | Represents a collections of all layout slides of defined master slide. Extends LayoutSlideCollection class with methods for adding/inserting/removing/cloning/reordering layout slides in context of the individual collections of master's layout slides. |
      | `MasterSlide` | Represents a master slide in a presentation. |
      | `MasterSlideCollection` | Represents a collection of master slides. |
      | `NotesSize` | Represents a size of notes slide. |
      | `NotesSlide` | Represents a notes slide in a presentation. |
      | `NotesSlideHeaderFooterManager` | Represents manager which holds behavior of the notes slide placeholders, including header placeholder. |
      | `NotesSlideManager` | Notes slide manager. |
      | `PPImage` | Represents an image in a presentation. |
      | `PVIObject` | Encapsulates basic service infrastructure for objects can be a subject of property value inheritance. |
      | `Paragraph` | Represents a paragraph of text. |
      | `ParagraphCollection` | Represents a collection of a paragraphs. |
      | `ParagraphFormat` | This class contains the paragraph formatting properties. Unlike , all properties of this class are writeable. |
      | `PatternFormat` | Represents a pattern to fill a shape. |
      | `Picture` | Represents a picture in a presentation. |
      | `PictureFillFormat` | Represents a picture fill style. |
      | `PictureFrame` | Represents a frame with a picture inside. |
      | `PictureFrameLock` | Determines which operations are disabled on the parent PictureFrame. |
      | `Portion` | Represents a portion of text inside a text paragraph. |
      | `PortionCollection` | Represents a collection of portions. |
      | `PortionFormat` | This class contains the text portion formatting properties. Unlike , all properties of this class are writeable. |
      | `Presentation` | Represents a Microsoft PowerPoint presentation. |
      | `Row` | Represents a row in a table. |
      | `RowCollection` | Represents table row collection. |
      | `Shape` | Represents a shape on a slide. This is an abstract base class. |
      | `ShapeBevel` | Contains the properties of shape's main face relief. |
      | `ShapeCollection` | Represents a collection of shapes. |
      | `ShapeFrame` | Represents shape frame's properties. |
      | `Slide` | Represents a slide in a presentation. |
      | `SlideCollection` | Represents a collection of a slides. |
      | `Table` | Represents a table on a slide. |
      | `TableFormat` | Represents format of a table. |
      | `TextFrame` | Represents a TextFrame. |
      | `TextFrameFormat` | Contains the TextFrame's formatTextFrameFormatting properties. |
      | `ThreeDFormat` | Represents 3-D properties. |
      | `AnimationTimeLine` | Represents timeline of animation. |
      | `Behavior` | Represent base class behavior of effect. |
      | `BehaviorCollection` | Represents collection of behavior effects. |
      | `BehaviorFactory` | Factory for creating behavior effect instances. |
      | `BehaviorProperty` | Represent property types for animation behavior. Follows the list of properties from https://msdn.microsoft.com/en-us/library/dd949052(v=office.15).aspx and https://msdn.microsoft.com/en-us/library/documentformat.openxml.presentation.attributename(v=office.15).aspx |
      | `BehaviorPropertyCollection` | Represents collection of behavior properties. |
      | `ColorEffect` | Represent color effect behavior of effect. |
      | `ColorOffset` | Represent color offset. |
      | `CommandEffect` | Represent command effect behavior of effect. |
      | `Effect` | Represents animation effect. |
      | `FilterEffect` | Represent filter effect behavior of effect. |
      | `IBehavior` | Represent base class behavior of effect. |
      | `IBehaviorCollection` | Represents collection of behavior effects. |
      | `IBehaviorFactory` | Allows to create animation effects |
      | `IBehaviorProperty` | Represent property types for animation behavior. Follows the list of properties from https://msdn.microsoft.com/en-us/library/dd949052(v=office.15).aspx and https://msdn.microsoft.com/en-us/library/documentformat.openxml.presentation.attributename(v=office.15).aspx |
      | `IBehaviorPropertyCollection` | Represents timing properties for the effect behavior. |
      | `IColorEffect` | Represents a color effect for an animation behavior. |
      | `IColorOffset` | Represent color offset. |
      | `ICommandEffect` | Represents a command effect for an animation behavior. |
      | `IEffect` | Represents animation effect. |
      | `IFilterEffect` | Represent filter effect of behavior. |
      | `IMotionCmdPath` | Represent one command of a path. |
      | `IMotionEffect` | Represent motion effect behavior of effect. |
      | `IMotionPath` | Represent motion path. |
      | `IPoint` | Represent animation point. |
      | `IPointCollection` | Represents a collection of portions. |
      | `IPropertyEffect` | Represent property effect behavior. |
      | `IRotationEffect` | Represent rotation behavior of effect. |
      | `IScaleEffect` | Represents animation scale effect. |
      | `ISequence` | Represents sequence (collection of effects). |
      | `ISequenceCollection` | Represents collection of interactive sequences. |
      | `ISetEffect` | Represents a set effect for an animation behavior. |
      | `ITextAnimation` | Represent text animation. |
      | `ITextAnimationCollection` | Represents collection of text animations. |
      | `ITiming` | Represents animation timing. |
      | `MotionCmdPath` | Represent one command of a path. |
      | `MotionEffect` | Represent motion effect behavior of effect. |
      | `MotionPath` | Represent motion path. |
      | `Point` | Represents animation point. |
      | `PointCollection` | Represents a collection of animation points. |
      | `PropertyEffect` | Represent property effect behavior of effect. |
      | `RotationEffect` | Represent rotation effect behavior of effect. |
      | `ScaleEffect` | Represent scale effect behavior of effect. |
      | `Sequence` | Represents sequence (collection of effects). |
      | `SequenceCollection` | Represents collection of interactive sequences. |
      | `SetEffect` | Represent set effect behavior of effect. |
      | `TextAnimation` | Represent text animation. |
      | `TextAnimationCollection` | Represents collection of text animations. |
      | `Timing` | Represents animation timing. |
      | `AxesManager` | Provides access to chart axes. |
      | `Axis` | Encapsulates the object that represents a chart's axis. |
      | `BaseChartValue` | Base class for chart value types. |
      | `Chart` | Represents a chart on a slide. |
      | `ChartCategory` | Represents a chart category. |
      | `ChartCategoryCollection` | Represents collection of chart categories. |
      | `ChartData` | Represents data used for chart plotting. |
      | `ChartDataCell` | Represents a cell in the chart data workbook. |
      | `ChartDataPoint` | Represents a series data point. |
      | `ChartDataPointCollection` | Represents collection of data points for a series. |
      | `ChartDataWorkbook` | Provides access to the embedded Excel workbook for chart data. |
      | `ChartDataWorksheet` | Represents a worksheet in the chart data workbook. |
      | `ChartLinesFormat` | Represents gridlines format properties. |
      | `ChartPlotArea` | Represents rectangle where chart should be plotted. |
      | `ChartPortionFormat` | Chart portion formatting — wraps <a:defRPr> inside <c:txPr>. |
      | `ChartSeries` | Represents a chart series. |
      | `ChartSeriesCollection` | Represents collection of chart series. |
      | `ChartSeriesGroup` | Represents group of series. |
      | `ChartSeriesGroupCollection` | Collection of ChartSeriesGroup objects. |
      | `ChartSeriesReadonlyCollection` | Readonly view of chart series belonging to a single series group. |
      | `ChartTextFormat` | Specifies default text formatting for chart text elements. |
      | `ChartTitle` | Represents chart title properties. |
      | `ChartWall` | Represents walls on 3D charts. |
      | `DataLabel` | Represents a series data point label. |
      | `DataLabelCollection` | Represents the labels of a chart series. |
      | `DataLabelFormat` | Represents formatting options for DataLabel. |
      | `DataSourceTypeForErrorBarsCustomValues` | Specifies types of values in ChartDataPoint.ErrorBarsCustomValues properties list. |
      | `DataTable` | Represents data table properties. |
      | `DoubleChartValue` | Represents a double value backed by a workbook cell or literal. |
      | `ErrorBarsCustomValues` | Specifies the error bar values for a single data point. |
      | `ErrorBarsFormat` | Represents error bars of chart series. |
      | `Format` | Represents chart format properties (fill, line, effect, 3D). |
      | `IActualLayout` | Specifies actual position of a chart element. |
      | `IAxesManager` | Provides access to chart axes. |
      | `IAxis` | Encapsulates the object that represents a chart's axis. |
      | `IAxisFormat` | Represents chart format properties. |
      | `IBaseChartValue` | Represents a value of a chart. |
      | `IChart` | Represents an graphic chart on a slide. |
      | `IChartCategory` | Represents chart categories. |
      | `IChartCategoryCollection` | Represents collection of |
      | `IChartCategoryLevelsManager` | Managed container of the values of the chart category levels. |
      | `IChartCellCollection` | Represents collection of a cells with data. |
      | `IChartComponent` | Represents a component of a chart. |
      | `IChartData` | Represents data used for a chart plotting. |
      | `IChartDataCell` | Represents cell for chart data. |
      | `IChartDataPoint` | Represents series data point. |
      | `IChartDataPointCollection` | Represents collection of a series data point. |
      | `IChartDataWorkbook` | Provides access to embedded Excel workbook |
      | `IChartDataWorksheet` | Represents worksheet associated with |
      | `IChartDataWorksheetCollection` | Represents the collection of worksheets of chart data workbook. |
      | `IChartLinesFormat` | Represents gridlines format properties. |
      | `IChartParagraphFormat` | Represents a paragraph formatting properties of a chart. |
      | `IChartPlotArea` | Represents chart title properties. |
      | `IChartPortionFormat` | Represents the chart portion formatting properties used in charts. |
      | `IChartSeries` | Represents a chart series. |
      | `IChartSeriesCollection` | Represents collection of |
      | `IChartSeriesGroup` | Represents group of series. |
      | `IChartSeriesGroupCollection` | Represents the collection of groups of combinable series. |
      | `IChartSeriesReadonlyCollection` | Represents a readonly collection of |
      | `IChartTextBlockFormat` | Represents formatting properties for chart text elements. |
      | `IChartTextFormat` | Chart operate with restricted set of text format properties. IChartTextFormat, IChartTextBlockFormat, IChartParagraphFormat, IChartPortionFormat interfaces describe this restricted set. |
      | `IChartTitle` | Represents chart title properties. |
      | `IChartWall` | Represents walls on 3d charts. |
      | `IDataLabel` | Represents a series labels. |
      | `IDataLabelCollection` | Represents a series labels. |
      | `IDataLabelFormat` | Represents formatting options for DataLabel. |
      | `IDataSourceTypeForErrorBarsCustomValues` | Specifies types of values in ChartDataPoint.ErrorBarsCustomValues properties list |
      | `IDataTable` | Represents data table properties. |
      | `IDoubleChartValue` | Represent double value which can be stored in PPTX presentation document in two ways: 1) in cell/cells of workbook related to chart; 2) as literal value. |
      | `IErrorBarsCustomValues` | Specifies the errors bar values. It shall be used only when the Error bars value type is Custom. |
      | `IErrorBarsFormat` | Represents error bars of chart series. ErrorBars custom values are in IChartDataPointCollection (in property). |
      | `IFormat` | Represents chart format properties. |
      | `IFormattedTextContainer` | Represents chart text format. |
      | `ILayoutable` | Specifies the exact position of a chart element. |
      | `ILegend` | Represents chart's legend properties. |
      | `ILegendEntryCollection` | Represents legends collection. |
      | `ILegendEntryProperties` | Represents legend properties of a chart. |
      | `IMarker` | Represents marker of a chert. |
      | `IMultipleCellChartValue` | Represents a collection of a chart cells. |
      | `IOverridableText` | Represents overridable text for a chart. |
      | `IPieSplitCustomPointCollection` | Represents a collection of points that shall be drawn in the second pie or bar on a bar-of-pie or pie-of-pie chart with a custom split. |
      | `IRotation3D` | Represents 3D rotation of a chart. |
      | `ISingleCellChartValue` | Represents a chart data cell. |
      | `IStringChartValue` | Represent string value which can be stored in PPTX presentation document in two ways: 1) in cell/cells of workbook related to chart; 2) as literal value. |
      | `IStringOrDoubleChartValue` | Represent string or double value which can be stored in PPTX presentation document in two ways: 1) in cell/cells of workbook related to chart; 2) as literal value. |
      | `ITrendline` | Class represents trend line of chart series |
      | `ITrendlineCollection` | Represents a collection of TrendlineEx |
      | `IUpDownBarsManager` | Provide access to up/down bars of Line- or Stock-chart. |
      | `Legend` | Represents chart's legend properties. |
      | `LegendEntryCollection` | Collection of legend entries. |
      | `LegendEntryProperties` | Represents legend properties of a chart entry. |
      | `Marker` | Represents a chart marker (symbol at data points). |
      | `Rotation3D` | Represents 3D rotation of a chart. |
      | `StringChartValue` | Represents a string value backed by workbook cells or literal. |
      | `StringOrDoubleChartValue` | Represents a value that can be string or double, backed by a cell or literal. |
      | `Trendline` | Represents a trend line of a chart series. |
      | `TrendlineCollection` | Represents a collection of Trendline objects for a chart series. |
      | `Color` | Represents an ARGB color, equivalent to System.Drawing.Color. |
      | `PointF` | Represents a 2D point with float coordinates, equivalent to System.Drawing.PointF. |
      | `Size` | Represents a 2D size with integer dimensions, equivalent to System.Drawing.Size. |
      | `SizeF` | Represents a 2D size with float dimensions, equivalent to System.Drawing.SizeF. |
      | `Blur` | Represents a Blur effect that is applied to the entire shape, including its fill. All color channels, including alpha, are affected. |
      | `FillOverlay` | Represents a Fill Overlay effect. A fill overlay may be used to specify an additional fill for an object and blend the two fills together. |
      | `Glow` | Represents a Glow effect, in which a color blurred outline is added outside the edges of the object. |
      | `IBlur` | Represents a Blur effect that is applied to the entire shape, including its fill. All color channels, including alpha, are affected. |
      | `IFillOverlay` | Represents a Fill Overlay effect. A fill overlay may be used to specify an additional fill for an object and blend the two fills together. |
      | `IGlow` | Represents a Glow effect, in which a color blurred outline is added outside the edges of the object. |
      | `IImageTransformOperation` | The IImageTransformOperation interface defines an image transformation operation such as cropping or color adjustment. |
      | `IInnerShadow` | Represents a inner shadow effect. |
      | `IOuterShadow` | Represents an Outer Shadow effect. |
      | `IPresetShadow` | Represents a Preset Shadow effect. |
      | `IReflection` | Represents a reflection effect. |
      | `ISoftEdge` | Represents a Soft Edge effect. The edges of the shape are blurred, while the fill is not affected. |
      | `ImageTransformOperation` | The ImageTransformOperation class implements an image transformation operation applied to picture frames. |
      | `InnerShadow` | Represents a Inner Shadow effect. |
      | `OuterShadow` | Represents an Outer Shadow effect. |
      | `PresetShadow` | Represents a Preset Shadow effect. |
      | `Reflection` | Represents a Reflection effect. |
      | `SoftEdge` | Represents a soft edge effect. The edges of the shape are blurred, while the fill is not affected. |
      | `ISaveOptions` | Options that control how a presentation is saved. |
      | `MarkdownSaveOptions` | Represents options that control how presentation should be saved to markdown. |
      | `SaveOptions` | Abstract class with options that control how a presentation is saved. |
      | `CornerDirectionTransition` | Corner direction slide transition effect. |
      | `EightDirectionTransition` | Eight direction slide transition effect. |
      | `EmptyTransition` | Empty slide transition effect. |
      | `FlyThroughTransition` | Fly-through slide transition effect. |
      | `GlitterTransition` | Glitter slide transition effect. |
      | `ICornerDirectionTransition` | Corner direction slide transition effect. |
      | `IEightDirectionTransition` | Eight direction slide transition effect. |
      | `IEmptyTransition` | Empty slide transition effect. |
      | `IFlyThroughTransition` | Fly-through slide transition effect. |
      | `IGlitterTransition` | Glitter slide transition effect. |
      | `IInOutTransition` | In-Out slide transition effect. |
      | `ILeftRightDirectionTransition` | Left-right direction slide transition effect. |
      | `IMorphTransition` | Ripple slide transition effect. |
      | `IOptionalBlackTransition` | Optional black slide transition effect. |
      | `IOrientationTransition` | Orientation slide transition effect. |
      | `IRevealTransition` | Reveal slide transition effect. |
      | `IRippleTransition` | Ripple slide transition effect. |
      | `IShredTransition` | Shred slide transition effect. |
      | `ISideDirectionTransition` | Side direction slide transition effect. |
      | `ISplitTransition` | Split slide transition effect. |
      | `ITransitionValueBase` | Represents base class for slide transition effects. |
      | `IWheelTransition` | Wheel slide transition effect. |
      | `InOutTransition` | In-Out slide transition effect. |
      | `LeftRightDirectionTransition` | Left-right direction slide transition effect. |
      | `MorphTransition` | Morph slide transition effect. |
      | `OptionalBlackTransition` | Optional black slide transition effect. |
      | `OrientationTransition` | Orientation slide transition effect. |
      | `RevealTransition` | Reveal slide transition effect. |
      | `RippleTransition` | Ripple slide transition effect. |
      | `ShredTransition` | Shred slide transition effect. |
      | `SideDirectionTransition` | Side direction slide transition effect. |
      | `SlideShowTransition` | Represents slide show transition. |
      | `SplitTransition` | Split slide transition effect. |
      | `TransitionValueBase` | Base class for slide transition effects. |
      | `WheelTransition` | Wheel slide transition effect. |
      | `BaseOverrideThemeManager` | Base class for classes that provide access to different types of overriden themes. |
      | `BaseThemeManager` | Base class for classes that provide access to different types of themes. |
      | `ColorScheme` | Stores theme-defined colors. |
      | `EffectStyle` | Represents an effect style. |
      | `EffectStyleCollection` | Represents a collection of effect styles. |
      | `ExtraColorScheme` | Represents an additional color scheme which can be assigned to a slide. |
      | `ExtraColorSchemeCollection` | Represents a collection of additional color schemes. |
      | `FillFormatCollection` | Represents the collection of fill styles. |
      | `FontScheme` | Stores theme-defined fonts. |
      | `FormatScheme` | Stores theme-defined formats for the shapes. |
      | `IColorScheme` | Stores theme-defined colors. |
      | `IEffectStyle` | Represents an effect style. |
      | `IEffectStyleCollection` | Represents a collection of effect styles. |
      | `IExtraColorScheme` | Represents an additional color scheme which can be assigned to a slide. |
      | `IExtraColorSchemeCollection` | Represents a collection of additional color schemes. |
      | `IFillFormatCollection` | Represents the collection of fill styles. |
      | `IFontScheme` | Stores theme-defined fonts. |
      | `IFormatScheme` | Stores theme-defined formats for the shapes. |
      | `ILineFormatCollection` | Represents the collection of line styles. |
      | `IMasterTheme` | Represents a master theme. |
      | `IMasterThemeManager` | Provides access to presentation master theme. |
      | `IMasterThemeable` | Represent master theme manager. |
      | `IOverrideTheme` | Represents a overriding theme. |
      | `IOverrideThemeManager` | Provides access to different types of overriden themes. |
      | `IOverrideThemeable` | Represents override theme manager. |
      | `ITheme` | Represents a theme. |
      | `IThemeManager` | Represent theme properties. |
      | `IThemeable` | Represents objects that can be themed with . |
      | `LayoutSlideThemeManager` | Provides access to layout slide theme overriden. |
      | `LineFormatCollection` | Represents the collection of line styles. |
      | `MasterTheme` | Represents a master theme. |
      | `MasterThemeManager` | Provides access to presentation master theme. |
      | `NotesSlideThemeManager` | Provides access to notes slide theme overriden. |
      | `OverrideTheme` | Represents a overriding theme. |
      | `SlideThemeManager` | Provides access to slide theme overriden. |
      | `Theme` | Represents a theme. |

      #### Enumerations

      | Enumeration | Description |
      | `BackgroundType` | Defines the slide background fill source. |
      | `BevelPresetType` | Constants which define 3D bevel of shape. |
      | `BulletType` | Represents the type of the extended bullets. |
      | `CameraPresetType` | Constants which define camera preset type. |
      | `ColorType` | Represents different color modes. |
      | `FillBlendMode` | Determines blend mode. |
      | `FillType` | Specifies the interior fill type of various visual objects. |
      | `FontAlignment` | Represents vertical font alignment. |
      | `GradientDirection` | Represents the gradient style. |
      | `GradientShape` | Represents the shape of gradient fill. |
      | `LightRigPresetType` | Constants which define light preset types. |
      | `LightingDirection` | Constants which define light directions. |
      | `LineAlignment` | Represents the lines alignment type. |
      | `LineArrowheadLength` | Represents the length of an arrowhead. |
      | `LineArrowheadStyle` | Represents the style of an arrowhead. |
      | `LineArrowheadWidth` | Represents the width of an arrowhead. |
      | `LineCapStyle` | Represents the line cap style. |
      | `LineDashStyle` | Represents the line dash style. |
      | `LineJoinStyle` | Represents the lines join style. |
      | `LineStyle` | Represents the style of a line. |
      | `MaterialPresetType` | Constants which define material of shape. |
      | `NullableBool` | Represents triple boolean values. |
      | `NumberedBulletStyle` | Represents the style of the numbered bullets. |
      | `Orientation` | Represents the orientation of a shape. |
      | `PatternStyle` | Represents the pattern style. |
      | `PictureFillMode` | Determines how picture will fill area. |
      | `PresetColor` | Represents predefined color presets. |
      | `PresetShadowType` | Represents a preset for a shadow effect. |
      | `RectangleAlignment` | Defines 2-dimension allignment. |
      | `SchemeColor` | Represents colors in a color scheme. |
      | `ShapeType` | Represents preset geometry of geometry shapes. |
      | `SlideLayoutType` | Represents the slide layout type. |
      | `SourceFormat` | Represents source file format. |
      | `TableStylePreset` | Represents builtin table styles. |
      | `TextAlignment` | Represents different text alignment styles. |
      | `TextAnchorType` | text box alignment within a text area. |
      | `TextAutofitType` | Represents text autofit mode. |
      | `TextCapType` | Represents the type of text capitalisation. |
      | `TextShapeType` | Represents text wrapping shape. |
      | `TextStrikethroughType` | Represents the type of text strikethrough. |
      | `TextUnderlineType` | Represents the type of text underline. |
      | `TextVerticalType` | Determines vertical writing mode for a text. |
      | `TileFlip` | Defines tile flipping mode. |
      | `AfterAnimationType` | Represents the after animation type of an animation effect. |
      | `AnimateTextType` | Represents the animate text type of an animation effect. |
      | `BehaviorAccumulateType` | Represents types of accumulation of effect behaviors. |
      | `BehaviorAdditiveType` | Represents additive type for effect behavior. |
      | `BuildType` | Determines how text will appear on a shape during animation. |
      | `ColorDirection` | Represents color direction for color effect behavior. |
      | `ColorSpace` | Represents color space for color effect behavior. |
      | `CommandEffectType` | Represents command effect type for command effect behavior. |
      | `EffectChartMajorGroupingType` | Represents the type of an animation effect for chart's element. |
      | `EffectChartMinorGroupingType` | Represents the type of an animation effect for chart's element in series or category. |
      | `EffectFillType` | Represent fill types. |
      | `EffectPresetClassType` | Represent effect class types. |
      | `EffectRestartType` | Represent restart types for timing. |
      | `EffectSubtype` | Represents subtypes of animation effect. |
      | `EffectTriggerType` | Represent trigger type of effect. |
      | `EffectType` | Represents the type of an animation effect. |
      | `FilterEffectRevealType` | Represents filter reveal type. |
      | `FilterEffectSubtype` | Represents filter effect subtypes. |
      | `FilterEffectType` | Represents filter effect types. |
      | `MotionCommandPathType` | Represent types of command for animation motion effect behavior. |
      | `MotionOriginType` | Specifies what the origin of the motion path is relative to. Such as the layout of the slide, or the parent. |
      | `MotionPathEditMode` | Specifies how the motion path moves when the target shape is moved |
      | `MotionPathPointsType` | Represent types of points in animation motion path. |
      | `PropertyCalcModeType` | Represent calc mode for animation property. |
      | `PropertyValueType` | Represent property value types. |
      | `AxisPositionType` | Determines a position of axis. |
      | `BubbleSizeRepresentationType` | Specifies the possible ways to represent data as bubble chart sizes. |
      | `CategoryAxisType` | Represents a type of a category axis. |
      | `ChartDataSourceType` | Represents a type of data source of the chart. |
      | `ChartType` | Represents a type of chart. |
      | `CombinableSeriesTypesGroup` | Enumeration of groups of combinable series types. Each element relates to group of types of chart series that can persist simultaneously in one ChartSeriesGroup. For example: ChartType.PercentsStackedArea series cannot be simultaneously with ChartType.StackedArea series in one ChartSeriesGroup. But two or more ChartType.PercentsStackedArea can be in one ChartSeriesGroup simultaneously (CombinableSeriesTypesGroup.AreaChart_PercentsStackedArea). And ChartType.Line series can be with ChartType.LineWithMarkers series simultaneously in one CombinableSeriesTypesGroup.LineChart_Line ChartSeriesGroup. |
      | `CrossesType` | Determines where axis will cross. |
      | `DataSourceType` | Data source types. |
      | `DisplayBlanksAsType` | Determines how missing data will be displayed. |
      | `DisplayUnitType` | Determines multiplicity of the displayed data. |
      | `ErrorBarType` | Represents type of error bar |
      | `ErrorBarValueType` | Represents type of error bar value |
      | `LayoutTargetType` | If layout of the plot area defined manually this property specifies whether |
      | `LegendDataLabelPosition` | Determines position of data labels. |
      | `LegendPositionType` | Determines a position of legend on a chart. |
      | `MarkerStyleType` | Determines form of marker on chart's data point. |
      | `PieSplitType` | Represents a type of splitting points in the second pie or bar on a pie-of-pie or bar-of-pie chart. |
      | `StyleType` | Represents chart style. |
      | `TickLabelPositionType` | Represents the position type of tick-mark labels on the specified axis. |
      | `TickMarkType` | Represents the tick mark type for the specified axis. |
      | `TimeUnitType` | Represents the base unit for the category axis |
      | `TrendlineType` | Represents type of trend line |
      | `Flavor` | All markdown specifications used in program. |
      | `HandleRepeatedSpaces` | Specifies how repeated regular space characters should be handled during Markdown export. |
      | `MarkdownExportType` | Type of rendering document. |
      | `NewLineType` | Type of new line that will be used in generated document. |
      | `SaveFormat` | Constants which define the format of a saved presentation. |
      | `TransitionCornerAndCenterDirectionType` | Specifies a direction restricted to the corners and center. |
      | `TransitionCornerDirectionType` | Represent corner direction transition types. |
      | `TransitionEightDirectionType` | Represent eight direction transition types. |
      | `TransitionInOutDirectionType` | Represent in or out direction transition types. |
      | `TransitionLeftRightDirectionType` | Specifies a direction restricted to the values of left and right. |
      | `TransitionMorphType` | Represent a type of morph transition. |
      | `TransitionPattern` | Specifies a geometric pattern that tiles together to fill a larger area. |
      | `TransitionShredPattern` | Specifies a geometric shape that tiles together to fill a larger area. |
      | `TransitionSideDirectionType` | Represent side direction transition types. |
      | `TransitionSoundMode` | Represent sound mode of transition. |
      | `TransitionSpeed` | Represent transition speed types. |
      | `TransitionType` | Represent slide show transition type. |

      #### Detailed Member Reference

      ### slides_foss

      The `slides_foss` module exposes core presentation processing types including `Presentation`, `Slide`, `Shape`, `TextFrame`, `Paragraph`, `Portion`, `Chart`, `SaveFormat`, `Color`, `TransitionType`, and `CommentAuthor`.

      ### Presentation

      The `Presentation` class represents a complete presentation document and provides access to slides, shapes, masters, layout slides, images, document properties, and notes size, while offering methods to save the presentation in various formats.

      - `as_i_presentation_component`: Defined as `def as_i_presentation_component(self) -> IPresentationComponent`.
      - `comment_authors`: Returns the collection of comment authors. Read-only.
      - `current_date_time`: Returns or sets date and time which will substitute content of datetime fields. Time of this Presentation object creation by default. Read/write .
      - `dispose`: Release all resources used by this Presentation object.
      - `document_properties`: Returns DocumentProperties object which contains standard and custom document properties. Read-only .
      - `first_slide_number`: Represents the first slide number in the presentation
      - `images`: Returns the collection of all images in the presentation. Read-only .
      - `layout_slides`: Returns a list of all layout slides that are defined in the presentation. Read-only .
      - `master_theme`: Returns master theme of the presentation. Read-only .
      - `masters`: Returns a list of all master slides that are defined in the presentation. Read-only .
      - `notes_size`: Returns notes slide size object. Read-only.
      - `presentation`: Defined as `def presentation(self) -> IPresentation`.
      - `save`: Save the presentation to a file or stream.
      - `slides`: Returns a list of all slides that are defined in the presentation. Read-only .
      - `source_format`: Returns information about from which format presentation was loaded. Read-only .

      ### Slide

      The `Slide` class represents an individual slide within a presentation and provides access to its comments, hidden status, layout slide, notes slide manager, slide number, and theme manager.

      - `get_slide_comments`: Returns all comments on this slide, optionally filtered by author.
      - `hidden`: Determines whether the specified slide is hidden during a slide show. Read/write .
      - `layout_slide`: Returns or sets the layout slide for the current slide. Read/write .
      - `notes_slide_manager`: Allow to access notes slide, add and remove it. Read-only.
      - `remove`: Defined as `def remove(self) -> None`.
      - `slide_number`: Returns a number of slide. Index of slide in collection is always equal to SlideNumber - Presentation.FirstSlideNumber. Read/write .
      - `theme_manager`: Returns the overriding theme manager. Read-only .

      ### Shape

      The `Shape` class represents a drawing object on a slide such as a rectangle, circle, connector, chart, picture frame, table, or text frame, and exposes properties for formatting, positioning, rotation, hyperlinking, and alternative text.

      - `alternative_text`: Returns or sets the alternative text associated with a shape. Read/write .
      - `alternative_text_title`: Returns or sets the title of alternative text associated with a shape. Read/write .
      - `as_i_presentation_component`: Defined as `def as_i_presentation_component(self) -> IPresentationComponent`.
      - `as_i_slide_component`: Defined as `def as_i_slide_component(self) -> ISlideComponent`.
      - `connection_site_count`: Returns the number of connection sites on the shape. Read-only .
      - `effect_format`: Returns the EffectFormat object which contains pixel effects applied to a shape. Note: can return null for certain types of shapes which don't have effect properties. Read-only .
      - `fill_format`: Returns the FillFormat object that contains fill formatting properties for a shape. Note: can return null for certain types of shapes which don't have fill properties. Read-only .
      - `frame`: Returns or sets the shape frame's properties. Read/write .
      - `height`: Gets or sets the height of the shape, measured in points. Read/write .
      - `hidden`: Determines whether the shape is hidden. Read/write .
      - `hyperlink_click`: Returns or sets the hyperlink followed when the shape is clicked. Read/write .
      - `hyperlink_mouse_over`: Returns or sets the hyperlink followed when the pointer rests on the shape. Read/write .
      - `is_decorative`: Gets or sets 'Mark as decorative' option Reed/write .
      - `is_grouped`: Determines whether the shape is grouped. Read-only .
      - `line_format`: Returns the LineFormat object that contains line formatting properties for a shape. Note: can return null for certain types of shapes which don't have line properties. Read-only .
      - `name`: Returns or sets the name of a shape. Must be not null. Use empty string value if needed. Read/write .
      - `office_interop_shape_id`: Returns a slide-scoped unique identifier that remains constant for the lifetime of the shape and lets PowerPoint or interop code reliably reference the shape from anywhere in the document. Read-only . See also .
      - `presentation`: Returns the parent presentation of a slide. Read-only .
      - `raw_frame`: Returns or sets the raw shape frame's properties. Read/write .
      - `rotation`: Returns or sets the number of degrees the specified shape is rotated around the z-axis. A positive value indicates clockwise rotation; a negative value indicates counterclockwise rotation. Read/write .
      - `slide`: Returns the parent slide of a shape. Read-only .
      - `three_d_format`: Returns the ThreeDFormat object that 3d effect properties for a shape. Note: can return null for certain types of shapes which don't have 3d properties. Read-only .
      - `unique_id`: Returns an internal, presentation-scoped identifier intended for use by add-ins or other code. Because this value can be reassigned by the user or programmatically, it must not be treated as a persistent unique key. Read-only . See also .
      - `width`: Gets or sets the width of the shape, measured in points. Read/write .
      - `x`: Gets or sets the x-coordinate of the shape's upper-left corner, measured in points. Read/write .
      - `y`: Gets or sets the y-coordinate of the shape's upper-left corner, measured in points. Read/write .
      - `z_order_position`: Returns the position of a shape in the z-order. Shapes[0] returns the shape at the back of the z-order, and Shapes[Shapes.Count - 1] returns the shape at the front of the z-order. Read-only .

      ### TextFrame

      The `TextFrame` class represents a container for text content on a shape and provides access to its paragraphs, as well as properties for frame formatting and positioning.

      - `as_i_presentation_component`: Defined as `def as_i_presentation_component(self) -> IPresentationComponent`.
      - `as_i_slide_component`: Defined as `def as_i_slide_component(self) -> ISlideComponent`.
      - `paragraphs`: Returns the list of all paragraphs in a frame. Read-only .
      - `parent_cell`: Returns the parent cell or null if the parent object does not implement the ICell interface. Read-only .
      - `parent_shape`: Returns the parent shape or null if the parent object does not implement the IShape interface Read-only .
      - `presentation`: Returns the parent presentation of a TextFrame. Read-only .
      - `slide`: Returns the parent slide of a TextFrame. Read-only .
      - `text`: Gets or sets the plain text for a TextFrame. Read/write .
      - `text_frame_format`: Returns the formatting object for this TextFrame object. Read-only .

      ### Paragraph

      The `Paragraph` class represents a paragraph within a text frame and provides access to its portions, formatting, and alignment.

      - `as_i_presentation_component`: Defined as `def as_i_presentation_component(self) -> IPresentationComponent`.
      - `as_i_slide_component`: Defined as `def as_i_slide_component(self) -> ISlideComponent`.
      - `paragraph_format`: Returns the formatting object for this paragraph. Read-only .
      - `portions`: Returns the collection of a text portions. Read-only .
      - `presentation`: Defined as `def presentation(self) -> IPresentation`.
      - `slide`: Defined as `def slide(self) -> IBaseSlide`.
      - `text`: Gets or sets the the plain text of a paragraph. Read/write .

      ### Portion

      The `Portion` class represents a run of text with consistent formatting within a paragraph and exposes properties for font formatting such as bold and height.

      - `as_i_presentation_component`: Defined as `def as_i_presentation_component(self) -> IPresentationComponent`.
      - `as_i_slide_component`: Defined as `def as_i_slide_component(self) -> ISlideComponent`.
      - `portion_format`: Returns oformatting bject which contains explicitly set formatting properties of the text portion with no inheritance applied. Read-only .
      - `presentation`: Defined as `def presentation(self) -> IPresentation`.
      - `slide`: Defined as `def slide(self) -> IBaseSlide`.
      - `text`: Gets or sets the plain text of a portion. Read/write .

      ### Chart

      The `Chart` class represents a chart shape on a slide and provides access to its data, series, categories, title, data points, and chart data workbook.

      - `axes`: Defined as `def axes(self) -> 'IAxesManager'`.
      - `back_wall`: Defined as `def back_wall(self) -> 'IChartWall'`.
      - `chart`: Defined as `def chart(self)`.
      - `chart_data`: Defined as `def chart_data(self) -> IChartData`.
      - `chart_data_table`: Defined as `def chart_data_table(self) -> IDataTable`.
      - `chart_title`: Defined as `def chart_title(self) -> 'IChartTitle'`.
      - `display_blanks_as`: Defined as `def display_blanks_as(self) -> 'DisplayBlanksAsType'`.
      - `effect_format`: Defined as `def effect_format(self)`.
      - `fill_format`: Defined as `def fill_format(self)`.
      - `floor`: Defined as `def floor(self) -> 'IChartWall'`.
      - `has_data_table`: Defined as `def has_data_table(self) -> bool`.
      - `has_legend`: Defined as `def has_legend(self) -> bool`.
      - `has_rounded_corners`: Defined as `def has_rounded_corners(self) -> bool`.
      - `has_title`: Defined as `def has_title(self) -> bool`.
      - `legend`: Defined as `def legend(self) -> 'ILegend'`.
      - `line_format`: Defined as `def line_format(self)`.
      - `plot_area`: Defined as `def plot_area(self) -> 'IChartPlotArea'`.
      - `plot_visible_cells_only`: Defined as `def plot_visible_cells_only(self) -> bool`.
      - `rotation_3d`: Defined as `def rotation_3d(self) -> 'IRotation3D'`.
      - `show_data_labels_over_maximum`: Defined as `def show_data_labels_over_maximum(self) -> bool`.
      - `side_wall`: Defined as `def side_wall(self) -> 'IChartWall'`.
      - `style`: Defined as `def style(self) -> 'StyleType'`.
      - `text_format`: Defined as `def text_format(self) -> 'IChartTextFormat'`.
      - `three_d_format`: Defined as `def three_d_format(self)`.
      - `type`: Defined as `def type(self) -> ChartType`.
      - `validate_chart_layout`: Defined as `def validate_chart_layout(self) -> None`.

      ### SaveFormat

      The `SaveFormat` enumeration defines output formats for saving presentations, including PPTX and other supported file types.

      ### Color

      The `Color` class provides methods for creating and manipulating colors, including the `from_argb` factory method.

      - `a`: Defined as `def a(self) -> int`.
      - `b`: Defined as `def b(self) -> int`.
      - `from_argb`: Create a Color from ARGB components.
      - `g`: Defined as `def g(self) -> int`.
      - `r`: Defined as `def r(self) -> int`.

      ### TransitionType

      The `TransitionType` enumeration defines slide transition effects that can be applied to slides for slide shows.

      ### CommentAuthor

      The `CommentAuthor` class represents a person who can author comments in a presentation and provides properties for author name and initials.

      - `comments`: Returns the collection of comments made by this author. Read-only.
      - `initials`: Returns or sets the author's initials. Read/write str.
      - `name`: Returns or sets the author's name. Read/write str.
      - `remove`: Removes the author from the parent collection.

      </details>
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.Slides FOSS for Python, straight from its own sealed, reviewed documentation candidate:

      Aspose.Slides FOSS for Python creates and edits PowerPoint presentations in the OOXML family, writes them to disk, and supports basic slide content such as shapes, charts, images, tables, text frames, comments, and hyperlinks.

      - Only seven `SaveFormat` values produce valid output files; fourteen others raise ValueError instead of writing a mislabelled file.
      - Rendering and conversion to PDF, HTML, XPS, or images is not implemented, and presentation sections, slide size, SmartArt, OLE objects, mathematical text, VBA macros, digital signatures, encryption, and most action settings are absent.
      - The `add_image` method requires image bytes or a file-like object, not a file path, so callers must open the file and pass the handle or its bytes.
      - Assigning to a property a shape or formatting object that does not have that property raises AttributeError, so misspelt property names fail where they are written.
      - `Comment` threads written on save come from the classic comment list; resolved status, @-mentions, and reply chains that the classic list cannot express are lost if the presentation's comment authors are modified before saving.
      - Unknown XML parts encountered during load are preserved verbatim on save, so opening and re-saving a file will never strip content this library does not yet understand.

      `SaveFormat` and the file name are independent: `save("deck.pptx",
      SaveFormat.POTX)` writes a genuine template under a `.pptx` name, and
      PowerPoint refuses to open a file whose extension disagrees with the format
      declared inside it. Give the file the extension of the format you asked for.

      Assigning to a property a shape or a formatting object does not have raises
      `AttributeError` rather than being accepted and discarded, so a misspelt
      property name fails where it is written. Names beginning with an underscore
      are unaffected.

      Comment threads are written from the classic comment list on save. A deck
      authored in PowerPoint can carry resolved status, @-mentions and reply chains
      that the classic list cannot express; those are lost if the presentation's
      comment authors are touched before saving. Loading and saving without going
      near comments preserves the file's own threads untouched.

      Unknown XML parts encountered during load are preserved verbatim on save —
      opening and re-saving a file will never strip content this library does not yet understand.
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
  enable: false
  list: []
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
