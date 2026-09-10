# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/tree_helpers.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""tree_helpers.py â€” Tree-sitter parser loading and generic tree utilities.

Extracted from scout.py (step 4.1). Contains:
- Language constants (_CLASS_TYPES, _FUNC_TYPES, etc.)
- Parser loading (get_parser, _LANG_ALIASES)
- Generic tree helpers (collect_nodes, node_text, child_by_field, etc.)
- Visibility check (is_public)
- Source file collection (_collect_source_files)
- Base class / enum member extraction helpers

No Scout class dependencies â€” these are pure functions usable in isolation.
"""
from __future__ import annotations

import itertools
import logging
import os
import re
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

# HARDEN-words-net (2026-07-22): raised from 500 after words/net (Aspose.Words
# FOSS for .NET) exercised this cap for the first time ever â€” 3,167 source
# files under its package root vs. a 500-file ceiling nobody had revisited
# since. That silently dropped ~84% of the product's source files from
# extraction before scout_report.json's own files_attempted/skip-rate
# monitoring ever saw them (the report's counts are computed AFTER this cap
# already applied), so the existing skip-rate warnings never fired despite
# the API surface being severely incomplete. Every other product measured at
# the time of this fix was comfortably under 500 (pdf/net 385, slides/net
# 297, cells/net 219); 8000 gives ~2.5x headroom over words/net's own size.
# This remains a finite safety ceiling, not a "normal size" throttle â€” a
# repo that exceeds even this now logs a loud warning and records the true
# candidate count in scout_report.json (see _collect_source_files below),
# so a future product outgrowing it is visible immediately, not rediscovered
# by another full audit.
#
# SCOUT_MAX_FILES env var overrides this without a code edit + redeploy, for
# the next product that outgrows even 8000 â€” read once at import time, same
# as every other consumer of this constant.
_MAX_FILES_DEFAULT = 8000
MAX_FILES = int(os.environ.get("SCOUT_MAX_FILES", _MAX_FILES_DEFAULT))  # shared cap for _collect_source_files


# ---------------------------------------------------------------------------
# Language mapping constants
# ---------------------------------------------------------------------------

PLATFORM_TO_LANG: dict[str, str] = {
    "python": "python",
    "net": "csharp",
    "dotnet": "csharp",  # kept for backward compatibility
    "java": "java",
    "cpp": "cpp",
    "typescript": "typescript",
    "javascript": "javascript",
    "nodejs": "javascript",
    "go": "go",
    "rust": "rust",
}

_CLASS_TYPES: dict[str, set[str]] = {
    "java": {"class_declaration", "interface_declaration", "enum_declaration",
             "annotation_type_declaration"},
    "csharp": {"class_declaration", "interface_declaration", "enum_declaration",
               "struct_declaration", "record_declaration"},
    "javascript": {"class_declaration"},
    # TC-MT040-10: abstract_class_declaration is a DISTINCT tree-sitter node
    # type from class_declaration for `export abstract class X {}` (verified
    # via a live tree-sitter-typescript parse probe) -- without it here, every
    # abstract TS class was invisible to extraction entirely (not merely
    # missing its is_abstract flag). Confirmed live: 8 real abstract classes
    # under 3d/typescript's src/ (e.g. formats/Exporter.ts's `export abstract
    # class Exporter`), none previously extracted.
    "typescript": {"class_declaration", "interface_declaration",
                   "type_alias_declaration", "enum_declaration",
                   "abstract_class_declaration"},
    "cpp": {"class_specifier", "struct_specifier", "enum_specifier"},
    "python": {"class_definition"},
    "go": {"type_declaration", "type_spec"},
    "rust": {"struct_item", "enum_item", "trait_item", "union_item"},
}

_FUNC_TYPES: dict[str, set[str]] = {
    "java": {"method_declaration", "constructor_declaration"},
    "csharp": {"method_declaration", "constructor_declaration",
               "property_declaration"},
    "javascript": {"function_declaration", "method_definition", "arrow_function",
                   "public_field_definition"},
    # TC-MT040-10: abstract method signatures (`abstract load(p: string): void;`,
    # no body) inside an abstract_class_declaration body parse as their own
    # distinct `abstract_method_signature` node type, not `method_signature`
    # (that one is for interface/ambient bodies) -- verified via parse probe.
    "typescript": {"function_declaration", "method_definition", "arrow_function",
                   "public_field_definition", "property_signature", "method_signature",
                   "abstract_method_signature"},
    "go": {"function_declaration", "method_declaration"},
    "cpp": {"function_definition"},
    "python": {"function_definition"},
    # function_signature_item = body-less trait method declarations
    "rust": {"function_item", "function_signature_item"},
}

_PY_ENUM_BASES: frozenset[str] = frozenset({
    "Enum", "IntEnum", "StrEnum", "Flag", "IntFlag",
    "enum.Enum", "enum.IntEnum", "enum.StrEnum", "enum.Flag", "enum.IntFlag",
})

_IMPORT_TYPES: dict[str, set[str]] = {
    "java": {"import_declaration"},
    "csharp": {"using_directive"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "go": {"import_declaration"},
    "cpp": set(),
    "python": {"import_statement", "import_from_statement"},
    "rust": {"use_declaration"},
}

_MODULE_TYPES: dict[str, set[str]] = {
    "java": {"package_declaration"},
    "csharp": {"namespace_declaration", "file_scoped_namespace_declaration"},
    "javascript": set(),
    # TC-MT040-15: `namespace Foo {}` / `export namespace Foo {}` parse as
    # `internal_module`; the legacy `module Foo {}` keyword form (rare, but
    # real tree-sitter-typescript grammar output) parses as a separate
    # top-level `module` node type -- both verified via parse probe. Neither
    # was previously recognized as a module/namespace container at all.
    "typescript": {"internal_module", "module"},
    "go": {"package_clause"},
    "cpp": {"namespace_definition"},
    "python": set(),
    "rust": {"mod_item"},
}

_NOT_IMPL_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"raise\s+NotImplementedError"),
    "csharp": re.compile(r"throw\s+new\s+NotImplementedException\s*\("),
    "java": re.compile(r"throw\s+new\s+UnsupportedOperationException\s*\("),
    "cpp": re.compile(r'throw\s+std::runtime_error\s*\(\s*"not implemented"',
                      re.IGNORECASE),
    "javascript": re.compile(r'throw\s+new\s+Error\s*\(\s*"not implemented"',
                              re.IGNORECASE),
    "typescript": re.compile(r'throw\s+new\s+Error\s*\(\s*"not implemented"',
                              re.IGNORECASE),
    # SFX-5: Go uses errors.New/fmt.Errorf with "not implemented" strings, or panic.
    "go": re.compile(
        r'errors\.New\s*\(\s*"not implemented"\s*\)'
        r'|fmt\.Errorf\s*\(\s*"not implemented[^"]*"\s*\)'
        r'|panic\s*\(\s*"not implemented[^"]*"\s*\)',
        re.IGNORECASE,
    ),
    "rust": re.compile(
        r"unimplemented!\s*\(|todo!\s*\("
        r'|panic!\s*\(\s*"not implemented',
        re.IGNORECASE,
    ),
}

_FILE_EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "csharp": ".cs",
    "java": ".java",
    "cpp": ".cpp",
    "typescript": ".ts",
    "javascript": ".js",
    "go": ".go",
    "rust": ".rs",
}

_HEADER_EXTENSIONS: dict[str, str | list[str]] = {
    "cpp": [".h", ".hpp"],
}

_DOC_COMMENT_STYLES: dict[str, str] = {
    "java": "javadoc",
    "csharp": "xml_doc",
    "javascript": "jsdoc",
    "typescript": "jsdoc",
    "cpp": "javadoc",
    "python": "docstring",
    "go": "godoc",
    "rust": "rustdoc",
}


# ---------------------------------------------------------------------------
# Parser loading
# ---------------------------------------------------------------------------

_LANG_ALIASES: dict[str, str] = {"csharp": "_c_sharp_separate"}


def get_parser(language: str):
    """Return a tree-sitter Parser for *language*."""
    resolved = _LANG_ALIASES.get(language, language)
    if resolved == "_c_sharp_separate":
        import tree_sitter_c_sharp as tsc
        from tree_sitter import Language, Parser
        lang_obj = Language(tsc.language())
        parser = Parser(lang_obj)
        return parser
    else:
        from tree_sitter_language_pack import get_parser as _get
        return _get(resolved)


# ---------------------------------------------------------------------------
# Generic tree helpers
# ---------------------------------------------------------------------------

def collect_nodes(root, type_names: set[str]) -> list:
    """Iterative depth-first collection of nodes whose type is in *type_names*."""
    result: list = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in type_names:
            result.append(node)
        stack.extend(reversed(node.children))
    return result


def node_text(node) -> str:
    """Return the UTF-8 text of a tree-sitter node."""
    if node is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def child_by_field(node, name: str):
    """Return child_by_field_name, tolerating None."""
    try:
        return node.child_by_field_name(name)
    except Exception:
        return None


def find_child_by_type(node, type_name: str):
    """Return the first direct child with the given type."""
    for ch in node.children:
        if ch.type == type_name:
            return ch
    return None


def find_children_by_type(node, type_name: str) -> list:
    """Return all direct children with the given type."""
    return [ch for ch in node.children if ch.type == type_name]


def _node_name(node, language: str) -> str:
    """Extract the identifier name from a declaration node."""
    name_node = child_by_field(node, "name")
    if name_node:
        return node_text(name_node)
    # fallback: first identifier child
    for ch in node.children:
        if ch.type == "identifier" or ch.type == "type_identifier":
            return node_text(ch)
    return ""


# ---------------------------------------------------------------------------
# Visibility check
# ---------------------------------------------------------------------------

def is_public(node, language: str) -> bool:
    """Return True if *node* represents a public declaration."""
    name = _node_name(node, language)
    if not name:
        return False

    if language == "go":
        return name[0].isupper()

    if language == "rust":
        # Trait members (function_item / function_signature_item inside a
        # trait body) are implicitly public â€” the trait itself is gated
        # separately as its own class-type node. Trait-impl members
        # (`impl Trait for Type { fn ... }`) inherit the trait's visibility
        # and carry no `pub` of their own. Inherent-impl members and
        # module-level items require an explicit bare `pub` â€”
        # `pub(crate)` / `pub(super)` / `pub(in ...)` are crate-internal
        # and must NOT be extracted as public API.
        parent = node.parent
        while parent is not None:
            if parent.type == "trait_item":
                return True
            if parent.type == "impl_item":
                if child_by_field(parent, "trait") is not None:
                    return True
                break  # inherent impl â€” fall through to the pub check
            parent = parent.parent
        for ch in node.children:
            if ch.type == "visibility_modifier":
                return node_text(ch).strip() == "pub"
        return False

    if language == "python":
        if name == "__init__":
            return True
        return not name.startswith("_")

    if language in ("javascript", "typescript"):
        parent = node.parent
        # Class/interface members are public unless explicitly private/protected
        if parent and parent.type in ("class_body", "interface_body", "object_type"):
            for ch in node.children:
                if ch.type == "accessibility_modifier":
                    if node_text(ch) in ("private", "protected"):
                        return False
            if name.startswith("#"):
                return False
            # FIX-03: TypeScript convention uses _ prefix for private members.
            # Many TS libraries use this convention without explicit modifiers.
            if name.startswith("_"):
                return False
            return True
        if parent and parent.type == "export_statement":
            return True
        # top-level declarations in modules are considered public
        if parent and parent.type in ("program", "module"):
            return True
        return False

    if language in ("java", "csharp", "cpp"):
        # C++ top-level classes/structs are implicitly public API
        if language == "cpp" and node.type in ("class_specifier", "struct_specifier",
                                                "enum_specifier"):
            parent = node.parent
            if parent and parent.type in ("translation_unit", "declaration",
                                           "namespace_definition",
                                           "declaration_list"):
                return True
            if parent and parent.type == "declaration":
                gp = parent.parent
                if gp and gp.type in ("translation_unit", "namespace_definition",
                                       "declaration_list"):
                    return True
        # HARDEN-A11 (2026-07-22): accumulate EVERY modifier/modifiers sibling
        # before deciding, instead of returning on the first one found. Java's
        # grammar bundles all annotations/keywords into one "modifiers" node, so
        # short-circuiting on the first hit was harmless there â€” but C#'s
        # grammar emits each modifier keyword (public, static, virtual, ...) as
        # its OWN separate sibling node, in whatever order the source wrote
        # them. `static public void Foo()` has "static" as the first modifier
        # sibling; the old code returned False right there and silently
        # dropped every such member from the extracted API surface. This is
        # the same bug SHAPE as ST-014 (checking only the nearest sibling in a
        # sequence instead of the whole stack) â€” same fix, generalized.
        found_modifier_node = False
        modifier_texts: list[str] = []
        for ch in node.children:
            if ch.type == "modifiers" or ch.type == "modifier":
                found_modifier_node = True
                modifier_texts.append(node_text(ch))
            elif ch.type == "public":
                return True
        if found_modifier_node:
            return "public" in " ".join(modifier_texts)
        # C# / Java: interface members are implicitly public
        if node.parent and node.parent.type in ("interface_declaration",
                                                  "interface_body",
                                                  "declaration_list"):
            gp = node.parent
            if gp.type in ("declaration_list", "interface_body"):
                gp = gp.parent
            if gp and gp.type == "interface_declaration":
                return True
        # C++: check access specifier in class body
        if language == "cpp":
            prev = node.prev_named_sibling
            while prev:
                if prev.type == "access_specifier":
                    return "public" in node_text(prev)
                prev = prev.prev_named_sibling
        return False

    return True


_CSHARP_OBSOLETE_ATTR_NAMES = ("Obsolete", "ObsoleteAttribute")


def deprecated_marker(node, language: str) -> tuple[bool, str]:
    """Return (is_deprecated, reason) from a REAL code-level deprecation marker
    on *node*'s own declaration (mission-plan-the-end-to-end-vast-hejlsberg.md
    Task 15, Â§9.10, closes G9): Java's ``@Deprecated`` annotation, C#'s
    ``[Obsolete(...)]`` attribute.

    This is distinct from, and complementary to, a Javadoc ``@deprecated``
    comment TAG or an XML-doc ``/// <summary>`` mentioning deprecation --
    those live inside doc COMMENTS and are captured separately by
    scout_enrichers/_javadoc.py / _xml_doc.py (which also set/OR this same
    ``deprecated`` signal true when they find one, since either source is a
    real, valid deprecation signal in each language's own convention). *reason*
    is only ever populated here for C# (the attribute argument IS the reason
    string, e.g. ``[Obsolete("Use Bar instead")]``); Java's ``@Deprecated``
    annotation carries no free-text reason of its own -- Java's reason, when
    present, comes exclusively from the Javadoc ``@deprecated`` tag instead.
    """
    if language == "java":
        for ch in node.children:
            if ch.type in ("modifiers", "modifier"):
                if re.search(r"@Deprecated\b", node_text(ch)):
                    return True, ""
        return False, ""

    if language == "csharp":
        for ch in node.children:
            if ch.type != "attribute_list":
                continue
            for attr in find_children_by_type(ch, "attribute"):
                ident = find_child_by_type(attr, "identifier")
                if ident is None or node_text(ident) not in _CSHARP_OBSOLETE_ATTR_NAMES:
                    continue
                reason_match = re.search(r'"([^"]*)"', node_text(attr))
                return True, (reason_match.group(1) if reason_match else "")
        return False, ""

    return False, ""


def visibility_tier(node, language: str, *, has_docstring: bool = False,
                     in_module_all: bool = False) -> str:
    """Return visibility tier for a node that already passed is_public().

    Tiers (highest to lowest signal):
      "exported"     â€” Explicitly exported (__all__, export keyword, Go uppercase)
      "public"       â€” Explicitly public (modifier) or documented (has docstring)
      "conventional" â€” Public by naming convention only (no explicit marker)

    For Python, callers should pass *has_docstring* and *in_module_all* since
    those require context beyond the tree-sitter node itself.
    """
    if language == "python":
        if in_module_all:
            return "exported"
        if has_docstring:
            return "public"
        return "conventional"
    if language in ("java", "csharp", "cpp"):
        return "public"
    if language in ("javascript", "typescript"):
        parent = node.parent
        if parent and parent.type == "export_statement":
            return "exported"
        return "public"
    if language == "go":
        return "exported"
    if language == "rust":
        # `pub` is an explicit visibility marker (crate-external export).
        return "public"
    return "public"


# ---------------------------------------------------------------------------
# Source file collection
# ---------------------------------------------------------------------------

# Directories that should never contribute to the public API surface.
_NON_PUBLIC_DIRS: frozenset[str] = frozenset({
    "tests", "test", "spec", "specs",
    "examples", "example", "samples", "sample", "demo", "demos",
    "docs", "doc", "documentation",
    ".github", ".git",
    "benchmarks", "benchmark", "perf",
    "scripts", "tools", "build", "dist",
})


def _is_non_public_path(p: Path, root: Path) -> bool:
    """Return True if any path component relative to *root* is a non-public directory."""
    try:
        parts = p.relative_to(root).parts
    except ValueError:
        parts = p.parts
    return any(part.lower() in _NON_PUBLIC_DIRS for part in parts)


_FORMAT_STEM_RE = re.compile(
    r"(format|exporter|importer|loader|saver|reader|writer)", re.IGNORECASE
)


def _collect_source_files(
    root: Path, ext: str, header_ext: str | list[str] = "",
    *, stats: "dict[str, Any] | None" = None,
) -> list[Path]:
    """Glob for source files, capped at MAX_FILES, excluding non-public directories.

    Files are ordered by a three-tier priority key so critical extraction
    targets are never evicted by the MAX_FILES cap:
      0 â€” _internal/ implementation files (exporter/parser classes live here)
      1 â€” format-enum and I/O-class files (SaveFormat, SourceFormat, *Exporter â€¦)
      2 â€” all other public source files (alphabetical within tier)

    If the true candidate count exceeds MAX_FILES, a warning is logged (this
    used to be silent â€” a product bigger than the cap looked identical to one
    that fit comfortably inside it, until an actual audit found the gap; see
    the MAX_FILES history note above). Pass ``stats`` (a dict) to also receive
    the true pre-cap candidate count and whether capping occurred, for
    callers that persist it into a durable report (e.g. scout_report.json).
    """
    def _priority(p: Path) -> tuple:
        rel = p.relative_to(root).as_posix()
        if "/_internal/" in rel:
            return (0, rel)
        if _FORMAT_STEM_RE.search(p.stem):
            return (1, rel)
        return (2, rel)

    candidates = sorted(
        (f for f in root.rglob(f"*{ext}") if not _is_non_public_path(f, root)),
        key=_priority,
    )
    total_candidates = len(candidates)
    files = candidates[:MAX_FILES]
    if header_ext:
        exts = [header_ext] if isinstance(header_ext, str) else header_ext
        for he in exts:
            remaining = MAX_FILES - len(files)
            he_candidates = sorted(
                (f for f in root.rglob(f"*{he}") if not _is_non_public_path(f, root)),
                key=_priority,
            )
            total_candidates += len(he_candidates)
            if remaining > 0:
                files.extend(he_candidates[:remaining])

    files_capped = total_candidates > MAX_FILES
    if files_capped:
        LOG.warning(
            "MAX_FILES cap reached scanning %s: %d source files found, only %d will be "
            "processed (%d dropped) -- API surface will be INCOMPLETE for this product. "
            "Raise MAX_FILES in scripts/pipeline/extraction/tree_helpers.py if this repo's "
            "size is expected to persist.",
            root, total_candidates, MAX_FILES, total_candidates - MAX_FILES,
        )
    if stats is not None:
        stats["candidates_found"] = total_candidates
        stats["files_capped"] = files_capped
    return files


# ---------------------------------------------------------------------------
# Base class extraction
# ---------------------------------------------------------------------------

def _extract_bases(node, language: str) -> list[str]:
    """Extract base classes / interfaces from a class declaration."""
    bases: list[str] = []
    if language == "rust":
        # Rust has no inheritance. "Bases" are (a) traits from #[derive(...)]
        # attributes preceding the item and (b) supertraits on trait items
        # (`pub trait Foo: Bar + Baz`). Traits implemented via separate
        # `impl Trait for Type` blocks are added later by
        # api_surface._associate_rust_impl_methods.
        cur = node.prev_named_sibling
        while cur is not None and cur.type in ("attribute_item", "line_comment"):
            if cur.type == "attribute_item":
                m = re.search(r"derive\s*\(([^)]*)\)", node_text(cur))
                if m:
                    for part in m.group(1).split(","):
                        part = part.strip()
                        if part:
                            bases.append(part)
            cur = cur.prev_named_sibling
        if node.type == "trait_item":
            tb = find_child_by_type(node, "trait_bounds")
            if tb is not None:
                for ch in tb.children:
                    if ch.type in ("type_identifier", "generic_type",
                                    "scoped_type_identifier"):
                        bases.append(node_text(ch))
        return bases

    if language == "python":
        arg_list = find_child_by_type(node, "argument_list")
        if arg_list:
            for ch in arg_list.children:
                if ch.type in ("identifier", "attribute"):
                    bases.append(node_text(ch))
        return bases

    if language == "go":
        # Go has no classical inheritance; "bases" here covers two real
        # idioms the generic fallback loop below cannot see because it only
        # inspects a type_spec's direct children, never descending into a
        # struct_type/interface_type body:
        #   (a) struct embedding: an anonymous field (no "name" field on its
        #       field_declaration) whose "type" field names the embedded type,
        #       e.g. `type CaretAnnotation struct { drawingAnnotationBase }`.
        #   (b) interface embedding: a `type_elem` child of interface_type
        #       naming another embedded interface, e.g.
        #       `type ReadWriteCloser interface { Reader; Writer }`.
        # Verified against real tree-sitter-go output (method_elem/type_elem/
        # field_declaration field names: "name", "type", "result").
        #
        # The type-alias-to-underlying-primitive idiom (`type AnnotationType
        # int`) has no struct_type/interface_type "type" field at all -- it
        # falls through unchanged to the generic loop below, which already
        # handles it correctly (matches the bare type_identifier "int"; the
        # type's own name is filtered out later by api_surface.py's
        # self-referential-bases removal).
        type_node = child_by_field(node, "type")
        if type_node is not None and type_node.type == "struct_type":
            field_list = find_child_by_type(type_node, "field_declaration_list")
            if field_list is not None:
                for field_decl in field_list.children:
                    if field_decl.type != "field_declaration":
                        continue
                    if child_by_field(field_decl, "name") is not None:
                        continue  # named field, not embedded
                    embedded_type = child_by_field(field_decl, "type")
                    if embedded_type is not None:
                        bases.append(node_text(embedded_type))
            return bases
        if type_node is not None and type_node.type == "interface_type":
            for ch in type_node.children:
                if ch.type == "type_elem":
                    for sub in ch.children:
                        if sub.type in ("type_identifier", "qualified_type"):
                            bases.append(node_text(sub))
                            break
            return bases
        # Neither struct_type nor interface_type (e.g. the enum-alias idiom):
        # fall through to the generic loop below, unchanged.

    # Java/C#: superclass, interfaces, base_list
    for ch in node.children:
        if ch.type == "class_heritage":
            # TC-MT040-10 finding: TypeScript wraps a combined `extends X
            # implements Y, Z` as ONE class_heritage node containing
            # extends_clause + implements_clause as separate children
            # (verified via parse probe) -- node_text() on the whole
            # class_heritage node then returns both clauses concatenated
            # ("Base implements IExport"), which the single
            # leading-keyword-strip + comma-split below cannot separate
            # correctly (it produced ONE bogus base "Base implements
            # IExport" instead of the two correct ones, "Base" and
            # "IExport"). Not abstract-class-specific -- affects any TS
            # class_declaration with both clauses present too, same grammar
            # shape either way. Process each sub-clause independently
            # instead of the parent node as a whole.
            sub_chs = [c for c in ch.children
                       if c.type in ("extends_clause", "implements_clause")]
            if sub_chs:
                for sub in sub_chs:
                    sub_text = re.sub(r"^(extends|implements)\s*", "", node_text(sub))
                    for part in sub_text.split(","):
                        part = part.strip()
                        if part:
                            bases.append(part)
                continue
            # No recognized sub-clauses -- fall through to the generic
            # whole-node handling below (preserves prior behavior exactly
            # for any other class_heritage shape).
        if ch.type in ("superclass", "type_identifier", "base_list",
                        "super_interfaces", "extends_interfaces",
                        "class_heritage", "extends_clause",
                        "implements_clause"):
            text = node_text(ch)
            # strip keywords
            text = re.sub(r"^(extends|implements|:)\s*", "", text)
            # Conditionally-compiled base lists (C# `#if`/`#elif`/`#else`/`#endif`
            # around one or more interfaces, e.g. CompositeNode's real declaration:
            #   : Node, IEnumerable<Node>, INodeCollection
            #   #if !JAVA && !CPLUSPLUS  // XPath navigation is supported on ...
            #       , IXPathNavigable
            #   #endif
            # tree-sitter includes the preprocessor lines verbatim inside the
            # base_list node's text span, and a naive comma-split then treats the
            # directive line (plus its trailing `//` comment) as if it were itself
            # a base-class name. Strip preprocessor directive lines and any `//`
            # line comment before splitting, so only real identifiers remain.
            text = re.sub(r"^\s*#\s*(if|elif|else|endif)\b.*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"//[^\n]*", "", text)
            for part in text.split(","):
                part = part.strip()
                if part:
                    bases.append(part)
    return bases


# ---------------------------------------------------------------------------
# C# preprocessor-conditional exclusion (DEBUG/JAVA/CPLUSPLUS/etc. branches)
# ---------------------------------------------------------------------------

# Symbols that are unambiguously FALSE when extracting the real, published
# .NET (C#) Release build's API surface: transpilation-target flags (this
# source is shared across the Java/C++/Python ports of the same product) and
# build-configuration flags. Found 2026-07-23: the C# scout extractor
# collected `method_declaration`/`property_declaration` nodes via a plain
# recursive collect_nodes() with no awareness of `#if`/`#elif` ancestry, so
# a `#if DEBUG` -only helper (`dd()`) and a `#elif JAVA` -only `ToString()`
# override on Node.cs both leaked into api_surface.json and were rendered as
# real public .NET API on 48 published reference pages (confirmed via an
# independent adversarial review of the words/net launch).
_PREPROC_KNOWN_FALSE_SYMBOLS = frozenset({
    "DEBUG", "JAVA", "PLAIN_JAVA", "CPLUSPLUS", "PYNET", "TEST",
})

_PREPROC_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|&&|\|\||!|\(|\)")
_PREPROC_SAFE_RE = re.compile(r"^[A-Za-z0-9_\s!&|()]*$")

# Cap on distinct unrecognized symbols per condition before giving up (avoids
# a combinatorial blow-up on a pathological expression; none observed in
# practice exceed 3).
_PREPROC_MAX_UNKNOWNS = 8


def _condition_definitely_false(condition_text: str) -> bool:
    """True if a C# `#if`/`#elif` condition can be proven false for a real
    .NET Release build regardless of the value of any symbol NOT in
    _PREPROC_KNOWN_FALSE_SYMBOLS (target-framework symbols like NETSTANDARD,
    NET8_0_OR_GREATER, OPTIMIZED are deliberately left unresolved -- their
    real value depends on which of several supported target frameworks a
    consumer builds against, which this extractor has no principled way to
    pick, so a condition depending only on those is conservatively treated
    as reachable rather than guessed at).

    Evaluates the expression under every combination of True/False for the
    unrecognized symbols; only returns True if EVERY combination evaluates
    to False (i.e. no possible value of the unresolved symbols could make
    this branch reachable).
    """
    condition_text = condition_text.split("//", 1)[0].strip()
    if not condition_text:
        return False
    tokens = _PREPROC_TOKEN_RE.findall(condition_text)
    if not tokens:
        return False
    identifiers = {t for t in tokens if t not in ("&&", "||", "!", "(", ")")}
    unknowns = sorted(identifiers - _PREPROC_KNOWN_FALSE_SYMBOLS)
    if len(unknowns) > _PREPROC_MAX_UNKNOWNS:
        return False
    # Reject anything containing characters outside the expected grammar
    # before eval() -- defense in depth even though this only ever runs
    # against our own cloned source, never untrusted input.
    if not _PREPROC_SAFE_RE.match(condition_text):
        return False
    py_expr = condition_text.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    for combo in itertools.product((False, True), repeat=len(unknowns)):
        env = dict(zip(unknowns, combo))
        for sym in _PREPROC_KNOWN_FALSE_SYMBOLS:
            env[sym] = False
        try:
            if eval(py_expr, {"__builtins__": {}}, env):  # noqa: S307 - restricted env, own source only
                return False
        except Exception:
            return False
    return True


_PREPROC_BRANCH_TYPES = frozenset({"preproc_if", "preproc_elif"})


def _in_excluded_preproc_branch(node, language: str) -> bool:
    """True if `node` is nested (at any depth) inside a C# `#if`/`#elif`
    branch whose condition is definitely false for a real .NET Release build
    (see _condition_definitely_false). `#else` branches are never excluded
    here -- correctly resolving one requires negating every sibling
    `#if`/`#elif` condition in the chain, which this deliberately does not
    attempt (a bounded, conservative scope: this closes the confirmed
    DEBUG/JAVA/CPLUSPLUS/etc. leak without risking a false exclusion on the
    much harder `#else` case).

    Bug found 2026-07-23 (independent review, real source file
    OleFormat.cs): the ancestor walk did not stop at a `preproc_else`
    ancestor -- it continued past it to the outer `preproc_if`/`preproc_elif`
    and evaluated THAT condition, wrongly applying the IF-branch's exclusion
    verdict to content that is actually in the ELSE branch (where the
    condition is false, which is often exactly why that branch is the real
    .NET code -- e.g. `#if PYNET ... #else ... #endif`). This is doubly
    important because tree-sitter's C# grammar was also observed to produce
    an incorrect (too-wide) span for a `preproc_else` node in this same file
    -- `GetRawData()`, an unconditional method ~400 lines after the real
    `#endif`, was parsed as a descendant of the `preproc_else`/`preproc_if`
    pair. Stopping the walk the moment a `preproc_else` ancestor is seen
    (returning "not excluded" per this function's documented else-branch
    scope) fixes both problems at once: it stops applying the wrong
    condition, and it stops trusting a tree-sitter span that may not be
    trustworthy for `#else` in the first place.
    """
    if language != "csharp":
        return False
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "preproc_else":
            return False
        if ancestor.type in _PREPROC_BRANCH_TYPES:
            cond_node = ancestor.children[1] if len(ancestor.children) > 1 else None
            if cond_node is not None:
                condition_text = node_text(cond_node)
                if _condition_definitely_false(condition_text):
                    return True
        ancestor = ancestor.parent
    return False


# ---------------------------------------------------------------------------
# Interface-contract member synthesis (C#, TC-MT007-01 / backlog G-40)
# ---------------------------------------------------------------------------

# Standard BCL interfaces this synthesizes members for, and the members each
# one contributes. Deliberately a small, closed, hand-vetted set (not an
# open-ended interface-parsing engine) -- exactly the interfaces named in the
# G-40 finding, no more. `IEnumerator<T>`/`IEnumerable<T>` model .NET's real
# transitive contract: `IEnumerator<T> : IEnumerator, IDisposable`, so the
# generic form also contributes Dispose(); the non-generic forms do not.
_SYNTHESIZABLE_INTERFACE_MEMBERS: dict[str, list[dict[str, Any]]] = {
    "IDisposable": [
        {"kind": "method", "name": "Dispose", "return_type": "void"},
    ],
    "IEnumerator": [
        {"kind": "method", "name": "MoveNext", "return_type": "bool"},
        {"kind": "method", "name": "Reset", "return_type": "void"},
        {"kind": "property", "name": "Current", "type": "object"},
    ],
    "IEnumerator<T>": [
        {"kind": "method", "name": "MoveNext", "return_type": "bool"},
        {"kind": "method", "name": "Reset", "return_type": "void"},
        {"kind": "property", "name": "Current", "type": "T"},
        {"kind": "method", "name": "Dispose", "return_type": "void"},
    ],
    "IEnumerable": [
        {"kind": "method", "name": "GetEnumerator", "return_type": "IEnumerator"},
    ],
    "IEnumerable<T>": [
        {"kind": "method", "name": "GetEnumerator", "return_type": "IEnumerator<T>"},
    ],
    "IComparable": [
        {"kind": "method", "name": "CompareTo", "return_type": "int"},
    ],
    "IComparable<T>": [
        {"kind": "method", "name": "CompareTo", "return_type": "int"},
    ],
}

_GENERIC_BASE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*<(.+)>\s*$")


def _canonical_interface_key(base_name: str) -> "tuple[str, str | None] | None":
    """Map a raw base-list entry (e.g. 'IEnumerator<Node>', 'IDisposable') to
    (key, generic_arg) where key is an entry in _SYNTHESIZABLE_INTERFACE_MEMBERS
    and generic_arg is the real captured type argument ('Node') for generic
    bases or None for non-generic ones -- or None (the whole return value) if
    the base isn't one of the small set of interfaces this synthesizes members
    for. Exact-name matching only (plus stripping one level of generic
    argument) -- never substring or fuzzy matching, so an unrelated interface
    that merely contains one of these names as a substring (there are none
    known today, but a future one is plausible) can never be mis-synthesized
    against.
    """
    m = _GENERIC_BASE_RE.match(base_name.strip())
    if m:
        bare, arg = m.group(1), m.group(2).strip()
        generic_key = f"{bare}<T>"
        if generic_key in _SYNTHESIZABLE_INTERFACE_MEMBERS:
            return generic_key, arg
        return None
    bare = base_name.strip()
    if bare in _SYNTHESIZABLE_INTERFACE_MEMBERS:
        return bare, None
    return None


def synthesize_interface_members(
    bases: list[str], methods: list[dict], properties: list[dict], language: str
) -> tuple[list[dict], list[dict]]:
    """Return (new_methods, new_properties) to append to a C# class's already-
    extracted `methods`/`properties`, synthesizing standard interface-contract
    members for interfaces the class implements without an explicit same-file
    override.

    Found 2026-07-24 (backlog G-40, MT007-L2-02): the scout extractor captures
    a class's implemented-interface list (`bases`) but never synthesizes the
    interface's own contract members for classes that implement them without
    declaring an explicit override in the same file (common for simple
    iterator/wrapper classes, e.g. `NodeEnumerator : IEnumerator<Node>,
    IDocumentPositionListener` -- `Dispose()`/`MoveNext()`/`Reset()`/`Current`
    are all real, callable, spec-guaranteed members that were previously
    absent from api_surface.json entirely).

    Each synthesized entry is tagged `"origin": "interface_synthesized"` (only
    on the synthesized entries -- explicit entries are left with no `origin`
    key at all, backward-compatible with every existing consumer, rather than
    retrofitting an `origin` field onto every extraction call site repo-wide,
    which would be a far larger, riskier change than this finding warrants).
    Synthesized entries have no real declaration line (`"line": None`) and do
    NOT generate a claims.json entry -- a claim represents an extracted fact
    with real source evidence, and there is no source line to cite for a
    member that isn't written in this file.

    Only C# is in scope (`language == "csharp"`) -- this is where the gap was
    found and where `bases` reliably captures interface names; other
    languages' interface/protocol models differ enough that generalizing here
    would be guessing, not fixing a confirmed gap.
    """
    if language != "csharp":
        return [], []

    existing_method_names = {m["name"] for m in methods}
    existing_property_names = {p["name"] for p in properties}

    new_methods: list[dict] = []
    new_properties: list[dict] = []
    seen_this_class: set[str] = set()

    for base in bases:
        resolved = _canonical_interface_key(base)
        if resolved is None:
            continue
        key, generic_arg = resolved
        for member in _SYNTHESIZABLE_INTERFACE_MEMBERS[key]:
            name = member["name"]
            if name in seen_this_class:
                continue  # e.g. IEnumerator<T> and IEnumerator both listed -- don't duplicate
            if member["kind"] == "method":
                if name in existing_method_names:
                    continue  # explicit override present -- do not shadow it
                return_type = member["return_type"]
                if generic_arg and "T" in return_type:
                    return_type = return_type.replace("T", generic_arg)
                new_methods.append({
                    "name": name,
                    "params": [],
                    "return_type": return_type,
                    "doc": "",
                    "line": None,
                    "is_constructor": False,
                    "origin": "interface_synthesized",
                })
            else:
                if name in existing_property_names:
                    continue
                prop_type = member["type"]
                if generic_arg and prop_type == "T":
                    prop_type = generic_arg
                new_properties.append({
                    "name": name,
                    "type": prop_type,
                    "doc": "",
                    "line": None,
                    "writable": False,
                    "origin": "interface_synthesized",
                })
            seen_this_class.add(name)

    return new_methods, new_properties


# ---------------------------------------------------------------------------
# Enum member extraction
# ---------------------------------------------------------------------------

def _extract_enum_members(node, language: str) -> list[dict[str, str]]:
    """Extract enum constant names and values."""
    members: list[dict[str, str]] = []
    body = (find_child_by_type(node, "enum_body")
            or find_child_by_type(node, "enum_member_declaration_list")
            or find_child_by_type(node, "enum_declaration_list")
            or find_child_by_type(node, "enumerator_list")
            or find_child_by_type(node, "enum_variant_list")
            or find_child_by_type(node, "declaration_list")
            or find_child_by_type(node, "block"))
    if body is None:
        body = node

    for ch in body.children:
        if ch.type in ("enum_constant", "enum_member_declaration",
                        "enum_assignment", "enumerator", "property_identifier",
                        "enum_variant"):
            name = _node_name(ch, language) or node_text(ch).strip()
            val_node = child_by_field(ch, "value")
            val = node_text(val_node) if val_node else ""
            if name and name not in ("{", "}", ","):
                members.append({"name": name, "value": val})
    return members


def _extract_python_enum_members(node) -> list[dict[str, str]]:
    """Extract enum members from Python enum class (class-level assignments)."""
    members: list[dict[str, str]] = []
    body = find_child_by_type(node, "block")
    if not body:
        return members
    for ch in body.children:
        # Assignments may appear directly or wrapped in expression_statement
        if ch.type == "expression_statement":
            expr = ch.children[0] if ch.children else None
            if not expr or expr.type != "assignment":
                continue
        elif ch.type == "assignment":
            expr = ch
        else:
            continue
        lhs = child_by_field(expr, "left")
        rhs = child_by_field(expr, "right")
        if lhs and lhs.type == "identifier":
            name = node_text(lhs)
            if not name.startswith("_"):
                val = node_text(rhs) if rhs else ""
                members.append({"name": name, "value": val})
    return members
