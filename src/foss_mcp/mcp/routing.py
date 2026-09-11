"""Deployment-identity-only scope resolution.

Product identity for a request comes ENTIRELY from how this server process was deployed
(``deployment_config``) - never from anything the client sent. A client-controlled scope
header is a confirmed defect in the reference system: it let one deployment answer for a
product it was never configured to serve, because the isolation boundary was something a
client could simply ask its way past. ``resolve_scope`` enforces the fix structurally: its
signature accepts ``request``, but the request is never read - not a header, not a tool
argument, not any field of it. Scope is deployment identity, plain and inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    """The product identity a deployment serves - the same addressable unit
    ``foss_mcp.indexing.generation_manifest.GenerationKey`` scopes a generation by.
    """

    family: str
    platform: str
    source_kind: str = "self_extracted"


@dataclass(frozen=True)
class DeploymentConfig:
    """What THIS server process was deployed to serve - fixed at deploy time, never at
    request time. The only input ``resolve_scope`` is allowed to depend on.
    """

    family: str
    platform: str
    source_kind: str = "self_extracted"


def resolve_scope(deployment_config: DeploymentConfig, request: object) -> Scope:
    """The scope this deployment serves - deployment identity only, nothing from ``request``.

    ``request`` is accepted (the MCP request or tool-call context a caller has in hand) but
    never read: no header, no tool argument, no field of it may influence the result. This is
    the fix for the confirmed reference-system defect where a client-supplied scope header was
    honoured, letting one deployment answer for a product it was never configured to serve.
    """
    del request  # deliberately, completely unused - see module and function docstrings.
    return Scope(
        family=deployment_config.family,
        platform=deployment_config.platform,
        source_kind=deployment_config.source_kind,
    )
