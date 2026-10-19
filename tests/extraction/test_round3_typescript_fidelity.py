"""TC-533 (Round 3, rows RD3-010 to RD3-013): the three committed TypeScript fixtures serve what the upstream says.

Every expectation below comes from an oracle that is NOT the extractor: the TypeScript compiler (``typescript``
5.x ``createSourceFile`` / ``createProgram`` + ``getExportsOfModule``) run over a clone of each repository at the
chart pin on 2026-10-19, or the upstream source text itself (the lines are quoted in the comments). The
ratchet-style numbers are exact for the pin: the fixtures are committed and the clones are fixed commits.
Offline: the committed fixtures only.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
VALUES = Path(__file__).resolve().parents[2] / "infra" / "helm" / "foss-mcp" / "values.yaml"

PILOTS = ("cells_typescript", "3d_typescript", "pdf_typescript")
EXTRACTOR_VERSION = 5

# The 532 names ``ts.TypeChecker.getExportsOfModule`` returns for src/index.ts of pdf/typescript at 155bfc7a33f0.
TSC_EXPORTS = frozenset(
    """
    AddBarcodeOptions AddHtmlResult AddImageOptions AddImagePagesOptions AddImagePagesResult AddLayerOptions
    AddMarkdownResult AddSVGOptions AddSVGResult AddTOCResult AddTableOptions AddTableResult AnnotData
    Annotation AnnotationMatch AnnotationTextKey AnnotationTextMatch ApplyRedactionsOptions ArtifactBBoxSource
    ArtifactEdge ArtifactEvent Attachment AttachmentOptions AuthoringFont AutoTagOptions AutoTagReport
    Background BackgroundStyle BarcodeModel BarcodeSpec BatesOptions BookletOptions BorderEdges BorderInfo
    BorderSides BorderStyle ButtonField ButtonIconPosition CONSTRUCTS CadesAttributes CaretAnnotation
    CaretOptions CellBuilder CellHeader CellOptions CellTextOptions CertInfo CertifyOptions ChainOptions
    ChainResult Change CheckboxField CheckboxInit ChoiceField ChoiceInit ChoiceOption CmykTransform
    CollectionFieldDef CollectionFieldType CollectionSettings CollectionView ColorConvertOptions
    ColorConvertReport ColorImageResult ColorSkipped ColoredTilingPattern ColumnWidth ComboBoxInit
    CommitmentType Construct ContentAddr ContentItem ContentOp ContentVisitor ContentWalkOptions
    ConversionReport ConvertAction ConvertCategory ConvertColorsOptions ConvertOptions Crl CrlEntry Decoration
    DecorationOptions DecorationStyle DigestAlgorithm DocMdpPermission Document DocumentJavaScript
    DocumentTimestampOptions DocumentTimestampReport DocxExtras DocxOptions DrPruneResult DssEntry Duplex Edged
    EditableContent ElemOpts ElementPolicy EmbeddedFont EncodedImage EncryptOptions EpubOptions
    ExportFormDataOptions ExternalSigner ExtractOptions ExtractPagesOptions Field FieldActions
    FieldActionsUpdate FieldBorderStyle FieldInit FieldLock FieldStyle FieldType FileAttachmentAnnotation
    FileAttachmentOptions FlattenLayersReport FloatBoxImageOptions FloatBoxOptions FloatingBox Flow FlowClear
    FlowCodeOptions FlowElement FlowHeadingOptions FlowImageOptions FlowListItem FlowListNode FlowListOptions
    FlowOptions FlowParagraphOptions FlowQuoteBar FlowQuoteOptions FlowRuleOptions FlowTableOptions FontFamily
    FontMatch FontOptimization Form FormData FormDataField G4EncodeOptions GlyphEvent GoToAction Gradient
    GradientStop HeaderFooterBand HeaderFooterOptions HtmlComment HtmlDoctype HtmlDocument HtmlElement
    HtmlElements HtmlFileOptions HtmlFlowOptions HtmlFlowResult HtmlFragment HtmlNamespace HtmlNode HtmlOptions
    HtmlProcessingInstruction HtmlText HttpTimestampOptions IMAGE_FORMATS IMAGE_MODES IccTransformOptions
    ImageEvent ImageFormat ImageInfo ImageMode ImageOptimization ImageOptions ImportOptions ImportReport
    ImportedAnnot InlineImageInfo InsertPagesOptions InvalidPasswordError JavaScriptAction KeyUsageName
    LIMIT_FIELDS Layer LayerConfig LayerState LayerUsage LayoutAttributes LimitField LinearBarcode
    LinearGradient LinearizationCheck LinkAction LinkAnnotation LinkOptions ListAttributes ListBoxInit
    LoadFontOptions LoadLimitPatch LoadLimitValues LoadLimits MATHML_NS MarkRedactTextOptions MarkdownAsset
    MarkdownElements MarkdownExportOptions MarkdownExportResult MarkdownFlowOptions MarkdownFontFamily
    MarkdownFontSpec MarkdownOptions MarkdownResult MarkdownStyle MarkupAnnotation MarkupOptions MarkupType
    MatrixBarcode MdAlign MdBlock MdBlockQuote MdCode MdCodeBlock MdDocument MdEmph MdHardBreak MdHeading
    MdHtmlBlock MdHtmlInline MdImage MdInline MdItem MdLink MdList MdNode MdParagraph MdSoftBreak
    MdStrikethrough MdStrong MdTable MdTableCell MdTableRow MdText MdThematicBreak Metadata MetadataUpdate
    NUpOptions NamedDest NamedDestination NonFullScreenPageMode NotRendered ObjStmDamage OcspResponse
    OcspSingleResponse OoxmlPart OoxmlRelationship OpenAction OpenOptions OptimizeImageOptions OptimizeOptions
    OptimizeReport OptionalContent OutlineDest OutlineItem OutlineView OverlayOptions PDF17_NS PDF20_NS Padding
    Page PageArtifact PageBoundary PageDest PageFormat PageGraphics PageLabel PageLayout PageMode PagePath
    PageTransition ParseHtmlBytesOptions PathEvent PathPaint PathSegment PathSubpath PdfALevel PdfAction
    PdfParseError PdfRevision PdfUaConvertOptions PdfUaPart PdfXConvertOptions PdfXLevel PemSigner Permissions
    Pkcs12Signer PlaceContext PlaceElementsOptions PlaceElementsResult PlaceOptions PlaceResult PrintPageRange
    PrintScaling PrunePolicy PubSecEncryptOptions PubSecRecipient PubSecRecipientCert PushButtonInit QrEcc
    QrOptions Quantized RGB RadialGradient RadioField RadioGroupInit RadioOption ReadingDirection RecoveryReport
    Rect RedactAnnotation RedactAnnotationOptions RedactOptions RegionHits RemoveImageOptions
    ReplaceImageOptions ResetAction ResizeOptions ResolvedFamily ResolvedPadding ResourceLimitError
    RevocationFetcher RevocationMaterial RevocationResult RevocationStatus RowBuilder RowOptions
    STANDARD_STRUCTURE_TYPES SaveImageFormat SaveImageOptions SaveImagesOptions SaveImagesSkipped SaveOptions
    SearchOptions SeedCertRequirement SeedCertValue SeedDigestMethod SeedLockDocument SeedRequirement SeedValue
    SeedValueError Severity SigAlg SignOptions SignatureAppearance SignatureField SignatureFieldInit
    SignatureReport SignatureScheme Signer SignerLocation SignerOptions SkippedAnnot SkippedFont SkippedFrame
    SkippedImage SplitOptions StampAnnotation StampAnnotationOptions StampOptions StampPosition StampWithOptions
    StdFont StreamFilterName StructContainer StructElement StructRemoveResult StructRetagResult StructTreeRoot
    SubmitAction SvgOptions TOCEntry TOCOptions Table TableAttributes TableBuilder TableCell TableDefaults
    TableExtractOptions TableMetrics TableRow TableStitchOptions TargetSpace Template TextAnnotation TextBlock
    TextBlockOptions TextField TextFieldInit TextFont TextFragment TextLine TextMatch TextNoteOptions TextRun
    TiffEncodeOptions TiffExportOptions TiffFrame TilingPattern TilingPatternOptions TimeStampRequestInfo
    TimeStampRequestOptions TimeStampTokenOptions TimestampInfo TimestampProvider TrailerChoice
    TransitionDimension TransitionDirection TransitionMotion TransitionStyle TstInfo UncoloredTilingPattern
    UnsupportedFeatureError UriAction UsageContext UsageEvent ValidationDataOptions ValidationIssue
    ValidationReport VectorGraphics VerifyOptions ViewerPreferences ViewerPreferencesUpdate WatermarkOptions
    WidgetStyle XfaConvertOptions XfaConvertReport XfaFieldResult XfaSkipped XmpMetadata XmpUpdate ZipEntry
    ascii85Encode asciiHexEncode buildTimeStampRequest buildTimeStampToken checkRevocation clearMetadataFile
    codeBlock createTable crlStatus defaultPrunePolicy describeNotRendered encodeBmp encodeFilter encodeG4
    encodeGif encodeStream encodeTiff exportFdfFile exportXfdfFile extractArtifacts extractPaths extractTables
    extractTaggedTables extractTimeStampToken faceFor filterDisallowedHtml findIssuer heading htmlElements
    htmlFileToPdf httpTimestampProvider iccCmykTransform image imageExtension imageKey importFdfFile
    importXfdfFile isMdBlock isMdContainer isMdInline list lzwEncode makeCode128 makeEan13 makeEan8 makeQr
    makeUpcA mapRegions markdownElements ocspStatus paragraph parseContentStream parseCrl parseHtml
    parseHtmlBytes parseMarkdown parseOcspResponse parseTimeStampRequest parseTstInfo placeElements quantize
    quote readDssCerts readDssMaterial readMetadataFile redactText replaceText resolveFamily
    resolveMarkdownStyle richTextToPlain rule runLengthEncode saveDocxFile saveImagesFile saveMarkdownFile
    savePageImageFile searchAnnotationText searchAnnotations searchText serializeContentStream splitPdfFile
    stitchTables table tiffPageCount updateMetadataFile verifyCertChain verifyCrlSignature verifyLinearization
    verifyOcspSignature verifyTimestampToken visitContent vriKey
    """.split()
)
# Two of them are tagged ``@internal`` upstream (src/flowelement.ts:23 and :41), which the extractor never serves.
INTERNAL_EXPORTS = frozenset({"PlaceContext", "PlaceResult"})

# The public, non-`_` properties and accessors of ``class Document`` (src/document.ts) that the compiler lists,
# less ``decodeBudget``, which the source tags ``@internal`` (src/document.ts:476).
DOCUMENT_PROPERTIES = (
    "DisplayDocTitle DocumentTimestamps Form IsLinearized IsTagged Lang OptionalContent PageLayout PageMode "
    "Pages Permissions Revisions Signatures hasIncrementalUpdates loadLimits recovery"
).split()

# Counted over the OWN members (no ``inherited_from``) of the fixture, and by the compiler over the same files.
# cells (28 classes, 53 interfaces, 25 aliases; 25 files)
CELLS_ORACLE = {
    "interface_readonly": 0,
    "interface_optional": 125,
    "class_properties": 41,
    "class_static_properties": 1,
    "static_methods": 22,
    "async_methods": 7,
    "defaults": 8,
    "optional_params": 25,
    "rest_params": 0,
}
# 3d (138 files); ``static_methods`` excludes the 23 static accessors, which are properties.
D3_ORACLE = {
    "interface_readonly": 1,
    "interface_optional": 27,
    "class_properties": 104,
    "class_static_properties": 104,
    "static_methods": 68,
    "async_methods": 0,
    "defaults": 72,
    "optional_params": 69,
    "rest_params": 20,
}
D3_STATIC_ACCESSORS = 23  # grep -c 'static get ' over src/: 23 (a static setter pairs with a getter)
D3_READONLY_CLASS_PROPERTIES = 42


@cache
def fixture(pilot: str) -> dict[str, Any]:
    return json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


def entry(pilot: str, class_import: str) -> dict[str, Any]:
    found = [t for t in fixture(pilot)["types"] if t.get("class_import") == class_import]
    assert len(found) == 1, (class_import, len(found))
    return found[0]


def by_name(item: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {member["name"]: member for member in item[key]}


def own(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if not item.get("inherited_from")]


def measured(pilot: str) -> dict[str, int]:
    """The same quantities the compiler oracle counts, over the fixture's own members."""
    counts = dict.fromkeys(CELLS_ORACLE, 0)
    for item in fixture(pilot)["types"]:
        if item["kind"] in ("function", "constant"):
            continue
        interface = item["kind"] in ("interface_declaration", "type_alias_declaration")
        for prop in own(item["properties"]):
            if "access_mode" in prop:
                continue
            modifiers = prop.get("modifiers", [])
            if interface:
                counts["interface_readonly"] += prop["writable"] is False
                counts["interface_optional"] += "optional" in modifiers
            else:
                counts["class_properties"] += 1
                counts["class_static_properties"] += "static" in modifiers
        for method in own(item["methods"]):
            counts["static_methods"] += "static" in method.get("modifiers", [])
            counts["async_methods"] += "async" in method.get("modifiers", [])
            for param in method["params"]:
                counts["defaults"] += "default" in param
                counts["optional_params"] += bool(param.get("optional"))
                counts["rest_params"] += param["name"].startswith("...")
    return counts


def pin(pilot: str) -> str:
    family, platform = pilot.split("_")
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    (found,) = [
        p["library"]["commit"]
        for p in data["ingestion"]["pilots"]
        if p["family"] == family and p["platform"] == platform
    ]
    return str(found)


# -- every regenerated fixture ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_fixture_is_current_complete_and_at_the_chart_pin(pilot: str) -> None:
    data = fixture(pilot)
    assert data["extractor_version"] == EXTRACTOR_VERSION
    assert data["source_commit"] == pin(pilot)
    assert data["truncated"] is False
    assert data["reduced_type_count"] == data["type_count"] == len(data["types"])
    functions = sum(1 for t in data["types"] if t["kind"] == "function")
    assert data["symbol_counts"] == {"types": len(data["types"]) - functions, "functions": functions}
    assert set(data["counts"]) == {"types", "functions", "methods"}
    assert data["parse_failures"]["engine"] == "tree-sitter"
    assert "\r" not in (FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_property_or_return_or_parameter_type_starts_with_a_colon(pilot: str) -> None:
    stray = []
    for item in fixture(pilot)["types"]:
        for prop in item["properties"]:
            if str(prop.get("type", "")).lstrip().startswith(":"):
                stray.append(f"{item['name']}.{prop['name']}")
        for method in [*item["methods"], *([item] if item["kind"] == "function" else [])]:
            if str(method.get("return_type", "")).lstrip().startswith(":"):
                stray.append(f"{item['name']}.{method['name']} ->")
            stray.extend(
                f"{item['name']}.{method['name']}({p['name']})"
                for p in method["params"]
                if str(p.get("type", "")).lstrip().startswith(":")
            )
    assert stray == []


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_type_alias_keeps_its_right_hand_side_and_every_base_is_balanced(pilot: str) -> None:
    aliases = [t for t in fixture(pilot)["types"] if t["kind"] == "type_alias_declaration"]
    assert [t["name"] for t in aliases if not t.get("alias_of")] == []
    for item in fixture(pilot)["types"]:
        for base in item["bases"]:
            assert base == base.strip() and base, (item["name"], base)
            assert base.count("<") == base.count(">"), (item["name"], base)
            assert base.count("(") == base.count(")"), (item["name"], base)


# classes and the number of their DISTINCT own property and accessor names, per the compiler: for every class of the
# fixture the compiler's list of public, non-`_` property declarations and accessors was identical to the fixture's.
CLASS_PROPERTY_COUNT = {"cells_typescript": (28, 121), "3d_typescript": (134, 499)}


@pytest.mark.parametrize("pilot", sorted(CLASS_PROPERTY_COUNT))
def test_no_member_is_a_phantom_of_an_inline_object_type(pilot: str) -> None:
    """A class's properties are the ones its body declares (the compiler's count over the same files)."""
    classes = [
        t for t in fixture(pilot)["types"] if t["kind"] in ("class_declaration", "abstract_class_declaration")
    ]
    wanted_classes, wanted_names = CLASS_PROPERTY_COUNT[pilot]
    assert len(classes) == wanted_classes
    assert sum(len({p["name"] for p in own(t["properties"])}) for t in classes) == wanted_names


# -- cells/typescript (RD3-010) -----------------------------------------------------------------------------------------


def test_cells_matches_the_compiler_counts() -> None:
    assert measured("cells_typescript") == CELLS_ORACLE


def test_cells_interface_property_font_is_writable_and_optional() -> None:
    # aspose_cells/types.ts:16 -- `font?: Font;` in `export interface Style { ... }` (no `readonly` anywhere in the file)
    style = entry("cells_typescript", "aspose_cells.types.Style")
    assert style["kind"] == "interface_declaration"
    font = by_name(style, "properties")["font"]
    assert font["writable"] is True
    assert font["modifiers"] == ["optional"]
    assert font["type"] == "Font"


def test_cells_has_no_read_only_interface_property_and_the_old_count_is_gone() -> None:
    props = [
        prop
        for item in fixture("cells_typescript")["types"]
        if item["kind"] == "interface_declaration"
        for prop in item["properties"]
    ]
    assert len(props) > 700  # the old fixture's 724 read-only interface properties, now writable or inherited
    assert [prop for prop in props if prop.get("writable") is False] == []


def test_cells_type_alias_arrow_shape_type_keeps_its_union() -> None:
    # aspose_cells/types.ts:289 -- `export type ArrowShapeType = "arrow" | "rightArrow" | ... | "pentagonArrow";`
    alias = entry("cells_typescript", "aspose_cells.types.ArrowShapeType")
    assert alias["alias_of"].startswith('"arrow" | "rightArrow"')
    assert alias["alias_of"].endswith('"pentagonArrow"')
    assert (
        len([t for t in fixture("cells_typescript")["types"] if t["kind"] == "type_alias_declaration"]) == 25
    )


def test_cells_workbook_lists_only_what_its_body_declares() -> None:
    # aspose_cells/workbook.ts: six getters (lines 69-93); the other 15 "properties" the old fixture showed belonged
    # to the inline option types of `save`, `writeToZip` and other methods (lines 392, 439, 905, 925, 1087, ...).
    workbook = entry("cells_typescript", "aspose_cells.workbook.Workbook")
    assert [p["name"] for p in workbook["properties"]] == [
        "worksheets",
        "sharedStrings",
        "isProtected",
        "charts",
        "images",
        "activeTab",
    ]
    # get only, except `activeTab`, which has a setter (line 93)
    modes = {p["name"]: p["access_mode"] for p in workbook["properties"]}
    assert modes.pop("activeTab") == "readwrite"
    assert set(modes.values()) == {"readonly"}


def test_cells_static_and_async_members_are_marked() -> None:
    workbook = entry("cells_typescript", "aspose_cells.workbook.Workbook")
    load = by_name(workbook, "methods")["load"]  # `static async load(filePath: string, password?: string)`
    assert load["modifiers"] == ["static", "async"]
    assert [p["name"] for p in load["params"] if p.get("optional")] == ["password"]
    assert load["return_type"] == "Promise<Workbook>"


def test_cells_iterator_return_keeps_its_text() -> None:
    # aspose_cells/worksheetCollection.ts:14 -- `[Symbol.iterator](): Iterator<Worksheet> {`
    collection = entry("cells_typescript", "aspose_cells.worksheetCollection.WorksheetCollection")
    assert by_name(collection, "methods")["[Symbol.iterator]"]["return_type"] == "Iterator<Worksheet>"


def test_cells_password_save_option_is_advertised_by_the_signature_and_read_nowhere_upstream() -> None:
    """The limitation TC-559 serves: ``save(filePath, options?: { password?: string; saveFormat?: SaveFormat })``.

    Upstream source at the pin (aspose_cells/workbook.ts): the signature is lines 390-393; line 394 reads only
    ``options?.saveFormat``; ``writeToZip(zip, options?: { password?: string; ... })`` (line 437-440) never reads
    ``options.password``; ``_password`` (line 43) is assigned in ``load`` (54) and ``protect`` (387) and read nowhere.
    """
    save = by_name(entry("cells_typescript", "aspose_cells.workbook.Workbook"), "methods")["save"]
    options = save["params"][1]
    assert options["name"] == "options"
    assert options["optional"] is True
    assert "password?: string" in options["type"]


# -- 3d/typescript (RD3-011) --------------------------------------------------------------------------------------------


def test_3d_matches_the_compiler_counts() -> None:
    assert measured("3d_typescript") == D3_ORACLE


def test_3d_readonly_class_properties_and_static_accessors() -> None:
    classes = [
        t
        for t in fixture("3d_typescript")["types"]
        if t["kind"] in ("class_declaration", "abstract_class_declaration")
    ]
    plain = [p for t in classes for p in own(t["properties"]) if "access_mode" not in p]
    assert sum(1 for p in plain if p["writable"] is False) == D3_READONLY_CLASS_PROPERTIES
    static_accessors = [
        p
        for t in classes
        for p in own(t["properties"])
        if "access_mode" in p and "static" in p.get("modifiers", [])
    ]
    assert len(static_accessors) == D3_STATIC_ACCESSORS


def test_3d_scene_from_file_is_static() -> None:
    # src/aspose/threed/Scene.ts:386 -- `static fromFile(fileName: string): Scene {`
    method = by_name(entry("3d_typescript", "aspose.threed.Scene.Scene"), "methods")["fromFile"]
    assert method["modifiers"] == ["static"]
    assert [(p["name"], p["type"]) for p in method["params"]] == [("fileName", "string")]
    assert method["return_type"] == "Scene"


def test_3d_mesh_create_polygon_keeps_its_rest_parameter() -> None:
    # src/aspose/threed/entities/Mesh.ts:35 -- `createPolygon(...args: any[]): void {`
    method = by_name(entry("3d_typescript", "aspose.threed.entities.Mesh.Mesh"), "methods")["createPolygon"]
    assert [(p["name"], p["type"]) for p in method["params"]] == [("...args", "any[]")]


def test_3d_static_getters_are_properties_not_methods() -> None:
    # `BoundingBox.infinite` and `.null` are `static get` accessors: reading them, not calling them.
    box = entry("3d_typescript", "aspose.threed.utilities.BoundingBox.BoundingBox")
    assert {"infinite", "null"} <= set(by_name(box, "properties"))
    assert {"infinite", "null"}.isdisjoint(by_name(box, "methods"))
    infinite = by_name(box, "properties")["infinite"]
    assert infinite["modifiers"] == ["static"]
    assert infinite["access_mode"] == "readonly"


# -- pdf/typescript (RD3-012, RD3-013) ----------------------------------------------------------------------------------


def test_pdf_serves_exactly_the_export_surface_of_the_package_entry() -> None:
    served = {t["name"] for t in fixture("pdf_typescript")["types"]}
    assert served == TSC_EXPORTS - INTERNAL_EXPORTS
    assert len(TSC_EXPORTS) == 532  # the audit's "532 public exports"


def test_pdf_serves_no_internal_function_or_type() -> None:
    data = fixture("pdf_typescript")
    assert [t["name"] for t in data["types"] if t.get("reachable") is not True] == []
    names = {t["name"] for t in data["types"]}
    assert {"Document", "Page"} <= names
    assert not {"PdfDict", "PdfObject", "AP_FONT_KEY"} & names
    assert data["symbol_counts"]["functions"] == 93  # of 3,060 functions the old fixture served
    assert sum(1 for t in data["types"] if t["kind"] == "function") == 93


def test_pdf_renamed_re_exports_are_served_under_the_exported_name() -> None:
    # src/index.ts:150 -- `export { describe as describeNotRendered, CONSTRUCTS } from './htmlreport.js';`
    # src/index.ts:9   -- `export type { SaveImagesOptions, SkippedImage as SaveImagesSkipped, HtmlFileOptions } from './node.js';`
    names = {t["name"] for t in fixture("pdf_typescript")["types"]}
    assert {"describeNotRendered", "SaveImagesSkipped", "CONSTRUCTS"} <= names
    assert "describe" not in names
    skipped = [t for t in fixture("pdf_typescript")["types"] if t["name"] == "SaveImagesSkipped"]
    assert [t["file"] for t in skipped] == ["src/node.ts"]


def test_pdf_utility_type_bases_are_single_bases() -> None:
    bases = {
        # src/stamp.ts:396, src/toc.ts:49-50 (the `extends` line wraps), src/raster.ts:1797
        "TextBlockOptions": ["Omit<StampOptions, 'align' | 'rotate'>"],
        "TOCOptions": ["Omit<TextBlockOptions, 'align' | 'valign' | 'tag' | 'artifact'>"],
        "TiffExportOptions": ["Omit<ImageOptions, 'format' | 'quality'>"],
    }
    got = {t["name"]: t["bases"] for t in fixture("pdf_typescript")["types"] if t["name"] in bases}
    assert got == bases


def test_pdf_has_no_phantom_inline_object_property() -> None:
    document = [t for t in fixture("pdf_typescript")["types"] if t.get("class_import") == "document.Document"]
    assert len(document) == 1
    assert sorted(p["name"] for p in own(document[0]["properties"])) == sorted(DOCUMENT_PROPERTIES)
    # `class Flow` (src/flow.ts:1227) declares no property; the 189 phantoms came from private-method return types.
    flow = [t for t in fixture("pdf_typescript")["types"] if t.get("class_import") == "flow.Flow"]
    assert len(flow) == 1
    assert flow[0]["properties"] == []
    assert by_name(flow[0], "methods")["AddParagraph"]["return_type"] == "this"
    # a class property may only be declared in the class itself: no class property line precedes the class line
    for item in fixture("pdf_typescript")["types"]:
        if item["kind"] in ("class_declaration", "abstract_class_declaration"):
            for prop in own(item["properties"]):
                assert prop["file"] == item["file"] and prop["line"] >= item["line"], (
                    item["name"],
                    prop["name"],
                )


def test_pdf_keeps_the_four_level_interface_chain() -> None:
    chain = ["ComboBoxInit", "ChoiceInit", "FieldInit", "FieldStyle"]
    names = {t["name"]: t for t in fixture("pdf_typescript")["types"]}
    assert [names[name]["bases"] for name in chain] == [
        ["ChoiceInit"],
        ["FieldInit"],
        ["FieldStyle"],
        ["WidgetStyle"],
    ]


def test_a_property_that_is_read_only_upstream_stays_read_only() -> None:
    # src/document.ts:371 -- `readonly Pages: Page[]`
    document = entry("pdf_typescript", "document.Document")
    assert by_name(document, "properties")["Pages"]["writable"] is False
    # src/document.ts:459 -- `recovery?: RecoveryReport` is a plain writable optional field
    recovery = by_name(document, "properties")["recovery"]
    assert recovery["writable"] is True
    assert recovery["modifiers"] == ["optional"]


def test_the_readme_allowlist_holds_only_non_api_words_for_the_three_pilots() -> None:
    allowed = json.loads((FIXTURES / "readme_identifier_allowlist.json").read_text(encoding="utf-8"))
    assert allowed.get("cells_typescript") is None
    assert allowed.get("pdf_typescript") is None
    # RangeError is a JavaScript built-in, `org` the Java-style namespace root of a package path, and
    # `json` the file name in `package.json`: none is an API symbol of the 3d package.
    assert allowed["3d_typescript"] == ["RangeError", "json", "org"]
