"""The static public surface: definitions, __all__, re-exports with provenance, nothing guessed.

Ported from repository-presenter's ``platforms/test_python_surface.py``. Trimmed to what
``inspect_public_surface`` itself proves: the tests there that exercised
``public_symbol_facts`` (fact-ID assembly, canonical-path selection among aliases, shadowing)
are not ported, since that function was not ported — it is a caller's concern
(``foss_mcp.extraction.tree_sitter_engine``'s own docstring), not this engine's.
"""

from __future__ import annotations

from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine.python_surface import PublicSymbol, inspect_public_surface


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _package(tmp_path: Path) -> Path:
    _write(
        tmp_path,
        "pkg/__init__.py",
        "from .widget import Widget, _Hidden\nfrom .factory import make, other\n"
        "from .sub import *\nfrom . import sub\n__all__ = ['Widget', 'make', 'sub']\n",
    )
    _write(
        tmp_path,
        "pkg/widget.py",
        "class Widget:\n    def render(self):\n        pass\n\n    def _hide(self):\n"
        "        pass\n\n    @property\n    def size(self):\n        return 1\n\n"
        "    def render(self):\n        pass\n\nclass _Hidden:\n    def visible(self):\n"
        "        pass\n",
    )
    _write(
        tmp_path, "pkg/factory.py", "def make():\n    return Widget()\n\ndef other():\n    pass\n"
    )
    _write(tmp_path, "pkg/sub/__init__.py", "from .leaf import Leaf\n")
    _write(tmp_path, "pkg/sub/leaf.py", "class Leaf:\n    pass\n")
    _write(tmp_path, "pkg/_private.py", "class Exposed:\n    pass\n")
    _write(tmp_path, "pkg/tests/test_widget.py", "class NotPublic:\n    pass\n")
    return tmp_path


def test_definitions_all_and_reexports_are_inventoried_without_importing(tmp_path: Path) -> None:
    surface = inspect_public_surface(_package(tmp_path), ["pkg"])
    by_name = {symbol.qualified_name: symbol for symbol in surface.symbols}

    assert sorted(by_name) == [
        "pkg",
        "pkg.Widget",
        "pkg.factory",
        "pkg.factory.make",
        "pkg.factory.other",
        "pkg.make",
        "pkg.sub",
        "pkg.sub.Leaf",
        "pkg.sub.leaf",
        "pkg.sub.leaf.Leaf",
        "pkg.widget",
        "pkg.widget.Widget",
        "pkg.widget.Widget.render",
        "pkg.widget.Widget.size",
    ]
    assert by_name["pkg.widget.Widget.render"] == PublicSymbol(
        "pkg.widget.Widget.render",
        "pkg.widget",
        "render",
        "method",
        "pkg/widget.py",
        2,
        "name",
        signature="def render(self)",
    )
    assert by_name["pkg.widget.Widget.size"].kind == "method"
    assert "pkg.widget.Widget._hide" not in by_name
    assert "pkg.widget._Hidden.visible" not in by_name
    assert by_name["pkg.Widget"] == PublicSymbol(
        "pkg.Widget",
        "pkg",
        "Widget",
        "class",
        "pkg/__init__.py",
        1,
        "reexport",
        "pkg.widget.Widget",
        signature="class Widget",
    )
    assert by_name["pkg.make"].kind == "function"
    assert by_name["pkg.make"].reexported_from == "pkg.factory.make"
    assert by_name["pkg.sub"].kind == "module"
    assert by_name["pkg.sub"].public_by == "reexport"
    assert by_name["pkg.sub.Leaf"].reexported_from == "pkg.sub.leaf.Leaf"
    assert by_name["pkg.factory.other"].public_by == "name"
    assert "pkg.other" not in by_name
    assert "pkg._Hidden" not in by_name and "pkg.widget._Hidden" not in by_name
    assert not any(name.startswith("pkg._private") for name in by_name)
    assert not any("tests" in name for name in by_name)
    assert surface.unresolved == ("pkg:3:from .sub import *",)


def test_a_malformed_module_is_recorded_and_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from .good import Good\n")
    _write(tmp_path, "pkg/good.py", "class Good:\n    pass\n")
    _write(tmp_path, "pkg/broken.py", "class Broken(:\n")
    surface = inspect_public_surface(tmp_path, ["pkg"])
    assert [s.qualified_name for s in surface.symbols] == [
        "pkg",
        "pkg.Good",
        "pkg.good",
        "pkg.good.Good",
    ]
    assert len(surface.unresolved) == 1
    assert surface.unresolved[0].startswith("pkg.broken:1:syntax-error:pkg/broken.py:")


def test_a_reexport_of_a_missing_origin_stays_unresolved(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from .gone import Ghost\n")
    surface = inspect_public_surface(tmp_path, ["pkg"])
    ghost = next(s for s in surface.symbols if s.name == "Ghost")
    assert ghost.kind == "unknown"
    assert surface.unresolved == ("pkg:1:unresolved-reexport:pkg.gone.Ghost",)


def test_utf8_bom_and_src_layout_are_accepted(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "﻿from .core import Core\n")
    _write(tmp_path, "src/pkg/core.py", "﻿class Core:\n    pass\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert [s.qualified_name for s in surface.symbols] == [
        "pkg",
        "pkg.Core",
        "pkg.core",
        "pkg.core.Core",
    ]
    assert surface.symbols[1].source_path == "src/pkg/__init__.py"


def test_symbols_carry_their_kind_signature_and_docstring(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "lib/__init__.py",
        '"""Lib: scenes and shapes.\n\nMore."""\nfrom .shapes import Shape, Kind\n',
    )
    _write(
        tmp_path,
        "lib/shapes.py",
        "from enum import Enum\n\n\nclass Kind(Enum):\n"
        '    """Which shape."""\n\n    BOX = 1\n\n\nclass Shape(Base):\n'
        '    """A shape in a scene.\n\n    Longer text.\n    """\n\n'
        "    def area(self, scale: float = 1.0) -> float:\n"
        '        """The area, scaled."""\n        return 0.0\n\n\n'
        "async def load(path: str) -> Shape:\n    return Shape()\n",
    )
    surface = inspect_public_surface(tmp_path, ["lib"])
    by_name = {symbol.qualified_name: symbol for symbol in surface.symbols}
    assert by_name["lib"].docstring == "Lib: scenes and shapes."
    assert by_name["lib.shapes.Kind"].kind == "enum"
    assert by_name["lib.shapes.Kind"].signature == "class Kind(Enum)"
    assert by_name["lib.shapes.Shape"].docstring == "A shape in a scene."
    assert by_name["lib.shapes.Shape"].signature == "class Shape(Base)"
    assert by_name["lib.shapes.Shape.area"].signature == "def area(self, scale: float=1.0) -> float"
    assert by_name["lib.shapes.Shape.area"].docstring == "The area, scaled."
    assert by_name["lib.shapes.load"].signature == "async def load(path: str) -> Shape"
    # A re-export carries its origin's evidence.
    assert by_name["lib.Kind"].kind == "enum" and by_name["lib.Kind"].docstring == "Which shape."


def test_a_later_import_of_the_same_name_wins(tmp_path: Path) -> None:
    """Python binds a package name to its last import; a re-export walk must match that."""
    _write(tmp_path, "pkg/__init__.py", "from .impl.Thing import Thing\nfrom .Thing import Thing\n")
    _write(tmp_path, "pkg/Thing.py", "class Thing:\n    '''Flat.'''\n")
    _write(tmp_path, "pkg/impl/__init__.py", "")
    _write(tmp_path, "pkg/impl/Thing.py", "class Thing:\n    '''Nested.'''\n")
    surface = inspect_public_surface(tmp_path, ["pkg"])
    by_name = {symbol.qualified_name: symbol for symbol in surface.symbols}
    exposed = by_name["pkg.Thing"]
    assert exposed.kind == "class"
    assert exposed.reexported_from == "pkg.Thing.Thing"
    assert exposed.docstring == "Flat."
    # The nested definition is still its own, separately reachable symbol.
    assert by_name["pkg.impl.Thing.Thing"].kind == "class"


def test_a_reexport_chain_resolves_at_every_hop_not_only_the_first(tmp_path: Path) -> None:
    """A package re-exporting what another package already re-exported is two hops."""
    _write(tmp_path, "pkg/__init__.py", "from .errors import BarcodeError\n")
    _write(tmp_path, "pkg/errors/__init__.py", "from .base import BarcodeError\n")
    _write(tmp_path, "pkg/errors/base.py", "class BarcodeError(Exception):\n    '''Bad.'''\n")
    surface = inspect_public_surface(tmp_path, ["pkg"])
    by_name = {symbol.qualified_name: symbol for symbol in surface.symbols}
    assert by_name["pkg.BarcodeError"].kind == "class"
    assert by_name["pkg.errors.BarcodeError"].kind == "class"
    assert not [note for note in surface.unresolved if "BarcodeError" in note]


def test_a_private_module_that_only_forwards_a_name_is_followed_to_the_definition(
    tmp_path: Path,
) -> None:
    """A public name stays public through however many private modules forward it."""
    _write(tmp_path, "pkg/dom/__init__.py", "from ._window import BarProp\n")
    _write(tmp_path, "pkg/dom/_window.py", "from ._impl import BarProp\n")
    _write(tmp_path, "pkg/dom/_impl.py", "class BarProp:\n    pass\n")
    _write(tmp_path, "pkg/__init__.py", "")
    surface = inspect_public_surface(tmp_path, ["pkg"])
    by_name = {symbol.qualified_name: symbol for symbol in surface.symbols}
    assert by_name["pkg.dom.BarProp"].kind == "class"
    assert not surface.unresolved


def test_modules_that_forward_a_name_to_each_other_stay_unresolved(tmp_path: Path) -> None:
    """A cycle is answered with UNRESOLVED, never a crash - nothing is guessed."""
    _write(tmp_path, "pkg/__init__.py", "from ._left import Loop\n")
    _write(tmp_path, "pkg/_left.py", "from ._right import Loop\n")
    _write(tmp_path, "pkg/_right.py", "from ._left import Loop\n")
    surface = inspect_public_surface(tmp_path, ["pkg"])
    loop = next(symbol for symbol in surface.symbols if symbol.name == "Loop")
    assert loop.kind == "unknown"
    assert surface.unresolved == ("pkg:1:unresolved-reexport:pkg._left.Loop",)


def test_a_package_that_reexports_a_submodule_of_its_own_name_stays_a_module(
    tmp_path: Path,
) -> None:
    """``from . import cfb`` names ``pkg.cfb`` as its own origin; the file system answers it."""
    _write(tmp_path, "pkg/__init__.py", "from . import cfb\n")
    _write(tmp_path, "pkg/cfb/__init__.py", "from .reader import Reader\n")
    _write(tmp_path, "pkg/cfb/reader.py", "class Reader:\n    pass\n")
    surface = inspect_public_surface(tmp_path, ["pkg"])
    by_name = {symbol.qualified_name: symbol for symbol in surface.symbols}
    assert by_name["pkg.cfb"].public_by == "reexport"
    assert by_name["pkg.cfb"].reexported_from == "pkg.cfb"
    assert by_name["pkg.cfb"].kind == "module"
    assert not [note for note in surface.unresolved if "unresolved-reexport" in note]
