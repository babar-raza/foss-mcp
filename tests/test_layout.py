"""Layout tests for the src/foss_mcp package tree (plan 11.3 / TC-001).

These tests assert the *structural* contract the card exists to create, not
runtime behavior:

  * every package plan 11.3 requires under src/foss_mcp/ exists as a real
    package (a directory with an actual __init__.py file) and is importable
    as such;
  * tests/ mirrors src/foss_mcp/ package for package;
  * docs/REPOSITORY_LAYOUT.md names every package and states the ops/ rule.

Note on "importable": Python 3 treats a directory with no __init__.py as an
implicit PEP 420 namespace package and will still "import" it successfully.
An import-only check would therefore be vacuous against exactly the mutation
this suite must catch (deleting src/foss_mcp/indexing/__init__.py) - so every
package is checked both for a real __init__.py file on disk *and* for
importing as a real (non-namespace) package.
"""

from __future__ import annotations

import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "foss_mcp"
TESTS_ROOT = Path(__file__).resolve().parent
LAYOUT_DOC = REPO_ROOT / "docs" / "REPOSITORY_LAYOUT.md"

# Relative, slash-separated path under src/foss_mcp/ for every subpackage
# plan 11.3 requires (the root package itself is handled separately).
SUBPACKAGES = [
    "mcp",
    "mcp/tools",
    "extraction",
    "extraction/tree_sitter_engine",
    "furnished_content",
    "normalization",
    "indexing",
    "retrieval",
    "telemetry",
    "config",
]

# Dotted import name for every package, including the root.
ALL_DOTTED_NAMES = ["foss_mcp"] + ["foss_mcp." + p.replace("/", ".") for p in SUBPACKAGES]


def test_every_package_has_a_real_init_file():
    """Every package's __init__.py must exist as an actual file on disk."""
    missing = []
    root_init = SRC_ROOT / "__init__.py"
    if not root_init.is_file():
        missing.append(str(root_init))
    for rel in SUBPACKAGES:
        init_path = SRC_ROOT / Path(rel) / "__init__.py"
        if not init_path.is_file():
            missing.append(str(init_path))
    assert not missing, f"missing __init__.py for: {missing}"


def test_every_package_is_importable_as_a_real_package():
    """Every package must import cleanly and be a real package (not a
    namespace-package stand-in for a missing __init__.py)."""
    failures = []
    for name in ALL_DOTTED_NAMES:
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            failures.append(f"{name}: import failed: {exc}")
            continue
        # A namespace package (no __init__.py) still "imports" under PEP 420
        # but has __file__ is None. Require a real file-backed package.
        if getattr(module, "__file__", None) is None:
            failures.append(f"{name}: imported as a namespace package (no __init__.py present)")
    assert not failures, "\n".join(failures)


def test_tests_tree_mirrors_src_layout():
    """tests/ must contain a same-named, same-shaped directory (with its own
    __init__.py) for every subpackage under src/foss_mcp/."""
    missing = []
    for rel in SUBPACKAGES:
        mirror_dir = TESTS_ROOT / Path(rel)
        if not mirror_dir.is_dir():
            missing.append(f"missing directory tests/{rel}")
            continue
        mirror_init = mirror_dir / "__init__.py"
        if not mirror_init.is_file():
            missing.append(f"missing tests/{rel}/__init__.py")
    assert not missing, "\n".join(missing)


def test_repository_layout_doc_names_every_package():
    """docs/REPOSITORY_LAYOUT.md must name every package so a reader can find
    where a given file belongs."""
    assert LAYOUT_DOC.is_file(), f"{LAYOUT_DOC} does not exist"
    text = LAYOUT_DOC.read_text(encoding="utf-8")
    missing = [name for name in ALL_DOTTED_NAMES if name not in text]
    assert not missing, f"docs/REPOSITORY_LAYOUT.md does not name: {missing}"


def test_repository_layout_doc_states_the_ops_rule():
    """The doc must state that ops/ is supervisor tooling never imported by
    src/, per this card's actions."""
    text = LAYOUT_DOC.read_text(encoding="utf-8")
    assert "ops/" in text, "docs/REPOSITORY_LAYOUT.md does not mention ops/"
    assert "never imported" in text.lower(), (
        "docs/REPOSITORY_LAYOUT.md does not state that ops/ is never imported by src/"
    )
