# foss-mcp round-2 deep audit — handoff to fixing agent

Server audited: public tunnel to `foss-mcp-serving:demo-ce89954`. 32 pilots, each audited solo by its own agent against a 10-dimension protocol (`PROTOCOL.md` in this folder), with a full time/context budget per pilot — not split across agents. Every finding below is reproducible: each issue carries exact repro commands and an evidence file under its pilot's folder.

**This is round 2.** Round 1 (`...\scratchpad\portfolio\`) was a lighter pass with real-world scenarios; round 2 re-verified every round-1 finding and went far deeper (ground-truth symbol diffs, example execution, adversarial honesty probes, cross-pilot comparison, safety scans).

## Headline numbers

- **285 issues** across 32 pilots: **29 P0, 94 P1, 100 P2, 62 P3**.
- **No pilot scored above B+.** Rating distribution: B+ ×1, B ×6, B- ×7, C+ ×8, C ×5, D ×5. Full table below.
- Zero pilots are unverified this round — all 8 source-only pilots that round 1 left `BLOCKED_ENV` (tex/python, barcode/python, html/python, imaging/net, plus 4 C++ pilots) were built and run this round under explicit user authorization.

## How to use this handoff

- **`ISSUES.json`** (this folder): all 285 issues, one schema, machine-readable. Each entry has `id`, `pilot`, `severity`, `category`, `tools`, `repro`, `expected`, `actual`, `evidence`, `suggested_fix`, `probable_owner`, `confidence`.
- **Per-pilot folders** (`<family>-<platform>/`): `report.md` (narrative, test matrix, versions compared), `issues.json` (that pilot's subset), `evidence/` (raw saved MCP responses), `work/` (test projects/scenarios, independently re-runnable).
- Start with the **cross-cutting fixes** below — several of these each resolve 10-40 issues across many pilots in one change, rather than fixing 32 pilots one at a time.

## Cross-cutting fixes (highest leverage first)

### 1. `find_examples` is broken almost everywhere — fix the matcher, not the index (49 issues, `example_curator`)
Across nearly all 32 pilots, `find_examples` matches on example *title* words only, not body/code content. Typical finding: an example whose code literally contains `mapi_message`, `watermark`, `formula`, `Workbook`, `TextFragment` etc. is unreachable by that exact word. Pilots with especially thin corpora (2-4 examples total) make this worse: cells/java and slides/cpp return **zero** hits for every query tried, while sibling platforms of the same product do return hits for the same concepts (confirmed via T9 cross-pilot checks on cells/*, slides/*, pdf/*). This is the single most reported defect in the whole audit — fixing the matcher to index body text (not just titles) is likely the highest-value change available.

### 2. The self-reported "API-Coverage: N of N types (100%)" metric is not trustworthy (21 issues, `indexer`)
This number is **always exactly 100%** by construction in every pilot checked — it counts whatever got indexed and calls that the total, so it can never report less than 100%. Concretely this produces:
- **Phantom symbols** (3d/java: 37 of 74 plausible-but-nonexistent names resolve as confident real symbols; claimed 266 types vs. 195 real; 3d/net has a similarly inflated count against a different commit).
- **Duplicate indexing inflating the count** (slides/python: every class double-indexed under two FQNs, ~2x the real count; email/python: `MutableMapiAttachment` indexed as distinct from its identical twin `MapiAttachment`).
- **Undercounting real breakage** (tex/python: claims "11 of 45 files could not be parsed"; real figure via `ast.parse` is 35 of 45, over 3x worse).
- **Wrong vs. independently-verified ground truth** (pdf/go: claims 285/285; `go doc -all` on the exact pinned commit finds 234; pdf/net, pdf/python show the identical always-100% shape).
Recommend: compute coverage against an independent ground-truth enumeration (the real package/headers), not against the indexer's own output, and stop reporting a tautological 100%.

### 3. Product-reference sections (`formats`/`limitations`/`install`) are the least reliable content layer (46 issues, `product_reference_builder`)
Repeated patterns: sections truncated mid-sentence (pdf/java, pdf/cpp, cells/go, slides/cpp, slides/java...); sections empty for one platform while every sibling has real content from an equivalent README section (cells/cpp's `formats`/`limitations` are empty despite the README having the exact matching "Scope and Limitations" heading that `cells/python`/`cells/net` extract fine — proven as an extraction bug, not missing source; pdf/typescript's `formats`/`limitations` are empty because its README states formats only inside a Mermaid diagram); sections self-contradicting (pdf/cpp's `install` says FetchContent "does not work" then its own trailing notice recommends FetchContent); sections asserting **specific, falsifiable, false claims** about the installed package (3d/net claims PLY/RVM/AMF/HTML5 export "work" — all four throw or write empty files; slides/java claims SaveFormat enforcement and slide-registration are "confirmed by reading the XML" — neither holds on the published jar). Recommend auditing the extraction pipeline per-platform (it clearly works for some sibling platforms of the same product but not others) and adding automated fact-checking against the indexed symbols/examples before publishing a reference-section claim.

### 4. Silent-failure upstream library bugs that the MCP presents with no warning (45 issues, `upstream-library-bug`, but the fix is MCP-side)
These are defects in the Aspose libraries themselves, not fabrications by the MCP — but the MCP's job is to warn a coding agent before it hits them, and currently doesn't:
- **Export/save silently writes the wrong thing, no exception:** cells/java, cells/net both silently write a full XLSX ZIP to a `.pdf`/`.csv`-named file; slides/java's `Presentation.save()` ignores the `SaveFormat` argument entirely (9 formats tested, all byte-identical PPTX); 3d/java has **0 of 37** documented export formats produce a usable file; 3d/net's glTF writer emits output its own reader rejects; cells/go silently turns numeric cells into strings on reload.
- **Security-relevant:** pdf/cpp's AES-256 encryption produces a file that cannot be decrypted by Aspose's own reader or by `pypdf`+`cryptography` — a crypto feature that doesn't actually protect or preserve content. imaging/net's exact indexed commit has 3 known DoS bugs (confirmed via the GitHub compare API) fixed 5 commits later upstream — the MCP tells users to build the vulnerable commit.
- **Recommendation:** add a lightweight "known upstream defect" annotation surfaced through `limitations`/`get_symbol` for the specific APIs involved, independent of whether/when each gets fixed upstream.

### 5. `search_symbols`/`get_symbol` fragment matching is inconsistently promised vs. delivered (15 issues, `mcp-ranking`, mostly `indexer`)
Tool descriptions promise "type name or fragment" matching; in practice short prefixes miss real types unpredictably across many pilots (pdf/typescript: `Doc` misses `Document` entirely; 3d/net and 3d/java: `Sc`/`Vec`/`Matri` all miss; slides/net: `Colo` misses but `Col` hits). This is confusing specifically because it's inconsistent, not because fragment search is absent.

## Family-level finding: the entire `3d` product line is the worst performer

All 4 `3d/*` pilots tested (net, java, python, typescript) rated **D, D, C, C+** — the four lowest/near-lowest scores of the whole portfolio. Every platform has broken or entirely non-functional export for most of its documented formats (3d/java: 0 of 37; 3d/net: PLY/RVM/AMF/HTML5 all fail; 3d/python: OBJ crashes, glTF writes a malformed file), and both net and java separately show symbol-index contamination (phantom types/inflated counts). This did not happen on any other product family to this degree — recommend treating `3d` as a dedicated remediation priority rather than folding it into the general backlog.

## All 29 P0 issues

| ID | Pilot | Category | Summary |
|---|---|---|---|
| 3D-JAVA-001 | 3d/java | coverage-gap | ~50% of sampled nonexistent type names resolve as confident real symbols |
| 3D-JAVA-002 | 3d/java | upstream-library-bug | STL export — the one format called "fully functional" — discards geometry |
| 3D-JAVA-003 | 3d/java | upstream-library-bug | STLASCII writes byte-identical binary output to STL_BINARY |
| 3D-JAVA-004 | 3d/java | upstream-library-bug | 0 of ~37 documented export formats produce a usable file |
| 3D-NET-001 | 3d/net | reference-section | formats/limitations falsely claim PLY/RVM/AMF/HTML5 export work |
| 3D-NET-002 | 3d/net | example-broken | The only "additional examples" hit does not compile |
| 3D-NET-003 | 3d/net | upstream-library-bug | FBX export silently loses 100% of scene content, no exception |
| 3D-NET-004 | 3d/net | upstream-library-bug | glTF/GLB writer emits spec-invalid output its own reader rejects |
| 3D-PYTHON-001 | 3d/python | upstream-library-bug | OBJ export crashes even via the MCP's own recommended import path |
| 3D-PYTHON-002 | 3d/python | upstream-library-bug | glTF export writes a mesh name as an integer, producing invalid glTF |
| CELLS-CPP-001 | cells/cpp | honesty | FAQ fabricates CSV load support that does not exist |
| CELLS-GO-001 | cells/go | upstream-library-bug | Numeric cells silently become strings after Save+Load |
| CELLS-JAVA-001 | cells/java | upstream-library-bug | Workbook.save() silently writes XLSX bytes to .pdf/.csv files |
| CELLS-NET-002 | cells/net | upstream-library-bug | Workbook.Save() with wrong extension silently mislabels XLSX |
| CELLS-PY-004 | cells/python | mcp-content-missing | FormulaEvaluator silently returns None for SUM/AVERAGE, undocumented |
| CELLS-PY-005 | cells/python | upstream-library-bug | Python datetime silently loses date-ness across save/reload |
| EMAIL-CPP-001 | email/cpp | upstream-library-bug | Embedded message saved as EML is a mislabeled CFB blob, violates RFC2045 |
| HTML-PY-001 | html/python | mcp-content-wrong | Limitations says form validation unimplemented; it works correctly |
| IMAGING-NET-001 | imaging/net | upstream-library-bug | DetectFormat throws on a non-seekable stream, contradicting its own README |
| IMAGING-NET-002 | imaging/net | upstream-library-bug | Multiple format handlers fabricate dimensions on truncated input |
| PDF-CPP-001 | pdf/cpp | upstream-library-bug | AES-256 encrypt produces a stream no decoder (incl. the library itself) can read |
| PDF-JAVA-001 | pdf/java | upstream-library-bug | WatermarkArtifact silently produces no watermark, no error |
| PDF-JAVA-002 | pdf/java | upstream-library-bug | TextStamp silently dropped in same-session saves |
| SLIDES-JAVA-001 | slides/java | mcp-content-wrong | Presentation.save() ignores SaveFormat entirely on the published jar |
| SLIDES-JAVA-002 | slides/java | mcp-content-wrong | agent_guidance's recommended slide-add pattern produces orphaned parts |
| SLIDES-PY-001 | slides/python | upstream-library-bug | add_clone() crashes with RecursionError when the slide has notes |
| SLIDES-PY-002 | slides/python | mcp-content-wrong | formats/limitations assert 7 SaveFormat values work; 5 are broken in the installed version |
| TEX-PYTHON-001 | tex/python | upstream-library-bug | Package cannot be imported at all — 78% of source files fail to parse |
| TEX-PYTHON-002 | tex/python | mcp-content-wrong | MCP's own parse-failure count understates real breakage 3x |

## Per-pilot ratings and issue counts

| Pilot | Rating | P0 | P1 | P2 | P3 | Total |
|---|---|---|---|---|---|---|
| 3d/java | D | 4 | 3 | 2 | 1 | 10 |
| 3d/net | D | 4 | 3 | 5 | 3 | 15 |
| 3d/python | C | 2 | 4 | 4 | 1 | 11 |
| 3d/typescript | C+ | 0 | 4 | 4 | 1 | 9 |
| barcode/python | B- | 0 | 1 | 1 | 4 | 6 |
| cells/cpp | C+ | 1 | 4 | 1 | 3 | 9 |
| cells/go | C | 1 | 3 | 5 | 2 | 11 |
| cells/java | B | 1 | 1 | 1 | 1 | 4 |
| cells/net | B- | 1 | 2 | 2 | 2 | 7 |
| cells/python | C+ | 2 | 4 | 2 | 3 | 11 |
| cells/rust | B | 0 | 3 | 5 | 3 | 11 |
| cells/typescript | D | 0 | 6 | 5 | 1 | 12 |
| email/cpp | C+ | 1 | 2 | 3 | 2 | 8 |
| email/net | B | 0 | 2 | 1 | 3 | 6 |
| email/python | B- | 0 | 3 | 4 | 0 | 7 |
| html/python | B- | 1 | 3 | 3 | 1 | 8 |
| imaging/net | C | 2 | 3 | 4 | 3 | 12 |
| note/python | B- | 0 | 3 | 4 | 2 | 9 |
| page/python | B | 0 | 4 | 2 | 3 | 9 |
| pdf/cpp | C+ | 1 | 4 | 5 | 2 | 12 |
| pdf/go | B- | 0 | 3 | 2 | 2 | 7 |
| pdf/java | C+ | 2 | 1 | 2 | 2 | 7 |
| pdf/net | B | 0 | 3 | 4 | 1 | 8 |
| pdf/python | C | 0 | 3 | 4 | 0 | 7 |
| pdf/typescript | B- | 0 | 4 | 4 | 2 | 10 |
| slides/cpp | B | 0 | 3 | 3 | 2 | 8 |
| slides/java | D | 2 | 2 | 4 | 1 | 9 |
| slides/net | B+ | 0 | 1 | 4 | 3 | 8 |
| slides/python | C | 2 | 2 | 1 | 1 | 6 |
| tex/python | D | 2 | 2 | 2 | 1 | 7 |
| words/net | C+ | 0 | 3 | 4 | 3 | 10 |
| words/python | C+ | 0 | 5 | 3 | 3 | 11 |
| **Total** | | **29** | **94** | **100** | **62** | **285** |

## Decisions the fixing team should make (not resolved here, by design)

- **Upstream vs. MCP-side fix**: every `upstream-library-bug` issue (61 total) is a defect in the Aspose library itself, not the MCP's content. For each, the audits recommend either filing it upstream, adding an MCP-side warning, or both — flagged per-issue in `ISSUES.json`'s `suggested_fix`, not decided by the audits.
- **Version drift policy**: several pilots index a commit ahead of (slides/java: 26.8.0 indexed vs 26.7.0 published) or behind (pdf/go: 57 commits behind) what's actually installable, and this measurably changes which claims are true. Needs a project-level policy (track registry releases vs. track upstream HEAD), not a per-pilot fix.
- **cells/typescript (D) and 3d family (D/D/C/C+)**: candidates for prioritized remediation given they're the lowest scores and cells/typescript's formulas being silently non-functional and write-only is a correctness trap, not just a documentation gap.

## What this audit did not cover

- Transport-level fuzzing (malformed JSON-RPC envelopes) — out of scope, tool-call-level testing only.
- Exhaustive type-by-type diffing (each pilot sampled 14-40 symbols against ground truth, not its full surface) — sampling found zero signature-level defects in nearly every pilot, so the sampling approach is validated, but a full sweep could surface more.
- Load/stress testing beyond a 20-call politeness cap per pilot.
- Any action on `aspose-mcp-devcontext`, `aspose.org`, `repository-presenter` (out of scope reference repos) or the live GitLab/registry/production deployment.
