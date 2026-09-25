"""list_members: the class graph - every method, property, and enum value a symbol declares.

Reuses ``get_symbol``'s exact-FQN lookup rather than a second parallel one, so an absent FQN
is the identical explicit ``NotFound`` in both tools, never a near match invented here.
"""

from __future__ import annotations

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import NotFound, get_symbol


def list_members(store: GenerationManifestStore, scope: Scope, fqn: str) -> tuple[str, ...] | NotFound:
    """Every method, property, and enum member *fqn* declares, in declaration order - methods
    first, then properties, then enum members. An absent FQN returns the same ``NotFound``
    ``get_symbol`` would. Base types are NOT included here - a base type is not a child of *fqn*,
    it stays exposed only through ``get_symbol``'s own ``bases`` field.
    """
    signature = get_symbol(store, scope, fqn)
    if isinstance(signature, NotFound):
        return signature
    return signature.methods + signature.properties + signature.members
