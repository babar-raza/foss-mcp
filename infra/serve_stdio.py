"""Serving container entrypoint: run the MCP server over stdio for one product deployment.

Deployment identity comes from environment variables set at deploy time, never from anything
a client sends - the same rule ``foss_mcp.mcp.routing.resolve_scope`` enforces at the code
level, applied here at the container level too.
"""

from __future__ import annotations

import os

import anyio

from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import run_stdio


async def main() -> None:
    config = DeploymentConfig(
        family=os.environ.get("FOSS_MCP_FAMILY", "pdf"),
        platform=os.environ.get("FOSS_MCP_PLATFORM", "net"),
        source_kind=os.environ.get("FOSS_MCP_SOURCE_KIND", "self_extracted"),
    )
    async with run_stdio(config) as running:
        await running


if __name__ == "__main__":
    anyio.run(main)
