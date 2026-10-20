# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/lang/csharp.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""extraction/lang/csharp.py â€” C# language adapter (TC-MT040-42).

See extraction/lang/__init__.py for the shared adapter contract this module
implements: declaration_kinds(), doc_anchor(), parse_doc(), export_surface(),
clear_cache().

export_surface() carries a relocation of what was
content_eval/evaluators/_reachability.py's ``_csharp_internal_names`` (the
evaluator's live clone-cache fallback for C# reachability) -- this is the
FIRST time this signal becomes available to the scout side (api_surface.py)
rather than only the evaluator side. It is NOT currently wired into
api_surface._compute_reachable's dispatch (which stays python/rust-only,
unchanged) -- see the TC-MT040-42 completion report for why: is_public()
already excludes non-`public` (including bare `internal`) C# classes from
extraction entirely, so a class this function would flag never reaches
_compute_reachable in the first place; wiring it there today would be
inert. It is available for a future card to consume.

IMPORTANT â€” polarity: unlike python/rust's export_surface (a positive
reachable-name allowlist), this function returns the set of names CONFIRMED
`internal` (i.e. UNREACHABLE). C# has no positive export-list mechanism the
way Python's `__all__` or Rust's `pub use` do; the best available live
signal, inherited unchanged from the original evaluator-side check, is a
denylist scan of `internal` declarations across the whole clone. Callers
must test membership as "in this set == unreachable", the OPPOSITE test
python/rust callers use ("in this set == reachable"). See
extraction/lang/__init__.py's module docstring.
"""

from __future__ import annotations

import itertools
import re
from functools import lru_cache
from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine.tree_helpers import (
    _CLASS_TYPES,
    _FUNC_TYPES,
    _IMPORT_TYPES,
    _MODULE_TYPES,
    _PREPROC_KNOWN_FALSE_SYMBOLS,
    _PREPROC_SAFE_RE,
    _PREPROC_TOKEN_RE,
    _condition_definitely_false,
    _in_excluded_preproc_branch,
    child_by_field,
    collect_nodes,
    find_child_by_type,
    find_children_by_type,
    node_text,
)

_LANG = "csharp"


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

    C#'s XML doc (``///``) comments precede the item as sibling comment
    nodes -- no wrapper redirection needed, identity.
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
    """Parse a raw C# XML-doc comment block into ``{"summary": str}``.

    *raw* is expected to be the already-gathered, newline-joined run of
    ``///`` comment lines -- tree-walking to gather that multi-line run
    (and to verify comment/declaration line-adjacency) stays in
    api_surface._extract_doc_comment for now (see
    extraction/lang/__init__.py's module docstring). This reproduces that
    function's ``///``-stripping, ``<paramref>``/``<typeparamref>``/``<see
    cref>`` tag-name preservation, remaining-XML-tag stripping, and
    first-sentence logic on an already-joined block.
    """
    lines = raw.split("\n")
    cleaned = " ".join(line.lstrip("/ ").strip() for line in lines)
    cleaned = re.sub(r'<(?:paramref|typeparamref)\s+name="([^"]+)"\s*/?>', r"\1", cleaned)
    cleaned = re.sub(r'<see\s+cref="([^"]+)"\s*/?>', lambda m: m.group(1).rsplit(".", 1)[-1], cleaned)
    cleaned = re.sub(r"<[^>]+>", "", cleaned).strip()
    return {"summary": _first_sentence(cleaned) if cleaned else ""}


# ---------------------------------------------------------------------------
# export_surface
# ---------------------------------------------------------------------------

_INTERNAL_DECL_RE = re.compile(
    r"\binternal\s+(?:sealed\s+|abstract\s+|static\s+|partial\s+)*"
    r"(?:class|struct|interface|enum)\s+(\w+)\b"
)


@lru_cache(maxsize=64)
def _internal_names_cached(repo: Path) -> frozenset:
    """Cached core of internal_names() -- see that function's docstring.
    Cached (functools.lru_cache) because content_eval/evaluators/
    _reachability.py's live_unreachable() calls this once per class-name
    check across a whole evaluator run, and the original _reachability.py
    implementation this replaces (its own _csharp_internal_names) was
    itself lru_cache-memoized for exactly that reason -- a full-repo
    `*.cs` rglob scan is not something every single class-name lookup
    should re-pay.
    """
    if not repo.is_dir():
        return frozenset()
    names: set[str] = set()
    try:
        for f in repo.rglob("*.cs"):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            names.update(_INTERNAL_DECL_RE.findall(text))
    except OSError:
        pass
    return frozenset(names)


def internal_names(repo: Path) -> frozenset:
    """Return every type name declared `internal` (not `public`) at the top
    level anywhere under *repo*. Never raises; returns an empty frozenset
    if *repo* is unavailable or unreadable.

    Moved from content_eval/evaluators/_reachability.py's
    ``_csharp_internal_names`` (TC-MT040-42), generalized from
    (family, platform) to a plain *repo* Path so both the scout side and
    the evaluator side can call it. See this module's docstring for the
    C#-only polarity note (this is a denylist, not an allowlist).
    """
    return _internal_names_cached(repo)


def export_surface(repo: Path, pkg_root: Path) -> tuple[set[str] | None, Path | None]:
    """Return (internal_names, repo) -- see this module's docstring for the
    polarity note (membership here means UNREACHABLE, the opposite test
    from python/rust's export_surface). *pkg_root* is accepted for
    interface uniformity across all 7 language modules but unused here --
    the original check always scanned the whole repository, not a
    sub-package root.
    """
    return set(internal_names(repo)), repo


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


def clear_cache() -> None:
    """Reset the memoized `internal` declaration scan
    (functools.lru_cache). Test/operator hook -- called by
    content_eval/evaluators/_reachability.py's own clear_caches().
    """
    _internal_names_cached.cache_clear()


# ---------------------------------------------------------------------------
# TC-558 (REQ-G3-009): the members and facts the C# language declares and the extraction used to drop
# ---------------------------------------------------------------------------


def _one_line(text: str) -> str:
    return " ".join(text.split())


# -- generic parameters and their constraints (RD3-044) ------------------------------------------------------------------


def type_parameters(node) -> str:
    """The generic parameter list of a type or method as written (``<T>``, ``<in TKey, out TValue>``); empty
    for a declaration that has none."""
    declared = find_child_by_type(node, "type_parameter_list")
    return _one_line(node_text(declared)) if declared is not None else ""


def type_constraints(node) -> str:
    """The ``where`` clauses of a generic type or method, one line (``where T : class, new()``); empty without."""
    clauses = find_children_by_type(node, "type_parameter_constraints_clause")
    return " ".join(_one_line(node_text(clause)) for clause in clauses)


def generic_details(node) -> dict[str, str]:
    """``{"generics": "<T>", "where": "where T : class"}`` of a type or a method, each key only when present;
    the keys are the ones a Rust item and a Java method already carry, so the renderer needs nothing new."""
    details: dict[str, str] = {}
    generics = type_parameters(node)
    if generics:
        details["generics"] = generics
        where = type_constraints(node)
        if where:
            details["where"] = where
    return details


# -- conditional modifiers (RD3-055) ----------------------------------------------------------------------------------------

_MODIFIER_WORDS = (
    rb"public|internal|protected|private|static|abstract|sealed|partial|readonly|unsafe|virtual|override"
    rb"|extern|new|async"
)
_MODIFIER_LINES = rb"((?:[ \t]*(?:" + _MODIFIER_WORDS + rb")[ \t]*\r?\n){1,4})"
_CONDITIONAL_MODIFIERS_RE = re.compile(
    rb"^[ \t]*#[ \t]*if[ \t]+([^\r\n]*?)[ \t]*\r?\n"
    + _MODIFIER_LINES
    + rb"[ \t]*#[ \t]*else[^\r\n]*\r?\n"
    + _MODIFIER_LINES
    + rb"[ \t]*#[ \t]*endif[^\r\n]*(?:\r?\n|$)",
    re.MULTILINE,
)


def resolve_conditional_modifiers(source: bytes) -> bytes:
    """*source* with every ``#if COND / <modifiers> / #else / <modifiers> / #endif`` block that precedes a declaration
    reduced to the modifiers of the branch the packaged .NET library compiles (RD3-055).

    The block sits between ``public``/``internal`` and the keyword of a type or member
    (``#if CPLUSPLUS public #else internal #endif interface INodeCollection``); a parser sees both words as
    modifiers of one declaration and takes it for public. Only a condition that is definitely false (a
    transpilation or build-configuration symbol: ``CPLUSPLUS``, ``JAVA``, ``DEBUG``...) or definitely true
    decides; any other block is left as it is. The line count never changes, so every line number holds.
    """

    def pick(match: re.Match[bytes]) -> bytes:
        condition = match.group(1).decode("ascii", "replace")
        if _condition_definitely_false(condition):
            kept = match.group(3)
        elif _condition_definitely_false(f"!({condition})"):
            kept = match.group(2)
        else:
            return match.group(0)
        kept_line = b" ".join(kept.split()) + b"\n"
        return kept_line + b"\n" * max(match.group(0).count(b"\n") - 1, 0)

    if b"#" not in source:
        return source
    return _CONDITIONAL_MODIFIERS_RE.sub(pick, source)


# -- using aliases (RD3-051) ------------------------------------------------------------------------------------------------

# The packaged library is the net8.0 build of a multi-targeted project (net462;netstandard2.0;net8.0): these symbols
# are defined / not defined in it. A symbol not listed here, nor a transpilation or configuration symbol, is unknown.
PACKAGED_TRUE_SYMBOLS = frozenset({"NET", "NET8_0", "NETCOREAPP"})
PACKAGED_FALSE_SYMBOLS = frozenset(
    {
        "NETSTANDARD",
        "NETFRAMEWORK",
        "NET462",
        "NET48",
        "NET47",
        "NET461",
        "NET9_0",
        "NET10_0",
        "NET7_0",
        "NET6_0",
    }
)
_OR_GREATER_RE = re.compile(r"NET(?:CORE)?(?:APP)?(\d+)_(\d+)_OR_GREATER")


def _symbol_value(symbol: str) -> bool | None:
    if symbol in _PREPROC_KNOWN_FALSE_SYMBOLS or symbol in PACKAGED_FALSE_SYMBOLS:
        return False
    if symbol in PACKAGED_TRUE_SYMBOLS:
        return True
    match = _OR_GREATER_RE.fullmatch(symbol)
    if match is not None and not symbol.startswith("NETSTANDARD"):
        return int(match.group(1)) <= 8
    return None


def condition_value(condition: str) -> bool | None:
    """The truth of a ``#if`` condition in the packaged net8.0 build: True, False, or None when a symbol it depends
    on is unknown and the answer would change with it."""
    condition = condition.split("//", 1)[0].strip()
    if not condition or not _PREPROC_SAFE_RE.match(condition):
        return None
    identifiers = {t for t in _PREPROC_TOKEN_RE.findall(condition) if t not in ("&&", "||", "!", "(", ")")}
    known: dict[str, bool] = {}
    unknown: list[str] = []
    for symbol in sorted(identifiers):
        value = _symbol_value(symbol)
        if value is None:
            unknown.append(symbol)
        else:
            known[symbol] = value
    expression = condition.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    outcomes: set[bool] = set()
    for combo in itertools.product((False, True), repeat=min(len(unknown), 8)):
        env = {**known, **dict(zip(unknown, combo, strict=False))}
        try:
            outcomes.add(bool(eval(expression, {"__builtins__": {}}, env)))  # noqa: S307 - restricted env, own source
        except Exception:
            return None
        if len(outcomes) > 1:
            return None
    return outcomes.pop() if len(outcomes) == 1 else None


def _both(left: bool | None, right: bool | None) -> bool | None:
    if left is False or right is False:
        return False
    if left is None or right is None:
        return None
    return True


def _branch_status(node) -> bool | None:
    """Whether *node* is compiled in the packaged build, from the ``#if`` / ``#elif`` / ``#else`` branches it sits in."""
    status: bool | None = True
    child = node
    parent = node.parent
    while parent is not None:
        if parent.type in ("preproc_if", "preproc_elif"):
            condition = child_by_field(parent, "condition")
            value = condition_value(node_text(condition)) if condition is not None else None
            alternative = parent.child_by_field_name("alternative")
            if alternative is not None and child == alternative:
                value = None if value is None else not value
            status = _both(status, value)
        child = parent
        parent = parent.parent
    return status


def alias_map(root) -> dict[str, str]:
    """``{alias: target}`` of the ``using Alias = Target;`` directives of one file, each resolved for the packaged
    build (RD3-051). An alias declared once is its target; one declared per target framework
    (``#if NETSTANDARD || NET using Image = SkiaSharp.SKBitmap; #else using Image = System.Drawing.Image;``) is the
    target of the branch the net8.0 build compiles; an alias whose branch cannot be decided is not resolved."""
    candidates: dict[str, list[tuple[bool | None, str]]] = {}
    for node in collect_nodes(root, {"using_directive"}):
        name = child_by_field(node, "name")
        if name is None:
            continue
        children = list(node.children)
        equals = next((i for i, ch in enumerate(children) if ch.type == "="), None)
        if equals is None:
            continue
        target = next((ch for ch in children[equals + 1 :] if ch.type != ";"), None)
        if target is None:
            continue
        candidates.setdefault(node_text(name), []).append(
            (_branch_status(node), _one_line(node_text(target)))
        )
    resolved: dict[str, str] = {}
    for alias, options in candidates.items():
        compiled = [target for status, target in options if status is True]
        possible = [target for status, target in options if status is not False]
        if compiled:
            resolved[alias] = compiled[0]
        elif len(set(possible)) == 1:
            resolved[alias] = possible[0]
    return resolved


_IDENTIFIER_IN_TYPE_RE = re.compile(r"(?<![\w.@])[A-Za-z_]\w*")


def resolve_aliases(type_text: str, aliases: dict[str, str]) -> str:
    """*type_text* with every alias name (not preceded by ``.``) replaced by its target: ``List<Image>`` becomes
    ``List<SkiaSharp.SKBitmap>``, ``Drawing2D.Matrix`` becomes ``System.Drawing.Drawing2D.Matrix``."""
    if not type_text or not aliases:
        return type_text
    return _IDENTIFIER_IN_TYPE_RE.sub(lambda m: aliases.get(m.group(0), m.group(0)), type_text)


# -- constructors, records, fields and events --------------------------------------------------------------------------------


def has_modifier(node, keyword: str) -> bool:
    return any(ch.type == "modifier" and node_text(ch).strip() == keyword for ch in node.children)


def body_of(cnode):
    return child_by_field(cnode, "body") or find_child_by_type(cnode, "declaration_list")


def member_nodes(cnode) -> list:
    """The direct members of a type's body, descending into ``#if`` / ``#elif`` / ``#else`` groups (their members
    are members of the type)."""
    body = body_of(cnode)
    if body is None:
        return []
    members: list = []
    stack = list(reversed(body.children))
    while stack:
        node = stack.pop()
        if node.type in ("preproc_if", "preproc_elif", "preproc_else"):
            stack.extend(reversed(node.children))
        else:
            members.append(node)
    return members


def primary_parameters(cnode):
    """The ``parameter_list`` of a record's or a class's primary constructor (``record R(int A)``, C# 12
    ``class C(int a)``), or None."""
    if cnode.type not in ("record_declaration", "class_declaration", "struct_declaration"):
        return None
    return find_child_by_type(cnode, "parameter_list")


def declares_instance_constructor(cnode) -> bool:
    """True when the type body declares an instance constructor of any visibility (a static constructor does not
    count), or the type has a primary constructor. Such a type has no implicit default constructor (C# spec 15.11.5)."""
    if primary_parameters(cnode) is not None:
        return True
    return any(
        member.type == "constructor_declaration"
        and not has_modifier(member, "static")
        and not _in_excluded_preproc_branch(member, "csharp")
        for member in member_nodes(cnode)
    )


def may_get_implicit_constructor(cnode) -> bool:
    """True for a ``class`` that is neither static nor abstract: if it declares no instance constructor it has the
    public implicit parameterless one (C# spec 15.11.5). A struct, interface, enum, delegate and record are not
    covered by this rule (RD3-047)."""
    return (
        cnode.type == "class_declaration"
        and not has_modifier(cnode, "static")
        and not has_modifier(cnode, "abstract")
        and not declares_instance_constructor(cnode)
    )


def record_is_struct(cnode) -> bool:
    return any(ch.type == "struct" for ch in cnode.children)


def record_properties_are_init_only(cnode) -> bool:
    """A positional ``record`` (class) and a ``readonly record struct`` declare ``init`` properties; a plain
    ``record struct`` declares settable ones (C# spec, positional records)."""
    return not record_is_struct(cnode) or has_modifier(cnode, "readonly")
