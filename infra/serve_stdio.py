"""Serving container entrypoint: run the MCP server over stdio for one product deployment.

Deployment identity comes from environment variables set at deploy time, never from anything
a client sends - the same rule ``foss_mcp.mcp.routing.resolve_scope`` enforces at the code
level, applied here at the container level too. There are deliberately no defaults for either
identity variable: an unset identity must never fall back to another product, matching the rule
``infra/serve_http.py`` already states and enforces for the same two variables.

Logging and the required-and-loud env read are NOT reimplemented here: this module reuses
``infra/serve_http.py``'s own ``configure_logging`` and ``_required_env`` directly, so stdio and
HTTP serving share exactly one definition of "how we log" and "how we fail on a missing
identity" rather than two copies that can drift.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio

# infra/ is not a package (no __init__.py) - import serve_http from its path, the same way
# tests/infra/test_serve_http.py and tests/infra/test_helm_manifest_step.py already do.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from serve_http import _required_env, configure_logging  # noqa: E402

from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import run_stdio


async def main() -> None:
    configure_logging()
    config = DeploymentConfig(
        family=_required_env("FOSS_MCP_FAMILY"),
        platform=_required_env("FOSS_MCP_PLATFORM"),
        source_kind=os.environ.get("FOSS_MCP_SOURCE_KIND", "self_extracted"),
    )
    async with run_stdio(config) as running:
        await running


if __name__ == "__main__":
    anyio.run(main)
