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
# clear_cache
# ---------------------------------------------------------------------------


def clear_cache() -> None:
    """Reset the memoized `pub use` scan (functools.lru_cache). Test/operator
    hook -- called by content_eval/evaluators/_reachability.py's own
    clear_caches().
    """
    _reexported_names_cached.cache_clear()
