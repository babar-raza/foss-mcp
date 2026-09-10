# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/lang/python.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""extraction/lang/python.py â€” Python language adapter (TC-MT040-42).

See extraction/lang/__init__.py for the shared adapter contract this module
implements: declaration_kinds(), doc_anchor(), parse_doc(), export_surface(),
clear_cache().

export_surface() carries the exact, behavior-preserving relocation of what
was api_surface.py's ``_python_top_level_exports`` (RC-W1-004) -- the one
hard rule for this card is that python's reachability output must not change
in any way a golden test would catch. See ``top_level_exports()``'s
docstring below for the full algorithm rationale (namespace-parent layout,
ambiguity handling, etc.) -- unchanged from the original.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine.tree_helpers import _CLASS_TYPES, _FUNC_TYPES, _IMPORT_TYPES, _MODULE_TYPES

_LANG = "python"


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

    Python's docstring lives INSIDE the declaration's own body (the first
    statement of the function/class block), not on a preceding sibling --
    no wrapper redirection is needed, so this is the identity function.
    """
    return node


# ---------------------------------------------------------------------------
# parse_doc
# ---------------------------------------------------------------------------

_STRING_PREFIX_RE = re.compile(r'^[a-zA-Z]{1,2}(?=["\'])')


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
    """Parse a raw Python docstring literal (including its quote/prefix
    characters, e.g. an ``r'''...'''`` raw triple-quoted literal) into
    ``{"summary": str}``.

    Thin, independently-tested reproduction of what
    api_surface._extract_doc_comment already does for python today: strip
    the string prefix (r/R/u/U, b/f combinations) and surrounding
    quote/whitespace characters, then take the first sentence. See
    extraction/lang/__init__.py's module docstring for why this is not yet
    wired into that function's hot path.
    """
    match = _STRING_PREFIX_RE.match(raw)
    text = raw[match.end():] if match else raw
    text = text.strip("\"' \n\r")
    return {"summary": _first_sentence(text)}


# ---------------------------------------------------------------------------
# export_surface
# ---------------------------------------------------------------------------

def _extract_all_from_init(init_path: Path) -> "set[str] | None":
    """Return the name set from a single __init__.py's top-level __all__.

    Returns None if the file can't be read or has no recognizable top-level
    ``__all__`` assignment. Does not handle ``__all__ +=``, conditional
    assignment, or programmatic construction (same documented limitation
    since this was api_surface.py's S-1 per-file __all__ scan).

    Moved verbatim from extraction/api_surface.py (TC-MT040-42).
    """
    try:
        text = init_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^__all__\s*=\s*[\[\(]([^\]\)]*)[\]\)]", text, re.MULTILINE | re.DOTALL)
    if not m:
        return None
    return set(re.findall(r'["\'](\w+)["\']', m.group(1)))


@lru_cache(maxsize=64)
def _top_level_exports_cached(pkg_root: Path) -> "tuple[frozenset | None, Path | None]":
    """Cached core of top_level_exports() -- see that function's docstring
    for the algorithm. Cached (functools.lru_cache) because
    content_eval/evaluators/_reachability.py's live_unreachable() calls this
    once per class-name check across a whole evaluator run and the original
    _reachability.py implementation this replaces
    (_python_top_level_all) was itself lru_cache-memoized for exactly that
    reason. api_surface.py's own precompute call site only ever calls this
    once per extraction run, so caching is a no-op cost there, not a
    behavior change -- confirmed no test in this repo mutates a pkg_root
    fixture in place and expects a second, uncached read (see
    test_api_surface_reachability.py, which uses a fresh tmp_path per test).
    Returns a frozenset (not a plain set) so the cached value itself can
    never be mutated by a caller.
    """
    top_init = pkg_root / "__init__.py"
    if top_init.is_file():
        names = _extract_all_from_init(top_init)
        if names is not None:
            return frozenset(names), pkg_root
    try:
        subdirs_with_all = []
        for child in sorted(pkg_root.iterdir()):
            if not child.is_dir():
                continue
            child_init = child / "__init__.py"
            if not child_init.is_file():
                continue
            names = _extract_all_from_init(child_init)
            if names is not None:
                subdirs_with_all.append((child, names))
        if len(subdirs_with_all) == 1:
            return frozenset(subdirs_with_all[0][1]), subdirs_with_all[0][0]
    except OSError:
        pass
    return None, None


def top_level_exports(pkg_root: Path) -> "tuple[set[str] | None, Path | None]":
    """Return (export_names, export_root) for the package's real public surface.

    Tries ``pkg_root/__init__.py`` first (the common case). Some Aspose FOSS
    Python packages use a namespace-parent layout instead -- ``pkg_root``
    resolves to an outer directory (e.g. ``aspose/``) whose own
    ``__init__.py`` is empty, with the real package one level down (e.g.
    ``aspose/words_foss/``, which declares the actual ``__all__``).
    Confirmed live: words/python's ``package_root.py`` resolves *pkg_root*
    to ``aspose/`` (empty ``__init__.py``), not ``aspose/words_foss/``.

    When *pkg_root*'s own ``__init__.py`` has no usable ``__all__``, looks
    for exactly one direct subdirectory whose ``__init__.py`` does -- an
    unambiguous single-subpackage case. Multiple or zero matches mean
    "cannot positively determine," returning ``(None, None)`` so the caller
    defaults every entry's ``reachable`` to True.

    The returned *export_root* is the directory those names are reachable
    relative to -- callers must only apply the check to classes whose file
    lives under it; classes elsewhere are out of scope for this signal.

    Moved verbatim from extraction/api_surface.py's
    ``_python_top_level_exports`` (TC-MT040-42) -- api_surface.py now
    re-exports this under its original private name as a thin wrapper.
    """
    names, root = _top_level_exports_cached(pkg_root)
    return (set(names) if names is not None else None), root


def export_surface(repo: Path, pkg_root: Path) -> "tuple[set[str] | None, Path | None]":
    """Return (reachable_names, scope_root) -- see extraction/lang/__init__.py's
    module docstring for the shared contract. *repo* is accepted for
    interface uniformity across all 7 language modules but unused here (the
    original python check never needed it -- ``pkg_root`` alone is
    sufficient). Delegates to top_level_exports(); see its docstring for the
    full algorithm.
    """
    return top_level_exports(pkg_root)


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------

def clear_cache() -> None:
    """Reset the memoized __all__ scan (functools.lru_cache). Test/operator
    hook -- called by content_eval/evaluators/_reachability.py's own
    clear_caches().
    """
    _top_level_exports_cached.cache_clear()
