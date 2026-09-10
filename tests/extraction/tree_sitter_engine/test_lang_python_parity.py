"""The parity control: the production Python reader held to the tree-sitter oracle.

Adapted from repository-presenter's ``test_parity.py``. That file compared the ported
``python_surface.inspect_public_surface`` against a facade wrapping ``api_surface`` this card
does not port; here the tree-sitter side calls ``api_surface.extract_api_surface`` directly with
``language="python"`` (``lang.python``, the parity oracle) instead.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface, python_surface

PACKAGE = '''
"""A small product package."""


class Scene:
    """The root of a scene graph."""

    def save(self, path):
        """Write the scene."""
        return None

    def _internal(self):
        return None


class Node:
    """A node."""

    def attach(self, child):
        return child


class _Private:
    pass


def helper(value):
    """A module-level function."""
    return value
'''


def _package(root: Path) -> Path:
    package = root / "aspose" / "threed"
    package.mkdir(parents=True)
    (root / "aspose" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text(PACKAGE, encoding="utf-8")
    return package


def _oracle_names(package: Path, root: Path) -> set[str]:
    types, *_ = api_surface.extract_api_surface(get_parser("python"), "python", package, root, "threed")
    names: set[str] = set()
    for entry in types:
        names.add(entry["name"])
        names.update(m["name"] for m in entry["methods"])
    return names


def test_the_two_readers_agree_on_every_public_class_and_method(tmp_path: Path) -> None:
    """Both read the same tree; only their scopes differ."""
    package = _package(tmp_path)
    from_oracle = _oracle_names(package, tmp_path)
    own = python_surface.inspect_public_surface(tmp_path, ["aspose"])

    def leaf(value: str) -> str:
        return value.rsplit(".", 1)[-1]

    classes = {leaf(s.qualified_name) for s in own.symbols if s.kind == "class"}
    methods = {leaf(s.qualified_name) for s in own.symbols if s.kind == "method"}
    assert classes and methods, "the production reader found nothing; the fixture is wrong"

    # Every public class and method the production reader found, the oracle found too.
    assert classes <= from_oracle, sorted(classes - from_oracle)
    assert methods <= from_oracle, sorted(methods - from_oracle)
    # And neither invents a private name.
    assert "_Private" not in from_oracle and "_internal" not in from_oracle
