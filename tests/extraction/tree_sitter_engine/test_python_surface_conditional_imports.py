"""TC-674: a module-level re-export import written inside a ``try``/``except`` or ``if``/``else``
block (the common "fast implementation with a pure-Python fallback", or Python-version-conditional,
packaging idiom) is resolved - or honestly disclosed as ``unresolved`` - by the same rules a direct
top-level ``from ... import`` already gets, instead of being silently invisible.

Branch-resolution order (stated here, as the taskcard requires, and exercised by every test below):
for ``try``/``except``, the ``try`` body is read before each ``except`` handler's body (in source
order) before ``else`` before ``finally``; for ``if``/``else``, the ``if`` body is read before
``else``/``elif``. When the SAME exposed name is bound by more than one branch, the LAST one in this
order wins - the same "a name bound more than once is its LAST binding" convention
``_last_binding``/TC-532 already applies to module-level redefinitions. Nesting is never followed
past one level inside a module-top-level ``Try``/``If``: a ``Try``/``If`` nested inside a branch, or
a statement inside a function/class body within one, stays exactly as invisible as before this card.

Uses the same ``_write``/``inspect_public_surface`` fixture pattern as
``test_python_surface.py``'s own existing tests.
"""

from __future__ import annotations

from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine.python_surface import inspect_public_surface


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_try_except_guarded_reexport_now_resolves(tmp_path: Path) -> None:
    """(a) The "fast implementation with a pure-Python fallback" idiom: both branches bind the
    same name. Per the stated order, the ``except`` handler (read after the ``try`` body) wins."""
    _write(
        tmp_path,
        "pkg/__init__.py",
        "try:\n    from ._fast import FastThing\nexcept ImportError:\n    from ._pure import FastThing\n",
    )
    _write(tmp_path, "pkg/_fast.py", "class FastThing:\n    '''The fast one.'''\n")
    _write(tmp_path, "pkg/_pure.py", "class FastThing:\n    '''The pure one.'''\n")

    surface = inspect_public_surface(tmp_path, ["pkg"])
    by_name = {s.qualified_name: s for s in surface.symbols}

    assert "pkg.FastThing" in by_name
    fast_thing = by_name["pkg.FastThing"]
    assert fast_thing.kind == "class"
    assert fast_thing.public_by == "reexport"
    # The except handler is read after the try body, so it provides the LAST binding.
    assert fast_thing.reexported_from == "pkg._pure.FastThing"
    assert fast_thing.docstring == "The pure one."
    assert not any("FastThing" in entry for entry in surface.unresolved)


def test_an_if_else_version_conditional_reexport_resolves_the_same_way(tmp_path: Path) -> None:
    """(b) The ``sys.version_info``-style idiom. Per the stated order, the ``else`` branch (read
    after the ``if`` body) wins."""
    _write(
        tmp_path,
        "pkg/__init__.py",
        "import sys\n"
        "if sys.version_info >= (3, 8):\n"
        "    from ._new import Thing\n"
        "else:\n"
        "    from ._old import Thing\n",
    )
    _write(tmp_path, "pkg/_new.py", "class Thing:\n    pass\n")
    _write(tmp_path, "pkg/_old.py", "class Thing:\n    pass\n")

    surface = inspect_public_surface(tmp_path, ["pkg"])
    by_name = {s.qualified_name: s for s in surface.symbols}

    assert "pkg.Thing" in by_name
    thing = by_name["pkg.Thing"]
    assert thing.kind == "class"
    assert thing.public_by == "reexport"
    # The else branch is read after the if body, so it provides the LAST binding.
    assert thing.reexported_from == "pkg._old.Thing"
    assert not any("Thing" in entry for entry in surface.unresolved)


def test_only_the_resolvable_branch_of_two_still_resolves(tmp_path: Path) -> None:
    """(c) The try body names a module that does not exist in this fixture at all; the except
    handler (read LAST, so it provides the final binding regardless) names a real one. Made the
    except branch the resolvable one deliberately, so the "last one wins" order and "the resolvable
    branch wins" are the same outcome here and the test is unambiguous either way."""
    _write(
        tmp_path,
        "pkg/__init__.py",
        "try:\n    from ._missing import Thing\nexcept ImportError:\n    from ._real import Thing\n",
    )
    _write(tmp_path, "pkg/_real.py", "class Thing:\n    pass\n")
    # Deliberately no pkg/_missing.py on disk.

    surface = inspect_public_surface(tmp_path, ["pkg"])
    by_name = {s.qualified_name: s for s in surface.symbols}

    assert "pkg.Thing" in by_name
    thing = by_name["pkg.Thing"]
    assert thing.kind == "class"
    assert thing.reexported_from == "pkg._real.Thing"
    # The missing branch's attempt is simply superseded by the surviving binding (TC-532's own
    # "a name bound more than once is its LAST binding" precedent never discloses the binding a
    # later one replaced either), so no extra unresolved noise is expected for it.
    assert not any("Thing" in entry for entry in surface.unresolved)


def test_an_import_nested_two_levels_deep_stays_invisible(tmp_path: Path) -> None:
    """(d) Scope boundary: one level inside a module-top-level Try/If is resolved; two levels is
    not. Exercises both shapes the card names: a Try nested inside another Try, and an import
    inside a function body nested inside an If."""
    _write(
        tmp_path,
        "pkg/__init__.py",
        "try:\n"
        "    try:\n"
        "        from ._deep import Nested\n"
        "    except ImportError:\n"
        "        pass\n"
        "except Exception:\n"
        "    pass\n"
        "if True:\n"
        "    def _loader():\n"
        "        from ._deeper import Deeper\n"
        "        return Deeper\n",
    )
    _write(tmp_path, "pkg/_deep.py", "class Nested:\n    pass\n")
    _write(tmp_path, "pkg/_deeper.py", "class Deeper:\n    pass\n")

    surface = inspect_public_surface(tmp_path, ["pkg"])
    names = {s.qualified_name for s in surface.symbols}

    assert "pkg.Nested" not in names
    assert "pkg.Deeper" not in names
    assert not any("Nested" in entry for entry in surface.unresolved)
    assert not any("Deeper" in entry for entry in surface.unresolved)
    # The module itself, with nothing else, is still the only symbol this file contributes.
    assert names == {"pkg"}


def test_star_imports_and_plain_top_level_reexports_are_unaffected(tmp_path: Path) -> None:
    """(e) Pre-existing, already-correct behavior - a star import stays honestly ``unresolved``,
    never guessed, and a plain top-level re-export resolves exactly as before - is unchanged by
    this card's new Try/If handling."""
    _write(
        tmp_path,
        "pkg/__init__.py",
        "from .widget import Widget\nfrom .sub import *\n",
    )
    _write(tmp_path, "pkg/widget.py", "class Widget:\n    pass\n")
    _write(tmp_path, "pkg/sub.py", "class Leaf:\n    pass\n")

    surface = inspect_public_surface(tmp_path, ["pkg"])
    by_name = {s.qualified_name: s for s in surface.symbols}

    assert by_name["pkg.Widget"].reexported_from == "pkg.widget.Widget"
    assert by_name["pkg.Widget"].public_by == "reexport"
    assert surface.unresolved == ("pkg:2:from .sub import *",)
