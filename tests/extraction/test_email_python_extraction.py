"""Offline tests against the committed email/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-151 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/email_python/api_surface.json``.

Like slides/python (and unlike pdf/net, a tree-sitter/C# reader), this fixture was
produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"``. Unlike slides/python's reduced 300-entry fixture, this repository's full,
unreduced surface is only 63 types, so its observed ``kind`` vocabulary includes a
top-level ``"function"`` entry alongside ``{"class", "enum"}`` - observed directly from
the live reader's output for this repository, not guessed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "email_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "email" / "python.yaml"

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
    # Real Aspose.Email-FOSS-for-Python classes/functions, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"CFBDocument", "CFBReader", "DirectoryEntry"} <= names
    assert all(entry["class_import"].startswith("aspose.email_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Unlike slides/python's
    # reduced 300-entry fixture, this repository's unreduced 63-entry surface includes a
    # top-level function alongside classes and enums.
    assert kinds == {"class", "enum", "function"}
