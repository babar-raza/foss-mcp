# Round-3 independent review of the shipped image (2026-10-17)

This file is the register of every failure, gap and lead that the third independent review found against
`foss-mcp-serving:demo-91dcf9b` (image id `sha256:1eca007c463f`, built from commit `91dcf9bd`, 32 pilots, gate 32 of 32,
local only, NOT pushed). It is the source of truth for what must be closed before an image is uploaded.

- Requirement: `REQ-G3-009` (ops/requirements.yaml). Decision: docs/DECISION_LOG.md, entry "Round-3 independent review ...".
- Work: the cards named in the "Card" column (plans/TC-5xx.yaml). Nothing in this file is executed work: every row is OPEN.
- Closure rule (REQ-G3-009 and REQ-G3-008): a row is CLOSED only when its card is accepted with a permanent regression test or
  deployment proof, or when the row is moved to "Accepted or upstream" below with a written reason in docs/RELEASE.md.
- Evidence root (outside the repository, kept by the supervisor): the round-3 audit folder, one sub-folder per investigator
  R1..R12 (`R6/rt/...` runtime programs, `R7/out/...` reflection dumps, `R5/oracle/...`, ...) and its digest file. Paths in the
  Evidence column are relative to that root. Finding ids in "Src" are the investigators' own ids (qrN-xx, RN-xx, R9-Tn, qr2-D/H/L/R, qr1-N, qr4-xx, qr7-xx, qr12-D).

## 1. Method (what "independent" meant)

Twelve read-only investigators attacked the image and the repository with these rules: no repo edits, no state changes, no cluster
changes, mutation experiments only on copies of the baked data in throwaway containers; fixtures and the project's extractor were
never used as the oracle (upstream source, compiled libraries, reflection, the real package registries and real toolchains were);
no reproduction means no defect; severity follows evidence. Slices:

| Id | Slice | Result file |
|---|---|---|
| R1 | baseline, change map, the 85 pre-production register rows | reports in the evidence root |
| R2 | the 133 ledger rows, automated suites, test-weakening audit, blind spots, 19-fixture upstream audit, reproducibility | |
| R3 | protocol (4 SDK clients), 32-pilot transport, 9,725 security probes, concurrency, soak, cancellation, NetworkPolicy | |
| R4 | list_recent_changes sequencing, rollback, publish policy, storage corruption, product reference, cold path | |
| R5 | Python differential, 11 pilots, whole surface | |
| R6 | TypeScript, Go, Rust, C++ differential, 10 pilots, runtime programs | |
| R7 | .NET and Java differential, 11 pilots, whole surface, reflection oracles | |
| R8 | retrieval and discovery, 32 pilots, 444 natural-language tasks | |
| R9 | agentic workflows, Python and Node pilots | |
| R10 | agentic workflows, .NET and Java pilots | |
| R11 | agentic workflows, C++, Go, Rust pilots | |
| R12 | install truth, registry and package drift, recent changes, product reference, deprecation, 32 pilots | |

## 2. Headline

Verdict NOT_READY for "primary development-context service for every intended product": 1 pilot READY, 19 READY_WITH_ACCEPTED_LIMITATION,
11 NOT_READY, 1 BLOCKED_BY_UPSTREAM (tex/python). Solid and re-verified: protocol (the original lockout is gone; Python SDK 2.2.0 and 1.13.1,
TypeScript SDK 1.17.4 and 1.32.1), isolation (0 leaks in 9,725 probes), honesty (0 fabrications in 1,150+ queries), type coverage,
publish safety, pointer-flip consistency, readiness refusals. Not solid: member-level fidelity of the older extractions, install and
package truth, drift detection, retrieval from user wording, shipped recovery tooling.

NOT_READY pilots: 3d/net, 3d/python, 3d/typescript, barcode/python, cells/cpp, cells/python, cells/rust, cells/typescript,
email/python, note/python, page/python. BLOCKED_BY_UPSTREAM: tex/python. READY: cells/net. All other pilots are
READY_WITH_ACCEPTED_LIMITATION with the gaps listed below.

## 3. Findings register

Severity: P1 can make an agent write failing or wrong code from served information, or block first use; P2 materially degrades
correctness or discoverability; P3 hardening or disclosure. "Type": NEW, REG (regression of a supervisor fix), STILL (a prior finding that
is still present), LEAD (not proven, needs an experiment first). Status: see the Status column added to each row by this card's closure (G3/TC-537), derived from the accepted cards' own receipts in project/state.yaml: CLOSED with its accepted card id, or DISCLOSED/REJECTED with its disposition in section 4.

### 3.1 Python extraction (card TC-532)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-001 | P1 | REG | cells/python, barcode/python, email/python, note/python, page/python | Fixtures predate the defaults, keyword-only `*` and static/classmethod work (15 of 32 fixtures carry no extractor_version): defaults, `*` and marks lost. barcode 26 of 27 sampled methods lost defaults and 34 the `*`; cells 177 of 1,342 defaults and 53 static marks; email 51 of 239 and 40 classmethod marks; note 48 of 145; page 66 of 923 and 24 static. Runtime proof: `generate("code128","HELLO123",None,None)` and email `from_bytes(...)` with 4 positional arguments raise TypeError. | qr5-1, qr1-N1, qr2 L-026, R9-T4 | R5/oracle, R5/exec_tests.py, R1/up | CLOSED (TC-532) |
| RD3-002 | P1 | STILL | barcode/python (8 enums, 36 members), html/python (TokenizerState, 80, P3) | Enum members built with `auto()` are dropped, so legal option values are undiscoverable. | qr5-3, qr2-D2, R9-T5 | R5/oracle, R2/dataaudit* | CLOSED (TC-532) |
| RD3-003 | P1 | NEW | 3d/python | Two classes of the same name (the real one and an empty stub that the package re-imports last) are merged: `FbxLoadOptions`, `ObjLoadOptions`, `ObjSaveOptions`, `StlLoadOptions`, `StlSaveOptions`, `GltfLoadOptions`, `GltfSaveOptions` are empty at runtime (`FbxLoadOptions(fmt)` -> TypeError) but are served with members and constructors, and the first search hit is the broken one (`AttributeError: create_save_options`). | qr5-2, R9-T2 | R5/exec_tests.py, R9/3d-python | CLOSED (TC-532) |
| RD3-004 | P2 | NEW | 3d/python (41), slides/python (75) | A method defined twice in a class body is served with the first, shadowed definition; keyword calls fail (`remove_property(property=...)`). | qr5-5 | R5/oracle | CLOSED (TC-532) |
| RD3-005 | P2 | NEW | pdf/python, slides/python | Annotation classes are empty or partial: the underscore-prefixed base is not followed (18 classes, 877 inherited members missing) and factory-built properties are invisible (58). 8 re-exported pdf types and about 10 slides alias types are served with no members (QA round C). | qr5-4, QA-C P2-2/P2-3 | R5/oracle | CLOSED (TC-532) |
| RD3-006 | P3 | NEW | page, tex, email, barcode | Dataclass `__init__` is not synthesised and class-level and instance attributes are absent: `ImageSaveOptions(...)` fails with a missing required `format` the MCP never shows. | qr5-7, R9-T4 | R9/page-python | CLOSED (TC-532) |
| RD3-007 | P3 | NEW | pdf (18), slides (85) | An overriding method shows the base-class signature. | qr5-8 | R5/oracle | CLOSED (TC-532) |
| RD3-008 | P3 | STILL | 3d, pdf | Properties assigned as `x = property(getter)` or by factory are missing (`Matrix4.identity`, `Annotation.color`). | qr2 L-027 | R2/fixtures_A | CLOSED (TC-532) |
| RD3-009 | P3 | LEAD | barcode, words, slides | barcode public return types not indexed (7 of 42); words class-constant "enums" carry no values; slides `Presentation.__init__(*args, **kwargs)`. | qr5-9, qr5-10, qr2-L2 | R5 | CLOSED (TC-532) |

### 3.2 TypeScript extraction (card TC-533)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-010 | P1 | STILL | cells/typescript | 84% of compared members disagree with upstream: every interface property is shown read-only (724 vs 0), type-alias bodies dropped (25 of 25), 768 property types carry a leading colon, 22 static flags, 28 optional parameters and 8 defaults lost, `this`/iterator returns empty, and `Workbook` lists about 12 of 21 properties that belong to private-method return types. The `password` save option is a silent no-op while the signature advertises it. | qr1-N2, qr2-D4, R9-T4, R9-T5, R9-T8 | R2/fixtures_B, R9/cells-typescript | CLOSED (TC-533) |
| RD3-011 | P1 | NEW | 3d/typescript | static 0 of 56 and optional 0 of 51 marked (91 static methods, 114 static properties, 72 defaults, 69 optionals, 20 rest parameters lost): `scene.fromFile("m.obj")` fails `tsc` (TS2576); `Mesh.createPolygon(...args)` is served as `createPolygon()`. | R6-TS-1, qr2-D4, R9-T4 | R6/rt/ts3d, R6/exp | CLOSED (TC-533) |
| RD3-012 | P2 | STILL | pdf/typescript | 3,060 `function` chunks of which 1,941 are not exported (532 public exports in total) and 189 phantom inline-object properties: nearly two thirds of symbol results are internals. | qr2-D3, R6-TS-2, qr1-N3 | R6/ts_pdf.json, R2 | CLOSED (TC-533) |
| RD3-013 | P3 | NEW | pdf/typescript | Generic utility-type bases split at the comma (`Omit<StampOptions, 'align'|'rotate'>`). | qr2-D6 | R2 | CLOSED (TC-533) |

### 3.3 C++ extraction (card TC-534)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-020 | P2 | NEW | slides/cpp (286 Color constants), pdf/cpp (56), cells/cpp (14 missing) | `static const` / `static constexpr` data members lose their qualifiers and are served writable. | qr2-D5 | R2/fixtures_B | CLOSED (TC-534) |
| RD3-021 | P2 | NEW | pdf/cpp (14 to 21), slides/cpp (8 to 10) | Public nested types missing though public signatures reference them (`PdfFileEditor::ContentsResizeParameters`, collection iterators). | R6-CPP-2, qr2-D5 | R6/cpp_*.json | CLOSED (TC-534) |
| RD3-022 | P2 | STILL | slides/cpp (55 `Internal::*`, 28 labelled public), cells/cpp (8 `.cpp`-local classes) | Implementation types served as API. | R6-CPP-1, qr2 L-033 | R6, R2 | CLOSED (TC-534) |
| RD3-023 | P3 | LEAD | all 4 C++ pilots | Member-level compile verification was not completed by the review (type-level only); the new fidelity gate must cover it. | R6 scope | R6 | CLOSED (TC-534) |

### 3.4 Go and Rust extraction (card TC-535)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-025 | P1 | NEW | pdf/go | The `ai` sub-package (31 names: `NewChatCopilot`, `OpenAIClient`, `APIError`, ...) is served as the root package with no import path; `pdf.NewChatCopilot` -> `undefined`. | R6-GO-1, R11-G1 | R6/rt/go_pdf | CLOSED (TC-535) |
| RD3-026 | P2 | NEW | pdf/go (26), cells/go (20) | Untyped package constants and vars not indexed (`PageFormatA4`, `FontHelvetica`, `ErrEncrypted`, `AlignCenter`, ...). | R6-GO-2 | R6/go_*.json | CLOSED (TC-535) |
| RD3-027 | P3 | STILL | cells/go, pdf/go | Phantom functions (`Close`, `ReadAt` of unexported types), struct-tag text served as the field type (`Cell.XMLName`, 5 fields), `RowCallback` has no signature; 424 promoted/embedded members of pdf/go differ from upstream (20.6%). | R6-GO-3, qr2 L-036 | R2/fixtures_B | CLOSED (TC-535) |
| RD3-030 | P1 | NEW | cells/rust | 106 of 221 symbols are not importable (private modules, trait-impl functions `eq`/`fmt`/`hash`), so `use aspose_cells_foss_rust::WorkbookModel` fails E0432; the coverage line overstates the public surface. | R6-RS-1 | R6/rt/rust_b, R6/rs_leak.json | CLOSED (TC-535) |
| RD3-031 | P2 | STILL | cells/rust | Method receivers (`&self`, `&mut self`, `self`) and generic bounds are not rendered (0 of 1,430): an MCP-literal program fails E0596. | R6-RS-2, RST-11 | R6/rt/rust_a | CLOSED (TC-535) |
| RD3-032 | P2 | NEW | cells/rust | Data-carrying enum payloads are lost (`CellValue`, `CellsError`, `CellObjectValue`). | R6-RS-4 | R6 | CLOSED (TC-535) |
| RD3-033 | P3 | NEW | cells/rust | Type aliases not indexed (`Style`, `WorksheetCollection`, ...) while signatures return them; "Bases" lists derive traits. | R6-RS-3, qr2 L-124 | R6 | CLOSED (TC-535) |

### 3.5 Java extraction (card TC-557)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-040 | P2 | STILL | 3d/java, pdf/java (9 of 9) | Varargs parameters dropped (`Line.fromPoints(Vector3...)` served `fromPoints()`): empty object at runtime. | qr7-01, RST-10 | R7/proof/threed_java | CLOSED (TC-557) |
| RD3-041 | P2 | STILL | all Java | `throws` never rendered (0 of 609): an MCP-only program fails `unreported exception IOException`. | qr7-02 | R7/proof | CLOSED (TC-557) |
| RD3-042 | P2 | STILL | all Java | `static` never marked (0 of 799). | qr7-03, R10-7, qr2-D6 | R7, R10 | CLOSED (TC-557) |
| RD3-043 | P2 | STILL | Java and .NET | Public mutable instance fields never served (3d/java 55, pdf/java 133, slides/java 10, 3d/net 56, pdf/net 81 incl. 23 `HtmlSaveOptions` options). | qr7-04 | R7/fields.py | CLOSED (TC-557) |
| RD3-044 | P2 | NEW | Java 14, .NET 9 | Generic method type parameters and bounds dropped (`Node.GetEntity<T>()` served `GetEntity() -> T`: CS0411). | qr7-06 | R7/generics.py | CLOSED (TC-557) |
| RD3-045 | P3 | NEW | pdf/java (about 21), cells/java (6) | Methods of anonymous inner classes attributed to the outer type (phantom `Page.add(Artifact)`, `OptionCollection.hasNext()`). | qr7-12, qr2-D6 | R7 | CLOSED (TC-557) |
| RD3-046 | P3 | NEW | slides/java, email/net | Record members lost (canonical constructor, accessors, `Deconstruct`, equality). | qr7-14 | R7 | CLOSED (TC-557) |
| RD3-047 | P3 | NEW | all Java and .NET | Implicit default constructors not rendered (pdf/net 139, words/net 134, pdf/java 84, ...); an agent cannot tell "no public constructor" from "implicit default". | qr7-13 | R7/summary.txt | CLOSED (TC-557) |

### 3.6 .NET extraction (card TC-558)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-050 | P2 | NEW | 3d/net (67), pdf/net (17), email/net (4), slides/net (4) | Operators and conversions never served (0 of 92). | qr7-05 | R7 | CLOSED (TC-558) |
| RD3-051 | P2 | NEW | words/net, pdf/net | `using` aliases served unresolved, sometimes per target framework: words/net `Image = SkiaSharp.SKBitmap` (CS1503), pdf/net `SdiImageFormat` in 14 overloads (CS0103). | qr7-08 | R7/proof/words_net | CLOSED (TC-558) |
| RD3-052 | P3 | NEW | 3d/net | `TriMesh` and `TriMesh<T>` merged in one chunk (`TriMesh.FromMesh(mesh)` CS1501). | qr7-07 | R7 | CLOSED (TC-558) |
| RD3-053 | P3 | NEW | pdf/net 8, words/net 4, pdf/java 6 | Generic base lists cut at the comma (`['IDictionary<string','XmpValue>']`). | qr7-11 | R7 | CLOSED (TC-558) |
| RD3-054 | P3 | NEW | pdf/net (14 delegates, 7 events), 3d/net (3 delegates) | Delegates and events not indexed. | qr7-15 | R7 | CLOSED (TC-558) |
| RD3-055 | P3 | NEW | words/net | 3 types inside `#if CPLUSPLUS public #else internal` treated as public. | qr2-D6 | R2 | CLOSED (TC-558) |

### 3.7 Unsupported and stub capability markers (card TC-559)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-060 | P2 | NEW | 3d/python (734 of 3,743 members are `NotImplementedError`/`pass` stubs), 3d/net (`Scene.Render`, `PolygonModifier`, `PdfFormat`), pdf/python (`HtmlLoadOptions`, `SvgLoadOptions`), slides/cpp and slides/java (`SaveFormat` lists PDF/XPS/TIFF/HTML but save throws or silently writes a PPTX named .pdf), cells/cpp (`Save("out.csv")` writes XLSX), words/net (PDF/HTML/EPUB throw) | Unsupported or stub symbols are served as normal API; the scope documentation says they throw (symbol found, docs say unsupported). | R8-03, qr5-6, R10-3, R11-S2, R11-C3 | R8/deep6.py, R10, R11 | CLOSED (TC-559) |
| RD3-061 | P3 | NEW | slides/net, words/net, slides/java | Negative knowledge is not reachable through `lookup` (limitations "no data source"; "save presentation as PDF" returns the quick start). | R10-4 | R10 | CLOSED (TC-559) |

### 3.8 Freshness and drift (card TC-560)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-070 | P2 | NEW | slides/python, 3d/java, 3d/net, slides/java | The index is ahead of the published package and nothing says so: slides/python documents `hyperlink_click` (AttributeError on 26.8.0), 3d/java 65 source types absent from the jar (`ImageRenderOptions`: javac cannot find symbol), 3d/net 103 documented types absent from NuGet 26.1.0 (196 vs 293; documented `FileFormat` names do not compile), slides/java 45 commits ahead. Freshness reports `unknown`, `in_index: true`. | qr12-D1, R10-1, qr7-10 | R12/exp, R7/proof/threed_net_pkg | CLOSED (TC-560) |
| RD3-071 | P2 | NEW | pdf/go, pdf/net | The package is ahead of the index and it is not detected for Go (v0.11.0 `HiddenDataSanitizationOptions` absent from the index; `go get` resolves it); pdf/net 26.10.0 is detected only by version string. | qr12-D2, qr7-10 | R12/exp/gopdf | CLOSED (TC-560) |
| RD3-072 | P3 | NEW | note/python, cells/go | `list_recent_changes` ignores tags newer than the latest GitHub release (v26.9.0, v26.7.1) though it falls back to tags when there are no releases. | qr12-D4 | R12/truth | CLOSED (TC-560) |
| RD3-073 | P3 | NEW | email/python, page/python | `stale` is reported in the wrong direction for tag-only repositories (the index is ahead of the tag). | qr12-D7 | R12 | CLOSED (TC-560) |

### 3.9 Install guidance, product reference and tex (card TC-561)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-080 | P1 | REG | cells/cpp | The install section gives no repository URL, commit, `SOURCE_SUBDIR Aspose.Cells.Foss.Cpp` or CMake target (`aspose_cells_foss`) and does not mention the NuGet package `Aspose.Cells.Cpp.FOSS`; plain FetchContent adds nothing. TC-518 had made it serve the README recipe; the re-ingest lost it and the test was rewritten to accept the loss. | R11-C1, qr12-D5, qr2-R1, qr4-09 | R11/cellscpp, R2/weakening; TC-575 makes visible and honestly discloses (not closes) the related manifest_path/readme_document gap this row's own TC-561 closure never populated | CLOSED (TC-561) |
| RD3-081 | P2 | NEW | slides/cpp | Install snippet names a CMake target that does not exist (`aspose_slides_foss`; real `AsposeSlidesFoss::AsposeSlidesFoss`, named in the same section). | R11-S1 | R11/slidescpp | CLOSED (TC-561) |
| RD3-082 | P2 | NEW | pdf/cpp | The documented `add_subdirectory` route is broken upstream (`${CMAKE_SOURCE_DIR}`); the working route is a standalone build, `cmake --install`, `find_package`. | R11-P1 | R11/pdfcpp | CLOSED (TC-561) |
| RD3-083 | P3 | NEW | email/cpp | No repository URL anywhere. | R11-E1 | R11 | CLOSED (TC-561) |
| RD3-084 | P3 | NEW | imaging/net | Says "not found on NuGet" and then quotes `dotnet add package Aspose.Imaging.Foss` verbatim. | qr12-D6 | R12 | CLOSED (TC-561) |
| RD3-085 | P2 | NEW | words/python | `pip install aspose-words-foss` fails on Python 3.13 (`Requires-Python <3.13`); the install section is silent, only the compatibility section has the bound. | R9-T3, qr12-L3 | R9/words-python | CLOSED (TC-561) |
| RD3-086 | P2 | NEW | pdf/typescript | Install section empty and no getting_started documents: build steps (`npm install`, `npm run build`, `dist/index.js`) needed the upstream README. | R9-T6 | R9/pdf-typescript | CLOSED (TC-561) |
| RD3-087 | P3 | NEW | 4 Java pilots | Maven coordinate without a version; slides/java pom 26.8.0 vs Central 26.7.0. | R10-8 | R10 | CLOSED (TC-561) |
| RD3-088 | P3 | NEW | 3d/net | Compatibility `net10.0` taken from the converter project; the package targets net6/8/10. | qr12-D8 | R12 | CLOSED (TC-561) |
| RD3-089 | P2 | STILL | cells/cpp, pdf/typescript, imaging/net, words/net, barcode, tex, slides/net | formats/limitations answer "no data source" though the README has the section; agent_guidance NotAvailable for pdf/go though CLAUDE.md exists upstream (fill rates: formats 27/32, limitations 27/32, agent_guidance 19/32). | qr4-09, AGT-02, D-12 | R4/out_prodref | CLOSED (TC-561) |
| RD3-090 | P3 | NEW | 3d/python, 3d/typescript | agent_guidance is a contributor porting prompt ("implement a python module called ..."). | qr12-H1 | R12 | CLOSED (TC-561) |
| RD3-091 | P3 | NEW | pdf/java, words/python | Deprecation partly lost (replacement not named; `DeprecationWarning` kwargs unmarked). | qr12-D9 | R12/depr.py | CLOSED (TC-561) |
| RD3-092 | P1 | NEW | tex/python | `import aspose_tex` raises IndentationError at the pinned commit (35 of 45 files fail `ast.parse`); the MCP serves the repaired API and quick start as usable and its notice says "11 of 45 source files could not be parsed". | R9-T1, qr5 tex note | R9/tex-python | CLOSED (TC-561) |

### 3.10 Retrieval and discovery (card TC-562)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-100 | P2 | NEW | all (cells family weakest, lookup MRR .27 to .44) | Natural-language queries with function words miss ("merge cells" hits, "merge cells together" misses; no stop-word list, small synonym list, no plural handling in `search_symbols`; lookup hit@10 .61 over 444 tasks). | R8-01 | R8/analysis.json | CLOSED (TC-562) |
| RD3-101 | P2 | NEW | words/net, words/python, slides/net, slides/python, pdf/python, pdf/typescript | Wrong-but-plausible answers with the correct type absent ("make text bold" -> `TextPath`; "add a slide" -> paragraph collections; "merge pdfs" -> `DssMaterial`); 6% of lookup tasks return a non-empty wrong answer. | R8-02 | R8/deep5.py | CLOSED (TC-562) |
| RD3-102 | P3 | STILL | pdf/net, 30 of 32 pilots, pdf/cpp, pdf/go, words/net, pdf/net+pdf/java | `lookup` returns unrelated table chunks instead of an honest miss ("merge pdf files", "colour"); "getting started" misses on 30 of 32; `search_symbols('Do')` returns nothing; "watermark" misses `AddTextWatermark`; "save a document as PDF" returns a class table. | qr1-N5, qr1-N6, qr7-16, qr7-17, R11-G2 | R1/out, R7 | CLOSED (TC-562) |
| RD3-103 | P3 | NEW | 29 of 32 | Gibberish (`a.b.c::d`) returns documentation and examples. | R8-06 | R8/deep1.py | CLOSED (TC-562) |
| RD3-104 | P3 | NEW | html/python and large results | No pagination: `search_symbols top_k=100` returns about 12 items; `omitted_items` only inside `response_budget`. | R8-05 | R8/deep3.py | CLOSED (TC-562) |
| RD3-105 | P3 | NEW | all | No Unicode folding (full-width, non-Latin queries are plain misses). | R8-07 | R8 | CLOSED (TC-562) |
| RD3-106 | P3 | NEW | 32 of 32 | A `find_examples` miss says "semantic fallback skipped: error" / `vector_fallback: skipped_error` in the by-design lexical-only mode. | R3-02, qr1-N7, R8-07 | R3 | CLOSED (TC-562) |
| RD3-107 | P3 | NEW | all | `search_docs` requires `content_type` while `lookup` does not. | qr2-H3 | R2 | CLOSED (TC-562) |
| RD3-108 | P3 | LEAD | barcode (97%), 3d/python, note, pdf/python, words/python | Alias duplicates (3% of chunks) and internal helpers may dilute the top 10. | R8-08 | R8 | CLOSED (TC-562) |

### 3.11 Documentation excerpts (card TC-563)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-110 | P2 | REG | all (58% of doc chunks; 64% of pdf/python hits) | The PS-02 response budget cuts every documentation item to 1,536 characters even when the response is tiny; 11 chunks are cut inside a code fence; the marker says "narrow the query" but a narrowed query returns the same cut chunk (no way to fetch the rest). | R3-01, R8-04 | R3/trunc.py | CLOSED (TC-563) |

### 3.12 Serving robustness and operations (card TC-564)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-120 | P2 | REG | all (not reachable in the baked image) | Sidecars are read once at boot; an absent sidecar makes `list_recent_changes` return `[]` with the note "An empty list is a complete answer, not a fetch failure" (pdf/go really has 12 releases), `/readyz` stays 200 with no warning and nothing is logged; sidecars added later are ignored until restart. | qr4-01 | R4/out_sidecar | CLOSED (TC-564) |
| RD3-121 | P2 | NEW | Kubernetes | `infra/rollback.py` is in no shipped image, though docs/DEPLOYMENT.md says it is: the documented rollback runbook cannot run. | qr4-02 | R4/tools | CLOSED (TC-564) |
| RD3-122 | P3 | NEW | all | Rollback validates shape only (accepts a 1-chunk, 0-symbol, 60%-regressed, different-repository or different-commit target; no post-flip readiness probe or auto-revert); sidecars are not rolled back (install notice keeps the newer commit); `--list` crashes on a corrupt generation file; fenced publishers leave orphan generation files. | qr4-03, qr4-04, qr4-08 | R4/out_rollback | CLOSED (TC-564) |
| RD3-123 | P3 | NEW | all | A corrupt pointer or generation makes `list_recent_changes` and `report_index_freshness` return `invalid_argument`/`internal` with raw exception text; a vanished pointer file gives non-error `found:false`. | qr4-05, qr4-06 | R4/out_corrupt | CLOSED (TC-564) |
| RD3-124 | P3 | NEW | all | Observability: the default `/metrics` body is two counters (Prometheus on request only), `serverInfo.version` is `0.1.0+unknown`, call logs lack generation id and source commit, success responses carry no correlation id, transport rejections are logged without reason; `ClientDisconnect` is an ERROR with a traceback and no ids. | R3-03, R3-04, qr4-10, qr2-H4 | R3, R4 | CLOSED (TC-564) |
| RD3-125 | P3 | STILL | all | 200 abandoned sessions lock every new client out (503) until the 5-minute idle sweep; sessions are lost on restart (`Session not found`, no recovery); no read timeout for stalled connections; Host not validated; invalid request ids get an empty 202; two error envelope shapes. | R3-05, R3-06, R3-07, R3-10, R3-11, qr2-H1 | R3 | CLOSED (TC-564) |
| RD3-126 | P3 | NEW | all | `/data/manifests` is owned by the server user, so sidecars can be removed when a peer runs without `--read-only`. | qr2-H2 | R2 | CLOSED (TC-564) |

### 3.13 Example truth (card TC-565)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-140 | P2 | NEW | 3d/net, 3d/java | Furnished examples labelled "Compiled or run by the ingestion verifier" are compile-only and fail or produce nothing at runtime: 3d/net `Save("o.obj", FileFormat.WavefrontOBJ)` throws NotImplementedException; 3d/java "Build a Scene From a Primitive" writes an empty 35-byte STL, "Quick Start (2)" throws IOException; the same example is returned twice at two scores. | R10-2, qr4-07 | R4/out_cold, R10 | CLOSED (TC-565) |
| RD3-141 | P3 | STILL | all | Examples exist only for README paths (1 to 14 per pilot; "add a digital signature" misses on 32 of 32). | AGT-06 | R1 | CLOSED (TC-565) |

### 3.14 Permanent verification (card TC-566) and release hygiene (cards TC-536, TC-537)

| Id | Sev | Type | Pilots | Finding | Src | Evidence | Status |
|---|---|---|---|---|---|---|---|
| RD3-150 | P1 | STILL | fleet | No independent oracle: the shipped data is a pure function of the committed fixtures (14,347 of 14,348 chunks rebuild byte-identically), so every image-vs-fixture test proves self-consistency only. An upstream audit of 19 fixtures found 0 to 2% disagreement for the newest extractor and 6% to 84% for older ones. | qr2 L-095, area 48/49 | R2/fixtures_A, R2/fixtures_B | CLOSED (TC-566) |
| RD3-151 | P3 | NEW | fleet | Live container tests cover 7 of 32 pilots and rot: the pdf/typescript e2e test still pins the old two-commit mixed generation and fails at HEAD. | qr2-D1 | R2 | CLOSED (TC-536) |
| RD3-152 | P3 | NEW | environment | `tests/test_githooks.py` expects `core.hooksPath` to be the relative `.githooks`; this clone stores an absolute path. | ci_check.log | | CLOSED (TC-566) |
| RD3-153 | P3 | NEW | environment | A Windows application-control policy intermittently blocks real Rust builds in tests (os error 4551): the 3 real-Rust tests error in some runs and pass in others. | ci_check.log, R2 | | CLOSED (TC-566) |
| RD3-154 | P3 | NEW | process | Weakened or rewritten-to-match assertions to revisit: W2 `test_cpp_installs_are_the_readme_recipe` (cells/cpp dropped; reverse after RD3-080), W5 `EXTRACTOR_VERSION == 2` relaxed to `>=`, W3 data ceiling raised to 45 MB, W4 related-tests retry, W6 TC-554 deselects 3 Rust tests, W1 positive guard on cells/cpp free functions removed. | qr2 weakening audit | R2/weakening | CLOSED (TC-534, TC-561, TC-566; W3 accepted via OQ-017; W4 not independently reverified) |
| RD3-155 | P3 | NEW | docs | docs/RELEASE.md sections 8 and 9 are stale in 8 places (L-071, L-080, L-097, L-100, L-102, L-107, L-128, TC-397/402); promote L-095, L-121, L-125; close stale rows; PREPROD_REVIEW.md (TC-537) must include this register. | qr1-N4 | R1 | DISCLOSED (section 4) |

### 3.15 Follow-up risk list reviewed on 2026-10-19 (cards TC-568 to TC-570 and supervisor items SUP-1 to SUP-8)

The operator pasted an eleven-point "risks, gaps and follow-ups" list. Each point was re-checked against the live repository, state and remote
before anything was registered. Verdicts: points 2 (round-3), 4 (image behind HEAD: the 12 later commits touch no image input, `stale_image_paths` is empty;
a rebuild after the fixes is already enforced by the gate-exit freshness check) and 9 (accepted limitations: OQ-008 is RESOLVED; the Java/Rust items are now
reversed by TC-535 and TC-557) were already covered. The rest produced these rows.

| Id | Sev | Type | Scope | Finding (verified on 2026-10-19) | Closes with | Status |
|---|---|---|---|---|---|---|
| RD3-160 | P2 | STILL | delivery | Nothing is delivered: four recorded images (demo-cbbd93e, 4e5804b, 58ba586, 91dcf9b), all LOCAL_ONLY, no push result anywhere, `gatectl gate-exit` problem "no standalone demo image is recorded as PUSHED with a passing 32/32 report" (REQ-G3-001..004). OWNER-13 (registry host and the operator's `docker login`) is the blocker. Seven owner items are OPEN, not one (see RD3-162). | OWNER-13, SUP-7 | DISCLOSED (section 4) |
| RD3-161 | P2 | NEW | delivery | `scripts/release/push_image.py` captures the pushed digest but never pulls the image back from the registry and re-runs the 32 of 32 gate against the registry copy, so a successful push proves nothing about what a peer will pull. | TC-569 | CLOSED (TC-569) |
| RD3-162 | P3 | NEW | process | OWNER-07 (pdf/java example does not compile upstream), 08 (GitHub rate limit), 10 (docker compose permission denial), 11 (B08 missing audit text), 12 (stale live Jobs, fake install coordinates), 13 (registry), 14 (GitHub token HTTP 401) are OPEN; only OWNER-13 is consumed by a gate; the others were last checked between 10-04 and 10-14 and some are probably satisfied (the 10-17 re-ingest re-ran every Job; workers ran docker compose under TC-567). OWNER-14 matters for the closing re-ingest: with an invalid token the sidecar fetch fails and sidecars degrade to `fetch_failed`. | SUP-2, OQ-015 | DISCLOSED (section 4) |
| RD3-163 | P2 | STILL | process | Card-authoring defects keep happening: 4 in the last 48 hours (TC-554 and TC-555 `min_tests` above the first check's collected count, TC-554 check 2 including host-sensitive tests, TC-567 check naming a file that does not exist); 34 rework and 11 supersede instructions since 10-12 (81 dispatches); the decision log attributes 65% of rework to card defects. There is no preflight. | SUP-1 | DISCLOSED (section 4) |
| RD3-164 | P2 | NEW | evidence | Quoted numbers are not reproducible from committed output: "about 17,000 methods, 0 real mismatches" (this register's source, written from agent reports), "183 verified examples are baked" (decision log 10-15) while the 126 example chunks are served by the image, `tests/fixtures/verified_examples` holds 273 entries of which about 197 read as verified, and the pdf/typescript pin note says about 4,445 types where the fixture holds 4,393. | TC-568, SUP-6 | CLOSED (TC-568) |
| RD3-165 | P3 | NEW | review | Most of the 1.57M added lines are generated fixtures, baked generations and evidence; a human cannot review them and no tool summarises what a re-ingest changed per pilot (types, functions, members, signatures, docs, examples). | TC-568 | CLOSED (TC-568) |
| RD3-166 | P3 | NEW | process | `origin/main` equals HEAD although G3 is not accepted (AGENTS.md: the supervisor pushes main once per accepted gate); the 10-17 push was ordered by the operator and earlier pushes happened too. Remote CI for the pushed head IS green (workflows CI and Mirror to GitLab, 2026-10-17 14:59Z, head 0e6eadf4; previous head 11d258a6 also green), but no CI result is recorded in the repository. The repository is public. | SUP-3 | DISCLOSED (section 4) |
| RD3-167 | P3 | NEW | process | No committed liveness record: commit gaps of 21.4 h (10-13 12:01 to 10-14 09:26), 13.3 h, 12.2 h and 9.9 h and no commits on 10-18 and 10-19 have no reason in the repository (10-18 and 10-19 were idle waiting for the operator's go-ahead; the earlier gaps are unexplained). | SUP-4 | DISCLOSED (section 4) |
| RD3-168 | P3 | NEW | hygiene | Untracked `evidence/build/G3/TC-498/` (TC-498 is SUPERSEDED, TC-499 ACCEPTED); 151 agent worktree directories (13.5 GB) under `.claude/worktrees` inside a OneDrive-synced path (excluded by `.git/info/exclude`), 153 registered worktrees, 314 local `worker/*` branches (8 merged), 74 local foss-mcp docker images, stale cluster namespaces poc14b and poc14c. | SUP-5 | DISCLOSED (section 4) |
| RD3-169 | P3 | NEW | reproducibility | `Dockerfile.ingestion` floats the .NET SDK channels (8.0 and 10.0), `uv python install 3.12` and the apt packages (openjdk-21-jdk, maven, g++, cmake, git-lfs): example-verification verdicts depend on the day the image was built (OQ-008 accepted this for the C++ verifier; R2 area 50 found the rest). | TC-570 | CLOSED (TC-570) |
| RD3-170 | P3 | NEW | environment | `core.hooksPath` is stored as an absolute path and something rewrites it after it is set to `.githooks` (the push script sets it again in the same command). | SUP-5 | CLOSED (TC-566) |
| RD3-171 | P3 | REJECTED | delivery | The internal GitLab project that main is mirrored to has a working container registry (verified read-only on 2026-10-19 with the operator-named GITLAB_TOKEN; nothing printed or stored), but the OPERATOR STATED ON 2026-10-19 THAT IT IS NOT THE DELIVERY REGISTRY. The delivery registry is a different one and the operator will share its access when the logged tasks are done. Do not push to the GitLab registry. | SUP-7 | REJECTED (section 4) |
| RD3-172 | P3 | LEAD | publication | The repository is public: review what a push publishes (internal GitLab URL in the mirror workflow, the operator's email in commit metadata, the registry host and image digest once delivered, this register of defects). | SUP-8 | DISCLOSED (section 4) |

## 4. Accepted or upstream (disclose, no product code change)

Each needs a sentence in docs/RELEASE.md or the pilot's limitations text (cards TC-561, TC-565, TC-537).

| Id | Pilot | Fact | Disposition |
|---|---|---|---|
| RD3-U01 | cells/cpp | Upstream does not build with GCC 16.2 (`-Werror`, trigraph in NumberFormat.cpp, missing `<limits>`). | BLOCKED_BY_UPSTREAM, disclose |
| RD3-U02 | cells/rust | `Workbook::load_xlsx` maps every failure (corrupt, missing, openpyxl-made file) to `Unsupported("xlsx load")`; lead: `apply_number_format` on a formula cell reads back General. | upstream, disclose |
| RD3-U03 | pdf/cpp | `TextAbsorber` fails on `/Filter` arrays (common reportlab output); the MCP's own encrypt-then-open example pair produces unreadable text (`flate: bad zlib header check`). | upstream, disclose |
| RD3-U04 | 3d/net, 3d/python, 3d/java | Format save paths throw NotImplemented or write empty files; only STL via `Save(path)` works; OBJ export bug in 3d/python. | upstream, covered by RD3-060/140 |
| RD3-U05 | slides/java, slides/cpp, words/net | Unsupported export formats are silent (PPTX named .pdf) or throw (`NotSupportedException("FOSS")`, 0-byte files). | upstream, covered by RD3-060 |
| RD3-U06 | imaging/net | TIFF bit depth read as 134; truncated PNG returns Height 0. README install names a package that is not on NuGet. | upstream |
| RD3-U07 | email/cpp | Library-written .msg rejected by `extract_msg` (lead; Outlook not tested). | upstream lead |
| RD3-U08 | tex/python | 35 of 45 files unparseable upstream, library not importable; no upstream fix at HEAD. | BLOCKED_BY_UPSTREAM, RD3-092 |
| RD3-U09 | pdf/go, cells/go | Index is at an older commit than `go get` resolves (snapshot age). | covered by RD3-071 |
| RD3-U10 | cluster | The cluster runs `rev-e4a6ada` (27), `rev-ae713e4` (4), `rev-tex-a06079c` (1), not the shipped image; stale namespaces poc14b and poc14c; serving pods in the dev cluster have 443 egress; image tags without digests, one replica, no PDB; RollingUpdate on a single-writer claim. | not part of the release (docs/RELEASE.md section 9: the cluster is not the release); disclose |
| RD3-U11 | all | Example search is keyword-only (no embedding gateway in the image); natural-language class discovery is weak (L-121); no member documentation (TC-526/527/528 cut). | accepted limitations, keep |
| RD3-160 | delivery | Nothing is delivered yet; OWNER-13 (the registry host and the operator's own `docker login`) is the only remaining blocker, and SUP-7's delivery runbook is rehearsed and ready. | BLOCKED_EXTERNAL (OWNER-13); disclosed in docs/RELEASE.md section 4, Hosting; does not block gate G3 per AGENTS.md's blocking taxonomy |
| RD3-162 | process | Seven owner items were open, not one; SUP-2 re-verified all seven (OWNER-08/12 SATISFIED, OWNER-14 confirmed still rejected and handled via the OQ-015 sidecar carry-forward, OWNER-07/10/11 re-read and unchanged, OWNER-13 corrected). | disclosed; see docs/DECISION_LOG.md 2026-10-22 and ops/owner_items.yaml |
| RD3-163 | process | Card-authoring defects were recurring (34 rework and 11 supersede instructions since 10-12). SUP-1 built ops/cardpreflight.py and `gatectl preflight` to catch this class of defect before dispatch. | disclosed; ops/cardpreflight.py, commits b415fb97 and 49331f28 |
| RD3-166 | process | main was pushed to GitHub on 2026-10-17 before G3 was accepted, on the operator's explicit instruction; the pushed head passed full local CI and both remote workflows (CI, Mirror to GitLab). | disclosed as a recorded exception; see docs/DECISION_LOG.md (2026-10-17, 2026-10-19 and 2026-10-22 entries) and evidence/release/push-log.jsonl |
| RD3-167 | process | Commit gaps of 9.9 to 21.4 hours had no reason recorded anywhere in the repository. SUP-4 added evidence/liveness.jsonl, one line per supervisor wake or planned pause with the reason. | disclosed; evidence/liveness.jsonl, commit e2186f3d |
| RD3-168 | hygiene | 151 agent worktrees (13.5 GB) under a OneDrive-synced path, 74 local images and untracked evidence/build/G3/TC-498 had accumulated. SUP-5 removed the worktrees, pruned the images and the untracked evidence; the 314 local worker branches are a deliberate keep, the loop's own history. | disclosed; see docs/DECISION_LOG.md 2026-10-22 |
| RD3-171 | delivery | The internal GitLab project's container registry works and was verified read-only on 2026-10-19, but the operator stated on 2026-10-19 that it is NOT the delivery registry. | REJECTED as the delivery target by the operator; never pushed to; the real registry is still OWNER-13 |
| RD3-172 | publication | The repository is public: the internal GitLab mirror URL, the operator's commit email and this register of defects are already exposed by the 2026-10-17 push; no registry host or digest exists yet because nothing has been pushed to the real delivery registry. | disclosed; SUP-8's exposure review is recorded in docs/DECISION_LOG.md 2026-10-22; `gatectl validate`'s secret scan covers docs/ and evidence/ |
| RD3-155 | docs | docs/RELEASE.md sections 8 and 9 were stale in 7 of the 8 named places (L-071, L-080, L-097, L-100, L-102, L-107, L-128); this card (G3/TC-537) fixes those seven and commits docs/PREPROD_REVIEW.md including this register, as the finding requires. | disclosed; TC-397/402 (docs/RELEASE.md section 9, which this card's own rule keeps untouched) and promoting L-095/L-121/L-125 are explicit residual follow-up items this card does not touch, not silently closed |

## 5. Pilot verdicts at registration

| Verdict | Pilots |
|---|---|
| READY | cells/net |
| NOT_READY (11) | 3d/net (RD3-070, 050, 051, 052, 140), 3d/python (003, 004, 060), 3d/typescript (011), barcode/python (001, 002), cells/cpp (080, 022), cells/python (001), cells/rust (030, 031, 032), cells/typescript (010), email/python (001), note/python (001), page/python (001, 006) |
| BLOCKED_BY_UPSTREAM | tex/python (092) |
| READY_WITH_ACCEPTED_LIMITATION (19) | 3d/java, cells/go, cells/java, email/cpp, email/net, html/python, imaging/net, pdf/cpp, pdf/go, pdf/java, pdf/net, pdf/python, pdf/typescript, slides/cpp, slides/java, slides/net, slides/python, words/net, words/python |

A pilot moves to READY only after its cards are accepted and an independent QA pass on the rebuilt image finds none of its rows
reproducing.

## 6. Work plan, order and time estimate

Nothing below has been started. Tracks run in parallel; reviews are serial (one `gatectl review` at a time).

| Track | Cards in order | Why this order |
|---|---|---|
| A, extraction chain (shared extractor, chunk builder and fleet tests, so strictly serial) | TC-532 Python, TC-533 TypeScript, TC-534 C++, TC-535 Go and Rust, TC-557 Java, TC-558 .NET, TC-559 unsupported markers, then TC-566 | each card re-extracts its fixtures and repins the fleet tests |
| B, serving chain | TC-563 documentation excerpts, TC-562 retrieval, TC-564 serving and operations | share the tool and budget code |
| C, freshness and install chain | TC-560 freshness, TC-561 install and product reference (includes the tex warning) | share the product-reference and releases code |
| D | TC-565 example truth | independent files |
| E, release tooling (added 2026-10-19) | TC-568 claims ledger and generation diff report, TC-569 post-push verification; TC-570 ingestion toolchain pins after TC-564 (shared Dockerfile.ingestion) | independent scripts and tests; TC-570 chains behind TC-564 |
| Done | TC-567 (pdf/typescript e2e pin; accepted and pushed 2026-10-17) | local CI green |
| Supervisor items (not worker cards; they edit ops/, local hosts and the cluster) | SUP-1 to SUP-8 of section 6.1 | TC-537's closure test requires every SUP row to be marked DONE with its evidence reference |
| Closing (after A to E and the SUP items) | supervisor re-ingest of all 32 pilots, snapshot, TC-536 budgets and e2e pins, TC-537 closure documents, rebuild, gate 32 of 32, attack suite, independent QA round, release record, local CI | the data and image are rebuilt once, after the last fix |

Estimate (wall-clock, continuous operation; per card one worker 30 to 75 minutes plus one review 20 to 30 minutes; about 35% of
cards needed one rework round in this program; a rework adds 60 to 80 minutes):

| Item | Likely | Range |
|---|---|---|
| Track A, 8 cards in series (about 80 minutes each, 2 expected reworks) | 10.5 h | 8 to 15 h |
| Tracks B, C, D in parallel with A (reviews interleave into A's worker time) | absorbed | +0 to 2 h if reviews queue |
| Closing: re-ingest 32 pilots about 45 min, snapshot and apply 20 min, TC-536 and TC-537 about 1.5 h, rebuild, gate and attack suite 20 min, independent QA round 75 min, release record and full local CI 45 min | 3.5 h | 3 to 5 h |
| Track E (TC-568, TC-569, TC-570: three more reviews in the serial review queue, about 75 minutes of review time) | +1.5 h | +1 to 2.5 h |
| Supervisor items SUP-1 to SUP-8 (SUP-1 preflight 1.5 h and SUP-5 hygiene 40 minutes first, the rest while workers run, delivery runbook after the registry answer) | about 3 h, mostly parallel | 2 to 4 h |
| Total to a re-recorded, QA-verified image, ready for the operator's registry login | about 15.5 h | 12 to 24 h |
| After the operator supplies the registry host and logs in: push, pull-back verification (TC-569 tool), gate against the registry copy, release record, gate exit | about 1 h | 0.5 to 2 h |

Tiering if the operator wants a faster partial path (each tier still ends with the closing steps, about 3.5 h):
Tier 1, the ship blockers (rows P1 plus the drift and install rows of NOT_READY pilots): TC-532, TC-533, TC-535 (Rust and Go),
TC-558 and TC-557 (the 3d/net and Java correctness rows), TC-560, TC-561 about 9 h. Tier 2 adds TC-534, TC-559, TC-562, TC-563,
TC-564, TC-565 about 5 h more. Tier 3 is TC-566, TC-536, TC-537, documentation, about 3 h more (TC-566 is the permanent gate that
prevents recurrence and should not be dropped).

Assumptions that move the estimate: host load (other repositories' test runs slowed one review by 2x today), the intermittent
application-control policy (retry once), GitHub rate limits during re-extraction clones (the extraction needs the network),
and card defects found at dispatch (65% of past rework was a card defect: every card here is registered as a DRAFT and must have its
inputs, numbers and negative control finalised by the supervisor before dispatch, Supervisor rule 5).

## 6.2 Execution sequence (the order in which everything logged will be run; nothing has started)

Rules for the sequence: at most three workers run at once (the host is shared and OneDrive-backed); `gatectl review` runs one at a time; a card is
dispatched only when its dependencies are accepted and its draft has been finalised (min_tests per check, a real negative control, write_paths checked,
SUP-1 preflight green); the extraction cards are strictly serial because they share the extractor, the chunk builder and the fleet tests.
Estimates are wall-clock for the supervisor plus workers in continuous operation.

| Step | What | Depends on | Est. |
|---|---|---|---|
| **Phase 0, supervisor preparation** | | | about 1.5 h before the first dispatch, the rest overlaps phase 1 |
| 0.1 | SUP-5 hygiene, non-destructive first: untracked TC-498 evidence, integrated worktrees and branches (151 directories, 13.5 GB under OneDrive), stale local images, find and stop the `core.hooksPath` rewriter | none | 40 min |
| 0.2 | SUP-1 card preflight (collected tests vs min_tests per check, check paths, write_paths, negative-control dry run) and run it over every draft card | none | 1.5 h |
| 0.3 | Finalise the first-wave cards TC-563, TC-560, TC-532 (then TC-568, TC-569) | 0.2 for the preflight; can start by hand | 1 h |
| 0.4 | SUP-2 owner items re-verified (all but the GitHub token probe), SUP-3 push log, SUP-4 liveness record | none | 1 h, overlaps phase 1 |
| **Phase 1, first wave** | | | |
| 1.1 | Dispatch TC-563 (documentation excerpts), TC-560 (drift detection), TC-532 (Python extractor and stale Python fixtures) in parallel | 0.3 | worker 30 to 75 min each |
| 1.2 | Review and integrate TC-563, then dispatch TC-562 (retrieval) | 1.1 | 25 min review |
| 1.3 | Review and integrate TC-560, then dispatch TC-561 (install and product-reference truth, tex warning) | 1.1 | 25 min review |
| 1.4 | Review and integrate TC-532, then dispatch TC-533; fill free worker slots with TC-568 (claims ledger and generation diff) and TC-569 (post-push verification), each reviewed when ready | 1.1 | review about 30 min, rework expected |
| **Phase 2, serving, install, examples (alongside phase 3)** | | | |
| 2.1 | TC-562 retrieval relevance | TC-563 | 1 h worker plus review |
| 2.2 | TC-561 install guidance, product-reference truth, tex not-importable warning | TC-560 | 1 h plus review |
| 2.3 | TC-564 serving robustness, observability, shipped and hardened rollback tool | TC-560, 562, 563 | 1.2 h plus review |
| 2.4 | TC-565 example truth (run-based verification, labels, working 3d examples) | TC-561, 562 | 1 h plus review |
| 2.5 | TC-570 pin the ingestion toolchain | TC-564 | 45 min plus review |
| **Phase 3, extraction chain, strictly serial** | | | about 10.5 h in total with expected rework |
| 3.1 | TC-533 TypeScript (cells, 3d, pdf) | TC-532 | 80 min each step: finalise, worker, review, rework, integrate |
| 3.2 | TC-534 C++ | TC-533 | |
| 3.3 | TC-535 Go and Rust | TC-534 | |
| 3.4 | TC-557 Java | TC-535 | |
| 3.5 | TC-558 .NET | TC-557 | |
| 3.6 | TC-559 unsupported and stub markers | TC-558 | |
| 3.7 | TC-566 permanent independent fidelity gate and the stale and environment test repairs | TC-558, TC-567 (done) | |
| **Phase 4, closing, supervisor** (starts when phases 1 to 3 are all accepted) | | | about 3.5 h |
| 4.1 | OQ-015: probe the GitHub token (status code only; OWNER-14); if invalid, plan to carry forward the last good sidecars | phase 3 done | 10 min |
| 4.2 | Build ingestion and serving images at HEAD and import them into the kind node | 4.1 | 10 min |
| 4.3 | Re-ingest all 32 pilots (acceptRegression only where reasoned), prove, snapshot, apply, compact; review the TC-568 generation diff against the pre-fix tree | 4.2 | 60 min |
| 4.4 | TC-536 pins, budgets, stale entries, e2e pins, expired exceptions (worker, review) | 4.3 | 1 h |
| 4.5 | Rebuild the demo image from final HEAD; gate 32 of 32; attack suite; commit the TC-568 claims ledger | 4.4 | 30 min |
| 4.6 | Independent QA round on the rebuilt image: fresh agents, the 11 NOT_READY pilots and tex/python fully, the TC-566 differential verifier on all 32, agentic workflows | 4.5 | 75 min |
| 4.7 | TC-537 closure (register statuses, RELEASE.md, PREPROD_REVIEW.md, closure test); SUP-6 claim corrections; SUP-8 exposure review; resolve OQ-012 to OQ-015 | 4.6 | 1 h |
| 4.8 | `gatectl release-record` (LOCAL_ONLY), full local `scripts/ci_check.sh` green, push main with the push-log row (SUP-3) | 4.7 | 45 min |
| 4.9 | SUP-7 rehearsal of push, pull-back by digest, gate and release-record against a throw-away local `registry:2` container | 4.8 | 30 min |
| **Phase 5, delivery (needs the operator)** | | | about 1 h |
| 5.1 | The operator shares the delivery registry's host, path and access and runs `docker login` | phase 4 done | operator |
| 5.2 | `push_image.py --dry-run`, then the push, capturing the digest | 5.1 | 15 min |
| 5.3 | TC-569 pull-back by digest and re-gate against the registry copy | 5.2 | 20 min |
| 5.4 | `gatectl release-record --push-result`, `gatectl gate-exit G3`, push-log row, final push of main | 5.3 | 15 min |

Totals: about 15.5 h (12 to 24) to the end of phase 4, plus about 1 h for phase 5 once the operator supplies access.
Tier cut if time forces one (each tier still runs phase 4): tier 1 = TC-532, 533, 535, 557, 558, 560, 561 (about 9 h); tier 2 adds TC-534, 559, 562, 563, 564, 565 (about 5 h); tier 3 adds TC-566, 568, 569, 570 and the closure documents (about 3 h). TC-566 is the permanent gate that prevents recurrence and should not be dropped.

## 6.1 Supervisor work items (not dispatched to workers: they edit ops/, hosts and the cluster)

Status: see the Status column added to each row by this card's closure (G3/TC-537); rows this card could not close with an accepted card id are DISCLOSED or REJECTED in section 4 instead. TC-537's closure test requires each row marked DONE with an evidence reference
(a commit id or a file under evidence/release/).

| Id | Rows | Item | Status | Verification when done |
|---|---|---|---|---|
| SUP-1 | RD3-163 | Card preflight in ops (cardlint): run `pytest --collect-only` for each check and compare the collected count with `min_tests`; every path named by a check command exists at the issue revision or is in `write_paths`; every `write_paths` entry exists or is a new test or script; a negative-control `mutate` is applied to a scratch copy and must change a file; warn when a check includes tests that need the network or build real native code without `network: true`; surface the ruff format/check requirement in the dispatch text. Also run it over all draft cards now (TC-532..537, 557..570) before the first dispatch. | DONE (ops/cardpreflight.py, gatectl preflight, commits b415fb97 and 49331f28) | `gatectl validate` fails on a card with a wrong min_tests or a missing check file; unit tests in ops/tests |
| SUP-2 | RD3-162 | Re-verify the seven OPEN owner items (07, 08, 10, 11, 12, 13, 14): update status, last_checked_at and note from current evidence; OWNER-12 and OWNER-10 are probably satisfied; confirm OWNER-14 (GitHub token) with a status-code-only probe BEFORE the closing re-ingest (never print the token). With an invalid token the closing re-ingest carries forward the last good sidecars (OQ-015). | DONE (OWNER-08 and OWNER-12 settled in commit e2186f3d; OWNER-07, 10, 11 re-read and unchanged; OWNER-13 corrected; OWNER-14 token probe ran during the TC-536 closing re-ingest, commit ad8c2329: token rejected HTTP 401, sidecars carried forward per OQ-015, resolved as RESOLVED in ops/open_questions.jsonl 2026-10-22) | ops/owner_items.yaml diff with evidence per item |
| SUP-3 | RD3-166 | Record the operator-authorized push before gate acceptance (decision log, done 2026-10-19) and add a push log: after every push, record head sha, the CI and Mirror-to-GitLab results (`gh run list`) in evidence/release/push-log.jsonl. | DONE (evidence/release/push-log.jsonl in commit e2186f3d) | push-log.jsonl has a row for 0e6eadf4 with both workflows success |
| SUP-4 | RD3-167 | Liveness record: a committed evidence/liveness.jsonl (one line per supervisor wake or planned pause with the reason) and a decision-log note naming the known gaps as unexplained; no reason is invented. | DONE (evidence/liveness.jsonl in commit e2186f3d) | file present; the 10-18 and 10-19 idle periods recorded as 'waiting for operator' |
| SUP-5 | RD3-168, RD3-170 | Hygiene, non-destructive first: list, then remove only what is safe: untracked evidence/build/G3/TC-498; agent worktrees whose branch commits are integrated or abandoned (151 directories, 13.5 GB, under a OneDrive path) with `git worktree prune`; worker branches that are integrated; local docker images except the release candidate and the final; cluster namespaces poc14b and poc14c (read-only check first, operator approval before deleting cluster objects). Find what rewrites `core.hooksPath` to an absolute path (IDE git integration, GitHub Desktop or a script) and stop it. | DONE (151 stale worktrees removed (13.5 GB), 67 stale docker images and build cache pruned, untracked TC-498 evidence deleted; the hooksPath rewriter is the agent-worktree creation (the Claude Code harness sets core.hooksPath to an absolute path whenever it creates a worker worktree), fixed permanently by TC-566 accepting both forms; the 314 local worker branches are a deliberate decision, not unfinished cleanup - see docs/DECISION_LOG.md 2026-10-22 - kept intentionally as the loop's own history, costing disk, not correctness) | `git worktree list` count, disk freed, `git status` clean, hooksPath stays `.githooks` after a commit |
| SUP-6 | RD3-164 | Claims audit: append correction notes (no history rewrite) to docs/DECISION_LOG.md for every quoted number that TC-568's ledger does not reproduce (17,000 methods / 0 mismatches, 183 verified examples, 4,445 types, 40 vs 32 pilots in old entries). | DONE (docs/DECISION_LOG.md 2026-10-22 entry, corrections measured fresh by `scripts/release/claims_ledger.py` against the final re-ingested tree, commit ad8c2329: 159,144 members, 918 functions; 164 served example chunks, 273 cache entries (197 verified); pdf/typescript 541 symbols (448 types) at its single re-pinned commit) | the 2026-10-22 decision-log entry cites the fresh ledger run's own numbers |
| SUP-7 | RD3-160, RD3-171 | Delivery runbook for OWNER-13: the operator shares the delivery registry's host, path and access after all other work is done (the GitLab registry that was verified is NOT the target); first REHEARSE the whole path against a throw-away local `registry:2` container (push, TC-569 pull-back by digest, gate, release-record) so the real delivery has no first-time step; then, once the operator has run `docker login` for the real registry: `push_image.py --dry-run`; push; TC-569 pull-back verification; `gatectl release-record --push-result`; `gatectl gate-exit G3`; resolve OQ-012..015; record the push-log row. Nothing is pushed before the final image passes the closing steps. | DONE for everything a supervisor or worker can do without the operator: rehearsal against a throw-away registry:2 done 2026-10-19 (push, pull by digest, inspect, manifest, image id, revision all pass; runbook in docs/RELEASE.md section 4, Delivery runbook). OQ-012..015 resolved 2026-10-22. The one remaining action is OWNER-13 itself (operator shares the real registry and runs docker login); per AGENTS.md's blocking taxonomy an owner item blocks only the step that names it, never gate G3 - the real push happens after G3 acceptance, not before it. See docs/DECISION_LOG.md 2026-10-22. | release record state PUSHED; gate-exit reports no problems |
| SUP-8 | RD3-172 | Public-repository exposure review before the delivery push: internal GitLab URL, commit-author email, registry host and digest, the ROUND3 register; confirm the secret scan covers docs/ and evidence/. | DONE (docs/DECISION_LOG.md 2026-10-22 entry: GitLab URL and operator email already public via the 2026-10-17 push; no real registry host/digest exists yet (still OWNER-13); `gatectl validate`'s secret scan confirmed to cover docs/ and evidence/) | written review entry in the decision log |

## 7. Resume instructions

1. `python ops/gatectl.py validate`, then `python ops/gatectl.py next` names the lowest READY card (TC-532 until the chain starts).
2. Before dispatching any card: re-read its card text, replace its placeholder negative control with a real falsifier, calibrate
   `min_tests` (it applies to EACH check), and check its `write_paths` against the files it must touch (a card that needs a file outside
   them is a card defect).
3. Dispatch per AGENTS.md (one fresh worker per card in a worktree, FOSS_MCP_WORKER=<card> for its commit); review one at a time; kill
   leftover review processes after each receipt; `gatectl integrate`.
4. After the last card of tracks A to D: re-ingest, snapshot, TC-536, TC-537, rebuild `demo-<sha>`, gate, attack suite, QA round,
   `gatectl release-record`, local `scripts/ci_check.sh` green, then ask the operator for the registry host and their own `docker login`.
5. Local CI at registration (HEAD 8e3a3de1): lint, format, mypy and `gatectl validate` pass; pytest has exactly three items:
   the stale pdf/typescript e2e pin (RD3-151, TC-536), `core.hooksPath` (RD3-152, TC-566) and the intermittent Rust build error (RD3-153).
