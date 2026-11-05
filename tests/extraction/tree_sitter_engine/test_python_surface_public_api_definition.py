"""G3/TC-676 (O2, REQ-G3-012): ground-truth divergence tests between the oracle's machine-readable
Python visibility rule (``config/public_api_definition/python.yaml``) and the EXTRACTOR's real,
current ``_methods``/``PUBLISHED_DUNDERS`` behavior (``src/foss_mcp/extraction/tree_sitter_engine/
python_surface.py``).

This is a NEW file -- no existing ``test_python_surface*.py`` file is touched by this card.

The extractor's REAL public entry point, ``inspect_public_surface`` (the same one every other
``test_python_surface*.py`` module in this directory already exercises), is called against a
synthetic on-disk package, exactly like ``test_python_surface_members.py``'s own established
pattern. Nothing here imports the oracle's config into the EXTRACTOR's production code --
``src/foss_mcp/extraction/**`` must never read ``public_api_definition`` at all; this file reads it
only as a ground-truth fixture, in test code.

Two things are asserted, as DATA against the real YAML, never hardcoded prose duplicating it:

(a) **Agreement** on representative non-dunder names: the oracle's rule (leading-underscore-is-
    private, from ``visibility.private_prefix``) and the extractor's real, current behavior agree
    on an ordinary public method, an ordinary private (``_``-prefixed) method, and ``__init__``.

(b) **The real, confirmed divergence** on ``__repr__`` and ``__eq__`` (named explicitly in
    ``plans/TC-676.yaml`` as the evidence): the oracle's config says every dunder is public
    (``visibility.dunder_exception: all``); the extractor's real ``PUBLISHED_DUNDERS`` allow-list
    (``("__enter__", "__exit__", "__aenter__", "__aexit__")``) does NOT include them, so the
    extractor's real, current behavior treats them as PRIVATE. This is a KNOWN, DISCLOSED
    divergence this card surfaces and pins -- it is never "fixed" by changing either side's real
    behavior here. A later card, informed by this one's now-explicit evidence, decides whether a
    reconciliation is warranted.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# PUBLISHED_DUNDERS is the extractor's own real allow-list, read directly off the real committed
# module, never re-typed by hand - if it ever changes, this test's divergence assertions below are
# checked against the real, current value automatically.
from foss_mcp.extraction.tree_sitter_engine.python_surface import (
    PUBLISHED_DUNDERS,
    inspect_public_surface,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "public_api_definition" / "python.yaml"


def _definition() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _oracle_says_public(name: str, definition: dict) -> bool:
    """The same generic rule ``source_differential._is_public_name_by_definition`` applies,
    reimplemented here independently (never imported from the production oracle module) so this
    extractor-side test does not depend on importing ``scripts/release`` at all."""
    visibility = definition["visibility"]
    prefix = visibility["private_prefix"]
    if not name.startswith(prefix):
        return True
    double_prefix = prefix * 2
    is_dunder = name.startswith(double_prefix) and name.endswith(double_prefix)
    dunder_exception = visibility["dunder_exception"]
    if dunder_exception == "all":
        return is_dunder
    if dunder_exception == "none":
        return False
    return is_dunder and name in dunder_exception


def _write_package(root: Path) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "widget.py").write_text(
        "class Widget:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n\n"
        "    def build(self):\n"
        "        return self.name\n\n"
        "    def _hidden(self):\n"
        "        pass\n\n"
        "    def __repr__(self):\n"
        "        return self.name\n\n"
        "    def __eq__(self, other):\n"
        "        return self.name == other.name\n\n"
        "    def __enter__(self):\n"
        "        return self\n\n"
        "    def __exit__(self, *exc):\n"
        "        return False\n",
        encoding="utf-8",
    )


def _extractor_published_names(tmp_path: Path) -> set[str]:
    _write_package(tmp_path)
    surface = inspect_public_surface(tmp_path, ["pkg"])
    return {
        symbol.qualified_name.rsplit(".", 1)[-1]
        for symbol in surface.symbols
        if symbol.qualified_name.startswith("pkg.widget.Widget.")
    }


# --- (a) agreement on representative non-dunder names -------------------------------------------


def test_oracle_and_extractor_agree_an_ordinary_public_method_is_public(tmp_path: Path) -> None:
    definition = _definition()
    published = _extractor_published_names(tmp_path)
    assert _oracle_says_public("build", definition) is True
    assert "build" in published


def test_oracle_and_extractor_agree_a_leading_underscore_method_is_private(tmp_path: Path) -> None:
    definition = _definition()
    published = _extractor_published_names(tmp_path)
    assert _oracle_says_public("_hidden", definition) is False
    assert "_hidden" not in published


def test_oracle_and_extractor_agree_init_is_public(tmp_path: Path) -> None:
    definition = _definition()
    published = _extractor_published_names(tmp_path)
    assert _oracle_says_public("__init__", definition) is True
    assert "__init__" in published


def test_oracle_and_extractor_agree_on_every_published_dunder_in_the_extractors_own_allowlist(
    tmp_path: Path,
) -> None:
    """Sanity: PUBLISHED_DUNDERS is, today, a subset of "every dunder" - so the oracle's
    dunder_exception: all rule can never disagree with the extractor on a name already in that
    allow-list. If this ever stopped holding, it would itself be a new divergence worth surfacing
    explicitly, not silently absorbed into test (b) below."""
    definition = _definition()
    for name in PUBLISHED_DUNDERS:
        assert _oracle_says_public(name, definition) is True


# --- (b) the real, confirmed, DISCLOSED dunder divergence ---------------------------------------


def test_KNOWN_DIVERGENCE_oracle_says_repr_is_public_extractor_says_private(tmp_path: Path) -> None:
    """DISCLOSED DIVERGENCE (plans/TC-676.yaml): the oracle's config
    (visibility.dunder_exception: all) says __repr__ is public. The extractor's real,
    current PUBLISHED_DUNDERS allow-list does NOT include __repr__, so its real behavior
    publishes nothing named __repr__ for Widget. This is NOT a bug this card fixes - it is the
    real, measured discrepancy this card's own purpose section documents, pinned here so it can
    never silently drift further apart unnoticed."""
    definition = _definition()
    published = _extractor_published_names(tmp_path)
    assert "__repr__" not in PUBLISHED_DUNDERS
    assert _oracle_says_public("__repr__", definition) is True  # the ORACLE's real rule
    assert "__repr__" not in published  # the EXTRACTOR's real, current behavior


def test_KNOWN_DIVERGENCE_oracle_says_eq_is_public_extractor_says_private(tmp_path: Path) -> None:
    """DISCLOSED DIVERGENCE (plans/TC-676.yaml): identical reasoning to the __repr__ case above,
    for __eq__ - the second dunder the card names explicitly as evidence."""
    definition = _definition()
    published = _extractor_published_names(tmp_path)
    assert "__eq__" not in PUBLISHED_DUNDERS
    assert _oracle_says_public("__eq__", definition) is True  # the ORACLE's real rule
    assert "__eq__" not in published  # the EXTRACTOR's real, current behavior


def test_extractor_production_code_never_imports_the_public_api_definition_config() -> None:
    """The single most important constraint on this card (see plans/TC-676.yaml's SCOPE NOTE):
    src/foss_mcp/extraction/**/*.py must NEVER read or import config/public_api_definition - only
    this TEST file (and the oracle's own tests) may load it, as a ground-truth fixture. A grep is
    the real, direct proof; this test makes that proof part of the suite itself, not just a
    one-time manual check during development."""
    extraction_root = REPO_ROOT / "src" / "foss_mcp" / "extraction"
    offenders = [
        path
        for path in extraction_root.rglob("*.py")
        if "public_api_definition" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"extractor production code must never reference public_api_definition: {offenders}"
    )
