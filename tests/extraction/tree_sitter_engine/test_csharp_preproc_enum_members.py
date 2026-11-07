"""WORDS-NET-006 (G3/TC-717): C# enum-member extraction preprocessor-awareness.

``tree_helpers._extract_enum_members`` used to iterate ONLY the direct children of an
enum's body (``for ch in body.children: if ch.type in ("enum_constant", ...)``), with no
handling at all for a `preproc_if`/`preproc_elif`/`preproc_else` wrapper node sitting at
that position in the tree. A preprocessor-guarded C# enum constant (e.g. the real
`Aspose.Words.SaveFormat.WebP`, wrapped in `#if NETSTANDARD || NET || NET462_OR_GREATER ||
JAVA ... #endif`) is a direct child of the enum body that is itself a `preproc_if` node,
not an `enum_member_declaration` directly -- so the old loop's type check never matched
it, and the constant (and everything else in that `#if` block) was silently dropped.

The fix makes `_extract_enum_members` preprocessor-aware for `language == "csharp"` ONLY:
it flattens the body's direct children through any `preproc_if`/`preproc_elif`/
`preproc_else` wrapper (the same one-level-at-a-time stack-based flatten
`lang.csharp.member_nodes` already applies to class/struct bodies), then drops any
flattened node whose enclosing `#if`/`#elif` condition is definitely false for a real
.NET Release build, via the new `_in_excluded_preproc_branch_bounded` -- scoped to the
enum's own body, not the existing unbounded `_in_excluded_preproc_branch` (see that new
function's own docstring in tree_helpers.py for the real malformed-parse case that
requires the bound).

Tests (a)-(d) below are exercised through the full `api_surface.extract_api_surface()`
pipeline on small, hand-authored, tree-sitter-parseable C#/Java snippets written to a
`tmp_path` package -- mirroring `test_tree_helpers.py`'s existing TypeScript/Java/C#
convention -- not the full real pilot source (that lives in
`tests/fixtures/words_net/api_surface.json` and is regenerated separately). The extra
bounded-walk regression test calls `_extract_enum_members` directly against a
hand-parsed tree instead, because the one real construct that exercises it
(`#if COND <modifier> #else <modifier> #endif` immediately before a declaration keyword)
is already rewritten away, textually, before parsing, by
`lang.csharp.resolve_conditional_modifiers` (TC-558/RD3-055) when going through the real
pipeline -- so a full-pipeline test of that exact source text would never reach this
code path at all.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.extraction.tree_sitter_engine.tree_helpers import _extract_enum_members

# (a) `#if NETSTANDARD ... #endif` guards the LAST member before the closing brace (no
# trailing comma inside the guarded block) -- exactly the real `SaveFormat.WebP` shape
# (`#if NETSTANDARD || NET || NET462_OR_GREATER || JAVA ... WebP = 107 #endif }`, reduced
# to one symbol). NETSTANDARD is a target-framework symbol, deliberately left unresolved
# by `_condition_definitely_false` (its real value depends on which supported TFM a
# consumer builds against), so the condition is NOT provably false and `Beta` must be
# extracted as reachable. (A guarded member FOLLOWED by a comma and more members, inside
# the same `#if` block, hits an unrelated tree-sitter-c-sharp grammar limitation --
# verified by a live parse probe -- that is not what this fix is about; the real upstream
# source never does that for `SaveFormat.WebP` either, since it is the enum's last
# member.)
CSHARP_REACHABLE_GUARD = """
namespace P
{
    public enum Format
    {
        Alpha = 0,
        Gamma = 2,
#if NETSTANDARD
        Beta = 1
#endif
    }
}
"""

# (b) `#if DEBUG` -- DEBUG is one of the small, closed set of symbols
# `_condition_definitely_false` treats as definitely false for a real .NET Release build
# (never true in a shipped release), so `Beta` must NOT be extracted.
CSHARP_DEFINITELY_FALSE_GUARD = """
namespace P
{
    public enum Format
    {
        Alpha = 0,
        Gamma = 2,
#if DEBUG
        Beta = 1
#endif
    }
}
"""

# (c) No preprocessor directives at all -- the common case. The flatten must be a true
# no-op: the extracted list must be identical to what the unmodified direct-children loop
# already produced for every C# enum before this fix.
CSHARP_NO_GUARD = """
namespace P
{
    public enum Format
    {
        Alpha = 0,
        Beta = 1,
        Gamma = 2
    }
}
"""

# (d) Java is untouched by the C#-only change. A Java enum with constructor arguments
# (TC-689's own regression shape) must keep extracting identically regardless of this
# card's C#-only flatten/filter addition.
JAVA_ENUM = """
package p;

public enum Color {
    RED(255, 0, 0),
    GREEN(0, 255, 0),
    BLUE(0, 0, 255);

    Color(int r, int g, int b) {}
}
"""


def _extract_csharp(source: str, tmp_path: Path) -> list[dict]:
    package = tmp_path / "src" / "P"
    package.mkdir(parents=True)
    (package / "Format.cs").write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("csharp"), "csharp", package, tmp_path, "widget")
    return types


def _extract_java(source: str, tmp_path: Path) -> list[dict]:
    package = tmp_path / "src" / "p"
    package.mkdir(parents=True)
    (package / "Color.java").write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("java"), "java", package, tmp_path, "widget")
    return types


def _member_names(types: list[dict], type_name: str) -> list[str]:
    by_name = {t["name"]: t for t in types}
    assert type_name in by_name, f"{type_name!r} not found among extracted types: {sorted(by_name)}"
    return [m["name"] for m in by_name[type_name]["enum_members"]]


def test_csharp_enum_member_guarded_by_reachable_condition_is_extracted(tmp_path: Path) -> None:
    """(a) `#if NETSTANDARD ... #endif` around `Beta`: NETSTANDARD is an unresolved
    target-framework symbol, not provably false, so `Beta` must now be extracted --
    exactly the real `SaveFormat.WebP` shape (`WORDS-NET-006`)."""
    types = _extract_csharp(CSHARP_REACHABLE_GUARD, tmp_path)
    assert set(_member_names(types, "Format")) == {"Alpha", "Beta", "Gamma"}


def test_csharp_enum_member_guarded_by_debug_is_not_extracted(tmp_path: Path) -> None:
    """(b) `#if DEBUG ... #endif` around `Beta`: DEBUG is definitely false for a real
    .NET Release build, so `Beta` must stay excluded."""
    types = _extract_csharp(CSHARP_DEFINITELY_FALSE_GUARD, tmp_path)
    assert set(_member_names(types, "Format")) == {"Alpha", "Gamma"}
    assert "Beta" not in _member_names(types, "Format")


def test_csharp_enum_with_no_preproc_directives_is_unchanged(tmp_path: Path) -> None:
    """(c) No `#if`/`#elif`/`#else` anywhere: the flatten must be a pure no-op, producing
    the exact same list the unmodified direct-children loop always produced."""
    types = _extract_csharp(CSHARP_NO_GUARD, tmp_path)
    assert _member_names(types, "Format") == ["Alpha", "Beta", "Gamma"]


def test_java_enum_with_constructor_arguments_is_unaffected(tmp_path: Path) -> None:
    """(d) Java enum extraction (including TC-689's constructor-argument value capture)
    must be completely unaffected by this C#-only change."""
    types = _extract_java(JAVA_ENUM, tmp_path)
    by_name = {t["name"]: t for t in types}
    assert "Color" in by_name
    members = by_name["Color"]["enum_members"]
    assert [m["name"] for m in members] == ["RED", "GREEN", "BLUE"]
    assert [m["value"] for m in members] == ["(255, 0, 0)", "(0, 255, 0)", "(0, 0, 255)"]


def test_csharp_enum_declaration_itself_inside_outer_preproc_is_not_excluded() -> None:
    """Regression found while implementing this fix, beyond the card's four required
    cases: an outer `#if`/`#else` guarding the enum's own access modifier -- not any of
    its members -- must never exclude members that carry no preprocessor directive of
    their own inside the body.

    Reproduces the real `Aspose.Words.DocumentPositionMovement` enum's shape directly at
    the tree-sitter level: tree-sitter-c-sharp cannot parse `#if CPLUSPLUS public #else
    internal #endif` guarding the modifier before `enum`, and recovers by nesting the
    ENTIRE `enum_declaration` inside one `preproc_if` node whose condition is the
    known-false symbol `CPLUSPLUS`. Calling the pre-existing *unbounded*
    `_in_excluded_preproc_branch` from any of this enum's members climbed straight past
    the enum's own body to that outer `preproc_if`, found `CPLUSPLUS` definitely false,
    and wrongly excluded every member (6 -> 0) -- confirmed by a full real-source scan of
    all 6 C# pilots' pinned commits during this card's own implementation.
    `_in_excluded_preproc_branch_bounded` fixes this by never walking past the enum's own
    body.

    Parses directly with `get_parser("csharp")` and calls `_extract_enum_members`
    directly, deliberately bypassing `api_surface.extract_api_surface()` (and so also
    `lang.csharp.resolve_conditional_modifiers`, TC-558/RD3-055): that function already
    rewrites exactly this `#if COND <modifier> #else <modifier> #endif`-before-a-keyword
    text pattern away, textually, before parsing -- which is also why the real
    `DocumentPositionMovement` is correctly absent from the committed `words_net` fixture
    today, unrelated to this card's fix. A full-pipeline test of this same source text
    would therefore never exercise `_in_excluded_preproc_branch_bounded` at all; this
    test exists specifically to cover the bounded-walk mechanism itself, independent of
    that other, unrelated pre-parse rewrite.
    """
    source = b"""
namespace P
{
#if CPLUSPLUS
    public
#else
    internal
#endif
    enum Format
    {
        Alpha,
        Beta,
        Gamma
    }
}
"""
    parser = get_parser("csharp")
    root = parser.parse(source).root_node

    def find_enum(node):
        if node.type == "enum_declaration":
            return node
        for child in node.children:
            found = find_enum(child)
            if found is not None:
                return found
        return None

    enum_node = find_enum(root)
    assert enum_node is not None, "test fixture source did not parse to an enum_declaration"
    members = _extract_enum_members(enum_node, "csharp")
    assert [m["name"] for m in members] == ["Alpha", "Beta", "Gamma"]
