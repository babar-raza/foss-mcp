"""``_extract_bases()``'s recognized-child-types tuple (TC-316).

TC-306's worker, while trying to re-pin the cells/typescript fixture, found that
``tree_helpers.py``'s ``_extract_bases()`` recognizes TypeScript's class-level
``class_heritage``/``extends_clause``/``implements_clause`` nodes but NOT
``extends_type_clause`` -- the real tree-sitter-typescript node type for an
INTERFACE extending another interface. Confirmed via a live parse probe
(``tree_sitter_language_pack.get_parser("typescript")`` against
``export interface ChartInfo extends ShapeInfo {}``): this parses as
``interface_declaration -> type_identifier, extends_type_clause[extends,
type_identifier], interface_body``. Live-verified against the real, pinned
cells/typescript repository: ``aspose_cells/types.ts`` has 19 real
``interface X extends ShapeInfo { ... }`` declarations that extracted as
``bases: []`` for every one of them, with no trace anywhere in the fixture,
before this fix.

The tests below are all exercised through the full
``api_surface.extract_api_surface()`` pipeline, mirroring
``test_api_surface_other_languages.py``'s existing TypeScript/C++ convention
(synthetic source written to a ``tmp_path`` package, asserted against the
engine's raw ``bases`` list), since no unit-level test of ``_extract_bases()``
on a bare parsed node previously existed in this repo.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

# TC-316: the exact gap. ChartInfo's `extends_type_clause` child (text
# "extends ShapeInfo") was not in the recognized-child-types tuple at all, so
# the generic loop produced bases: [] with zero trace. MultiChild covers the
# comma-split case (`interface Foo extends A, B {}`) -- a single
# `extends_type_clause` child whose text is "extends A, B".
TYPESCRIPT_INTERFACE_EXTENDS = """
export interface ShapeInfo {
    name: string;
}

export interface ChartInfo extends ShapeInfo {
    title: string;
}

export interface MultiChild extends A, B {
    bar: number;
}

export interface Plain {
    baz: string;
}
"""

# Regression fixture: every OTHER node type already recognized by the tuple
# before this card, each exercised through a real TypeScript class so the
# fix's addition cannot be shown to have disturbed them. `Combo` reproduces
# the TC-MT040-10 combined `extends X implements Y, Z` shape (one
# `class_heritage` node wrapping both an `extends_clause` and an
# `implements_clause` sub-child); `JustExtends` and `JustImplements` isolate
# each clause alone.
TYPESCRIPT_CLASS_HERITAGE_REGRESSION = """
export class Combo extends Base implements IFoo, IBar {
    x: number;
}

export class JustExtends extends OnlyBase {
    y: number;
}

export class JustImplements implements OnlyIface {
    z: number;
}
"""

JAVA_REGRESSION = """
package p;

public class Widget extends BaseWidget implements Shape, Sized {
    public int size() { return 1; }
}
"""

CSHARP_REGRESSION = """
namespace P
{
    public class Widget : BaseWidget, IShape, ISized
    {
        public int Size() { return 1; }
    }
}
"""


def _ts_package(root: Path, source: str) -> Path:
    (root / "package.json").write_text('{"name": "widget"}\n', encoding="utf-8")
    package = root / "src"
    package.mkdir()
    (package / "shapes.ts").write_text(source, encoding="utf-8")
    return package


def _extract_ts(package: Path, root: Path) -> list[dict]:
    types, *_ = api_surface.extract_api_surface(get_parser("typescript"), "typescript", package, root, "widget")
    return types


def test_typescript_interface_extends_interface_populates_bases(tmp_path: Path) -> None:
    """The exact TC-316 bug: a single `interface X extends Y` relationship."""
    package = _ts_package(tmp_path, TYPESCRIPT_INTERFACE_EXTENDS)
    types = _extract_ts(package, tmp_path)
    by_name = {t["name"]: t for t in types}

    assert by_name["ChartInfo"]["kind"] == "interface_declaration"
    assert by_name["ChartInfo"]["bases"] == ["ShapeInfo"]


def test_typescript_interface_extends_multiple_interfaces_splits_bases(tmp_path: Path) -> None:
    """`interface Foo extends A, B {}` must split into two separate bases."""
    package = _ts_package(tmp_path, TYPESCRIPT_INTERFACE_EXTENDS)
    types = _extract_ts(package, tmp_path)
    by_name = {t["name"]: t for t in types}

    assert by_name["MultiChild"]["bases"] == ["A", "B"]


def test_typescript_interface_with_no_extends_has_empty_bases(tmp_path: Path) -> None:
    """A plain interface with no extends clause must not fabricate a base."""
    package = _ts_package(tmp_path, TYPESCRIPT_INTERFACE_EXTENDS)
    types = _extract_ts(package, tmp_path)
    by_name = {t["name"]: t for t in types}

    assert by_name["Plain"]["bases"] == []


def test_typescript_class_heritage_combined_extends_implements_is_unchanged(tmp_path: Path) -> None:
    """TC-MT040-10's combined `extends X implements Y, Z` shape (class_heritage
    wrapping extends_clause + implements_clause) must still split correctly
    into three bases, unaffected by the new extends_type_clause entry."""
    package = _ts_package(tmp_path, TYPESCRIPT_CLASS_HERITAGE_REGRESSION)
    types = _extract_ts(package, tmp_path)
    by_name = {t["name"]: t for t in types}

    assert by_name["Combo"]["bases"] == ["Base", "IFoo", "IBar"]


def test_typescript_class_with_only_extends_clause_is_unchanged(tmp_path: Path) -> None:
    package = _ts_package(tmp_path, TYPESCRIPT_CLASS_HERITAGE_REGRESSION)
    types = _extract_ts(package, tmp_path)
    by_name = {t["name"]: t for t in types}

    assert by_name["JustExtends"]["bases"] == ["OnlyBase"]


def test_typescript_class_with_only_implements_clause_is_unchanged(tmp_path: Path) -> None:
    package = _ts_package(tmp_path, TYPESCRIPT_CLASS_HERITAGE_REGRESSION)
    types = _extract_ts(package, tmp_path)
    by_name = {t["name"]: t for t in types}

    assert by_name["JustImplements"]["bases"] == ["OnlyIface"]


def test_java_superclass_and_super_interfaces_are_unchanged(tmp_path: Path) -> None:
    """Java's `superclass`/interfaces node types, also members of the same
    recognized-child-types tuple, must be unaffected by a TypeScript-only
    addition."""
    package = tmp_path / "src" / "p"
    package.mkdir(parents=True)
    (package / "Widget.java").write_text(JAVA_REGRESSION, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("java"), "java", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    assert by_name["Widget"]["bases"] == ["BaseWidget", "Shape", "Sized"]


def test_csharp_base_list_is_unchanged(tmp_path: Path) -> None:
    """C#'s `base_list` node type, also a member of the same recognized-
    child-types tuple, must be unaffected by a TypeScript-only addition."""
    package = tmp_path / "src" / "P"
    package.mkdir(parents=True)
    (package / "Widget.cs").write_text(CSHARP_REGRESSION, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("csharp"), "csharp", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    assert by_name["Widget"]["bases"] == ["BaseWidget", "IShape", "ISized"]
