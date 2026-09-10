# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/lang/__init__.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""extraction/lang/ â€” per-language extraction adapters (TC-MT040-42).

Root problem this package addresses: before this package existed,
``extraction/api_surface.py`` implemented per-language semantics
(export-surface/reachability detection, doc-comment extraction) as scattered
inline branches keyed on ``language == "python"`` / ``"rust"`` / etc.,
duplicated independently in
``content_eval/evaluators/_reachability.py`` (a SEPARATE reachability
implementation used by evaluators) and never consulted at all by
``lib/reference_filter.py`` (reference-page generation). A missing
per-language branch was indistinguishable from "this language genuinely has
no such concept" -- nothing declared what capabilities exist per language,
and there were 3 independent, unsynchronized implementations of similar
logic.

This package declares one module per language -- ``python``, ``rust``,
``csharp``, ``typescript``, ``java``, ``cpp``, ``go`` -- each exposing the
same small, consistent interface so callers can do
``lang.python.export_surface(repo, pkg_root)`` instead of branching on a
language string:

    declaration_kinds() -> dict
        Node-kind sets (class/func/module/import) for this one language --
        a per-language subset of tree_helpers' cross-language dicts.

    doc_anchor(node)
        Given a tree-sitter node for an exported declaration, return the
        node whose ``prev_named_sibling`` (or whatever comment-adjacency
        convention the language's grammar uses) actually holds that
        declaration's doc comment. Identity for most languages today; Go
        overrides it for ``type_spec`` nodes (the godoc comment precedes
        the parent ``type_declaration``, not the ``type_spec`` itself).
        This is the seam a future language (e.g. TypeScript, a LATER
        taskcard) can override when its grammar wraps exports in a
        container node.

    parse_doc(raw: str) -> dict
        Parse a raw doc-comment string per this language's doc style
        (``xml_doc`` / ``javadoc`` / ``jsdoc`` / ``docstring`` / ``rustdoc``
        / ``godoc`` -- see ``tree_helpers._DOC_COMMENT_STYLES``) into at
        least ``{"summary": str}``. THIS CARD (TC-MT040-42) ships these as
        faithful, independently-tested reproductions of what
        ``api_surface._extract_doc_comment`` already does for that language
        today (first-sentence extraction, style-specific cleanup) -- they
        are NOT yet wired into ``_extract_doc_comment``'s hot path (that
        function keeps its own proven inline implementation for now; see
        the TC-MT040-42 completion report for the risk rationale). A later
        card (TC-MT040-12) builds the full TSDoc block-tag parser on this
        seam and can migrate the hot path onto it once diffed against the
        goldens this card also ships.

    export_surface(repo: Path, pkg_root: Path) -> tuple[set[str] | None, Path | None]
        Returns (names, scope_root). *names* is the set of identifiers this
        adapter can positively account for; *scope_root* is the directory
        those names are meaningful relative to (or unused/None when the
        language has no such scoping concept). ``None`` for *names* means
        "cannot positively determine" -- fail-safe: callers must then treat
        every item as reachable, never manufacture a false positive.

        NOTE on the return shape: the taskcard that commissioned this
        package described this function as returning bare
        ``set[str] | None``. That is insufficient to preserve
        ``api_surface.py``'s existing Python reachability semantics
        byte-for-byte (the namespace-parent case needs the resolved export
        root, not just the name set -- see ``python.export_surface``'s
        docstring), so this package returns the richer
        ``(names, scope_root)`` tuple for every language instead, uniformly.

        Per-language behavior:
          python  -- real logic, moved verbatim from
                     api_surface._python_top_level_exports.
          rust    -- real logic, moved verbatim from
                     api_surface._rust_reexported_names.
          csharp  -- real logic, moved from
                     _reachability._csharp_internal_names. NOTE: polarity is
                     INVERTED relative to python/rust -- the returned set is
                     the set of names CONFIRMED `internal` (i.e.
                     UNREACHABLE), not a positive reachable-name allowlist.
                     C# has no positive export-list mechanism the way
                     Python's `__all__` or Rust's `pub use` do; the best
                     available live signal is a denylist. This is the first
                     time C# reachability becomes available to the scout
                     side (previously evaluator-only) -- see the
                     TC-MT040-42 report.
          typescript, java, cpp, go -- (None, None) unconditionally. No
                     reachability signal is implemented for these yet
                     (TypeScript's real implementation is TC-MT040-11, a
                     LATER taskcard).

    clear_cache() -> None
        Test/operator hook: reset any memoized (``functools.lru_cache``)
        state this module's ``export_surface`` holds. No-op for languages
        that don't cache. ``content_eval/evaluators/_reachability.py``'s
        own ``clear_caches()`` calls this on every language module.

    export_groups(repo: Path, pkg_root: Path) -> dict[str, str] | None  (OPTIONAL,
    TC-MT040-43 / D-4)
        NOT part of the required contract above -- an additive capability a
        language module MAY implement when its entry-point/barrel can name
        sub-namespace groupings (e.g. TypeScript's
        ``export * as Forms from './forms.js'``). Returns a best-effort
        ``{exported_name: group_label}`` map, or ``None`` when the adapter
        doesn't implement this, can't resolve the barrel, or found no named
        groupings. Callers probe for it with
        ``getattr(module, "export_groups", None)`` rather than assuming
        every language module defines it. Only ``typescript`` implements it
        today (see ``extraction/lang/typescript.py``); it backs
        ``commands/knowledge/promote.py``'s module/topic-assignment
        strategy (b) (module_key/module_label written onto api_surface.json
        entries at promote time -- see that file's "Module/topic
        assignment" section for the full ordered strategy).

Consumers (as of this card):
  - extraction/api_surface.py's ``_compute_reachable`` calls
    ``python.export_surface`` / ``rust.export_surface`` (python/rust only --
    unchanged dispatch scope; csharp/typescript/etc. are not wired into the
    scout-time reachability signal yet, see the completion report).
  - content_eval/evaluators/_reachability.py's ``live_unreachable`` calls
    ``python.export_surface`` / ``rust.export_surface`` /
    ``csharp.export_surface`` for its live clone-cache fallback.
  - lib/reference_filter.py declares (but, per this card, does not yet
    populate) ``REACHABILITY_ENFORCED_PLATFORMS`` as the future wiring
    point for reference-page generation (TC-MT040-17).
"""
from __future__ import annotations

from . import cpp, csharp, go, java, python, rust, typescript

LANGUAGES: dict = {
    "python": python,
    "rust": rust,
    "csharp": csharp,
    "typescript": typescript,
    "java": java,
    "cpp": cpp,
    "go": go,
}


def get(language: str):
    """Return the adapter module for *language*, or None if unrecognized."""
    return LANGUAGES.get(language)


def clear_all_caches() -> None:
    """Call ``clear_cache()`` on every language adapter module."""
    for mod in LANGUAGES.values():
        mod.clear_cache()
