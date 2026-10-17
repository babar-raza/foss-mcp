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
method the code defines; ``__init__`` is the one public dunder (the constructor), and a
``@property`` getter (annotated or not) is a ``property`` member. A package ``__init__`` re-export is public by the same rule and keeps
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

SymbolKind = Literal["module", "class", "enum", "function", "method", "property", "unknown"]
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})
PublicBy = Literal["name", "__all__", "reexport"]

_EXCLUDED_PARTS = frozenset({"__pycache__", "build", "dist", "tests", "test", "docs", "examples"})

# TC-522: a parameter default longer than this many characters is cut and ends in "...".
_DEFAULT_TEXT_LIMIT = 120


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
    bases: tuple[str, ...] = ()  # a class's base-class list, each entry ast.unparse(base)
    return_type: str | None = None  # a function/method's ast.unparse(node.returns); None if unannotated
    param_types: tuple[tuple[str, str], ...] = ()  # (arg_name, ast.unparse(annotation)) per annotated arg
    # TC-393: EVERY declared parameter in order, (name, annotation text or ""), a leading self/cls
    # excluded, ``*args`` as "*name" and ``**kwargs`` as "**name".
    params: tuple[tuple[str, str], ...] = ()
    writable: bool | None = None  # a property only: True with a ``@<name>.setter`` sibling, else False
    # TC-522: one ``(name, kind, default)`` per entry of ``params``, same order and names. ``kind`` is
    # positional, positional_only, var_positional, keyword_only or var_keyword; ``default`` is the
    # source text of the default (cut at ``_DEFAULT_TEXT_LIMIT``), empty when the parameter has none.
    param_details: tuple[tuple[str, str, str], ...] = ()
    # TC-522: "static", "classmethod", "async" (in that order) for a function or method; none for a
    # property getter.
    modifiers: tuple[str, ...] = ()


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


def _structured_fields(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[tuple[str, ...], str | None, tuple[tuple[str, str], ...]]:
    """The base-class list / return-type annotation / per-param annotations of *node*, read
    straight from the same AST nodes ``_signature()`` already renders into one opaque string -
    never by re-parsing that rendered string. Returns ``(bases, return_type, param_types)``;
    whichever two of the three don't apply to *node*'s own kind come back empty/``None``.
    """
    if isinstance(node, ast.ClassDef):
        return tuple(ast.unparse(base) for base in node.bases), None, ()
    return_type = ast.unparse(node.returns) if node.returns is not None else None
    param_types = tuple(
        (arg.arg, ast.unparse(arg.annotation))
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        if arg.annotation is not None
    )
    return (), return_type, param_types


def _declared_params(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[tuple[str, str], ...]:
    """Every parameter *node* declares, in source order, as ``(name, annotation or "")``.

    Positional-only, regular, ``*args`` (as ``*name``), keyword-only, then ``**kwargs`` (as
    ``**name``); an unannotated parameter is kept with an empty type rather than dropped, and a
    leading ``self`` or ``cls`` is excluded.
    """

    def _text(arg: ast.arg) -> str:
        return ast.unparse(arg.annotation) if arg.annotation is not None else ""

    args = node.args
    positional = [*args.posonlyargs, *args.args]
    if positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]
    declared = [(arg.arg, _text(arg)) for arg in positional]
    if args.vararg is not None:
        declared.append((f"*{args.vararg.arg}", _text(args.vararg)))
    declared.extend((arg.arg, _text(arg)) for arg in args.kwonlyargs)
    if args.kwarg is not None:
        declared.append((f"**{args.kwarg.arg}", _text(args.kwarg)))
    return tuple(declared)


def _default_text(node: ast.expr | None) -> str:
    """TC-522: the source text of a default value, cut at ``_DEFAULT_TEXT_LIMIT`` characters with a
    trailing ``...``; empty when the parameter has no default."""
    if node is None:
        return ""
    text = ast.unparse(node)
    return text if len(text) <= _DEFAULT_TEXT_LIMIT else text[:_DEFAULT_TEXT_LIMIT] + "..."


def _declared_param_details(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[tuple[str, str, str], ...]:
    """TC-522: ``(name, kind, default)`` for every parameter ``_declared_params`` lists, in the same
    order with the same names (``*args`` / ``**kw`` keep their stars, a leading self/cls is dropped).

    ``kind`` is positional_only, positional, var_positional, keyword_only or var_keyword.
    ``default`` is the default's source text, empty when there is none.
    """
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    positional_only = len(args.posonlyargs)
    skip = 1 if positional and positional[0].arg in ("self", "cls") else 0
    details: list[tuple[str, str, str]] = []
    for index, arg in enumerate(positional):
        if index < skip:
            continue
        kind = "positional_only" if index < positional_only else "positional"
        details.append((arg.arg, kind, _default_text(defaults[index])))
    if args.vararg is not None:
        details.append((f"*{args.vararg.arg}", "var_positional", ""))
    details.extend(
        (arg.arg, "keyword_only", _default_text(default))
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    )
    if args.kwarg is not None:
        details.append((f"**{args.kwarg.arg}", "var_keyword", ""))
    return tuple(details)


def _function_modifiers(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """TC-522: ``static`` / ``classmethod`` / ``async`` (in that order) for *node*, found by decorator
    name even when other decorators are stacked. A property getter has none."""
    if _is_property_getter(node):
        return ()
    names = {ast.unparse(decorator) for decorator in node.decorator_list}
    found: list[str] = []
    if names & {"staticmethod", "builtins.staticmethod"}:
        found.append("static")
    if names & {"classmethod", "builtins.classmethod"}:
        found.append("classmethod")
    if isinstance(node, ast.AsyncFunctionDef):
        found.append("async")
    return tuple(found)


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
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
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
                kind: SymbolKind = _class_kind(node) if isinstance(node, ast.ClassDef) else "function"
                bases, return_type, param_types = _structured_fields(node)
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
                        bases=bases,
                        return_type=return_type,
                        param_types=param_types,
                        params=() if isinstance(node, ast.ClassDef) else _declared_params(node),
                        param_details=() if isinstance(node, ast.ClassDef) else _declared_param_details(node),
                        modifiers=() if isinstance(node, ast.ClassDef) else _function_modifiers(node),
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


# TC-473: the dunders published as methods besides ``__init__`` - the context-manager protocol.
PUBLISHED_DUNDERS = ("__enter__", "__exit__", "__aenter__", "__aexit__")

_PROPERTY_DECORATORS = frozenset({"property", "cached_property", "functools.cached_property"})


def _is_property_getter(item: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether *item* is decorated ``@property`` / ``@cached_property`` / ``@functools.cached_property``."""
    return any(ast.unparse(decorator) in _PROPERTY_DECORATORS for decorator in item.decorator_list)


def _setter_names(node: ast.ClassDef) -> frozenset[str]:
    """The names a class body gives a ``@<name>.setter`` sibling."""
    return frozenset(
        decorator.value.id
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        for decorator in item.decorator_list
        if isinstance(decorator, ast.Attribute)
        and decorator.attr == "setter"
        and isinstance(decorator.value, ast.Name)
    )


def _methods(node: ast.ClassDef, module: str, relative: str, public_by: PublicBy) -> list[PublicSymbol]:
    """The public members a class body defines, each once, in source order.

    A method is public when its name has no leading underscore; ``__init__`` (and only that
    dunder) is the constructor and is public too. Every ``@property`` /
    ``@cached_property`` getter is a ``property`` member rather than a method, whether or not it
    carries a return annotation (an unannotated one has an empty type), writable only when a
    ``@<name>.setter`` sibling exists.
    """
    found: list[PublicSymbol] = []
    seen: set[str] = set()
    setters = _setter_names(node)
    for item in node.body:
        if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        published = item.name == "__init__" or item.name in PUBLISHED_DUNDERS
        if (item.name.startswith("_") and not published) or item.name in seen:
            continue
        seen.add(item.name)
        bases, return_type, param_types = _structured_fields(item)
        is_property = _is_property_getter(item)
        found.append(
            PublicSymbol(
                f"{module}.{node.name}.{item.name}",
                module,
                item.name,
                "property" if is_property else "method",
                relative,
                item.lineno,
                public_by,
                docstring=_first_docstring_line(item),
                signature=_signature(item),
                bases=bases,
                return_type=return_type,
                param_types=param_types,
                params=_declared_params(item),
                writable=(item.name in setters) if is_property else None,
                param_details=_declared_param_details(item),
                modifiers=_function_modifiers(item),
            )
        )
    if _class_kind(node) != "enum":
        found.extend(_attribute_members(node, module, relative, public_by, seen))
    return found


def _attribute_symbol(
    node: ast.ClassDef,
    module: str,
    relative: str,
    public_by: PublicBy,
    name: str,
    type_text: str,
    line: int,
    *,
    writable: bool = True,
) -> PublicSymbol:
    return PublicSymbol(
        f"{module}.{node.name}.{name}",
        module,
        name,
        "property",
        relative,
        line,
        public_by,
        docstring=None,
        return_type=type_text or None,
        writable=writable,
    )


def _init_attribute_targets(statements: Sequence[ast.stmt]) -> list[tuple[str, str, int]]:
    """``(name, type text, line)`` for each ``self.<name> = ...`` / ``self.<name>: T = ...`` found in
    *statements*, nested blocks included, nested function and class definitions excluded."""
    found: list[tuple[str, str, int]] = []

    def _self_attr(target: ast.expr) -> str | None:
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            return target.attr
        return None

    for stmt in statements:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                attr = _self_attr(target)
                if attr is not None:
                    found.append((attr, "", stmt.lineno))
        elif isinstance(stmt, ast.AnnAssign):
            attr = _self_attr(stmt.target)
            if attr is not None:
                found.append((attr, ast.unparse(stmt.annotation), stmt.lineno))
        for field_name in ("body", "orelse", "finalbody"):
            nested = getattr(stmt, field_name, None)
            if isinstance(nested, list):
                found.extend(_init_attribute_targets(nested))
        for handler in getattr(stmt, "handlers", []):
            found.extend(_init_attribute_targets(handler.body))
        for case in getattr(stmt, "cases", []):
            found.extend(_init_attribute_targets(case.body))
    return found


_CONSTANT_TYPE_NAMES = {
    int: "int",
    float: "float",
    str: "str",
    bool: "bool",
    bytes: "bytes",
    complex: "complex",
}


def _constant_type_text(value: ast.expr) -> str | None:
    """TC-545: the Python type name of a literal class-body value, ``""`` for the literal ``None``,
    and ``None`` when *value* is not a literal (a call, a name, an attribute and so on)."""
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if literal is None:
        return ""
    return _CONSTANT_TYPE_NAMES.get(type(literal)) or type(literal).__name__


def _attribute_members(
    node: ast.ClassDef, module: str, relative: str, public_by: PublicBy, seen: set[str]
) -> list[PublicSymbol]:
    """TC-473: public attribute members - class-body annotated fields and ``__init__`` ``self.<name>``
    assignments - as writable ``property`` symbols, each name once and never one already in *seen*.

    TC-545: a public class-body assignment with one plain ``Name`` target and a literal value
    (``X_AXIS = None``, ``MAX = 5``) is a constant: a read-only ``property`` typed by its literal
    (empty for ``None``), in source order beside the annotated fields. A call, a name, an attribute,
    a chained or tuple target, or an underscore-prefixed name is not a constant and is skipped."""
    candidates: list[tuple[str, str, int]] = []
    constants: set[str] = set()
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            type_text = ast.unparse(item.annotation)
            if type_text.startswith("ClassVar") or type_text.startswith(("typing.ClassVar", "t.ClassVar")):
                continue
            candidates.append((item.target.id, type_text, item.lineno))
        elif (
            isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
            and not item.targets[0].id.startswith("_")
        ):
            constant_type = _constant_type_text(item.value)
            if constant_type is not None:
                name = item.targets[0].id
                if name not in {known for known, _, _ in candidates}:
                    constants.add(name)
                candidates.append((name, constant_type, item.lineno))
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == "__init__":
            candidates.extend(_init_attribute_targets(item.body))
            break
    found: list[PublicSymbol] = []
    for name, type_text, line in candidates:
        if name.startswith("_") or name in seen:
            continue
        seen.add(name)
        found.append(
            _attribute_symbol(
                node, module, relative, public_by, name, type_text, line, writable=name not in constants
            )
        )
    return found


@dataclass(frozen=True)
class _OriginDefinition:
    """The full evidence read directly off disk for one re-exported definition.

    ``_reexport_kind`` already has this evidence for free whenever the origin module was
    scanned by ``_module_symbols`` (it is sitting right there on the ``PublicSymbol``). This
    is the same evidence recovered the hard way, by re-reading the origin file's own AST node,
    for the one case ``_module_symbols`` refuses to scan: an origin module with a leading-
    underscore path component (``dom/_document.py``, re-exported via ``dom/__init__.py`` - the
    "real impl module, public package re-export" convention, measured 2026-10-09 on
    Aspose.HTML for Python as 101 of 104 real modules).
    """

    kind: SymbolKind
    docstring: str | None
    signature: str | None
    bases: tuple[str, ...]
    return_type: str | None
    param_types: tuple[tuple[str, str], ...]
    methods: tuple[PublicSymbol, ...] = ()  # the origin ClassDef's own public methods, via _methods()
    params: tuple[tuple[str, str], ...] = ()  # TC-393: every declared parameter (functions only)
    param_details: tuple[tuple[str, str, str], ...] = ()  # TC-522: (name, kind, default) per param
    modifiers: tuple[str, ...] = ()  # TC-522: static / classmethod / async (functions only)


def _origin_definition(
    origin: str, source_root: Path, seen: frozenset[str] = frozenset()
) -> _OriginDefinition | None:
    """The full evidence of one re-exported definition, read from its own file; ``None`` if
    absent.

    A module that only forwards a name is followed to the module that defines it. The public
    path is what a reader imports, and it stays public however many private modules stand
    between it and the class body: ``aspose_html.dom`` exposes ``BarProp``, ``dom/_window.py``
    forwards it, and reading only ``_window.py``'s own body found no definition and left the
    exposed name uncitable (measured 2026-09-07 on Aspose.HTML for Python). ``seen`` stops a
    cycle of modules that forward to each other.
    """
    origin_path = source_root / Path(*origin.split("."))
    if origin_path.is_dir() or origin_path.with_suffix(".py").is_file():
        return _OriginDefinition("module", None, None, (), None, ())
    module, name = origin.rsplit(".", 1)
    package_path = source_root / Path(*module.split("."))
    for candidate in (package_path.with_suffix(".py"), package_path / "__init__.py"):
        if not candidate.is_file():
            continue
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8-sig", errors="replace"))
        except SyntaxError:
            return None
        found: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | None = None
        forwarded: str | None = None
        for item in tree.body:
            if isinstance(item, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name:
                found = item
                break
            if isinstance(item, ast.ImportFrom) and forwarded is None:
                forwarded = _forwarded_origin(item, module, candidate.name == "__init__.py", name)
        if found is not None:
            kind: SymbolKind = _class_kind(found) if isinstance(found, ast.ClassDef) else "function"
            bases, return_type, param_types = _structured_fields(found)
            methods = (
                tuple(_methods(found, module, candidate.name, "reexport"))
                if isinstance(found, ast.ClassDef)
                else ()
            )
            return _OriginDefinition(
                kind,
                _first_docstring_line(found),
                _signature(found),
                bases,
                return_type,
                param_types,
                methods,
                params=() if isinstance(found, ast.ClassDef) else _declared_params(found),
                param_details=() if isinstance(found, ast.ClassDef) else _declared_param_details(found),
                modifiers=() if isinstance(found, ast.ClassDef) else _function_modifiers(found),
            )
        if forwarded is None or forwarded in seen or forwarded == origin:
            return None
        return _origin_definition(forwarded, source_root, seen | {origin})
    return None


def _origin_syntax_error(
    origin: str, source_root: Path, repository_root: Path, seen: frozenset[str] = frozenset()
) -> str | None:
    """TC-426: the ``syntax-error`` entry for the file a re-export's origin lives in, if that file
    does not parse; ``None`` otherwise.

    ``_origin_definition`` swallows the ``SyntaxError`` of the origin's file and reports only that
    the name could not be found, so a re-export from a private module (``_input/reader.py``) that
    fails to parse showed up as ``unresolved-reexport`` alone and the real cause never surfaced.
    This finds the same candidate file ``_origin_definition`` reads (the module ``.py``, else the
    package ``__init__.py``), follows a forwarded import the same way with the same cycle guard, and
    returns the entry in the format ``_module_symbols`` already uses:
    ``<module>:<line>:syntax-error:<repository-relative path>:<message>``.
    """
    origin_path = source_root / Path(*origin.split("."))
    if origin_path.is_dir() or origin_path.with_suffix(".py").is_file():
        return None
    module, name = origin.rsplit(".", 1)
    package_path = source_root / Path(*module.split("."))
    for candidate in (package_path.with_suffix(".py"), package_path / "__init__.py"):
        if not candidate.is_file():
            continue
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8-sig", errors="replace"))
        except SyntaxError as exc:
            relative = candidate.relative_to(repository_root).as_posix()
            return f"{module}:{exc.lineno or 0}:syntax-error:{relative}:{exc.msg}"
        forwarded: str | None = None
        for item in tree.body:
            if isinstance(item, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name:
                return None
            if isinstance(item, ast.ImportFrom) and forwarded is None:
                forwarded = _forwarded_origin(item, module, candidate.name == "__init__.py", name)
        if forwarded is None or forwarded in seen or forwarded == origin:
            return None
        return _origin_syntax_error(forwarded, source_root, repository_root, seen | {origin})
    return None


def _origin_kind(origin: str, source_root: Path, seen: frozenset[str] = frozenset()) -> SymbolKind | None:
    """The kind of one re-exported definition, read from its own file; ``None`` if absent.

    A single-value convenience over ``_origin_definition`` for a caller that wants only the
    ``SymbolKind`` - kept so widening ``_origin_definition``'s return shape, for the structured
    bases/return_type/param_types/docstring/signature a re-export's origin also carries, never
    changes this function's existing one-value contract.
    """
    definition = _origin_definition(origin, source_root, seen)
    return definition.kind if definition is not None else None


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
) -> tuple[PublicSymbol | None, SymbolKind | None, _OriginDefinition | None]:
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
    ``seen`` makes a cycle terminate rather than recurse. The third element is the structured
    evidence read off disk for that unscanned-module case; it is ``None`` whenever the first
    element (``origin``) is not ``None``, since that symbol's own fields already carry it.
    """
    seen: set[str] = set()
    current = symbol
    while True:
        target = current.reexported_from
        if target is None or target in seen:
            return None, None, None
        seen.add(target)
        origin = symbols.get(target)
        # A package that re-exports a submodule of its own name forwards to itself; the file
        # system answers what the symbol table cannot (``email_foss.cfb`` re-exporting from
        # ``email_foss.cfb``), and reading the chain instead would end the walk at that symbol.
        if origin is None or origin.qualified_name == current.qualified_name:
            definition = _origin_definition(target, source_root, frozenset(seen))
            return None, (definition.kind if definition is not None else None), definition
        if origin.kind != "unknown":
            return origin, origin.kind, None
        current = origin


def _reexported_methods(definition: _OriginDefinition, symbol: PublicSymbol, name: str) -> list[PublicSymbol]:
    """One rehomed ``PublicSymbol`` per method *definition* (an underscore-origin's recovered
    evidence) carries, keyed under the PUBLIC re-export's own qualified name.

    ``_module_symbols()``'s normal per-file scan already turns a class's own methods into
    sibling ``PublicSymbol`` entries by calling ``_methods()`` directly on the ``ast.ClassDef``
    it just scanned; an underscore-prefixed origin module is never scanned that way at all, so
    without this, a class reachable only through such a re-export keeps its bases/return_type/
    docstring/signature (``_origin_definition()``'s own fix) but never gains a single method -
    measured 2026-10-09 on Aspose.HTML for Python: 321 of 323 real classes carry zero methods.

    Each method keeps its own kind/docstring/signature/bases/return_type/param_types verbatim -
    read straight from the origin's own AST, via ``definition.methods`` - but ``qualified_name``
    is rewritten to ``f"{name}.{method.name}"`` and ``module`` to *symbol*'s own module, so
    ``run_extraction.py``'s ``_python_types_from_surface()`` (which finds a method's owner by
    stripping its qualified_name's last segment) resolves it against the PUBLIC owner, never the
    private origin. ``source_path``/``line`` reuse *symbol*'s own - the same citable, public
    import-location precedent the class-level ``replace()`` call already sets - never the
    private implementation file's.
    """
    return [
        replace(
            method,
            qualified_name=f"{name}.{method.name}",
            module=symbol.module,
            source_path=symbol.source_path,
            line=symbol.line,
        )
        for method in definition.methods
    ]


def _find_class(
    module: str, name: str, source_root: Path, seen: frozenset[str] = frozenset()
) -> tuple[ast.ClassDef, str, bool] | None:
    """TC-429: the ``ClassDef`` that binds *name* in *module*, with the module that holds its body.

    Returns ``(class node, module holding the body, whether that module is a package
    __init__)``. A module that only imports the name is followed to its origin with the same
    rules and cycle guard as ``_origin_definition``; ``None`` when no class definition is
    reached (absent, not a class, unparsable, or a cycle).
    """
    key = f"{module}.{name}"
    if key in seen:
        return None
    package_path = source_root / Path(*module.split("."))
    for candidate in (package_path.with_suffix(".py"), package_path / "__init__.py"):
        if not candidate.is_file():
            continue
        is_package = candidate.name == "__init__.py"
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8-sig", errors="replace"))
        except SyntaxError:
            return None
        forwarded: str | None = None
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and item.name == name:
                return item, module, is_package
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name:
                return None
            if isinstance(item, ast.ImportFrom) and forwarded is None:
                forwarded = _forwarded_origin(item, module, is_package, name)
        if forwarded is None or "." not in forwarded:
            return None
        next_module, next_name = forwarded.rsplit(".", 1)
        return _find_class(next_module, next_name, source_root, seen | {key})
    return None


def _is_non_public_base(written: str, node: ast.ClassDef, module: str) -> bool:
    """A base is non-public when its name (as written or as defined) starts with an underscore, or
    its body lives in a module with an underscore path component."""
    return (
        written.startswith("_")
        or node.name.startswith("_")
        or any(part.startswith("_") for part in module.split("."))
    )


def _inherited_private_base_methods(
    symbols: dict[str, PublicSymbol], source_root: Path
) -> list[PublicSymbol]:
    """TC-429: the public methods a public class inherits from its NON-PUBLIC base classes.

    A public class built from private mixins (``class SampleClient(_CoreClientMixin,
    _MailClientMixin)``) was served with no methods: the mixins are private, so never a public
    symbol, and nothing flattened inheritance. For every public class symbol, each ``Name``
    base is resolved in the module holding the class body (a class in the same module, or an
    ``import from`` binding followed to its definition). A non-public base (underscore name, or
    defined in a module with an underscore path component) contributes its public methods, then
    its own non-public bases recursively: the subclass's own methods first, then bases in
    declaration order, depth first, the first definition of a name winning. Public,
    unresolvable and non-class bases contribute nothing, and a base cycle terminates.

    Each contributed method keeps its kind, signature and annotations and is rehomed as
    ``_reexported_methods`` does (qualified name under the subclass, the subclass symbol's
    module, source_path and line). Nothing already in *symbols* is replaced.
    """
    added: dict[str, PublicSymbol] = {}
    for symbol in list(symbols.values()):
        if symbol.kind not in ("class", "enum") or symbol.qualified_name != f"{symbol.module}.{symbol.name}":
            continue
        if symbol.reexported_from is not None:
            if "." not in symbol.reexported_from:
                continue
            body_module, body_name = symbol.reexported_from.rsplit(".", 1)
        else:
            body_module, body_name = symbol.module, symbol.name
        located = _find_class(body_module, body_name, source_root)
        if located is None:
            continue
        own, holder, _ = located
        taken = {item.name for item in own.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)}
        visited: set[tuple[str, str]] = {(holder, own.name)}

        def visit(
            node: ast.ClassDef,
            node_module: str,
            symbol: PublicSymbol = symbol,
            taken: set[str] = taken,
            visited: set[tuple[str, str]] = visited,
        ) -> None:
            for base in node.bases:
                if not isinstance(base, ast.Name):
                    continue
                base_located = _find_class(node_module, base.id, source_root)
                if base_located is None:
                    continue
                base_node, base_module, _ = base_located
                if not _is_non_public_base(base.id, base_node, base_module):
                    continue
                if (base_module, base_node.name) in visited:
                    continue
                visited.add((base_module, base_node.name))
                for method in _methods(base_node, base_module, symbol.source_path, symbol.public_by):
                    name = f"{symbol.qualified_name}.{method.name}"
                    if method.name in taken or name in symbols or name in added:
                        continue
                    added[name] = replace(
                        method,
                        qualified_name=name,
                        module=symbol.module,
                        source_path=symbol.source_path,
                        line=symbol.line,
                    )
                visit(base_node, base_module)

        visit(own, holder)
    return list(added.values())


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
            origin, kind, definition = _reexport_kind(symbol, symbols, source_root)
            if kind is None:
                unresolved.append(
                    f"{symbol.module}:{symbol.line}:unresolved-reexport:{symbol.reexported_from}"
                )
                origin_error = _origin_syntax_error(
                    symbol.reexported_from, source_root, repository_root, frozenset({symbol.reexported_from})
                )
                if origin_error is not None:
                    unresolved.append(origin_error)
            else:
                # A re-export carries its origin's own docstring, signature, and structured
                # base-class/return-type/param-annotation data as evidence - from the origin
                # symbol itself when its module was scanned, otherwise from the same AST node
                # read directly off disk (``definition``, for an underscore-module origin).
                resolved = replace(
                    symbol,
                    kind=kind,
                    docstring=origin.docstring
                    if origin is not None
                    else (definition.docstring if definition else None),
                    signature=origin.signature
                    if origin is not None
                    else (definition.signature if definition else None),
                    bases=origin.bases if origin is not None else (definition.bases if definition else ()),
                    return_type=origin.return_type
                    if origin is not None
                    else (definition.return_type if definition else None),
                    param_types=origin.param_types
                    if origin is not None
                    else (definition.param_types if definition else ()),
                    params=origin.params if origin is not None else (definition.params if definition else ()),
                    param_details=origin.param_details
                    if origin is not None
                    else (definition.param_details if definition else ()),
                    modifiers=origin.modifiers
                    if origin is not None
                    else (definition.modifiers if definition else ()),
                )
                symbols[name] = resolved
                # An underscore-origin class's own methods are never otherwise scanned (see
                # _reexported_methods()'s own docstring); recover them here, the one place that
                # already has both the resolved PublicSymbol and its origin's evidence.
                if definition is not None:
                    for method_symbol in _reexported_methods(definition, resolved, name):
                        if method_symbol.qualified_name not in symbols:
                            symbols[method_symbol.qualified_name] = method_symbol
        # TC-429: a public class built from private mixins gains their public methods.
        for inherited in _inherited_private_base_methods(symbols, source_root):
            symbols.setdefault(inherited.qualified_name, inherited)
    return PublicSurface(
        symbols=tuple(symbols[name] for name in sorted(symbols)),
        unresolved=tuple(sorted(set(unresolved))),
    )
