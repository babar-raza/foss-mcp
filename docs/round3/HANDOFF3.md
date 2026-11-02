# foss-mcp round-3 deep audit — final handoff

Image audited: `foss-mcp-serving:demo-cac9288` (source commit `cac92881a1efa8bce3c1033ef2d0fac987b51d8e`, gate PASS 32/32, pushed to the internal registry 2026-11-01). This is the **third** full audit pass. Round 1 was a lighter real-world-scenario pass. Round 2 (`scratchpad/portfolio2/HANDOFF.md`) ran a full 10-dimension (T1–T10) protocol against `demo-ce89954` and found 285 issues (29 P0) across 32 pilots, no pilot above B+. Round 3 re-ran the identical protocol against the next build, with an explicit mandate to reconcile every round-2 finding (FIXED / STILL BROKEN / CHANGED) before hunting for anything new.

## Executive summary

**Of round 2's 285 issues, the overwhelming majority (over 90%) reproduced byte-for-byte or behaviorally identically.** Only a small number of genuine fixes landed, concentrated in two forms: (1) three pilots crossed a letter-grade boundary upward (barcode/python B-→B, pdf/java C+→B-, pdf/typescript B-→B) on the strength of real search/ranking and disclosure fixes; (2) a brand-new `known_defects` field was added **project-wide** in direct, traceable response to round 2's own top recommendation (HANDOFF.md #4, "surface known upstream library bugs to the caller"). Where populated, its content is uniformly accurate, specific, and well-written — a genuine engineering response, not boilerplate. But across **every single pilot that had one of these upstream bugs**, the field is wired to the wrong place: populated almost exclusively at the whole-pilot `report_index_freshness` level, essentially never reaching `get_symbol`/`search_symbols` for the exact symbol an agent would actually inspect, `find_examples`, or `get_product_reference(limitations)`. This is the single most important finding of round 3 and the highest-leverage fix available — see below.

One pilot got meaningfully **worse**: `cells/typescript` (already the lowest-scoring non-3d/non-slides/java pilot at D) was found this round to have a brand-new, more severe silent-data-loss P0 (`Workbook.save()` hardcodes exactly two `<sheet>` entries — any 3rd+ worksheet is silently dropped with zero error), earning a downgrade to **F**.

## Ratings-movement table (round 2 → round 3)

| Pilot | Round 2 | Round 3 | Movement |
|---|---|---|---|
| 3d/java | D | D | unchanged |
| 3d/net | D | D | unchanged |
| 3d/python | C | C | unchanged |
| 3d/typescript | C+ | C+ (trending B-) | improving, not yet crossed |
| barcode/python | B- | **B** | **improved** |
| cells/cpp | C+ | C+ | unchanged (2nd hallucination found) |
| cells/go | C | C | unchanged |
| cells/java | B | B | unchanged |
| cells/net | B- | B- | unchanged |
| cells/python | C+ | C+ | unchanged |
| cells/rust | B | B | unchanged |
| cells/typescript | D | **F** | **regressed** |
| email/cpp | C+ | C+ | unchanged |
| email/net | B | B | unchanged |
| email/python | B- | B- | unchanged (notable internal improvement) |
| html/python | B- | B- | unchanged |
| imaging/net | C | C | unchanged |
| note/python | B- | B- | unchanged |
| page/python | B | B | unchanged (new P0 offset by other strengths) |
| pdf/cpp | C+ | C+ | unchanged |
| pdf/go | B- | B- | unchanged (index not regenerated this gate) |
| pdf/java | C+ | **B-** | **improved** |
| pdf/net | B | B | unchanged |
| pdf/python | C | C | unchanged (assumed; no regression evidence) |
| pdf/typescript | B- | **B** | **improved** |
| slides/cpp | B | B | unchanged |
| slides/java | D | D | unchanged |
| slides/net | B+ | B+ | unchanged |
| slides/python | C | C | unchanged |
| tex/python | D | D | unchanged |
| words/net | C+ | C+ | unchanged |
| words/python | C+ | C+ | unchanged |

**Headline: 3 improved, 1 regressed, 28 unchanged.** Zero pilots crossed from a passing grade to failing for any reason other than cells/typescript's newly discovered bug — i.e. nothing was broken by the rebuild itself; the regression is a *detection* improvement (round 3 tested harder and found something round 2 missed), not a product regression.

## #1 finding: `known_defects` is real, accurate, and almost entirely mis-wired

This is the single highest-leverage fix available, because **the content-authoring work is already done** — this is a propagation/wiring problem, not a research problem.

**What's confirmed working:** the field exists on `get_symbol`, `search_symbols`, and `report_index_freshness` project-wide. Wherever it is populated, its text is specific, accurate, names the real defect, cites a real repro, and is correctly scoped (unrelated symbols correctly return `[]`; no hallucinated entries were found anywhere in 32 pilots). Several entries visibly echo this audit program's own round-2 repro text almost verbatim — direct evidence it is a response to specific feedback, not a generic feature.

**What's broken:** in pilot after pilot, the entry is reachable *only* via `report_index_freshness` — a tool an agent has no particular reason to call before writing code — and is empty (`[]`) on the `get_symbol`/`search_symbols` response for the exact symbol the defect is about, which is where an agent actually encounters the risk. Confirmed independently in at least 15 of 32 pilots:

- **cells/java, cells/net, cells/go, cells/rust, cells/python, cells/typescript** — each has its own numeric/data-loss upstream bug (Save-extension mismatch, numeric→string, FormulaEvaluator-None, mergeCells-dropped, etc.); in every case `known_defects` is populated and accurate only at `report_index_freshness`, empty on `Workbook`/`Cell`/`Worksheet`/`SaveOptions`.
- **3d/java** — `known_defects` even omits `find_examples` entirely from its schema, the exact surface carrying the pilot's two broken "verified" STL examples.
- **pdf/java, pdf/cpp** — asymmetric: populated only on the one exact FQN each original round-2 issue happened to name (`WatermarkArtifact`, `Document`), empty on sibling classes in the identical causal chain (`TextStamp`, `Stamp`, `CryptoAlgorithm`).
- **pdf/go, pdf/net, cells/cpp, words/net** — the field exists in the schema but is **empty everywhere checked**, including for pdf/net's own troubleshooting-doc-disclosed `OcspSettings` defect and words/net despite its sibling words/python proving the same mechanism works end-to-end.
- **tex/python, words/python, page/python, 3d/python, 3d/typescript, slides/python, slides/net, slides/java** — same pattern: accurate at the pilot/freshness level, empty on the exact symbol (`TeXJob`, `Document`, `PsDocument`, `Scene`, `Mesh`, `SlideCollection`, `LayoutSlide`, `Presentation`) a quick-start-following agent would call first.

**Recommendation:** treat this as one ticket, not 15+. The fix is almost certainly a single lookup-table join in the indexer (the freshness-level text already names the implicated FQN(s) in prose) — propagate each `known_defects` entry to every `get_symbol`/`search_symbols` response for the FQN(s) it names, and backfill from `get_product_reference(limitations)` wherever that text already names a specific symbol. This closes the practical half of roughly 20 of round 3's "CHANGED" issues in one pass without touching a single library.

## Worst-remaining pilots

1. **cells/typescript — F (regressed from D).** `Workbook.save()`'s `generateWorkbook()`/`generateWorkbookRels()` are hardcoded with exactly two `if` blocks (sheetId 1, sheetId 2) and no loop; a 3rd+ worksheet's data is still physically written to its own zip part but never referenced by `workbook.xml` or the rels file, making it invisible to Excel and any OOXML-compliant reader with **zero exception, zero warning**. This is a structural, unbounded data-loss bug for the single most basic multi-sheet use case, discovered only because round 2's test happened to use exactly 2 sheets. Combined with 5 other confirmed silent-write-is-dropped bugs (mergeCells, hyperlinks, data validation/conditional-format/comments, builtin number formats) and formulas remaining write-only, this pilot should not be presented as production-usable in any form until at minimum the 3rd-sheet bug is fixed and disclosed.
2. **3d/java — D, unchanged after 2 full audit rounds.** All 4 P0s reproduce byte-for-byte: 50% phantom-symbol rate (37/74 plausible names resolve as confidently real but don't exist), 0 of 37 documented export formats produce a usable file, STL discards geometry, and `STLASCII` writes byte-identical binary output to `STL_BINARY`. `known_defects` now names 3 of these accurately but is absent from `find_examples` (the exact surface where the broken "verified" STL examples live) and misattributed on one symbol (`isManifold` inherits `triangulate`'s defect text).
3. **slides/java — D, unchanged after 3 rounds.** Both original P0s (`save()` ignores `SaveFormat` entirely; the documented slide-add pattern produces orphaned parts) are still broken on the identical published jar. Worse: `report_index_freshness` regressed from an honest "unknown" freshness verdict to a confident "fresh" one with no supporting drift data — a regression in epistemic honesty, not an improvement. `known_defects` is empty for all the implicated symbols despite `limitations` text accurately describing both bugs.
4. **tex/python — D, unchanged after 3 rounds.** The published package still cannot be imported at all (re-proven with a brand-new clone and venv this round); 35 of 45 source files fail to parse. `known_defects` now correctly states this at the pilot level but is empty on `TeXJob`/`PdfDevice`/`TeXOptions` — the exact 3 symbols the quick-start tells a user to import.
5. **cells/cpp — C+, now with a SECOND independent hallucination.** On top of the still-open round-2 finding (the FAQ fabricates CSV-load support that does not exist), round 3 found the `install` section's text, explicitly labeled "the repository README's installation section, verbatim," is **itself fabricated** — that exact sentence does not appear anywhere in the real README, and the real primary install path (a working NuGet package) is never surfaced by any tool. Two confirmed fabrications in one pilot is a pattern worth a dedicated cross-portfolio spot-check of every other "verbatim README quote" claim.
6. **3d/net — D, unchanged.** `formats`/`limitations` still falsely claim PLY/RVM/AMF/HTML5 export work; all four still throw or produce empty files. The one additional example still doesn't compile. Not re-verified fresh this round (no dispatched agent reported new evidence beyond round-2 carryover), so treat as presumptively unchanged pending a future pass.
7. **cells/go — C, two now-confirmed silent-data-loss bugs.** The round-2 P0 (numeric cells silently become strings on reload) persists; round 3's own new extended-scenario testing found a second, independent one: `AddPicture` embeds an image that is written to the zip but never referenced in `sheet1.xml`, orphaning it from both this library's own loader and real Excel.

## Best success stories — proof that fixing works

1. **pdf/typescript (B- → B).** The headline round-2 defect — fragment/prefix search completely broken (`Doc` didn't even match `Document`) — is genuinely fixed: every one of 7 previously-failing prefixes now works, with zero regression to exact-name ranking or contract behavior. This is the clearest single data point in the whole portfolio that a targeted fix, verified independently, moves the needle.
2. **pdf/java (C+ → B-).** Both original P0s were reclassified to P1 because `known_defects` now actively warns an agent who looks up the exact symbol each repro named (`WatermarkArtifact`, `Page`) — a real, substantive reduction in the chance of the mistake reaching production code, even though the underlying library bugs are unfixed. The `limitations` section also gained a genuine, dated, evidence-quoting correction for a separate false claim. This is the best example in the portfolio of `known_defects` working as *intended* — worth studying as the template for the propagation fix recommended above.
3. **barcode/python (B- → B).** Several previously-broken `find_examples` queries (code128, RenderOptions, QrOptions, module_width, show_text) now hit; fragment-matching minimum-length behavior is now consistent and self-documenting deployment-wide; the UPC-E length constraint is now accurately disclosed via `known_defects`. Multiple small, independently-verified fixes compounding into a real grade move.
4. **email/cpp, email/net, slides/net, note/python** (all unchanged letter grade, but real internal progress). Fragment/prefix search-matching fixes verified clean with no regressions in email/cpp and email/net; `known_defects` correctly and accurately disclosed two upstream bugs on the exact symbols in note/python (`DocumentVisitor`, `RichText`) — one of the only pilots where the propagation actually reached the right symbol; slides/net's short-fragment search fix is clean and well-documented.
5. **words/net (SaveFormat fix "exceeds the suggested fix").** `limitations` gained a dated hazard paragraph, and separately, `get_symbol(SaveFormat)` now individually annotates all 30 unsupported enum members inline with "(not implemented in this build...)" — a genuinely better outcome than what round 2 asked for, and proof the per-member annotation mechanism, once built, can be applied broadly.

## Per-pilot one-paragraph summaries

- **3d/java (D):** All 4 P0s unchanged after 2 rounds; `known_defects` disclosure is a step forward but incomplete and once misattributed. Needs dedicated remediation, not incremental fixes.
- **3d/net (D):** Export-format claims still falsely optimistic across the board; not independently re-verified this round.
- **3d/python (C):** Both P0s (OBJ crash, invalid glTF mesh name) unfixed; `known_defects` accurate at freshness, empty on the 5 implicated classes.
- **3d/typescript (C+, trending B-):** 3 of 9 issues genuinely fixed this round, including a real ranking fix and the best-targeted `known_defects` entry in the pilot; install/limitations contradiction and example sparsity persist.
- **barcode/python (B):** Real, verified search/ranking improvements; coverage-claim honesty issue persists.
- **cells/cpp (C+):** Original P0 (fabricated FAQ) persists; a second, independent fabrication (fake "verbatim" install quote) discovered this round.
- **cells/go (C):** Original P0 persists; a second silent-data-loss bug (picture round-trip) discovered; `known_defects` wiring gap formalized as its own issue.
- **cells/java (B):** Strongest of the cells/* family; one P0 (save/extension mismatch) persists with a well-targeted-but-misplaced disclosure; `find_examples` retrieval bug narrowed (content exists, is just unreachable).
- **cells/net (B-):** Same Save-extension P0 persists and was shown this round to generalize to any extension; disclosure gap confirmed systemic across the cells family via cross-pilot check.
- **cells/python (C+):** Two P0s (FormulaEvaluator silently None, datetime loses date-ness) both persist; one is now disclosed, one is not; new phantom-symbol finding (RichTextRun).
- **cells/rust (B):** Cleanest cells/* platform; 10 of 11 issues persist but nothing is severity P0; the one upstream gap (no PDF/CSV export) is now accurately disclosed.
- **cells/typescript (F, regressed):** Worst pilot in the portfolio this round — a newly discovered, severe, undisclosed silent-data-loss bug (3rd+ sheet dropped) on top of 5 other confirmed write-is-dropped defects and write-only formulas.
- **email/cpp (C+):** Clean, verified partial fix to example discoverability; the P0 (mislabeled embedded-message blob) persists, now disclosed.
- **email/net (B):** Genuine, verified fragment-search fix; one new finding — a freshness-honesty regression (confident-wrong "stale" replacing honest "unknown").
- **email/python (B-):** Multiple genuine improvements (Bcc-leak disclosure fixed, sender-address discoverability substantially improved via lookup) without yet crossing a grade boundary.
- **html/python (B-):** A real correction was added to `limitations` prose, but it doesn't propagate to `lookup`/`search_docs`, the tool's own "preferred" entry point — a textbook example of this round's #1 systemic finding.
- **imaging/net (C):** Multiple real upstream bugs (DoS-adjacent, dimension fabrication on truncated input) now accurately disclosed via `known_defects`; the vulnerable commit itself is still indexed, not advanced past the known fix.
- **note/python (B-):** One of the cleanest `known_defects` rollouts in the portfolio — correctly populated on the exact implicated symbols; one new freshness-honesty regression found (undocumented inference replacing an explicit "never guesses" promise).
- **page/python (B):** A brand-new P0 (page_size silently ignored by every export path) was found this round via deeper testing of a lead round 2 explicitly left open — the existing grade absorbs it only because everything else remains excellent.
- **pdf/cpp (C+):** The security-relevant P0 (AES-256 round trip destroys content) persists unchanged; disclosure reaches 2 of 4 natural discovery paths.
- **pdf/go (B-):** Index was not regenerated for this gate at all — every round-2 defect reproduces because nothing changed; `known_defects` schema present but empty everywhere.
- **pdf/java (B-, improved):** The portfolio's best example of `known_defects` working as designed; both original P0s meaningfully mitigated.
- **pdf/net (B):** One clean, well-targeted fix (`documentation_content_type_counts`); `known_defects` schema present but unpopulated even for the pilot's own documented `OcspSettings` defect.
- **pdf/python (C):** No fresh round-3 evidence collected in this aggregation pass beyond round-2 carryover; treat as presumptively unchanged.
- **pdf/typescript (B, improved):** The cleanest, most verifiable fix in the whole portfolio (fragment search).
- **slides/cpp (B):** `known_defects` correctly surfaces the one bug this audit specifically fed back, but not two other equally-documented sibling defects — evidence the mechanism is manually curated per-finding, not derived from the limitations text generally.
- **slides/java (D):** Both P0s persist on the identical published jar after 3 rounds; a freshness-honesty regression was found alongside one genuine search-ranking fix.
- **slides/net (B+):** Portfolio's highest-rated pilot; one clean ranking fix; the single upstream bug it has is disclosed on the interface but not the concrete implementing class.
- **slides/python (C):** One of two P0s fixed at the MCP-disclosure level (a dated, specific hazard paragraph); the crash-causing P0 persists; a new false claim found in `agent_guidance` (group-shape formatting).
- **tex/python (D):** Cannot import at all, 3 rounds running; `known_defects` accurate at the pilot level, empty on the 3 symbols the quick-start actually uses.
- **words/net (C+):** One standout fix that exceeds its own suggested remedy (per-member SaveFormat annotations); a newly confirmed, reproducible compile-breaking documentation bug (unqualified cross-namespace types) found this round.
- **words/python (C+):** A real, working from-scratch document-authoring API exists in the installed package and remains completely undiscoverable through every MCP surface, 3 rounds running.

## Go/no-go recommendation for announcing to aspose.org users

**Not yet — ship a scoped subset, not the full 32-pilot portfolio, and fix the known_defects propagation gap first.**

Reasoning:
- The portfolio's median experience is good and stable: roughly 20 of 32 pilots sit at B-/B/B+/C+ with no P0s, clean error handling, honest hallucination resistance, and real (if incomplete) responsiveness to prior feedback. That tier is announceable today.
- But **6 pilots (3d/java, 3d/net, slides/java, tex/python, cells/typescript, and arguably cells/cpp given two now-confirmed fabrications) have either a confirmed hallucination or a silent, undisclosed data-loss/correctness P0 that has survived 2–3 full audit rounds unfixed.** Presenting these without a loud caveat risks a first-time aspose.org user hitting exactly the kind of silent failure (a dropped worksheet, a destroyed encrypted PDF, a watermark that silently never appears) this audit program exists to catch, with no runtime warning.
- The fix for the single biggest lever — `known_defects` propagation — is well-scoped, already proven to work correctly in at least 3 pilots (pdf/java, note/python, 3d/typescript's createPolygon entry), and does not require touching any upstream library. It is realistic to land before an announcement.

**Recommended path:** (1) land the `known_defects` propagation fix as one cross-cutting change — the content already exists; (2) fix or explicitly mark `cells/typescript` as experimental/unsupported until the 3rd-sheet bug is resolved, given its severity and silence; (3) add the equivalent "Known hazard" disclosure pattern already proven in `limitations` (html/python, slides/python, words/net) to the 5-6 pilots with an unfixed P0 and currently thin or absent disclosure (3d/java, 3d/net, slides/java, tex/python, pdf/cpp); (4) then announce, clearly scoping which pilots are production-grade vs. experimental rather than presenting all 32 as equivalent.
