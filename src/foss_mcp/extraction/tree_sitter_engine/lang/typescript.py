# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/lang/typescript.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""extraction/lang/typescript.py â€” TypeScript language adapter (TC-MT040-42,
real implementations landed by TC-MT040-10/11/12/13/15).

See extraction/lang/__init__.py for the shared adapter contract this module
implements: declaration_kinds(), doc_anchor(), parse_doc(), export_surface(),
clear_cache().
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine.tree_helpers import _CLASS_TYPES, _FUNC_TYPES, _IMPORT_TYPES, _MODULE_TYPES

_LANG = "typescript"


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
# doc_anchor (TC-MT040-12)
# ---------------------------------------------------------------------------

def doc_anchor(node):
    """Return the node whose doc comment belongs to *node*.

    Verified via a live tree-sitter-typescript parse probe: for
    ``export class X {}`` / ``export function f() {}`` / ``export const
    X = ...`` / ``export default class X {}`` / etc., the declaration node
    (``class_declaration``, ``function_declaration``, ``lexical_declaration``,
    ...) is wrapped in a parent ``export_statement`` node via its own
    ``declaration`` field -- and the JSDoc ``/** ... */`` block precedes the
    *export_statement*, not the inner declaration node. The inner
    declaration node has no preceding named sibling of its own (it is
    ``export_statement``'s first/only named child), so the default
    ``prev_named_sibling`` walk in api_surface._extract_doc_comment finds
    nothing for every exported TS declaration -- this was a real, total blind
    spot, not merely a rare edge case (confirmed: it explains why
    ``export abstract class Exporter`` in 3d/typescript's real
    formats/Exporter.ts had zero doc text a plain sibling walk could ever
    reach, JSDoc block included).

    Redirects to the parent `export_statement` only when *node* IS that
    parent's own `declaration` field (guards against redirecting some other,
    unrelated node that merely happens to have an export_statement parent).
    A bare (non-exported) declaration's parent is not `export_statement`, so
    it falls through unchanged -- identity, exactly as before.
    """
    parent = node.parent
    if parent is not None and parent.type == "export_statement":
        try:
            decl = parent.child_by_field_name("declaration")
        except Exception:
            decl = None
        # NOTE: tree-sitter's Python bindings return a fresh Node wrapper
        # object on every attribute access -- `is` identity between two
        # separately-obtained Node objects for the very same underlying
        # tree position is reliably False (confirmed via a live parse
        # probe: `exp.child_by_field_name("declaration") is decl` is False
        # even though both refer to the identical node). `==` (which
        # tree-sitter implements to compare underlying node identity, not
        # structural/text equality) is the correct comparison here.
        if decl is not None and decl == node:
            return parent
    return node


# ---------------------------------------------------------------------------
# parse_doc (TC-MT040-12: full TSDoc block-tag parser)
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


_JSDOC_LINE_RE = re.compile(r"^\s*\*\s?", re.MULTILINE)
_JSDOC_TAG_SPLIT_RE = re.compile(r"(?m)^\s*@(\w+)\b")
_JSDOC_INLINE_LINK_RE = re.compile(r"\{@link\s+([^}]+)\}")
_JSDOC_PARAM_RE = re.compile(
    r"^\s*(?:\{[^}]*\}\s*)?"          # optional {Type}
    r"(?:\[\s*([\w.$]+)(?:\s*=\s*[^\]]*)?\s*\]|([\w.$]+))"  # [name=default] or name
    r"\s*-?\s*(.*)$",
    re.DOTALL,
)
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)\n?```$", re.DOTALL)


def _clean_inline_links(text: str) -> str:
    """Reduce ``{@link X#y}`` / ``{@link X.y}`` / ``{@link y}`` inline
    references to just ``y`` (or ``X.y`` when no ``#`` member separator is
    present) inside free-text doc content.
    """
    def _sub(m: "re.Match[str]") -> str:
        ref = m.group(1).strip()
        # Drop an optional trailing pipe-delimited display text: {@link X|text}
        ref = ref.split("|", 1)[0].strip()
        if "#" in ref:
            _cls, _member = ref.split("#", 1)
            return _member.strip()
        return ref
    return _JSDOC_INLINE_LINK_RE.sub(_sub, text)


def _strip_comment_delims(raw: str) -> str:
    """Strip ``/**``/``*/`` delimiters and per-line leading ``*`` markers,
    returning the raw (not yet first-sentence-trimmed) body text.
    """
    body = raw.strip()
    if body.startswith("/**"):
        body = body[3:]
    elif body.startswith("/*"):
        body = body[2:]
    if body.endswith("*/"):
        body = body[:-2]
    body = _JSDOC_LINE_RE.sub("", body)
    return body


def parse_doc(raw: str) -> dict:
    """Parse a raw JSDoc/TSDoc comment block (``/** ... */``, full text
    including delimiters) into a dict.

    Always includes ``"summary"`` (backward-compatible with the pre-TC-12
    shallow implementation -- everything before the first ``@tag`` line,
    first-sentence-trimmed; ``{"summary": ""}`` for non-JSDoc input). Only
    ADDITIONALLY includes the following keys when the corresponding tag is
    actually present (so a plain summary-only comment still round-trips to
    exactly ``{"summary": ...}``, matching the pre-existing
    TestParseDoc.test_typescript_jsdoc regression test byte-for-byte):

      params: {name: desc}       -- from one or more @param lines
      returns: str                -- from @returns / @return
      remarks: str                 -- from @remarks
      examples: [str]              -- from one or more @example blocks
                                       (a fenced ```lang ... ``` wrapper, if
                                       present, is stripped)
      deprecated: True | str       -- from @deprecated (str when it carries
                                       free text, True when bare)

    @throws / @see / @since / @typeParam are recognized (so they don't leak
    into the summary) but intentionally ignored -- not surfaced as fields.
    ``{@link X#y}`` / ``{@link X.y}`` inline references are reduced to just
    ``y`` / ``X.y`` wherever they appear in retained free text.
    """
    if not raw.startswith("/**"):
        return {"summary": ""}

    body = _strip_comment_delims(raw)

    # Split into a leading (untagged) summary chunk + one chunk per @tag,
    # each chunk running up to (but not including) the next @tag line.
    tag_starts = list(_JSDOC_TAG_SPLIT_RE.finditer(body))
    if tag_starts:
        summary_raw = body[: tag_starts[0].start()]
    else:
        summary_raw = body

    result: dict = {"summary": _first_sentence(_clean_inline_links(summary_raw).strip())}

    params: dict = {}
    returns_parts: list[str] = []
    remarks_parts: list[str] = []
    examples: list[str] = []
    deprecated: "bool | str" = False

    for idx, m in enumerate(tag_starts):
        tag = m.group(1).lower()
        chunk_start = m.end()
        chunk_end = tag_starts[idx + 1].start() if idx + 1 < len(tag_starts) else len(body)
        chunk = body[chunk_start:chunk_end].strip()
        chunk = _clean_inline_links(chunk)

        if tag == "param":
            pm = _JSDOC_PARAM_RE.match(chunk)
            if pm:
                pname = (pm.group(1) or pm.group(2) or "").strip()
                pdesc = (pm.group(3) or "").strip()
                pdesc = " ".join(pdesc.split())
                if pname:
                    params[pname] = pdesc
        elif tag in ("returns", "return"):
            returns_parts.append(" ".join(chunk.split()))
        elif tag == "remarks":
            remarks_parts.append(" ".join(chunk.split()))
        elif tag == "example":
            fence_m = _FENCE_RE.match(chunk.strip())
            examples.append(fence_m.group(1) if fence_m else chunk.strip())
        elif tag == "deprecated":
            text = " ".join(chunk.split())
            deprecated = text if text else True
        # @throws / @see / @since / @typeParam / anything else: ignored.

    if params:
        result["params"] = params
    if returns_parts:
        result["returns"] = " ".join(returns_parts).strip()
    if remarks_parts:
        result["remarks"] = " ".join(remarks_parts).strip()
    if examples:
        result["examples"] = examples
    if deprecated:
        result["deprecated"] = deprecated

    return result


# ---------------------------------------------------------------------------
# export_surface (TC-MT040-11: real barrel/reachability implementation)
# ---------------------------------------------------------------------------

_JS_LIKE_EXT_RE = re.compile(r"\.(d\.ts|mjs|cjs|js|ts|tsx)$")
_DIST_PREFIXES = ("dist/", "lib/", "build/")

_EXPORT_STAR_AS_RE = re.compile(
    r"\bexport\s+\*\s+as\s+[A-Za-z_$][\w$]*\s+from\s+['\"]([^'\"]+)['\"]"
)
# Same pattern as _EXPORT_STAR_AS_RE, but with the namespace name itself
# captured too (TC-MT040-43 / D-4 strategy (b): export_groups() below needs
# both pieces; _EXPORT_STAR_AS_RE stays as-is since export_surface()'s
# reachability bail-check only ever needed the specifier).
_EXPORT_STAR_AS_NAMED_RE = re.compile(
    r"\bexport\s+\*\s+as\s+(?P<ns>[A-Za-z_$][\w$]*)\s+from\s+['\"](?P<spec>[^'\"]+)['\"]"
)
_EXPORT_EQUALS_RE = re.compile(r"\bexport\s*=(?!=)")
_EXPORT_STAR_FROM_RE = re.compile(r"\bexport\s+\*\s+from\s+['\"]([^'\"]+)['\"]")
_EXPORT_BRACE_RE = re.compile(
    r"export\s+(?:type\s+)?\{(?P<names>[^}]*)\}"
    r"(?:\s*from\s*['\"][^'\"]*['\"])?\s*;",
)
_EXPORT_DEFAULT_NAME_RE = re.compile(
    r"\bexport\s+default\s+(?P<name>[A-Za-z_$][\w$]*)\s*;"
)
_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:abstract\s+)?"
    r"(?:class|interface|enum|function|const|let|var|type|namespace)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)"
)

_MAX_TS_BARREL_DEPTH = 8


def _map_pkgjson_specifier(raw: str) -> str:
    """Apply the dist/lib/build -> src and .js/.mjs/.cjs/.d.ts -> .ts
    mapping to a package.json-declared entry-point path (relative to the
    package root).
    """
    val = raw.replace("\\", "/")
    if val.startswith("./"):
        val = val[2:]
    parts = val.split("/")
    if parts and parts[0] in ("dist", "lib", "build"):
        parts[0] = "src"
    val = "/".join(parts)
    if val.endswith(".d.ts"):
        val = val[: -len(".d.ts")] + ".ts"
    elif val.endswith((".mjs", ".cjs", ".js")):
        val = val.rsplit(".", 1)[0] + ".ts"
    return val


def _pkgjson_entry_candidates(data: dict) -> "list[str]":
    """Return raw (unmapped) candidate entry-point path strings from a
    parsed package.json dict, in resolution-priority order: exports["."]
    (string, or dict tried in import/default/types order), then main,
    module, types.
    """
    candidates: list[str] = []
    exports = data.get("exports")
    if isinstance(exports, dict):
        dot = exports.get(".")
        if isinstance(dot, str):
            candidates.append(dot)
        elif isinstance(dot, dict):
            for key in ("import", "default", "types"):
                val = dot.get(key)
                if isinstance(val, str):
                    candidates.append(val)
    for field in ("main", "module", "types"):
        val = data.get(field)
        if isinstance(val, str) and val:
            candidates.append(val)
    return candidates


@lru_cache(maxsize=64)
def resolve_entry_point(repo: Path, pkg_root: Path) -> "Path | None":
    """Resolve the package's real entry-point (barrel) source file.

    Tries, in order: package.json's ``exports["."]`` (string, or a dict
    tried via ``import``/``default``/``types`` keys), then ``main``,
    ``module``, ``types`` -- each mapped through the dist/lib/build -> src
    and .js/.mjs/.cjs/.d.ts -> .ts conventions and checked for existence on
    disk. Falls back to ``pkg_root/index.ts``, then ``repo/src/index.ts``,
    then ``repo/index.ts`` -- the first that exists. Returns None (unknown,
    fail-safe) when nothing resolves.

    Exposed as its own cached, public function (not folded into
    export_surface()'s return value) so a caller can learn what the barrel
    resolved to -- for scout_report.json's operator-visible
    ``ts_entry_point`` field -- even in the "entry found, but its exports
    could not be safely enumerated" case, where export_surface() itself must
    return (None, None) for the whole result (see its own docstring).
    """
    pkg_json = repo / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            for raw in _pkgjson_entry_candidates(data):
                mapped = _map_pkgjson_specifier(raw)
                if not mapped:
                    continue
                candidate = repo / mapped
                if candidate.is_file():
                    return candidate

    for candidate in (pkg_root / "index.ts", repo / "src" / "index.ts", repo / "index.ts"):
        if candidate.is_file():
            return candidate
    return None


def _is_relative_specifier(spec: str) -> bool:
    return spec.startswith("./") or spec.startswith("../")


def _resolve_relative_ts_file(base_dir: Path, spec: str) -> "Path | None":
    """Resolve a relative import/re-export specifier to a real file on disk.

    Tries ``<stem>.ts``, ``<stem>.tsx``, ``<stem>/index.ts``, ``<stem>.d.ts``
    relative to *base_dir* (the directory of the file containing the
    specifier), after stripping any existing JS-like extension from *spec*.
    Returns None if none of those exist.
    """
    stem = _JS_LIKE_EXT_RE.sub("", spec)
    for suffix in (".ts", ".tsx", "/index.ts", ".d.ts"):
        candidate = base_dir / (stem + suffix)
        if candidate.is_file():
            return candidate
    return None


def _strip_inline_type_modifier(item: str) -> str:
    """Strip a TS 5+ per-specifier inline `type` modifier, e.g.
    ``export { type Foo, Bar }`` -> item "type Foo" becomes "Foo"."""
    return re.sub(r"^type\s+", "", item.strip())


def _collect_file_exports(
    path: Path, visited: "set[Path]", depth: int,
) -> "tuple[set[str], bool] | None":
    """Return (names, found_any_export_statement) for *path*, recursing into
    any `export * from './relative'` targets. Returns None to signal an
    unrecoverable "cannot safely enumerate" condition that must propagate
    all the way up (namespace-qualified/CJS export, an unresolvable or
    non-relative `export *` target, an unreadable file, or exceeding the
    recursion depth guard).
    """
    if path in visited:
        # Already accounted for by an earlier visit in this same walk (a
        # re-export cycle) -- contributes nothing new, and is not itself a
        # "no exports found" condition.
        return set(), True
    if depth > _MAX_TS_BARREL_DEPTH:
        return None
    visited.add(path)

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Namespace-qualified (`export * as ns from ...`) and CommonJS-style
    # (`export = ...`) exports defeat this regex-only pass entirely --
    # bail globally rather than silently under-reporting.
    if _EXPORT_STAR_AS_RE.search(text) or _EXPORT_EQUALS_RE.search(text):
        return None

    names: set[str] = set()
    found_any = False

    for m in _EXPORT_BRACE_RE.finditer(text):
        found_any = True
        for item in m.group("names").split(","):
            item = _strip_inline_type_modifier(item)
            if not item:
                continue
            if " as " in item:
                orig, alias = item.split(" as ", 1)
                orig = orig.strip()
                alias = alias.strip()
                if orig:
                    names.add(orig)
                if alias:
                    names.add(alias)
            else:
                names.add(item)

    for m in _EXPORT_DEFAULT_NAME_RE.finditer(text):
        found_any = True
        names.add(m.group("name"))

    for m in _EXPORT_DECL_RE.finditer(text):
        found_any = True
        names.add(m.group("name"))

    for m in _EXPORT_STAR_FROM_RE.finditer(text):
        found_any = True
        spec = m.group(1)
        if not _is_relative_specifier(spec):
            return None  # bare package star-export -- cannot enumerate
        target = _resolve_relative_ts_file(path.parent, spec)
        if target is None:
            return None  # unresolvable target -- cannot enumerate
        sub = _collect_file_exports(target, visited, depth + 1)
        if sub is None:
            return None  # propagate the bail
        sub_names, _sub_found_any = sub
        names.update(sub_names)

    return names, found_any


@lru_cache(maxsize=64)
def _export_names_cached(repo: Path, pkg_root: Path) -> "frozenset | None":
    entry = resolve_entry_point(repo, pkg_root)
    if entry is None:
        return None
    result = _collect_file_exports(entry, set(), 0)
    if result is None:
        return None
    names, found_any = result
    if not found_any:
        # Entry file exists but has literally no export statement of any
        # recognized form (e.g. a demo/script entry point) -- "unknown",
        # not "confirmed nothing reachable". Real example:
        # aspose_cells_typescript's repo-root index.ts (package.json's own
        # declared `main`) only imports and runs code, exporting nothing.
        return None
    return frozenset(names)


def export_surface(repo: Path, pkg_root: Path) -> "tuple[set[str] | None, Path | None]":
    """Return (reachable_names, entry_path) -- see extraction/lang/__init__.py's
    module docstring for the shared contract.

    *entry_path* is the resolved barrel/entry-point file's own path (the
    TypeScript analog of python's export_root -- the concept of a namespace-
    parent directory doesn't apply to TS, so this is the single file the
    names are reachable relative to instead of a directory).

    Fail-safe by construction: both elements are None together whenever the
    entry point can't be found OR its exports can't be safely, fully
    enumerated (a non-relative/unresolvable `export *`, a namespace-qualified
    or CommonJS-style export, or an entry file with no recognized export
    statement at all) -- never a partial name set silently missing an
    unenumerable source. Callers wanting the resolved entry file's path even
    when enumeration itself failed (e.g. for an operator-visible
    scout_report.json field) should call resolve_entry_point() directly
    instead of relying on this function's second element in that case.
    """
    names = _export_names_cached(repo, pkg_root)
    if names is None:
        return None, None
    entry = resolve_entry_point(repo, pkg_root)
    return set(names), entry


# ---------------------------------------------------------------------------
# export_groups (TC-MT040-43 / D-4 strategy (b) -- OPTIONAL adapter
# capability, not part of the shared extraction/lang/ contract)
# ---------------------------------------------------------------------------

def export_groups(repo: Path, pkg_root: Path) -> "dict[str, str] | None":
    """Best-effort ``{exported_name: group_label}`` map for names the
    barrel/entry-point re-exports under a NAMED sub-namespace -- e.g.
    ``export * as Forms from './forms.js'`` groups every name that
    ``./forms.js`` itself exports under the label ``"Forms"``.

    This is an ADDITIVE, OPTIONAL capability layered on top of the shared
    export_surface() contract (see extraction/lang/__init__.py) -- it is
    NOT part of that contract's return shape (which stays the established
    ``(names, entry_path)`` 2-tuple three existing consumers already
    unpack), so it is exposed as its own function that a caller probes for
    with ``getattr(module, "export_groups", None)`` (only this module
    implements it today; commands/knowledge/promote.py's D-4 module-
    assignment strategy is the one caller).

    Returns ``None`` when the barrel can't be resolved, or contains no
    ``export * as Name from '...'`` groupings at all (the common case --
    most TS barrels re-export everything flat via ``export { ... } from``)
    -- callers must treat ``None`` as "no grouping information available",
    not "confirmed no groups", and fall back to another strategy. Unlike
    export_surface()'s all-or-nothing reachability contract, a single
    unresolvable/non-relative ``export * as`` target only drops THAT one
    grouping (skipped, not a global bail) -- this function is presentational
    best-effort, never a claim about reachability.
    """
    entry = resolve_entry_point(repo, pkg_root)
    if entry is None:
        return None
    try:
        text = entry.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    groups: dict[str, str] = {}
    for m in _EXPORT_STAR_AS_NAMED_RE.finditer(text):
        ns = m.group("ns")
        spec = m.group("spec")
        if not _is_relative_specifier(spec):
            continue  # bare/unresolvable package target -- skip this grouping only
        target = _resolve_relative_ts_file(entry.parent, spec)
        if target is None:
            continue
        sub = _collect_file_exports(target, set(), 0)
        if sub is None:
            continue
        sub_names, _found_any = sub
        for nm in sub_names:
            groups.setdefault(nm, ns)

    return groups if groups else None


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------

def clear_cache() -> None:
    """Reset the memoized entry-point resolution and export-enumeration
    scans (functools.lru_cache). Test/operator hook -- called by
    content_eval/evaluators/_reachability.py's own clear_caches() (via
    extraction/lang/__init__.py's clear_all_caches(), which iterates every
    language module) and directly by this module's own tests.
    """
    resolve_entry_point.cache_clear()
    _export_names_cached.cache_clear()
