"""run_extraction.py's python dispatch: routed through the independent pure-``ast`` reader
(python_surface.inspect_public_surface), never through tree-sitter/get_parser, with its output
adapted into the same dict shape extract_api_surface produces.

Offline only (network: false, per TC-021's card): every test builds a small synthetic python
package on disk and calls extract_from_clone / extract_pinned_repository directly - the latter's
clone_shallow is monkeypatched to a local copy so no clone ever happens.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine.python_surface import PublicSurface, PublicSymbol

PACKAGE_INIT = '''"""A small product package."""
from .core import Gadget, make_widget
from .missing import Nothing

__all__ = ["Gadget", "make_widget", "Nothing"]
'''

PACKAGE_CORE = '''"""Core widget types."""


class Gadget:
    """The root gadget."""

    def build(self):
        """Assemble the gadget."""
        return None

    def _internal(self):
        return None


class Color(Enum):
    """A named color."""

    RED = 1
    BLUE = 2


def make_widget(name):
    """Build a widget by name."""
    return name


def _hidden_helper():
    return None
'''
# Color's `Enum` base is never imported here - the reader only ever parses this file with
# ``ast``, it never imports or executes it, so the base-class name is read as literal syntax.


def _write_package(root: Path) -> None:
    """A src-layout python package: a marker file, module-level classes/functions with
    docstrings, an ``__all__``, a private name, a re-export, and one unresolvable re-export.
    """
    (root / "pyproject.toml").write_text('[project]\nname = "widgets"\n', encoding="utf-8")
    pkg = root / "src" / "widgets"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(PACKAGE_INIT, encoding="utf-8")
    (pkg / "core.py").write_text(PACKAGE_CORE, encoding="utf-8")


def _manifest(**overrides: Any) -> dict[str, Any]:
    manifest = {"platform": "python", "family": "widgets", "repository": "acme/widgets"}
    manifest.update(overrides)
    return manifest


def test_python_manifest_extracts_without_keyerror(tmp_path: Path) -> None:
    """The bug this card fixes: platform='python' used to raise a bare KeyError."""
    _write_package(tmp_path)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    assert types, "the production reader found nothing; the fixture package is wrong"


def test_output_entries_match_extract_api_surface_dict_shape(tmp_path: Path) -> None:
    _write_package(tmp_path)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    required = {
        "name",
        "kind",
        "file",
        "line",
        "doc",
        "visibility",
        "deprecated",
        "deprecated_reason",
        "bases",
        "class_import",
        "canonical_namespace",
        "methods",
        "properties",
    }
    for entry in types:
        assert required <= entry.keys(), entry
        if entry["kind"] == "enum":
            assert "enum_members" in entry


def test_class_kind_doc_and_line_are_correct(tmp_path: Path) -> None:
    _write_package(tmp_path)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    by_import = {entry["class_import"]: entry for entry in types}

    gadget = by_import["widgets.core.Gadget"]
    assert gadget["kind"] == "class"
    assert gadget["doc"] == "The root gadget."
    assert gadget["line"] == 4  # `class Gadget:` is the 4th line of PACKAGE_CORE
    assert gadget["visibility"] == "public"
    method_names = {m["name"] for m in gadget["methods"]}
    assert method_names == {"build"}  # `_internal` never surfaces
    build = next(m for m in gadget["methods"] if m["name"] == "build")
    assert build["doc"] == "Assemble the gadget."

    color = by_import["widgets.core.Color"]
    assert color["kind"] == "enum"
    assert color["enum_members"] == []

    widget_fn = by_import["widgets.core.make_widget"]
    assert widget_fn["kind"] == "function"
    assert widget_fn["doc"] == "Build a widget by name."


def test_reexport_resolves_to_the_same_class_under_the_package_name(tmp_path: Path) -> None:
    _write_package(tmp_path)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    by_import = {entry["class_import"]: entry for entry in types}

    # `widgets.Gadget` (the public re-export __init__.py exposes) resolves to the same class
    # kind and docstring as the module where it is actually defined.
    reexported = by_import["widgets.Gadget"]
    assert reexported["kind"] == "class"
    assert reexported["doc"] == "The root gadget."
    assert reexported["canonical_namespace"] == "widgets"


def test_unresolved_reexport_surfaces_rather_than_being_dropped(tmp_path: Path) -> None:
    _write_package(tmp_path)
    types, unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())

    # `Nothing` is re-exported from a module (`widgets.missing`) that does not exist on disk -
    # it must never silently vanish, and it must never be fabricated as a real class entry.
    names = {entry["class_import"] for entry in types}
    assert "widgets.Nothing" not in names
    assert any("unresolved-reexport" in item and "Nothing" in item for item in unresolved)


def test_never_calls_get_parser_for_a_python_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_package(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("get_parser must never be called for a python manifest")

    monkeypatch.setattr(run_extraction, "get_parser", _boom)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    assert types


def test_never_dispatches_python_through_language_by_platform(tmp_path: Path) -> None:
    """'python' is not, and must never become, a key of _LANGUAGE_BY_PLATFORM - that table is
    the tree-sitter-language-pack spelling table, and python never reaches it."""
    assert "python" not in run_extraction._LANGUAGE_BY_PLATFORM


def test_extract_pinned_repository_records_language_python_with_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_src = tmp_path / "src_repo"
    package_src.mkdir()
    _write_package(package_src)
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "platform: python\nfamily: widgets\nrepository: acme/widgets\n", encoding="utf-8"
    )

    def _fake_clone(_repository: str, dest: Path) -> str:
        # extract_pinned_repository's workdir already exists (tempfile.mkdtemp) - copy into it.
        shutil.copytree(package_src, dest, dirs_exist_ok=True)
        return "a" * 40

    monkeypatch.setattr(run_extraction, "clone_shallow", _fake_clone)
    artifact = run_extraction.extract_pinned_repository(manifest_path)

    assert artifact["language"] == "python"
    assert artifact["source_commit"] == "a" * 40
    assert artifact["type_count"] == len(artifact["types"])
    assert any(entry["class_import"] == "widgets.core.Gadget" for entry in artifact["types"])
    assert "unresolved" in artifact and artifact["unresolved"]


PACKAGE_INHERITANCE = '''"""Package with a real base/subclass relationship, for TC-281's inheritance-
flattening fix: api_surface._flatten_inheritance() is now reached on the Python path too.
"""


class Base:
    """The base class."""

    def inherited_method(self):
        """A method Child inherits unchanged - must gain inherited_from pointing to Base."""
        return None

    def overridden_method(self):
        """Base's own version - Child declares its own, which must win and stay untagged."""
        return "base"


class Child(Base):
    """Subclass: one own-only method, plus an override of a method Base also declares."""

    def own_method(self):
        """Declared only here - must never gain an inherited_from tag."""
        return None

    def overridden_method(self):
        """Child's own override, same name as Base's - must stay Child's own, untagged."""
        return "child"
'''


def _write_inheritance_package(root: Path) -> None:
    """A src-layout python package mirroring ``_write_package``'s own convention, carrying a
    real local-base ``Child(Base)`` relationship instead of ``_write_package``'s unrelated
    classes.
    """
    (root / "pyproject.toml").write_text('[project]\nname = "widgets"\n', encoding="utf-8")
    pkg = root / "src" / "widgets"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""A small product package."""\n', encoding="utf-8")
    (pkg / "core.py").write_text(PACKAGE_INHERITANCE, encoding="utf-8")


def test_extract_python_surface_flattens_inherited_members_from_a_real_base_class(
    tmp_path: Path,
) -> None:
    """TC-281: api_surface._flatten_inheritance() is fully language-agnostic (it operates
    purely on class_import/name/bases/methods/properties dict keys) but never ran for any
    Python-sourced pilot before this fix, because run_extraction.py routed Python entirely
    through python_surface.py, never through api_surface.extract_api_surface() - the only
    other place _flatten_inheritance() was invoked. After the fix, a subclass's own methods
    list must genuinely include its real base class's methods, each tagged with the correct
    inherited_from pointing at the real declaring ancestor's class_import.
    """
    _write_inheritance_package(tmp_path)
    types, _unresolved = run_extraction._extract_python_surface(tmp_path)
    by_name = {entry["name"]: entry for entry in types}

    base = by_name["Base"]
    child = by_name["Child"]
    assert child["bases"] == ["Base"]

    method_names = {m["name"] for m in child["methods"]}
    assert "inherited_method" in method_names, "Base's method must now be copied onto Child"

    inherited = next(m for m in child["methods"] if m["name"] == "inherited_method")
    assert inherited["inherited_from"] == base["class_import"]
    # The base's own entry for the same method must never itself carry an inherited_from -
    # it is genuinely declared there, not copied from anywhere.
    base_inherited_method = next(m for m in base["methods"] if m["name"] == "inherited_method")
    assert "inherited_from" not in base_inherited_method


def test_extract_python_surface_never_tags_a_genuinely_own_declared_method(
    tmp_path: Path,
) -> None:
    """Regression (mirrors TC-261's own do-not-break convention from
    test_flatten_inheritance_provenance.py, applied here for the Python path for the first
    time): a method the subclass declares itself - even one that shares a name with a base
    method it overrides - must never gain an inherited_from key at all.
    """
    _write_inheritance_package(tmp_path)
    types, _unresolved = run_extraction._extract_python_surface(tmp_path)
    by_name = {entry["name"]: entry for entry in types}
    child = by_name["Child"]

    own_method = next(m for m in child["methods"] if m["name"] == "own_method")
    assert "inherited_from" not in own_method

    # Child's own override shares a name with Base's method - the child's own declaration
    # must win (not be duplicated or replaced by the base's copy) and must stay untagged.
    overridden = [m for m in child["methods"] if m["name"] == "overridden_method"]
    assert len(overridden) == 1, "the override must not be duplicated by the flattening pass"
    assert "inherited_from" not in overridden[0]
    assert overridden[0]["doc"].startswith("Child's own override")


def _class_symbol(qualified_name: str, *, bases: tuple[str, ...] = ()) -> PublicSymbol:
    module, name = qualified_name.rsplit(".", 1)
    return PublicSymbol(qualified_name, module, name, "class", "pkg/mod.py", 1, "name", bases=bases)


def _method_symbol(
    qualified_name: str,
    *,
    return_type: str | None = None,
    param_types: tuple[tuple[str, str], ...] = (),
) -> PublicSymbol:
    module = qualified_name.rsplit(".", 2)[0]
    name = qualified_name.rsplit(".", 1)[-1]
    return PublicSymbol(
        qualified_name,
        module,
        name,
        "method",
        "pkg/mod.py",
        2,
        "name",
        return_type=return_type,
        param_types=param_types,
    )


def test_python_types_from_surface_makes_centrality_ranking_non_degenerate() -> None:
    """TC-265's whole point: a Python-sourced base-class/return-type reference must make the
    referenced class score higher under ``_centrality_scores()`` than a type nothing else ever
    mentions - the measurement TC-252 relies on, previously always zero for every Python pilot
    because ``PublicSymbol`` discarded this data entirely.
    """
    surface = PublicSurface(
        symbols=(
            _class_symbol("pkg.mod.Base"),
            _class_symbol("pkg.mod.Shape", bases=("Base",)),
            _method_symbol("pkg.mod.Shape.touch", return_type="Base"),
            _class_symbol("pkg.mod.Unrelated"),
        ),
        unresolved=(),
    )
    types = run_extraction._python_types_from_surface(surface)
    scores = run_extraction._centrality_scores(types)
    by_name = dict(zip((entry["name"] for entry in types), scores, strict=True))

    assert by_name["Base"] > by_name["Unrelated"]
    assert by_name["Unrelated"] == 0
    shape = next(entry for entry in types if entry["name"] == "Shape")
    assert shape["bases"] == ["Base"]
    touch = next(method for method in shape["methods"] if method["name"] == "touch")
    assert touch["return_type"] == "Base"


def test_python_types_from_surface_never_raises_or_pollutes_for_zero_annotations() -> None:
    """A class/method with no base classes and no annotations anywhere must still extract
    cleanly and must never contribute a false reference to any other type's score."""
    surface = PublicSurface(
        symbols=(
            _class_symbol("pkg.mod.Plain"),
            _method_symbol("pkg.mod.Plain.noop"),
            _class_symbol("pkg.mod.Other"),
        ),
        unresolved=(),
    )
    types = run_extraction._python_types_from_surface(surface)
    scores = run_extraction._centrality_scores(types)

    assert scores == [0, 0]
    plain = next(entry for entry in types if entry["name"] == "Plain")
    assert plain["bases"] == []
    noop = next(method for method in plain["methods"] if method["name"] == "noop")
    assert noop["return_type"] == ""
    assert noop["params"] == []


def _synthetic_type(
    name: str,
    *,
    bases: list[str] | None = None,
    return_type: str = "",
    param_type: str = "",
    property_type: str = "",
) -> dict[str, Any]:
    """A minimal plain-dict type entry carrying only the fields reduce_fixture's centrality
    score reads (bases, methods[].return_type, methods[].params[].type, properties[].type) -
    not a real clone, matching the taskcard's own synthetic-artifact instruction.
    """
    return {
        "name": name,
        "class_import": name,
        "bases": list(bases or []),
        "methods": [
            {
                "name": "m",
                "return_type": return_type,
                "params": [{"name": "p", "type": param_type}] if param_type else [],
            }
        ]
        if return_type or param_type
        else [],
        "properties": [{"name": "prop", "type": property_type}] if property_type else [],
    }


PACKAGE_UNDERSCORE_ORIGIN_INIT = '"""A package using the real-impl-in-underscore-module,\npublic-package-re-export convention."""\nfrom ._base import Base\n'

PACKAGE_UNDERSCORE_ORIGIN_BASE = '''class Base:
    """The base class, defined only behind a leading-underscore impl module."""

    def greet(self):
        """A method only recoverable through TC-294's underscore-origin method fix."""
        return "hi"
'''

PACKAGE_UNDERSCORE_ORIGIN_CHILD = '''class Child(Base):
    """Subclass of the underscore-origin base, defined in a normally-scanned module."""

    def own_method(self):
        """Declared only here - must never gain an inherited_from tag."""
        return None
'''


def _write_underscore_origin_inheritance_package(root: Path) -> None:
    """TC-294: mirrors ``_write_inheritance_package``'s own convention, except ``Base`` lives
    behind a leading-underscore impl module (``_base.py``) and is only reachable through
    ``__init__.py``'s re-export - the convention ``_origin_definition()`` (TC-274) recovers
    bases/return_type/param_types/docstring/signature for, but which never recovered the
    origin's own methods before this card.
    """
    (root / "pyproject.toml").write_text('[project]\nname = "widgets"\n', encoding="utf-8")
    pkg = root / "src" / "widgets"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(PACKAGE_UNDERSCORE_ORIGIN_INIT, encoding="utf-8")
    (pkg / "_base.py").write_text(PACKAGE_UNDERSCORE_ORIGIN_BASE, encoding="utf-8")
    (pkg / "child.py").write_text(PACKAGE_UNDERSCORE_ORIGIN_CHILD, encoding="utf-8")


def test_underscore_origin_methods_reach_the_owning_type_and_flow_through_flattening(
    tmp_path: Path,
) -> None:
    """TC-294 end to end: a real method recovered off an underscore-origin class's own AST
    (via the new ``_reexported_methods`` injection in ``inspect_public_surface()``) must reach
    its owning type's own "methods" list through ``_python_types_from_surface()``, AND
    ``_flatten_inheritance()`` (TC-281, wired into the Python path) must then be able to copy
    that recovered method onto a real subclass, with a correct ``inherited_from`` tag pointing
    at the base's own ``class_import`` - proving the fix is reachable end to end, not only at
    the ``python_surface`` unit level.
    """
    _write_underscore_origin_inheritance_package(tmp_path)
    types, _unresolved = run_extraction._extract_python_surface(tmp_path)
    by_name = {entry["name"]: entry for entry in types}

    base = by_name["Base"]
    base_method_names = {m["name"] for m in base["methods"]}
    assert "greet" in base_method_names, "the underscore-origin base's own method must surface"
    base_greet = next(m for m in base["methods"] if m["name"] == "greet")
    assert "inherited_from" not in base_greet
    assert base_greet["doc"] == "A method only recoverable through TC-294's underscore-origin method fix."

    child = by_name["Child"]
    assert child["bases"] == ["Base"]
    child_method_names = {m["name"] for m in child["methods"]}
    assert "greet" in child_method_names, "Base's recovered method must now be copied onto Child"
    child_greet = next(m for m in child["methods"] if m["name"] == "greet")
    assert child_greet["inherited_from"] == base["class_import"]

    own_method = next(m for m in child["methods"] if m["name"] == "own_method")
    assert "inherited_from" not in own_method


def test_reduce_fixture_keeps_a_centrally_referenced_type_over_alphabetically_earlier_ones() -> None:
    """The bug this card fixes: a pure alphabetical cut keeps "Aardvark"/"Bumble"/"Charlie" (all
    alphabetically earlier than "Zentral") and drops "Zentral" - even though "Zentral" is the
    type two other types in this artifact actually reference (via "bases" and "return_type"),
    the way "Page" is referenced throughout a real PDF library but still sorted out of
    tests/fixtures/pdf_go/api_surface.json today.
    """
    central = _synthetic_type("Zentral")
    referencer_one = _synthetic_type("Referrer1", bases=["Zentral"])
    referencer_two = _synthetic_type("Referrer2", return_type="Zentral")
    unreferenced_a = _synthetic_type("Aardvark")
    unreferenced_b = _synthetic_type("Bumble")
    unreferenced_c = _synthetic_type("Charlie")

    artifact = {
        "types": [
            unreferenced_a,
            unreferenced_b,
            unreferenced_c,
            referencer_one,
            referencer_two,
            central,
        ]
    }

    # Sanity check on the premise: a pure alphabetical cut (the pre-fix behavior) keeps the
    # first 3 of these 6 names and drops "Zentral" entirely.
    alphabetical_order = sorted(artifact["types"], key=lambda entry: entry["name"])
    assert {entry["name"] for entry in alphabetical_order[:3]} == {"Aardvark", "Bumble", "Charlie"}

    fixture = run_extraction.reduce_fixture(artifact, max_types=3)

    names = {entry["name"] for entry in fixture["types"]}
    assert "Zentral" in names, names  # referenced twice; must survive the cut now
    # Only 3 of 6 types fit. "Charlie" sorted ahead of the cutoff under the old, pure
    # alphabetical algorithm - it is never referenced by anything, so it is the one dropped now
    # in favor of the type other types actually reference.
    assert "Charlie" not in names, names
    assert names == {"Zentral", "Aardvark", "Bumble"}
    assert fixture["reduced_type_count"] == 3
    assert fixture["truncated"] is True


def test_reduce_fixture_breaks_equal_centrality_scores_alphabetically() -> None:
    """Two types with an EQUAL centrality score (both referenced zero times here) must still
    come out in alphabetical order, so the same artifact always reduces to the same fixture.
    """
    artifact = {
        "types": [
            _synthetic_type("Zulu"),
            _synthetic_type("Alpha"),
            _synthetic_type("Mike"),
        ]
    }
    fixture = run_extraction.reduce_fixture(artifact, max_types=3)

    names = [entry["name"] for entry in fixture["types"]]
    assert names == ["Alpha", "Mike", "Zulu"]
    assert fixture["truncated"] is False
