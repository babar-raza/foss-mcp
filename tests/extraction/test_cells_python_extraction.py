"""Offline tests against the committed cells/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-025/TC-093 set
``network: false`` for the first slides/cells pilots; this card follows the same pattern),
so nothing here may reach out; everything is read from the fixture ``run_extraction.py``
already produced and committed at ``tests/fixtures/cells_python/api_surface.json``.

Like slides/python (see tests/extraction/test_slides_python_extraction.py), this fixture was
produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all. Unlike slides/python,
this repository's observed ``kind`` vocabulary among the (untruncated, 204-entry) reduced
fixture is ``{"class", "enum", "function"}`` - a top-level ``"function"`` entry did survive
here (e.g. module-level helpers like ``is_encrypted_file``), observed directly from the live
run, not guessed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "python.yaml"

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
    # Real Aspose.Cells-FOSS-for-Python classes/enums/functions, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Cell", "Cells", "Chart", "ChartType", "AgileEncryptionParameters"} <= names
    assert all(entry["class_import"].startswith("aspose.cells_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Unlike slides/python,
    # this repository's reduced fixture also contains top-level module functions.
    assert kinds == {"class", "enum", "function"}
