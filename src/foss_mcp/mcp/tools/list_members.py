"""list_members: the class graph - every method, property, and enum value a symbol declares.

Reuses ``get_symbol``'s exact-FQN lookup rather than a second parallel one, so an absent FQN
is the identical explicit ``NotFound`` in both tools, never a near match invented here. An
``Example: ...`` or ``Doc: ...`` pseudo-FQN is not a symbol, so it is a ``NotFound`` here too
(TC-386) rather than an empty member list.
"""

from __future__ import annotations

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import (
    _TYPE_LIMITATION_PREFIX,
    NotFound,
    ResolvedSignature,
    get_symbol,
)


def list_members(store: GenerationManifestStore, scope: Scope, fqn: str) -> tuple[str, ...] | NotFound:
    """Every method, property, and enum member *fqn* declares, in declaration order - methods
    first, then properties, then enum members. An absent FQN returns the same ``NotFound``
    ``get_symbol`` would. Base types are NOT included here - a base type is not a child of *fqn*,
    it stays exposed only through ``get_symbol``'s own ``bases`` field.

    TC-639 (KD2, G3/REQ-G3-015): this tool's return shape is a flat ``tuple[str, ...]`` - it has no
    field to add a ``known_defects`` list to without breaking its existing, simple contract. Its
    established precedent (TC-559's own type-level ``Limitation:`` line, below) is to PREPEND any
    type-level disclosure line directly into that same tuple, so it is chosen here too: a type-level
    known-defect disclosure line is prepended the same way, right after the limitation line and
    before the member lists. A single resolved member's lines (``signature.member_lines``, just
    below) already excludes the type-level limitation line for the identical reason - a member-only
    query is not asking about the type as a whole - so the type-level known-defect line is withheld
    there too, for the same reason, rather than inventing new, untested behaviour for that case.
    """
    signature = get_symbol(store, scope, fqn)
    if isinstance(signature, NotFound):
        return signature
    if isinstance(signature, ResolvedSignature) and signature.member:
        # TC-540: "<Type>.<member>" returns just that member's lines (all its overloads).
        return signature.member_lines
    # TC-559: a type that is not implemented says so first (``Limitation: not implemented in this build ...``);
    # a member that is not implemented carries the same phrase on its own line. A working type adds nothing.
    type_limitation = tuple(
        line for line in signature.limitations if line.startswith(_TYPE_LIMITATION_PREFIX)
    )
    return (
        type_limitation
        + signature.known_defects
        + signature.methods
        + signature.properties
        + signature.members
    )
