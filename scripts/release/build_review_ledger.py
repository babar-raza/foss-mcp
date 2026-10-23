#!/usr/bin/env python3
"""Generate docs/REVIEW_LEDGER.md from data, project/state.yaml and docs/RELEASE.md.

The finding table below is DATA. The Status column is computed at run time:

* FIXED    - every fixing card is ACCEPTED or SUPERSEDED in project/state.yaml
             (a superseded card's work was fully carried by its accepted
             successor; and every required record exists). Evidence names the
             cards and the harness check that must pass on the final image.
* ACCEPTED - the finding carries an explicit accepted decision and every card
             that fixes part of it is ACCEPTED or SUPERSEDED. The reason is in Evidence. At
             run time the id must appear bold in docs/RELEASE.md section 8
             (``**L-0NN**``), or, for a deferred extractor card, the card id in
             section 9 (``**TC-397**``); a WARNING lists every one that does not.
* OPEN     - anything else. Evidence names the pending cards and their status.

Usage (stdlib + PyYAML only):

    python scripts/release/build_review_ledger.py                     # markdown to stdout
    python scripts/release/build_review_ledger.py docs/REVIEW_LEDGER.md
    python scripts/release/build_review_ledger.py --check             # counts and the OPEN list
    python scripts/release/build_review_ledger.py --require-data ...  # a FIXED row whose fix changes
                                               # baked data stays OPEN until the
                                               # demo manifests are refreshed

Options: --repo PATH, --date YYYY-MM-DD, --image-revision REV.
Deterministic: output depends only on the repository's files and git history.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

SCRIPT_NAME = "scripts/release/build_review_ledger.py"
IMAGE_REVISION_PLACEHOLDER = "`<image revision: set at release with --image-revision>`"
SEVERITIES = ("Blocker", "Major", "Minor", "Polish")
STATUSES = ("FIXED", "ACCEPTED", "OPEN")

# Where a fix lands in the baked data. "reingest": chunk text or what ingestion
# publishes; "refetch": product-reference or release side files. Both live under
# infra/demo-manifests and reach the image only after a refresh of that tree.
DATA_PATHS = {
    "reingest": ["infra/demo-manifests/*/generation__*.json"],
    "refetch": [
        "infra/demo-manifests/product_reference_*.json",
        "infra/demo-manifests/recent_releases_*.json",
    ],
}
DATA_WORDS = {
    "reingest": "the final re-ingest and demo-manifest snapshot",
    "refetch": "the side-file refetch and demo-manifest snapshot",
    "rebuild": "the serving image rebuild",
}


@dataclass(frozen=True)
class Accepted:
    reason: str
    section: str = "8"  # "8": ledger id in RELEASE.md section 8; "9": card ids in section 9
    cards: tuple[str, ...] = ()  # section-9 card ids
    kind: str = "limitation"  # or "not a defect"


@dataclass(frozen=True)
class Record:
    """A non-card fix: a supervisor record that must exist in the repository."""

    label: str
    path: str
    regex: str | None = None  # a line matching this regex must exist
    yaml_key: str | None = None  # this top-level key must be a non-empty list


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    text: str
    cards: tuple[str, ...] = ()
    check: str = ""
    sources: str = ""
    note: str = ""
    effect: str = ""  # "", "reingest", "refetch", "rebuild"
    accepted: Accepted | None = None
    record: Record | None = None
    gap: str = ""  # part not covered by the cards; OPEN until accepted


def F(fid, severity, text, cards=(), **kw) -> Finding:
    if isinstance(cards, str):
        cards = tuple(c.strip() for c in cards.split(",") if c.strip())
    return Finding(fid, severity, text, tuple(cards), **kw)


def A8(reason, kind="limitation") -> Accepted:
    return Accepted(reason=reason, section="8", kind=kind)


def A9(reason, *cards) -> Accepted:
    return Accepted(reason=reason, section="9", cards=tuple(cards))


NOT_DEFECT = "not a defect"

# ---------------------------------------------------------------------------
# The finding table (132 review findings plus one card-mandated policy row).
# ---------------------------------------------------------------------------
FINDINGS: list[Finding] = [
    # --- Serving: input handling and robustness -----------------------------
    F(
        "L-001",
        "Blocker",
        "report_index_freshness `source_kind` path traversal reads another pilot's generation and mkdirs on read, "
        "inside and outside the data root, without bound; the demo stage `COPY --chown` made baked data writable",
        "TC-380, TC-472",
        check="rv1 C07, C08; rv3 L17, L18; rv4 L12; qa isolation",
        sources="rv1 B2; rv3 M1; rv4 B1; QA 1; rv2 Minor; audit A N-3",
        effect="rebuild",
    ),
    F(
        "L-002",
        "Blocker",
        "One stray scope directory (as created by L-001) stops the next `docker restart`: entrypoint exits 2 "
        '"pilot is ambiguous"',
        "TC-390, TC-380",
        check="rv1 C24; rv4 L13",
        sources="rv1 B1; rv4 B1",
        note="TC-390 makes the entrypoint ignore stray directories; TC-380 removes the cause",
    ),
    F(
        "L-003",
        "Minor",
        "On a read-only root filesystem, an unknown or 300-character `source_kind` answers `internal` instead of "
        "`invalid_argument`",
        "TC-380",
        check="rv3 R02",
        sources="rv3 M1; rv4 m6; QA 1; QA 11",
    ),
    F(
        "L-004",
        "Blocker",
        "Abandoned MCP sessions are never reclaimed (SDK defaults: 30 min idle, 10,000 sessions); OOM-kill after "
        "~1,300 sessions under demo limits",
        "TC-421",
        check="rv3 X01",
        sources="rv3 B4; rv1 Minor (600 sessions +14 MB)",
        note="TC-421 bounds sessions to 200 live and 5 minutes idle; TC-391 only re-tunes the bound",
        effect="rebuild",
    ),
    F(
        "L-005",
        "Major",
        "One large query stalls the server: handlers run synchronously on the event loop and re-parse the whole "
        "generation per call, blocking /healthz and /readyz",
        "TC-389, TC-380",
        check="rv1 C15; rv4 L14; rv3 L26",
        sources="rv1 M2; rv4 M1; rv3 Minor 7; QA 17",
    ),
    F(
        "L-006",
        "Major",
        "`search_docs` with `top_k=2**63` gets no reply within 10 s (the project's own adversarial suite fails "
        "words/net)",
        "TC-380, TC-389",
        check="scripts/release/adversarial_suite.py; rv4 advsuite",
        sources="rv4 baseline",
    ),
    F(
        "L-007",
        "Minor",
        'Arguments are coerced and unknown arguments silently ignored (`top_k` "3"/true/2.0, `bogus_field`, '
        "`list_recent_changes {query}`); get_symbol does not strip whitespace",
        "TC-380",
        check="rv1 C09; rv2 malformed",
        sources="rv1 Minor; rv2 Minor; QA 18; audit A N-3",
    ),
    F(
        "L-008",
        "Minor",
        "Query and fqn are echoed back without a size cap (1 MB query gives ~4 MB response; 50k chars give 200 KB)",
        "TC-380",
        check="rv1 C15; qa boundary",
        sources="rv1 M2; QA 14",
    ),
    F(
        "L-009",
        "Minor",
        'Implementation detail in error text: "mkdir: embedded null character in path"',
        "TC-380",
        check="rv1 C10",
        sources="rv1 Minor; QA 11",
    ),
    F(
        "L-010",
        "Polish",
        'Arguments containing "/" (section=../../etc/passwd, content_type with "/") answer "An internal error '
        'occurred" under code invalid_argument',
        "TC-380",
        check="rv3 L20",
        sources="audit B N-05; rv3 Minor 4",
    ),
    F(
        "L-011",
        "Minor",
        "report_index_freshness accepts non-SHA, empty or version-string commits and any `source_kind` value "
        '("bogus" gives "never published")',
        "TC-380, TC-383",
        check="rv2 freshness",
        sources="audit A N-3; rv2 Minor; rv4 M7; QA 11",
    ),
    # --- Serving: protocol and transports ------------------------------------
    F(
        "L-012",
        "Blocker",
        "Official MCP SDK client locked out after initialize by the protocol-version handshake (raw R2 B01)",
        "TC-250",
        check="rv3 S01; rv4 P01; rv2 protocol",
        sources="audit A A-030; audit B B-001; verified OK by rv1-rv4 and QA",
        note="TC-250 fixes the TC-246 regression",
    ),
    F(
        "L-013",
        "Blocker",
        "A non-string `query` leaked a raw traceback with no correlation id",
        "TC-239",
        check="rv1 C09, C10; rv3 L21",
        sources="audit A A-004; audit B B-007; raw R1 P0",
    ),
    F(
        "L-014",
        "Minor",
        "An invalid enum value on search_docs was classified INTERNAL instead of invalid_argument",
        "TC-239",
        check="rv3 L19",
        sources="audit A A-015",
    ),
    F(
        "L-015",
        "Minor",
        "The protocol-version negotiation module was dead code",
        "TC-246, TC-250",
        check="tests/test_unwired_modules.py",
        sources="audit A A-005; raw R1 P2",
    ),
    F(
        "L-016",
        "Minor",
        "The stdio transport had no structured logging",
        "TC-245",
        check="tests of infra/serve_stdio.py (TC-245)",
        sources="audit A A-006; raw R1 P2",
    ),
    F(
        "L-017",
        "Major",
        'stdio omits the product-reference and release sidecars: install says "no packaging manifest" and '
        "releases come back [] while HTTP serves both",
        "TC-390",
        check="rv1 C16",
        sources="rv1 M3; rv4 m2",
    ),
    F(
        "L-018",
        "Minor",
        "A malformed JSON-RPC body gets HTTP 400 carrying the raw pydantic dump and an errors.pydantic.dev URL",
        "TC-391",
        check="rv3 L25",
        sources="audit A N-4; rv1 Polish; rv3 Polish",
        effect="rebuild",
    ),
    F(
        "L-019",
        "Minor",
        'serverInfo.version is "0.0.0" and initialize returns no instructions or product identity',
        "TC-391",
        check="rv1 C02",
        sources="audit B N-06; rv1 Minor; rv2 Polish; rv3 Polish; rv4 M7; QA 20",
        effect="rebuild",
    ),
    F(
        "L-020",
        "Polish",
        "Entrypoint rejects `PDF/NET` without a hint; a bad FOSS_MCP_LOG_LEVEL or FOSS_MCP_SHUTDOWN_GRACE_SECONDS "
        "ends in a raw traceback",
        "TC-390",
        check="rv1 E01-E03; rv3 I09",
        sources="rv1 Polish; QA 19",
    ),
    F(
        "L-021",
        "Minor",
        "A browser Origin (http://localhost:3000) is rejected with no hint that FOSS_MCP_ALLOWED_ORIGINS exists",
        "TC-390",
        check="rv4 P02",
        sources="rv4 m8; QA 19",
        note="the usage text names the variable",
    ),
    F(
        "L-022",
        "Polish",
        "The stateless 2026-07-28 MCP-Protocol-Version header is rejected",
        check="rv4 P02",
        sources="rv4 m8",
        accepted=A8(
            "only handshake-reachable protocol revisions are accepted, and 2026-07-28 is the "
            "non-handshake stateless revision (supervisor, TC-250 fix plan in docs/DECISION_LOG.md)"
        ),
    ),
    # --- Symbol chunks and extraction ----------------------------------------
    F(
        "L-023",
        "Blocker",
        "Unknown return types render as `-> void` (all .NET pilots, jmap/nodejs, cells/python 97%, Rust, every "
        "constructor), and unit tests pin it",
        "TC-382",
        check="rv1 C11; rv3 D03, D10; rv4 DAT-F07; qa void check",
        sources="rv1 B3; rv2 M1; rv3 B1; rv4 B2; QA 2; audit C N-1 (B-1)",
        note="the chunk builder renders only stated facts and the `-> void` pins were replaced",
        effect="reingest",
    ),
    F(
        "L-024",
        "Blocker",
        "The C# extractor loses every return type (field `returns`), `static` and other modifiers, `params` arrays "
        "and default values",
        "TC-396, TC-400",
        check="rv3 D10, D12; rv4 DAT-F07",
        sources="audit A A-034; audit B B-008a; rv3 B1; rv4 B2; rv2 M7",
        effect="reingest",
    ),
    F(
        "L-025",
        "Major",
        '.NET indexers (`this[...]`, e.g. cells/net `Cells["A1"]`) are never extracted',
        "TC-396, TC-400",
        check="rv3 D10 (cells/net indexer truth)",
        sources="audit B B-008b; raw R3 G",
        effect="reingest",
    ),
    F(
        "L-026",
        "Blocker",
        "Python unannotated parameters are dropped (slides/python 100%, cells/python 98% parameterless); overloads "
        "collapse; keyword-only markers and defaults are lost",
        "TC-393, TC-401, TC-395",
        check="rv1 C25; rv3 D04; rv2 signatures",
        sources="audit A A-032; audit B B-008c; audit C C-007, N-2; rv1 B4; rv2 M2; rv3 B2; rv4 B3",
        effect="reingest",
    ),
    F(
        "L-027",
        "Major",
        "Python `@property` is served as a zero-argument method (`prs.slides()` gives TypeError)",
        "TC-393, TC-420, TC-401, TC-395",
        check="rv2 signatures; rv3 D10",
        sources="audit A A-032; audit B B-008c; rv2 M2; rv3 B2; rv4 B3",
        effect="reingest",
    ),
    F(
        "L-028",
        "Major",
        "Python constructors (`__init__`) are never extracted, so no Python pilot shows how to open an existing file",
        "TC-393, TC-401, TC-395",
        check="rv3 D13",
        sources="audit B N-03, B-023; rv2 M2; rv3 B2",
        effect="reingest",
    ),
    F(
        "L-029",
        "Major",
        "Python enums have zero members (141 enums fleet-wide)",
        "TC-394, TC-401, TC-395",
        check="rv3 D14",
        sources="rv2 M2; rv3 B2, Minor 5",
        effect="reingest",
    ),
    F(
        "L-030",
        "Major",
        "Python re-export shells: the natural import path (aspose.cells_foss.Workbook, aspose.threed.Scene) returns "
        "an empty class ranked first, and pairs use up cap slots",
        "TC-394, TC-382, TC-401, TC-395",
        check="rv3 D11; rv4 DAT-F03; rv2 alias",
        sources="audit B N-04, B-014, B-015; audit C C-023 (B-5); rv1 B4, Polish; rv2 M3; rv3 M2; rv4 M5; QA 6",
        effect="reingest",
    ),
    F(
        "L-031",
        "Major",
        "A Python public class built from private mixins is served with no methods (jmap/python JmapClient; "
        "list_mailboxes NotFound)",
        "TC-429, TC-401",
        check="rv2 core (jmap/python)",
        sources="rv2 M4; QA 6; audit B B-030 (Python part)",
        effect="reingest",
    ),
    F(
        "L-032",
        "Major",
        "C++ parameterised and declaration-only constructors are dropped; `&` and `const` are lost; const/non-const "
        "overloads render as identical lines",
        check="rv3 D13, D10",
        sources="audit A A-033; audit B B-008d, N-07; rv1 B4; rv2 M6; rv3 M3",
        accepted=A9(
            "the C++ extractor fix and the C++ re-extraction were cut from this release "
            "(docs/DECISION_LOG.md 2026-10-12)",
            "TC-397",
            "TC-402",
        ),
    ),
    F(
        "L-033",
        "Major",
        "C++ free functions lose all parameters and their namespace; out-of-line members are published as free "
        "functions; .cpp helpers are indexed",
        check="rv4 DAT-F06",
        sources="rv4 M2; rv3 Minor 8",
        accepted=A9(
            "the C++ extractor fix and the C++ re-extraction were cut from this release "
            "(docs/DECISION_LOG.md 2026-10-12)",
            "TC-397",
            "TC-402",
        ),
    ),
    F(
        "L-034",
        "Minor",
        'Base-class constructors and destructors are listed as "inherited" members (1,630 lines across 17-19 '
        "pilots)",
        "TC-392, TC-382",
        check="rv3 D15",
        sources="audit B B-020; audit C N-3; rv2 Minor; rv3 Minor 1",
        note="TC-382 hides them when chunks are built from today's fixtures; TC-392 stops the extractor producing them",
        effect="reingest",
    ),
    F(
        "L-035",
        "Major",
        "Go `*_test.go` symbols are indexed as public API (jmap/go 75 of 149; pdf/go Example*), and lookup returns "
        "Test* functions",
        "TC-392, TC-382",
        check="rv1 C17; rv3 D06; rv4 DAT-F05",
        sources="audit C C-016 (B-7); rv1 M6; rv2 M5; rv3 M6; rv4 M3; QA 7",
        note="TC-382 drops test-file symbols when chunks are built; TC-392 excludes them at extraction",
        effect="reingest",
    ),
    F(
        "L-036",
        "Major",
        "Go struct tags are published as the field type (197 of 225 jmap/go fields)",
        check="rv3 D10 (jmap/go field truth)",
        sources="rv2 M5",
        accepted=A9(
            "the Go struct-field fix and the Go re-extraction were cut from this release "
            "(docs/DECISION_LOG.md 2026-10-12)",
            "TC-428",
            "TC-403",
        ),
    ),
    F(
        "L-037",
        "Major",
        "Non-public code is published under colliding bare FQNs (Go receiver methods of unexported types, Rust "
        "trait boilerplate, TS non-exported helpers); get_symbol returns an arbitrary match",
        "TC-382",
        check="rv3 D05; rv4 DAT-F02; qa duplicate FQNs",
        sources="rv3 M7; rv4 M3; QA 7",
        effect="reingest",
        note="TC-382 qualifies every colliding FQN, so get_symbol no longer returns an arbitrary match",
        accepted=A9(
            "the non-public functions themselves stay published, because the Go/Rust/TypeScript "
            "public-API filter and its re-extraction were cut (docs/DECISION_LOG.md 2026-10-12)",
            "TC-398",
            "TC-403",
        ),
    ),
    F(
        "L-038",
        "Minor",
        "Untyped parameters render as `options: ` with an empty type (65 in jmap/nodejs)",
        "TC-382",
        check="rv3 D10 (jmap/nodejs fromJson)",
        sources="rv1 B3; audit B B-008e",
        note="the missing JSDoc types themselves are L-092",
        effect="reingest",
    ),
    F(
        "L-039",
        "Minor",
        "TypeScript property types carry the declaration colon (`id: : string`), properties are listed twice, and "
        "object-literal keys leak in as class properties",
        "TC-382",
        check="qa contract",
        sources="audit A A-039; audit B B-027; rv2 Minor; raw R3 O",
        effect="reingest",
        accepted=A9(
            "TC-382 fixes the colon and the duplicates; the export filter that would drop object-literal "
            "keys was cut (docs/DECISION_LOG.md 2026-10-12)",
            "TC-398",
            "TC-403",
        ),
    ),
    F(
        "L-040",
        "Polish",
        "Enum members with no value render with a dangling `=` (`None = `, `DARKEN = `, `Custom = `)",
        "TC-382",
        check="tests/indexing/test_chunk_builder_honesty.py (rule 11)",
        sources="rv1 Polish; rv2 Minor; rv3 Minor 5",
        effect="reingest",
    ),
    F(
        "L-041",
        "Minor",
        "Deprecation is recorded in the fixtures but never rendered (e.g. `[Obsolete] UpdateTableLayout`)",
        "TC-382",
        check="tests/indexing/test_chunk_builder_honesty.py (deprecated)",
        sources="audit C C-012 (B-8); rv3 Polish",
        effect="reingest",
    ),
    F(
        "L-042",
        "Major",
        "A nested class FQN dropped its enclosing class (JmapClientOptions.Builder)",
        "TC-241",
        check="rv4 (verified OK)",
        sources="audit A A-013",
    ),
    F(
        "L-043",
        "Blocker",
        "Phantom C++ members were synthesised from Get/Set pairs (cells/cpp Borders), including out-of-line "
        "definitions (raw R3 C1)",
        "TC-259, TC-276",
        check="rv2-rv4 (verified OK)",
        sources="audit B B-019; audit C C-001",
    ),
    F(
        "L-044",
        "Major",
        "Base-class members were shown as the subclass's own, with no provenance (raw R3 C3)",
        "TC-261, TC-281, TC-282, TC-283, TC-284, TC-285, TC-286, TC-287, TC-297, TC-316",
        check="rv4 (verified OK)",
        sources="audit C C-003; audit B B-020 (tagging part)",
        note="regeneration waves TC-282 to TC-316; TC-289 superseded by TC-297",
    ),
    F(
        "L-045",
        "Blocker",
        "The symbol index was capped at the first 20 types and cut alphabetically (raw R1 P0)",
        "TC-238, TC-252",
        check="rv3 D02",
        sources="audit A A-007",
        note="regenerations TC-255 to TC-258, TC-262, TC-263, TC-266 to TC-272, TC-277",
    ),
    F(
        "L-046",
        "Blocker",
        "The 300-type cap silently drops central types (slides/python Presentation, Slide, SaveFormat; words/net "
        "DocumentBuilder; pdf/net PageCollection, TextAbsorber; pdf/python Document; pdf/go Open)",
        "TC-394, TC-395, TC-400, TC-401",
        check="rv3 D02, D09; rv2 core; rv4 L07",
        sources="audit A A-007 residual, A-056; audit B B-003; audit C C-004 (B-3); rv1 M1; rv2 B2; rv3 B3; rv4 M5; "
        "QA 6",
        effect="reingest",
        accepted=A9(
            "for the C++, Go, Rust and TypeScript pilots the re-extraction with the new selection was cut "
            "(docs/DECISION_LOG.md 2026-10-12), so the 300-type cap may still hide central types there; "
            "the cut is disclosed per pilot (L-047)",
            "TC-402",
            "TC-403",
        ),
    ),
    F(
        "L-047",
        "Major",
        "Truncation is never disclosed, so a real but unindexed type looks like an authoritative NotFound",
        "TC-383, TC-382",
        check="rv1 C19; rv3 D09",
        sources="rv1 M1; rv2 B2; QA data gaps; audit A A-007",
        note="TC-382 writes the `API-Coverage:` line; TC-383 reports it",
        effect="reingest",
    ),
    F(
        "L-048",
        "Major",
        "Tests and the proof harness cannot see missing central types: the probe is picked from the truncated list, "
        "and a test asserts Presentation is absent",
        "TC-395",
        check="rv3 D02",
        sources="audit A A-003; audit B B-028, B-029; audit C C-024, B-3",
        note="TC-395 adds the central-class fixture test and drops the absence assertion",
    ),
    F(
        "L-049",
        "Major",
        "slides/java Presentation, pdf/go Page, pdf/typescript Document and pdf/net Page were missing",
        "TC-257, TC-262, TC-255, TC-263",
        check="rv3 D02",
        sources="audit A A-036, A-037, A-038; audit B B-003 (fixed part), B-031; audit C C-004",
    ),
    F(
        "L-050",
        "Major",
        "cells/cpp agent_guidance named APIs that were not indexed",
        "TC-271",
        check="rv2 core (cells/cpp)",
        sources="audit A A-012 (the install part is L-070)",
    ),
    F(
        "L-051",
        "Major",
        "The ingestion citation validator silently drops real API symbols (142, e.g. words/python SaveFormat, "
        "jmap/python Email and MethodError)",
        "TC-384",
        check="qa fixture comparison; rv2 core",
        sources="audit A N-1, A-052, A-056; rv2 D2; QA data gaps",
        effect="reingest",
    ),
    F(
        "L-052",
        "Major",
        "tex/python serves only 4 exception classes (the upstream source does not parse), and nothing tells the client",
        "TC-426",
        check="rv3 D02 (UPSTREAM)",
        sources="audit A A-014; rv2 B3; rv3 Upstream; rv4 data gaps; rv1 data gaps",
        note="an extraction-notice chunk names the unparseable upstream files; the missing classes are an upstream "
        "source defect",
        effect="reingest",
    ),
    # --- Retrieval and tool contracts ----------------------------------------
    F(
        "L-053",
        "Major",
        "search_docs labels every symbol chunk `developer_guide`, and lookup takes the first category with a hit: "
        "answers are 18-96 KB of class dumps or the same install chunk",
        "TC-381",
        check="rv1 C13; rv3 L31; rv4 L08",
        sources="rv1 M4; rv2 M10; rv3 M4, Polish; rv4 M6; QA 3; audit C (minor list)",
    ),
    F(
        "L-054",
        "Major",
        'The find_examples relevance floor is defeated by the product-family word and by single words ("page" '
        "scores 1.0), so irrelevant examples come back with confidence",
        "TC-381, TC-399",
        check="rv3 L11; qa find_examples",
        sources="audit B B-009; audit C C-005 (B-6); raw R3 C5; rv1 M5; rv2 M10; rv4 M6; QA 4",
        note='the family word no longer counts: pdf/net "split a pdf into pages" now answers NoExampleFound',
        accepted=A8(
            "a query of one or two distinctive words is answered only by an example whose title contains one "
            "of them, so a word that code merely calls no longer returns that example; a longer question "
            "needs 60% of its other words in the example, the family word never counts, and match_basis "
            "states what each coverage number means"
        ),
    ),
    F(
        "L-055",
        "Major",
        "find_examples snippets carry title, `Kind:`, prose, a shortcode and `Source-Commit:`; any doc comment "
        'containing "Example:" is served as verified (3d/java DracoFormat)',
        "TC-381, TC-465",
        check="rv1 C12; rv4 DAT-F04",
        sources="rv1 M5; rv2 M10; rv4 m4; QA 9",
        note="TC-381 makes the snippet code only; TC-465 makes one predicate define a verified example",
    ),
    F(
        "L-056",
        "Minor",
        "Miss shapes are unmarked: a find_examples miss has no reason or NoExampleFound marker, NotFound has no "
        "found:false, and the unknown-tool error has no message",
        "TC-381, TC-386",
        check="rv3 L15",
        sources="audit A A-050, N-3; audit B B-009; audit C C-014; QA 13",
    ),
    F(
        "L-057",
        "Minor",
        'No stemming, plural or typo tolerance: "install" misses "Installation"; Workbok and Documnet get no '
        "suggestion",
        "TC-381, TC-386",
        check="rv1 C23; rv2 install_doc",
        sources="audit A A-051; audit B B-016; rv1 M4, Polish; QA 15; raw R3 J",
    ),
    F(
        "L-058",
        "Major",
        'Nonexistent features and nonsense questions get confident answers (slides/java "password" gives '
        'PptCorruptFileException; "bake a cake" is answered on 18-22 of 40 pilots)',
        "TC-381, TC-399",
        check="rv4 L09; rv2 negative",
        sources="audit B B-013; rv2 Minor; rv4 M6; raw R3 H",
    ),
    F(
        "L-059",
        "Polish",
        'The lookup miss message says "no symbol matches" even after searching docs and examples',
        "TC-381, TC-386, TC-493",
        check="rv3 L15",
        sources="audit A N-5; QA 15",
        note="TC-493 makes lookup's miss reason name the documentation, example and symbol searches it ran",
    ),
    F(
        "L-060",
        "Minor",
        'Pseudo-symbols leak into get_symbol and list_members ("Example: ..." answers kind verified_example; '
        '"Doc: ..." lists [] as success)',
        "TC-386",
        check="qa contract",
        sources="QA 12",
    ),
    F(
        "L-061",
        "Minor",
        "The find_examples description advertises an `fqn` argument its schema lacks",
        "TC-386",
        check="rv1 C03",
        sources="audit A A-047; rv1 Minor; rv3 Minor 2; QA 8",
    ),
    F(
        "L-062",
        "Minor",
        "Tool descriptions hard-code PDF/.NET examples that resolve nowhere (member FQN aspose.pdf.Document.save, "
        "AFRelationship, `pip install aspose-pdf`) and have no enums, bounds or annotations",
        "TC-386",
        check="rv1 C03",
        sources="audit C C-013; rv4 M7; QA 8; rv1 Minor; rv2 Minor",
    ),
    F(
        "L-063",
        "Minor",
        "cells/cpp `Cell`, pdf/net `Document` and words/python `Table` were buried in the ranking",
        "TC-278",
        check="rv3 D11",
        sources="audit A A-046; audit B B-012, B-018; raw R3 C7",
    ),
    F(
        "L-064",
        "Major",
        "search_symbols could not find bare method names",
        "TC-279",
        check="rv3 D11",
        sources="audit C C-006",
    ),
    # --- Product reference and releases --------------------------------------
    F(
        "L-065",
        "Blocker",
        "Install coordinates were fabricated for unpublished packages (raw R3 C2)",
        "TC-260, TC-275, TC-353",
        check="rv3 L12",
        sources="audit A A-040; audit B B-011; audit C C-002; rv3 verified OK",
    ),
    F(
        "L-066",
        "Minor",
        "License is NotAvailable where a POM, a root LICENSE or Directory.Build.props states it (18 pilots, incl. "
        "hard-coded java/go/cpp and slides/net)",
        "TC-387, TC-427",
        check="rv4 DAT-F08; rv2 prodref",
        sources="rv2 M9; rv4 m1; QA 10; rv1 Minor; audit A A-045",
        effect="refetch",
    ),
    F(
        "L-067",
        "Minor",
        "support is NotAvailable on 16 pilots although the repository URL is known (earlier it worked only for .NET)",
        "TC-254, TC-427",
        check="rv2 prodref",
        sources="audit A A-042; audit B B-004; rv2 M9",
        note="TC-427 adds the labelled source-repository fallback",
        effect="rebuild",
    ),
    F(
        "L-068",
        "Minor",
        "The agent_guidance lookup is case-sensitive: cells/java `Agents.md`, cells/typescript and slides/python "
        '`agents.md` answer "not present"',
        "TC-427",
        check="rv2 prodref",
        sources="audit A A-044; rv2 M9",
        effect="refetch",
    ),
    F(
        "L-069",
        "Minor",
        "Upstream AGENTS.md and CONTRIBUTING text is returned verbatim with no untrusted-content label",
        "TC-387",
        check="rv2 prodref",
        sources="audit C C-026",
    ),
    F(
        "L-070",
        "Major",
        'C++ install answers only "cpp has no package-manager install command" and gives no build-from-source path '
        "(email/cpp, jmap/cpp, cells/cpp)",
        "TC-387",
        check="rv3 L12",
        sources="audit A A-012 (install), A-055; rv3 Minor 6",
        note="the reason now points at the distribution notes",
    ),
    F(
        "L-071",
        "Minor",
        "formats and limitations are hard-coded NotAvailable fleet-wide (e.g. slides/python's unsupported PDF export "
        "is never surfaced)",
        check="rv2 prodref",
        sources="audit A A-054; audit C C-008; rv1 Minor, M; rv2 M9; rv4 fresh-agent slides/python",
        accepted=A8(
            "no data source reaches these two sections, and the tool answers NotAvailable with that reason "
            "instead of inventing content (TC-427 drafter)"
        ),
    ),
    F(
        "L-072",
        "Minor",
        "The 5 C++ pilots have no package-registry sidecar, so they have no install verification",
        check="tests/deploy/test_demo_manifests_sidecars.py",
        sources="rv3 data gaps; rv4 data gaps",
        accepted=A8(
            "C++ has no registry coordinate, and the real-tree test requires the file's absence "
            '(docs/DECISION_LOG.md 2026-10-11 "C++ pilots have no package-registry sidecar by design")'
        ),
    ),
    F(
        "L-073",
        "Major",
        "A failed release fetch is written as `[]` and treated as fresh for 24 h (slides/net, slides/cpp, "
        "note/python serve [] although they have real releases)",
        "TC-244, TC-388",
        check="rv1 C22; rv2 changes --check-upstream; rv3 L13; rv4 L05",
        sources="audit A A-010; audit B B-002, N-02; rv2 M8, D4; rv3 Minor 3",
        effect="refetch",
    ),
    F(
        "L-074",
        "Minor",
        "Release entries carry no date or URL",
        "TC-388",
        check="rv2 changes",
        sources="rv1 Minor; rv4 m7; QA 16",
        effect="refetch",
    ),
    F(
        "L-075",
        "Major",
        "The releases sidecar was read at boot with no wait (K8s race, raw R2 B02)",
        "TC-251",
        check="tests of the sidecar wait (TC-251)",
        sources="audit A A-031; audit B B-002",
    ),
    F(
        "L-076",
        "Minor",
        "report_index_freshness reports one arbitrary commit for a mixed generation and rejects short or uppercase SHAs",
        "TC-383",
        check="rv1 C14; rv3 D08; rv4 L06",
        sources="QA 5, QA 11; rv1 Minor; rv3 Polish; audit B N-01; audit C N-4",
    ),
    F(
        "L-077",
        "Major",
        "Mixed provenance in one pilot: pdf/net symbols are from 10a3638 but docs and product reference from b717287 "
        "(net8.0 contradicts v26.10.0 notes); pdf/typescript the same",
        "TC-383, TC-394, TC-400",
        check="rv4 DAT-F01; rv3 D08",
        sources="audit A A-002; audit B N-01; audit C N-4; rv1 M7; rv2 Minor; rv4 M4, m5; QA 5, QA 16",
        effect="reingest",
        accepted=A9(
            "pdf/net is re-extracted at its pinned commit (TC-400); pdf/typescript's re-extraction was cut, "
            "so its symbols stay at the earlier commit, and report_index_freshness lists both commits "
            "(TC-383)",
            "TC-403",
        ),
    ),
    F(
        "L-078",
        "Minor",
        "Freshness cannot detect staleness itself: it needs a commit the agent cannot obtain, answers stale:true "
        '"unknown", ignores side files; 5 pilots trail upstream HEAD',
        "TC-383, TC-386",
        check="rv2 freshness; rv3 L14",
        sources="audit B B-021; audit C C-021; rv4 M7; rv2 Minor, D3; rv1 Minor",
        note="TC-386 wires the pinned library commit into report_index_freshness",
    ),
    F(
        "L-079",
        "Major",
        "Product-reference sidecars were stale or unfetched (25/40) because the GitHub token was unset and nothing "
        "was cached",
        "TC-243, TC-354",
        check="tests/deploy/test_demo_manifests_sidecars.py",
        sources="audit A A-011, A-053",
    ),
    F(
        "L-080",
        "Minor",
        "/readyz parses the whole generation on every probe, needs no auth, and reports ready for near-empty pilots "
        "(tex/python)",
        "TC-389",
        check="rv1 /readyz probe",
        sources="rv1 Minor",
        accepted=A8(
            "/readyz needs no authentication, like any container probe, and answers only ready or not "
            "ready; it does not judge how much content a pilot has (tex/python is ready with 4 classes and "
            "its extraction notice says why)"
        ),
    ),
    # --- Content and ingestion -----------------------------------------------
    F(
        "L-081",
        "Minor",
        "Hugo shortcodes and zero-width or bidi characters appear in served text, and nothing bounds a chunk's size",
        "TC-385",
        check="qa contract",
        sources="audit A N-2; audit C C-027; rv4 data gaps; QA 9",
        effect="reingest",
    ),
    F(
        "L-082",
        "Major",
        "34 of 40 pilots had no documentation pages; getting_started, troubleshooting and faq were empty",
        "TC-351, TC-356, TC-357, TC-358, TC-364, TC-366, TC-367, TC-368, TC-369, TC-370, TC-371, TC-372, TC-373, "
        "TC-374, TC-375",
        check="rv4 DAT-F09; rv3 D07; rv2 docs",
        sources="audit A A-009; audit B B-017; raw R3 C8; rv1, rv2 D1, rv3, rv4 and QA data gaps",
        note="faq and troubleshooting stay empty upstream (L-107)",
        effect="reingest",
    ),
    F(
        "L-083",
        "Blocker",
        "The citation filter deletes 90-98% of authored documentation (pdf/net keeps 2%; install and quick start "
        "are lost)",
        "TC-425",
        check="rv2 docs, install_doc; rv1 C23",
        sources="rv2 B1; audit C (minor list: one-line install chunks); QA data gaps",
        effect="reingest",
    ),
    F(
        "L-084",
        "Blocker",
        "Verified examples exist for only 6-7 pilots, and find_examples misses everywhere else; 17 pilots still had "
        "none after the first re-ingest",
        "TC-441, TC-450, TC-451, TC-452, TC-453, TC-454, TC-455, TC-456, TC-457, TC-458, TC-459, TC-460, TC-461, "
        "TC-462, TC-463, TC-464",
        check="tests/deploy/test_verified_examples_coverage.py (TC-440, at least 2 per pilot); rv4 DAT-F10; rv3 L11; "
        "rv2 examples",
        sources="audit A A-008; rv1 data; rv2 D1; rv3 data; rv4 data; REQ-G3-007",
        note="verifier fixes F1-F7, the verdict cache and authored examples F8",
        effect="reingest",
    ),
    F(
        "L-085",
        "Minor",
        "Round-4 findings were never tracked in cards or open questions",
        "TC-489",
        check="this ledger; tests pinning docs/RELEASE.md section 8 (TC-489)",
        sources="audit C C-034",
        note="every round-4 finding now has a card (TC-380 to TC-429, TC-470 to TC-475) or a published accepted "
        "reason (TC-489, TC-490); this ledger is the tracking record",
    ),
    F(
        "L-086",
        "Minor",
        "The AGENTS.md 200-line budget check was reported missing",
        "TC-295",
        check="gatectl validate",
        sources="audit C C-031",
    ),
    F(
        "L-087",
        "Blocker",
        "Rollback bypassed the publish-safety guard (raw R2 B10), and the regression guard lived only in the CLI",
        "TC-253, TC-242",
        check="tests/infra/test_rollback.py",
        sources="audit A A-018, A-043; audit B B-005",
    ),
    F(
        "L-088",
        "Blocker",
        "The K8s ingestion Job chained steps with `&&`, so it was marked Failed after content had been published "
        "(raw R1 P0)",
        "TC-240",
        check="helm template tests (TC-240)",
        sources="audit A A-001",
    ),
    # --- Triaged 2026-10-11 (previously without a card) -----------------------
    F(
        "L-089",
        "Blocker",
        "The index runs ahead of or behind the installable package (pdf/go is 66 commits past v0.9.0, so "
        "BarcodeField fails to build; slides/python is 50 commits ahead of PyPI)",
        "TC-470",
        check="get_product_reference install notice tests (TC-470)",
        sources="audit C C-010 (B-4); rv3 M5; rv4 M4; rv2 Minor; audit A A-049",
        effect="rebuild",
        accepted=A8(
            "every install answer names the indexed commit and Go answers give `go get <module>@<commit>`; "
            "computing the registry release's distance needs network at ingestion and a new data file per "
            "pilot, planned after the pilot"
        ),
    ),
    F(
        "L-090",
        "Major",
        "The jmap/nodejs and jmap/typescript client API is assembled from mixins and stays invisible (getMailboxes "
        "NotFound; JmapClient is an empty wrapper)",
        sources="audit A A-035; audit B B-030; audit C C-017; rv2 M4; rv1 B4",
        accepted=A8(
            "JS/TS class-expression mixins are not flattened; a medium-sized change to a shared extractor "
            "file, accepted with that risk for two pilots"
        ),
    ),
    F(
        "L-091",
        "Major",
        "Go/Rust FQNs carry no package or crate path, so the import path cannot be discovered (cells/go module root "
        "vs aspose/cells_foss; jmap/rust private modules)",
        "TC-386",
        sources="audit A A-041; rv2 M5; rv4 m3; QA 20",
        accepted=A8(
            "the tool descriptions say names are package-relative (TC-386) and the Go install answer gives "
            "the module root; sub-package and crate paths need the README"
        ),
    ),
    F(
        "L-092",
        "Major",
        "The JS extractor never reads JSDoc, so `@param {T}` and `@returns {T}` types are lost (jmap/nodejs)",
        sources="audit A A-019; audit B B-008e; rv2 M1; rv1 B3",
        accepted=A9(
            "the JSDoc type fix was cut from this release with the Go/Rust/TypeScript re-extraction "
            "(docs/DECISION_LOG.md 2026-10-12)",
            "TC-475",
            "TC-403",
        ),
    ),
    F(
        "L-093",
        "Major",
        "Python instance attributes and dataclass fields are not extracted (words/python MarkdownSaveOptions, "
        "page/python McpInput)",
        "TC-473, TC-401",
        check="rv2 signatures (words/python MarkdownSaveOptions)",
        sources="rv1 B4; rv2 M2",
        effect="reingest",
    ),
    F(
        "L-094",
        "Major",
        "slides/python dispose and context-manager methods cannot be discovered (a 510 MB leak was measured)",
        "TC-473, TC-395",
        check="rv3 D10 (slides/python Presentation)",
        sources="audit C C-020",
        effect="reingest",
    ),
    F(
        "L-095",
        "Major",
        "There is no signature-vs-upstream fidelity test; verifiers miss 4 of 6 injected defect classes; the publish "
        "guard checks only counts (>50%)",
        sources="audit A A-003, A-022; audit C C-024; rv1 E; rv3 bake pipeline",
        accepted=A8(
            "no automatic signature-quality gate at ingestion; fidelity is pinned by per-language extractor "
            "tests, per-pilot fixture tests and the review harnesses re-run on the final image"
        ),
    ),
    F(
        "L-096",
        "Minor",
        "Python top-level functions render with no signature (barcode `generate`)",
        "TC-473, TC-401, TC-395",
        check="rv3 D10 (barcode/python generate)",
        sources="rv3 B2",
        effect="reingest",
    ),
    F(
        "L-097",
        "Minor",
        "Go typed constants and enums are not extracted",
        sources="rv2 M5; rv3 Minor 5",
        accepted=A8(
            "Go `const (...)` groups are not extracted as enum members; the named type is shown and README "
            "examples use the values by name"
        ),
    ),
    F(
        "L-098",
        "Minor",
        "The stdio transport drops replies still in flight when stdin closes (3 of 6 lost)",
        sources="rv2 Minor",
        accepted=A8(
            "MCP SDK behaviour at stdin EOF; a client that waits for its replies is unaffected, and the "
            "standalone image serves HTTP"
        ),
    ),
    F(
        "L-099",
        "Minor",
        "Every result duplicates its payload (text, structuredContent and raw_text): get_symbol is 55 KB, an "
        "html/python search 255 KB",
        sources="audit C C-015; rv1 Minor; rv2 Polish; rv4 M6; QA 13",
        accepted=A8(
            "text plus structuredContent is what the MCP specification recommends; only get_symbol adds "
            "raw_text"
        ),
    ),
    F(
        "L-100",
        "Minor",
        "/metrics has two counters; the usage queue is never drained (stuck at 1000 queued, 80 dropped); logs lack "
        "pilot identity and error code",
        sources="audit B B-025; rv1 Minor; raw R3 O",
        accepted=A8(
            "the queue is bounded (under 1 MB); metrics and logs are enough to run a pilot, not to alert on"
        ),
    ),
    F(
        "L-101",
        "Minor",
        "server.py hard-codes /data/manifests while the entrypoint and sidecars honour FOSS_MCP_MANIFESTS_DIR",
        "TC-474",
        check="tests/infra/test_serve_http.py (TC-474)",
        sources="rv3 Minor 9",
        effect="rebuild",
    ),
    F(
        "L-102",
        "Minor",
        "pdf/cpp install denies a NuGet package that its own release notes advertise (Aspose.PDF.Cpp.FOSS 1.0.1 exists)",
        "TC-387",
        sources="audit B B-022; raw R3 H",
        accepted=A8(
            "C++ has no package-manager coordinate in CMake; the NotAvailable reason points to "
            "list_recent_changes, whose release note names the NuGet package (TC-387)"
        ),
    ),
    F(
        "L-103",
        "Minor",
        "The Java install answer is a bare coordinate with no version",
        "TC-470",
        check="get_product_reference install notice tests (TC-470)",
        sources="audit B B-034; rv2 Minor; rv4 m5; QA 10",
        note="the Maven notice adds the version the pom at the indexed commit declares",
        effect="rebuild",
    ),
    F(
        "L-104",
        "Minor",
        "The ranking side effect of TC-279: a class with a member named X outranks types named X (PageSize, "
        "IPresentation); words/python has 3 Document classes",
        sources="audit C N-5; audit B B-014",
        accepted=A8(
            "not reproduced on main: search_symbols ranks type-name tiers above the member tier "
            "(TC-278, TC-279); the three words/python Document classes are three real upstream classes",
            kind=NOT_DEFECT,
        ),
    ),
    F(
        "L-105",
        "Minor",
        'lookup ignores version questions ("in version 0.1.0")',
        sources="audit C C-011",
        accepted=A8("each pilot indexes exactly one version, the pinned commit"),
    ),
    F(
        "L-106",
        "Minor",
        'There is no reverse discovery ("which method returns X")',
        sources="audit C C-018",
        accepted=A8("a missing capability in this release, planned later"),
    ),
    F(
        "L-107",
        "Minor",
        "There is no faq or troubleshooting content in any pilot",
        sources="rv4 Polish; QA data gaps; audit A A-009",
        accepted=A8("the upstream READMEs carry none, and search_docs answers an honest miss (TC-381)"),
    ),
    F(
        "L-108",
        "Minor",
        "The registry check proves a name exists, not that it is the right package (aspose-3d-foss points at another "
        "repo; excel-cells could be squatted)",
        sources="audit C C-025",
        accepted=A8(
            "comparing the registry's repository URL with the configured repository is planned after the "
            "pilot"
        ),
    ),
    F(
        "L-109",
        "Minor",
        "Upstream package defects: the 3d/net package is non-functional, 3d/python OBJ export is buggy, "
        "cells/typescript ships a demo-only entry point, jmap release notes are templated",
        sources="audit A A-057, (d); rv2 matrix (3d/python); rv1 data",
        accepted=A8("upstream defects, not MCP defects; the MCP reports the packages as they are"),
    ),
    F(
        "L-110",
        "Minor",
        "B08 from the second audit has no recoverable content (OWNER-11 OPEN)",
        sources="audit B B-006; audit C C-009",
        accepted=A8(
            "retired as unidentifiable: later rounds re-verified every earlier finding and found nothing "
            "unmapped (docs/DECISION_LOG.md 2026-10-09, owner item OWNER-11)"
        ),
    ),
    F(
        "L-111",
        "Minor",
        "Accepted cards cannot be proven integrated, because integration replays commits with new hashes",
        sources="audit C C-033",
        accepted=A8(
            "integration is proven by the `(G3/TC-NNN)` commit subject on main and the acceptance receipt"
        ),
    ),
    F(
        "L-112",
        "Minor",
        'The decision log claims closure that live data contradicts (C6 "fully closed", C1 out-of-line fixed, '
        "C4 closed)",
        sources="rv4 m9; audit C N-2",
        note="the claims concern C6 (TC-281 to TC-287; Python parameters TC-393, TC-401), C1 out-of-line "
        "(TC-276, TC-397) and C4 (TC-252, TC-394)",
        record=Record(
            label="supervisor correction entry in docs/DECISION_LOG.md",
            path="docs/DECISION_LOG.md",
            regex=r"^## .*[Cc]orrect.*\b(C1|C4|C6)\b",
        ),
    ),
    F(
        "L-113",
        "Minor",
        "The reviewed image has no release record (release_images is empty)",
        sources="rv4 baseline",
        note="`gatectl release-record` (the TC-348 mechanism) after the push",
        record=Record(
            label="release record in project/state.yaml release_images",
            path="project/state.yaml",
            yaml_key="release_images",
        ),
    ),
    F(
        "L-114",
        "Minor",
        "The container security context was never reviewed, and the demo stage `COPY --chown` makes baked data "
        "writable by the server",
        "TC-472",
        check="Dockerfile.serving pin test (TC-472); gate-exit live smoke test",
        sources="audit A A-020; rv3 M1",
        effect="rebuild",
    ),
    F(
        "L-115",
        "Major",
        "K8s: the serving init container waits forever for sidecars, and a GitHub 403 leaves no file (the TC-251 wedge)",
        sources="audit C C-028; rv1 Not covered",
        accepted=A8("Kubernetes chart only; the standalone image has no init container"),
    ),
    F(
        "L-116",
        "Major",
        "K8s: Helm installs are not atomic (`--wait` without `--atomic`)",
        sources="audit C C-029",
        accepted=A8("operator tool for the Kubernetes chart, not part of the image"),
    ),
    F(
        "L-117",
        "Minor",
        "K8s: the NetworkPolicy allows a full mesh between pilots, and egress enforcement was disputed (kindnet)",
        sources="audit A A-048; audit B B-010; raw R3 L, O",
        accepted=A8("Kubernetes chart only; the standalone image runs one pilot per container"),
    ),
    F(
        "L-118",
        "Minor",
        "K8s: the chart has no `required()` guard on scope fields",
        sources="audit C C-030",
        accepted=A8(
            "Kubernetes chart only; the standalone entrypoint refuses to start without a valid pilot"
        ),
    ),
    F(
        "L-119",
        "Minor",
        "K8s: /readyz ignores product-reference freshness",
        sources="audit A A-021",
        accepted=A8(
            "by design: readiness means the lexical documents are servable; side files are read once at start"
        ),
    ),
    F(
        "L-120",
        "Polish",
        "The generation can change mid-session without warning",
        sources="audit C C-022",
        accepted=A8("the standalone image bakes one generation and never re-ingests"),
    ),
    F(
        "L-121",
        "Minor",
        "Task-to-API coverage (~59%) and composite planning were never re-measured",
        sources="audit C C-019",
        accepted=A8(
            "measured in review round 4, before the G3 fixes, and not re-measured on the final image"
        ),
    ),
    F(
        "L-122",
        "Polish",
        'C++ compatibility has no unit ("20", "17"); compatibility is missing for 3d/python, cells/rust, '
        "cells/typescript and jmap/rust",
        "TC-471",
        check="get_product_reference compatibility tests (TC-471)",
        sources="audit B B-026; rv2 data gaps; raw R3 O",
        effect="rebuild",
        accepted=A8(
            "TC-471 renders `C++17`/`C++20` and reads 3d/python's python_requires; cells/rust, jmap/rust "
            "and cells/typescript state no version, and the server does not infer one"
        ),
    ),
    F(
        "L-123",
        "Polish",
        'The healthcheck interval of 30 s shows "starting" long after ready; JSON log lines include uvicorn\'s ANSI '
        "color_message",
        "TC-474",
        check="tests/infra/test_serve_http.py (TC-474)",
        sources="QA 20",
        effect="rebuild",
        accepted=A8(
            "TC-474 drops color_message; the 30 s healthcheck interval is cosmetic, and a shorter start "
            "interval needs Docker Engine 25 or later"
        ),
    ),
    F(
        "L-124",
        "Polish",
        "Rust `Bases:` lists derive macros (Debug, Clone)",
        sources="rv3 Polish",
        accepted=A8("these are real trait implementations; Rust has no class inheritance"),
    ),
    F(
        "L-125",
        "Polish",
        "Raw tree-sitter kinds are exposed (class_declaration, type_spec), and internal namespaces are indexed "
        "(pdf/cpp foundation::, aspose.page.mcp.handlers)",
        sources="rv4 Polish",
        accepted=A8("parser node names are shown as-is; the namespaces are declared public in the source"),
    ),
    F(
        "L-126",
        "Polish",
        "Java getters are synthesised as pseudo-properties beside the methods",
        sources="rv2 Polish",
        accepted=A8("a documented convenience view; the getter method stays listed"),
    ),
    F(
        "L-127",
        "Polish",
        "list_members returns one flat list with no member-kind labels, and lookup has 4 response shapes with no "
        "discriminator",
        sources="QA 8; QA 13",
        accepted=A8("clients tell the shapes apart by their fields; a kind field is planned later"),
    ),
    F(
        "L-128",
        "Polish",
        "Duplicate doc chunks appear in results",
        sources="rv1 Polish",
        accepted=A8("no two indexed chunks have identical text; long sections split into same-title chunks"),
    ),
    F(
        "L-129",
        "Polish",
        "Re-`initialize` on an existing session is accepted",
        sources="rv2 Minor",
        accepted=A8("MCP SDK behaviour; it replaces only that session's client information"),
    ),
    F(
        "L-130",
        "Polish",
        "imaging/net serves only 3 types",
        sources="audit A A-014; rv1 data gaps",
        accepted=A8(
            "the fixture is untruncated (`type_count` 3, `truncated` false): the library's real public "
            "surface",
            kind=NOT_DEFECT,
        ),
    ),
    F(
        "L-131",
        "Polish",
        "False alarms: .NET license NotAvailable (A-016), rollback.py with no callers (A-017), concurrency "
        '"differing answers" (rv2)',
        sources="audit A A-016, A-017; rv2 false alarm",
        accepted=A8(
            "pdf/net's license is MIT in its csproj; infra/rollback.py is an operator CLI covered by "
            "tests/infra/test_rollback.py; rv2 determinism.py shows identical answers",
            kind=NOT_DEFECT,
        ),
    ),
    F(
        "L-132",
        "Polish",
        "`gatectl validate` failed during audit C (state.yaml vs a fresh rebuild, with receipts in flight)",
        sources="audit C C-035",
        accepted=A8(
            "transient: `gatectl validate` passes on main and is re-run at gate exit", kind=NOT_DEFECT
        ),
    ),
    # --- Card-mandated policy row ---------------------------------------------
    F(
        "L-133",
        "Minor",
        "The C++ verifier puts include/aspose_jmap on the include path, so jmap/cpp's README `#include "
        '"JmapClient.hpp"` compiles although the library\'s documented CMake target would not find it',
        "TC-455",
        check="TC-455 verifier tests",
        sources="TC-455 policy decision (example diagnosis class E)",
        accepted=A8(
            "the candidate still compiles and links against the real pinned library, and only the page's "
            "own `#include` lines are ever added; the published example keeps the README's code"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Repository inputs
# ---------------------------------------------------------------------------
def find_repo(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "project" / "state.yaml").is_file() and (parent / "ops" / "gatectl.py").is_file():
            return parent
    return None


def load_state(repo: Path) -> dict:
    with open(repo / "project" / "state.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def release_sections(repo: Path) -> dict[str, set[str]]:
    """Bold ids per numbered section of docs/RELEASE.md: {"8": {"L-089", ...}, "9": {"TC-397", ...}}."""
    out: dict[str, set[str]] = {}
    path = repo / "docs" / "RELEASE.md"
    if not path.is_file():
        return out
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## (\d+)\.", line)
        if m:
            current = m.group(1)
            out.setdefault(current, set())
            continue
        if line.startswith("## "):
            current = None
            continue
        if current:
            out[current].update(re.findall(r"\*\*((?:L|TC)-\d{3}[a-z]?)\*\*", line))
    return out


def receipt_time(repo: Path, card: dict) -> datetime | None:
    rec = card.get("receipt")
    if not rec:
        return None
    path = repo / "evidence" / "build" / rec
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))  # JSON is YAML
        return datetime.fromisoformat(str(data["generated_at"]).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001 - a missing or odd receipt means "unknown"
        return None


def last_data_commit(repo: Path, kind: str) -> tuple[str, datetime] | None:
    paths = DATA_PATHS.get(kind)
    if not paths:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%H %cI %s", "--", *paths],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    if not out:
        return None
    full, iso, subject = out.split(" ", 2)
    short = full[:8]
    tc = re.search(r"\((?:G\d+/)?(TC-\d+[a-z]?)\)", subject)
    label = f"{short} ({tc.group(1)})" if tc else short
    return label, datetime.fromisoformat(iso).astimezone(UTC)


def record_ok(repo: Path, rec: Record) -> bool:
    path = repo / rec.path
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if rec.regex is not None:
        return re.search(rec.regex, text, flags=re.MULTILINE) is not None
    if rec.yaml_key is not None:
        value = (yaml.safe_load(text) or {}).get(rec.yaml_key)
        return isinstance(value, list) and len(value) > 0
    return False


# ---------------------------------------------------------------------------
# Status computation
# ---------------------------------------------------------------------------
@dataclass
class Row:
    f: Finding
    status: str
    evidence: str
    pending: list[tuple[str, str]] = field(default_factory=list)
    awaiting_data: bool = False
    release_missing: list[str] = field(default_factory=list)


def card_list(ids, statuses) -> str:
    return ", ".join(f"{c} ({statuses.get(c, 'UNKNOWN')})" for c in ids)


# A SUPERSEDED card's work was fully carried by its accepted successor, exactly the precedent
# ops/gatecli.py's own `_accepted_or_superseded_cards()` already follows for the commit-guard and
# the source-ref-staleness check. The ledger treats a finding's closing card list the same way: a
# card in either status is done, never permanently pending just because its own id was superseded.
CLOSED_STATUSES = frozenset({"ACCEPTED", "SUPERSEDED"})


def compute(repo: Path, require_data: bool) -> tuple[list[Row], dict]:
    state = load_state(repo)
    cards = {c["id"]: c for c in state.get("cards", [])}
    statuses = {cid: c.get("status", "UNKNOWN") for cid, c in cards.items()}
    sections = release_sections(repo)
    data_commits = {k: last_data_commit(repo, k) for k in DATA_PATHS}
    rows: list[Row] = []
    for f in FINDINGS:
        pending = [(c, statuses.get(c, "UNKNOWN")) for c in f.cards if statuses.get(c) not in CLOSED_STATUSES]
        done = [c for c in f.cards if statuses.get(c) in CLOSED_STATUSES]
        rec_ok = record_ok(repo, f.record) if f.record else True

        # Has the baked data been refreshed after every fixing card was accepted?
        awaiting = False
        data_label = ""
        if f.effect in DATA_PATHS and f.cards and not pending:
            dc = data_commits.get(f.effect)
            times = [receipt_time(repo, cards[c]) for c in f.cards if c in cards]
            latest = max((t for t in times if t is not None), default=None)
            if dc is None or latest is None or dc[1] < latest:
                awaiting = True
                data_label = dc[0] if dc else "unknown"

        # Release-document reference for an accepted decision.
        missing: list[str] = []
        if f.accepted:
            if f.accepted.section == "9":
                missing = [c for c in f.accepted.cards if c not in sections.get("9", set())]
            elif f.id not in sections.get("8", set()):
                missing = [f.id]

        parts: list[str] = []
        if pending or not rec_ok or (awaiting and require_data):
            status = "OPEN"
        elif f.accepted:
            status = "ACCEPTED"
        elif f.gap:
            status = "OPEN"
        elif f.cards or f.record:
            status = "FIXED"
        else:
            status = "OPEN"

        if status == "FIXED":
            if f.cards:
                parts.append(f"Fixed by {card_list(f.cards, statuses)}")
            if f.record:
                parts.append(f"Fixed by the {f.record.label}")
            if f.note:
                parts.append(f.note)
            if awaiting:
                parts.append(
                    f"Not yet in the baked data: infra/demo-manifests was last refreshed at {data_label}, "
                    f"before the fix was accepted; it reaches the image with {DATA_WORDS[f.effect]}"
                )
            elif f.effect:
                parts.append(f"Reaches the image with {DATA_WORDS[f.effect]}")
            parts.append(f"Check on the final image: {f.check or 'the committed tests of the fixing cards'}")
        elif status == "ACCEPTED":
            kind = "Not a defect" if f.accepted.kind == NOT_DEFECT else "Accepted"
            parts.append(f"{kind}: {f.accepted.reason}")
            if f.accepted.section == "9":
                parts.append(
                    "See docs/RELEASE.md section 9 (" + ", ".join(f"**{c}**" for c in f.accepted.cards) + ")"
                )
            else:
                parts.append(f"See docs/RELEASE.md section 8 (**{f.id}**)")
            if done:
                parts.append(f"Fixed part: {card_list(done, statuses)}")
                if awaiting:
                    parts.append(
                        f"The fixed part is not yet in the baked data (infra/demo-manifests last refreshed "
                        f"at {data_label}); it reaches the image with {DATA_WORDS[f.effect]}"
                    )
            if f.note:
                parts.append(f.note)
            if f.check:
                parts.append(f"Check: {f.check}")
        else:  # OPEN
            if pending:
                parts.append(f"Waiting for {card_list([c for c, _ in pending], statuses)}")
            if done:
                parts.append(f"Done: {card_list(done, statuses)}")
            if not rec_ok and f.record:
                parts.append(f"Waiting for the {f.record.label}")
            if awaiting and require_data and not pending:
                parts.append(
                    f"Waiting for {DATA_WORDS[f.effect]} (infra/demo-manifests last refreshed at "
                    f"{data_label})"
                )
            if f.gap and not f.accepted:
                parts.append(f"Not covered: {f.gap}")
            if f.accepted:
                ref = (
                    "section 9 (" + ", ".join(f.accepted.cards) + ")"
                    if f.accepted.section == "9"
                    else "section 8"
                )
                parts.append(
                    f"Accepted part, final once the cards above are ACCEPTED: {f.accepted.reason} "
                    f"(docs/RELEASE.md {ref})"
                )
            if not f.cards and not f.record and not f.accepted:
                parts.append("No card and no recorded decision")
            if f.note:
                parts.append(f.note)
            if f.check:
                parts.append(f"Check: {f.check}")
        if f.sources:
            parts.append(f"Sources: {f.sources}")
        evidence = " ".join(p[0].upper() + p[1:] + ("" if p.endswith(".") else ".") for p in parts)
        rows.append(Row(f, status, evidence, pending, awaiting, missing if status == "ACCEPTED" else []))
    meta = {
        "generated_at": state.get("generated_at", "unknown"),
        "data_commits": data_commits,
        "sections": sections,
    }
    return rows, meta


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(rows: list[Row], meta: dict, date: str, image_revision: str) -> str:
    gen = meta["generated_at"]
    counts = {s: sum(1 for r in rows if r.status == s) for s in STATUSES}
    lines = [
        "# Review ledger",
        "",
        f"Date: {date}. Image revision: {image_revision}. Statuses computed from `project/state.yaml` "
        f"(generated {gen}) and `docs/RELEASE.md`.",
        "",
        "This ledger lists every finding of the four raw adversarial review rounds "
        "(`review/raw-adversral-review.md`), the regression audits A, B and C, the four independent re-runs of the "
        "review prompts and the peer QA run, plus one policy limitation a card requires here. "
        "**FIXED** means every card that fixes the finding is ACCEPTED, or superseded by one that is (its work "
        "fully carried by the successor), in `project/state.yaml`; the Evidence names "
        "those cards and the check that must pass on the final image, and says so while a fix that changes baked "
        "data is not yet in `infra/demo-manifests`. **ACCEPTED** means this release knowingly ships the finding; "
        "the reason is in the Evidence and is repeated in `docs/RELEASE.md`, section 8 by ledger id or section 9 "
        "by the deferred card id. **OPEN** means a card or a decision is still pending, and the Evidence names it. "
        f"The ledger is generated by `{SCRIPT_NAME}` from data in that script, `project/state.yaml` and "
        "`docs/RELEASE.md`; do not edit it by hand, re-run the script.",
        "",
        f"Totals: {len(rows)} findings, {counts['FIXED']} FIXED, {counts['ACCEPTED']} ACCEPTED, "
        f"{counts['OPEN']} OPEN.",
        "",
        "Keys used in the Evidence column:",
        "",
        "- Severity is the highest any source gave: P0 or blocker is Blocker, P1 or major is Major, P2 or minor is "
        "Minor, P3 or polish is Polish.",
        "- Sources: raw R1 to R4 are the four rounds of the raw review; audit A, B and C are the regression audits "
        "(new defects cited as N-n); rv1 to rv4 are the re-runs of review prompts 1 to 4 (B blocker, M major, "
        "m minor); QA n is item n of the peer QA report.",
        "- Checks: rv1 Cnn/Enn, rv2 by name, rv3 Inn/Dnn/Lnn/S01/R0n/X01, rv4 DAT-Fnn/Lnn/Pnn and qa (area) are the "
        "review harnesses, re-run against the final image; a path is a committed test or script.",
        "",
        "| ID | Finding | Severity | Status | Evidence |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r.f.id} | {cell(r.f.text)} | {r.f.severity} | {r.status} | {cell(r.evidence)} |")
    lines.append("")
    return "\n".join(lines)


def validate_data() -> list[str]:
    errors = []
    seen = set()
    for f in FINDINGS:
        if f.id in seen:
            errors.append(f"duplicate id {f.id}")
        seen.add(f.id)
        if f.severity not in SEVERITIES:
            errors.append(f"{f.id}: bad severity {f.severity}")
        for c in f.cards:
            if not re.fullmatch(r"TC-\d{3}[a-z]?", c):
                errors.append(f"{f.id}: bad card id {c}")
        if f.accepted and f.accepted.section == "9" and not f.accepted.cards:
            errors.append(f"{f.id}: a section-9 acceptance must name the deferred cards")
        if f.record and not (f.record.regex or f.record.yaml_key):
            errors.append(f"{f.id}: a record needs a regex or a yaml_key")
    ids = [int(f.id[2:]) for f in FINDINGS]
    if ids != list(range(1, len(ids) + 1)):
        errors.append("ids are not L-001.. in order without gaps")
    return errors


def post_checks(rows: list[Row]) -> list[str]:
    warnings = []
    for r in rows:
        if r.status == "FIXED" and not re.search(r"TC-\d{3}", r.evidence):
            warnings.append(f"{r.f.id}: FIXED row names no TC- card id")
    missing = [(r.f.id, m) for r in rows for m in r.release_missing]
    if missing:
        warnings.append(
            "ACCEPTED rows not found in docs/RELEASE.md: "
            + ", ".join(
                f"{fid} (needs **{m}** in section {'9' if m.startswith('TC-') else '8'})"
                for fid, m in missing
            )
        )
    return warnings


def check_report(rows: list[Row], meta: dict, require_data: bool) -> str:
    out = []
    counts = {s: sum(1 for r in rows if r.status == s) for s in STATUSES}
    out.append(f"state.yaml generated_at {meta['generated_at']}; require_data={require_data}")
    for kind, dc in meta["data_commits"].items():
        out.append(f"last {kind} data commit: {dc[0] + ' ' + dc[1].isoformat() if dc else 'none'}")
    out.append(f"total {len(rows)}: " + ", ".join(f"{s} {counts[s]}" for s in STATUSES))
    out.append("")
    header = f"{'severity':<9}" + "".join(f"{s:>10}" for s in STATUSES) + f"{'total':>8}"
    out.append(header)
    for sev in SEVERITIES:
        n = {s: sum(1 for r in rows if r.f.severity == sev and r.status == s) for s in STATUSES}
        out.append(f"{sev:<9}" + "".join(f"{n[s]:>10}" for s in STATUSES) + f"{sum(n.values()):>8}")
    out.append("")
    opens = [r for r in rows if r.status == "OPEN"]
    out.append(f"OPEN rows ({len(opens)}):")
    for r in opens:
        why = []
        if r.pending:
            why.append("pending " + ", ".join(f"{c} {s}" for c, s in r.pending))
        if r.f.record and "Waiting for the" in r.evidence:
            why.append(f"needs {r.f.record.label}")
        if r.f.gap and not r.f.accepted and not r.pending:
            why.append("needs a card or an ACCEPT decision for: " + r.f.gap)
        if r.awaiting_data and require_data and not r.pending:
            why.append("needs " + DATA_WORDS[r.f.effect])
        out.append(f"  {r.f.id} [{r.f.severity}] " + "; ".join(why or ["no card and no decision"]))
    stale = [r for r in rows if r.awaiting_data and r.status != "OPEN"]
    out.append("")
    out.append(f"FIXED/ACCEPTED rows whose fix is not yet in the baked data ({len(stale)}):")
    for r in stale:
        out.append(f"  {r.f.id} [{r.status}] {r.f.effect}: {', '.join(r.f.cards)}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", nargs="?", help="write the markdown here instead of stdout")
    ap.add_argument("--repo", help="repository root (default: found from this file)")
    ap.add_argument("--check", action="store_true", help="print counts and the OPEN list instead of markdown")
    ap.add_argument(
        "--require-data",
        action="store_true",
        help="keep a fix that changes baked data OPEN until infra/demo-manifests is refreshed after it",
    )
    ap.add_argument("--date", help="ledger date (default: the date of project/state.yaml generated_at)")
    ap.add_argument("--image-revision", default=IMAGE_REVISION_PLACEHOLDER)
    args = ap.parse_args(argv)

    errors = validate_data()
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2
    repo = find_repo(args.repo)
    if repo is None:
        print("ERROR: repository root not found; pass --repo", file=sys.stderr)
        return 2
    rows, meta = compute(repo, args.require_data)
    for w in post_checks(rows):
        print(f"WARNING: {w}", file=sys.stderr)

    if args.check:
        print(check_report(rows, meta, args.require_data))
        return 0
    date = args.date or str(meta["generated_at"])[:10]
    text = render(rows, meta, date, args.image_revision)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
