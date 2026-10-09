"""Offline tests against the committed html/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-155 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/html_python/api_surface.json``.

Like slides/python (and unlike pdf/net's tree-sitter/C# reader), this fixture was produced
by the independent pure-``ast`` reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``,
adapted in ``run_extraction._python_types_from_surface``): its ``language`` field reads
literally ``"python"`` and its observed ``kind`` vocabulary among the reduced 300 entries is
``{"class", "function"}`` - unlike slides/python, a handful of module-level functions (e.g.
``element_matches``, ``select``, ``detect_encoding``) survived the centrality-ranked truncation
here, so no top-level ``"enum"`` entry appears and there is no tree-sitter grammar name at all.

Regenerated 2026-10-09 (G2/TC-267, rework attempt 2) once TC-274 fixed the real gap TC-267's
own first attempt found and stopped on: this repository's near-universal "real class in a
leading-underscore impl module, re-exported via a public package __init__.py" convention
(101 of 104 real .py files) meant ``bases``/``return_type``/``param_types`` were discarded for
almost every type here even after TC-265. With TC-274 integrated, 218 of the full 323 types now
carry a real, non-empty ``bases`` list and the centrality ranking is a genuine measurement
rather than a near-total no-op (15 of 323 types score above zero, versus 3 before TC-274).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "html_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "html" / "python.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "python"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]


def test_the_fixture_contains_real_python_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.HTML-FOSS-for-Python classes, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"HTMLDocument", "DOMParser", "CSSMediaRule"} <= names
    assert all(entry["class_import"].startswith("aspose_html") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed.
    assert kinds == {"class", "function"}


def test_the_real_highest_centrality_type_survived_truncation() -> None:
    # Measured directly over this fixture's own live run's FULL 323-type artifact (before
    # the 300-cap truncation), using the project's own _centrality_scores(): HTMLElement
    # scores 156 - by a wide margin the single most-referenced type, since every concrete
    # HTML element class (HTMLDivElement, HTMLButtonElement, HTMLMediaElement, ...) names it
    # as a base. This is only a meaningful measurement, rather than a structural no-op, since
    # TC-274 fixed python_surface.py to recover bases/return_type/param_types for a class
    # defined in a leading-underscore impl module and re-exported via a public package
    # __init__.py (this repository's own near-universal convention: 101 of 104 real .py
    # files) - confirmed live here by 218 of the full 323 types carrying a non-empty
    # ``bases`` list, and 15 of 323 scoring above zero, versus just 3 before that fix.
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    assert "HTMLElement" in by_name
    assert by_name["HTMLElement"]["class_import"] == "aspose_html.dom.HTMLElement"
    # Real, observed inheritance evidence - not a placeholder: a handful of real concrete
    # HTML element classes this same fixture carries do name "HTMLElement" as a base.
    referencing = [
        entry["name"]
        for entry in data["types"]
        if entry is not by_name["HTMLElement"] and "HTMLElement" in (entry.get("bases") or [])
    ]
    assert referencing, "expected at least one kept type to list HTMLElement as a base"
