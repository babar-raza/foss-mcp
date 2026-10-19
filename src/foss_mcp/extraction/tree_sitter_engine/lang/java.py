# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/lang/java.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""extraction/lang/java.py â€” Java language adapter (TC-MT040-42).

See extraction/lang/__init__.py for the shared adapter contract this module
implements: declaration_kinds(), doc_anchor(), parse_doc(), export_surface(),
clear_cache().

No reachability signal is implemented for Java yet -- export_surface()
returns (None, None) unconditionally (fail-safe default). Java is not part
of this mission's TypeScript-hardening scope; declaring the seam here keeps
the adapter set uniform for the day a real signal is added.
"""

from __future__ import annotations

import re
from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine.tree_helpers import (
    _CLASS_TYPES,
    _FUNC_TYPES,
    _IMPORT_TYPES,
    _MODULE_TYPES,
    child_by_field,
    node_text,
)

_LANG = "java"


# ---------------------------------------------------------------------------
# declaration_kinds
# ---------------------------------------------------------------------------


def declaration_kinds() -> dict:
    """Return this language's tree-sitter node-kind sets used during
    extraction. Keys: "class_types", "func_types", "module_types",
    "import_types" -- direct per-language subsets of tree_helpers' shared
    cross-language dicts.
    """
    return {
        "class_types": _CLASS_TYPES.get(_LANG, set()),
        "func_types": _FUNC_TYPES.get(_LANG, set()),
        "module_types": _MODULE_TYPES.get(_LANG, set()),
        "import_types": _IMPORT_TYPES.get(_LANG, set()),
    }


# ---------------------------------------------------------------------------
# doc_anchor
# ---------------------------------------------------------------------------


def doc_anchor(node):
    """Return the node whose doc comment belongs to *node*.

    Javadoc (``/** ... */``) comments precede the item as a single sibling
    comment node -- no wrapper redirection needed, identity.
    """
    return node


# ---------------------------------------------------------------------------
# parse_doc
# ---------------------------------------------------------------------------


def _first_sentence(text: str) -> str:
    """Return the first sentence (up to first period-space or newline).

    Mirrors api_surface._first_sentence exactly.
    """
    text = text.strip()
    m = re.match(r"^(.+?[.!?])(?:\s|$)", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.split("\n")[0].strip()[:200]


def parse_doc(raw: str) -> dict:
    """Parse a raw Javadoc comment block (``/** ... */``, full text
    including delimiters) into ``{"summary": str}``.

    Thin, independently-tested reproduction of what
    api_surface._extract_doc_comment already does for the "javadoc" style
    today.
    """
    if not raw.startswith("/**"):
        return {"summary": ""}
    cleaned = re.sub(r"/\*\*|\*/|\n\s*\*\s?", " ", raw).strip()
    return {"summary": _first_sentence(cleaned)}


# ---------------------------------------------------------------------------
# export_surface
# ---------------------------------------------------------------------------


def export_surface(repo: Path, pkg_root: Path) -> tuple[set[str] | None, Path | None]:
    """No reachability signal implemented for Java yet -- always (None, None)
    (fail-safe: caller must treat every item as reachable).
    """
    return None, None


# ---------------------------------------------------------------------------
# TC-557 (REQ-G3-009, RD3-040..047): member facts the Java language defines and the extraction used to drop
# ---------------------------------------------------------------------------

_ANONYMOUS_BODY_PARENTS = frozenset({"object_creation_expression", "enum_constant"})


def _one_line(text: str) -> str:
    return " ".join(text.split())


def modifier_keywords(node) -> set[str]:
    """The keyword modifiers (``public``, ``static``, ``final``, ...) of a declaration: the token children
    of its ``modifiers`` node. Annotations are not keywords and are never included."""
    found: set[str] = set()
    for child in node.children:
        if child.type == "modifiers":
            found.update(sub.type for sub in child.children if sub.type.isalpha() and sub.is_named is False)
    return found


def is_static(node) -> bool:
    """True when the declaration carries the ``static`` modifier (RD3-042)."""
    return "static" in modifier_keywords(node)


def spread_param(node) -> dict[str, object] | None:
    """The parameter a ``spread_parameter`` (``Vector3... points``) declares, as ``{name, type, varargs}``.

    The type keeps its ``...`` (``Vector3...``): a varargs parameter is an array parameter a caller fills with
    any number of arguments, and a rendering without the three dots reads as ``fromPoints()`` (RD3-040).
    ``None`` for a malformed node.
    """
    declarator = next((child for child in node.children if child.type == "variable_declarator"), None)
    name_node = child_by_field(declarator, "name") if declarator is not None else None
    if name_node is None:
        return None
    type_node = next(
        (
            child
            for child in node.children
            if child.is_named and child.type not in ("modifiers", "variable_declarator")
        ),
        None,
    )
    base = _one_line(node_text(type_node)) if type_node is not None else ""
    return {"name": node_text(name_node), "type": f"{base}...", "varargs": True}


def throws_clause(node) -> list[str]:
    """The exception types a method or constructor declares with ``throws``, in source order (RD3-041)."""
    clause = next((child for child in node.children if child.type == "throws"), None)
    if clause is None:
        return []
    return [_one_line(node_text(child)) for child in clause.children if child.is_named]


def type_parameters(node) -> str:
    """The generic type parameters of a method or constructor with their bounds, as written
    (``<U extends Comparable<U>>``); empty for a member that declares none (RD3-044)."""
    declared = child_by_field(node, "type_parameters")
    return _one_line(node_text(declared)) if declared is not None else ""


def in_anonymous_class(node, owner) -> bool:
    """True when *node* sits inside the body of an anonymous class (``new Runnable() { ... }``) or of an
    enum constant (``A { ... }``) that is itself inside *owner*: its members are not members of *owner* (RD3-045)."""
    parent = node.parent
    while parent is not None and parent != owner:
        if parent.type == "class_body" and parent.parent is not None:
            if parent.parent.type in _ANONYMOUS_BODY_PARENTS:
                return True
        parent = parent.parent
    return False


def _body(cnode):
    return child_by_field(cnode, "body")


def declares_constructor(cnode) -> bool:
    """True when the class body itself declares a constructor, of any visibility."""
    body = _body(cnode)
    return body is not None and any(child.type == "constructor_declaration" for child in body.children)


def gets_implicit_constructor(cnode) -> bool:
    """True when the Java language gives *cnode* a default constructor (JLS 8.8.9): a ``class`` that declares no
    constructor at all. An interface, an enum, an annotation type and a record never get one (RD3-047)."""
    return cnode.type == "class_declaration" and not declares_constructor(cnode)


def compact_constructor(cnode):
    """The compact canonical constructor (``R { ... }``) a record body declares, else ``None``."""
    body = _body(cnode)
    for child in body.children if body is not None else []:
        if child.type == "compact_constructor_declaration":
            return child
    return None


def declares_canonical_constructor(cnode, component_types: list[str]) -> bool:
    """True when a record body declares its canonical constructor with a full parameter list: a constructor
    whose parameter types are the component types in order (JLS 8.10.4). A compact one has no list; see
    ``compact_constructor``."""
    body = _body(cnode)
    if body is None:
        return False
    for child in body.children:
        if child.type == "constructor_declaration":
            formal = child_by_field(child, "parameters")
            types: list[str] = []
            for param in formal.children if formal is not None else []:
                if param.type == "spread_parameter":
                    spread = spread_param(param)
                    types.append(str(spread["type"]) if spread else "")
                elif param.type == "formal_parameter":
                    type_node = child_by_field(param, "type")
                    types.append(_one_line(node_text(type_node)) if type_node is not None else "")
            if types == component_types:
                return True
    return False


def declared_method_arity(cnode) -> set[tuple[str, int]]:
    """``(name, parameter count)`` of every method the body of *cnode* declares directly."""
    body = _body(cnode)
    declared: set[tuple[str, int]] = set()
    for child in body.children if body is not None else []:
        if child.type != "method_declaration":
            continue
        name_node = child_by_field(child, "name")
        formal = child_by_field(child, "parameters")
        count = sum(
            1 for p in (formal.children if formal is not None else []) if p.type.endswith("parameter")
        )
        declared.add((node_text(name_node), count))
    return declared


def declarator_dimensions(declarator) -> str:
    """The array brackets a declarator carries (``int x[]`` -> ``[]``), else empty."""
    dims = child_by_field(declarator, "dimensions")
    return _one_line(node_text(dims)).replace(" ", "") if dims is not None else ""


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


def clear_cache() -> None:
    """No-op -- this module holds no cached state."""
    return None
