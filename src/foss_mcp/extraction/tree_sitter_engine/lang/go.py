# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/lang/go.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""extraction/lang/go.py â€” Go language adapter (TC-MT040-42).

See extraction/lang/__init__.py for the shared adapter contract this module
implements: declaration_kinds(), doc_anchor(), parse_doc(), export_surface(),
clear_cache().

doc_anchor() is the one language whose current behavior is NOT the trivial
identity function: for a `type_spec` node, api_surface.py already special-
cases the godoc comment lookup to use the node's parent `type_declaration`
instead (SFX-2: "For Go type_spec nodes, the godoc comment precedes the
parent type_declaration node, not the type_spec itself"). This module makes
that existing, already-correct special case declarative instead of an inline
`if language == "go" and cnode.type == "type_spec"` branch at the call site.

No reachability signal is implemented for Go yet -- export_surface() returns
(None, None) unconditionally (fail-safe default).
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
    find_child_by_type,
    node_text,
)

_LANG = "go"


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

    SFX-2 (pre-existing, relocated here unchanged): a `type_spec` node's
    godoc comment precedes its PARENT `type_declaration` node, not the
    `type_spec` itself -- e.g. `type Foo struct { ... }` where the comment
    sits above the `type` keyword. Every other Go node type uses the
    identity default.
    """
    if node.type == "type_spec" and node.parent is not None:
        return node.parent
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
    """Parse a raw godoc comment block into ``{"summary": str}``.

    *raw* is expected to be the already-gathered, newline-joined run of
    ``//`` comment lines (each line still carrying its ``//`` prefix) --
    tree-walking to gather that multi-line run stays in
    api_surface._extract_doc_comment for now (see
    extraction/lang/__init__.py's module docstring). This reproduces that
    function's per-line ``//``-stripping (a blank `//` line ends the summary
    paragraph, even before any content has been collected -- an exact,
    intentionally-preserved quirk of the original godoc branch, unlike the
    rust/xml_doc branches which only stop on a blank line AFTER collecting
    at least one line) and first-sentence logic on an already-joined block.
    """
    para_lines: list[str] = []
    for ln in raw.split("\n"):
        bare = ln.strip().lstrip("/").strip()
        if not bare:  # blank `//` line ends the summary paragraph
            break
        para_lines.append(bare)
    cleaned = " ".join(para_lines)
    return {"summary": _first_sentence(cleaned) if cleaned else ""}


# ---------------------------------------------------------------------------
# export_surface
# ---------------------------------------------------------------------------


def export_surface(repo: Path, pkg_root: Path) -> tuple[set[str] | None, Path | None]:
    """No reachability signal implemented for Go yet -- always (None, None)
    (fail-safe: caller must treat every item as reachable).
    """
    return None, None


# ---------------------------------------------------------------------------
# TC-535 (REQ-G3-009, RD3-025 to RD3-027): package identity, type shapes and promoted members
# ---------------------------------------------------------------------------

# The mandated switch. True: every Go symbol is recorded with the name and the import path of its package
# (the module path of ``go.mod`` plus the sub-directory), so a symbol of a sub-package (pdf/go ``ai``) is
# never served as if it were the root package. False: the extractor treats the repository as ONE flat
# package, as it did before TC-535 (no ``package`` / ``import_path`` on any entry, receiver methods and
# constants bind to a type by its bare name).
GO_SUBPACKAGE_IMPORT_PATHS = True

_MODULE_LINE = re.compile(r"^\s*module\s+(?:\"([^\"]+)\"|(\S+))", re.MULTILINE)
# A directory segment under which Go forbids importing from outside the module (the ``internal`` rule).
_NOT_IMPORTABLE_SEGMENTS = frozenset({"internal"})


def module_path(root: Path) -> str:
    """The module path ``go.mod`` at *root* declares, or ``""`` when there is none."""
    go_mod = root / "go.mod"
    try:
        text = go_mod.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = _MODULE_LINE.search(text)
    if match is None:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def find_module(directory: Path, ceiling: Path, cache: dict[Path, tuple[Path, str]]) -> tuple[Path, str]:
    """The module a Go source directory belongs to: ``(module root, module path)``.

    The nearest ``go.mod`` at or above *directory* decides, searched no higher than *ceiling* (the repository
    root), so a nested module (a ``tools/`` directory with a ``go.mod`` of its own) gets its own module path.
    ``(ceiling, "")`` when there is none: the import path is then unknown and is not invented.
    """
    trail: list[Path] = []
    current = directory
    result: tuple[Path, str] | None = None
    while True:
        known = cache.get(current)
        if known is not None:
            result = known
            break
        trail.append(current)
        if (current / "go.mod").is_file():
            result = (current, module_path(current))
            break
        if current == ceiling or current.parent == current:
            break
        current = current.parent
    if result is None:
        result = (ceiling, "")
    for visited in trail:
        cache[visited] = result
    return result


def package_name(root_node) -> str:
    """The name a Go file's ``package`` clause declares (``""`` when it has none)."""
    for child in root_node.children:
        if child.type == "package_clause":
            for sub in child.children:
                if sub.type == "package_identifier":
                    return node_text(sub)
    return ""


def import_path(module: str, rel_dir: str) -> str:
    """The import path of the package in *rel_dir* (posix, ``""`` or ``"."`` for the module root) of *module*.

    ``""`` when the module path is unknown: a relative directory alone is not an import path.
    """
    if not module:
        return ""
    sub = rel_dir.strip("/")
    if sub in ("", "."):
        return module
    return f"{module}/{sub}"


def is_importable(rel_dir: str, package: str) -> bool:
    """False for a ``package main`` and for anything under an ``internal`` directory: nobody can import it."""
    if package == "main":
        return False
    return not any(part in _NOT_IMPORTABLE_SEGMENTS for part in rel_dir.replace("\\", "/").split("/"))


def is_top_level_type(spec) -> bool:
    """True for a ``type_spec`` declared at package level (not inside a function body)."""
    declaration = spec.parent
    return (
        declaration is not None
        and declaration.parent is not None
        and declaration.parent.type == "source_file"
    )


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _embedded_ref(type_node) -> tuple[str, str] | None:
    """``(qualifier, name)`` of an embedded type node (``Base``, ``pkg.Other``, ``Inner[T]``), or None."""
    node = type_node
    if node.type == "generic_type":
        inner = child_by_field(node, "type") or find_child_by_type(node, "type_identifier")
        if inner is None:
            return None
        node = inner
    if node.type == "type_identifier":
        return "", node_text(node)
    if node.type == "qualified_type":
        qualifier = child_by_field(node, "package")
        name = child_by_field(node, "name")
        if qualifier is not None and name is not None:
            return node_text(qualifier), node_text(name)
    return None


def struct_shape(spec, rel: str = "") -> dict:
    """The embedded types and the fields of a ``type_spec``'s struct or interface body.

    ``{"kind": "struct" | "interface" | "other", "embedded": [(qualifier, name), ...],
    "fields": [{"name", "type", "line"}, ...]}``. A field's type is the ``type`` field of its declaration,
    never the struct tag that follows it (the RD3-027 defect: ``Cell.XMLName`` was served with the type
    text `` `xml:"c"` ``). ``fields`` holds every named field, exported or not (an unexported field still
    hides a promoted member of the same name).
    """
    body = child_by_field(spec, "type")
    shape: dict = {"kind": "other", "embedded": [], "fields": []}
    if body is None:
        return shape
    if body.type == "struct_type":
        shape["kind"] = "struct"
        field_list = find_child_by_type(body, "field_declaration_list")
        if field_list is None:
            return shape
        for declaration in field_list.children:
            if declaration.type != "field_declaration":
                continue
            type_node = child_by_field(declaration, "type")
            names = [
                node_text(n)
                for n in declaration.children_by_field_name("name")
                if n.type == "field_identifier"
            ]
            if not names:
                reference = _embedded_ref(type_node) if type_node is not None else None
                if reference is not None:
                    shape["embedded"].append(reference)
                continue
            type_text = _one_line(node_text(type_node)) if type_node is not None else ""
            for name in names:
                shape["fields"].append(
                    {"name": name, "type": type_text, "line": declaration.start_point[0] + 1, "file": rel}
                )
        return shape
    if body.type == "interface_type":
        shape["kind"] = "interface"
        for child in body.children:
            if child.type != "type_elem":
                continue
            for sub in child.children:
                reference = _embedded_ref(sub)
                if reference is not None:
                    shape["embedded"].append(reference)
                    break
        return shape
    return shape


_OTHER_UNDERLYING_NODES = frozenset(
    {
        "function_type",
        "map_type",
        "slice_type",
        "array_type",
        "pointer_type",
        "channel_type",
        "generic_type",
        "qualified_type",
        "parenthesized_type",
    }
)
_UNDERLYING_LIMIT = 300


def underlying_text(spec) -> str:
    """The right-hand side of a defined type that is neither a struct, an interface nor a bare identifier.

    ``type RowCallback func(rowIndex int, cells map[string]string) error`` gives the whole ``func(...)
    error`` signature (RD3-027), ``type Row []Cell`` gives ``[]Cell``. A struct, an interface or a bare
    identifier (``type SaveFormat string``, already in ``bases``) gives ``""``.
    """
    body = child_by_field(spec, "type")
    if body is None or body.type not in _OTHER_UNDERLYING_NODES:
        return ""
    return _one_line(node_text(body))[:_UNDERLYING_LIMIT]


def resolve_embedded(
    package_key: str,
    reference: tuple[str, str],
    shapes: dict,
    packages_by_name: dict[str, set[str]],
) -> tuple[str, str] | None:
    """The ``(package key, type name)`` of an embedded type that this repository declares, else None.

    An unqualified name is a type of the same package; a qualified one (``other.Base``) is looked up in the
    one package of the repository that declares that package name. A type of the standard library or of
    another module has no shape here and is not resolved.
    """
    qualifier, name = reference
    if not qualifier:
        key = (package_key, name)
        return key if key in shapes else None
    candidates = packages_by_name.get(qualifier, set())
    if len(candidates) != 1:
        return None
    key = (next(iter(candidates)), name)
    return key if key in shapes else None


def promote_members(
    start: tuple[str, str],
    shapes: dict,
    methods_by_type: dict,
    packages_by_name: dict[str, set[str]],
) -> tuple[list[dict], list[dict]]:
    """The exported methods and fields that type *start* gets through embedding, by the Go selector rules.

    Breadth first from the type's own embedded fields: a name found at the shallowest depth wins, a name the
    type itself declares (method, field, or the embedded field's own name) hides any deeper one, and a name
    found more than once at the same depth is ambiguous and is not promoted (interface methods that agree
    on name and signature are one). Embedding is followed through unexported types (``type TextBox struct
    { fieldBase }`` promotes the methods of the unexported ``fieldBase``). ``shapes`` is ``{(package key,
    type name): struct_shape(...)}`` for every type of the repository, ``methods_by_type`` is ``{(package
    key, type name): [method entry, ...]}`` for every type, exported or not. Each returned member is a copy
    of the declaring entry with ``inherited_from`` and ``promoted_from`` set to the declaring type's name.
    """
    shape = shapes.get(start)
    if shape is None:
        return [], []
    package_key = start[0]
    claimed: set[str] = {m["name"] for m in methods_by_type.get(start, [])}
    claimed |= {f["name"] for f in shape["fields"]}
    claimed |= {name for _, name in shape["embedded"]}
    promoted_methods: list[dict] = []
    promoted_fields: list[dict] = []
    expanded: set[tuple[str, str]] = {start}
    frontier = [
        resolved
        for reference in shape["embedded"]
        if (resolved := resolve_embedded(package_key, reference, shapes, packages_by_name)) is not None
    ]
    while frontier:
        candidates: dict[str, list[tuple[str, dict, tuple[str, str]]]] = {}
        order: list[str] = []
        next_frontier: list[tuple[str, str]] = []
        for key in frontier:
            if key in expanded and frontier.count(key) == 1:
                continue
            member_shape = shapes[key]
            for method in methods_by_type.get(key, []):
                if method["name"][:1].isupper():
                    candidates.setdefault(method["name"], []).append(("method", method, key))
                    order.append(method["name"])
            for field in member_shape["fields"]:
                if field["name"][:1].isupper():
                    candidates.setdefault(field["name"], []).append(("field", field, key))
                    order.append(field["name"])
            for reference in member_shape["embedded"]:
                resolved = resolve_embedded(key[0], reference, shapes, packages_by_name)
                if resolved is not None:
                    if reference[1][:1].isupper():
                        candidates.setdefault(reference[1], []).append(
                            ("embedded", {"name": reference[1]}, key)
                        )
                        order.append(reference[1])
                    next_frontier.append(resolved)
        seen_names: set[str] = set()
        for name in order:
            if name in seen_names:
                continue
            seen_names.add(name)
            found = candidates[name]
            if name in claimed:
                continue
            kinds = {kind for kind, _, _ in found}
            if len(found) > 1:
                same_interface_method = (
                    kinds == {"method"}
                    and all(shapes[key]["kind"] == "interface" for _, _, key in found)
                    and len({_signature(member) for _, member, _ in found}) == 1
                )
                if not same_interface_method:
                    continue
            kind, member, origin = found[0]
            if kind == "embedded":
                continue
            copy_of = dict(member)
            copy_of["inherited_from"] = origin[1]
            copy_of["promoted_from"] = origin[1]
            (promoted_methods if kind == "method" else promoted_fields).append(copy_of)
        claimed |= set(candidates)
        expanded |= set(frontier)
        frontier = [key for key in next_frontier if key not in expanded]
    return promoted_methods, promoted_fields


def _signature(method: dict) -> tuple:
    return (
        tuple(p.get("type", "") for p in method.get("params", [])),
        method.get("return_type", ""),
    )


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


def clear_cache() -> None:
    """No-op -- this module holds no cached state."""
    return None
