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

The server is built exactly as the HTTP path builds it: with the product-reference inputs and the recent
releases read from the sidecars for this identity in the manifests directory
(``serve_http._serving_product_reference_inputs`` / ``_serving_recent_releases``), so a tool answers the
same over stdio as over HTTP. Only the transport differs.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

# infra/ is not a package (no __init__.py) - import serve_http from its path, the same way
# tests/infra/test_serve_http.py and tests/infra/test_helm_manifest_step.py already do.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from serve_http import (  # noqa: E402
    DEFAULT_MANIFESTS_DIR,
    MANIFESTS_DIR_ENV,
    _required_env,
    _serving_product_reference_inputs,
    _serving_recent_releases,
    configure_logging,
)

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import _default_manifest_store, create_server


def build_stdio_server(
    deployment_config: DeploymentConfig,
    manifest_store: GenerationManifestStore | None = None,
    manifests_dir: Path | None = None,
) -> Server:
    """The same ``create_server`` call ``serve_http.build_app`` makes: the store defaults to the
    deployment's own, ``manifests_dir`` to ``FOSS_MCP_MANIFESTS_DIR`` or ``/data/manifests``, and the
    sidecars in it feed ``product_reference_inputs`` and ``recent_releases``."""
    resolved_dir = (
        manifests_dir
        if manifests_dir is not None
        else Path(os.environ.get(MANIFESTS_DIR_ENV, DEFAULT_MANIFESTS_DIR))
    )
    return create_server(
        deployment_config,
        manifest_store if manifest_store is not None else _default_manifest_store(),
        product_reference_inputs=_serving_product_reference_inputs(resolved_dir, deployment_config),
        recent_releases=_serving_recent_releases(resolved_dir, deployment_config),
    )


@asynccontextmanager
async def run_stdio(deployment_config: DeploymentConfig):
    """Serve ``build_stdio_server`` over the process's stdin/stdout."""
    server = build_stdio_server(deployment_config)
    async with stdio_server() as (read_stream, write_stream):
        yield server.run(read_stream, write_stream, server.create_initialization_options())


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
