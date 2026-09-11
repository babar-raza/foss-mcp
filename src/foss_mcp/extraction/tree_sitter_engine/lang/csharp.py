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

import re
from functools import lru_cache
from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine.tree_helpers import (
    _CLASS_TYPES,
    _FUNC_TYPES,
    _IMPORT_TYPES,
    _MODULE_TYPES,
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
    cleaned = " ".join(l.lstrip("/ ").strip() for l in lines)
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
