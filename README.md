# foss-mcp

MCP (Model Context Protocol) servers for Aspose's open-source ("FOSS") product line, so an AI coding assistant can
look up real API surface, documentation and verified examples for the specific FOSS library a developer is using.
One image serves 32 pilots (family and platform pairs such as `pdf/net` or `slides/python`) across 12 product
families: 3d, barcode, cells, email, html, imaging, note, page, pdf, slides, tex and words.

This is a from-scratch design, not a fork of Aspose's internal commercial-product retrieval system ("ADCS"). ADCS was
audited first; every one of its confirmed defects (scope widening on a miss, client-controlled tenant headers,
generation identity that doesn't survive rollback, promotional content mixed into technical answers) has a
corresponding design rule here that does the opposite. See `docs/DECISION_LOG.md` for dated decisions and
`docs/REPOSITORY_LAYOUT.md` for where things live.

## Run it

The standalone image bakes in the data of every pilot, so you need only Docker. One container serves exactly one
pilot. The commands below are from `docs/RELEASE.md` section 7; the registry host is a placeholder.

```bash
docker pull registry.example.internal:5000/foss-mcp/foss-mcp-serving@sha256:<digest>
docker run --rm <image> --list
docker run -d --rm --read-only --tmpfs /tmp --memory 256m --cpus 0.5 -p 127.0.0.1:8080:8080 -e FOSS_MCP_PILOT=pdf/net <image>
```

`--list` prints the pilots baked into the image; pick one for `FOSS_MCP_PILOT` (`family/platform`). Wait for
`http://127.0.0.1:8080/readyz` to answer 200, then point an MCP client at `http://127.0.0.1:8080/mcp` (Streamable
HTTP). `/healthz` answers liveness and `/metrics` answers Prometheus text. Start one container per pilot you need,
each on its own host port.

To start a whole family, use `docker-compose.demo.yml`. Set `FOSS_MCP_DEMO_IMAGE` to the image, then start services
by name or by profile (every family has one, and `all` starts all 32):

```bash
export FOSS_MCP_DEMO_IMAGE=<image>
docker compose -f docker-compose.demo.yml up -d pdf-net slides-python
docker compose -f docker-compose.demo.yml --profile pdf up -d
```

There is one service per pilot, published on loopback ports 8101 to 8132; the header of the compose file lists which
pilot has which port.

## Security

The server has no authentication and no TLS. It speaks plain HTTP, so keep it on a loopback address or a trusted
network. Browser Origins are rejected unless they are listed in `FOSS_MCP_ALLOWED_ORIGINS`. Put a reverse proxy in
front of it if you need TLS or authentication.

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

## The nine tools

Every pilot exposes the same nine tools. Thin or missing data surfaces as an honest empty result or an explicit
`NotAvailable` or `NotFound`, never as a fabricated answer or a widened search.

| Tool | Purpose |
|---|---|
| `lookup` | Forgiving entry point: tries documentation, symbols and verified examples for a question that is not known to be one or the other |
| `search_docs` | Prose search inside one documentation category (`getting_started`, `developer_guide`, `troubleshooting` or `faq`) |
| `search_symbols` | Search the self-extracted API surface by type name or fragment, or by the name of a member |
| `get_symbol` | Exact signature, parameters and returns for one fully-qualified name, or an explicit miss |
| `list_members` | Every method, property and enum value that a class or namespace declares |
| `find_examples` | Verified examples only; nothing is ever executed by the tool itself |
| `get_product_reference` | One section per call: install, formats, limitations, compatibility, license, support, contributing or agent_guidance |
| `list_recent_changes` | Recent GitHub releases, each with an honest `richness` verdict |
| `report_index_freshness` | The indexed commit against upstream, how many types are indexed, and the example search mode |

## What to know before relying on it

This image is a release candidate for peer testing. The independent review of it is not complete.

- Every pilot indexes the whole public surface the extractor found at its pinned commit: pdf/java, for example, holds
  1,239 of 1,239 types, and pdf/typescript 1,333 of 1,333 types and 3,060 of 3,060 functions. Symbol chunks carry an
  `API-Coverage:` line that states it, and a miss carries a `coverage_note` only when an index is partial. The index
  holds the public surface only, so a miss does not prove a symbol is absent upstream.
- Several extractor and documentation improvements were deferred. Missing members, dropped varargs parameters, missing
  `static` marks and missing parameter documentation are not flagged by any response, so read the library's source or
  README for those.
- `docs/RELEASE.md` section 8 lists every accepted limitation, section 9 lists the deferred work and what a peer will
  notice, and `docs/REVIEW_LEDGER.md` is the full ledger of review findings.
- `mcp-gateway` and the Kubernetes install are not part of the release. `docs/DEPLOYMENT.md` is the cluster runbook.

## Repository layout

See `docs/REPOSITORY_LAYOUT.md` for the authoritative description. Summary:

- `src/foss_mcp/` - the product package (`mcp`, `extraction`, `normalization`, `furnished_content`, `indexing`,
  `retrieval`, `telemetry`, `config`). Never imports from `ops/`.
- `infra/` - container entrypoints and the Helm chart.
- `config/products/<family>/<platform>.yaml` - one manifest per onboarded product.
- `ops/` - this repository's own governed-development tooling (`gatectl`), never imported by `src/`.
- `plans/TC-NNN.yaml` - taskcards, each a worker's complete contract for one unit of work.
- `schemas/` - JSON Schemas for every governance artifact.

Development needs Python 3.13+: create a venv, install `requirements.lock`, and run `scripts/ci_check.sh`.

## Governance and contributing

This repository governs its own development per `AGENTS.md` (authoritative — read it before
making any change): a supervisor authors taskcards and negative controls, workers implement
exactly one taskcard each inside its declared `write_paths`, and `ops/gatectl.py` — never a
worker's own report — issues every verdict. Decisions made during execution are recorded in
`docs/DECISION_LOG.md`; open questions that don't block progress are tracked in
`ops/open_questions.jsonl` and resolved at gate exit.

## License

MIT (`pyproject.toml`).
