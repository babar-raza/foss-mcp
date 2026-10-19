"""TC-532 (Round 3, rows RD3-001 to RD3-009): what the pure-``ast`` Python reader must get right.

Every expectation here follows from the rules of the Python language for a hand-written source - never
from what the extractor printed: ``auto()`` members of an enum, the LAST definition of a name winning,
the LAST ``from ... import`` of an exported name winning, the members a class inherits through an
underscore-prefixed base, properties built by ``property(...)`` or by a factory function, and the
``__init__`` that ``@dataclass`` / ``NamedTuple`` generate. Offline: the packages are written into
``tmp_path`` and read by ``inspect_public_surface`` and ``run_extraction``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import python_surface
from foss_mcp.extraction.tree_sitter_engine.python_surface import PublicSymbol, inspect_public_surface


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _symbols(root: Path, package: str = "pkg") -> dict[str, PublicSymbol]:
    return {symbol.qualified_name: symbol for symbol in inspect_public_surface(root, [package]).symbols}


def _entries(root: Path) -> dict[str, dict[str, Any]]:
    """The entries the whole python path makes of ``root/src/pkg``: the surface, the adapter, the
    inheritance flattening and the override rule."""
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\n', encoding="utf-8")
    types, _unresolved = run_extraction._extract_python_surface(root)
    return {entry["class_import"]: entry for entry in types}


def _src(root: Path, relative: str, text: str) -> None:
    _write(root, f"src/pkg/{relative}", text)


def _method(entry: dict[str, Any], name: str) -> dict[str, Any]:
    return next(method for method in entry["methods"] if method["name"] == name)


def _methods(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {method["name"]: method for method in entry["methods"]}


def _properties(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {prop["name"]: prop for prop in entry["properties"]}


# --------------------------------------------------------------------------- enum members (RD3-002)


def test_the_auto_calls_are_the_two_spellings_of_enum_auto() -> None:
    assert python_surface._ENUM_AUTO_CALLS == ("auto", "enum.auto")


def test_auto_members_are_recorded_in_source_order(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/mod.py",
        "from enum import Enum, auto\n\nclass E(Enum):\n    A = auto()\n    B = auto()\n    C = auto()\n",
    )
    _write(tmp_path, "pkg/__init__.py", "")
    symbol = _symbols(tmp_path)["pkg.mod.E"]
    assert symbol.kind == "enum"
    assert symbol.enum_members == (("A", "auto()"), ("B", "auto()"), ("C", "auto()"))


def test_the_dotted_enum_auto_is_an_automatic_member_too(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/mod.py", "import enum\n\nclass E(enum.Enum):\n    A = enum.auto()\n    B = 7\n")
    assert _symbols(tmp_path)["pkg.mod.E"].enum_members == (("A", "auto()"), ("B", "7"))


def test_int_enum_str_enum_and_flag_bases_make_an_enum_with_auto_members(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "import enum\nfrom enum import auto\n\n"
        "class I(enum.IntEnum):\n    ONE = auto()\n\n"
        "class S(enum.StrEnum):\n    LOW = auto()\n\n"
        "class F(enum.Flag):\n    R = auto()\n    W = auto()\n",
    )
    symbols = _symbols(tmp_path)
    for name, members in {"I": ("ONE",), "S": ("LOW",), "F": ("R", "W")}.items():
        symbol = symbols[f"pkg.mod.{name}"]
        assert symbol.kind == "enum"
        assert tuple(member for member, _ in symbol.enum_members) == members


def test_literal_auto_and_computed_values_and_private_names(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "from enum import Enum, auto\n\nclass E(Enum):\n"
        "    A = 1\n    B = auto()\n    C = 'x'\n    D = make()\n    _skip = 9\n    E: int = 5\n"
        "    def label(self):\n        return 'e'\n",
    )
    assert _symbols(tmp_path)["pkg.mod.E"].enum_members == (
        ("A", "1"),
        ("B", "auto()"),
        ("C", "'x'"),
        ("D", ""),
        ("E", "5"),
    )


def test_an_enum_defined_in_a_private_module_keeps_its_members_when_exported(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from ._modes import Mode\n")
    _write(
        tmp_path,
        "pkg/_modes.py",
        "from enum import Enum, auto\n\nclass Mode(Enum):\n    FAST = auto()\n    SLOW = auto()\n",
    )
    exported = _symbols(tmp_path)["pkg.Mode"]
    assert exported.kind == "enum"
    assert exported.enum_members == (("FAST", "auto()"), ("SLOW", "auto()"))


def test_the_enum_reaches_the_entry_with_its_members_through_two_reexports(tmp_path: Path) -> None:
    _src(tmp_path, "__init__.py", "from .api import Mode\n")
    _src(tmp_path, "api.py", "from ._modes import Mode\n")
    _src(tmp_path, "_modes.py", "from enum import Enum, auto\n\nclass Mode(Enum):\n    FAST = auto()\n")
    entry = _entries(tmp_path)["pkg.Mode"]
    assert entry["kind"] == "enum"
    assert entry["enum_members"] == [{"name": "FAST", "value": "auto()"}]


# --------------------------------------------------------------------------- the last definition wins (RD3-004)


def test_a_method_defined_twice_is_the_second_definition(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "class T:\n"
        "    def rotate(self, rot):\n        pass\n\n"
        "    def other(self):\n        pass\n\n"
        "    def rotate(self, rot, order=None):\n        pass\n",
    )
    symbols = _symbols(tmp_path)
    rotate = symbols["pkg.mod.T.rotate"]
    assert rotate.line == 8
    assert rotate.params == (("rot", ""), ("order", ""))
    assert rotate.param_details == (("rot", "positional", ""), ("order", "positional", "None"))
    members = sorted(name for name in symbols if name.startswith("pkg.mod.T."))
    assert members == ["pkg.mod.T.other", "pkg.mod.T.rotate"]  # one symbol per name


def test_overload_stubs_never_replace_the_implementation(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "from typing import overload\n\nclass T:\n"
        "    @overload\n    def get(self, key: int) -> int: ...\n"
        "    @overload\n    def get(self, key: str) -> str: ...\n"
        "    def get(self, key, default=None):\n        pass\n",
    )
    get = _symbols(tmp_path)["pkg.mod.T.get"]
    assert get.line == 8
    assert get.params == (("key", ""), ("default", ""))


def test_a_property_with_its_setter_stays_one_writable_property(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "class T:\n"
        "    @property\n    def size(self) -> int:\n        return 1\n\n"
        "    @size.setter\n    def size(self, value):\n        pass\n",
    )
    size = _symbols(tmp_path)["pkg.mod.T.size"]
    assert (size.kind, size.writable, size.return_type, size.line) == ("property", True, "int", 3)


def test_a_method_after_a_property_of_the_same_name_replaces_it(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "class T:\n    @property\n    def size(self):\n        return 1\n\n    def size(self, unit):\n        pass\n",
    )
    size = _symbols(tmp_path)["pkg.mod.T.size"]
    assert (size.kind, size.params) == ("method", (("unit", ""),))


def test_a_module_function_and_a_module_class_defined_twice_are_the_last_one(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "def run(a):\n    pass\n\ndef run(a, b=2):\n    pass\n\n"
        "class K:\n    def old(self):\n        pass\n\nclass K:\n    def new(self):\n        pass\n",
    )
    symbols = _symbols(tmp_path)
    assert symbols["pkg.mod.run"].params == (("a", ""), ("b", ""))
    assert symbols["pkg.mod.run"].line == 4
    assert "pkg.mod.K.new" in symbols and "pkg.mod.K.old" not in symbols


# --------------------------------------------------------------------------- name binding by import order (RD3-003)


THREE_D = {
    "__init__.py": "from .fbx.Opts import Opts\nfrom .Opts import Opts\n",
    "fbx/__init__.py": "",
    "fbx/Opts.py": "class Opts:\n    def __init__(self, fmt=None):\n        pass\n\n    def work(self):\n        pass\n",
    "Opts.py": 'class Opts:\n    """Placeholder."""\n',
}


def _three_d(root: Path) -> None:
    for relative, text in THREE_D.items():
        _write(root, f"pkg/{relative}", text)


def test_the_last_import_of_an_exported_name_wins(tmp_path: Path) -> None:
    _three_d(tmp_path)
    exported = _symbols(tmp_path)["pkg.Opts"]
    assert exported.reexported_from == "pkg.Opts.Opts"  # the later import, the placeholder


def test_the_class_the_name_replaced_keeps_its_own_module_name_and_is_marked(tmp_path: Path) -> None:
    _three_d(tmp_path)
    symbols = _symbols(tmp_path)
    assert symbols["pkg.fbx.Opts.Opts"].shadowed_by == "pkg.Opts"
    assert symbols["pkg.Opts"].shadows == "pkg.fbx.Opts.Opts"
    assert symbols["pkg.fbx.Opts.Opts.work"].kind == "method"


def test_the_exported_placeholder_is_not_given_the_other_classes_members(tmp_path: Path) -> None:
    for relative, text in THREE_D.items():
        _src(tmp_path, relative, text)
    entries = _entries(tmp_path)
    assert entries["pkg.Opts"]["shadows"] == "pkg.fbx.Opts.Opts"
    assert entries["pkg.Opts"]["methods"] == [] and entries["pkg.Opts"]["properties"] == []
    assert entries["pkg.Opts.Opts"]["methods"] == []
    assert entries["pkg.fbx.Opts.Opts"]["shadowed_by"] == "pkg.Opts"
    assert {method["name"] for method in entries["pkg.fbx.Opts.Opts"]["methods"]} == {"__init__", "work"}


def test_importing_the_same_class_twice_marks_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from .a import X\nfrom .a import X\n")
    _write(tmp_path, "pkg/a.py", "class X:\n    def m(self):\n        pass\n")
    symbols = _symbols(tmp_path)
    assert symbols["pkg.X"].shadows is None and symbols["pkg.a.X"].shadowed_by is None


def test_a_class_exported_under_a_name_nothing_else_took_is_not_marked(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from .a import X\nfrom .b import Y\n")
    _write(tmp_path, "pkg/a.py", "class X:\n    pass\n")
    _write(tmp_path, "pkg/b.py", "class Y:\n    pass\n")
    symbols = _symbols(tmp_path)
    assert all(symbol.shadows is None and symbol.shadowed_by is None for symbol in symbols.values())


def test_a_base_name_binds_to_the_class_its_module_imports_last(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/a.py", "class Base:\n    def from_a(self):\n        pass\n")
    _write(tmp_path, "pkg/b.py", "class Base:\n    def from_b(self):\n        pass\n")
    _write(tmp_path, "pkg/c.py", "from .a import Base\nfrom .b import Base\n\nclass Kid(Base):\n    pass\n")
    assert dict(_symbols(tmp_path)["pkg.c.Kid"].base_origins) == {"Base": "pkg.b.Base"}


# --------------------------------------------------------------------------- private bases (RD3-005)


def test_a_private_bases_public_members_are_listed_on_the_public_class(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "class _Base:\n    def bind(self, path=None):\n        pass\n\n    def _hidden(self):\n        pass\n\n"
        "class Facade(_Base):\n    def own(self):\n        pass\n",
    )
    symbols = _symbols(tmp_path)
    assert symbols["pkg.mod.Facade.bind"].inherited_from == "pkg.mod._Base"
    assert symbols["pkg.mod.Facade.own"].inherited_from is None
    assert "pkg.mod.Facade._hidden" not in symbols


def test_a_public_ancestor_behind_a_private_base_is_inherited_too(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "class Annotation:\n    @property\n    def color(self):\n        return 1\n\n"
        "class Markup(Annotation):\n    def popup(self):\n        pass\n\n"
        "class _Shape(Markup):\n    @property\n    def interior(self):\n        return 2\n\n"
        "class Circle(_Shape):\n    pass\n",
    )
    symbols = _symbols(tmp_path)
    assert symbols["pkg.mod.Circle.interior"].inherited_from == "pkg.mod._Shape"
    assert symbols["pkg.mod.Circle.popup"].inherited_from == "pkg.mod.Markup"
    assert symbols["pkg.mod.Circle.color"].inherited_from == "pkg.mod.Annotation"
    assert symbols["pkg.mod.Circle.color"].kind == "property"


def test_public_bases_are_flattened_with_the_class_the_source_names(tmp_path: Path) -> None:
    _src(tmp_path, "__init__.py", "from .shapes import Shape\nfrom .geo import Geo\n")
    _src(tmp_path, "shapes.py", "class Shape:\n    def move(self, dx, dy=0):\n        pass\n")
    _src(tmp_path, "geo.py", "from .shapes import Shape\n\nclass Geo(Shape):\n    pass\n")
    entries = _entries(tmp_path)
    for fqn in ("pkg.geo.Geo", "pkg.Geo"):
        move = _method(entries[fqn], "move")
        assert move["inherited_from"] == "pkg.shapes.Shape"
        assert move["params"][1] == {"name": "dy", "type": "", "default": "0"}


def test_two_classes_of_one_name_are_told_apart_by_the_import_not_the_bare_name(tmp_path: Path) -> None:
    _src(tmp_path, "__init__.py", "")
    _src(tmp_path, "a.py", "class Base:\n    def from_a(self):\n        pass\n")
    _src(tmp_path, "b.py", "class Base:\n    def from_b(self):\n        pass\n")
    _src(tmp_path, "c.py", "from .b import Base\n\nclass Kid(Base):\n    pass\n")
    kid = _entries(tmp_path)["pkg.c.Kid"]
    assert set(_methods(kid)) == {"from_b"}


def test_an_overriding_method_shows_its_own_signature_only(tmp_path: Path) -> None:
    _src(tmp_path, "__init__.py", "")
    _src(
        tmp_path,
        "mod.py",
        "class Base:\n    def save(self, path):\n        pass\n\n    def keep(self):\n        pass\n\n"
        "class Child(Base):\n    def save(self, path, *, overwrite=False):\n        pass\n",
    )
    child = _entries(tmp_path)["pkg.mod.Child"]
    saves = [method for method in child["methods"] if method["name"] == "save"]
    assert len(saves) == 1
    assert "inherited_from" not in saves[0]
    assert [param["name"] for param in saves[0]["params"]] == ["path", "overwrite"]
    assert saves[0]["params"][1] == {
        "name": "overwrite",
        "type": "",
        "default": "False",
        "kind": "keyword_only",
    }
    assert _method(child, "keep")["inherited_from"] == "pkg.mod.Base"


# --------------------------------------------------------------------------- built properties (RD3-008)


FACTORY = '''\
def _name_property(key: str, doc: str) -> property:
    """A property over one name entry."""

    def getter(self) -> str | None:
        return None

    def setter(self, value: str | None) -> None:
        pass

    return property(getter, setter, doc=doc)


def _read_only(key: str) -> property:
    def getter(self) -> int:
        return 1

    return property(getter)


class Annotation:
    icon = _name_property("Name", "Which icon a viewer draws.\\n\\nMore text.")
    count = _read_only("N")
    plain = property(lambda self: 1)
    both = property(lambda self: 1, lambda self, value: None)
    notprop = _unrelated(3)

    def get_size(self) -> float:
        return 1.0

    def set_size(self, value):
        pass

    size = property(get_size, set_size)
    area = property(get_size)


def _unrelated(x):
    return x
'''


def test_a_class_attribute_built_by_a_factory_is_a_property(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/mod.py", FACTORY)
    symbols = _symbols(tmp_path)
    icon = symbols["pkg.mod.Annotation.icon"]
    assert (icon.kind, icon.writable, icon.return_type) == ("property", True, "str | None")
    assert icon.docstring == "Which icon a viewer draws."
    count = symbols["pkg.mod.Annotation.count"]
    assert (count.kind, count.writable, count.return_type) == ("property", False, "int")


def test_property_calls_are_properties_typed_by_their_getter_and_writable_by_their_setter(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/mod.py", FACTORY)
    symbols = _symbols(tmp_path)
    assert symbols["pkg.mod.Annotation.plain"].writable is False
    assert symbols["pkg.mod.Annotation.both"].writable is True
    assert (symbols["pkg.mod.Annotation.size"].writable, symbols["pkg.mod.Annotation.size"].return_type) == (
        True,
        "float",
    )
    assert (symbols["pkg.mod.Annotation.area"].writable, symbols["pkg.mod.Annotation.area"].return_type) == (
        False,
        "float",
    )


def test_a_call_of_a_function_that_does_not_build_a_property_is_not_a_property(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/mod.py", FACTORY)
    assert "pkg.mod.Annotation.notprop" not in _symbols(tmp_path)


def test_factory_built_properties_reach_the_entry_and_a_subclass(tmp_path: Path) -> None:
    _src(tmp_path, "__init__.py", "")
    _src(tmp_path, "mod.py", FACTORY + "\n\nclass Sub(Annotation):\n    pass\n")
    entries = _entries(tmp_path)
    assert _properties(entries["pkg.mod.Annotation"])["icon"]["type"] == "str | None"
    assert _properties(entries["pkg.mod.Sub"])["icon"]["inherited_from"] == "pkg.mod.Annotation"


# --------------------------------------------------------------------------- the generated __init__ (RD3-006)


def _init_of(root: Path, source: str, name: str = "C") -> PublicSymbol:
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/mod.py", source)
    return _symbols(root)[f"pkg.mod.{name}.__init__"]


def test_a_dataclass_gets_the_init_python_generates(tmp_path: Path) -> None:
    init = _init_of(
        tmp_path, "from dataclasses import dataclass\n\n@dataclass\nclass C:\n    a: int\n    b: str = 'x'\n"
    )
    assert init.kind == "method"
    assert init.params == (("a", "int"), ("b", "str"))
    assert init.param_details == (("a", "positional", ""), ("b", "positional", "'x'"))
    assert init.signature == "def __init__(self, a: int, b: str = 'x')"


def test_dataclass_field_options_are_honoured(tmp_path: Path) -> None:
    source = (
        "import dataclasses\nfrom dataclasses import dataclass, field, KW_ONLY, InitVar\n"
        "from typing import ClassVar\n\n"
        "@dataclass\nclass C:\n"
        "    a: int\n"
        "    items: list = field(default_factory=list)\n"
        "    skipped: int = field(default=0, init=False)\n"
        "    LIMIT: ClassVar[int] = 3\n"
        "    seed: InitVar[int] = 1\n"
        "    _: KW_ONLY\n"
        "    mode: str = 'r'\n"
        "    flag: bool = dataclasses.field(default=True, kw_only=True)\n"
    )
    init = _init_of(tmp_path, source)
    assert [name for name, _ in init.params] == ["a", "items", "seed", "mode", "flag"]
    assert init.param_details == (
        ("a", "positional", ""),
        ("items", "positional", "<factory>"),
        ("seed", "positional", "1"),
        ("mode", "keyword_only", "'r'"),
        ("flag", "keyword_only", "True"),
    )
    assert dict(init.params)["seed"] == "int"  # InitVar[int] is an argument of type int
    assert init.signature == (
        "def __init__(self, a: int, items: list = <factory>, seed: int = 1, *, mode: str = 'r', flag: bool = True)"
    )


def test_a_keyword_only_dataclass_takes_every_field_by_keyword(tmp_path: Path) -> None:
    init = _init_of(
        tmp_path,
        "from dataclasses import dataclass\n\n@dataclass(kw_only=True)\nclass C:\n    a: int\n    b: int = 2\n",
    )
    assert init.param_details == (("a", "keyword_only", ""), ("b", "keyword_only", "2"))
    assert init.signature == "def __init__(self, *, a: int, b: int = 2)"


def test_the_fields_of_a_dataclass_base_come_first(tmp_path: Path) -> None:
    source = (
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass Base:\n    x: int\n    y: int = 0\n\n"
        "@dataclass\nclass C(Base):\n    z: str = ''\n    y: int = 5\n"
    )
    init = _init_of(tmp_path, source)
    assert init.params == (("x", "int"), ("y", "int"), ("z", "str"))
    assert init.param_details[1] == ("y", "positional", "5")  # redefined: its own default, original place


def test_a_dataclass_with_its_own_init_or_init_false_gets_no_generated_one(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass Own:\n    a: int\n\n    def __init__(self, a, extra=1):\n        self.a = a\n\n"
        "@dataclass(init=False)\nclass NoInit:\n    a: int\n",
    )
    symbols = _symbols(tmp_path)
    assert symbols["pkg.mod.Own.__init__"].params == (("a", ""), ("extra", ""))
    assert "pkg.mod.NoInit.__init__" not in symbols


def test_a_named_tuple_gets_its_constructor(tmp_path: Path) -> None:
    init = _init_of(
        tmp_path,
        "from typing import NamedTuple\n\nclass C(NamedTuple):\n    a: int\n    b: str = 'x'\n",
    )
    assert init.signature == "def __init__(self, a: int, b: str = 'x')"


def test_a_plain_class_gets_no_generated_init(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/mod.py", "class C:\n    a: int = 1\n")
    assert "pkg.mod.C.__init__" not in _symbols(tmp_path)


def test_a_frozen_dataclass_has_read_only_fields_and_a_plain_one_writable_fields(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\nclass F:\n    a: int = 1\n\n"
        "@dataclass\nclass W:\n    a: int = 1\n",
    )
    symbols = _symbols(tmp_path)
    assert symbols["pkg.mod.F.a"].writable is False
    assert symbols["pkg.mod.W.a"].writable is True


def test_literals_assigned_to_attributes_are_carried(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "class C:\n    MAX = 5\n    NAME = 'n'\n    NONE = None\n    size: int = 3\n\n"
        "    def __init__(self):\n        self.count = 0\n        self.other = make()\n        self.flag: bool = True\n",
    )
    values = {
        name.rsplit(".", 1)[1]: symbol.value
        for name, symbol in _symbols(tmp_path).items()
        if symbol.kind == "property"
    }
    assert values == {
        "MAX": "5",
        "NAME": "'n'",
        "NONE": "None",
        "size": "3",
        "count": "0",
        "other": None,
        "flag": "True",
    }


# --------------------------------------------------------------------------- the scan itself


def test_a_scan_is_deterministic_and_leaves_no_cache_behind(tmp_path: Path) -> None:
    _three_d(tmp_path)
    first = inspect_public_surface(tmp_path, ["pkg"])
    second = inspect_public_surface(tmp_path, ["pkg"])
    assert first == second
    assert python_surface._PARSE_CACHE is None
