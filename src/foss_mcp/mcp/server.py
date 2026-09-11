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
import typing
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool

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
    required unless the parameter has a default.
    """
    signature = inspect.signature(func)
    hints = typing.get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        properties[name] = _json_type(hints.get(name, str))
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
                Tool(name=name, description=f"foss-mcp tool: {name}", input_schema=schemas[name])
                for name in registry
            ]
        )

    async def _call_tool(context: Any, params: CallToolRequestParams) -> CallToolResult:
        handler = registry.get(params.name)
        if handler is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"unknown tool: {params.name}")], is_error=True
            )
        try:
            result = handler(**(params.arguments or {}))
        except Exception as exc:  # a tool-domain error - a normal CallToolResult, never a
            # protocol-level rejection, which happens earlier, at the transport, and never
            # reaches tool dispatch at all.
            return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
        return CallToolResult(
            content=[TextContent(type="text", text=repr(result))],
            structured_content={"result": _to_jsonable(result)},
            is_error=False,
        )

    return Server(
        name=SERVER_NAME, version=SERVER_VERSION, on_list_tools=_list_tools, on_call_tool=_call_tool
    )


@asynccontextmanager
async def run_stdio(deployment_config: DeploymentConfig, manifest_store: GenerationManifestStore | None = None):
    """Serve ``create_server`` over the process's stdin/stdout."""
    server = create_server(deployment_config, manifest_store)
    async with stdio_server() as (read_stream, write_stream):
        yield server.run(read_stream, write_stream, server.create_initialization_options())
