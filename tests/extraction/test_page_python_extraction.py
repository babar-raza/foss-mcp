"""Offline tests against the committed page/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-156 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/page_python/api_surface.json``.

Like slides/python and barcode/python (and unlike pdf/net's tree-sitter/C# reader), this
fixture was produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all. Like barcode/python
(and unlike slides/python), this repository's 244-entry surface (no truncation - the
default ``--max-types 300`` already covers the whole real surface) does retain top-level
module functions: the observed ``kind`` vocabulary is ``{"class", "enum", "function"}``.
Unlike both prior Python-platform pilots, this package's ``class_import`` prefix is
``aspose.page`` (neither ``aspose.slides_foss`` nor ``aspose_barcode_foss``) - observed
directly from this card's own run, not derived from a pattern.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "page_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "page" / "python.yaml"

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
    # Real Aspose.Page-FOSS-for-Python classes, not placeholders - would not survive an
    # empty or synthetic fixture. Matrix/Path come from common/render_model.py;
    # AxialShading from common/color_resources.py.
    assert {"AxialShading", "Matrix", "Path"} <= names
    assert all(entry["class_import"].startswith("aspose.page") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Like barcode/python,
    # this reduced surface retains top-level module functions alongside classes and enums.
    assert kinds == {"class", "enum", "function"}


def test_a_real_top_level_function_is_present() -> None:
    data = _load_fixture()
    functions = {entry["name"] for entry in data["types"] if entry["kind"] == "function"}
    # Real top-level PS/EPS/XPS-to-PDF conversion entry points from this library's own
    # description, observed directly in this card's own run - not guessed.
    assert {"ps_to_pdf", "xps_to_pdf", "eps_metadata"} <= functions
