"""Bootstrap the MCP server on a maintained SDK - not a hand-rolled envelope.

Uses the official Model Context Protocol Python SDK (PyPI package ``mcp``, pinned in
``requirements.lock`` since TC-016 reported it blocked and the coordinator pinned it).

ONE dispatch core, two transports: ``create_server`` builds the same ``Server`` - the same
tool registry, the same fixed deployment scope - that both ``run_stdio`` (below) and
``infra/serve_http.py`` serve. Neither transport re-implements tool dispatch; only how bytes
reach the process differs.

All nine tools are registered here (``foss_mcp.mcp.tools``), each schema derived from its own
client-facing wrapper's signature - never the underlying tool function's raw signature, which
also takes ``store``/``scope`` that a client must never be able to supply (product identity
comes from deployment config alone: ``foss_mcp.mcp.routing.resolve_scope``, never a tool
argument, per the reference-system defect this project does not inherit).
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import json
import logging
import os
import re
import threading
import time
import types
import typing
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Any

import anyio.to_thread
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestError, GenerationManifestStore
from foss_mcp.mcp import sidecar_state
from foss_mcp.mcp.health import GENERATION_UNAVAILABLE_MESSAGE, GenerationWatch
from foss_mcp.mcp.identity import (
    DeploymentFacts,
    deployment_instructions,
    deployment_title,
    read_deployment_facts,
    render_description,
)
from foss_mcp.mcp.response_budget import apply_budget
from foss_mcp.mcp.response_envelope import (
    NOTE_KEY,
    annotate_result,
    empty_result_note,
    origin_of,
    scope_object,
)
from foss_mcp.mcp.routing import DeploymentConfig, Scope, resolve_scope
from foss_mcp.mcp.sidecar_state import (
    RELEASES_FETCH_FAILED_MESSAGE,
    RELEASES_NOT_LOADED_MESSAGE,
    SIDECAR_FETCH_FAILED,
    SIDECAR_NOT_PRESENT,
    SIDECAR_PRESENT,
    SidecarFetchFailedError,
    SidecarNotLoadedError,
)
from foss_mcp.mcp.tools.find_examples import find_examples
from foss_mcp.mcp.tools.get_product_reference import SECTIONS, ProductReferenceInputs, get_product_reference
from foss_mcp.mcp.tools.get_symbol import get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.mcp.tools.list_recent_changes import list_recent_changes
from foss_mcp.mcp.tools.lookup import lookup
from foss_mcp.mcp.tools.readme_sections import ReadmeSections, read_readme_sections
from foss_mcp.mcp.tools.release_alignment import align_releases, package_version
from foss_mcp.mcp.tools.report_index_freshness import (
    read_generation_commits,
    read_indexed_commits,
    report_index_freshness,
)
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, search_docs
from foss_mcp.mcp.tools.search_symbols import SOURCE_KIND, scope_key, search_symbols
from foss_mcp.normalization.document_schema import SourceKind
from foss_mcp.telemetry.metrics import MetricsRegistry
from foss_mcp.telemetry.usage_recorder import (
    UsageEvent,
    UsageRecorder,
    build_event,
    new_correlation_id,
)

SERVER_NAME = "foss-mcp"
SERVER_BASE_VERSION = "0.1.0"
# The release build (scripts/release/build_images.py) writes the exact commit it built into this
# file next to the package, because nothing inside a running container can read an image label.
BUILD_REVISION_FILENAME = "BUILD_REVISION"
# Set by both Dockerfiles from the FOSS_MCP_BUILD_REVISION build argument (G3/TC-564).
BUILD_REVISION_ENV = "FOSS_MCP_BUILD_REVISION"
# The ``_meta`` key a successful tool result carries its correlation id under (G3/TC-564).
CORRELATION_ID_META_KEY = "correlation_id"
_REVISION_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")

SERVER_INSTRUCTIONS = (
    "This server answers questions about one open-source product's documentation and API; which "
    "product (and platform) is fixed by the deployment, so you never choose it. Start with `lookup` "
    "for task questions. For exact API facts use `search_symbols` to find a name, then `get_symbol` "
    "for its signature and details. Use `find_examples` for verified code. Use "
    "`report_index_freshness` to see what is covered and where it came from. A miss or an empty "
    "result is an honest answer: nothing in the index matched, so do not guess past it."
)

DEFAULT_MANIFEST_ROOT = Path("/data/manifests")


def server_version(revision_file: Path | None = None) -> str:
    """``0.1.0+<first 12 characters of the build revision>`` when the BUILD_REVISION file next to
    the package holds a 40- or 64-character lowercase hex string, else ``0.1.0+unknown``. Never
    raises: a missing, unreadable or malformed file only means the build is unidentified.

    G3/TC-564 (RD3-124): a plain ``docker build`` writes no BUILD_REVISION file, and an image label cannot
    be read from inside the container, so the default lookup falls back to the ``FOSS_MCP_BUILD_REVISION``
    environment variable the Dockerfiles set from a build argument (the same 40- or 64-character hex
    rule). An explicit *revision_file* is read alone, as before."""
    path = (
        revision_file
        if revision_file is not None
        else Path(__file__).resolve().parents[1] / BUILD_REVISION_FILENAME
    )
    try:
        revision = path.read_text(encoding="ascii").strip().lower()
    except (OSError, ValueError):
        revision = ""
    if _REVISION_PATTERN.fullmatch(revision) is None and revision_file is None:
        revision = os.environ.get(BUILD_REVISION_ENV, "").strip().lower()
    if _REVISION_PATTERN.fullmatch(revision) is None:
        return f"{SERVER_BASE_VERSION}+unknown"
    return f"{SERVER_BASE_VERSION}+{revision[:12]}"


def _default_manifest_store() -> GenerationManifestStore:
    return GenerationManifestStore(DEFAULT_MANIFEST_ROOT)


# ---------------------------------------------------------------------
# Client-facing tool wrappers: exactly the arguments a caller may supply.
# Each closes over the server's own fixed store/scope/inputs - never a parameter a client
# request could override.
# ---------------------------------------------------------------------


def _wrap_lookup(store: GenerationManifestStore, scope: Scope) -> Callable[..., Any]:
    def lookup_tool(*, query: str, content_type: str | None = None, top_k: int = 10) -> Any:
        return lookup(store, scope, query, content_type=content_type, top_k=top_k)

    return lookup_tool


def _wrap_search_docs(store: GenerationManifestStore, scope: Scope) -> Callable[..., Any]:
    def search_docs_tool(
        *,
        query: str,
        content_type: str | None = None,
        top_k: int = 10,
        chunk_id: str | None = None,
        offset: int = 0,
    ) -> Any:
        return search_docs(store, scope, query, content_type, top_k=top_k, chunk_id=chunk_id, offset=offset)

    return search_docs_tool


def _wrap_search_symbols(store: GenerationManifestStore, scope: Scope) -> Callable[..., Any]:
    def search_symbols_tool(*, query: str, top_k: int = 10, offset: int = 0) -> Any:
        return search_symbols(store, scope, query, top_k=top_k, offset=offset)

    return search_symbols_tool


def _wrap_get_symbol(store: GenerationManifestStore, scope: Scope) -> Callable[..., Any]:
    def get_symbol_tool(*, fqn: str) -> Any:
        return get_symbol(store, scope, fqn)

    return get_symbol_tool


def _wrap_list_members(store: GenerationManifestStore, scope: Scope) -> Callable[..., Any]:
    def list_members_tool(*, fqn: str) -> Any:
        return list_members(store, scope, fqn)

    return list_members_tool


def _wrap_find_examples(store: GenerationManifestStore, scope: Scope) -> Callable[..., Any]:
    def find_examples_tool(*, query: str, top_k: int = 5) -> Any:
        return find_examples(store, scope, query, top_k=top_k)

    return find_examples_tool


# G3/TC-518: the sections whose answer can use the README sections the deployment's index holds.
_README_SECTIONS = frozenset({"formats", "limitations", "install", "compatibility"})


@dataclasses.dataclass(frozen=True)
class SidecarSnapshot:
    """What the optional sidecars say RIGHT NOW (G3/TC-564): the product-reference inputs, the recent
    releases and the releases sidecar's state. A server built with a fixed snapshot behaves as before; a
    serving process passes a provider that re-reads the files when they change, so a sidecar added later
    is picked up on the next request."""

    product_reference_inputs: ProductReferenceInputs
    recent_releases: Sequence[Release] = ()
    recent_releases_state: str = SIDECAR_PRESENT


SidecarProvider = Callable[[], SidecarSnapshot]


def _hex_prefix_match(commit: str, other: str) -> bool:
    """True when two commit ids are equal or one is an abbreviation (at least 7 characters) of the other."""
    first, second = commit.strip().lower(), other.strip().lower()
    short, long = sorted((first, second), key=len)
    return bool(short) and (short == long or (len(short) >= 7 and long.startswith(short)))


class _ActiveGeneration:
    """The active generation's id and source commits, read cheaply and cached per generation id (G3/TC-564).

    Used for the tool-call log lines (generation id and source commit on every record) and to keep the
    install notice truthful after a rollback. Never raises; an unreadable generation is ``(None, None, ())``."""

    def __init__(self, store: GenerationManifestStore, scope: Scope) -> None:
        self._store = store
        self._scope = scope
        self._key = scope_key(scope, SOURCE_KIND)
        self._lock = threading.Lock()
        self._cached: tuple[str, str | None, tuple[str, ...]] | None = None

    def peek(self) -> tuple[str | None, str | None]:
        """``(generation_id, source_commit)`` for the log line of a call that has just finished. Reads only
        the small pointer file; the commit is the cached one for that generation, ``None`` while it has not
        been read yet (``read`` fills the cache, in a worker thread, once a tool actually reads the
        generation). Never raises."""
        try:
            active = self._store.read_active(self._key)
        except Exception:  # noqa: BLE001 - logging identity must never fail a call.
            return None, None
        if not isinstance(active, str) or not active:
            return None, None
        cached = self._cached
        if cached is not None and cached[0] == active:
            return active, cached[1]
        return active, None

    def read(self) -> tuple[str | None, str | None, tuple[str, ...]]:
        """``(generation_id, indexed_source_commit, every_commit)`` of the active generation."""
        try:
            active = self._store.read_active(self._key)
            if not isinstance(active, str) or not active:
                return None, None, ()
            with self._lock:
                cached = self._cached
            if cached is not None and cached[0] == active:
                return cached
            generation_id, indexed, commits = read_generation_commits(self._store, self._scope)
            if generation_id is None:
                return None, None, ()
            result = (generation_id, indexed, commits)
            with self._lock:
                self._cached = result
            return result
        except Exception:  # noqa: BLE001 - logging identity must never fail a call.
            return None, None, ()

    def install_inputs(self, inputs: ProductReferenceInputs) -> ProductReferenceInputs:
        """*inputs* with ``source_ref`` replaced by the active generation's own source commit when the sidecar's
        commit is not one the generation was built from (a rollback leaves the sidecar on the newer commit).
        The install notice then names the commit the served index really describes."""
        if not inputs.source_ref:
            return inputs
        _, indexed, commits = self.read()
        if indexed is None or any(_hex_prefix_match(commit, inputs.source_ref) for commit in commits):
            return inputs
        return dataclasses.replace(inputs, source_ref=indexed)


def _fixed_sidecars(
    product_reference_inputs: ProductReferenceInputs | None,
    recent_releases: Sequence[Release],
    recent_releases_state: str,
) -> SidecarProvider:
    """A provider that always returns the same snapshot (the server as it was built before TC-564)."""
    snapshot = SidecarSnapshot(
        product_reference_inputs or ProductReferenceInputs(), tuple(recent_releases), recent_releases_state
    )
    return lambda: snapshot


def _live_get_product_reference(
    sidecars: SidecarProvider,
    store: GenerationManifestStore,
    scope: Scope,
    active: _ActiveGeneration | None = None,
) -> Callable[..., Any]:
    """``get_product_reference`` reading its inputs from *sidecars* on every call. With *active* the install
    notice names the commit the ACTIVE generation was built from when the sidecar's commit is not one of
    its commits (G3/TC-564, after a rollback); without it the sidecar is served as it is."""

    def get_product_reference_tool(*, section: str) -> Any:
        inputs = sidecars().product_reference_inputs
        if active is not None:
            inputs = active.install_inputs(inputs)
        # A read of the active generation per call is cheap (the store caches the parsed generation) and
        # never raises: any failure is the empty ReadmeSections, which answers as before.
        readme: ReadmeSections | None = (
            read_readme_sections(store, scope) if section in _README_SECTIONS else None
        )
        return get_product_reference(inputs, section, readme=readme)  # type: ignore[arg-type]

    return get_product_reference_tool


def _live_list_recent_changes(
    sidecars: SidecarProvider, store: GenerationManifestStore, scope: Scope
) -> Callable[..., Any]:
    def list_recent_changes_tool(*, limit: int = 10) -> Any:
        snapshot = sidecars()
        releases, state = snapshot.recent_releases, snapshot.recent_releases_state
        # G3/TC-564 (RD3-120): a releases file that is not on the server at all is not "no releases". The
        # tool function keeps its contract (an empty or absent list answers as a list); the server, which
        # alone knows the file is missing, answers ``unavailable`` while SIDECAR_STATE_WARNINGS is on.
        if state == SIDECAR_NOT_PRESENT and sidecar_state.SIDECAR_STATE_WARNINGS:
            raise SidecarNotLoadedError(RELEASES_NOT_LOADED_MESSAGE)
        # G3/TC-519: mark each release as in the index / newer than the index / unknown, computed per
        # call from the active generation's source commits and the manifest's declared version.
        # A failed fetch raises inside list_recent_changes; no generation is read for it.
        alignment = (
            None
            if state == SIDECAR_FETCH_FAILED
            else align_releases(
                releases,
                indexed_commits=read_indexed_commits(store, scope),
                indexed_version=package_version(snapshot.product_reference_inputs),
            )
        )
        return list_recent_changes(releases, limit=limit, state=state, alignment=alignment)

    return list_recent_changes_tool


def _live_report_index_freshness(
    sidecars: SidecarProvider, store: GenerationManifestStore, scope: Scope
) -> Callable[..., Any]:
    def report_index_freshness_tool(
        *, source_kind: str = "self_extracted", current_source_commit: str | None = None
    ) -> Any:
        snapshot = sidecars()
        inputs = snapshot.product_reference_inputs
        releases = snapshot.recent_releases
        return report_index_freshness(
            store,
            scope,
            source_kind,
            current_source_commit,
            pinned_library_commit=inputs.source_ref,
            product_reference=inputs,
            releases=releases,
        )

    return report_index_freshness_tool


def _wrap_get_product_reference(
    inputs: ProductReferenceInputs, store: GenerationManifestStore, scope: Scope
) -> Callable[..., Any]:
    return _live_get_product_reference(_fixed_sidecars(inputs, (), SIDECAR_PRESENT), store, scope)


def _wrap_list_recent_changes(
    releases: Sequence[Release],
    state: str,
    store: GenerationManifestStore,
    scope: Scope,
    inputs: ProductReferenceInputs,
) -> Callable[..., Any]:
    return _live_list_recent_changes(_fixed_sidecars(inputs, releases, state), store, scope)


def _wrap_report_index_freshness(
    store: GenerationManifestStore,
    scope: Scope,
    inputs: ProductReferenceInputs,
    releases: Sequence[Release],
) -> Callable[..., Any]:
    return _live_report_index_freshness(_fixed_sidecars(inputs, releases, SIDECAR_PRESENT), store, scope)


# ---------------------------------------------------------------------
# Real, agent-facing tool descriptions - grounded in each tool module's own documented
# behavior (see foss_mcp.mcp.tools.*'s own docstrings), never invented and never copied from
# any other server. Each states what the tool does, when to prefer it over a sibling tool,
# concrete real example values, and what not to guess where that matters.
# ---------------------------------------------------------------------

TOOL_DESCRIPTIONS: dict[str, str] = {
    "lookup": (
        "Forgiving one-shot entry point that dispatches to search_docs and/or search_symbols "
        "so you don't have to pick the right index yourself. PREFERRED for a natural-language "
        "task question with no known symbol name, e.g. query='how do I {task_example}' or "
        "query='known issues' - it tries documentation first "
        "and composes it with any verified code example find_examples has for the same query, "
        "falling back to a symbol search only if nothing matches. For a bare, identifier-like "
        "query (a type name such as '{example_short}' in this index), it tries search_symbols "
        "first instead - no need to call search_symbols yourself first in that case. Pass "
        "content_type explicitly (one of 'getting_started', 'developer_guide', "
        "'troubleshooting', 'faq') only if you already know which documentation category you "
        "want; that delegates entirely to search_docs. If you already know the exact type FQN, "
        "call get_symbol directly instead - it is exact where this tool ranks and can miss. "
        "Returns an honest miss, never a fabricated answer, when nothing genuinely matches."
    ),
    "search_docs": (
        "Search the documentation. content_type ('getting_started', 'developer_guide', "
        "'troubleshooting' or 'faq') is OPTIONAL: omitted, every category is searched together "
        "and each match says which category it belongs to; given, only that category is searched, "
        "e.g. content_type='troubleshooting', query='known issues'. A query that is only the name "
        "of a category ('getting started', 'faq') returns that category's documentation. With a "
        "content_type, a query that matches only under a DIFFERENT content_type returns an "
        "explicit miss, never a widened cross-category result - do not treat a miss as proof the "
        "topic is undocumented anywhere; omit content_type to search them all. A long chunk may "
        "come back cut; the marker at the end of its text names the exact call that returns the "
        "rest - this tool again, with the chunk_id and an offset (a character position in the "
        "chunk) taken from that marker."
    ),
    "search_symbols": (
        "Search this deployment's self-extracted API surface by type name or fragment, e.g. "
        "query='{example_short}', or by the exact name of a method or property, "
        "which finds the type that declares it - a member has no FQN of its own, so this is "
        "how you find one. PREFERRED over lookup when you already know you want a "
        "symbol/class/method rather than documentation prose. Returns an explicit miss (never a "
        "fuzzy near-match, though a miss may carry did-you-mean suggestions) when nothing "
        "matches this scope's currently active index; it never falls back to a different "
        "product or platform. Once you know the exact type FQN, call get_symbol instead for the "
        "single exact, deterministic signature - this tool ranks and can return several partial "
        "matches. A sentence in your own words ('merge cells together', 'add a slide') is read as "
        "a task: the types that explain it come back, each labelled match_quality 'exact' or "
        "'partial' (a partial one names the words it could not explain in unmatched_terms). Every "
        "match carries total_matches and next_offset: call again with offset=next_offset for the "
        "next page; if the response says lower-ranked items were left out, set offset to the "
        "number of matches you received. Caveat: the API index may hold only part of a library's types, so a miss does "
        "not prove a type is absent upstream; report_index_freshness says how many types the "
        "index holds when that is known."
    ),
    "get_symbol": (
        "Return the exact signature - kind, bases, methods, properties, members - for ONE "
        "fully-qualified TYPE name, e.g. fqn='{example_fqn}'. Only a "
        "type has an FQN: a method or property has none of its own, but <Type>.<member> "
        "(the full type name or its short name, then a dot or '::', then the member name) resolves "
        "to the owning type's signature, with every overload line of that member in member_lines, "
        "and a short type name that ends exactly one indexed FQN resolves too. A name shared by "
        "several types, or a member the type does not declare, is an explicit NotFound that lists "
        "the full names or the nearest member names. Matching is by exact names, never a substring "
        "or fuzzy match, and example or documentation entries are never symbols. Verify the exact FQN via "
        "search_symbols first if you are not certain of it - do not guess it, since a "
        "near-miss returns an explicit NotFound (found false, with a reason and any "
        "did-you-mean suggestions) rather than the closest thing this index happens to "
        "contain. PREFERRED one-shot call once you know the exact FQN - no need to chain "
        "search_symbols first in that case. Caveat: a Go, Rust or TypeScript symbol name is not "
        "an import path - use get_product_reference with section='install' and the product "
        "README for imports."
    ),
    "list_members": (
        "List every method, property, and enum member a TYPE declares, in declaration order "
        "(methods first, then properties, then enum members) - the same exact type-FQN lookup "
        "get_symbol uses under the hood, e.g. fqn='{example_fqn}'. "
        "Call this instead of get_symbol when you only want the member names for a known type, "
        "not its full signature block. A method or property has no FQN of its own, but "
        "<Type>.<member> returns just that member's lines (all its overloads). Base types are NOT included - a base class is not a member of "
        "its subclass; call get_symbol and read its own bases field for that. Returns the "
        "identical NotFound get_symbol would for an FQN that does not exist - verify the exact "
        "FQN via search_symbols first if unsure, do not guess it."
    ),
    "find_examples": (
        "Return a verified code snippet for a natural-language query. It takes only query and "
        "top_k. A query equal to an example's title prefixed with the literal text Example: "
        "(the title exactly as an example is listed) is an exact match and is answered first; "
        "any other query searches every example-bearing chunk ({example_search_clause}), e.g. "
        "query='{task_example}'. Never executes any snippet itself - it only "
        "returns text a human or another process may choose to run. Returns an explicit "
        "NoExampleFound miss (found false, with a reason) when nothing verified exists; never "
        "fabricates or improvises an example. Prefer lookup instead when you also want "
        "documentation guidance for the same task question - lookup composes both into one "
        "answer."
    ),
    "get_product_reference": (
        "Fetch one specific reference section for this deployment's product: 'install' (the "
        "package coordinate, or the source repository and exact commit when no registry package "
        "resolves), 'formats', 'limitations', 'compatibility' (the real value from the repo's "
        "own packaging manifest, e.g. an exact .NET TargetFramework string - never a flattened "
        "generic claim), 'license', 'support', 'contributing', or 'agent_guidance'. Each "
        "section returns either its real content or an explicit NotAvailable with a reason. "
        "Formats and limitations have no structured source in this index for any product, so "
        "use lookup or search_docs (for example query='limitations') to read them from the "
        "product documentation. Contributing and agent_guidance are NotAvailable with a reason "
        "ending in 'is not present in this repository' only when the source repository "
        "genuinely lacks that file. A NotAvailable whose reason says the data could not be "
        "fetched is transient: retry later, and do not read it as evidence that the repository "
        "lacks the file."
    ),
    "list_recent_changes": (
        "Return the most recent releases (newest first) for this deployment's product, each "
        "carrying an honest richness verdict (e.g. detailed / templated / none) alongside its "
        "raw body text - a templated release note that only fills in a version number is "
        "labeled as such, never reported the same way as a real migration note. Use this for "
        "'what changed recently' questions; it takes no query, only limit (default 10) - it is "
        "not a search tool, so do not pass a query string here. An empty list means the "
        "product has no GitHub releases. An empty list comes with a note that says so. When the release data could not be fetched, the call "
        "returns an error saying so (retry later), never an empty list. Each release states in_index "
        "(true when the indexed API includes it, false when it is newer than the index, null when "
        "unknown) and source ('release' or 'tag')."
    ),
    "report_index_freshness": (
        "Compare the indexed generation's recorded source commit against a "
        "current_source_commit you supply, for this deployment's active scope; the report also "
        "carries the product's pinned library commit when it is known, and how many of the "
        "library's types the API index holds when the index states it. Pass "
        "source_kind='self_extracted' (the default). Only 'self_extracted' holds an index in "
        "this product: the other three values (furnished, repo_native_manifest, "
        'repo_native_release_notes) always answer "never published", so there is no reason '
        "to pass them. If "
        "current_source_commit is omitted, staleness cannot be ruled out and the report says "
        "so explicitly rather than assuming freshness - it never guesses. Check this before "
        "relying heavily on search results if you need to know whether the index reflects the "
        "latest upstream release. The report gives freshness_state (fresh, stale or unknown) with its "
        "basis, and when no commit is supplied it compares against the latest release when it can."
    ),
}


def tool_description(name: str, facts: DeploymentFacts | None = None) -> str:
    """The real, agent-facing description for tool *name*: what it does, when to prefer it
    over a sibling tool, concrete real example values, and what not to guess. Raises
    ``KeyError`` for an unregistered name - there is no silent placeholder fallback.

    ``TOOL_DESCRIPTIONS`` holds templates (G3/TC-514). *facts* are the deployment's own example
    values (``read_deployment_facts``); ``None`` renders the legacy pdf/net example values, so a
    static caller sees today's text and a deployment sees its own.
    """
    return render_description(TOOL_DESCRIPTIONS[name], facts)


# Shared per-field-name schema descriptions, reused everywhere a field of that name appears
# rather than hand-written once per tool.
_FIELD_DESCRIPTIONS: dict[str, str] = {
    "query": (
        "The search text: a natural-language task question (e.g. 'how do I add a watermark "
        "to a PDF') or, where the tool searches symbols, a type name or fragment. Must be "
        "non-empty. The advertised input schema carries a description specific to each tool."
    ),
    "content_type": (
        "One of the four documentation categories: 'getting_started', 'developer_guide', "
        "'troubleshooting', 'faq'. Optional on search_docs (omitted, every category is searched) and "
        "on lookup, where omitting it lets lookup infer the right source for you instead of you "
        "guessing the category. Must be non-empty when supplied."
    ),
    "top_k": (
        "Maximum number of matches to return. Must be a positive integer, capped at 100; a "
        "non-positive or absurdly large value is rejected rather than silently truncated."
    ),
    "fqn": (
        "The exact, fully-qualified TYPE name to match verbatim, e.g. '{example_fqn}' "
        "- case-sensitive exact match only, never a substring or fuzzy "
        "match. A method or property has no FQN of its own; <Type>.<member> is accepted. Verify "
        "the exact FQN via search_symbols first if you are not certain of it - do not guess it."
    ),
    "section": (
        "Which product-reference section to fetch: one of 'install', 'formats', "
        "'limitations', 'compatibility', 'license', 'support', 'contributing', "
        "'agent_guidance'."
    ),
    "limit": "Maximum number of recent releases to return. Must be a positive integer, capped at 100.",
    "source_kind": (
        "Which indexed source to check freshness for; defaults to 'self_extracted', this "
        "project's own API-surface index."
    ),
    "current_source_commit": (
        "The current upstream source commit SHA to compare the indexed generation against, if "
        "known; omit if unknown - staleness then cannot be ruled out and the report says so."
    ),
}


# ---------------------------------------------------------------------
# Pydantic input models: one per tool, validated with Model.model_validate(arguments) BEFORE
# the underlying handler is ever called, so a malformed argument raises a clean
# pydantic.ValidationError (a ValueError subclass) that propagates to _call_tool's own shared
# except-clause instead of whatever a downstream function happens to raise. top_k rejects a
# non-positive or absurdly large value; query/content_type reject an empty string.
# ---------------------------------------------------------------------

_TOP_K_MAX = 100

# Length caps: no response can echo a large input back (the largest field a client controls).
_QUERY_MAX_LENGTH = 1000
_FQN_MAX_LENGTH = 512
_SHORT_STRING_MAX_LENGTH = 128

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")

# The closed sets are derived from the constants the tools themselves enforce, never retyped.
_SourceKindLiteral = typing.Literal[tuple(kind.value for kind in SourceKind)]  # type: ignore[valid-type]
_ContentTypeLiteral = typing.Literal[CONTENT_TYPES]  # type: ignore[valid-type]
_SectionLiteral = typing.Literal[SECTIONS]  # type: ignore[valid-type]

# Every input model is closed (no unknown arguments) and strict (no coercion: ``true`` or
# ``"3"`` is never a valid ``top_k``).
_STRICT_CLOSED = ConfigDict(extra="forbid", strict=True)


def _require_non_empty(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _reject_control_characters(value: str, field_name: str, *, allow_whitespace_controls: bool) -> str:
    """Reject NUL and every other C0 control character - except, when allowed, tab, newline
    and carriage return. The message never repeats the rejected value."""
    allowed = "\t\n\r" if allow_whitespace_controls else ""
    if any(ord(ch) < 32 and ch not in allowed for ch in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _clean_text(value: str, field_name: str, *, allow_whitespace_controls: bool) -> str:
    """Strip, require non-empty, and reject control characters."""
    stripped = value.strip()
    _require_non_empty(stripped, field_name)
    return _reject_control_characters(
        stripped, field_name, allow_whitespace_controls=allow_whitespace_controls
    )


class LookupInput(BaseModel):
    model_config = _STRICT_CLOSED

    query: str = Field(max_length=_QUERY_MAX_LENGTH)
    content_type: _ContentTypeLiteral | None = None
    top_k: int = Field(default=10, gt=0, le=_TOP_K_MAX)

    @field_validator("query")
    @classmethod
    def _query_clean(cls, value: str) -> str:
        return _clean_text(value, "query", allow_whitespace_controls=True)


class SearchDocsInput(BaseModel):
    model_config = _STRICT_CLOSED

    query: str = Field(max_length=_QUERY_MAX_LENGTH)
    # G3/TC-562: optional; omitted, every documentation category is searched.
    content_type: _ContentTypeLiteral | None = None
    top_k: int = Field(default=10, gt=0, le=_TOP_K_MAX)
    # G3/TC-563: read the rest of a documentation chunk the response budget cut.
    chunk_id: str | None = Field(default=None, max_length=_SHORT_STRING_MAX_LENGTH)
    offset: int = Field(default=0, ge=0)

    @field_validator("query")
    @classmethod
    def _query_clean(cls, value: str) -> str:
        return _clean_text(value, "query", allow_whitespace_controls=True)

    @field_validator("chunk_id")
    @classmethod
    def _chunk_id_clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_text(value, "chunk_id", allow_whitespace_controls=False)


class SearchSymbolsInput(BaseModel):
    model_config = _STRICT_CLOSED

    query: str = Field(max_length=_QUERY_MAX_LENGTH)
    top_k: int = Field(default=10, gt=0, le=_TOP_K_MAX)
    # G3/TC-562: page through the ranked symbols (the next page's offset is in every match's ``next_offset``).
    offset: int = Field(default=0, ge=0)

    @field_validator("query")
    @classmethod
    def _query_clean(cls, value: str) -> str:
        return _clean_text(value, "query", allow_whitespace_controls=True)


class GetSymbolInput(BaseModel):
    model_config = _STRICT_CLOSED

    fqn: str = Field(max_length=_FQN_MAX_LENGTH)

    @field_validator("fqn")
    @classmethod
    def _fqn_clean(cls, value: str) -> str:
        return _clean_text(value, "fqn", allow_whitespace_controls=False)


class ListMembersInput(BaseModel):
    model_config = _STRICT_CLOSED

    fqn: str = Field(max_length=_FQN_MAX_LENGTH)

    @field_validator("fqn")
    @classmethod
    def _fqn_clean(cls, value: str) -> str:
        return _clean_text(value, "fqn", allow_whitespace_controls=False)


class FindExamplesInput(BaseModel):
    model_config = _STRICT_CLOSED

    query: str = Field(max_length=_QUERY_MAX_LENGTH)
    top_k: int = Field(default=5, gt=0, le=_TOP_K_MAX)

    @field_validator("query")
    @classmethod
    def _query_clean(cls, value: str) -> str:
        return _clean_text(value, "query", allow_whitespace_controls=True)


class GetProductReferenceInput(BaseModel):
    model_config = _STRICT_CLOSED

    section: _SectionLiteral


class ListRecentChangesInput(BaseModel):
    model_config = _STRICT_CLOSED

    limit: int = Field(default=10, gt=0, le=_TOP_K_MAX)


class ReportIndexFreshnessInput(BaseModel):
    model_config = _STRICT_CLOSED

    source_kind: _SourceKindLiteral = SourceKind.SELF_EXTRACTED.value  # type: ignore[valid-type]
    current_source_commit: str | None = Field(default=None, max_length=_SHORT_STRING_MAX_LENGTH)

    @field_validator("current_source_commit")
    @classmethod
    def _commit_normalised(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip().lower()
        if _COMMIT_PATTERN.fullmatch(normalised) is None:
            raise ValueError("current_source_commit must be a 7 to 64 character hexadecimal commit id")
        return normalised


_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "lookup": LookupInput,
    "search_docs": SearchDocsInput,
    "search_symbols": SearchSymbolsInput,
    "get_symbol": GetSymbolInput,
    "list_members": ListMembersInput,
    "find_examples": FindExamplesInput,
    "get_product_reference": GetProductReferenceInput,
    "list_recent_changes": ListRecentChangesInput,
    "report_index_freshness": ReportIndexFreshnessInput,
}


# ---------------------------------------------------------------------
# JSON Schema, derived from each wrapper's own signature - never hand-duplicated.
# ---------------------------------------------------------------------


def _json_type(annotation: object) -> dict[str, Any]:
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _json_type(args[0])
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    return {"type": "string"}


def schema_for(func: Callable[..., Any]) -> dict[str, Any]:
    """A JSON Schema object derived from *func*'s own signature: one property per parameter,
    required unless the parameter has a default. Each property also carries a short,
    concrete description from ``_FIELD_DESCRIPTIONS``, keyed by parameter name and shared
    across every tool where that same field name appears (e.g. every ``top_k`` gets the
    identical description), when one is registered for that name.
    """
    signature = inspect.signature(func)
    hints = typing.get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        property_schema = _json_type(hints.get(name, str))
        description = _FIELD_DESCRIPTIONS.get(name)
        if description is not None:
            property_schema = {**property_schema, "description": description}
        properties[name] = property_schema
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


# One description per (tool, field): each talks only about its own tool. The advertised schemas
# use these, never the shared per-field-name text above (which stays for ``schema_for``).
_TOOL_FIELD_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "lookup": {
        "query": (
            "What you want to know, as a natural-language task question (e.g. 'how do I "
            "{task_example}') or a bare type name; lookup chooses the index to search. "
            "Must be non-empty."
        ),
        "content_type": (
            "Optional documentation category. Supplying one delegates the whole call to "
            "search_docs for that category; omit it to let lookup infer the right source."
        ),
        "top_k": "Maximum number of matches to return, from 1 to 100 (default 10).",
    },
    "search_docs": {
        "query": (
            "Documentation search text (e.g. 'known issues'), matched within the chosen "
            "content_type, or within every category when none is given. Must be non-empty."
        ),
        "content_type": (
            "Optional documentation category to search; omit it to search every category (each match "
            "names its own). With one, a query that matches only under a different category is a miss, "
            "not a widened result."
        ),
        "top_k": "Maximum number of documentation matches to return, from 1 to 100 (default 10).",
        "chunk_id": (
            "Optional. The chunk_id of a documentation chunk whose text came back cut (the marker at the "
            "end of the text names it). With it this tool does not search: it returns that one chunk "
            "from character `offset` on, in a part that fits one response, with `next_offset` when "
            "more follows. query is still required; give the same content_type the hit had (or none, if "
            "the search had none)."
        ),
        "offset": (
            "Optional, with chunk_id only: the character position in the chunk where the returned part "
            "starts (default 0, the start). Use the offset named by the truncation marker, or the "
            "`next_offset` of the previous part to read the rest of a truncated chunk."
        ),
    },
    "search_symbols": {
        "query": (
            "A type name or fragment (e.g. '{example_short}' in this index), or the exact name "
            "of a method or property to find the type that declares it. Must be non-empty."
        ),
        "top_k": "Maximum number of symbol matches to return, from 1 to 100 (default 10).",
        "offset": (
            "Optional. How many of the best matches to skip (default 0): pass the `next_offset` of the "
            "previous page to read the next one."
        ),
    },
    "get_symbol": {
        "fqn": (
            "The exact, fully-qualified TYPE name, e.g. '{example_fqn}'. "
            "Case-sensitive; a method or property has no FQN of its own, but <Type>.<member> "
            "is accepted. Verify it with search_symbols if unsure."
        ),
    },
    "list_members": {
        "fqn": (
            "The exact, fully-qualified TYPE name whose members to list, e.g. "
            "'{example_fqn}'. Case-sensitive; a method or property "
            "has no FQN of its own, but <Type>.<member> returns that member's lines. Verify it "
            "with search_symbols if unsure."
        ),
    },
    "find_examples": {
        "query": (
            "What the example should do, as a natural-language request (e.g. "
            "'{task_example}'), or an example's exact title prefixed with the literal text "
            "Example: for an exact match. Must be non-empty."
        ),
        "top_k": "Maximum number of examples to return, from 1 to 100 (default 5).",
    },
    "get_product_reference": {
        "section": "The product-reference section to fetch; each is answered with its content or an explicit NotAvailable.",
    },
    "list_recent_changes": {
        "limit": "Maximum number of recent releases to return, newest first, from 1 to 100 (default 10).",
    },
    "report_index_freshness": {
        "source_kind": (
            "Which indexed source to check freshness for; only 'self_extracted' holds an index "
            'in this product, and the other three values always answer "never published".'
        ),
        "current_source_commit": (
            "The current upstream source commit as 7 to 64 hexadecimal characters, if known; "
            "omit it if unknown - staleness then cannot be ruled out and the report says so."
        ),
    },
}

# Constraints the models enforce in validators rather than in field metadata, stated here so the
# advertised schema carries them too.
_FIELD_SCHEMA_EXTRAS: dict[str, dict[str, Any]] = {
    "query": {"minLength": 1},
    "fqn": {"minLength": 1},
    "current_source_commit": {"pattern": "^\\s*[0-9a-fA-F]{7,64}\\s*$"},
}

_TOOL_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def _strip_none(annotation: object) -> object:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        remaining = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(remaining) == 1:
            return remaining[0]
    return annotation


def _advertised_property(
    tool_name: str, field_name: str, field: Any, facts: DeploymentFacts | None = None
) -> dict[str, Any]:
    annotation = _strip_none(field.annotation)
    prop: dict[str, Any]
    if typing.get_origin(annotation) is typing.Literal:
        prop = {"type": "string", "enum": list(typing.get_args(annotation))}
    else:
        prop = dict(_json_type(annotation))
    prop["description"] = render_description(_TOOL_FIELD_DESCRIPTIONS[tool_name][field_name], facts)
    for constraint in field.metadata:
        max_length = getattr(constraint, "max_length", None)
        if max_length is not None:
            prop["maxLength"] = max_length
        minimum = getattr(constraint, "ge", None)
        if minimum is None and getattr(constraint, "gt", None) is not None:
            minimum = constraint.gt + 1
        if minimum is not None:
            prop["minimum"] = minimum
        maximum = getattr(constraint, "le", None)
        if maximum is None and getattr(constraint, "lt", None) is not None:
            maximum = constraint.lt - 1
        if maximum is not None:
            prop["maximum"] = maximum
    prop.update(_FIELD_SCHEMA_EXTRAS.get(field_name, {}))
    if not field.is_required():
        default = field.get_default(call_default_factory=True)
        if default is not None:
            prop["default"] = default
    return prop


def tool_input_schema(tool_name: str, facts: DeploymentFacts | None = None) -> dict[str, Any]:
    """The JSON Schema advertised in ``tools/list`` for *tool_name*, built from its Pydantic
    input model in ``_INPUT_MODELS`` - the model the call is validated against, so the two cannot
    drift. Flat on purpose (clients build calls from it): ``type`` on every property, no
    ``anyOf``/``$ref``, an optional field simply absent from ``required``. Wherever the model
    states them: ``enum``, ``default``, ``minimum``, ``maximum``, ``maxLength``, ``pattern``.

    The field descriptions are templates (G3/TC-514), rendered with *facts* exactly as
    ``tool_description`` renders the tool descriptions: ``None`` is the legacy pdf/net example
    values, a deployment passes its own.
    """
    model = _INPUT_MODELS[tool_name]
    properties = {
        name: _advertised_property(tool_name, name, field, facts)
        for name, field in model.model_fields.items()
    }
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    required = [name for name, field in model.model_fields.items() if field.is_required()]
    if required:
        schema["required"] = required
    return schema


def _assert_schema_matches_wrapper(
    tool_name: str, advertised: dict[str, Any], wrapper_schema: dict[str, Any]
) -> None:
    """Fail server construction if a tool's advertised schema (built from its input model) and its
    client-facing wrapper's own signature (``schema_for``) disagree on parameter names, types or
    which are required - the drift this would otherwise only show to a peer at call time."""
    advertised_shape = {name: prop["type"] for name, prop in advertised["properties"].items()}
    wrapper_shape = {name: prop["type"] for name, prop in wrapper_schema["properties"].items()}
    if advertised_shape != wrapper_shape or advertised.get("required", []) != wrapper_schema.get(
        "required", []
    ):
        raise RuntimeError(f"tool '{tool_name}': advertised schema disagrees with its wrapper signature")


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _to_jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, list | tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


def render_result_text(result: Any) -> str:
    """The plain-text ``TextContent`` rendering of a tool's return value - real, parseable
    JSON built from the identical ``_to_jsonable(result)`` form already computed for
    ``structured_content``, never Python's own ``repr()`` debug syntax. Every client receives
    both channels; neither should require Python-specific parsing, and both must agree.
    """
    return json.dumps(_to_jsonable(result), default=str)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Tool-error vocabulary and client-safe exception sanitizer.
#
# A real, independent review of a working commercial MCP server found a concrete gap
# foss-mcp genuinely shared: returning ``str(exc)`` to the client on any unexpected
# exception, unsanitized - an internal file path, a library name (``site-packages``), or a
# raw traceback could leak straight to a real client. ``public_error_message`` is the one
# place that decision is made; every error path in ``_call_tool`` reports through it and
# through the small, closed ``ToolErrorCode`` vocabulary below, never inventing a new code.
# ---------------------------------------------------------------------


class ToolErrorCode(str, Enum):
    """A small, closed set of tool-error codes - every error ``CallToolResult`` this server
    ever produces carries exactly one of these in its ``structured_content``. Extend only for
    a real, currently-distinguishable case; never add a code nothing produces.
    """

    NOT_FOUND = "not_found"
    INVALID_ARGUMENT = "invalid_argument"
    INTERNAL = "internal"
    UNAVAILABLE = "unavailable"


# A Windows path: a drive letter followed by two or more backslash-separated segments.
_WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\(?:[^\s\\]+\\)+[^\s\\]+")
# A POSIX path: two or more slash-separated segments.
_POSIX_PATH_PATTERN = re.compile(r"(?:/[^/\s]+){2,}")

# Substrings that, on their own, mark exception text as an internal implementation detail
# rather than something safe to hand a client verbatim.
_UNSAFE_SUBSTRINGS = ("site-packages", "Traceback", "object at 0x")

_MAX_SAFE_MESSAGE_LENGTH = 300


def _contains_internal_detail(text: str) -> bool:
    if "\n" in text:
        return True
    if len(text) > _MAX_SAFE_MESSAGE_LENGTH:
        return True
    if any(marker in text for marker in _UNSAFE_SUBSTRINGS):
        return True
    if _WINDOWS_PATH_PATTERN.search(text) or _POSIX_PATH_PATTERN.search(text):
        return True
    return False


def public_error_message(exc: Exception, *, fallback: str, correlation_id: str | None = None) -> str:
    """The client-safe rendering of *exc*.

    Returns ``str(exc)`` verbatim only when it passes real safety checks: no filesystem path
    (Windows or POSIX, multi-segment), none of ``site-packages``/``Traceback``/``object at
    0x``, no embedded newline, and no more than ~300 characters. Otherwise the real exception
    (type and message) is logged server-side via the standard ``logging`` module before
    *fallback* is returned instead - the real detail is never simply discarded, only ever kept
    out of the client's own result text.

    *correlation_id*, when supplied, is threaded into that same server-side warning line so an
    operator handed a correlation id by an end user can grep straight to the suppressed text -
    without it, the one log line that fires on suppression carried no way to find it again.
    """
    text = str(exc)
    # G3/TC-564: the text of a lookup or attribute fault is a field name or a type name from this
    # server's own code (``'text'``, ``'NoneType' object has no attribute ...``), never a message for a
    # client, however short and path-free it looks.
    if isinstance(exc, LookupError | AttributeError | TypeError) or _contains_internal_detail(text):
        logger.warning(
            "suppressed unsafe exception text from an MCP client-facing result: type=%s message=%r "
            "correlation_id=%s",
            type(exc).__name__,
            text,
            correlation_id,
        )
        return fallback
    return text


def public_validation_error_message(
    exc: ValidationError, valid_arguments: Sequence[str] | None = None
) -> str:
    """A safe, specific client-facing message built directly from *exc*'s own structured error
    list (``exc.errors()``) - never its raw ``str()``, which routinely spans multiple lines and
    easily trips ``public_error_message``'s length/newline safety filter on totally safe
    content. ``loc``/``msg`` only ever reference this project's own declared Pydantic field
    names and pydantic's own constraint-violation wording (e.g. ``'query: Value error, query
    must not be empty'``, ``'top_k: Input should be greater than 0'``) - never a file path, a
    library internal, or anything resembling what that filter exists to catch. This is a
    different, narrower, independently-safe path, not a weakening of the existing one.

    G3/TC-514: given *valid_arguments* (the tool's own argument names, in declaration order) an
    unknown argument is named and the valid ones are listed, so a caller can correct the call. The
    name is client-chosen, so it is sanitised (every character outside ``[A-Za-z0-9_.-]`` becomes
    ``?``) and a name longer than 40 characters is not echoed at all, only its limit.
    """
    return "; ".join(_error_text(error, valid_arguments) for error in exc.errors())


_UNEXPECTED_ARGUMENT_LOCATION = "unexpected argument"
# An unknown argument name is client-chosen: echoed only when it is at most this long.
_ARGUMENT_NAME_ECHO_MAX = 40
_ARGUMENT_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _unexpected_argument_text(error: Any, valid_arguments: Sequence[str]) -> str:
    name = str(error["loc"][0]) if error["loc"] else ""
    if len(name) > _ARGUMENT_NAME_ECHO_MAX:
        shown = f"(name longer than {_ARGUMENT_NAME_ECHO_MAX} characters)"
    else:
        shown = "'" + _ARGUMENT_NAME_UNSAFE.sub("?", name) + "'"
    return f"{_UNEXPECTED_ARGUMENT_LOCATION} {shown}; valid arguments: {', '.join(valid_arguments)}"


def _error_text(error: Any, valid_arguments: Sequence[str] | None) -> str:
    """One pydantic error for a client-facing message. For an unknown argument pydantic's ``loc``
    is the CLIENT-CHOSEN argument name - arbitrary and possibly huge - so it is named only when the
    caller supplies the valid names (and then sanitised and bounded); every other ``loc`` is one of
    this project's own field names."""
    if error["type"] == "extra_forbidden":
        if valid_arguments is not None:
            return _unexpected_argument_text(error, valid_arguments)
        return f"{_UNEXPECTED_ARGUMENT_LOCATION}: {error['msg']}"
    return f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"


# An unknown tool name is client-chosen: never echo more than this many characters of it.
_UNKNOWN_TOOL_ECHO_MAX = 64

_GENERIC_ERROR_FALLBACK = "An internal error occurred while handling this tool call."

# The tools that answer from the deployment's published generation. With no usable generation
# they answer ``unavailable`` (an error), never an honest-looking "not found".
# ``report_index_freshness`` is deliberately absent (it honestly reports "never published"), as
# are ``get_product_reference`` and ``list_recent_changes`` (they read sidecars, not the generation).
_GENERATION_TOOLS = frozenset(
    {"lookup", "search_docs", "search_symbols", "get_symbol", "list_members", "find_examples"}
)
# G3/TC-564 (RD3-123): the two tools that read the generation without being "generation tools" (the
# alignment of the releases with the index; the freshness report itself) get the same pre-check, so a
# corrupt pointer or generation is the sanitised ``unavailable`` on every tool, not raw exception text. A
# deployment that never published still gets its honest "never published" from report_index_freshness.
_GENERATION_READING_TOOLS = _GENERATION_TOOLS | {"list_recent_changes", "report_index_freshness"}


class _GenerationUnavailableError(RuntimeError):
    """No usable generation exists for this deployment; carries the short, path-free reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _run_tool_handler(
    handler: Callable[..., Any],
    tool_name: str,
    arguments: dict[str, Any],
    watch: GenerationWatch,
    active: Any = None,
) -> Any:
    """Run *handler* in the worker thread, after the cheap generation check for every tool that reads
    the generation (G3/TC-506, TC-564). Raises ``_GenerationUnavailableError`` when there is nothing
    usable to read. *active* (an ``_ActiveGeneration``) is warmed here, after the arguments were
    validated, so the call's log line can name the commit the generation was built from."""
    if tool_name in _GENERATION_READING_TOOLS:
        reason = watch.unavailable_reason()
        if reason is not None:
            raise _GenerationUnavailableError(reason)
        if active is not None:
            active.read()
    return handler(**arguments)


# The exceptions that come from reading the stored generation or pointer rather than from the caller's
# arguments: a manifest-store error, an OS error, undecodable bytes or invalid JSON. Their text names files
# and parser positions and is never shown to a client; they are the sanitised ``unavailable`` (G3/TC-564).
_STORAGE_FAULTS = (GenerationManifestError, OSError, UnicodeDecodeError, json.JSONDecodeError)

# ---------------------------------------------------------------------
# Tool-call logging: a real, independent review of a working commercial MCP server confirmed
# it logs every tool call (name, latency, outcome) and mints a correlation id that appears
# BOTH in its own server-side log line and in any error response returned to the client, so an
# operator can find the exact log line behind a reported failure. This is the minimum real
# version of that: one structured log line per tool call, via plain stdlib ``logging`` - no new
# dependency, no bespoke formatter (that is deployment-level configuration, out of scope here).
# ---------------------------------------------------------------------


# The logged and echoed tool name is client-chosen when it is not a registered tool: it is capped.
_LOGGED_TOOL_NAME_MAX = 64
# The metrics ``tool`` label for a name that is not a registered tool (G3/TC-509).
_UNKNOWN_TOOL_LABEL = "unknown"

# The tools whose "nothing matched" answer is a miss worth counting (G3/TC-509).
_RETRIEVAL_TOOLS = frozenset(
    {"lookup", "search_docs", "search_symbols", "get_symbol", "list_members", "find_examples"}
)
_MISS_RESULT_TYPES = frozenset({"Miss", "NotFound", "NoExampleFound"})


def _result_hit(tool_name: str, result: Any) -> bool | None:
    """Whether a successful retrieval-tool call found something: ``False`` for an honest miss
    (a ``Miss``, ``NotFound`` or ``NoExampleFound`` result, or any result whose ``found`` is
    ``False``), ``True`` otherwise. ``None`` for every other tool and for every error, because
    an empty ``list_recent_changes`` or a freshness report is not a miss."""
    if tool_name not in _RETRIEVAL_TOOLS:
        return None
    if type(result).__name__ in _MISS_RESULT_TYPES or getattr(result, "found", True) is False:
        return False
    return True


def _log_tool_call(
    logger: logging.Logger,
    *,
    tool_name: str,
    correlation_id: str,
    outcome: str,
    latency_ms: float,
    family: str | None = None,
    platform: str | None = None,
    code: str | None = None,
    hit: bool | None = None,
    generation_id: str | None = None,
    source_commit: str | None = None,
) -> None:
    """Emit exactly one structured log record for a single tool call.

    Besides the tool name, correlation id, outcome and latency, the record carries the pilot
    (``family``, ``platform``), the error ``code`` (``ok`` on success) and ``hit`` (``True`` or
    ``False`` for a retrieval tool, ``None`` otherwise), so an operator can see which pilot
    misses. The logged ``tool`` is capped at 64 characters: an unknown name is client-chosen.

    G3/TC-564 (RD3-124): ``generation_id`` and ``source_commit`` name the generation that was active for
    the call and the commit its API symbols were extracted from (``None`` when none is readable), so a
    line can be tied to the exact index that answered.
    """
    logged_tool = tool_name[:_LOGGED_TOOL_NAME_MAX]
    logger.info(
        "tool call: %s (%s)",
        logged_tool,
        outcome,
        extra={
            "tool": logged_tool,
            "correlation_id": correlation_id,
            "outcome": outcome,
            "latency_ms": latency_ms,
            "family": family,
            "platform": platform,
            "code": code,
            "hit": hit,
            "generation_id": generation_id,
            "source_commit": source_commit,
        },
    )


# ---------------------------------------------------------------------
# Telemetry wiring (G2/TC-142): TC-130's own import-graph walk confirmed
# foss_mcp.telemetry.usage_recorder.UsageRecorder/build_event had zero production callers - no
# tool call anywhere recorded a UsageEvent. _build_usage_event is the one real chokepoint that
# fixes that, called from every _call_tool exit path below.
# ---------------------------------------------------------------------


def _build_usage_event(
    *,
    correlation_id: str,
    deployment_id: str,
    tool_name: str,
    outcome: str,
    latency_ms: float,
    result: Any,
    arguments: dict,
) -> UsageEvent:
    """Build one real, contract-complete ``UsageEvent`` for a single ``_call_tool`` exit path.

    Three fields are deliberately simplified here, documented rather than silently gapped:

    - ``generation_id`` is always ``None`` - not genuinely derivable from this dispatch layer
      without per-tool knowledge of which generation each tool's own store lookup actually
      resolved to.
    - ``citation_count`` is always ``0`` - same reason as ``generation_id``.
    - ``cache_status`` is always ``"unknown"`` - this project has no caching layer, so no path
      could ever truthfully report ``"hit"``/``"miss"``.

    ``result_count`` is a best-effort, generic signal: the length of the jsonable *result* when
    it is a list, ``0`` otherwise - this covers every error path (where *result* is ``None``)
    as well as a successful call that returns a single object rather than a list. ``query`` is
    read from *arguments* (``arguments.get("query")``, ``None`` when absent) purely to derive
    the bounded ``query_shape_category`` - the raw text itself is never retained on the event.
    """
    jsonable_result = _to_jsonable(result)
    result_count = len(jsonable_result) if isinstance(jsonable_result, list) else 0
    return build_event(
        request_correlation_id=correlation_id,
        deployment_id=deployment_id,
        generation_id=None,
        tool_name=tool_name,
        outcome=outcome,  # type: ignore[arg-type]
        latency_ms=latency_ms,
        result_count=result_count,
        citation_count=0,
        cache_status="unknown",
        query=arguments.get("query"),
    )


def _record_usage_safely(usage_recorder: UsageRecorder, *, tool_name: str, **event_kwargs: Any) -> None:
    """Build and record one usage event, the same way at all four ``_call_tool`` exit paths -
    never letting a secondary failure in building or recording it prevent the already-prepared,
    correctly-sanitized ``CallToolResult`` from being returned.

    This closes a real, live-reproduced gap: ``_build_usage_event`` reads the RAW, unvalidated
    client argument for telemetry (``arguments.get("query")``), so a non-string argument that
    pydantic correctly rejected could still crash telemetry itself - escaping past every one of
    ``_call_tool``'s own sanitized branches as a bare, unsanitized exception. The real, durable
    fix is ``classify_query_shape`` no longer assuming its input is ``str | None`` at all; this
    is deliberately broad defense in depth on top of that fix, not a substitute for it -
    telemetry must never be able to turn a clean response into an unsanitized crash, for any
    reason, today's trigger or a future one.
    """
    try:
        usage_recorder.record(_build_usage_event(tool_name=tool_name, **event_kwargs))
    except Exception as telemetry_exc:  # noqa: BLE001 - deliberately broad: see docstring above.
        logger.warning("failed to record usage telemetry for tool '%s': %s", tool_name, telemetry_exc)


def _build_tool_registry(
    store: GenerationManifestStore,
    scope: Scope,
    product_reference_inputs: ProductReferenceInputs | None = None,
    recent_releases: Sequence[Release] = (),
    recent_releases_state: str = SIDECAR_PRESENT,
    *,
    sidecars: SidecarProvider | None = None,
    active: _ActiveGeneration | None = None,
    align_install: bool = False,
) -> dict[str, Callable[..., Any]]:
    """The nine client-facing tool wrappers. The sidecar data is either frozen from the three positional
    arguments or, with *sidecars*, read from the provider on every call (G3/TC-564)."""
    if sidecars is None:
        sidecars = _fixed_sidecars(product_reference_inputs, recent_releases, recent_releases_state)
    if active is None:
        active = _ActiveGeneration(store, scope)
    return {
        "lookup": _wrap_lookup(store, scope),
        "search_docs": _wrap_search_docs(store, scope),
        "search_symbols": _wrap_search_symbols(store, scope),
        "get_symbol": _wrap_get_symbol(store, scope),
        "list_members": _wrap_list_members(store, scope),
        "find_examples": _wrap_find_examples(store, scope),
        "get_product_reference": _live_get_product_reference(
            sidecars, store, scope, active if align_install else None
        ),
        "list_recent_changes": _live_list_recent_changes(sidecars, store, scope),
        "report_index_freshness": _live_report_index_freshness(sidecars, store, scope),
    }


class _DeploymentServer(Server):
    """A ``Server`` whose ``instructions`` are rendered when the handshake is built, not when the
    server is constructed (G3/TC-514).

    The instructions name a real symbol of the deployment's active generation, so rendering them
    reads that generation. Doing that in ``create_server`` would make a server started before its
    first generation is published say "nothing is readable" for its whole life, and would put a
    generation read in front of every call that never needed one. The constructor is handed a
    provider (a zero-argument callable) instead of a string; each read of ``instructions`` calls it,
    so a handshake always reflects the generation that is active now."""

    @property
    def instructions(self) -> str | None:
        provider = self._instructions_provider
        return provider() if provider is not None else None

    @instructions.setter
    def instructions(self, value: Any) -> None:
        self._instructions_provider = value if callable(value) or value is None else (lambda: value)


def create_server(
    deployment_config: DeploymentConfig,
    manifest_store: GenerationManifestStore | None = None,
    product_reference_inputs: ProductReferenceInputs | None = None,
    recent_releases: Sequence[Release] = (),
    usage_recorder: UsageRecorder | None = None,
    recent_releases_state: str = SIDECAR_PRESENT,
    metrics: MetricsRegistry | None = None,
    sidecars: SidecarProvider | None = None,
) -> Server:
    """Bootstrap the MCP server for one deployment, with all nine tools registered.

    ``sidecars`` (G3/TC-564) is a zero-argument callable returning the CURRENT ``SidecarSnapshot``; it is
    called on every tool call that reads a sidecar, so a serving process can hand in one that re-reads
    the files when they change. When it is not given the three arguments ``product_reference_inputs``,
    ``recent_releases`` and ``recent_releases_state`` are frozen into one snapshot, exactly as before.

    ``metrics`` (G3/TC-509) is the registry every finished tool call is observed into; it is
    resolved the same way as ``usage_recorder`` (a caller that renders it, ``build_app``'s
    ``/metrics`` route, passes the SAME instance in). A fresh registry labelled with this
    deployment's family and platform is built when none is given.

    ``recent_releases_state`` (G3/TC-507) is ``fetch_failed`` when the releases sidecar was
    unusable: ``list_recent_changes`` then answers an ``unavailable`` error instead of an empty
    list. ``product_reference_inputs`` carries its own ``sidecar_state``.

    ``resolve_scope`` fixes the scope once, here, from ``deployment_config`` alone; every
    tool wrapper closes over that one ``Scope`` for the lifetime of this server - no tool
    argument can ever change which product it answers for.

    ``usage_recorder`` mirrors ``manifest_store``'s own default-when-not-given pattern: a real
    ``UsageRecorder()`` is constructed here if the caller does not supply one. A caller that
    needs to read the SAME live instance elsewhere (``infra/serve_http.py``'s own ``build_app``,
    for its ``/metrics`` route) resolves it itself first and passes it in here - the identical
    pattern ``build_app`` already uses for ``manifest_store`` with ``/readyz``.
    """
    scope = resolve_scope(deployment_config, request=None)
    store = manifest_store or _default_manifest_store()
    # The install notice follows the active generation's commit only for a live (served) sidecar provider.
    align_install = sidecars is not None
    if sidecars is None:
        sidecars = _fixed_sidecars(product_reference_inputs, recent_releases, recent_releases_state)
    # NOT `usage_recorder or UsageRecorder()`: UsageRecorder defines __len__, so a real, valid,
    # but currently-EMPTY recorder (len 0 - the common case for any freshly constructed one,
    # including the exact recorder build_app passes in here) is falsy, and `or` would silently
    # discard it for a brand-new instance - breaking the whole point of sharing one real
    # recorder between create_server and build_app's own /metrics route.
    usage_recorder = usage_recorder if usage_recorder is not None else UsageRecorder()
    metrics = (
        metrics if metrics is not None else MetricsRegistry(family=scope.family, platform=scope.platform)
    )
    deployment_id = f"{scope.family}::{scope.platform}"
    generation_scope_key = scope_key(scope, SOURCE_KIND)
    active_generation = _ActiveGeneration(store, scope)
    generation_watch = GenerationWatch(store, generation_scope_key)
    registry = _build_tool_registry(
        store, scope, sidecars=sidecars, active=active_generation, align_install=align_install
    )
    schemas = {name: tool_input_schema(name) for name in registry}
    for name, handler in registry.items():
        _assert_schema_matches_wrapper(name, schemas[name], schema_for(handler))

    def _facts() -> DeploymentFacts:
        return read_deployment_facts(store, scope)

    async def _list_tools(context: Any, params: Any) -> ListToolsResult:
        # The deployment's own example values are read once per call (not per tool), in a worker
        # thread like the tool handlers, because reading the generation is file I/O.
        facts = await anyio.to_thread.run_sync(_facts)
        return ListToolsResult(
            tools=[
                Tool(
                    name=name,
                    description=tool_description(name, facts),
                    input_schema=tool_input_schema(name, facts),
                    annotations=_TOOL_ANNOTATIONS,
                )
                for name in registry
            ]
        )

    def _finish(
        tool_name: str,
        correlation_id: str,
        started_at: float,
        outcome: str,
        code: ToolErrorCode | None,
        result: Any,
        arguments: dict,
    ) -> None:
        """The ONE place a finished tool call is accounted for: the log line, the usage event and
        the metrics observation, in that order. Each is shielded, so a failure in one neither skips
        the others nor prevents the ``CallToolResult`` being returned."""
        latency_ms = (time.monotonic() - started_at) * 1000
        code_value = "ok" if code is None else code.value
        hit = _result_hit(tool_name, result) if code is None else None
        generation_id, source_commit = active_generation.peek()
        try:
            _log_tool_call(
                logger,
                tool_name=tool_name,
                correlation_id=correlation_id,
                outcome=outcome,
                latency_ms=latency_ms,
                family=scope.family,
                platform=scope.platform,
                code=code_value,
                hit=hit,
                generation_id=generation_id,
                source_commit=source_commit,
            )
        except Exception:  # noqa: BLE001 - accounting must never change a tool result.
            pass
        _record_usage_safely(
            usage_recorder,
            correlation_id=correlation_id,
            deployment_id=deployment_id,
            tool_name=tool_name,
            outcome=outcome,
            latency_ms=latency_ms,
            result=result,
            arguments=arguments,
        )
        try:
            metrics.observe_tool_call(
                tool=tool_name if tool_name in registry else _UNKNOWN_TOOL_LABEL,
                outcome=outcome,
                code=code_value,
                latency_seconds=latency_ms / 1000,
                miss=hit is False,
            )
        except Exception as metrics_exc:  # noqa: BLE001 - accounting must never change a tool result.
            logger.warning("failed to record tool metrics: %s", type(metrics_exc).__name__)

    async def _call_tool(context: Any, params: CallToolRequestParams) -> CallToolResult:
        # One correlation id per call, minted here and never regenerated for the rest of this
        # dispatch - the same value goes into the server-side log line below AND into any error
        # CallToolResult's own structured_content, so an operator can find the exact log line
        # behind a client-reported failure (TC-131's own error-code field is unaffected; this
        # works alongside it, not in place of it).
        correlation_id = new_correlation_id()
        started_at = time.monotonic()
        handler = registry.get(params.name)
        if handler is None:
            _finish(
                params.name,
                correlation_id,
                started_at,
                "error",
                ToolErrorCode.NOT_FOUND,
                None,
                params.arguments or {},
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=f"unknown tool: {params.name[:_UNKNOWN_TOOL_ECHO_MAX]}")
                ],
                structured_content={
                    "code": ToolErrorCode.NOT_FOUND.value,
                    "message": "unknown tool",
                    "correlation_id": correlation_id,
                },
                is_error=True,
            )
        try:
            # Validate once per tool via its own typed Pydantic model BEFORE the underlying
            # handler is ever called - a pydantic.ValidationError propagates straight to the
            # except-clause below, mapping a malformed argument to a clean, bounded
            # CallToolResult instead of whatever a downstream function happens to raise.
            input_model = _INPUT_MODELS[params.name]
            validated_arguments = input_model.model_validate(params.arguments or {})
            # The handler does CPU and file I/O, so it runs in a worker thread: the event loop
            # (and /healthz, /readyz, other sessions) stays responsive while it works. Anything it
            # raises propagates out of run_sync into the same except-branches below.
            result = await anyio.to_thread.run_sync(
                functools.partial(
                    _run_tool_handler,
                    handler,
                    params.name,
                    validated_arguments.model_dump(),
                    generation_watch,
                    active_generation,
                )
            )
        except ValidationError as exc:
            # TC-125's own validation-error path: a malformed argument, always INVALID_ARGUMENT.
            # Built straight from exc.errors() (TC-136) - a plain, safe validation message like
            # 'query: Value error, query must not be empty' never needs to go through the
            # generic sanitizer at all, let alone be swallowed by it on totally safe content.
            text = public_validation_error_message(exc, tuple(_INPUT_MODELS[params.name].model_fields))
            _finish(
                params.name,
                correlation_id,
                started_at,
                "error",
                ToolErrorCode.INVALID_ARGUMENT,
                None,
                params.arguments or {},
            )
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={
                    "code": ToolErrorCode.INVALID_ARGUMENT.value,
                    "message": text,
                    "correlation_id": correlation_id,
                },
                is_error=True,
            )
        except _STORAGE_FAULTS as exc:
            # G3/TC-564 (RD3-123): the stored pointer or generation is damaged (invalid JSON is a
            # ValueError, a vanished file an OSError): the sanitised ``unavailable``, never the parser's
            # position or a path as ``invalid_argument`` or ``internal``. The class name is logged.
            logger.warning(
                "tool '%s' hit a storage fault: %s correlation_id=%s",
                params.name,
                type(exc).__name__,
                correlation_id,
            )
            _finish(
                params.name,
                correlation_id,
                started_at,
                "error",
                ToolErrorCode.UNAVAILABLE,
                None,
                params.arguments or {},
            )
            return CallToolResult(
                content=[TextContent(type="text", text=GENERATION_UNAVAILABLE_MESSAGE)],
                structured_content={
                    "code": ToolErrorCode.UNAVAILABLE.value,
                    "message": GENERATION_UNAVAILABLE_MESSAGE,
                    "correlation_id": correlation_id,
                },
                is_error=True,
            )
        except ValueError as exc:
            # A plain ValueError raised by a tool handler (e.g. search_docs's own content_type
            # check) is, by this codebase's convention, always a caller-correctable bad-argument
            # error, never a server fault - live-confirmed misclassified as INTERNAL before this
            # fix. Built the same shape as the ValidationError branch above, reusing the
            # existing sanitizer rather than inventing a new one.
            text = public_error_message(exc, fallback=_GENERIC_ERROR_FALLBACK, correlation_id=correlation_id)
            _finish(
                params.name,
                correlation_id,
                started_at,
                "error",
                ToolErrorCode.INVALID_ARGUMENT,
                None,
                params.arguments or {},
            )
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={
                    "code": ToolErrorCode.INVALID_ARGUMENT.value,
                    "message": text,
                    "correlation_id": correlation_id,
                },
                is_error=True,
            )
        except _GenerationUnavailableError as exc:
            # No usable generation to search (G3/TC-506): an error with one fixed, sanitised
            # message and the code ``unavailable`` - never "found: false" (which an agent reads as
            # "this API does not exist") and never raw exception text. The reason is a fixed phrase
            # or an exception class name, never an exception message or a path.
            logger.warning(
                "tool '%s' has no usable generation: %s correlation_id=%s",
                params.name,
                exc.reason,
                correlation_id,
            )
            _finish(
                params.name,
                correlation_id,
                started_at,
                "error",
                ToolErrorCode.UNAVAILABLE,
                None,
                params.arguments or {},
            )
            return CallToolResult(
                content=[TextContent(type="text", text=GENERATION_UNAVAILABLE_MESSAGE)],
                structured_content={
                    "code": ToolErrorCode.UNAVAILABLE.value,
                    "message": GENERATION_UNAVAILABLE_MESSAGE,
                    "correlation_id": correlation_id,
                },
                is_error=True,
            )
        except SidecarNotLoadedError:
            # G3/TC-564 (RD3-120): the releases file is not on this server at all. An ``unavailable``
            # error that says so, never an empty list that reads as "this product has no releases".
            logger.warning(
                "tool '%s' has no release data: the releases sidecar is not loaded correlation_id=%s",
                params.name,
                correlation_id,
            )
            _finish(
                params.name,
                correlation_id,
                started_at,
                "error",
                ToolErrorCode.UNAVAILABLE,
                None,
                params.arguments or {},
            )
            return CallToolResult(
                content=[TextContent(type="text", text=RELEASES_NOT_LOADED_MESSAGE)],
                structured_content={
                    "code": ToolErrorCode.UNAVAILABLE.value,
                    "message": RELEASES_NOT_LOADED_MESSAGE,
                    "correlation_id": correlation_id,
                },
                is_error=True,
            )
        except SidecarFetchFailedError:
            # The data this tool reads could not be fetched during ingestion (G3/TC-507): an error
            # with one fixed message and the code ``unavailable`` - never an empty list, which an
            # agent reads as "this product has no releases".
            logger.warning(
                "tool '%s' has no usable release data: the releases sidecar is fetch_failed "
                "correlation_id=%s",
                params.name,
                correlation_id,
            )
            _finish(
                params.name,
                correlation_id,
                started_at,
                "error",
                ToolErrorCode.UNAVAILABLE,
                None,
                params.arguments or {},
            )
            return CallToolResult(
                content=[TextContent(type="text", text=RELEASES_FETCH_FAILED_MESSAGE)],
                structured_content={
                    "code": ToolErrorCode.UNAVAILABLE.value,
                    "message": RELEASES_FETCH_FAILED_MESSAGE,
                    "correlation_id": correlation_id,
                },
                is_error=True,
            )
        except Exception as exc:  # a tool-domain error - a normal CallToolResult, never a
            # protocol-level rejection, which happens earlier, at the transport, and never
            # reaches tool dispatch at all. The real exception (type, message, and the tool
            # name that raised it) is always logged server-side here, in full, regardless of
            # whether the sanitized text below turns out safe to hand back to the client.
            logger.error(
                "tool '%s' raised %s: %s correlation_id=%s",
                params.name,
                type(exc).__name__,
                exc,
                correlation_id,
            )
            text = public_error_message(exc, fallback=_GENERIC_ERROR_FALLBACK, correlation_id=correlation_id)
            _finish(
                params.name,
                correlation_id,
                started_at,
                "error",
                ToolErrorCode.INTERNAL,
                None,
                params.arguments or {},
            )
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={
                    "code": ToolErrorCode.INTERNAL.value,
                    "message": text,
                    "correlation_id": correlation_id,
                },
                is_error=True,
            )
        _finish(
            params.name,
            correlation_id,
            started_at,
            "success",
            None,
            result,
            validated_arguments.model_dump(),
        )
        # The envelope is additive (G3/TC-514): the result keeps its shape, a dict without a scope
        # gains one, repository text is marked untrusted, and the scope also rides beside the result.
        payload = annotate_result(params.name, _to_jsonable(result), scope)
        # G3/TC-515: the response is bounded BEFORE it is serialised, so the text channel and
        # structuredContent.result keep carrying the identical (budgeted) object.
        payload, budget_notes = apply_budget(
            params.name,
            payload,
            query=validated_arguments.model_dump().get("query"),
            family=scope.family,
        )
        content: list[Any] = [TextContent(type="text", text=render_result_text(payload))]
        structured: dict[str, Any] = {
            "result": payload,
            "scope": scope_object(scope),
            "content_origin": origin_of(params.name),
            "response_budget": budget_notes,
        }
        # G3/TC-542: an empty release list stays a bare list and is explained beside it.
        note = empty_result_note(params.name, payload)
        if note is not None:
            content.append(TextContent(type="text", text=note))
            structured[NOTE_KEY] = note
        # G3/TC-564 (RD3-124): a success carries the same correlation id an error does and the log line
        # carries, in the result's ``_meta`` so the pinned ``structuredContent`` keys stay exactly as they were.
        return CallToolResult(
            content=content,
            structured_content=structured,
            is_error=False,
            meta={CORRELATION_ID_META_KEY: correlation_id},  # type: ignore[call-arg]  # pydantic alias ``_meta``
        )

    def _instructions() -> str:
        return deployment_instructions(SERVER_INSTRUCTIONS, scope, _facts())

    return _DeploymentServer(  # type: ignore[call-overload]
        name=SERVER_NAME,
        version=server_version(),
        title=deployment_title(scope),
        instructions=_instructions,  # a provider, see _DeploymentServer
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


@asynccontextmanager
async def run_stdio(
    deployment_config: DeploymentConfig, manifest_store: GenerationManifestStore | None = None
):
    """Serve ``create_server`` over the process's stdin/stdout."""
    server = create_server(deployment_config, manifest_store)
    async with stdio_server() as (read_stream, write_stream):
        yield server.run(read_stream, write_stream, server.create_initialization_options())
