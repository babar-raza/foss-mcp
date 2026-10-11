"""TC-393: the Python reader keeps how to construct and call the API.

``__init__`` is the constructor, every declared parameter is kept (type empty when
unannotated, ``self``/``cls`` excluded), and an annotated ``@property`` getter is a property
with its type and writability. Offline: synthetic packages in ``tmp_path``, no clone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine.python_surface import (
    PublicSurface,
    PublicSymbol,
    inspect_public_surface,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _symbols(root: Path) -> dict[str, PublicSymbol]:
    return {symbol.qualified_name: symbol for symbol in inspect_public_surface(root, ["pkg"]).symbols}


def _types(root: Path) -> dict[str, dict[str, Any]]:
    surface = inspect_public_surface(root, ["pkg"])
    return {entry["name"]: entry for entry in run_extraction._python_types_from_surface(surface)}


CONSTRUCTED = (
    "class Document:\n"
    "    '''A document.'''\n\n"
    "    def __init__(self, source=None, load_options: LoadOptions | None = None, *args, **kwargs):\n"
    "        '''Open a document.'''\n\n"
    "    def __repr__(self):\n        return ''\n\n"
    "    def _hidden(self):\n        pass\n\n"
    "    def save(self, path: str) -> None:\n        pass\n"
)


def test_init_is_extracted_as_a_method_symbol_named_under_its_class(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/doc.py", CONSTRUCTED)
    init = _symbols(tmp_path)["pkg.doc.Document.__init__"]
    assert init.kind == "method"
    assert init.name == "__init__"
    assert init.docstring == "Open a document."


def test_init_keeps_all_four_params_in_order_with_types_or_empty(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/doc.py", CONSTRUCTED)
    init = _symbols(tmp_path)["pkg.doc.Document.__init__"]
    assert init.params == (
        ("source", ""),
        ("load_options", "LoadOptions | None"),
        ("*args", ""),
        ("**kwargs", ""),
    )
    # param_types keeps its old meaning: annotated parameters only.
    assert init.param_types == (("load_options", "LoadOptions | None"),)


def test_init_is_a_constructor_with_every_param_through_the_adapter(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/doc.py", CONSTRUCTED)
    document = _types(tmp_path)["Document"]
    methods = {method["name"]: method for method in document["methods"]}
    init = methods["__init__"]
    assert init["is_constructor"] is True
    assert init["params"] == [
        {"name": "source", "type": ""},
        {"name": "load_options", "type": "LoadOptions | None"},
        {"name": "*args", "type": ""},
        {"name": "**kwargs", "type": ""},
    ]
    assert methods["save"]["is_constructor"] is False


def test_other_dunders_and_private_methods_stay_excluded(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/doc.py", CONSTRUCTED)
    names = set(_symbols(tmp_path))
    assert "pkg.doc.Document.__repr__" not in names
    assert "pkg.doc.Document._hidden" not in names
    assert {"pkg.doc.Document.__init__", "pkg.doc.Document.save"} <= names


def test_unannotated_parameters_are_kept_not_dropped(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/shapes.py",
        "class IShapeCollection:\n"
        "    def add_auto_shape(self, shape_type, x, y, width, height):\n        pass\n",
    )
    symbol = _symbols(tmp_path)["pkg.shapes.IShapeCollection.add_auto_shape"]
    assert symbol.params == (("shape_type", ""), ("x", ""), ("y", ""), ("width", ""), ("height", ""))
    assert symbol.param_types == ()
    method = _types(tmp_path)["IShapeCollection"]["methods"][0]
    assert [param["name"] for param in method["params"]] == ["shape_type", "x", "y", "width", "height"]
    assert {param["type"] for param in method["params"]} == {""}


def test_cls_is_excluded_on_a_classmethod_and_self_on_a_method(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/shapes.py",
        "class Shape:\n"
        "    @classmethod\n    def load(cls, path: str, strict=False):\n        pass\n\n"
        "    def area(self, scale: float = 1.0) -> float:\n        return scale\n",
    )
    by_name = _symbols(tmp_path)
    assert by_name["pkg.shapes.Shape.load"].params == (("path", "str"), ("strict", ""))
    assert by_name["pkg.shapes.Shape.area"].params == (("scale", "float"),)


def test_positional_only_and_keyword_only_parameters_keep_their_order(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/shapes.py",
        "class Shape:\n"
        "    def mix(self, a: int, /, b, *rest: str, c: bool = True, d=None, **extra: int):\n"
        "        pass\n",
    )
    mix = _symbols(tmp_path)["pkg.shapes.Shape.mix"]
    assert mix.params == (
        ("a", "int"),
        ("b", ""),
        ("*rest", "str"),
        ("c", "bool"),
        ("d", ""),
        ("**extra", "int"),
    )


def test_a_method_with_only_self_has_no_params(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/shapes.py", "class Shape:\n    def touch(self):\n        pass\n")
    assert _symbols(tmp_path)["pkg.shapes.Shape.touch"].params == ()


def test_an_annotated_property_getter_is_a_property_not_a_method(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mail.py",
        "class MapiMessage:\n"
        "    @property\n    def subject(self) -> str:\n        '''The subject.'''\n        return ''\n\n"
        "    def send(self) -> None:\n        pass\n",
    )
    subject = _symbols(tmp_path)["pkg.mail.MapiMessage.subject"]
    assert subject.kind == "property"
    assert subject.return_type == "str"
    entry = _types(tmp_path)["MapiMessage"]
    assert [method["name"] for method in entry["methods"]] == ["send"]
    assert len(entry["properties"]) == 1
    assert entry["properties"][0]["name"] == "subject"
    assert entry["properties"][0]["type"] == "str"
    assert entry["properties"][0]["doc"] == "The subject."
    assert entry["properties"][0]["file"] == "pkg/mail.py"


def test_a_property_is_writable_only_with_a_setter(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mail.py",
        "class MapiMessage:\n"
        "    @property\n    def subject(self) -> str:\n        return ''\n\n"
        "    @subject.setter\n    def subject(self, value: str) -> None:\n        pass\n\n"
        "    @property\n    def sender(self) -> str:\n        return ''\n",
    )
    by_name = _symbols(tmp_path)
    assert by_name["pkg.mail.MapiMessage.subject"].writable is True
    assert by_name["pkg.mail.MapiMessage.sender"].writable is False
    properties = {prop["name"]: prop for prop in _types(tmp_path)["MapiMessage"]["properties"]}
    assert properties["subject"]["writable"] is True
    assert properties["sender"]["writable"] is False
    # The setter is not a second member.
    assert [name for name in by_name if name.endswith(".subject")] == ["pkg.mail.MapiMessage.subject"]


def test_cached_property_and_functools_cached_property_are_properties(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mail.py",
        "import functools\nfrom functools import cached_property\n\n\n"
        "class Folder:\n"
        "    @cached_property\n    def count(self) -> int:\n        return 0\n\n"
        "    @functools.cached_property\n    def label(self) -> str:\n        return ''\n",
    )
    by_name = _symbols(tmp_path)
    assert by_name["pkg.mail.Folder.count"].kind == "property"
    assert by_name["pkg.mail.Folder.label"].kind == "property"
    assert by_name["pkg.mail.Folder.count"].writable is False
    properties = {prop["name"]: prop["type"] for prop in _types(tmp_path)["Folder"]["properties"]}
    assert properties == {"count": "int", "label": "str"}


def test_an_ordinary_decorated_method_is_not_a_property(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/shapes.py",
        "class Shape:\n    @staticmethod\n    def make(size: int) -> int:\n        return size\n",
    )
    make = _symbols(tmp_path)["pkg.shapes.Shape.make"]
    assert make.kind == "method"
    assert make.writable is None


def test_an_init_reexported_through_a_package_init_is_recovered_from_its_origin(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "pkg/__init__.py", "from ._document import Document\n")
    _write(
        tmp_path,
        "pkg/_document.py",
        "class Document:\n"
        "    def __init__(self, source=None, options: str = 'x'):\n        pass\n\n"
        "    @property\n    def title(self) -> str:\n        return ''\n",
    )
    by_name = _symbols(tmp_path)
    init = by_name["pkg.Document.__init__"]
    assert init.kind == "method"
    assert init.module == "pkg"
    assert init.params == (("source", ""), ("options", "str"))
    assert by_name["pkg.Document.title"].kind == "property"
    entry = _types(tmp_path)["Document"]
    assert [method["name"] for method in entry["methods"] if method["is_constructor"]] == ["__init__"]
    assert [prop["name"] for prop in entry["properties"]] == ["title"]


def test_a_reexported_function_carries_every_declared_param(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from ._helpers import make\n")
    _write(tmp_path, "pkg/_helpers.py", "def make(name, size: int = 1, *flags):\n    return name\n")
    assert _symbols(tmp_path)["pkg.make"].params == (("name", ""), ("size", "int"), ("*flags", ""))


def _symbol(qualified_name: str, kind: str, **fields: Any) -> PublicSymbol:
    module = qualified_name.rsplit(".", 1)[0] if kind != "method" else qualified_name.rsplit(".", 2)[0]
    return PublicSymbol(
        qualified_name,
        module,
        qualified_name.rsplit(".", 1)[-1],
        kind,  # type: ignore[arg-type]
        "pkg/mod.py",
        1,
        "name",
        **fields,
    )


def test_the_adapter_falls_back_to_param_types_when_params_is_empty() -> None:
    surface = PublicSurface(
        symbols=(
            _symbol("pkg.mod.Shape", "class"),
            _symbol("pkg.mod.Shape.scale", "method", param_types=(("factor", "float"),)),
        ),
        unresolved=(),
    )
    (entry,) = run_extraction._python_types_from_surface(surface)
    assert entry["methods"][0]["params"] == [{"name": "factor", "type": "float"}]


def test_centrality_still_ignores_empty_types_and_counts_property_types(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/shapes.py",
        "class Base:\n    pass\n\n\nclass Unrelated:\n    pass\n\n\n"
        "class Shape:\n"
        "    def __init__(self, a, b, *args, **kwargs):\n        pass\n\n"
        "    @property\n    def base(self) -> Base:\n        return Base()\n",
    )
    surface = inspect_public_surface(tmp_path, ["pkg"])
    types = run_extraction._python_types_from_surface(surface)
    scores = dict(
        zip((entry["name"] for entry in types), run_extraction._centrality_scores(types), strict=True)
    )
    assert scores["Base"] == 1  # only through Shape.base's property type
    assert scores["Unrelated"] == 0
    assert scores["Shape"] == 0
