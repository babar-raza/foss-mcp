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
