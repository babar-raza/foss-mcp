"""G3/TC-844 (REQ-G3-012): the python stub flag is not raised for an interface contract or a shadowed member.

Regenerating slides/python's extraction with the TC-559 rules gave 3091 ``stub: true`` members where the
committed fixture has none: 917 were ``...`` methods of ``I*(ABC)`` interface classes, which declare a contract
the way an ``@abstractmethod`` does, and 2171 were copies of such members (or of an interface's
``raise NotImplementedError``) onto a concrete class such as ``Chart(IChart, GraphicalObject)``, although Python
resolves the name through ``GraphicalObject`` -> ``Shape`` to a real implementation. Measured over the pinned
clone of every python pilot (11), in-process, ``run_extraction._extract_python_surface``: slides 3091 -> 3
(its 3 real own stubs: 2 ``pass``, 1 ``raise NotImplementedError``); the other ten pilots are unchanged.

The expectations are the language's own rules, written as small hand-authored libraries: no previous run of
the extractor and no committed fixture is consulted.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import python_surface


def _entries(root: Path, body: str) -> dict[str, dict[str, Any]]:
    package = root / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mod.py").write_text(body, encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\n', encoding="utf-8")
    types, _unresolved = run_extraction._extract_python_surface(root)
    return {entry["name"]: entry for entry in types}


def _stubs(entry: dict[str, Any]) -> dict[str, bool]:
    """``{member name: flagged}`` for every method and property of *entry*, constructors left out."""
    members = [*entry.get("methods", []), *entry.get("properties", [])]
    return {member["name"]: member.get("stub") is True for member in members if member["name"] != "__init__"}


def _reason(entry: dict[str, Any], name: str) -> str:
    members = [*entry.get("methods", []), *entry.get("properties", [])]
    return next(member for member in members if member["name"] == name).get("stub_reason", "")


# ------------------------------------------------------------------------- (a) the interface contract


def test_an_ellipsis_method_of_an_abc_interface_is_a_contract_not_a_stub(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "from abc import ABC\n"
        "class IShape(ABC):\n"
        "    def area(self) -> float: ...\n"
        "    def name(self, unit):\n"
        '        """Documented."""\n'
        "        ...\n"
        "    @property\n"
        "    def width(self) -> int: ...\n",
    )
    assert _stubs(entries["IShape"]) == {"area": False, "name": False, "width": False}


def test_the_dotted_abc_base_and_the_abcmeta_metaclass_make_an_interface_too(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "import abc\n"
        "from abc import ABCMeta\n"
        "class IDotted(abc.ABC):\n"
        "    def run(self): ...\n"
        "class IMeta(metaclass=ABCMeta):\n"
        "    def run(self): ...\n"
        "class IDottedMeta(metaclass=abc.ABCMeta):\n"
        "    def run(self): ...\n"
        "class IMixed(object, abc.ABC):\n"
        "    def run(self): ...\n",
    )
    for name in ("IDotted", "IMeta", "IDottedMeta", "IMixed"):
        assert _stubs(entries[name]) == {"run": False}, name


def test_a_protocol_class_stays_a_contract(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "import typing\n"
        "from typing import Protocol\n"
        "class Reader(Protocol):\n"
        "    def read(self) -> bytes: ...\n"
        "class Writer(typing.Protocol):\n"
        "    def write(self, data: bytes) -> None: ...\n",
    )
    assert _stubs(entries["Reader"]) == {"read": False}
    assert _stubs(entries["Writer"]) == {"write": False}


def test_an_ellipsis_or_pass_body_in_an_ordinary_concrete_class_is_still_a_stub(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "class Shape:\n"
        "    def area(self): ...\n"
        "    def volume(self):\n"
        "        pass\n"
        "class Circle(Shape):\n"
        "    def radius(self): ...\n",
    )
    assert _stubs(entries["Shape"]) == {"area": True, "volume": True}
    assert _reason(entries["Shape"], "area") == "empty body (...)"
    assert _reason(entries["Shape"], "volume") == "empty body (pass)"
    assert _stubs(entries["Circle"])["radius"] is True


def test_a_concrete_subclass_of_an_abc_interface_is_not_itself_an_interface(tmp_path: Path) -> None:
    """Only a DIRECT ``ABC`` / ``Protocol`` base or ``metaclass=ABCMeta`` makes the contract: an
    implementation that derives from an interface and leaves ``...`` in a method has not implemented it."""
    entries = _entries(
        tmp_path,
        "from abc import ABC\n"
        "class IShape(ABC):\n"
        "    def area(self): ...\n"
        "class Square(IShape):\n"
        "    def area(self): ...\n"
        "    def side(self): ...\n",
    )
    assert _stubs(entries["IShape"]) == {"area": False}
    assert _stubs(entries["Square"]) == {"area": True, "side": True}


def test_pass_and_not_implemented_error_in_an_abc_class_stay_stubs(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "from abc import ABC\n"
        "class Device(ABC):\n"
        "    def flush(self):\n"
        "        pass\n"
        "    def open(self):\n"
        "        raise NotImplementedError\n"
        '    def close(self):\n        raise NotImplementedError("subclass")\n'
        "    def ready(self): ...\n",
    )
    assert _stubs(entries["Device"]) == {"flush": True, "open": True, "close": True, "ready": False}
    assert _reason(entries["Device"], "open") == "raises NotImplementedError"


def test_abstractmethod_is_unchanged(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "import abc\n"
        "class Base(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def one(self):\n"
        "        raise NotImplementedError\n"
        "    @abc.abstractmethod\n"
        "    def two(self):\n"
        "        pass\n"
        "class Plain:\n"
        "    @abc.abstractmethod\n"
        "    def three(self): ...\n",
    )
    assert _stubs(entries["Base"]) == {"one": False, "two": False}
    assert _stubs(entries["Plain"]) == {"three": False}


def test_stub_reason_judges_the_owner_class_directly() -> None:
    def function(source: str) -> ast.FunctionDef:
        node = ast.parse(source).body[0]
        assert isinstance(node, ast.FunctionDef)
        return node

    interface = ast.parse("class I(ABC):\n    pass\n").body[0]
    concrete = ast.parse("class C(Base):\n    pass\n").body[0]
    assert isinstance(interface, ast.ClassDef) and isinstance(concrete, ast.ClassDef)
    method = function("def f(self): ...\n")
    assert python_surface.stub_reason(method, interface) == ""
    assert python_surface.stub_reason(method, concrete) == "empty body (...)"
    assert python_surface.stub_reason(method) == "empty body (...)"
    raising = function("def f(self):\n    raise NotImplementedError\n")
    assert python_surface.stub_reason(raising, interface) == "raises NotImplementedError"


# ------------------------------------------------------------------- (b) the inherited member's flag


def test_an_interface_ellipsis_member_inherited_by_an_implementation_is_not_flagged(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "from abc import ABC\n"
        "class IShape(ABC):\n"
        "    def area(self): ...\n"
        "class IGraphic(IShape, ABC):\n"
        "    def paint(self): ...\n"
        "class Square(IGraphic):\n"
        "    def side(self):\n"
        "        return 1\n",
    )
    flags = _stubs(entries["Square"])
    assert flags == {"area": False, "paint": False, "side": False}
    inherited = {
        m["name"]: m["inherited_from"] for m in entries["Square"]["methods"] if "inherited_from" in m
    }
    assert set(inherited) == {"area", "paint"}


_DIAMOND = (
    "from abc import ABC\n"
    "class IShape(ABC):\n"
    "    def width(self):\n"
    "        raise NotImplementedError\n"
    "    def height(self):\n"
    "        raise NotImplementedError\n"
    "class Shape(IShape):\n"
    "    def width(self):\n"
    "        return 1\n"
    "class IGraphic(IShape, ABC):\n"
    "    def paint(self):\n"
    "        raise NotImplementedError\n"
    "class IChart(IGraphic, ABC):\n"
    "    def series(self):\n"
    "        raise NotImplementedError\n"
    "class Graphic(Shape, IGraphic, ABC):\n"
    "    def paint(self):\n"
    "        return 2\n"
    "class Chart(IChart, Graphic):\n"
    "    def render(self):\n"
    "        return 3\n"
)


def test_a_concrete_implementation_reached_through_another_base_wins_over_an_interface_stub(
    tmp_path: Path,
) -> None:
    """``Chart``'s MRO is Chart, IChart, Graphic, Shape, IGraphic, IShape: ``IChart`` is a base before
    ``Graphic``, so the members it flattened (``IShape.width``, ``IGraphic.paint``) used to reach ``Chart``
    flagged although ``Shape.width`` and ``Graphic.paint`` are real."""
    chart = _entries(tmp_path, _DIAMOND)["Chart"]
    flags = _stubs(chart)
    assert flags["width"] is False  # Shape.width is real and precedes IShape in the MRO
    assert flags["paint"] is False  # Graphic.paint is real and precedes IGraphic
    assert flags["height"] is True  # only IShape defines it, and its body is a stub
    assert flags["series"] is True  # IChart is the first, and only, definition and it is a stub
    assert flags["render"] is False


def test_the_first_definition_in_mro_order_decides_when_it_is_a_stub(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "class StubBase:\n"
        "    def load(self):\n"
        "        pass\n"
        "    def save(self):\n"
        "        return 1\n"
        "class RealBase:\n"
        "    def load(self):\n"
        "        return 1\n"
        "    def save(self):\n"
        "        pass\n"
        "class Both(StubBase, RealBase):\n"
        "    pass\n",
    )
    flags = _stubs(entries["Both"])
    assert flags["load"] is True  # StubBase.load comes first and is a stub: the stub flag is carried
    assert flags["save"] is False  # StubBase.save comes first and is real: RealBase's stub is never copied


def test_a_member_the_class_overrides_is_not_inherited_and_keeps_its_own_flag(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "class Base:\n"
        "    def run(self):\n"
        "        pass\n"
        "class Child(Base):\n"
        "    def run(self):\n"
        "        return 1\n"
        "class Lazy(Base):\n"
        "    def run(self):\n"
        "        raise NotImplementedError\n",
    )
    assert _stubs(entries["Child"]) == {"run": False}
    assert _stubs(entries["Lazy"]) == {"run": True}
    assert "inherited_from" not in entries["Child"]["methods"][0]


def test_an_inherited_property_follows_the_same_rule(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        "from abc import ABC\n"
        "class IBox(ABC):\n"
        "    @property\n"
        "    def size(self):\n"
        "        raise NotImplementedError\n"
        "class Box(IBox):\n"
        "    @property\n"
        "    def size(self):\n"
        "        return 1\n"
        "class Crate(IBox):\n"
        "    pass\n",
    )
    assert entries["Crate"]["properties"][0]["stub"] is True
    assert entries["Crate"]["properties"][0]["inherited_from"].endswith("IBox")
    assert entries["Box"]["properties"][0].get("stub") is None


# ------------------------------------------------------------------------------ the C3 linearization


@pytest.mark.parametrize(
    ("parents", "expected"),
    [
        ({"A": []}, ["A"]),
        ({"A": ["B"], "B": ["C"], "C": []}, ["A", "B", "C"]),
        # the classic diamond: D(B, C), B(A), C(A) -> D B C A
        ({"D": ["B", "C"], "B": ["A"], "C": ["A"], "A": []}, ["D", "B", "C", "A"]),
        # Chart(IChart, Graphic) with Graphic(Shape, IGraphic) and IChart(IGraphic)
        (
            {
                "Chart": ["IChart", "Graphic"],
                "IChart": ["IGraphic"],
                "Graphic": ["Shape", "IGraphic"],
                "Shape": ["IShape"],
                "IGraphic": ["IShape"],
                "IShape": [],
            },
            ["Chart", "IChart", "Graphic", "Shape", "IGraphic", "IShape"],
        ),
    ],
)
def test_c3_linearization_matches_pythons_method_resolution_order(
    parents: dict[str, list[str]], expected: list[str]
) -> None:
    assert run_extraction._c3_linearization(expected[0], parents, {}) == expected


def test_c3_linearization_terminates_on_a_cycle_and_an_inconsistent_order() -> None:
    cycle = {"A": ["B"], "B": ["A"]}
    assert set(run_extraction._c3_linearization("A", cycle, {})) == {"A", "B"}
    # X(B, A) with A(B) has no consistent order (TypeError in Python): any stable order will do.
    inconsistent = {"X": ["B", "A"], "A": ["B"], "B": []}
    order = run_extraction._c3_linearization("X", inconsistent, {})
    assert order[0] == "X" and set(order) == {"X", "A", "B"}
