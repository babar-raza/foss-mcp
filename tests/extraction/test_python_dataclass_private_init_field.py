"""TC-819 (RD2-164, note/python): a synthesized dataclass ``__init__`` does not expose private fields.

``Page.__init__(_children: list[Node] = <factory>, Title=...)`` advertised the private dataclass field
``_children`` as a public constructor parameter. The rule (scoped to ``python_surface._synthesized_init``
only, never to the generic ``chunk_builder._param_text``): a dataclass field whose NAME starts with ``_``
is dropped from the synthesized ``__init__``; every other parameter, and every hand-written ``__init__``,
is unchanged. Offline: hand-authored micro-sources written into ``tmp_path`` and read by ``python_surface``.
"""

from __future__ import annotations

from pathlib import Path

from foss_mcp.extraction.tree_sitter_engine import python_surface

PACKAGE = "privpkg"

SOURCE = """\
from __future__ import annotations

from dataclasses import InitVar, KW_ONLY, dataclass, field
from typing import ClassVar, NamedTuple


@dataclass
class Page:
    _children: list[int] = field(default_factory=list)
    Title: str | None = None
    Author: str | None = None


@dataclass
class OnlyPrivate:
    _children: list[int] = field(default_factory=list)


@dataclass
class RequiredPrivate:
    name: str
    _hidden: int
    size: int = 0


@dataclass
class NotInInit:
    _cache: dict[str, int] = field(default_factory=dict, init=False)
    visible: int = 0
    also_hidden: int = field(default=0, init=False)


@dataclass
class WithInitVar:
    _secret: InitVar[str]
    token: InitVar[str]
    label: str = ""


@dataclass
class UnderscoreInside:
    page_count: int = 0
    my_private_name: str = ""
    trailing_: int = 0


@dataclass
class DunderField:
    __slots_hint: int = 0
    value: int = 0


@dataclass
class Base:
    _items: list[int] = field(default_factory=list)
    base_public: int = 1


@dataclass
class Child(Base):
    child_public: str = "c"
    _child_private: int = 0


@dataclass
class Redefines(Base):
    _items: list[str] = field(default_factory=list)
    extra: int = 2


@dataclass
class KeywordOnly:
    first: int = 0
    _: KW_ONLY
    _kw_private: int = 0
    kw_public: int = 0


@dataclass(kw_only=True)
class AllKeywordOnly:
    _flag: bool = False
    shown: bool = True


@dataclass
class WithClassVar:
    _registry: ClassVar[dict[str, int]] = {}
    count: int = 0


@dataclass(init=False)
class NoInit:
    _a: int = 0
    b: int = 0


@dataclass
class OwnInit:
    _children: list[int] = field(default_factory=list)

    def __init__(self, _children: list[int] | None = None, extra: int = 0) -> None:
        self._children = _children or []


class PlainClass:
    _children: list[int] = []
    shown: int = 0

    def __init__(self, _children: list[int] | None = None, shown: int = 0) -> None:
        self._children = _children or []


class Point(NamedTuple):
    x: int
    y: int = 0
"""


def _init_symbols(root: Path) -> dict[str, python_surface.PublicSymbol]:
    package = root / PACKAGE
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("from .models import *\n", encoding="utf-8")
    (package / "models.py").write_text(SOURCE, encoding="utf-8")
    surface = python_surface.inspect_public_surface(root, [PACKAGE])
    return {
        symbol.qualified_name.rsplit(".", 2)[-2]: symbol
        for symbol in surface.symbols
        if symbol.qualified_name.endswith(".__init__")
        and symbol.qualified_name.startswith(f"{PACKAGE}.models.")
    }


def _names(symbol: python_surface.PublicSymbol) -> list[str]:
    return [name for name, _ in symbol.params]


def test_the_note_python_shape_drops_the_private_field_and_keeps_the_rest(tmp_path: Path) -> None:
    init = _init_symbols(tmp_path)["Page"]
    assert init.signature == "def __init__(self, Title: str | None = None, Author: str | None = None)"
    assert _names(init) == ["Title", "Author"]
    assert "_children" not in init.signature
    assert [name for name, _ in init.param_types] == ["Title", "Author"]
    assert [name for name, _, _ in init.param_details] == ["Title", "Author"]


def test_a_class_with_only_a_private_field_gets_an_empty_constructor(tmp_path: Path) -> None:
    init = _init_symbols(tmp_path)["OnlyPrivate"]
    assert init.signature == "def __init__(self)"
    assert init.params == ()
    assert init.param_details == ()


def test_a_required_private_field_is_dropped_too(tmp_path: Path) -> None:
    init = _init_symbols(tmp_path)["RequiredPrivate"]
    assert init.signature == "def __init__(self, name: str, size: int = 0)"


def test_init_false_fields_are_still_left_out_as_before(tmp_path: Path) -> None:
    init = _init_symbols(tmp_path)["NotInInit"]
    assert init.signature == "def __init__(self, visible: int = 0)"


def test_initvar_keeps_a_public_name_and_drops_a_private_one(tmp_path: Path) -> None:
    init = _init_symbols(tmp_path)["WithInitVar"]
    assert init.signature == "def __init__(self, token: str, label: str = '')"


def test_an_underscore_later_in_the_name_is_public(tmp_path: Path) -> None:
    init = _init_symbols(tmp_path)["UnderscoreInside"]
    assert init.signature == (
        "def __init__(self, page_count: int = 0, my_private_name: str = '', trailing_: int = 0)"
    )


def test_a_double_underscore_field_is_private(tmp_path: Path) -> None:
    init = _init_symbols(tmp_path)["DunderField"]
    assert init.signature == "def __init__(self, value: int = 0)"


def test_inherited_private_fields_are_dropped_and_inherited_public_ones_kept(tmp_path: Path) -> None:
    symbols = _init_symbols(tmp_path)
    assert symbols["Base"].signature == "def __init__(self, base_public: int = 1)"
    assert symbols["Child"].signature == "def __init__(self, base_public: int = 1, child_public: str = 'c')"
    assert symbols["Redefines"].signature == "def __init__(self, base_public: int = 1, extra: int = 2)"


def test_keyword_only_private_fields_are_dropped_without_a_dangling_star(tmp_path: Path) -> None:
    symbols = _init_symbols(tmp_path)
    assert symbols["KeywordOnly"].signature == "def __init__(self, first: int = 0, *, kw_public: int = 0)"
    assert symbols["AllKeywordOnly"].signature == "def __init__(self, *, shown: bool = True)"


def test_classvar_and_init_false_dataclasses_are_unchanged(tmp_path: Path) -> None:
    symbols = _init_symbols(tmp_path)
    assert symbols["WithClassVar"].signature == "def __init__(self, count: int = 0)"
    assert "NoInit" not in symbols


def test_a_hand_written_init_and_non_dataclass_classes_keep_their_private_parameters(
    tmp_path: Path,
) -> None:
    symbols = _init_symbols(tmp_path)
    for name in ("OwnInit", "PlainClass"):
        assert "_children: list[int] | None=None" in symbols[name].signature, name
    assert symbols["PlainClass"].signature.endswith("shown: int=0) -> None")


def test_a_namedtuple_is_untouched(tmp_path: Path) -> None:
    assert _init_symbols(tmp_path)["Point"].signature == "def __init__(self, x: int, y: int = 0)"
