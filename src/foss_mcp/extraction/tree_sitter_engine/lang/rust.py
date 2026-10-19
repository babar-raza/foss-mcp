# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/lang/rust.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""extraction/lang/rust.py â€” Rust language adapter (TC-MT040-42).

See extraction/lang/__init__.py for the shared adapter contract this module
implements: declaration_kinds(), doc_anchor(), parse_doc(), export_surface(),
clear_cache().

export_surface() carries the exact, behavior-preserving relocation of what
was api_surface.py's ``_rust_reexported_names`` (RC-W1-004) -- the one hard
rule for this card is that rust's reachability output must not change in any
way a golden test would catch.
"""

from __future__ import annotations

import re
from functools import lru_cache
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

_LANG = "rust"


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

    Rust's rustdoc (``///``) comments precede the item as sibling
    line_comment nodes -- no wrapper redirection needed, identity.
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
    """Parse a raw rustdoc comment block into ``{"summary": str}``.

    *raw* is expected to be the already-gathered, newline-joined run of
    ``///`` comment lines (each line still carrying its ``///`` prefix) --
    tree-walking to gather that multi-line run stays in
    api_surface._extract_doc_comment for now (see
    extraction/lang/__init__.py's module docstring). This reproduces that
    function's per-line ``///``-stripping + blank-line-ends-summary +
    first-sentence logic on an already-joined block.
    """
    para_lines: list[str] = []
    for ln in raw.split("\n"):
        bare = ln.strip()
        if bare.startswith("///"):
            bare = bare[3:]
        bare = bare.strip()
        if not bare and para_lines:
            break  # blank `///` line ends the summary paragraph
        if bare:
            para_lines.append(bare)
    cleaned = " ".join(para_lines)
    return {"summary": _first_sentence(cleaned) if cleaned else ""}


# ---------------------------------------------------------------------------
# export_surface
# ---------------------------------------------------------------------------

_RUST_PUB_USE_BRACE_RE = re.compile(r"pub\s+use\s+[\w:]+::\{([^}]*)\}\s*;", re.DOTALL)
_RUST_PUB_USE_SINGLE_RE = re.compile(r"pub\s+use\s+[\w:]+::(\w+)(?:\s+as\s+\w+)?\s*;")
_RUST_PUB_USE_GLOB_RE = re.compile(r"pub\s+use\s+[\w:]+::\*\s*;")


@lru_cache(maxsize=64)
def _reexported_names_cached(pkg_root: Path) -> frozenset | None:
    """Cached core of reexported_names() -- see that function's docstring
    for the algorithm. Cached (functools.lru_cache) for the same reason as
    python's _top_level_exports_cached: content_eval/evaluators/
    _reachability.py's live_unreachable() calls this once per class-name
    check across a whole evaluator run, and the original _reachability.py
    implementation this replaces (its own _rust_reexported_names) was
    itself lru_cache-memoized. api_surface.py's own precompute call site
    only calls this once per extraction run either way.
    """
    lib_rs = pkg_root / "lib.rs"
    if not lib_rs.is_file():
        return None
    try:
        text = lib_rs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if _RUST_PUB_USE_GLOB_RE.search(text):
        return None  # can't enumerate a glob re-export -- stay safe
    names: set[str] = set()
    found_any = False
    for m in _RUST_PUB_USE_BRACE_RE.finditer(text):
        found_any = True
        for item in m.group(1).split(","):
            item = item.strip()
            if not item or item == "self":
                continue
            # "OrigName" or "OrigName as Alias" -- struct/enum extraction
            # records the source-side name, so keep the pre-`as` identifier.
            orig = item.split(" as ")[0].strip()
            if orig:
                names.add(orig)
    for m in _RUST_PUB_USE_SINGLE_RE.finditer(text):
        found_any = True
        orig = m.group(1).strip()
        if orig:
            names.add(orig)
    if not found_any:
        return None  # no recognizable pub-use re-export -- stay safe
    return frozenset(names)


def reexported_names(pkg_root: Path) -> set[str] | None:
    """Return the set of names re-exported via `pub use` at the crate root.

    Reads ``pkg_root/lib.rs`` -- ``package_root.py``'s ``_detect_rust_root()``
    always resolves *pkg_root* to the crate's ``src/`` directory, so this is
    the crate root for every Rust product onboarded so far. Returns None
    when lib.rs is missing, contains a glob re-export (``pub use x::*;``)
    this regex-only pass cannot enumerate, or has no recognizable `pub use`
    statement at all -- all three cases mean "cannot positively determine,"
    so the caller defaults every entry's ``reachable`` to True.

    Moved verbatim from extraction/api_surface.py's
    ``_rust_reexported_names`` (TC-MT040-42) -- api_surface.py now
    re-exports this under its original private name as a thin wrapper.
    """
    names = _reexported_names_cached(pkg_root)
    return set(names) if names is not None else None


def export_surface(repo: Path, pkg_root: Path) -> tuple[set[str] | None, Path | None]:
    """Return (reachable_names, scope_root) -- see extraction/lang/__init__.py's
    module docstring for the shared contract. *repo* is accepted for
    interface uniformity across all 7 language modules but unused here (the
    original rust check never needed it). *scope_root* is always *pkg_root*
    itself -- unlike python, the rust reachability check in
    api_surface._compute_reachable never applies a path-scoping test, so
    this value is provided for contract consistency but not consumed by
    that dispatcher.
    """
    return reexported_names(pkg_root), pkg_root


# ---------------------------------------------------------------------------
# TC-535 (REQ-G3-009, RD3-030 to RD3-033): the importable surface of a crate and what its items say
# ---------------------------------------------------------------------------


def _one_line(text: str) -> str:
    return " ".join(text.split())


def fn_details(fnode) -> dict[str, str]:
    """The receiver, the generic parameters (with their bounds) and the ``where`` clause of a ``fn``.

    ``{"receiver": "&mut self", "generics": "<P: AsRef<str>>", "where": "where P: Send"}``, each key only
    when the function has it. The receiver is the ``self`` parameter as written (``&self``, ``&mut self``,
    ``self``, ``mut self``, ``&'a self``, ``self: Box<Self>``); a function without one is an associated
    function (``Workbook::new()``), not a method. Without the receiver a program written from the
    rendered signature calls a ``&mut self`` method on a shared reference (E0596).
    """
    details: dict[str, str] = {}
    parameters = child_by_field(fnode, "parameters")
    if parameters is not None:
        for child in parameters.children:
            if child.type == "self_parameter":
                details["receiver"] = _one_line(node_text(child))
                break
    generics = child_by_field(fnode, "type_parameters")
    if generics is not None:
        details["generics"] = _one_line(node_text(generics))
    where = find_child_by_type(fnode, "where_clause")
    if where is not None:
        details["where"] = _one_line(node_text(where))
    return details


def type_details(node) -> dict[str, str]:
    """``{"generics": "<T: Clone>", "where": "where T: Send"}`` of a struct, enum, union, trait or alias."""
    details: dict[str, str] = {}
    generics = child_by_field(node, "type_parameters")
    if generics is not None:
        details["generics"] = _one_line(node_text(generics))
    where = find_child_by_type(node, "where_clause")
    if where is not None:
        details["where"] = _one_line(node_text(where))
    return details


def enum_variants(enum_node) -> list[dict[str, str]]:
    """The variants of an ``enum_item``: ``{"name", "value", "payload"}``.

    ``payload`` is the data the variant carries as written (``(i32)`` for a tuple variant, ``{ x: u8 }`` for a
    struct variant, ``""`` for a unit variant); ``value`` is an explicit discriminant (``C = 5`` gives
    ``5``). Losing the payload is how ``CellValue::Number(f64)`` came to be served as a bare ``Number``.
    """
    body = child_by_field(enum_node, "body")
    variants: list[dict[str, str]] = []
    if body is None:
        return variants
    for variant in body.children:
        if variant.type != "enum_variant":
            continue
        name = child_by_field(variant, "name")
        if name is None:
            continue
        payload_node = child_by_field(variant, "body")
        value_node = child_by_field(variant, "value")
        variants.append(
            {
                "name": node_text(name),
                "value": _one_line(node_text(value_node)) if value_node is not None else "",
                "payload": _one_line(node_text(payload_node)) if payload_node is not None else "",
            }
        )
    return variants


def derive_traits(node) -> list[str]:
    """The traits a ``#[derive(...)]`` on an item lists (not bases of the item: nothing is inherited)."""
    derives: list[str] = []
    current = node.prev_named_sibling
    while current is not None and current.type in ("attribute_item", "line_comment"):
        if current.type == "attribute_item":
            match = re.search(r"derive\s*\(([^)]*)\)", node_text(current))
            if match:
                derives = [part.strip() for part in match.group(1).split(",") if part.strip()] + derives
        current = current.prev_named_sibling
    return derives


def supertraits(trait_node) -> list[str]:
    """The supertraits of a ``trait_item`` (``pub trait Foo: Bar + Baz`` gives ``Bar`` and ``Baz``)."""
    bounds = find_child_by_type(trait_node, "trait_bounds")
    if bounds is None:
        return []
    return [
        node_text(child)
        for child in bounds.children
        if child.type in ("type_identifier", "generic_type", "scoped_type_identifier")
    ]


def type_alias_target(node) -> str:
    """The right-hand side of a ``pub type`` alias as written, on one line."""
    target = child_by_field(node, "type")
    return _one_line(node_text(target)) if target is not None else ""


_ITEM_NODES = frozenset(
    {
        "struct_item",
        "enum_item",
        "union_item",
        "trait_item",
        "function_item",
        "type_item",
        "const_item",
        "static_item",
    }
)


class _Module:
    """One module of a crate: where it lives, what it declares and what it imports."""

    def __init__(self, file: Path, parent: _Module | None, children_dir: Path) -> None:
        self.file = file
        self.parent = parent
        self.children_dir = children_dir
        self.items: set[str] = set()
        self.pub_items: set[str] = set()
        self.children: dict[str, _Module] = {}
        self.pub_children: set[str] = set()
        # (is public, path segments, bound name or None for a glob)
        self.uses: list[tuple[bool, tuple[str, ...], str | None]] = []


def _is_pub(node) -> bool:
    for child in node.children:
        if child.type == "visibility_modifier":
            return node_text(child).strip() == "pub"
    return False


def _flatten_use(node, prefix: tuple[str, ...]) -> list[tuple[tuple[str, ...], str | None]]:
    """The ``(path, bound name)`` pairs of one ``use`` argument; a glob binds no name (``None``)."""
    kind = node.type
    if kind in ("identifier", "crate", "self", "super"):
        segment = node_text(node)
        if kind == "self" and prefix:
            return [(prefix, prefix[-1])]
        return [(prefix + (segment,), segment)]
    if kind in ("scoped_identifier", "scoped_type_identifier"):
        parts = tuple(s for s in re.split(r"::", _one_line(node_text(node))) if s)
        return [(prefix + parts, parts[-1])]
    if kind == "use_as_clause":
        path = child_by_field(node, "path")
        alias = child_by_field(node, "alias")
        if path is None or alias is None:
            return []
        inner = _flatten_use(path, prefix)
        return [(p, node_text(alias)) for p, _ in inner]
    if kind == "use_wildcard":
        body = _one_line(node_text(node))
        path = tuple(s for s in body.split("::") if s and s != "*")
        return [(prefix + path, None)]
    if kind == "scoped_use_list":
        path_node = child_by_field(node, "path")
        list_node = child_by_field(node, "list")
        base = prefix
        if path_node is not None:
            base = prefix + tuple(s for s in _one_line(node_text(path_node)).split("::") if s)
        found: list[tuple[tuple[str, ...], str | None]] = []
        if list_node is not None:
            for child in list_node.children:
                if child.is_named:
                    found.extend(_flatten_use(child, base))
        return found
    if kind == "use_list":
        found = []
        for child in node.children:
            if child.is_named:
                found.extend(_flatten_use(child, prefix))
        return found
    return []


def _path_attribute(attribute_item) -> str | None:
    match = re.search(r'path\s*=\s*"([^"]*)"', node_text(attribute_item))
    return match.group(1) if match else None


def _load_module(
    parser, file: Path, parent: _Module | None, children_dir: Path, seen: set[Path]
) -> _Module | None:
    try:
        source = file.read_bytes()
    except OSError:
        return None
    if file in seen:
        return None
    seen.add(file)
    module = _Module(file, parent, children_dir)
    _read_body(parser, parser.parse(source).root_node, module, seen, inline=False)
    return module


def _read_body(parser, body, module: _Module, seen: set[Path], *, inline: bool) -> None:
    pending_path: str | None = None
    for node in body.children:
        kind = node.type
        if kind == "attribute_item":
            declared = _path_attribute(node)
            if declared is not None:
                pending_path = declared
            continue
        if kind in ("line_comment", "block_comment"):
            continue
        if kind in _ITEM_NODES:
            name = child_by_field(node, "name")
            if name is not None:
                module.items.add(node_text(name))
                if _is_pub(node):
                    module.pub_items.add(node_text(name))
        elif kind == "use_declaration":
            argument = child_by_field(node, "argument")
            if argument is not None:
                public = _is_pub(node)
                for path, bound in _flatten_use(argument, ()):
                    module.uses.append((public, path, bound))
        elif kind == "mod_item":
            name_node = child_by_field(node, "name")
            if name_node is not None:
                _read_mod(parser, node, node_text(name_node), module, pending_path, seen, inline)
        pending_path = None


def _read_mod(
    parser, node, name: str, parent: _Module, path_attr: str | None, seen: set[Path], inline: bool
) -> None:
    body = child_by_field(node, "body")
    child: _Module | None
    if body is not None:  # an inline module: ``mod foo { ... }``
        child = _Module(parent.file, parent, parent.children_dir / name)
        _read_body(parser, body, child, seen, inline=True)
    else:
        if path_attr is not None:
            base = parent.children_dir if inline else parent.file.parent
            target = base / path_attr
            candidates = [(target, target.parent)]
        else:
            directory = parent.children_dir
            candidates = [
                (directory / f"{name}.rs", directory / name),
                (directory / name / "mod.rs", directory / name),
            ]
        child = None
        for candidate, children_dir in candidates:
            if candidate.is_file():
                child = _load_module(parser, candidate, parent, children_dir, seen)
                break
        if child is None:
            return
    parent.children[name] = child
    if _is_pub(node):
        parent.pub_children.add(name)


class _Surface:
    """Name resolution over a loaded module tree."""

    def __init__(self, root: _Module) -> None:
        self.root = root
        self._public: dict[int, dict[str, list[tuple]]] = {}
        self._building: set[int] = set()

    def lookup(self, module: _Module, name: str, guard: frozenset) -> list[tuple]:
        key = (id(module), name)
        if key in guard:
            return []
        guard = guard | {key}
        found: list[tuple] = []
        if name in module.items:
            found.append(("item", module, name))
        if name in module.children:
            found.append(("mod", module.children[name]))
        for _public, path, bound in module.uses:
            if bound == name:
                found.extend(self.resolve(module, path, guard))
        if not found:
            for _public, path, bound in module.uses:
                if bound is None:
                    for target in self.resolve(module, path, guard):
                        if target[0] == "mod":
                            found.extend(self.lookup(target[1], name, guard))
        return found

    def resolve(self, module: _Module, path: tuple[str, ...], guard: frozenset) -> list[tuple]:
        if not path:
            return []
        first, rest = path[0], path[1:]
        if first == "crate":
            current: list[tuple] = [("mod", self.root)]
        elif first == "self":
            current = [("mod", module)]
        elif first == "super":
            parent = module.parent
            current = [("mod", parent)] if parent is not None else []
            while rest and rest[0] == "super" and current:
                parent = current[0][1].parent
                current = [("mod", parent)] if parent is not None else []
                rest = rest[1:]
        else:
            current = self.lookup(module, first, guard)
        for segment in rest:
            following: list[tuple] = []
            for target in current:
                if target[0] == "mod":
                    following.extend(self.lookup(target[1], segment, guard))
            current = following
        return current

    def public_names(self, module: _Module) -> dict[str, list[tuple]]:
        cached = self._public.get(id(module))
        if cached is not None:
            return cached
        if id(module) in self._building:
            return {}
        self._building.add(id(module))
        names: dict[str, list[tuple]] = {}
        for item in module.pub_items:
            names.setdefault(item, []).append(("item", module, item))
        for child in module.pub_children:
            names.setdefault(child, []).append(("mod", module.children[child]))
        for public, path, bound in module.uses:
            if not public:
                continue
            targets = self.resolve(module, path, frozenset())
            if bound is not None:
                if targets:
                    names.setdefault(bound, []).extend(targets)
            else:
                for target in targets:
                    if target[0] == "mod":
                        for name, found in self.public_names(target[1]).items():
                            names.setdefault(name, []).extend(found)
        self._building.discard(id(module))
        self._public[id(module)] = names
        return names


def crate_surface(repo: Path, pkg_root: Path) -> dict[tuple[str, str], str] | None:
    """The items a crate lets other crates import: ``{(file, name): shortest import path}``.

    Walks the module tree from ``pkg_root/lib.rs`` (``mod x;`` to ``x.rs`` or ``x/mod.rs``, ``#[path =
    "..."]`` honoured, inline ``mod x { }`` read in place) and follows ONLY public edges: a ``pub mod``, a
    ``pub use a::b::{C, D as E}`` and a ``pub use a::*`` glob. An item is importable when such a chain
    leads from the crate root to it; a ``pub fn`` in a private module nobody re-exports, a ``pub(crate)``
    item and a file no ``mod`` names are not (README of cells/rust lists ``WorkbookModel``, which lives in a
    file no ``mod`` declares: ``use aspose_cells_foss_rust::WorkbookModel`` fails E0432). The key is the
    declaring file relative to *repo* (posix) and the item's own name; the value is the path from the crate
    root with ``::`` (``Workbook``, ``util::Helper``), the shortest one when several lead to the item.
    ``None`` when there is no readable ``lib.rs``.
    """
    from foss_mcp.extraction.tree_sitter_engine.tree_helpers import get_parser

    lib_rs = pkg_root / "lib.rs"
    if not lib_rs.is_file():
        return None
    parser = get_parser("rust")
    root = _load_module(parser, lib_rs, None, pkg_root, set())
    if root is None:
        return None
    surface = _Surface(root)
    best: dict[tuple[Path, str], tuple[str, ...]] = {}
    visited: set[tuple[int, tuple[str, ...]]] = set()

    def walk(module: _Module, prefix: tuple[str, ...], active: frozenset) -> None:
        if id(module) in active or (id(module), prefix) in visited:
            return
        visited.add((id(module), prefix))
        active = active | {id(module)}
        for name, targets in surface.public_names(module).items():
            for target in targets:
                if target[0] == "item":
                    path = prefix + (name,)
                    key = (target[1].file, target[2])
                    current = best.get(key)
                    if current is None or (len(path), path) < (len(current), current):
                        best[key] = path
                else:
                    walk(target[1], prefix + (name,), active)

    walk(root, (), frozenset())
    reachable: dict[tuple[str, str], str] = {}
    for (file, name), path in best.items():
        try:
            relative = file.relative_to(repo).as_posix()
        except ValueError:
            relative = file.as_posix()
        reachable[(relative, name)] = "::".join(path)
    return reachable


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


def clear_cache() -> None:
    """Reset the memoized `pub use` scan (functools.lru_cache). Test/operator
    hook -- called by content_eval/evaluators/_reachability.py's own
    clear_caches().
    """
    _reexported_names_cached.cache_clear()
