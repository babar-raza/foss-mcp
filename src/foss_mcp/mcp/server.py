"""Bootstrap the MCP server on a maintained SDK - not a hand-rolled envelope.

Uses the official Model Context Protocol Python SDK (PyPI package ``mcp``). The reference
system's own transport implementation is confirmed non-conformant; this project treats its
transport files as shape reference only, never as something to port, and speaks the protocol
through a maintained SDK instead.

BLOCKED_EXTERNAL, reported per AGENTS.md's blocking taxonomy: ``mcp`` is not installed in this
project's own environment and is not pinned in ``requirements.in``/``requirements.lock`` -
``requirements.*`` is coordinator-owned and out of this card's write_paths. This module was
written against, and interactively verified line-by-line against, the real ``mcp==2.2.0`` API
surface (``mcp.server.lowlevel.Server``, ``mcp.server.stdio.stdio_server``,
``mcp.types.ListToolsResult``) in a disposable virtualenv outside this project's own dependency
graph - never installed into ``.venv`` or added to ``requirements.*`` here. It cannot itself be
imported or exercised until the dependency is pinned; no test file in this card exercises it
for exactly that reason (``tests/mcp/test_routing.py`` and ``tests/mcp/test_revision_negotiation.py``
cover the two modules that need no SDK at all).

Scope and protocol-revision policy are deliberately NOT wired in here. Both are independent,
fully-tested modules (``foss_mcp.mcp.routing.resolve_scope``,
``foss_mcp.mcp.revision_negotiation.negotiate_revision``) a later card's tool handlers will
call; the low-level ``Server`` reserves ``initialize`` for its own runner and refuses to let a
caller override it, so this module's job here is narrower - an empty tool registry, ready for
those handlers to be added without disturbing how the server itself is bootstrapped.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import ListToolsResult

from foss_mcp.mcp.routing import DeploymentConfig, resolve_scope

SERVER_NAME = "foss-mcp"
SERVER_VERSION = "0.0.0"


async def _list_tools(context: Any, params: Any) -> ListToolsResult:
    """No tools are registered yet - a later card populates this registry."""
    return ListToolsResult(tools=[])


def create_server(deployment_config: DeploymentConfig) -> Server:
    """Bootstrap the MCP server for one deployment.

    Confirms this deployment's scope up front (``resolve_scope`` never reads a request, so
    this call cannot depend on anything but ``deployment_config``) and wires an empty tool
    registry - no tools are registered by this card.
    """
    resolve_scope(deployment_config, request=None)
    return Server(name=SERVER_NAME, version=SERVER_VERSION, on_list_tools=_list_tools)


@asynccontextmanager
async def run_stdio(deployment_config: DeploymentConfig):
    """Serve ``create_server(deployment_config)`` over the process's stdin/stdout."""
    server = create_server(deployment_config)
    async with stdio_server() as (read_stream, write_stream):
        yield server.run(read_stream, write_stream, server.create_initialization_options())
