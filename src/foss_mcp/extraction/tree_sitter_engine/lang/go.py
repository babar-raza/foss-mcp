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

from foss_mcp.extraction.tree_sitter_engine.tree_helpers import _CLASS_TYPES, _FUNC_TYPES, _IMPORT_TYPES, _MODULE_TYPES

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

def export_surface(repo: Path, pkg_root: Path) -> "tuple[set[str] | None, Path | None]":
    """No reachability signal implemented for Go yet -- always (None, None)
    (fail-safe: caller must treat every item as reachable).
    """
    return None, None


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------

def clear_cache() -> None:
    """No-op -- this module holds no cached state."""
    return None
