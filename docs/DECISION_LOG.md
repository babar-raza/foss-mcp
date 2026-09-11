# Decision Log

This file owns decisions made during execution (see `AGENTS.md`). Entries are
append-only: never rewrite or remove a past entry, only add new ones below it.

## 2026-09-10 — Storage topology decision (TC-004, REQ-G0-006)
Decision: **embedded** (in-process index) for product pdf/net.
Measured via `foss_mcp.indexing.topology_spike`, one common interface, 15-doc fixture corpus, 30 labelled queries, top_k=5:
embedded p95=0.058ms relevance=1.00 (30/30); service_shaped p95=0.692ms relevance=1.00 (30/30).
Rule (plan 18.1): keep the smaller-footprint profile (embedded) unless relevance is >10% worse or p95 is >2x the other's; neither held (0% relevance gap, 0.08x p95 ratio), so embedded is chosen.

## 2026-09-10 — Repo-wide lint was not gated by any card (supervisor note)
Gap: card checks gate acceptance, but no card required repo-wide `ruff check`/`format`,
so worker-written `src/` drifted style-wise while every card stayed green.
This is the "checks quietly stop running" failure mode the design cites (RC5/S5).
Applied now: `ruff check --fix` + `ruff format` — tool-applied, no semantic change; 117 tests
pass before and after. Structural fix for G1: repo-wide lint/format joins the gate-exit criteria
rather than relying on any card to remember it.

## 2026-09-10 — tree-sitter dependency surface pinned (supervisor, pre-TC-010)
Pinned exactly per the plan: tree-sitter==0.26.0, tree-sitter-c-sharp==0.23.5,
tree-sitter-language-pack==1.16.1. Lockfiles are coordinator-owned, so this is a
supervisor action rather than a card.
Observation, recorded rather than asserted: the plan documents `get_parser("c_sharp")`
(underscored) raising DownloadError. It did NOT reproduce in this environment — it returned a
parser. The failure may still be real on a cold grammar cache or without network. Production must
still use the pack's own spelling `"csharp"`, and TC-010 carries a test pinning the spelling
actually used, which protects us either way.

## 2026-09-11 — TC-011 fixture independently authenticated (supervisor)
The mechanical checks cannot tell real extraction from a fabricated fixture, so this was verified
by hand against upstream. source_commit b717287 is a real commit (Release v26.9.0, 2026-09-01) and
the current head of main. The fixture's first entry cites src/Collections/FileSpecificationDeps.cs
line 7; that exact file at that exact commit has `public enum AFRelationship` on line 7, with the
doc summary matching verbatim including the PDF 2.0 SS7.11.3 citation and the enum members in the
same order. 300 types carry 1,724 methods, 3,026 properties, 523 enum members. Genuine extraction.

## 2026-09-11 — Holdout checks introduced after TC-012 passed while failing its requirement
TC-012 was accepted, then revoked. Its 8 tests passed twice, its falsifier bit, scope was clean -
and classify_richness called auto-generated boilerplate 'detailed', because it measured formatting
rather than information. Invisible from inside the card: the fixtures and the classifier came from
one worker and agreed with each other.
Control added: supervisor-authored holdout tests under evidence/holdout/<card>/, globally denied to
every card, injected into the throwaway worktree at verification. Falsifiers prove a suite
exercises its code; only holdouts prove the code is right.
Attempt 2 fixed it on principle (change-verb AND a specific referent, fenced blocks stripped), with
no repo names in the logic. Verified independently on four repos neither side used: 3d-java's bare
"Full Changelog" link -> templated, cells-rust -> detailed, empty pdf-ts -> none, slides-py ->
detailed. Generalises; not tuned to the oracle.

## 2026-09-11 — OQ-001 resolved: unverifiable counts fail closed (TC-014)
Resolved as designed, not by settling the number. The furnished page's "805 classes" now returns
insufficient_evidence, because known_counts_from_fixture only trusts a per-kind count when the
fixture's truncated flag is false - and TC-011's is deliberately reduced. Independently probed for
vacuity: 899 types -> supported, 12345 types -> unsupported, 805 classes -> insufficient_evidence.
All three verdicts reachable and distinct, so the mechanism distinguishes WRONG from
NOT-CORROBORATED rather than shrugging at everything. Whether 805 is actually correct remains
unknown and is correctly reported as unknown. A holdout pins all of this.

## 2026-09-11 — Missing or unparseable MCP protocolVersion is rejected, not defaulted (TC-016)
Decision made by the worker on TC-016 and recorded here because docs/ is outside its write_paths.
The reference system treated a missing requested_revision as a non-fallback case and quietly
negotiated to its own primary revision. Per the MCP spec, initialize's protocolVersion is REQUIRED,
so a request without it is malformed rather than a permissive default: it raises
MissingProtocolVersionError. The same reasoning extends to an unparseable (non-YYYY-MM-DD) string,
which raises InvalidRevisionFormatError. The nearest-supported-or-min ALGORITHM is ported
structurally from the reference system; this permissiveness deliberately is not. Plan 11.3 required
this be an explicit decision rather than a silent inheritance.

## 2026-09-11 — MCP SDK pinned (supervisor, post-TC-016)
mcp==2.2.0 pinned with hashes. The worker hit the coordinator-owned boundary correctly: it did not
touch requirements.*, built a disposable venv OUTSIDE the project to verify server.py against the
real 2.2.0 API line by line, deleted it, and reported BLOCKED_EXTERNAL. Consequence worth noting -
TC-016's own checks exercise only routing and negotiation, neither of which needs the SDK, so
server.py was never imported by anything and the card could have shipped a file that does not load.
Its holdout now imports and constructs it.

## 2026-09-11 — Known limitation: section headings are dropped before content_type classification
Found while writing TC-017a's holdout. chunk_document moves a heading into Chunk.section_title,
build_lexical_index stores only chunk.text, and search_docs classifies document["text"] - so a
section headed "Troubleshooting" whose body never uses a category keyword classifies as
developer_guide. The heading is the strongest available signal and it is discarded.
Measured before judging: on the REAL furnished pdf/net page, 0 of 8 sections change classification
when the heading is included, because that page's headings are "Overview"/"Features" and carry no
category keywords either. So this is latent, not live, and TC-017a is not failed for it.
It WILL matter when kb.aspose.org troubleshooting and FAQ content is ingested, where headings are
the category. Fix then: classify over section_title + text, or carry section_title into the indexed
document. Recorded so it is a decision rather than an oversight.

## 2026-09-11 — requirements.lock was Windows-only; recompiled universally with uv
Found by TC-018's first real docker build, three cards after the lock was written. mcp declares
`pywin32>=311; sys_platform == 'win32'`, but pip-compile - run on this Windows host - flattened the
marker into an unconditional pin, and the lock carried ZERO environment markers anywhere. Every
Linux image died on `pip install --require-hashes`: no pywin32 distribution for linux.
Impact beyond containers: a lock that only resolves on the machine that generated it also defeats
customer self-hosting, which is a stated project requirement, and nothing in CI would have caught
it because CI runs on the same Windows host.
Fix: `uv pip compile --universal --generate-hashes` (uv 0.12.13, added as a build-time tool, not a
runtime dependency). Markers now preserved - 8 marker lines where there were none. This matches the
convention the sibling project already proved. A regression test in ops/tests asserts the lock
carries platform markers and that Windows-only packages are constrained, so recompiling with a tool
that cannot resolve cross-platform fails in the suite rather than in a container.

## 2026-09-11 — Supervisor swept a worker's deliverable into its own commit
`git add -A` while TC-018's work sat uncommitted put eleven product files - both Dockerfiles,
compose, the Helm chart, infra scripts and the test suite - into a supervisor commit whose message
was about a lockfile fix. Provenance corrupted three ways: wrong author, misleading message, and -
worst - the commit carried the supervisor trailer, so changed_paths classified it as governance and
SKIPPED it, meaning that product code would never have been scope-checked.
Caught because the worker reported a commit hash identical to the dispatch revision, which cannot
happen legitimately. Split back out before anything was pushed; the worker commits its own work.
Root cause is the one the design named before execution began: the two roles share one filesystem,
and prose separation does not enforce itself. `gatectl commit-guard` now refuses a supervisor commit
while worker product paths are uncommitted.

## 2026-09-11 — Decomposition defect: nobody was asked to wire the tools into the server (TC-019a)
Found by TC-020, which could not start. TC-016 built the server with a deliberately EMPTY tool
registry (my instruction), TC-017a/b/c implemented nine tool FUNCTIONS, and no card ever connected
them. The container served stdio only, compose published no port, and nothing constructed
TransportSecuritySettings.
All four claims independently verified before acting: on_list_tools returns an empty list; compose
has no ports stanza; mcp 2.2.0's streamable_http falls through to DEFAULT_NEGOTIATED_VERSION when
the MCP-Protocol-Version header is MISSING, so the SDK covers only the present-but-invalid case and
the missing case needs our own middleware; TransportSecuritySettings exposes allowed_origins and
nothing constructs one.
Root cause is mine and structural: I split the plan's single TC-017 ("implement all nine tools")
into three cards scoped to tool implementations, and the integration work fell into the gap between
them. The plan's own card would probably have carried it. Splitting for the 45-minute sizing rule
traded one risk for another, and this is the cost.
TC-019a added with REQ-G1-015 so the transport boundary is a claimed, proven requirement rather
than an assumption inside the E2E card.

## 2026-09-11 — Absent Origin is allowed; present-but-wrong is rejected (TC-019a)
Deliberate decision by the worker, recorded so it cannot drift silently. reject_request rejects an
Origin that is PRESENT and not allowlisted, but does not reject an ABSENT one: non-browser clients
legitimately never send Origin, and DNS rebinding - the threat the check exists for - is a
browser-specific attack. The SDK follows the same convention. A holdout pins both halves, because
the risk is that the leniency quietly widens into allowing a wrong Origin too.
Also recorded: the worker found a real container bug its in-process tests could not - the non-root
user (uid 10001) cannot create a top-level /data at runtime, so the default manifest-store path
failed with PermissionError on the first real request. Found by actually running `docker compose up
--build` and hitting the published port, which is TC-020's job done a card early.
