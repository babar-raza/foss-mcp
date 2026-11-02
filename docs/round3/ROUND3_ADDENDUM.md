# Round-3 audit addendum — read this FIRST, then read PROTOCOL.md in full

This is **round 3** of the same audit, against a freshly rebuilt deployment:
image `foss-mcp-serving:demo-cac9288` (source commit `cac92881a1efa8bce3c1033ef2d0fac987b51d8e`, built 2026-11-01, gate PASS 32/32). The base URL file (`...\scratchpad\public_url2.txt`, read automatically by `mcpraw.py`) has been updated to the new tunnel. Do not reuse any cached response from before today.

**Your job has two parts, in this order:**

1. **Re-verify your pilot's round-2 findings.** Round 2's full results for your pilot are at
   `...\scratchpad\portfolio2\<family>-<platform>\report.md` and `issues.json` — read both first.
   For every issue there, especially every **P0**, re-run its exact repro command/steps against
   the NEW deployment and record one of: FIXED (repro no longer reproduces, say what changed),
   STILL BROKEN (repro still reproduces, confirm with the same evidence standard as before), or
   CHANGED (behavior is different now but the underlying problem may persist in a new form —
   explain exactly how). Do not assume a fix from a changelog or commit message; only trust your
   own fresh repro.
2. **Check the new `known_defects` field.** Every `get_symbol`/`search_symbols`/`report_index_freshness`
   response now carries a `known_defects` array that did not exist in round 2 — this looks like a
   response to this audit's HANDOFF.md recommendation #4 (surface known upstream library bugs to
   the caller). For every round-2 issue on your pilot categorized `upstream-library-bug`, check
   whether `known_defects` is now populated for the affected symbol/section, and whether its content
   is accurate (names the real defect, not a vague placeholder). If your pilot had zero
   `upstream-library-bug` issues in round 2, check anyway: find_examples/search_symbols a symbol
   touched by any upstream bug you discover fresh this round, and see if `known_defects` picks it up.
3. **Then run the full protocol battery (T1-T10) exactly as `PROTOCOL.md` describes**, going for
   new issues beyond round 2, not just confirming old ones. Round 2 already established your
   pilot's symbol-accuracy baseline and ground-truth method (noted in its report.md) — reuse that
   ground truth where it's still valid (same indexed commit = same symbols are still ground truth;
   only re-derive ground truth if the install section reports a different version/commit now) to
   save time for genuinely new testing (find_examples coverage, product-reference accuracy,
   honesty, the new scenarios list in PROTOCOL.md, cross-pilot).

**This is a SOLO assignment** — you own this one pilot for the full run with full budget, same as round 2.

**Deliverables**, in `...\scratchpad\portfolio3\<family>-<platform>\` (PILOTDIR):
- `report.md` and `issues.json` in the same shape PROTOCOL.md specifies, PLUS a new top section
  titled `## Round-2 reconciliation` listing every round-2 issue ID for this pilot with its
  FIXED/STILL BROKEN/CHANGED verdict and fresh evidence.
- If the Write tool refuses `report.md` ("Subagents should return findings as text, not write
  report files"), do NOT work around it with Bash or any other tool — hand back the full content
  as text in your final reply, clearly marked, for the supervisor to save. This is a hard rule,
  not a suggestion: two round-2 agents who had not been told this went around the restriction
  before it was made explicit, and that was flagged as a problem afterward.
- Re-issue every issue ID from round 2 as the SAME ID if it's still open (so the fixing agent can
  track it across rounds), and new IDs for genuinely new findings.

**Scope/authorization carried over from round 2** (same as before, not re-asked): you MAY clone
and build/run upstream source for any pilot where round 2 already did so under explicit user
authorization (the C++ family: cells/cpp, email/cpp, pdf/cpp, slides/cpp; and the four
previously-source-only Python/JS pilots: tex/python, barcode/python, html/python, imaging/net).
For every other pilot, do not attempt a clone-and-build route unless the install section
prescribes exactly that AND it's the same authorization round 2 already used (reuse round 2's
clone read-only where present instead of re-cloning). If in doubt, treat it as NOT authorized and
say so rather than guessing — do not claim a authorization the user did not give.
