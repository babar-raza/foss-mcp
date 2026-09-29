# foss-mcp

MCP (Model Context Protocol) servers for Aspose's open-source ("FOSS") product line —
independently-addressable endpoints, one per product family/platform combination (e.g.
`pdf/net`, `slides/python`), so an AI coding assistant can look up real API surface,
documentation, and verified examples for the specific FOSS library a developer is using.

This is a from-scratch design, not a fork of Aspose's internal commercial-product retrieval
system ("ADCS"). ADCS was audited first; every one of its confirmed defects (scope widening on
a miss, client-controlled tenant headers, generation identity that doesn't survive rollback,
promotional content mixed into technical answers) has a corresponding design rule here that
does the opposite. See `docs/DECISION_LOG.md` for dated decisions and
`docs/REPOSITORY_LAYOUT.md` for where things live.

## How it works

```
FOSS GitHub repo ──┐
                    ├─▶ extraction ─▶ normalization ─▶ furnished_content ─▶ indexing ─▶ retrieval ─▶ mcp
repository-presenter┤
  sealed README ────┘
```

- **`foss_mcp.extraction`** — self-extracts API surface (classes/methods/signatures) directly
  from each FOSS product's own public GitHub repo via a tree-sitter engine (ported from
  `aspose.org`'s vendored extraction engine), plus repo-native artifacts: the packaging
  manifest (`.csproj`/`pyproject.toml`/`pom.xml`/`go.mod`/`Cargo.toml`/…), `AGENTS.md`/
  `CLAUDE.md`, `CONTRIBUTING.md`, and GitHub Releases.
- **`foss_mcp.normalization` / `foss_mcp.furnished_content`** — turns extracted facts and
  README-derived prose into a canonical, section-chunked document shape.
- **`foss_mcp.indexing`** — builds an immutable, content-addressed **generation** per
  `(family, platform, source_kind)` — lexical + embedding indexes, a citation-validating
  publisher, and compile/run-verified example candidates (real `javac`/`cargo build`/`go build`/
  venv-run checks per ecosystem, not just "the snippet parses").
- **`foss_mcp.retrieval`** and **`foss_mcp.mcp`** — query the published generation and serve it
  over the MCP protocol (official `mcp` SDK, not a hand-rolled envelope).

**Deployment identity is fixed at deploy time, never at request time.** Which product a server
answers for comes entirely from `foss_mcp.mcp.routing.DeploymentConfig` (env vars at container
start); no tool argument or client header can ever change it. A miss is a miss — no tool
silently substitutes a different scope, symbol, or generation for the one that was asked for.

### The three ingestion sources

| Source | What | Trust tier |
|---|---|---|
| Self-extracted | API surface, classes, signatures — parsed directly from the product's own shipped source | Highest — deterministic parse of real code |
| Repo-native | Packaging manifest, `AGENTS.md`, `CONTRIBUTING.md`, GitHub Releases | High for manifests; explicitly typed richness for release notes |
| Furnished content | Task-oriented prose/FAQ, sourced from `repository-presenter`'s locally-sealed README candidates (not a live `aspose.org` fetch — see `docs/DECISION_LOG.md`) | Medium — independently cross-checked against self-extracted symbols before a citation is trusted |

## MCP tools

Every deployment exposes the same nine tools regardless of how much data backs a given
product — thin or missing data surfaces as an honest empty result or explicit
`NotAvailable`/`NotFound`, never a fabricated answer or a widened search.

| Tool | Purpose |
|---|---|
| `lookup` | Forgiving entry point — dispatches to `search_symbols`/`search_docs`/`find_examples` for a query that isn't known to be one or the other |
| `search_docs` | Prose search filtered by `content_type` (`getting_started` \| `developer_guide` \| `troubleshooting` \| `faq`) |
| `search_symbols` | Search the self-extracted API surface by name/purpose |
| `get_symbol` | Exact signature/params/returns for one fully-qualified name, or an explicit 404 |
| `list_members` | Every method/property/enum value a class or namespace declares |
| `find_examples` | Verified snippets only — exact match first, then a semantic fallback; nothing is ever executed by the tool itself |
| `get_product_reference` | `section`-selected: `install`, `formats`, `limitations`, `compatibility`, `license`, `support`, `contributing`, `agent_guidance` |
| `list_recent_changes` | Recent GitHub releases, each carrying an honest `richness: detailed \| templated \| none` verdict |
| `report_index_freshness` | The indexed generation's source revision against the current upstream revision |

## Current status

Tracked in `project/state.yaml` (derived, never hand-authored — `ops/gatectl.py` is the only
writer). As of this writing:

- **G0** (foundation/contracts) and **G1** (first vertical slice, `pdf/net` end-to-end) —
  **accepted**.
- **G2** (the remaining six pilots) — **in progress**. All seven pilots have a product
  manifest under `config/products/` and a `serving-*` container in `docker-compose.yml`; six
  (`pdf/net`, `pdf/typescript`, `pdf/java`, `pdf/go`, `slides/python`, `cells/rust`) also have a
  working one-shot `ingest-*` job that builds real chunks from committed fixtures and publishes
  them through the real citation-validating pipeline. `pdf/cpp` has a serving container defined
  but no ingestion job wired yet.
- The `search_docs` furnished-content pipeline is being re-pointed from placeholder,
  aspose.org-copied fixtures to real README-derived content sourced from
  `repository-presenter`'s sealed candidates (in progress; not yet dispatched to all pilots).
- **Known integration gap**: `get_product_reference` and `list_recent_changes` are implemented
  and tested, but `infra/serve_http.py`/`infra/serve_stdio.py` don't yet pass real
  `ProductReferenceInputs`/release data into `create_server` — both tools currently answer
  `NotAvailable`/empty on every live deployment until that wiring lands, even though the six
  live pilots' symbol/doc/example tools are backed by real, published content.

## What's planned

- **G3** — recovery/rollback drills, cross-instance leakage proof across all 7×6 directed
  pilot pairs, security checks, and a real metrics→dashboard→alert pipeline.
- **G4** — a clean self-hosted setup from generated distribution artifacts with zero
  company-only credentials, plus green hosted CI on the pushed revision.
- **G5** — production Kubernetes hosting, DNS, and per-family repo publication (requires
  authority items not yet resolved — see `project/state.yaml`'s `owner_items`).
- **Post-pilot**: portfolio expansion beyond the 7 pilot product/platform combos (32 usable
  combos identified across 13 families), and a separate, deliberately isolated
  cross-instance catalog service ("which FOSS product handles X") that aggregates only coarse
  public metadata — never full API surface or citation-backed retrieval, to keep per-product
  isolation structural rather than policy.
- **Explicitly deferred, not planned**: known-issue/bug search (26/32 repos have zero GitHub
  issues ever filed — too thin to build on) and repo popularity/stars (no backing data, closer
  to a vanity metric than a developer need).

## Repository layout

See `docs/REPOSITORY_LAYOUT.md` for the authoritative description. Summary:

- `src/foss_mcp/` — the product package (`mcp`, `extraction`, `normalization`,
  `furnished_content`, `indexing`, `retrieval`, `telemetry`, `config`). Never imports from `ops/`.
- `infra/` — container entrypoints (`serve_stdio.py`, `serve_http.py`, `ingest.py`,
  `build_chunks.py`).
- `config/products/<family>/<platform>.yaml` — one manifest per onboarded product.
- `ops/` — this repository's own governed-development tooling (`gatectl`), never imported by
  `src/`.
- `plans/TC-NNN.yaml` — taskcards, each a worker's complete contract for one unit of work.
- `schemas/` — JSON Schemas for every governance artifact.

## Getting started

Requires Python 3.13+.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock   # .venv/bin/python on Linux/macOS

# Local CI-equivalent: lint, format check, mypy (ops/), pytest, gatectl validate
scripts/ci_check.sh
```

### Run a pilot locally

```bash
docker compose up --build ingest-pdf-net        # one-shot: build chunks, publish the generation
docker compose up --build serving               # then serve it (no `depends_on` — run in order)
```

Each pilot's serving container listens on its own host port (container port is always 8080
internally):

| Pilot | Port |
|---|---|
| `pdf/net` | 8080 |
| `pdf/typescript` | 8081 |
| `pdf/cpp` | 8082 (serving only — no ingestion job yet) |
| `pdf/java` | 8083 |
| `pdf/go` | 8084 |
| `slides/python` | 8085 |
| `cells/rust` | 8086 |

`GET /readyz` reports 200 only once that deployment's own scope has a real active generation
published — never bare process reachability.

### Connect an MCP client

- **stdio**: `python infra/serve_stdio.py` (reads `FOSS_MCP_FAMILY`/`FOSS_MCP_PLATFORM`/
  `FOSS_MCP_SOURCE_KIND` from the environment; defaults to `pdf`/`net`/`self_extracted`).
- **HTTP (Streamable HTTP)**: point a client at `http://localhost:<port>/mcp` for a running
  `serving-*` container above.

## Governance and contributing

This repository governs its own development per `AGENTS.md` (authoritative — read it before
making any change): a supervisor authors taskcards and negative controls, workers implement
exactly one taskcard each inside its declared `write_paths`, and `ops/gatectl.py` — never a
worker's own report — issues every verdict. Decisions made during execution are recorded in
`docs/DECISION_LOG.md`; open questions that don't block progress are tracked in
`ops/open_questions.jsonl` and resolved at gate exit.

## License

MIT (`pyproject.toml`).
