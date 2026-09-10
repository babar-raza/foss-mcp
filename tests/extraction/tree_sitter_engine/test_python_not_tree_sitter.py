"""Python extraction is production's one non-tree-sitter path.

``python_surface.inspect_public_surface`` is a pure ``ast`` reader; ``lang.python`` (the
tree-sitter-family adapter, also ported) exists only as a parity oracle and is never the
production path for Python. If a future edit wires Python extraction back through tree-sitter,
these tests catch it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from foss_mcp.extraction.tree_sitter_engine import python_surface


def test_python_surface_extraction_never_calls_get_parser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tree_sitter_language_pack.get_parser must not be called")

    monkeypatch.setattr("tree_sitter_language_pack.get_parser", _boom)

    package = tmp_path / "widget"
    package.mkdir()
    (package / "__init__.py").write_text(
        'class Scene:\n    """A scene."""\n\n    def save(self):\n        pass\n',
        encoding="utf-8",
    )
    surface = python_surface.inspect_public_surface(tmp_path, ["widget"])
    values = {s.qualified_name for s in surface.symbols}
    assert "widget.Scene" in values and "widget.Scene.save" in values


def test_python_surface_imports_no_tree_sitter_or_lang_module() -> None:
    """The production reader's own import statements, read with ``ast`` - not string-matched."""
    tree = ast.parse(inspect.getsource(python_surface))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("tree_sitter" in name for name in imported)
    assert not any(name.endswith(".lang") or name == "lang" for name in imported)


def test_the_tree_sitter_python_adapter_is_ported_but_not_imported_by_python_surface() -> None:
    """``lang.python`` exists (parity oracle) yet ``python_surface`` has no reference to it."""
    from foss_mcp.extraction.tree_sitter_engine import lang

    assert lang.get("python") is lang.python
    assert not hasattr(python_surface, "lang")
    assert not hasattr(python_surface, "get_parser")
