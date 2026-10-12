"""TC-473: module-function signatures, context-manager dunders, dataclass fields and public
instance attributes are published by the Python reader.

Offline: synthetic packages in ``tmp_path`` driven through the real ``inspect_public_surface`` and
``_python_types_from_surface``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction
from foss_mcp.extraction.tree_sitter_engine import python_surface
from foss_mcp.extraction.tree_sitter_engine.python_surface import (
    PUBLISHED_DUNDERS,
    PublicSymbol,
    inspect_public_surface,
)
from foss_mcp.indexing import chunk_builder


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _symbols(root: Path) -> dict[str, PublicSymbol]:
    return {symbol.qualified_name: symbol for symbol in inspect_public_surface(root, ["pkg"]).symbols}


def _types(root: Path) -> dict[str, dict[str, Any]]:
    surface = inspect_public_surface(root, ["pkg"])
    return {entry["name"]: entry for entry in run_extraction._python_types_from_surface(surface)}


def _properties(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {prop["name"]: prop for prop in entry["properties"]}


def _methods(entry: dict[str, Any]) -> set[str]:
    return {method["name"] for method in entry["methods"]}


def test_published_dunders_is_exactly_the_context_manager_protocol() -> None:
    assert PUBLISHED_DUNDERS == ("__enter__", "__exit__", "__aenter__", "__aexit__")
    assert hasattr(python_surface, "PUBLISHED_DUNDERS")


def test_function_entry_carries_params_and_return_type(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path, "pkg/mod.py", "def generate(data: str, symbology: str = 'qr') -> bytes:\n    return b''\n"
    )
    entry = _types(tmp_path)["generate"]
    assert entry["kind"] == "function"
    assert entry["params"] == [{"name": "data", "type": "str"}, {"name": "symbology", "type": "str"}]
    assert entry["return_type"] == "bytes"


def test_function_signature_is_rendered_not_a_bare_kind(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path, "pkg/mod.py", "def generate(data: str, symbology: str = 'qr') -> bytes:\n    return b''\n"
    )
    entry = _types(tmp_path)["generate"]
    text = chunk_builder._format_type_text(entry, fqn=entry["class_import"])
    assert "Methods:" in text
    assert "  - generate(data: str, symbology: str) -> bytes" in text


def test_unannotated_function_has_untyped_params_and_empty_return(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/mod.py", "def run(a, b):\n    pass\n")
    entry = _types(tmp_path)["run"]
    assert entry["params"] == [{"name": "a", "type": ""}, {"name": "b", "type": ""}]
    assert entry["return_type"] == ""


def test_class_and_enum_entries_get_no_function_keys(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/mod.py", "import enum\n\nclass A:\n    pass\n\nclass E(enum.Enum):\n    X = 1\n")
    types = _types(tmp_path)
    assert "params" not in types["A"]
    assert "return_type" not in types["A"]
    assert "params" not in types["E"]


CONTEXT = (
    "class Doc:\n"
    "    def __enter__(self):\n        return self\n\n"
    "    def __exit__(self, *exc):\n        return None\n\n"
    "    async def __aenter__(self):\n        return self\n\n"
    "    async def __aexit__(self, *exc):\n        return None\n\n"
    "    def __repr__(self):\n        return ''\n\n"
    "    def _helper(self):\n        pass\n\n"
    "    def save(self):\n        pass\n"
)


def test_context_manager_dunders_are_published_as_methods(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/doc.py", CONTEXT)
    symbols = _symbols(tmp_path)
    for name in PUBLISHED_DUNDERS:
        assert symbols[f"pkg.doc.Doc.{name}"].kind == "method"
    assert _methods(_types(tmp_path)["Doc"]) >= set(PUBLISHED_DUNDERS)


def test_other_underscore_names_stay_excluded(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/doc.py", CONTEXT)
    methods = _methods(_types(tmp_path)["Doc"])
    assert "__repr__" not in methods
    assert "_helper" not in methods
    assert "save" in methods


DATACLASS = (
    "from dataclasses import dataclass\n"
    "from typing import ClassVar\n\n"
    "@dataclass\nclass Options:\n"
    "    name: str\n"
    "    size: int = 0\n"
    "    _cache: dict = None\n"
    "    LIMIT: ClassVar[int] = 3\n"
)


def test_dataclass_fields_are_properties_with_types_and_writable(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/opts.py", DATACLASS)
    props = _properties(_types(tmp_path)["Options"])
    assert set(props) == {"name", "size"}
    assert props["name"]["type"] == "str"
    assert props["size"]["type"] == "int"
    assert props["name"]["writable"] is True
    assert props["size"]["writable"] is True


def test_attribute_symbols_have_no_docstring_and_the_statement_line(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/opts.py", DATACLASS)
    symbol = _symbols(tmp_path)["pkg.opts.Options.size"]
    assert symbol.kind == "property"
    assert symbol.docstring is None
    assert symbol.line == 7
    assert symbol.writable is True


INIT_ATTRS = (
    "class Box:\n"
    "    def __init__(self, title, flag):\n"
    "        self.title = title\n"
    "        self.count: int = 0\n"
    "        self._private = 1\n"
    "        if flag:\n"
    "            self.mode = 'x'\n"
    "        def inner():\n"
    "            self.nested = 1\n"
    "        for _ in range(1):\n"
    "            self.looped = 2\n"
)


def test_init_assignments_become_properties_including_nested_blocks(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/box.py", INIT_ATTRS)
    props = _properties(_types(tmp_path)["Box"])
    assert {"title", "count", "mode", "looped"} <= set(props)
    assert props["count"]["type"] == "int"
    assert props["title"]["type"] == ""
    assert props["mode"]["writable"] is True


def test_private_and_nested_function_attributes_are_excluded(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/box.py", INIT_ATTRS)
    props = _properties(_types(tmp_path)["Box"])
    assert "_private" not in props
    assert "nested" not in props


def test_attribute_that_is_also_a_property_appears_once_as_the_property(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/box.py",
        "class Box:\n"
        "    label: str = ''\n\n"
        "    def __init__(self):\n        self.title = 1\n        self.label = 'x'\n\n"
        "    @property\n    def title(self) -> str:\n        return ''\n",
    )
    box = _types(tmp_path)["Box"]
    names = [prop["name"] for prop in box["properties"]]
    assert names.count("title") == 1
    assert names.count("label") == 1
    assert _properties(box)["title"]["type"] == "str"
    assert _properties(box)["title"]["writable"] is False


def test_attribute_named_like_a_method_is_not_duplicated(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/box.py",
        "class Box:\n    def __init__(self):\n        self.save = None\n\n    def save(self):\n        pass\n",
    )
    box = _types(tmp_path)["Box"]
    assert "save" in _methods(box)
    assert "save" not in _properties(box)


def test_enum_class_gets_no_attribute_properties(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/colors.py",
        "import enum\n\n"
        "class Color(enum.Enum):\n"
        "    RED: int = 1\n\n"
        "    def __init__(self, value):\n"
        "        self.shade = value\n",
    )
    color = _types(tmp_path)["Color"]
    assert color["kind"] == "enum"
    assert color["properties"] == []


def test_reexported_class_keeps_its_attributes_and_dunders(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from ._impl import Handle\n")
    _write(
        tmp_path,
        "pkg/_impl.py",
        "class Handle:\n"
        "    size: int = 0\n\n"
        "    def __init__(self, path):\n        self.path = path\n\n"
        "    def __enter__(self):\n        return self\n\n"
        "    def __exit__(self, *exc):\n        return None\n",
    )
    handle = _types(tmp_path)["Handle"]
    assert {"size", "path"} <= set(_properties(handle))
    assert {"__enter__", "__exit__"} <= _methods(handle)


def test_mixin_flattening_carries_attributes_of_a_private_base(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/mod.py",
        "class _Base:\n    def __init__(self):\n        self.shared = 1\n\n    def ping(self):\n        pass\n\n"
        "class Child(_Base):\n    pass\n",
    )
    child = _types(tmp_path)["Child"]
    assert "ping" in _methods(child)
    assert "shared" in _properties(child)
