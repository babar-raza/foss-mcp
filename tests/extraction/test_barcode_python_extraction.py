"""Offline tests against the committed barcode/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-152 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/barcode_python/api_surface.json``.

Like slides/python (and unlike pdf/net's tree-sitter/C# reader), this fixture was
produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all. Unlike slides/python,
this repository's reduced 70-entry surface does survive top-level functions: the
observed ``kind`` vocabulary is ``{"class", "enum", "function"}``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "barcode_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "barcode" / "python.yaml"

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
    # Real Aspose.BarCode-FOSS-for-Python classes/enums/functions, not placeholders -
    # would not survive an empty or synthetic fixture.
    assert {"Barcode", "BarcodeError", "Code128EncodeMode"} <= names
    assert all(entry["class_import"].startswith("aspose_barcode_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Unlike slides/python,
    # this reduced surface does retain top-level module functions (e.g. "generate",
    # "qr") alongside classes and enums.
    assert kinds == {"class", "enum", "function"}


def test_a_real_top_level_function_is_present() -> None:
    data = _load_fixture()
    functions = {entry["name"] for entry in data["types"] if entry["kind"] == "function"}
    # Real top-level encoder functions from src/aspose_barcode_foss/api.py, observed
    # directly in this card's own run - not guessed.
    assert {"generate", "qr", "code128"} <= functions
