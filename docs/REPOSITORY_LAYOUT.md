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

## The `ops/` rule

`ops/` is supervisor tooling for this repository's own governed development
process (`gatectl`, taskcard/state/schema machinery, CI-adjacent scripts). It
is never imported by anything under `src/`. Product code that needs
configuration, logging, or any other cross-cutting facility gets it from
`foss_mcp.config` or `foss_mcp.telemetry`, never from `ops/`. This keeps the
product installable and runnable independently of the governance scaffolding
that builds it.
