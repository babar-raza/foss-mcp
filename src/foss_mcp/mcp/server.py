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
import inspect
import json
import logging
import re
import typing
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import BaseModel, Field, ValidationError, field_validator

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig, Scope, resolve_scope
from foss_mcp.mcp.tools.find_examples import find_examples
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs, get_product_reference
from foss_mcp.mcp.tools.get_symbol import get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.mcp.tools.list_recent_changes import list_recent_changes
from foss_mcp.mcp.tools.lookup import lookup
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from foss_mcp.mcp.tools.search_docs import search_docs
from foss_mcp.mcp.tools.search_symbols import search_symbols

SERVER_NAME = "foss-mcp"
SERVER_VERSION = "0.0.0"

DEFAULT_MANIFEST_ROOT = Path("/data/manifests")


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
    def search_docs_tool(*, query: str, content_type: str, top_k: int = 10) -> Any:
        return search_docs(store, scope, query, content_type, top_k=top_k)

    return search_docs_tool


def _wrap_search_symbols(store: GenerationManifestStore, scope: Scope) -> Callable[..., Any]:
    def search_symbols_tool(*, query: str, top_k: int = 10) -> Any:
        return search_symbols(store, scope, query, top_k=top_k)

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


def _wrap_get_product_reference(inputs: ProductReferenceInputs) -> Callable[..., Any]:
    def get_product_reference_tool(*, section: str) -> Any:
        return get_product_reference(inputs, section)  # type: ignore[arg-type]

    return get_product_reference_tool


def _wrap_list_recent_changes(releases: Sequence[Release]) -> Callable[..., Any]:
    def list_recent_changes_tool(*, limit: int = 10) -> Any:
        return list_recent_changes(releases, limit=limit)

    return list_recent_changes_tool


def _wrap_report_index_freshness(store: GenerationManifestStore, scope: Scope) -> Callable[..., Any]:
    def report_index_freshness_tool(
        *, source_kind: str = "self_extracted", current_source_commit: str | None = None
    ) -> Any:
        return report_index_freshness(store, scope, source_kind, current_source_commit)

    return report_index_freshness_tool


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
        "task question with no known symbol name, e.g. query='how do I add a watermark to a "
        "PDF' or query='why does saving fail with a font error' - it tries documentation first "
        "and composes it with any verified code example find_examples has for the same query, "
        "falling back to a symbol search only if nothing matches. For a bare, identifier-like "
        "query (e.g. query='AFRelationship'), it tries search_symbols first instead - no need "
        "to call search_symbols yourself first in that case. Pass content_type explicitly "
        "(one of 'getting_started', 'developer_guide', 'troubleshooting', 'faq') only if you "
        "already know which documentation category you want; that delegates entirely to "
        "search_docs. If you already know the exact FQN, call get_symbol directly instead - "
        "it is exact where this tool ranks and can miss. Returns an honest miss, never a "
        "fabricated answer, when nothing genuinely matches."
    ),
    "search_docs": (
        "Search ONLY documentation already classified under one specific content_type - "
        "'getting_started', 'developer_guide', 'troubleshooting', or 'faq' (content_type is "
        "REQUIRED here; there is no 'search every category' mode on this tool - use lookup "
        "with content_type omitted for that). Call this directly instead of lookup when you "
        "already know which category applies, e.g. content_type='troubleshooting', "
        "query='save fails with a font error'. A query that matches only under a DIFFERENT "
        "content_type than requested returns an explicit miss, never a widened cross-category "
        "result - do not treat a miss here as proof the topic is undocumented anywhere; retry "
        "with a different content_type if you are unsure which one applies."
    ),
    "search_symbols": (
        "Search this deployment's self-extracted API surface by symbol name or fragment, e.g. "
        "query='Document' or query='AddWatermarkAnnotation'. PREFERRED over lookup when you "
        "already know you want a symbol/class/method rather than documentation prose. Returns "
        "an explicit miss (never a fuzzy near-match) when nothing matches this scope's "
        "currently active index; it never falls back to a different product or platform. Once "
        "you know the exact FQN, call get_symbol instead for the single exact, deterministic "
        "signature - this tool ranks and can return several partial matches."
    ),
    "get_symbol": (
        "Return the exact signature - kind, bases, methods, properties, members - for ONE "
        "fully-qualified symbol name, e.g. fqn='Aspose.Pdf.Document' or "
        "fqn='aspose.pdf.Document.save'. Matching is exact string equality against the "
        "indexed FQN, never a substring or fuzzy match. Verify the exact FQN via search_symbols "
        "first if you are not certain of it - do not guess it, since a near-miss returns an "
        "explicit NotFound rather than the closest thing this index happens to contain. "
        "PREFERRED one-shot call once you know the exact FQN - no need to chain search_symbols "
        "first in that case."
    ),
    "list_members": (
        "List every method, property, and enum member a symbol declares, in declaration order "
        "(methods first, then properties, then enum members) - the same exact-FQN lookup "
        "get_symbol uses under the hood, e.g. fqn='Aspose.Pdf.Document'. Call this instead of "
        "get_symbol when you only want the member names for a known type, not its full "
        "signature block. Base types are NOT included - a base class is not a member of its "
        "subclass; call get_symbol and read its own bases field for that. Returns the identical "
        "NotFound get_symbol would for an FQN that does not exist - verify the exact FQN via "
        "search_symbols first if unsure, do not guess it."
    ),
    "find_examples": (
        "Return a verified code snippet for a query - an exact FQN match first, e.g. "
        "fqn='Aspose.Pdf.Facades.PdfFileSignature', a lexical/semantic search over every other "
        "example-bearing chunk otherwise, e.g. query='add a digital signature to a PDF'. Never "
        "executes any snippet itself - it only returns text a human or another process may "
        "choose to run. Returns an explicit NoExampleFound miss when nothing verified exists; "
        "never fabricates or improvises an example. Prefer lookup instead when you also want "
        "documentation guidance for the same task question - lookup composes both into one "
        "answer."
    ),
    "get_product_reference": (
        "Fetch one specific reference section for this deployment's product: 'install' (e.g. "
        "'pip install aspose-pdf'), 'formats', 'limitations', 'compatibility' (the real value "
        "from the repo's own packaging manifest, e.g. an exact .NET TargetFramework string - "
        "never a flattened generic claim), 'license', 'support', 'contributing', or "
        "'agent_guidance'. Each section returns either its real content or an explicit "
        "NotAvailable with a reason - 'formats' and 'limitations' are currently NotAvailable "
        "for every product, and 'contributing'/'agent_guidance' are NotAvailable whenever the "
        "source repository genuinely lacks that file. Treat NotAvailable as authoritative "
        "absence, never a transient failure - do not retry expecting different content."
    ),
    "list_recent_changes": (
        "Return the most recent releases (newest first) for this deployment's product, each "
        "carrying an honest richness verdict (e.g. detailed / templated / none) alongside its "
        "raw body text - a templated release note that only fills in a version number is "
        "labeled as such, never reported the same way as a real migration note. Use this for "
        "'what changed recently' questions; it takes no query, only limit (default 10) - it is "
        "not a search tool, so do not pass a query string here."
    ),
    "report_index_freshness": (
        "Compare the indexed generation's recorded source commit against a "
        "current_source_commit you supply, for this deployment's active scope. Pass "
        "source_kind='self_extracted' (the default, and currently the only source kind "
        "self-extracted symbols use) unless you specifically need a different index. If "
        "current_source_commit is omitted, staleness cannot be ruled out and the report says "
        "so explicitly rather than assuming freshness - it never guesses. Check this before "
        "relying heavily on search results if you need to know whether the index reflects the "
        "latest upstream release."
    ),
}


def tool_description(name: str) -> str:
    """The real, agent-facing description for tool *name*: what it does, when to prefer it
    over a sibling tool, concrete real example values, and what not to guess. Raises
    ``KeyError`` for an unregistered name - there is no silent placeholder fallback.
    """
    return TOOL_DESCRIPTIONS[name]


# Shared per-field-name schema descriptions, reused everywhere a field of that name appears
# rather than hand-written once per tool.
_FIELD_DESCRIPTIONS: dict[str, str] = {
    "query": (
        "The search text. Use a natural-language task question (e.g. 'how do I add a "
        "watermark to a PDF') for lookup/find_examples, or a bare symbol name/fragment (e.g. "
        "'AFRelationship') for search_symbols. Must be non-empty."
    ),
    "content_type": (
        "One of the four documentation categories: 'getting_started', 'developer_guide', "
        "'troubleshooting', 'faq'. Required by search_docs; optional on lookup, where "
        "omitting it lets lookup infer the right source for you instead of you guessing the "
        "category. Must be non-empty when supplied."
    ),
    "top_k": (
        "Maximum number of matches to return. Must be a positive integer, capped at 100; a "
        "non-positive or absurdly large value is rejected rather than silently truncated."
    ),
    "fqn": (
        "The exact, fully-qualified symbol name to match verbatim, e.g. 'Aspose.Pdf.Document' "
        "or 'aspose.pdf.Document.save' - case-sensitive exact match only, never a substring or "
        "fuzzy match. Verify the exact FQN via search_symbols first if you are not certain of "
        "it - do not guess it."
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


def _require_non_empty(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


class LookupInput(BaseModel):
    query: str
    content_type: str | None = None
    top_k: int = Field(default=10, gt=0, le=_TOP_K_MAX)

    @field_validator("query")
    @classmethod
    def _query_not_empty(cls, value: str) -> str:
        return _require_non_empty(value, "query")

    @field_validator("content_type")
    @classmethod
    def _content_type_not_empty(cls, value: str | None) -> str | None:
        return value if value is None else _require_non_empty(value, "content_type")


class SearchDocsInput(BaseModel):
    query: str
    content_type: str
    top_k: int = Field(default=10, gt=0, le=_TOP_K_MAX)

    @field_validator("query", "content_type")
    @classmethod
    def _not_empty(cls, value: str, info: Any) -> str:
        return _require_non_empty(value, info.field_name)


class SearchSymbolsInput(BaseModel):
    query: str
    top_k: int = Field(default=10, gt=0, le=_TOP_K_MAX)

    @field_validator("query")
    @classmethod
    def _query_not_empty(cls, value: str) -> str:
        return _require_non_empty(value, "query")


class GetSymbolInput(BaseModel):
    fqn: str

    @field_validator("fqn")
    @classmethod
    def _fqn_not_empty(cls, value: str) -> str:
        return _require_non_empty(value, "fqn")


class ListMembersInput(BaseModel):
    fqn: str

    @field_validator("fqn")
    @classmethod
    def _fqn_not_empty(cls, value: str) -> str:
        return _require_non_empty(value, "fqn")


class FindExamplesInput(BaseModel):
    query: str
    top_k: int = Field(default=5, gt=0, le=_TOP_K_MAX)

    @field_validator("query")
    @classmethod
    def _query_not_empty(cls, value: str) -> str:
        return _require_non_empty(value, "query")


class GetProductReferenceInput(BaseModel):
    section: str

    @field_validator("section")
    @classmethod
    def _section_not_empty(cls, value: str) -> str:
        return _require_non_empty(value, "section")


class ListRecentChangesInput(BaseModel):
    limit: int = Field(default=10, gt=0, le=_TOP_K_MAX)


class ReportIndexFreshnessInput(BaseModel):
    source_kind: str = "self_extracted"
    current_source_commit: str | None = None

    @field_validator("source_kind")
    @classmethod
    def _source_kind_not_empty(cls, value: str) -> str:
        return _require_non_empty(value, "source_kind")


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


def public_error_message(exc: Exception, *, fallback: str) -> str:
    """The client-safe rendering of *exc*.

    Returns ``str(exc)`` verbatim only when it passes real safety checks: no filesystem path
    (Windows or POSIX, multi-segment), none of ``site-packages``/``Traceback``/``object at
    0x``, no embedded newline, and no more than ~300 characters. Otherwise the real exception
    (type and message) is logged server-side via the standard ``logging`` module before
    *fallback* is returned instead - the real detail is never simply discarded, only ever kept
    out of the client's own result text.
    """
    text = str(exc)
    if _contains_internal_detail(text):
        logger.warning(
            "suppressed unsafe exception text from an MCP client-facing result: type=%s message=%r",
            type(exc).__name__,
            text,
        )
        return fallback
    return text


_GENERIC_ERROR_FALLBACK = "An internal error occurred while handling this tool call."


def _build_tool_registry(
    store: GenerationManifestStore,
    scope: Scope,
    product_reference_inputs: ProductReferenceInputs,
    recent_releases: Sequence[Release],
) -> dict[str, Callable[..., Any]]:
    return {
        "lookup": _wrap_lookup(store, scope),
        "search_docs": _wrap_search_docs(store, scope),
        "search_symbols": _wrap_search_symbols(store, scope),
        "get_symbol": _wrap_get_symbol(store, scope),
        "list_members": _wrap_list_members(store, scope),
        "find_examples": _wrap_find_examples(store, scope),
        "get_product_reference": _wrap_get_product_reference(product_reference_inputs),
        "list_recent_changes": _wrap_list_recent_changes(recent_releases),
        "report_index_freshness": _wrap_report_index_freshness(store, scope),
    }


def create_server(
    deployment_config: DeploymentConfig,
    manifest_store: GenerationManifestStore | None = None,
    product_reference_inputs: ProductReferenceInputs | None = None,
    recent_releases: Sequence[Release] = (),
) -> Server:
    """Bootstrap the MCP server for one deployment, with all nine tools registered.

    ``resolve_scope`` fixes the scope once, here, from ``deployment_config`` alone; every
    tool wrapper closes over that one ``Scope`` for the lifetime of this server - no tool
    argument can ever change which product it answers for.
    """
    scope = resolve_scope(deployment_config, request=None)
    store = manifest_store or _default_manifest_store()
    inputs = product_reference_inputs or ProductReferenceInputs()
    registry = _build_tool_registry(store, scope, inputs, recent_releases)
    schemas = {name: schema_for(handler) for name, handler in registry.items()}

    async def _list_tools(context: Any, params: Any) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(name=name, description=tool_description(name), input_schema=schemas[name])
                for name in registry
            ]
        )

    async def _call_tool(context: Any, params: CallToolRequestParams) -> CallToolResult:
        handler = registry.get(params.name)
        if handler is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"unknown tool: {params.name}")],
                structured_content={"code": ToolErrorCode.NOT_FOUND.value},
                is_error=True,
            )
        try:
            # Validate once per tool via its own typed Pydantic model BEFORE the underlying
            # handler is ever called - a pydantic.ValidationError propagates straight to the
            # except-clause below, mapping a malformed argument to a clean, bounded
            # CallToolResult instead of whatever a downstream function happens to raise.
            input_model = _INPUT_MODELS[params.name]
            validated_arguments = input_model.model_validate(params.arguments or {})
            result = handler(**validated_arguments.model_dump())
        except ValidationError as exc:
            # TC-125's own validation-error path: a malformed argument, always INVALID_ARGUMENT.
            # Still reported through the shared sanitizer, never str(exc) directly - a custom
            # field validator's ValueError message could itself embed internal detail.
            text = public_error_message(exc, fallback=_GENERIC_ERROR_FALLBACK)
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"code": ToolErrorCode.INVALID_ARGUMENT.value, "message": text},
                is_error=True,
            )
        except Exception as exc:  # a tool-domain error - a normal CallToolResult, never a
            # protocol-level rejection, which happens earlier, at the transport, and never
            # reaches tool dispatch at all. The real exception (type, message, and the tool
            # name that raised it) is always logged server-side here, in full, regardless of
            # whether the sanitized text below turns out safe to hand back to the client.
            logger.error("tool '%s' raised %s: %s", params.name, type(exc).__name__, exc)
            text = public_error_message(exc, fallback=_GENERIC_ERROR_FALLBACK)
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"code": ToolErrorCode.INTERNAL.value, "message": text},
                is_error=True,
            )
        return CallToolResult(
            content=[TextContent(type="text", text=render_result_text(result))],
            structured_content={"result": _to_jsonable(result)},
            is_error=False,
        )

    return Server(
        name=SERVER_NAME, version=SERVER_VERSION, on_list_tools=_list_tools, on_call_tool=_call_tool
    )


@asynccontextmanager
async def run_stdio(
    deployment_config: DeploymentConfig, manifest_store: GenerationManifestStore | None = None
):
    """Serve ``create_server`` over the process's stdin/stdout."""
    server = create_server(deployment_config, manifest_store)
    async with stdio_server() as (read_stream, write_stream):
        yield server.run(read_stream, write_stream, server.create_initialization_options())
