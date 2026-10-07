"""Offline tests against the committed 3d/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/3d_python/api_surface.json``.

Like pdf/python and slides/python, this fixture was produced by the independent pure-``ast``
reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all.

The real repository's full public surface is 697 types - over the CLI's default
``--max-types 300`` cap - so this fixture was generated with the default cap and is a
reduced, deterministic 300-entry sample, sorted by ``class_import``: ``type_count`` (697) is
greater than ``reduced_type_count`` (300). Observed directly from the live run, not assumed
from a sibling platform's fixture: the reduced sample's own kind vocabulary is only
``{"class", "enum"}`` - no top-level ``"function"`` entry survives the alphabetical truncation
to 300, unlike pdf/python's reduced sample.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "3d_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "3d" / "python.yaml"

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
    # Real Aspose.3D-FOSS-for-Python classes/enums, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"A3DObject", "AnimationChannel", "AssetInfo", "Axis"} <= names
    assert all(entry["class_import"].startswith("aspose") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository, not
    # guessed: the reduced 300-entry sample only contains "class" and "enum" entries, a
    # subset of the reader's full {"class", "enum", "function"} vocabulary (see
    # pdf/python's own test for a sample that does retain top-level functions).
    assert kinds
    assert kinds <= {"class", "enum", "function"}
