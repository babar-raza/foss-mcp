"""Every module the taskcard requires is present and importable (closeout: "every required
module exists"). Also the direct target of the card's negative control: deleting
``lang/csharp.py`` removes an entry this test imports by name, and breaks collection of every
other test module here too, since ``api_surface`` imports the ``lang`` package eagerly.
"""

from __future__ import annotations

import importlib

REQUIRED_MODULES = [
    "foss_mcp.extraction.tree_sitter_engine.api_surface",
    "foss_mcp.extraction.tree_sitter_engine.tree_helpers",
    "foss_mcp.extraction.tree_sitter_engine.package_manifest",
    "foss_mcp.extraction.tree_sitter_engine.package_root",
    "foss_mcp.extraction.tree_sitter_engine.python_surface",
    "foss_mcp.extraction.tree_sitter_engine.lang.cpp",
    "foss_mcp.extraction.tree_sitter_engine.lang.csharp",
    "foss_mcp.extraction.tree_sitter_engine.lang.go",
    "foss_mcp.extraction.tree_sitter_engine.lang.java",
    "foss_mcp.extraction.tree_sitter_engine.lang.python",
    "foss_mcp.extraction.tree_sitter_engine.lang.rust",
    "foss_mcp.extraction.tree_sitter_engine.lang.typescript",
]


def test_every_required_module_is_present_and_importable() -> None:
    for name in REQUIRED_MODULES:
        importlib.import_module(name)


def test_the_lang_registry_names_every_required_language_adapter() -> None:
    from foss_mcp.extraction.tree_sitter_engine import lang

    assert set(lang.LANGUAGES) == {
        "cpp",
        "csharp",
        "go",
        "java",
        "python",
        "rust",
        "typescript",
    }
