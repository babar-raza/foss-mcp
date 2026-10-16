"""TC-545: public literal class-body assignments are published as read-only ``property`` members.

3d/python writes every enum-like class as ``class Axis:`` with one ``NAME = None`` line per
constant; before this card the pure-``ast`` reader dropped all of them, so ``get_symbol`` answered
a class with no members. Offline: synthetic packages in ``tmp_path`` driven through the real
``inspect_public_surface`` and ``_python_types_from_surface``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import python_surface
from foss_mcp.extraction.tree_sitter_engine.python_surface import PublicSymbol, inspect_public_surface
from foss_mcp.indexing import chunk_builder

AXIS = '''\
class Axis:
    """
    Axis
    """

    NEGATIVE_X_AXIS = None
    """
    Negative X axis
    """

    NEGATIVE_Y_AXIS = None
    """
    Negative Y axis
    """

    NEGATIVE_Z_AXIS = None
    """
    Negative Z axis
    """

    X_AXIS = None
    """
    X axis
    """

    Y_AXIS = None
    """
    Y axis
    """

    Z_AXIS = None
    """
    Z axis
    """


Axis.NEGATIVE_X_AXIS = Axis()
Axis.NEGATIVE_Y_AXIS = Axis()
Axis.NEGATIVE_Z_AXIS = Axis()
Axis.X_AXIS = Axis()
Axis.Y_AXIS = Axis()
Axis.Z_AXIS = Axis()
'''

MAPPING_MODE = '''\
class MappingMode:
    """
    Mapping mode of a vertex element.
    """

    CONTROL_POINT = 0
    """Mapped to the control points."""

    POLYGON_VERTEX = 1
    """Mapped to the polygon vertices."""

    POLYGON = 2
    """Mapped to the polygons."""

    EDGE = 3
    """Mapped to the edges."""

    UNKNOWN = 4
    """Unknown mapping."""
'''


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _package(root: Path, module: str, text: str) -> None:
    _write(root, "pkg/__init__.py", "")
    _write(root, f"pkg/{module}.py", text)


def _symbols(root: Path) -> dict[str, PublicSymbol]:
    return {symbol.qualified_name: symbol for symbol in inspect_public_surface(root, ["pkg"]).symbols}


def _types(root: Path) -> dict[str, dict[str, Any]]:
    surface = inspect_public_surface(root, ["pkg"])
    return {entry["name"]: entry for entry in run_extraction._python_types_from_surface(surface)}


def _properties(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {prop["name"]: prop for prop in entry["properties"]}


def test_a_none_constant_is_a_read_only_property_without_a_type(tmp_path: Path) -> None:
    _package(tmp_path, "axis", AXIS)
    props = _properties(_types(tmp_path)["Axis"])
    assert set(props) == {
        "NEGATIVE_X_AXIS",
        "NEGATIVE_Y_AXIS",
        "NEGATIVE_Z_AXIS",
        "X_AXIS",
        "Y_AXIS",
        "Z_AXIS",
    }
    for prop in props.values():
        assert prop["type"] == ""
        assert prop["writable"] is False
    symbol = _symbols(tmp_path)["pkg.axis.Axis.X_AXIS"]
    assert symbol.kind == "property"
    assert symbol.writable is False
    assert symbol.return_type is None


def test_the_upstream_mapping_mode_file_lists_its_five_constants(tmp_path: Path) -> None:
    _package(tmp_path, "mapping", MAPPING_MODE)
    props = _properties(_types(tmp_path)["MappingMode"])
    assert set(props) == {"CONTROL_POINT", "POLYGON_VERTEX", "POLYGON", "EDGE", "UNKNOWN"}
    assert {prop["type"] for prop in props.values()} == {"int"}
    assert props["EDGE"]["writable"] is False


@pytest.mark.parametrize(
    ("assignment", "type_name"),
    [
        ("MAX = 5", "int"),
        ("RATIO = 0.5", "float"),
        ('NAME = "x"', "str"),
        ("FLAG = True", "bool"),
        ('RAW = b"x"', "bytes"),
        ("PAIR = (1, 2)", "tuple"),
        ("ITEMS = [1]", "list"),
        ('MAP = {"a": 1}', "dict"),
        ("NEG = -1", "int"),
    ],
)
def test_literal_constants_carry_their_type(tmp_path: Path, assignment: str, type_name: str) -> None:
    _package(tmp_path, "mod", f"class K:\n    {assignment}\n")
    name = assignment.split("=")[0].strip()
    prop = _properties(_types(tmp_path)["K"])[name]
    assert prop["type"] == type_name
    assert prop["writable"] is False


def test_private_and_dunder_names_are_not_constants(tmp_path: Path) -> None:
    _package(tmp_path, "mod", "class K:\n    _HIDDEN = 1\n    __slots__ = ()\n    VISIBLE = 2\n")
    assert list(_properties(_types(tmp_path)["K"])) == ["VISIBLE"]


def test_a_call_a_name_and_an_attribute_are_not_constants(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "mod",
        "import os\n\nother = 1\n\nclass Foo:\n    pass\n\n"
        "class K:\n    DEFAULT = Foo()\n    ALIAS = other\n    MODE = os.sep\n    REAL = 1\n",
    )
    assert list(_properties(_types(tmp_path)["K"])) == ["REAL"]


def test_chained_and_tuple_targets_are_not_constants(tmp_path: Path) -> None:
    _package(tmp_path, "mod", "class K:\n    A = B = 1\n    C, D = 1, 2\n    E = 3\n")
    assert list(_properties(_types(tmp_path)["K"])) == ["E"]


def test_an_enum_subclass_gets_no_constant_properties_and_keeps_its_members(tmp_path: Path) -> None:
    _package(tmp_path, "colors", "import enum\n\nclass Color(enum.Enum):\n    RED = 1\n    GREEN = 2\n")
    types = list(_types(tmp_path).values())
    run_extraction.fill_python_enum_members(types, tmp_path)
    color = next(entry for entry in types if entry["name"] == "Color")
    assert color["kind"] == "enum"
    assert color["properties"] == []
    assert [member["name"] for member in color["enum_members"]] == ["RED", "GREEN"]


def test_a_constant_named_like_a_method_or_a_property_getter_is_not_repeated(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "mod",
        "class K:\n"
        "    SAVE = 1\n"
        "    TITLE = 2\n"
        "    OTHER = 3\n\n"
        "    def SAVE(self):\n        pass\n\n"
        "    @property\n    def TITLE(self) -> str:\n        return ''\n",
    )
    entry = _types(tmp_path)["K"]
    props = _properties(entry)
    assert [prop["name"] for prop in entry["properties"]].count("TITLE") == 1
    assert props["TITLE"]["type"] == "str"
    assert "SAVE" not in props
    assert {method["name"] for method in entry["methods"]} == {"SAVE"}
    assert props["OTHER"]["type"] == "int"


def test_a_class_with_methods_lists_its_constants_in_source_order_before_init_attributes(
    tmp_path: Path,
) -> None:
    _package(
        tmp_path,
        "mod",
        "class K:\n"
        "    FIRST = 1\n"
        "    label: str = ''\n"
        "    SECOND = 'b'\n\n"
        "    def __init__(self):\n        self.third = 3\n\n"
        "    def run(self):\n        pass\n",
    )
    entry = _types(tmp_path)["K"]
    # The surface and run_extraction both sort by name, so the reader's own order is read off the
    # private helper: class-body fields and constants in source order, then ``__init__`` attributes.
    source = (tmp_path / "pkg" / "mod.py").read_text(encoding="utf-8")
    node = next(item for item in ast.parse(source).body if isinstance(item, ast.ClassDef))
    symbols = python_surface._attribute_members(node, "pkg.mod", "pkg/mod.py", "name", set())
    assert [symbol.name for symbol in symbols] == ["FIRST", "label", "SECOND", "third"]
    assert [symbol.line for symbol in symbols] == [2, 3, 4, 7]
    props = _properties(entry)
    assert (props["FIRST"]["writable"], props["label"]["writable"]) == (False, True)
    assert (props["SECOND"]["writable"], props["third"]["writable"]) == (False, True)
    assert {method["name"] for method in entry["methods"]} >= {"run"}


def test_an_annotated_field_keeps_its_annotation_and_stays_writable(tmp_path: Path) -> None:
    _package(tmp_path, "mod", "class K:\n    SIZE: int = 0\n    LIMIT = 9\n")
    props = _properties(_types(tmp_path)["K"])
    assert props["SIZE"]["type"] == "int"
    assert props["SIZE"]["writable"] is True
    assert props["LIMIT"]["writable"] is False


def test_an_annotated_field_is_not_made_read_only_by_a_later_assignment_of_the_same_name(
    tmp_path: Path,
) -> None:
    _package(tmp_path, "mod", "class K:\n    SIZE: int = 0\n    SIZE = 5\n")
    entry = _types(tmp_path)["K"]
    assert [prop["name"] for prop in entry["properties"]] == ["SIZE"]
    assert entry["properties"][0]["type"] == "int"
    assert entry["properties"][0]["writable"] is True


def test_classvar_stays_excluded(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "mod",
        "from typing import ClassVar\n\nclass K:\n    LIMIT: ClassVar[int] = 3\n    PLAIN = 4\n",
    )
    assert list(_properties(_types(tmp_path)["K"])) == ["PLAIN"]


def test_constants_reach_the_fixture_dict_as_read_only_properties_with_their_type(tmp_path: Path) -> None:
    _package(tmp_path, "mod", "class K:\n    MAX = 5\n    NONE = None\n")
    entry = _types(tmp_path)["K"]
    assert [(prop["name"], prop["type"], prop["writable"]) for prop in entry["properties"]] == [
        ("MAX", "int", False),
        ("NONE", "", False),
    ]
    assert set(entry["properties"][0]) >= {"name", "type", "writable", "doc", "file", "line"}
    assert entry["properties"][0]["line"] == 2


def test_the_chunk_renders_a_constant_as_a_read_only_property_line(tmp_path: Path) -> None:
    _package(tmp_path, "mod", "class K:\n    X_AXIS = None\n    MAX = 5\n")
    entry = _types(tmp_path)["K"]
    text = chunk_builder._format_type_text(entry, fqn=entry["class_import"])
    assert "  - X_AXIS (read-only)" in text.splitlines()
    assert "  - MAX: int (read-only)" in text.splitlines()


def test_a_class_that_had_no_member_now_has_properties_and_so_is_not_member_less(tmp_path: Path) -> None:
    _package(tmp_path, "axis", AXIS)
    entry = _types(tmp_path)["Axis"]
    assert entry["methods"] == []
    assert chunk_builder._has_members(entry)
    text = chunk_builder._format_type_text(entry, fqn=entry["class_import"])
    assert "Properties:" in text
    assert "  - Z_AXIS (read-only)" in text


def test_nested_classes_do_not_leak_constants_into_the_outer_class(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "mod",
        "class Outer:\n    OUTER = 1\n\n    class Inner:\n        INNER = 2\n",
    )
    outer = _types(tmp_path)["Outer"]
    assert list(_properties(outer)) == ["OUTER"]


def test_a_module_level_constant_is_not_a_class_member(tmp_path: Path) -> None:
    _package(tmp_path, "mod", "TOP = 1\n\nclass K:\n    INNER = 2\n")
    assert list(_properties(_types(tmp_path)["K"])) == ["INNER"]


def test_a_non_literal_container_is_not_a_constant(tmp_path: Path) -> None:
    _package(tmp_path, "mod", "class K:\n    ITEMS = [1, make()]\n    OK = (1, 2)\n")
    assert list(_properties(_types(tmp_path)["K"])) == ["OK"]


def test_run_extraction_reaches_the_new_code() -> None:
    assert "python_surface.inspect_public_surface" in inspect.getsource(
        run_extraction._extract_python_surface
    )
    assert "_attribute_members(" in inspect.getsource(python_surface._methods)
    assert "_constant_type_text(" in inspect.getsource(python_surface._attribute_members)
