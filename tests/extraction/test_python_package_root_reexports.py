"""G3/TC-818 (REQ-G3-012): every public Python import path of a re-exported symbol is a shell.

The defect (RD2-276, words/python): ``aspose/words_foss/__init__.py`` declares a literal ``__all__`` and ALSO
imports eleven enums from ``aspose.words_foss.model.enums`` that it never uses again, with the comment
``re-export for aw.ParagraphAlignment etc.``. ``from aspose.words_foss import CellMerge`` works - ``__all__`` only
steers ``import *`` - but the reader treated ``__all__`` as the gate for every name of the module, so
``aspose.words_foss.CellMerge`` had no symbol and only the defining-module path was indexed.

The rule under test: in a package ``__init__`` with a literal ``__all__``, an in-tree ``from .x import Y`` that
``__all__`` omits is still a public re-export when the author marked it one (``# noqa: F401`` on the statement, or
``import Y as Y``) and the module never refers to ``Y`` again. An unmarked import, a used one, an
``if TYPE_CHECKING`` import, a private name, a star import and an import of code outside the scanned tree are not.
Offline, deterministic, no skips; upstream-shaped micro-libraries hand-authored under ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import python_surface
from foss_mcp.indexing.symbol_identity import compute_canonical_identities


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _surface(root: Path, package: str = "pkg") -> python_surface.PublicSurface:
    return python_surface.inspect_public_surface(root, [package])


def _shells(surface: python_surface.PublicSurface) -> dict[str, str]:
    """``{qualified name: what it re-exports}`` for every re-export symbol the surface holds."""
    return {s.qualified_name: s.reexported_from or "" for s in surface.symbols if s.public_by == "reexport"}


def _library(root: Path) -> None:
    """The words/python shape plus every case the rule must not over-reach into."""
    _write(
        root,
        {
            "pkg/__init__.py": (
                "from __future__ import annotations\n"
                "from typing import TYPE_CHECKING, Any\n"
                "from pkg.document import Document\n"
                "from pkg.model.enums import (  # noqa: F401 - re-export for pkg.Mode etc.\n"
                "    Mode,\n"
                "    Level as Grade,\n"
                "    _Hidden,\n"
                ")\n"
                "from pkg.model import wrap, Used  # noqa: F401\n"
                "from pkg.model.helpers import Plain\n"
                "from pkg.model.helpers import Explicit as Explicit\n"
                "from pkg.model.nested import Deep  # noqa: F401\n"
                "from pkg.model.nested import *\n"
                "from .missing import Ghost  # noqa: F401\n"
                "from . import loading\n"
                "try:\n"
                "    from pkg.model.fast import Speedy  # noqa: F401\n"
                "except ImportError:\n"
                "    from pkg.model.slow import Speedy  # noqa: F401\n"
                "if TYPE_CHECKING:\n"
                "    from pkg.model.enums import OnlyForTypes\n"
                "\n"
                "def make(kind: Any = None):\n"
                "    return Used()\n"
                "\n"
                '__all__ = ["Document", "make"]\n'
            ),
            "pkg/document.py": "class Document:\n    def save(self): ...\n",
            "pkg/loading.py": "def load(): ...\n",
            "pkg/model/__init__.py": ("from .enums import Mode\nfrom .helpers import wrap, Used\n"),
            "pkg/model/helpers.py": (
                "def wrap(x): ...\nclass Used: ...\nclass Plain: ...\nclass Explicit: ...\n"
            ),
            "pkg/model/enums/__init__.py": (
                "from pkg.model.enums.layout import Mode, Level, OnlyForTypes, _Hidden  # noqa: F401\n"
            ),
            "pkg/model/enums/layout.py": (
                "class Mode:\n    A = 1\n    B = 2\nclass Level:\n    LOW = 1\n"
                "class OnlyForTypes:\n    X = 1\nclass _Hidden:\n    Y = 1\n"
            ),
            "pkg/model/nested/__init__.py": (
                'from .deep import Deep, Extra  # noqa: F401\n__all__ = ["Deep"]\n'
            ),
            "pkg/model/nested/deep.py": "class Deep:\n    def go(self): ...\nclass Extra:\n    def run(self): ...\n",
            "pkg/model/fast.py": "class Speedy:\n    def go(self): ...\n",
            "pkg/model/slow.py": "class Speedy:\n    def go(self): ...\n",
        },
    )


def test_unused_import_hidden_by_all_is_a_package_root_shell(tmp_path: Path) -> None:
    _library(tmp_path)
    shells = _shells(_surface(tmp_path))
    assert shells["pkg.Mode"] == "pkg.model.enums.Mode"
    assert shells["pkg.Grade"] == "pkg.model.enums.Level"  # `import Level as Grade`: exposed under its alias
    assert shells["pkg.Deep"] == "pkg.model.nested.Deep"
    assert shells["pkg.Document"] == "pkg.document.Document"  # in __all__: unchanged
    assert shells["pkg.wrap"] == "pkg.model.wrap"  # a re-exported function, one hop from its own shell
    assert (
        shells["pkg.Explicit"] == "pkg.model.helpers.Explicit"
    )  # `import Explicit as Explicit` is the marker


def test_chain_of_package_inits_exposes_each_public_path(tmp_path: Path) -> None:
    _library(tmp_path)
    shells = _shells(_surface(tmp_path))
    # Mode is importable from pkg, from pkg.model and from pkg.model.enums, and defined in layout.
    assert shells["pkg.Mode"] == "pkg.model.enums.Mode"
    assert shells["pkg.model.Mode"] == "pkg.model.enums.Mode"
    assert shells["pkg.model.enums.Mode"] == "pkg.model.enums.layout.Mode"
    # nested/__init__ lists only Deep in __all__; Extra is unused there and unreachable from pkg.
    assert shells["pkg.model.nested.Deep"] == "pkg.model.nested.deep.Deep"
    assert shells["pkg.model.nested.Extra"] == "pkg.model.nested.deep.Extra"
    assert "pkg.Extra" not in shells


def test_a_branch_import_is_exposed_by_its_last_binding(tmp_path: Path) -> None:
    _library(tmp_path)
    shells = _shells(_surface(tmp_path))
    assert shells["pkg.Speedy"] == "pkg.model.slow.Speedy"  # the last binding of a try/except wins


def test_internal_plumbing_is_never_exposed(tmp_path: Path) -> None:
    _library(tmp_path)
    surface = _surface(tmp_path)
    shells = _shells(surface)
    # used by make(): imported for the module's own use, even though a noqa comment sits on the import
    assert "pkg.Used" not in shells
    # no re-export marker: a package may keep an import out of __all__ on purpose
    assert "pkg.Plain" not in shells
    # `if TYPE_CHECKING:` is never a runtime re-export
    assert "pkg.OnlyForTypes" not in shells
    # a leading underscore is private, whatever the import statement says
    assert not any(name.rsplit(".", 1)[-1].startswith("_") for name in shells)
    # code outside the scanned tree is not the package's to re-export
    assert "pkg.Any" not in shells
    assert "pkg.TYPE_CHECKING" not in shells
    assert "pkg.annotations" not in shells
    # nothing is guessed from a star import or an import that resolves to nothing: both stay disclosed
    assert any("import *" in entry for entry in surface.unresolved)
    assert any("unresolved-reexport:pkg.missing.Ghost" in entry for entry in surface.unresolved)
    assert "pkg.Ghost" not in {s.qualified_name for s in surface.symbols if s.kind in ("class", "enum")}


def test_nothing_is_duplicated_and_the_definition_is_untouched(tmp_path: Path) -> None:
    _library(tmp_path)
    surface = _surface(tmp_path)
    names = [s.qualified_name for s in surface.symbols]
    assert len(names) == len(set(names))
    definition = next(s for s in surface.symbols if s.qualified_name == "pkg.model.enums.layout.Mode")
    assert definition.public_by == "name"
    assert definition.reexported_from is None
    shell = next(s for s in surface.symbols if s.qualified_name == "pkg.Mode")
    assert shell.kind == "class" and shell.public_by == "reexport"


def test_the_marker_is_a_noqa_naming_f401_or_a_blanket_noqa(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": (
                "from pkg.real import A  # noqa: F401\n"
                "from pkg.real import B  # NOQA\n"
                "from pkg.real import (\n    C,\n)  # noqa: E501, F401\n"
                "from pkg.real import D  # noqa: E501\n"
                "from pkg.real import E  # noqa:F401 - a re-export\n"
                "from pkg.real import F  # just a comment\n"
                '__all__ = ["G"]\n'
                "def G(): ...\n"
            ),
            "pkg/real.py": "".join(f"class {name}: ...\n" for name in "ABCDEF"),
        },
    )
    assert sorted(_shells(_surface(tmp_path))) == ["pkg.A", "pkg.B", "pkg.C", "pkg.E"]


def test_an_unmarked_unused_import_hidden_by_all_stays_hidden(tmp_path: Path) -> None:
    """The pinned behaviour of tree_sitter_engine/test_python_surface.py: no marker, no exposure."""
    _write(
        tmp_path,
        {
            "pkg/__init__.py": 'from .real import Thing, Other\n__all__ = ["Thing"]\n',
            "pkg/real.py": "class Thing: ...\nclass Other: ...\n",
        },
    )
    assert _shells(_surface(tmp_path)) == {"pkg.Thing": "pkg.real.Thing"}


def test_a_module_that_is_not_a_package_init_is_unchanged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/helper.py": 'from pkg.real import Thing  # noqa: F401\n__all__ = ["run"]\ndef run(): ...\n',
            "pkg/real.py": "class Thing: ...\n",
        },
    )
    assert "pkg.helper.Thing" not in {s.qualified_name for s in _surface(tmp_path).symbols}


def test_a_package_without_all_is_unchanged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "from pkg.real import Thing\n",
            "pkg/real.py": "class Thing: ...\n",
        },
    )
    assert _shells(_surface(tmp_path)) == {"pkg.Thing": "pkg.real.Thing"}


def test_an_import_the_package_uses_or_rebinds_stays_hidden(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": (
                "from pkg.real import Used, Rebound, Defined  # noqa: F401\n"
                "class Defined:\n    pass\n"
                "Rebound = None\n"
                "def go():\n    return Used.make()\n"
                '__all__ = ["go"]\n'
            ),
            "pkg/real.py": (
                "class Used:\n    @staticmethod\n    def make(): ...\nclass Rebound: ...\nclass Defined: ...\n"
            ),
        },
    )
    assert _shells(_surface(tmp_path)) == {}


def test_a_short_name_defined_twice_exposes_only_the_path_that_was_imported(tmp_path: Path) -> None:
    """The unique-name case resolves; the twin that nothing re-exports gains no package-root path."""
    _write(
        tmp_path,
        {
            "pkg/__init__.py": 'from pkg.a import Twin  # noqa: F401\n__all__ = ["other"]\ndef other(): ...\n',
            "pkg/a.py": "class Twin:\n    def go(self): ...\n",
            "pkg/b.py": "class Twin:\n    def stop(self): ...\n",
        },
    )
    surface = _surface(tmp_path)
    assert _shells(surface) == {"pkg.Twin": "pkg.a.Twin"}
    assert {s.qualified_name for s in surface.symbols if s.kind == "class"} == {
        "pkg.Twin",
        "pkg.a.Twin",
        "pkg.b.Twin",
    }


def _types(root: Path) -> list[dict[str, Any]]:
    return run_extraction._python_types_from_surface(_surface(root))


def test_the_package_root_shell_travels_into_the_extracted_types_and_pairs_with_its_definition(
    tmp_path: Path,
) -> None:
    _library(tmp_path)
    types = _types(tmp_path)
    by_import = {entry["class_import"]: entry for entry in types}
    shell = by_import["pkg.Mode"]
    assert shell["reexport_of"] == "pkg.model.enums.Mode"
    identities = compute_canonical_identities(types)
    index = {entry["class_import"]: i for i, entry in enumerate(types)}
    root_identity = identities[index["pkg.Mode"]]
    definition_identity = identities[index["pkg.model.enums.layout.Mode"]]
    assert root_identity.canonical_id == definition_identity.canonical_id == "pkg.model.enums.layout.Mode"
    assert not root_identity.is_definition
    assert definition_identity.is_definition
    assert "pkg.Mode" in definition_identity.aliases
    assert "pkg.model.enums.Mode" in definition_identity.aliases


def test_the_real_words_shape_resolves_the_package_root_name(tmp_path: Path) -> None:
    """RD2-276, in miniature: the exact import shape of ``aspose/words_foss/__init__.py``."""
    _write(
        tmp_path,
        {
            "aspose/__init__.py": "",
            "aspose/words_foss/__init__.py": (
                "from aspose.words_foss.document import Document, SaveFormat\n"
                "from aspose.words_foss.model import wrap_type, enums\n"
                "from aspose.words_foss.model.enums import (  # noqa: F401 - re-export for aw.Underline etc.\n"
                "    CellMerge,\n    Underline,\n)\n"
                '__all__ = ["Document", "SaveFormat"]\n'
            ),
            "aspose/words_foss/document.py": "class Document: ...\nclass SaveFormat:\n    DOCX = 1\n",
            "aspose/words_foss/model/__init__.py": "",
            "aspose/words_foss/model/wrap_type.py": "def wrap(x): ...\n",
            "aspose/words_foss/model/enums/__init__.py": (
                "from aspose.words_foss.model.enums.table import CellMerge  # noqa: F401\n"
                "from aspose.words_foss.model.enums.font import Underline  # noqa: F401\n"
            ),
            "aspose/words_foss/model/enums/table.py": "class CellMerge:\n    NONE = 0\n    FIRST = 1\n",
            "aspose/words_foss/model/enums/font.py": "class Underline:\n    NONE = 0\n    SINGLE = 1\n",
        },
    )
    types = _types_of(tmp_path, "aspose")
    by_import = {entry["class_import"]: entry for entry in types}
    for name in ("CellMerge", "Underline"):
        assert f"aspose.words_foss.{name}" in by_import
        assert (
            by_import[f"aspose.words_foss.{name}"]["reexport_of"] == f"aspose.words_foss.model.enums.{name}"
        )
    identities = compute_canonical_identities(types)
    index = {entry["class_import"]: i for i, entry in enumerate(types)}
    canonical = identities[index["aspose.words_foss.CellMerge"]].canonical_id
    assert canonical == identities[index["aspose.words_foss.model.enums.table.CellMerge"]].canonical_id


def _types_of(root: Path, package: str) -> list[dict[str, Any]]:
    return run_extraction._python_types_from_surface(_surface(root, package))
