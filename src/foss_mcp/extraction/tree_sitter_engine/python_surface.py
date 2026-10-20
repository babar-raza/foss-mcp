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

TC-532 (Round 3): a name bound more than once is its LAST binding, as in Python - the last ``def`` of a
method or function, the last ``class`` of a name, the last ``from ... import`` of an exported name (the
class it replaced is marked ``shadowed_by`` and the exported name ``shadows`` it); the members of a
class include those of its underscore-prefixed bases and of the public ancestors behind them
(``inherited_from``), attributes built by ``property(...)`` or by a factory function, the ``__init__``
that ``@dataclass`` / ``NamedTuple`` generate, enum members built with ``auto()`` and the literal values
of attributes.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

SymbolKind = Literal["module", "class", "enum", "function", "method", "property", "unknown"]
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})
# TC-532: the calls that give an enum member its value automatically. Such a member is recorded under
# the marker value ``auto()``, because the real value is only known when the class is created.
_ENUM_AUTO_CALLS = ("auto", "enum.auto")
_ENUM_AUTO_MARKER = "auto()"
_OVERLOAD_DECORATORS = frozenset({"overload", "typing.overload", "t.overload", "typing_extensions.overload"})
_DATACLASS_DECORATORS = frozenset({"dataclass", "dataclasses.dataclass"})
_NAMEDTUPLE_BASES = frozenset({"NamedTuple", "typing.NamedTuple", "t.NamedTuple"})
PublicBy = Literal["name", "__all__", "reexport"]

_EXCLUDED_PARTS = frozenset({"__pycache__", "build", "dist", "tests", "test", "docs", "examples"})

# TC-522: a parameter default longer than this many characters is cut and ends in "...".
_DEFAULT_TEXT_LIMIT = 120

# TC-555: the bounded textual repair of a file whose indentation was collapsed upstream. At most this
# many repairs are made before the original ``SyntaxError`` is raised.
_MAX_REPAIRS = 2000
_REPAIR_TRY = "if True: pass"
_REPAIR_INERT = "if False: pass"
_BARE_TRY = re.compile(r"^(\s*)try:\s*$")
_LADDER_HEADER = re.compile(r"^(\s*)(?:except\b.*|finally)\s*:\s*$")
_LEADING_SPACE = re.compile(r"\s*")

# A repaired file, keyed by its path: (first repaired line, number of repairs).
_RepairLedger = dict[Path, tuple[int, int]]

# TC-532: while ``inspect_public_surface`` runs, each source file is parsed once: base-class and
# re-export resolution look at the same files over and over. Entries are (tree, repairs, first repaired
# line, the SyntaxError if the file could not be recovered). None outside a scan.
_PARSE_CACHE: dict[Path, tuple[ast.Module | None, int, int, SyntaxError | None]] | None = None


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
    # TC-532: the class a member is inherited from (``module.Class``), None for an own member.
    inherited_from: str | None = None
    # TC-532: an enum's members, ``(name, value text)`` in source order (``auto()`` for an automatic one).
    enum_members: tuple[tuple[str, str], ...] = ()
    # TC-532: the literal a property is assigned (``MAX = 5``, ``self.x = 0``, a dataclass field default).
    value: str | None = None
    # TC-532: a class symbol only. ``base_origins`` is ``(base as written, module.Class that defines it)``
    # for each base the source resolves. ``shadows`` is set on an exported name that replaced, by a LATER
    # import of the same name, another class: the replaced class's qualified name. ``shadowed_by`` is set
    # on that replaced class: the exported name that took its place.
    base_origins: tuple[tuple[str, str], ...] = ()
    shadows: str | None = None
    shadowed_by: str | None = None
    # TC-559 (RD3-060): a function, method or property getter whose whole body is ``raise NotImplementedError``
    # (``stub_reason`` "raises NotImplementedError") or an empty ``pass`` / ``...`` ("empty body (pass)").
    # An annotation about the body, not part of the symbol's identity: two symbols equal in every other field
    # stay equal (``compare=False``), so a reader that builds the expected symbol by hand is unaffected.
    stub: bool = field(default=False, compare=False)
    stub_reason: str = field(default="", compare=False)


_ABSTRACT_DECORATORS = frozenset(
    {"abstractmethod", "abc.abstractmethod", "abstractproperty", "abc.abstractproperty"}
)
_PROTOCOL_BASES = frozenset({"Protocol", "typing.Protocol", "typing_extensions.Protocol", "t.Protocol"})
STUB_RAISES_REASON = "raises NotImplementedError"


def stub_reason(item: ast.FunctionDef | ast.AsyncFunctionDef, owner: ast.ClassDef | None = None) -> str:
    """TC-559: why *item* is a stub, or ``""`` when it is not.

    A stub is a function whose entire body is ``raise NotImplementedError`` (with or without a message), or an
    empty ``pass`` or ``...``, in either case after an optional docstring. NOT stubs: an ``@abstractmethod``
    (an abstract method is a contract), an ``@overload`` signature, a member of a ``Protocol`` class, and an
    ``__init__`` whose body is ``pass`` or ``...`` (a constructor with nothing to initialise is a working
    constructor). A body of only a docstring has no statement to judge and is not a stub.
    """
    decorators = {ast.unparse(decorator) for decorator in item.decorator_list}
    if decorators & _ABSTRACT_DECORATORS or decorators & _OVERLOAD_DECORATORS:
        return ""
    if owner is not None and any(ast.unparse(base) in _PROTOCOL_BASES for base in owner.bases):
        return ""
    body = list(item.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1:
        return ""
    statement = body[0]
    if isinstance(statement, ast.Raise) and statement.exc is not None:
        raised = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
        if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
            return STUB_RAISES_REASON
        return ""
    if item.name == "__init__":
        return ""
    if isinstance(statement, ast.Pass):
        return "empty body (pass)"
    if (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    ):
        return "empty body (...)"
    return ""


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


def _enum_member_value(value: ast.expr) -> str:
    """TC-532: the value text of an enum member: the marker ``auto()`` for a call of ``_ENUM_AUTO_CALLS``,
    the source of a literal, and ``""`` for any other expression (a call, a name, an attribute)."""
    if (
        isinstance(value, ast.Call)
        and not value.args
        and not value.keywords
        and ast.unparse(value.func) in _ENUM_AUTO_CALLS
    ):
        return _ENUM_AUTO_MARKER
    try:
        ast.literal_eval(value)
        return ast.unparse(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return ""


def enum_member_pairs(node: ast.ClassDef) -> tuple[tuple[str, str], ...]:
    """TC-532: an enum class body's members as ``(name, value text)`` in source order.

    A member is a plain assignment (``A = 1``, ``B: int = 2``, ``C = auto()``) of a public name; an
    underscore-prefixed name, a method and a nested class are not members. The value is the source of a
    literal, ``auto()`` for an automatic one, and empty for any other expression."""
    members: list[tuple[str, str]] = []
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        text = _enum_member_value(value)
        members.extend(
            (target.id, text)
            for target in targets
            if isinstance(target, ast.Name) and not target.id.startswith("_")
        )
    return tuple(members)


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


def _blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _indent_of(line: str) -> str:
    match = _LEADING_SPACE.match(line)
    return match.group(0) if match else ""


def _parse_repairing_collapsed_indentation(
    text: str, filename: str, *, repaired_lines: list[int] | None = None
) -> tuple[ast.Module, int]:
    """TC-555: parse *text*; a file whose indentation was collapsed is repaired textually first.

    Returns ``(tree, repairs)``: ``repairs`` is 0 when the text parses as written (the tree is then
    exactly ``ast.parse``'s). A file that lost its indentation (every indented line one space, a
    ``def`` followed by its docstring at the same column) is not valid Python, yet its declarations
    are intact. Each ``expected an indented block`` error closes the nearest header before it with
    `` pass``; a collapsed ``try:`` is neutralised to ``_REPAIR_TRY`` and then every ``except`` /
    ``finally`` header (a collapsed ladder cannot be a ladder) becomes ``if False: pass``, as does an
    ``else:`` / ``elif`` left without its ``if``. Lines are edited in place, so every declaration
    keeps its real line. Function bodies are lost; signatures are kept. Any other error, a header
    that does not end in a colon, or more than ``_MAX_REPAIRS`` repairs raises the ORIGINAL
    ``SyntaxError``.

    *repaired_lines*, when given, receives the 1-based line of every header repaired.
    """
    lines = text.split("\n")
    repairs = 0
    original: SyntaxError | None = None
    while True:
        try:
            return ast.parse("\n".join(lines), filename=filename), repairs
        except SyntaxError as exc:
            if original is None:
                original = exc
            if repairs >= _MAX_REPAIRS or exc.lineno is None or not 1 <= exc.lineno <= len(lines):
                raise original from None
            message = exc.msg or ""
            at = exc.lineno - 1
            was_try = False
            if message == "invalid syntax" and (
                lines[at].strip() == "else:" or lines[at].strip().startswith("elif ")
            ):
                header = at
                lines[header] = _indent_of(lines[header]) + _REPAIR_INERT
            elif message.startswith("expected an indented block"):
                header = at - 1
                while header >= 0 and _blank_or_comment(lines[header]):
                    header -= 1
                if header < 0:
                    raise original from None
                bare_try = _BARE_TRY.match(lines[header])
                if bare_try:
                    lines[header] = bare_try.group(1) + _REPAIR_TRY
                    was_try = True
                elif lines[header].rstrip().endswith(":"):
                    lines[header] = lines[header].rstrip() + " pass"
                else:
                    raise original from None
            elif message.startswith("expected 'except' or 'finally' block"):
                header = at
                while header >= 0 and not lines[header].strip().startswith("try:"):
                    header -= 1
                if header < 0:
                    raise original from None
                lines[header] = _indent_of(lines[header]) + _REPAIR_TRY
                was_try = True
            else:
                raise original from None
            repairs += 1
            if repaired_lines is not None:
                repaired_lines.append(header + 1)
            if was_try:
                for index, line in enumerate(lines):
                    ladder = _LADDER_HEADER.match(line)
                    if ladder:
                        lines[index] = ladder.group(1) + _REPAIR_INERT


def _parse_source_file(path: Path, repairs: _RepairLedger | None = None) -> ast.Module:
    """TC-555: the parsed module at *path*, repaired when its indentation is collapsed.

    A repaired file is recorded in *repairs* (``path -> (first repaired line, repair count)``) so the
    caller can disclose it. Raises ``SyntaxError`` when the file cannot be recovered.
    """
    cache = _PARSE_CACHE
    if cache is not None and path in cache:
        tree, count, first_line, error = cache[path]
    else:
        lines: list[int] = []
        tree, count, error = None, 0, None
        try:
            tree, count = _parse_repairing_collapsed_indentation(
                path.read_text(encoding="utf-8-sig", errors="replace"), str(path), repaired_lines=lines
            )
        except SyntaxError as exc:
            error = exc
        first_line = min(lines) if lines else 0
        if cache is not None:
            cache[path] = (tree, count, first_line, error)
    if error is not None:
        raise error
    assert tree is not None
    if count and repairs is not None:
        repairs[path] = (first_line, count)
    return tree


def _repaired_entry(module: str, relative: str, first_line: int, count: int) -> str:
    """The ``unresolved`` entry that discloses a file parsed only after its repair."""
    return f"{module}:{first_line}:repaired-syntax:{relative}:{count} header lines closed with pass"


def _module_symbols(
    path: Path, source_root: Path, repository_root: Path
) -> tuple[list[PublicSymbol], list[str]]:
    module, is_package = _module_name(path, source_root)
    relative = path.relative_to(repository_root).as_posix()
    if not module or any(part.startswith("_") for part in module.split(".")):
        return [], []
    repairs: _RepairLedger = {}
    try:
        tree = _parse_source_file(path, repairs)
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
    unresolved: list[str] = [
        _repaired_entry(module, relative, first_line, count) for first_line, count in repairs.values()
    ]
    context = _Context(module, source_root, _module_functions(tree))
    # TC-532: a name defined twice at the top level is the LAST definition.
    last_definition = {
        node.name: index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if last_definition[node.name] != index:
                continue
            public, public_by = _public_name(node.name, explicit)
            if public:
                kind: SymbolKind = _class_kind(node) if isinstance(node, ast.ClassDef) else "function"
                bases, return_type, param_types = _structured_fields(node)
                function_stub = "" if isinstance(node, ast.ClassDef) else stub_reason(node)
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
                        enum_members=enum_member_pairs(node)
                        if kind == "enum" and isinstance(node, ast.ClassDef)
                        else (),
                        base_origins=_base_origins(node, module, source_root)
                        if isinstance(node, ast.ClassDef)
                        else (),
                        stub=bool(function_stub),
                        stub_reason=function_stub,
                    )
                )
                if isinstance(node, ast.ClassDef):
                    symbols.extend(_methods(node, module, relative, public_by, context))
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


def _is_overload(item: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """TC-532: whether *item* is a ``@typing.overload`` stub, which never replaces what follows it."""
    return any(ast.unparse(decorator) in _OVERLOAD_DECORATORS for decorator in item.decorator_list)


def _is_accessor(item: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """TC-532: whether *item* is ``@<its own name>.setter`` or ``.deleter``: the second half of a property."""
    return any(
        isinstance(decorator, ast.Attribute)
        and decorator.attr in ("setter", "deleter")
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id == item.name
        for decorator in item.decorator_list
    )


def _winning_definition(
    definitions: Sequence[ast.FunctionDef | ast.AsyncFunctionDef],
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """TC-532: the definition of a name that Python keeps when a class body defines it more than once:
    the LAST one. ``@overload`` stubs never replace the implementation that follows them (only stubs: the
    last stub), and ``@x.setter`` / ``@x.deleter`` belong to the property their getter started."""
    real = [item for item in definitions if not _is_overload(item)] or list(definitions)
    primary = [item for item in real if not _is_accessor(item)] or real
    return primary[-1]


def _is_shadowed_definition(
    item: ast.FunctionDef | ast.AsyncFunctionDef, siblings: Sequence[ast.FunctionDef | ast.AsyncFunctionDef]
) -> bool:
    """TC-532: whether a later definition of the same name in the same class body replaces *item*."""
    return _winning_definition(siblings) is not item


def _definition_groups(node: ast.ClassDef) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    """The function definitions of a class body grouped by name, names in order of first appearance."""
    groups: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            groups.setdefault(item.name, []).append(item)
    return groups


@dataclass(frozen=True)
class _Context:
    """TC-532: what a class body can be read against: its module, the source root and the module-level
    functions (the factories behind ``x = _name_property(...)``)."""

    module: str
    source_root: Path | None = None
    functions: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)

    def class_of(self, expression: ast.expr) -> tuple[ast.ClassDef, _Context] | None:
        """The class a base-class expression names, with the context of the module that defines it."""
        if self.source_root is None or not isinstance(expression, ast.Name):
            return None
        located = _find_class(self.module, expression.id, self.source_root)
        if located is None:
            return None
        node, holder, _ = located
        return node, _context_for_module(holder, self.source_root)


def _module_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """The module-level functions of *tree*, the last definition of a name winning."""
    return {item.name: item for item in tree.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)}


def _context_for_module(module: str, source_root: Path | None) -> _Context:
    """The context of the module *module* under *source_root* (empty functions if it cannot be read)."""
    if source_root is None:
        return _Context(module)
    package_path = source_root / Path(*module.split("."))
    for candidate in (package_path.with_suffix(".py"), package_path / "__init__.py"):
        if candidate.is_file():
            try:
                return _Context(module, source_root, _module_functions(_parse_source_file(candidate)))
            except SyntaxError:
                break
    return _Context(module, source_root)


def _is_true(expression: ast.expr | None) -> bool:
    return isinstance(expression, ast.Constant) and expression.value is True


def _is_false(expression: ast.expr | None) -> bool:
    return isinstance(expression, ast.Constant) and expression.value is False


def _dataclass_options(node: ast.ClassDef) -> dict[str, ast.expr] | None:
    """TC-532: the keyword arguments of ``@dataclass(...)`` (``{}`` for a bare ``@dataclass``); None when
    the class is not decorated with a dataclass."""
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        target = call.func if call is not None else decorator
        if ast.unparse(target) in _DATACLASS_DECORATORS:
            return {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg} if call else {}
    return None


@dataclass(frozen=True)
class _InitField:
    name: str
    annotation: str
    default: str  # "" when the argument is required
    keyword_only: bool


def _init_fields(
    node: ast.ClassDef, context: _Context | None, visiting: frozenset[int] = frozenset()
) -> list[_InitField] | None:
    """TC-532: the ``__init__`` arguments the class body implies when it is a ``@dataclass`` or a
    ``NamedTuple``, base-class dataclass fields first (a redefined field keeps its original place), in
    the order Python generates them (keyword-only ones last); None when the class is neither or when
    ``init=False``.

    Honoured: ``field(default=..., default_factory=..., init=False, kw_only=...)``, the ``kw_only=True``
    decorator argument, the ``KW_ONLY`` sentinel, ``ClassVar`` (not a field) and ``InitVar[T]`` (an
    argument of type ``T``). A ``default_factory`` is shown as ``<factory>``, as ``inspect.signature``
    does.
    """
    options = _dataclass_options(node)
    named_tuple = any(ast.unparse(base) in _NAMEDTUPLE_BASES for base in node.bases)
    if options is None and not named_tuple:
        return None
    if options is not None and _is_false(options.get("init")):
        return None
    fields: dict[str, _InitField] = {}
    if options is not None and context is not None:
        for base in node.bases:
            located = context.class_of(base)
            if located is None or id(located[0]) in visiting or located[0] is node:
                continue
            for inherited in _init_fields(located[0], located[1], visiting | {id(node)}) or []:
                fields[inherited.name] = inherited
    class_keyword_only = options is not None and _is_true(options.get("kw_only"))
    keyword_only_from_here = False
    for statement in node.body:
        if not (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)):
            continue
        annotation = ast.unparse(statement.annotation)
        outer = annotation.partition("[")[0]
        if outer.rsplit(".", 1)[-1] == "KW_ONLY":
            keyword_only_from_here = True
            continue
        if outer.rsplit(".", 1)[-1] == "ClassVar":
            continue
        if outer.rsplit(".", 1)[-1] == "InitVar" and isinstance(statement.annotation, ast.Subscript):
            annotation = ast.unparse(statement.annotation.slice)
        default = ""
        keyword_only = class_keyword_only or keyword_only_from_here
        value = statement.value
        if isinstance(value, ast.Call) and ast.unparse(value.func) in ("field", "dataclasses.field"):
            keywords = {keyword.arg: keyword.value for keyword in value.keywords if keyword.arg}
            if _is_false(keywords.get("init")):
                fields.pop(statement.target.id, None)
                continue
            if "default" in keywords:
                default = _default_text(keywords["default"])
            elif "default_factory" in keywords:
                default = "<factory>"
            if _is_true(keywords.get("kw_only")):
                keyword_only = True
            elif _is_false(keywords.get("kw_only")):
                keyword_only = False
        elif value is not None:
            default = _default_text(value)
        fields[statement.target.id] = _InitField(statement.target.id, annotation, default, keyword_only)
    return list(fields.values())


def _synthesized_init(
    node: ast.ClassDef, module: str, relative: str, public_by: PublicBy, context: _Context | None
) -> PublicSymbol | None:
    """TC-532: the ``__init__`` Python generates for a dataclass / NamedTuple without one of its own."""
    fields = _init_fields(node, context)
    if fields is None:
        return None
    ordered = [f for f in fields if not f.keyword_only] + [f for f in fields if f.keyword_only]
    parts = ["self"]
    star_done = False
    for item in ordered:
        if item.keyword_only and not star_done:
            parts.append("*")
            star_done = True
        text = f"{item.name}: {item.annotation}" if item.annotation else item.name
        parts.append(f"{text} = {item.default}" if item.default else text)
    return PublicSymbol(
        f"{module}.{node.name}.__init__",
        module,
        "__init__",
        "method",
        relative,
        node.lineno,
        public_by,
        docstring=None,
        signature=f"def __init__({', '.join(parts)})",
        param_types=tuple((item.name, item.annotation) for item in ordered if item.annotation),
        params=tuple((item.name, item.annotation) for item in ordered),
        param_details=tuple(
            (item.name, "keyword_only" if item.keyword_only else "positional", item.default)
            for item in ordered
        ),
    )


def _methods(
    node: ast.ClassDef,
    module: str,
    relative: str,
    public_by: PublicBy,
    context: _Context | None = None,
) -> list[PublicSymbol]:
    """The public members a class body defines, each once, in source order.

    A method is public when its name has no leading underscore; ``__init__`` (and only that
    dunder) is the constructor and is public too. Every ``@property`` /
    ``@cached_property`` getter is a ``property`` member rather than a method, whether or not it
    carries a return annotation (an unannotated one has an empty type), writable only when a
    ``@<name>.setter`` sibling exists.

    TC-532: a name defined more than once is the LAST definition (Python's rule, see
    ``_winning_definition``); a ``@dataclass`` / ``NamedTuple`` without an ``__init__`` of its own gets
    the one Python generates for it.
    """
    found: list[PublicSymbol] = []
    seen: set[str] = set()
    setters = _setter_names(node)
    groups = _definition_groups(node)
    synthesized = (
        None if "__init__" in groups else _synthesized_init(node, module, relative, public_by, context)
    )
    if synthesized is not None:
        found.append(synthesized)
        seen.add("__init__")
    for name, definitions in groups.items():
        published = name == "__init__" or name in PUBLISHED_DUNDERS
        if name.startswith("_") and not published:
            continue
        item = _winning_definition(definitions)
        seen.add(name)
        bases, return_type, param_types = _structured_fields(item)
        is_property = _is_property_getter(item)
        member_stub = stub_reason(item, node)
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
                stub=bool(member_stub),
                stub_reason=member_stub,
            )
        )
    if _class_kind(node) != "enum":
        found.extend(_attribute_members(node, module, relative, public_by, seen, context=context))
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
    value: str | None = None,
    docstring: str | None = None,
) -> PublicSymbol:
    return PublicSymbol(
        f"{module}.{node.name}.{name}",
        module,
        name,
        "property",
        relative,
        line,
        public_by,
        docstring=docstring,
        return_type=type_text or None,
        writable=writable,
        value=value,
    )


def _self_attribute(target: ast.expr) -> str | None:
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
        return target.attr
    return None


def _literal_text(value: ast.expr | None) -> str | None:
    """TC-532: the source text of *value* when it is a literal (cut at ``_DEFAULT_TEXT_LIMIT``), else None."""
    if value is None:
        return None
    try:
        ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    return _default_text(value)


def _init_attribute_targets(statements: Sequence[ast.stmt]) -> list[tuple[str, str, int, str | None]]:
    """``(name, type text, line, literal text or None)`` for each ``self.<name> = ...`` /
    ``self.<name>: T = ...`` found in *statements*, nested blocks included, nested function and class
    definitions excluded. TC-532: a literal right-hand side is carried as the last element."""
    found: list[tuple[str, str, int, str | None]] = []

    for stmt in statements:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                attr = _self_attribute(target)
                if attr is not None:
                    found.append((attr, "", stmt.lineno, _literal_text(stmt.value)))
        elif isinstance(stmt, ast.AnnAssign):
            attr = _self_attribute(stmt.target)
            if attr is not None:
                found.append((attr, ast.unparse(stmt.annotation), stmt.lineno, _literal_text(stmt.value)))
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


@dataclass(frozen=True)
class _PropertySpec:
    type_text: str
    writable: bool
    doc: str | None


def _return_annotation(function: ast.AST | None) -> str:
    if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef) and function.returns is not None:
        return ast.unparse(function.returns)
    return ""


def _property_call_spec(
    call: ast.Call,
    scope: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
    doc: str | None = None,
) -> _PropertySpec | None:
    """TC-532: what ``property(fget, fset, fdel, doc=...)`` makes of a class attribute, with *scope* the
    functions its ``fget`` may name; None when *call* is not a call of ``property``."""
    if ast.unparse(call.func) not in ("property", "builtins.property"):
        return None
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    getter = call.args[0] if call.args else keywords.get("fget")
    setter = call.args[1] if len(call.args) > 1 else keywords.get("fset")
    writable = setter is not None and not (isinstance(setter, ast.Constant) and setter.value is None)
    type_text = _return_annotation(scope.get(getter.id)) if isinstance(getter, ast.Name) else ""
    explicit_doc = keywords.get("doc")
    if isinstance(explicit_doc, ast.Constant) and isinstance(explicit_doc.value, str):
        text = explicit_doc.value
        doc = (text.strip().splitlines() or [""])[0].strip() or doc
    return _PropertySpec(type_text, writable, doc)


def _property_spec(
    value: ast.expr,
    class_scope: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
    context: _Context | None,
) -> _PropertySpec | None:
    """TC-532: a class-level assignment that builds a property: ``x = property(get_x, set_x)`` or
    ``x = _name_property("Name", "doc")`` where the module-level function ``_name_property`` returns
    ``property(getter, setter, doc=doc)`` (its annotation is ``property``, or its body ends in such a
    ``return``). The type is the getter's return annotation, writability is the presence of a setter."""
    if not isinstance(value, ast.Call):
        return None
    direct = _property_call_spec(value, class_scope)
    if direct is not None:
        return direct
    if context is None or not isinstance(value.func, ast.Name):
        return None
    factory = context.functions.get(value.func.id)
    if factory is None:
        return None
    nested = {
        item.name: item
        for item in ast.walk(factory)
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    returned = [
        statement.value
        for statement in ast.walk(factory)
        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call)
    ]
    for call in returned:
        spec = _property_call_spec(call, nested)
        if spec is None:
            continue
        # The factory's ``doc`` parameter, bound to the argument of this call, is the property's doc.
        parameters = [argument.arg for argument in factory.args.args]
        doc = None
        if "doc" in parameters:
            bound = parameters.index("doc")
            argument = value.args[bound] if bound < len(value.args) else None
            for keyword in value.keywords:
                if keyword.arg == "doc":
                    argument = keyword.value
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                doc = (argument.value.strip().splitlines() or [""])[0].strip() or None
        return _PropertySpec(spec.type_text, spec.writable, doc or spec.doc)
    return None


def _attribute_members(
    node: ast.ClassDef,
    module: str,
    relative: str,
    public_by: PublicBy,
    seen: set[str],
    *,
    context: _Context | None = None,
) -> list[PublicSymbol]:
    """TC-473: public attribute members - class-body annotated fields and ``__init__`` ``self.<name>``
    assignments - as writable ``property`` symbols, each name once and never one already in *seen*.

    TC-545: a public class-body assignment with one plain ``Name`` target and a literal value
    (``X_AXIS = None``, ``MAX = 5``) is a constant: a read-only ``property`` typed by its literal
    (empty for ``None``), in source order beside the annotated fields. A call, a name, an attribute,
    a chained or tuple target, or an underscore-prefixed name is not a constant and is skipped.

    TC-532: the literal itself is carried (``value``), for a constant, for a dataclass field default and
    for a literal ``self.x = 0`` in ``__init__``; a class-level ``x = property(get_x)`` or
    ``x = _name_property(...)`` factory call is a property typed by its getter; the fields of a
    ``frozen=True`` dataclass are read-only."""
    candidates: list[tuple[str, str, int, str | None, bool | None, str | None]] = []
    constants: set[str] = set()
    class_scope = {
        item.name: item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    options = _dataclass_options(node)
    frozen = options is not None and _is_true(options.get("frozen"))
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            type_text = ast.unparse(item.annotation)
            if type_text.startswith("ClassVar") or type_text.startswith(("typing.ClassVar", "t.ClassVar")):
                continue
            default = item.value
            if (
                isinstance(default, ast.Call)
                and ast.unparse(default.func) in ("field", "dataclasses.field")
                and any(keyword.arg == "default" for keyword in default.keywords)
            ):
                default = next(keyword.value for keyword in default.keywords if keyword.arg == "default")
            candidates.append(
                (
                    item.target.id,
                    type_text,
                    item.lineno,
                    _literal_text(default),
                    False if frozen else None,
                    None,
                )
            )
        elif (
            isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
            and not item.targets[0].id.startswith("_")
        ):
            name = item.targets[0].id
            constant_type = _constant_type_text(item.value)
            if constant_type is not None:
                if name not in {known for known, *_ in candidates}:
                    constants.add(name)
                candidates.append((name, constant_type, item.lineno, _literal_text(item.value), None, None))
                continue
            spec = _property_spec(item.value, class_scope, context)
            if spec is not None:
                candidates.append((name, spec.type_text, item.lineno, None, spec.writable, spec.doc))
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == "__init__":
            candidates.extend(
                (name, type_text, line, literal, None, None)
                for name, type_text, line, literal in _init_attribute_targets(item.body)
            )
            break
    found: list[PublicSymbol] = []
    for name, type_text, line, literal, writable, doc in candidates:
        if name.startswith("_") or name in seen:
            continue
        seen.add(name)
        found.append(
            _attribute_symbol(
                node,
                module,
                relative,
                public_by,
                name,
                type_text,
                line,
                writable=(name not in constants) if writable is None else writable,
                value=literal,
                docstring=doc,
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
    enum_members: tuple[tuple[str, str], ...] = ()  # TC-532: an enum origin's members
    base_origins: tuple[tuple[str, str], ...] = ()  # TC-532: (base as written, defining class)


def _last_binding(
    tree: ast.Module, name: str, module: str, is_package: bool
) -> tuple[Literal["definition", "import"], Any] | None:
    """TC-532: what the top level of *tree* finally binds *name* to: ``("definition", node)`` for the
    last ``class`` / ``def`` of that name, ``("import", origin)`` for the last ``from ... import`` that
    names it, whichever comes LAST in the file (Python rebinds a name each time it is assigned)."""
    bound: tuple[Literal["definition", "import"], Any] | None = None
    for item in tree.body:
        if isinstance(item, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name:
            bound = ("definition", item)
        elif isinstance(item, ast.ImportFrom):
            forwarded = _forwarded_origin(item, module, is_package, name)
            if forwarded is not None:
                bound = ("import", forwarded)
    return bound


def _base_origins(node: ast.ClassDef, module: str, source_root: Path | None) -> tuple[tuple[str, str], ...]:
    """TC-532: ``(base as written, module.Class that defines it)`` for each plain-name base of *node* that
    the source resolves, read in the module that holds *node*'s body."""
    if source_root is None:
        return ()
    found: list[tuple[str, str]] = []
    for base in node.bases:
        if not isinstance(base, ast.Name):
            continue
        located = _find_class(module, base.id, source_root)
        if located is None or located[0] is node:
            continue
        found.append((base.id, f"{located[1]}.{located[0].name}"))
    return tuple(found)


def _origin_definition(
    origin: str,
    source_root: Path,
    seen: frozenset[str] = frozenset(),
    repairs: _RepairLedger | None = None,
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
            tree = _parse_source_file(candidate, repairs)
        except SyntaxError:
            return None
        binding = _last_binding(tree, name, module, candidate.name == "__init__.py")
        if binding is not None and binding[0] == "definition":
            found = binding[1]
            kind: SymbolKind = _class_kind(found) if isinstance(found, ast.ClassDef) else "function"
            bases, return_type, param_types = _structured_fields(found)
            context = _Context(module, source_root, _module_functions(tree))
            methods = (
                tuple(_methods(found, module, candidate.name, "reexport", context))
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
                enum_members=enum_member_pairs(found)
                if kind == "enum" and isinstance(found, ast.ClassDef)
                else (),
                base_origins=_base_origins(found, module, source_root)
                if isinstance(found, ast.ClassDef)
                else (),
            )
        forwarded = binding[1] if binding is not None else None
        if forwarded is None or forwarded in seen or forwarded == origin:
            return None
        return _origin_definition(forwarded, source_root, seen | {origin}, repairs)
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
            tree = _parse_source_file(candidate)
        except SyntaxError as exc:
            relative = candidate.relative_to(repository_root).as_posix()
            return f"{module}:{exc.lineno or 0}:syntax-error:{relative}:{exc.msg}"
        binding = _last_binding(tree, name, module, candidate.name == "__init__.py")
        if binding is not None and binding[0] == "definition":
            return None
        forwarded = binding[1] if binding is not None else None
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


def _is_foreign_import(origin: str, source_root: Path) -> bool:
    """TC-555: whether *origin* names code outside the scanned source tree (the standard library or
    a third-party package: ``pathlib.Path``, ``typing.TYPE_CHECKING``, ``__future__.annotations``).

    A package ``__init__`` that holds real definitions and also imports ``dataclass`` or ``Path``
    would otherwise report each as an ``unresolved-reexport``: not a failure of the extractor to
    read the package, only an import of something the package does not own. Its symbol is kept
    (it is never a type), only the false ``unresolved`` entry is not recorded.
    """
    top = origin.split(".", 1)[0]
    return not ((source_root / top).is_dir() or (source_root / f"{top}.py").is_file())


def _forwarded_origin(node: ast.ImportFrom, module: str, is_package: bool, name: str) -> str | None:
    """Where an ``import from`` statement says ``name`` really comes from, if it names it."""
    for alias in node.names:
        if (alias.asname or alias.name) != name:
            continue
        origin = _resolved_relative(module, is_package, node)
        return f"{origin}.{alias.name}" if origin else None
    return None


def _reexport_kind(
    symbol: PublicSymbol,
    symbols: dict[str, PublicSymbol],
    source_root: Path,
    repairs: _RepairLedger | None = None,
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
            definition = _origin_definition(target, source_root, frozenset(seen), repairs)
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
    module: str,
    name: str,
    source_root: Path,
    seen: frozenset[str] = frozenset(),
    repairs: _RepairLedger | None = None,
) -> tuple[ast.ClassDef, str, bool] | None:
    """TC-429: the ``ClassDef`` that binds *name* in *module*, with the module that holds its body.

    Returns ``(class node, module holding the body, whether that module is a package
    __init__)``. A module that only imports the name is followed to its origin with the same
    rules and cycle guard as ``_origin_definition``; ``None`` when no class definition is
    reached (absent, not a class, unparsable, or a cycle). TC-532: the LAST top-level binding of the
    name in a module is the one that counts (``_last_binding``).
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
            tree = _parse_source_file(candidate, repairs)
        except SyntaxError:
            return None
        binding = _last_binding(tree, name, module, is_package)
        if binding is None:
            return None
        if binding[0] == "definition":
            return (binding[1], module, is_package) if isinstance(binding[1], ast.ClassDef) else None
        forwarded = binding[1]
        if "." not in forwarded:
            return None
        next_module, next_name = forwarded.rsplit(".", 1)
        return _find_class(next_module, next_name, source_root, seen | {key}, repairs)
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
    symbols: dict[str, PublicSymbol], source_root: Path, repairs: _RepairLedger | None = None
) -> list[PublicSymbol]:
    """TC-429: the public methods a public class inherits from its NON-PUBLIC base classes.

    A public class built from private mixins (``class SampleClient(_CoreClientMixin,
    _MailClientMixin)``) was served with no methods: the mixins are private, so never a public
    symbol, and nothing flattened inheritance. For every public class symbol, each ``Name``
    base is resolved in the module holding the class body (a class in the same module, or an
    ``import from`` binding followed to its definition). A non-public base (underscore name, or
    defined in a module with an underscore path component) contributes its public methods, then
    its own bases recursively: the subclass's own methods first, then bases in
    declaration order, depth first, the first definition of a name winning. Unresolvable and
    non-class bases contribute nothing, and a base cycle terminates.

    TC-532: the walk does not stop at the first public ancestor behind a private base: pdf/python's
    ``CircleAnnotation(_ShapeAnnotation)`` with ``_ShapeAnnotation(MarkupAnnotation)`` inherits the
    members of ``MarkupAnnotation`` and ``Annotation`` too, and nothing else would link a public class to
    them through the private one. Each contributed member (a constructor excepted, which is shown as the
    class's own as before) records the class it comes from in ``inherited_from``.

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
        located = _find_class(body_module, body_name, source_root, repairs=repairs)
        if located is None:
            continue
        own, holder, _ = located
        taken = {item.name for item in own.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)}
        visited: set[tuple[str, str]] = {(holder, own.name)}

        def visit(
            node: ast.ClassDef,
            node_module: str,
            through_private: bool,
            symbol: PublicSymbol = symbol,
            taken: set[str] = taken,
            visited: set[tuple[str, str]] = visited,
        ) -> None:
            for base in node.bases:
                if not isinstance(base, ast.Name):
                    continue
                base_located = _find_class(node_module, base.id, source_root, repairs=repairs)
                if base_located is None:
                    continue
                base_node, base_module, _ = base_located
                private = _is_non_public_base(base.id, base_node, base_module)
                if not private and not through_private:
                    continue
                if (base_module, base_node.name) in visited:
                    continue
                visited.add((base_module, base_node.name))
                owner = f"{base_module}.{base_node.name}"
                context = _context_for_module(base_module, source_root)
                for method in _methods(base_node, base_module, symbol.source_path, symbol.public_by, context):
                    name = f"{symbol.qualified_name}.{method.name}"
                    if method.name in taken or name in symbols or name in added:
                        continue
                    added[name] = replace(
                        method,
                        qualified_name=name,
                        module=symbol.module,
                        source_path=symbol.source_path,
                        line=symbol.line,
                        inherited_from=None
                        if method.name == "__init__"
                        else (method.inherited_from or owner),
                    )
                visit(base_node, base_module, True)

        visit(own, holder, False)
    return list(added.values())


def _minimal_roots(package_dirs: Sequence[str]) -> list[str]:
    """Drop package directories that another listed directory already contains."""
    ordered = sorted(set(package_dirs))
    return [d for d in ordered if not any(d.startswith(other + "/") for other in ordered)]


def _definition_of(origin: str, symbols: dict[str, PublicSymbol]) -> str:
    """TC-532: the qualified name a chain of re-exports finally ends at (or *origin* when it ends at a
    name the scan never saw)."""
    for _ in range(16):
        target = symbols.get(origin)
        if target is None or target.reexported_from is None or target.reexported_from == origin:
            return origin
        origin = target.reexported_from
    return origin


def inspect_public_surface(repository_root: Path, package_dirs: Sequence[str]) -> PublicSurface:
    """Inspect the packages at ``package_dirs`` (repository-relative) without importing them."""
    global _PARSE_CACHE
    previous = _PARSE_CACHE
    _PARSE_CACHE = {}
    try:
        return _inspect_public_surface(repository_root, package_dirs)
    finally:
        _PARSE_CACHE = previous


def _inspect_public_surface(repository_root: Path, package_dirs: Sequence[str]) -> PublicSurface:
    symbols: dict[str, PublicSymbol] = {}
    unresolved: list[str] = []
    # TC-532: an exported name imported more than once binds the LAST import; the origin it replaced.
    replaced: dict[str, str] = {}
    for package_dir in _minimal_roots(package_dirs):
        package_path = repository_root / package_dir
        source_root = package_path.parent
        repairs: _RepairLedger = {}
        for path in sorted(package_path.rglob("*.py")):
            if any(part in _EXCLUDED_PARTS for part in path.relative_to(source_root).parts):
                continue
            module_symbols, module_unresolved = _module_symbols(path, source_root, repository_root)
            unresolved.extend(module_unresolved)
            for symbol in module_symbols:
                current = symbols.get(symbol.qualified_name)
                if current is None or (symbol.public_by == "reexport" and current.kind == "module"):
                    symbols[symbol.qualified_name] = symbol
                elif (
                    # Python binds a name to its last import: a later re-export of the
                    # same public name replaces the earlier one (a package importing
                    # ColladaLoadOptions from two modules exposes the second).
                    symbol.public_by == "reexport"
                    and current.public_by == "reexport"
                    and symbol.line > current.line
                ):
                    if current.reexported_from and current.reexported_from != symbol.reexported_from:
                        replaced[symbol.qualified_name] = current.reexported_from
                    symbols[symbol.qualified_name] = symbol
        for name, symbol in list(symbols.items()):
            if symbol.reexported_from is None or symbol.kind != "unknown":
                continue
            if _is_foreign_import(symbol.reexported_from, source_root):
                continue  # TC-555: an import of another library is not a re-export to resolve
            origin, kind, definition = _reexport_kind(symbol, symbols, source_root, repairs)
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
                    # The members travel with a re-export only when no scanned definition carries them: an
                    # enum read from a private module (the shell is then its only entry), or a re-export of
                    # such a shell. A shell of a scanned definition stays empty and is paired with it.
                    enum_members=definition.enum_members
                    if definition is not None
                    else (origin.enum_members if origin is not None and origin.reexported_from else ()),
                    base_origins=origin.base_origins
                    if origin is not None
                    else (definition.base_origins if definition else ()),
                )
                symbols[name] = resolved
                # An underscore-origin class's own methods are never otherwise scanned (see
                # _reexported_methods()'s own docstring); recover them here, the one place that
                # already has both the resolved PublicSymbol and its origin's evidence.
                if definition is not None:
                    for method_symbol in _reexported_methods(definition, resolved, name):
                        if method_symbol.qualified_name not in symbols:
                            symbols[method_symbol.qualified_name] = method_symbol
        # TC-532: an exported class name whose LAST import replaced a different class of the same name
        # is marked on both sides, so nothing pairs the exported (often empty) class with the other.
        for exported_name, earlier in replaced.items():
            exported = symbols.get(exported_name)
            if exported is None or exported.reexported_from is None or exported.kind not in ("class", "enum"):
                continue
            earlier_definition = _definition_of(earlier, symbols)
            if earlier_definition == _definition_of(exported.reexported_from, symbols):
                continue
            replaced_symbol = symbols.get(earlier_definition)
            if replaced_symbol is None or replaced_symbol.kind not in ("class", "enum"):
                continue
            symbols[exported_name] = replace(exported, shadows=earlier_definition)
            symbols[earlier_definition] = replace(replaced_symbol, shadowed_by=exported_name)
        # TC-429: a public class built from private mixins gains their public methods.
        for inherited in _inherited_private_base_methods(symbols, source_root, repairs):
            symbols.setdefault(inherited.qualified_name, inherited)
        # TC-555: a file reached only through a re-export or a private mixin was repaired too; the
        # entry is the same string ``_module_symbols`` makes for a scanned file, so a set merges them.
        for repaired_path, (first_line, count) in repairs.items():
            repaired_module, _ = _module_name(repaired_path, source_root)
            relative = repaired_path.relative_to(repository_root).as_posix()
            unresolved.append(_repaired_entry(repaired_module, relative, first_line, count))
    return PublicSurface(
        symbols=tuple(symbols[name] for name in sorted(symbols)),
        unresolved=tuple(sorted(set(unresolved))),
    )
