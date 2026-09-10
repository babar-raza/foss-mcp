# Ported from repository-presenter at commit 16d75e95d4 (source path
# src/repository_presenter/components/readme/extractors/platforms/python_surface.py),
# itself vendored from aspose.org. Patch applied: this is the production Python reader
# (pure stdlib `ast`, zero tree-sitter) — kept distinct from lang/python.py, the
# tree-sitter-family adapter ported alongside it as a parity oracle only. Trimmed to the
# extraction surface: `public_symbol_facts` and `_definition_of`, which assemble
# presenter-specific `Fact`/`Evidence` records from `repository_presenter.core.facts`, were
# dropped — this package extracts symbols, it does not decide what a caller does with them
# (docs/REPOSITORY_LAYOUT.md: `foss_mcp.extraction.tree_sitter_engine`). No other changes.
"""Static public surface of the Python packages: definitions, ``__all__``, and re-exports.

Nothing is imported or executed. Every module under the product packages is parsed with ``ast``.
A top-level class or function is public when its name has no leading underscore and, when the
module declares a literal ``__all__``, is listed there. A public class's methods without a
leading underscore are public with it, recorded as ``module.Class.method`` so prose may name a
method the code defines. A package ``__init__`` re-export is public by the same rule and keeps
the origin it came from. Star imports, unresolvable relative imports, and syntax errors are
recorded as unresolved, never guessed.
"""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

SymbolKind = Literal["module", "class", "enum", "function", "method", "unknown"]
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})
PublicBy = Literal["name", "__all__", "reexport"]

_EXCLUDED_PARTS = frozenset({"__pycache__", "build", "dist", "tests", "test", "docs", "examples"})


@dataclass(frozen=True)
class PublicSymbol:
    """One statically visible symbol with exact source provenance."""

    qualified_name: str
    module: str
    name: str
    kind: SymbolKind
    source_path: str
    line: int
    public_by: PublicBy
    reexported_from: str | None = None
    docstring: str | None = None  # the first line of the symbol's own docstring
    signature: str | None = None  # the definition line as the source states it


def _first_docstring_line(node: ast.AST) -> str | None:
    text = (
        ast.get_docstring(node, clean=True)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        else None
    )
    if not text:
        return None
    first = text.strip().splitlines()[0].strip()
    return first or None


def _signature(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _class_kind(node: ast.ClassDef) -> SymbolKind:
    for base in node.bases:
        name = ast.unparse(base).rsplit(".", 1)[-1]
        if name in _ENUM_BASES:
            return "enum"
    return "class"


@dataclass(frozen=True)
class PublicSurface:
    """Every public symbol of the inspected packages plus what could not be resolved."""

    symbols: tuple[PublicSymbol, ...]
    unresolved: tuple[str, ...]


def _literal_all(tree: ast.Module) -> set[str] | None:
    names: set[str] = set()
    found = False
    for node in tree.body:
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
        ) or (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
            and isinstance(node.op, ast.Add)
        ):
            value = node.value
            found = True
        if value is None:
            continue
        try:
            resolved = ast.literal_eval(value)
        except (ValueError, TypeError):
            continue
        if isinstance(resolved, list | tuple | set):
            names.update(item for item in resolved if isinstance(item, str))
    return names if found else None


def _public_name(name: str, explicit: set[str] | None) -> tuple[bool, PublicBy]:
    if name.startswith("_"):
        return False, "name"
    if explicit is not None:
        return name in explicit, "__all__"
    return True, "name"


def _module_name(path: Path, source_root: Path) -> tuple[str, bool]:
    relative = path.relative_to(source_root)
    is_package = path.name == "__init__.py"
    parts = relative.parent.parts if is_package else relative.with_suffix("").parts
    return ".".join(parts), is_package


def _resolved_relative(module: str, is_package: bool, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    package = module if is_package else module.rpartition(".")[0]
    try:
        return importlib.util.resolve_name("." * node.level + (node.module or ""), package)
    except (ImportError, ValueError):
        return None


def _module_symbols(
    path: Path, source_root: Path, repository_root: Path
) -> tuple[list[PublicSymbol], list[str]]:
    module, is_package = _module_name(path, source_root)
    relative = path.relative_to(repository_root).as_posix()
    if not module or any(part.startswith("_") for part in module.split(".")):
        return [], []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"), filename=str(path))
    except SyntaxError as exc:
        return [], [f"{module}:{exc.lineno or 0}:syntax-error:{relative}:{exc.msg}"]
    explicit = _literal_all(tree)
    symbols = [
        PublicSymbol(
            module,
            module,
            module.rsplit(".", 1)[-1],
            "module",
            relative,
            1,
            "name",
            docstring=_first_docstring_line(tree),
        )
    ]
    unresolved: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            public, public_by = _public_name(node.name, explicit)
            if public:
                kind: SymbolKind = (
                    _class_kind(node) if isinstance(node, ast.ClassDef) else "function"
                )
                symbols.append(
                    PublicSymbol(
                        f"{module}.{node.name}",
                        module,
                        node.name,
                        kind,
                        relative,
                        node.lineno,
                        public_by,
                        docstring=_first_docstring_line(node),
                        signature=_signature(node),
                    )
                )
                if isinstance(node, ast.ClassDef):
                    symbols.extend(_methods(node, module, relative, public_by))
        elif isinstance(node, ast.ImportFrom) and (is_package or explicit is not None):
            origin = _resolved_relative(module, is_package, node)
            if origin is None or any(alias.name == "*" for alias in node.names):
                unresolved.append(f"{module}:{node.lineno}:{ast.unparse(node)}")
                continue
            for alias in node.names:
                exposed = alias.asname or alias.name
                if _public_name(exposed, explicit)[0]:
                    symbols.append(
                        PublicSymbol(
                            f"{module}.{exposed}",
                            module,
                            exposed,
                            "unknown",
                            relative,
                            node.lineno,
                            "reexport",
                            reexported_from=f"{origin}.{alias.name}",
                        )
                    )
    return symbols, unresolved


def _methods(
    node: ast.ClassDef, module: str, relative: str, public_by: PublicBy
) -> list[PublicSymbol]:
    """The public methods a class body defines, each once, in source order."""
    found: list[PublicSymbol] = []
    seen: set[str] = set()
    for item in node.body:
        if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if item.name.startswith("_") or item.name in seen:
            continue
        seen.add(item.name)
        found.append(
            PublicSymbol(
                f"{module}.{node.name}.{item.name}",
                module,
                item.name,
                "method",
                relative,
                item.lineno,
                public_by,
                docstring=_first_docstring_line(item),
                signature=_signature(item),
            )
        )
    return found


def _origin_kind(
    origin: str, source_root: Path, seen: frozenset[str] = frozenset()
) -> SymbolKind | None:
    """The kind of one re-exported definition, read from its own file; ``None`` if absent.

    A module that only forwards a name is followed to the module that defines it. The public
    path is what a reader imports, and it stays public however many private modules stand
    between it and the class body: ``aspose_html.dom`` exposes ``BarProp``, ``dom/_window.py``
    forwards it, and reading only ``_window.py``'s own body found no definition and left the
    exposed name uncitable (measured 2026-09-07 on Aspose.HTML for Python). ``seen`` stops a
    cycle of modules that forward to each other.
    """
    origin_path = source_root / Path(*origin.split("."))
    if origin_path.is_dir() or origin_path.with_suffix(".py").is_file():
        return "module"
    module, name = origin.rsplit(".", 1)
    package_path = source_root / Path(*module.split("."))
    for candidate in (package_path.with_suffix(".py"), package_path / "__init__.py"):
        if not candidate.is_file():
            continue
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8-sig", errors="replace"))
        except SyntaxError:
            return None
        forwarded: str | None = None
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and item.name == name:
                return _class_kind(item)
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name:
                return "function"
            if isinstance(item, ast.ImportFrom) and forwarded is None:
                forwarded = _forwarded_origin(item, module, candidate.name == "__init__.py", name)
        if forwarded is None or forwarded in seen or forwarded == origin:
            return None
        return _origin_kind(forwarded, source_root, seen | {origin})
    return None


def _forwarded_origin(node: ast.ImportFrom, module: str, is_package: bool, name: str) -> str | None:
    """Where an ``import from`` statement says ``name`` really comes from, if it names it."""
    for alias in node.names:
        if (alias.asname or alias.name) != name:
            continue
        origin = _resolved_relative(module, is_package, node)
        return f"{origin}.{alias.name}" if origin else None
    return None


def _reexport_kind(
    symbol: PublicSymbol, symbols: dict[str, PublicSymbol], source_root: Path
) -> tuple[PublicSymbol | None, SymbolKind | None]:
    """The definition a re-export ends at and its kind, following every hop of the chain.

    One lookup is one hop too few whenever a package re-exports what another package already
    re-exported: ``aspose_barcode_foss.BarcodeError`` forwards to
    ``aspose_barcode_foss.exceptions.BarcodeError``, itself a re-export that this same pass
    resolves later, so reading only the first hop left the *shortest* public import path - the
    one a README names - ``UNRESOLVED`` while the longer one was ``SUPPORTED``. Measured
    2026-09-07 over the Python cohort's clones: 25 such symbols on Aspose.BarCode and 85 on
    Aspose.HTML, every one a real public class the prose could not cite.

    Each hop is followed until one carries a kind; a hop whose module was never scanned - a
    private module, or a plain module with no ``__all__`` - is read from its own file, and
    ``seen`` makes a cycle terminate rather than recurse.
    """
    seen: set[str] = set()
    current = symbol
    while True:
        target = current.reexported_from
        if target is None or target in seen:
            return None, None
        seen.add(target)
        origin = symbols.get(target)
        # A package that re-exports a submodule of its own name forwards to itself; the file
        # system answers what the symbol table cannot (``email_foss.cfb`` re-exporting from
        # ``email_foss.cfb``), and reading the chain instead would end the walk at that symbol.
        if origin is None or origin.qualified_name == current.qualified_name:
            return None, _origin_kind(target, source_root, frozenset(seen))
        if origin.kind != "unknown":
            return origin, origin.kind
        current = origin


def _minimal_roots(package_dirs: Sequence[str]) -> list[str]:
    """Drop package directories that another listed directory already contains."""
    ordered = sorted(set(package_dirs))
    return [d for d in ordered if not any(d.startswith(other + "/") for other in ordered)]


def inspect_public_surface(repository_root: Path, package_dirs: Sequence[str]) -> PublicSurface:
    """Inspect the packages at ``package_dirs`` (repository-relative) without importing them."""
    symbols: dict[str, PublicSymbol] = {}
    unresolved: list[str] = []
    for package_dir in _minimal_roots(package_dirs):
        package_path = repository_root / package_dir
        source_root = package_path.parent
        for path in sorted(package_path.rglob("*.py")):
            if any(part in _EXCLUDED_PARTS for part in path.relative_to(source_root).parts):
                continue
            module_symbols, module_unresolved = _module_symbols(path, source_root, repository_root)
            unresolved.extend(module_unresolved)
            for symbol in module_symbols:
                current = symbols.get(symbol.qualified_name)
                if (
                    current is None
                    or (symbol.public_by == "reexport" and current.kind == "module")
                    or (
                        # Python binds a name to its last import: a later re-export of the
                        # same public name replaces the earlier one (a package importing
                        # ColladaLoadOptions from two modules exposes the second).
                        symbol.public_by == "reexport"
                        and current.public_by == "reexport"
                        and symbol.line > current.line
                    )
                ):
                    symbols[symbol.qualified_name] = symbol
        for name, symbol in list(symbols.items()):
            if symbol.reexported_from is None or symbol.kind != "unknown":
                continue
            origin, kind = _reexport_kind(symbol, symbols, source_root)
            if kind is None:
                unresolved.append(
                    f"{symbol.module}:{symbol.line}:unresolved-reexport:{symbol.reexported_from}"
                )
            else:
                # A re-export carries its origin's own docstring and signature as evidence.
                symbols[name] = replace(
                    symbol,
                    kind=kind,
                    docstring=origin.docstring if origin is not None else None,
                    signature=origin.signature if origin is not None else None,
                )
    return PublicSurface(
        symbols=tuple(symbols[name] for name in sorted(symbols)),
        unresolved=tuple(sorted(set(unresolved))),
    )
