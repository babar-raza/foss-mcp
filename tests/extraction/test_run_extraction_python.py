"""run_extraction.py's python dispatch: routed through the independent pure-``ast`` reader
(python_surface.inspect_public_surface), never through tree-sitter/get_parser, with its output
adapted into the same dict shape extract_api_surface produces.

Offline only (network: false, per TC-021's card): every test builds a small synthetic python
package on disk and calls extract_from_clone / extract_pinned_repository directly - the latter's
clone_shallow is monkeypatched to a local copy so no clone ever happens.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction

PACKAGE_INIT = '''"""A small product package."""
from .core import Gadget, make_widget
from .missing import Nothing

__all__ = ["Gadget", "make_widget", "Nothing"]
'''

PACKAGE_CORE = '''"""Core widget types."""


class Gadget:
    """The root gadget."""

    def build(self):
        """Assemble the gadget."""
        return None

    def _internal(self):
        return None


class Color(Enum):
    """A named color."""

    RED = 1
    BLUE = 2


def make_widget(name):
    """Build a widget by name."""
    return name


def _hidden_helper():
    return None
'''
# Color's `Enum` base is never imported here - the reader only ever parses this file with
# ``ast``, it never imports or executes it, so the base-class name is read as literal syntax.


def _write_package(root: Path) -> None:
    """A src-layout python package: a marker file, module-level classes/functions with
    docstrings, an ``__all__``, a private name, a re-export, and one unresolvable re-export.
    """
    (root / "pyproject.toml").write_text("[project]\nname = \"widgets\"\n", encoding="utf-8")
    pkg = root / "src" / "widgets"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(PACKAGE_INIT, encoding="utf-8")
    (pkg / "core.py").write_text(PACKAGE_CORE, encoding="utf-8")


def _manifest(**overrides: Any) -> dict[str, Any]:
    manifest = {"platform": "python", "family": "widgets", "repository": "acme/widgets"}
    manifest.update(overrides)
    return manifest


def test_python_manifest_extracts_without_keyerror(tmp_path: Path) -> None:
    """The bug this card fixes: platform='python' used to raise a bare KeyError."""
    _write_package(tmp_path)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    assert types, "the production reader found nothing; the fixture package is wrong"


def test_output_entries_match_extract_api_surface_dict_shape(tmp_path: Path) -> None:
    _write_package(tmp_path)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    required = {
        "name",
        "kind",
        "file",
        "line",
        "doc",
        "visibility",
        "deprecated",
        "deprecated_reason",
        "bases",
        "class_import",
        "canonical_namespace",
        "methods",
        "properties",
    }
    for entry in types:
        assert required <= entry.keys(), entry
        if entry["kind"] == "enum":
            assert "enum_members" in entry


def test_class_kind_doc_and_line_are_correct(tmp_path: Path) -> None:
    _write_package(tmp_path)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    by_import = {entry["class_import"]: entry for entry in types}

    gadget = by_import["widgets.core.Gadget"]
    assert gadget["kind"] == "class"
    assert gadget["doc"] == "The root gadget."
    assert gadget["line"] == 4  # `class Gadget:` is the 4th line of PACKAGE_CORE
    assert gadget["visibility"] == "public"
    method_names = {m["name"] for m in gadget["methods"]}
    assert method_names == {"build"}  # `_internal` never surfaces
    build = next(m for m in gadget["methods"] if m["name"] == "build")
    assert build["doc"] == "Assemble the gadget."

    color = by_import["widgets.core.Color"]
    assert color["kind"] == "enum"
    assert color["enum_members"] == []

    widget_fn = by_import["widgets.core.make_widget"]
    assert widget_fn["kind"] == "function"
    assert widget_fn["doc"] == "Build a widget by name."


def test_reexport_resolves_to_the_same_class_under_the_package_name(tmp_path: Path) -> None:
    _write_package(tmp_path)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    by_import = {entry["class_import"]: entry for entry in types}

    # `widgets.Gadget` (the public re-export __init__.py exposes) resolves to the same class
    # kind and docstring as the module where it is actually defined.
    reexported = by_import["widgets.Gadget"]
    assert reexported["kind"] == "class"
    assert reexported["doc"] == "The root gadget."
    assert reexported["canonical_namespace"] == "widgets"


def test_unresolved_reexport_surfaces_rather_than_being_dropped(tmp_path: Path) -> None:
    _write_package(tmp_path)
    types, unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())

    # `Nothing` is re-exported from a module (`widgets.missing`) that does not exist on disk -
    # it must never silently vanish, and it must never be fabricated as a real class entry.
    names = {entry["class_import"] for entry in types}
    assert "widgets.Nothing" not in names
    assert any("unresolved-reexport" in item and "Nothing" in item for item in unresolved)


def test_never_calls_get_parser_for_a_python_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_package(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("get_parser must never be called for a python manifest")

    monkeypatch.setattr(run_extraction, "get_parser", _boom)
    types, _unresolved = run_extraction.extract_from_clone(tmp_path, _manifest())
    assert types


def test_never_dispatches_python_through_language_by_platform(tmp_path: Path) -> None:
    """'python' is not, and must never become, a key of _LANGUAGE_BY_PLATFORM - that table is
    the tree-sitter-language-pack spelling table, and python never reaches it."""
    assert "python" not in run_extraction._LANGUAGE_BY_PLATFORM


def test_extract_pinned_repository_records_language_python_with_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_src = tmp_path / "src_repo"
    package_src.mkdir()
    _write_package(package_src)
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "platform: python\nfamily: widgets\nrepository: acme/widgets\n", encoding="utf-8"
    )

    def _fake_clone(_repository: str, dest: Path) -> str:
        # extract_pinned_repository's workdir already exists (tempfile.mkdtemp) - copy into it.
        shutil.copytree(package_src, dest, dirs_exist_ok=True)
        return "a" * 40

    monkeypatch.setattr(run_extraction, "clone_shallow", _fake_clone)
    artifact = run_extraction.extract_pinned_repository(manifest_path)

    assert artifact["language"] == "python"
    assert artifact["source_commit"] == "a" * 40
    assert artifact["type_count"] == len(artifact["types"])
    assert any(entry["class_import"] == "widgets.core.Gadget" for entry in artifact["types"])
    assert "unresolved" in artifact and artifact["unresolved"]
