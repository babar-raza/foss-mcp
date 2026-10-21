# Repository layout

This document is the authority for where a file lives. When a file's home is
unclear, this document wins over local convention, precedent in another repo,
or a worker's own judgment. It is owned per `AGENTS.md`; only a taskcard whose
`write_paths` names this file may change it.

The product code lives under `src/foss_mcp/`, in a `src` layout, so that the
package can never be imported by accident from the repository root (only an
installed, or explicitly path-mounted, `foss_mcp` is importable). Tests live
under `tests/`, mirroring `src/foss_mcp/` package for package: every
subpackage below has a same-named, same-shaped directory under `tests/`
containing at least an `__init__.py`.

## `foss_mcp` (root — `src/foss_mcp/`)

The top-level package. Holds only what is genuinely cross-cutting: the
package docstring/version marker and re-exports that form the public surface
of the whole library, if any. It does not hold business logic — that belongs
in one of the subpackages below. It never imports from `ops/`.

## Deployment tools (`scripts/deploy/`, `tests/deploy/`)

`scripts/deploy/` holds the deployment tools for the Helm chart at
`infra/helm/foss-mcp`: the pilot release mapping, the installer and the proof
runner. Their offline tests live in `tests/deploy/`. These tools are not product
code and are not imported by `src/`; they call `helm`, `kubectl` and the proof
script `scripts/poc/pilot_workflow_proof.py`. The operator runbook is
`docs/DEPLOYMENT.md`.

## Demo manifests (`infra/demo-manifests/`)

Committed, already-published `GenerationManifestStore` output (one
subdirectory per scope, named `<family>__<platform>__<source_kind>`, matching
`GenerationManifestStore._scope_dir()` in
`src/foss_mcp/indexing/generation_manifest.py`) that `Dockerfile.serving`'s
`demo` stage `COPY`s into `/data/manifests`, making the self-contained demo
image buildable and reproducible from git alone, with no local ingestion run
and no `--build-context` flag required. It is produced by running the
ingestion pipeline (`build_chunks.py` + `ingest.py` + the sidecar scripts)
locally and committing only each scope's `active_pointer.json` and the one
`generation__*.json` file it names — never `lease_state.json`, and never an
orphaned/superseded generation file. It is build-time data, not product code,
and is never imported by `src/`.

## `foss_mcp.mcp` (`src/foss_mcp/mcp/`)

The MCP (Model Context Protocol) server surface: server construction,
transport wiring, request/response glue, and registration of tools with the
protocol runtime. It depends on the packages below to do real work; it does
not itself contain extraction, normalization, indexing, or retrieval logic.

## `foss_mcp.mcp.tools` (`src/foss_mcp/mcp/tools/`)

Individual MCP tool implementations — one tool's request handling per unit of
organization, calling into `extraction`, `retrieval`, etc. for behavior. It
does not define the MCP transport or server lifecycle itself; that is
`foss_mcp.mcp`'s job.

## `foss_mcp.extraction` (`src/foss_mcp/extraction/`)

The source-extraction pipeline: turning raw repository content into
structured facts. It does not perform normalization (canonicalizing shapes)
or indexing (persisting facts for retrieval) — those are separate packages
below, kept separate so extraction can be tested and swapped independently of
what consumes its output.

## `foss_mcp.extraction.tree_sitter_engine` (`src/foss_mcp/extraction/tree_sitter_engine/`)

The tree-sitter-backed parsing engine used by `foss_mcp.extraction`: grammar
loading, parse-tree construction, and syntax-tree queries. It does not decide
what to do with parsed output — that decision belongs to its caller in
`foss_mcp.extraction`. Other, non-tree-sitter extraction backends (if any)
are siblings of this package, not children of it.

## `foss_mcp.furnished_content` (`src/foss_mcp/furnished_content/`)

Assembly of "furnished" (enriched/annotated) content from normalized facts —
the layer that decorates plain extracted/normalized data with the additional
context a consumer needs. It does not do raw extraction or normalization
itself; it consumes their output.

## `foss_mcp.normalization` (`src/foss_mcp/normalization/`)

Normalization of extracted content into a canonical, stable shape that the
rest of the system can depend on regardless of source format or extraction
backend. It does not perform extraction (it consumes `foss_mcp.extraction`'s
output) and it does not index or retrieve.

## `foss_mcp.indexing` (`src/foss_mcp/indexing/`)

Building and maintaining indexes over normalized/furnished content so it can
be searched and retrieved efficiently. It does not implement query-time
ranking or result assembly — that is `foss_mcp.retrieval`'s job — and it does
not perform extraction or normalization itself.

## `foss_mcp.retrieval` (`src/foss_mcp/retrieval/`)

Querying the indexes built by `foss_mcp.indexing` and ranking/assembling
results for a caller (typically an MCP tool). It does not build or maintain
indexes itself.

## `foss_mcp.telemetry` (`src/foss_mcp/telemetry/`)

Logging, metrics, and tracing instrumentation for the product code. It does
not contain business logic; other packages depend on it for observability,
never the other way around.

## `foss_mcp.config` (`src/foss_mcp/config/`)

Configuration loading, validation, and typed access for the product code. It
does not perform I/O beyond reading configuration sources, and it does not
hold runtime state belonging to other packages.

## Round-3 blind-spot review checklist (card TC-566)

docs/ROUND3_REVIEW.md does not itself label any finding "C1" through "C8" (that labelling belongs to
an earlier, 2026-10-08 recon recorded in docs/DECISION_LOG.md, a different review). This checklist
instead groups docs/ROUND3_REVIEW.md's own distinct blind-spot MECHANISMS - the kind of gap, not the
specific pilot it hit - into eight classes, each named by the RD3 row(s) that first proved it real.
A reviewer checking a new card, fixture regeneration or release gate against this list is checking for
the MECHANISM recurring somewhere else, not just the one row already closed.

- **C1 - no independent oracle (self-consistency only).** A test whose expected value comes from the
  project's own extractor output, or from a fixture that output produced, can never see the extractor
  lose or fabricate data (RD3-150: 14,347 of 14,348 chunks rebuilt byte-identically from the committed
  fixtures). Every new regression test's expectation must come from an independent source: real
  upstream source read with the language's own tool, a compiled library's reflection, or a hand-written
  upstream-shaped snippet - never the extractor's own output.
- **C2 - live-content coverage gaps and stale pins.** A live container test that exists is not the
  same as one that still passes (RD3-151: the pdf/typescript e2e pin rotted stale at HEAD) and 7 of 32
  pilots had live coverage at all. A pin naming a commit, a count, or a generation must be re-derived
  (ideally read from the chart, not retyped) whenever the thing it pins moves, and every pilot needs
  its own live assertion, not a sample.
- **C3 - environment-identity assumptions baked into an exact-match assertion.** Asserting a
  configuration value's literal on-disk string (RD3-152: `core.hooksPath` assumed relative) breaks the
  moment a legitimate tool stores an equivalent value differently. Prefer asserting the EFFECT
  (the hook fires, the same content runs) over one specific spelling, or explicitly enumerate every
  legitimate spelling.
- **C4 - environment-sensitive flakiness mistaken for a code defect.** A test that fails only on some
  hosts or some runs (RD3-153: a Windows Application Control policy intermittently blocks freshly
  built Rust executables, `os error 4551`) must be named as environment-sensitive with a precise,
  observed marker before anyone "fixes" the code it never implicated. Never silently skip on ANY
  failure - only on the one, named, observed condition.
- **C5 - weakened or rewritten-to-match assertions.** `git log -p -S` on a test file finds the exact
  commit that loosened a pin to match current behaviour instead of restoring the behaviour the pin
  demanded (RD3-154: W1 to W6, e.g. `EXTRACTOR_VERSION == 2` relaxed to `>= 2`). Any rework that makes
  a check easier to pass, rather than fixing what it checks, is a debt entry, not a fix, and must be
  named in docs/RELEASE.md when it cannot be reversed immediately.
- **C6 - stale documentation claims.** A quoted number or status in docs/RELEASE.md or the decision
  log that nothing re-derives (RD3-155, RD3-164: "about 17,000 methods, 0 mismatches" with no
  reproducible source) decays the moment the data it describes changes. A claim that cannot be
  regenerated from committed evidence is a liability, not a record.
- **C7 - unsupported or stub capability served as normal API.** A symbol that exists in the index but
  throws, is a no-op, or silently writes the wrong thing at runtime (RD3-060: 3d/python's 734
  `NotImplementedError`/`pass` stubs served as ordinary methods) misleads a caller who has no way to
  tell "found" from "found and safe to call". Scope/limitations text must name every such gap the
  extractor can detect (a stub body, a documented "not implemented" note), never leave it to be found
  by trying.
- **C8 - drift between the index and the real, currently-published package.** The index can be either
  ahead or behind what a caller actually installs (RD3-070/RD3-071: `slides/python` documents an
  attribute absent from the published 26.8.0 wheel; `pdf/go`'s index misses a tag `go get` already
  resolves) with freshness reporting `unknown`/`in_index: true` regardless. A freshness or drift check
  must compare against the real, currently-resolvable package truth, not only against what was indexed.

## The `ops/` rule

`ops/` is supervisor tooling for this repository's own governed development
process (`gatectl`, taskcard/state/schema machinery, CI-adjacent scripts). It
is never imported by anything under `src/`. Product code that needs
configuration, logging, or any other cross-cutting facility gets it from
`foss_mcp.config` or `foss_mcp.telemetry`, never from `ops/`. This keeps the
product installable and runnable independently of the governance scaffolding
that builds it.
