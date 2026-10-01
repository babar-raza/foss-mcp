"""Offline tests against the committed cells/go fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-038 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/cells_go/api_surface.json``.

Like pdf/go (and unlike python), "go" is not special-cased in
``run_extraction._LANGUAGE_BY_PLATFORM`` and goes through the ordinary tree-sitter path
(grammar name literally ``"go"``) via ``tree_sitter_engine.api_surface``. Its ``language``
field reads literally ``"go"``, and the observed ``kind`` vocabulary among the fixture is the
tree-sitter Go grammar's own node-type names: ``{"type_spec", "function"}`` - Go has no
class/struct/enum/trait keywords of its own, so every named type declaration (struct,
interface, or defined type) surfaces as ``type_spec`` and every func declaration (free
function or method with a receiver) surfaces as ``function``.

Unlike pdf/go's 2192 real types (far over the CLI's default ``--max-types 300`` cap, so that
fixture is a genuinely reduced subset), this real repository has only 31 public types - well
under the cap - so the committed fixture here is the FULL surface, untruncated, exactly like
cells/rust's own fixture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_go" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "go.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "go"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Go` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "fa4e890e46c509efd22d09b97411202c2b0b8a67"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 31 public types - far under the CLI's default
    # --max-types 300 cap - so this fixture is the full, untruncated surface, unlike
    # pdf/go's genuinely reduced one.
    assert data["type_count"] == 31
    assert data["reduced_type_count"] == 31
    assert data["truncated"] is False


def test_the_fixture_contains_real_go_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Cells-FOSS-for-Go exported identifiers, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Workbook", "Worksheet", "Cell", "Cells", "Style"} <= names
    assert all(entry["file"].endswith(".go") for entry in data["types"])


def test_every_entry_is_a_real_go_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Go grammar's own node-type vocabulary for
    # this repository, not guessed or invented. Go has no class/struct/enum/trait keywords of
    # its own: every named type declaration surfaces as "type_spec" and every func declaration
    # (free function or method) surfaces as "function".
    assert kinds == {"type_spec", "function"}


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    for entry in data["types"]:
        for key in ("name", "kind", "file", "line", "doc", "methods", "properties", "bases", "reachable"):
            assert key in entry, (entry.get("name"), key)


def test_a_real_method_and_a_real_free_function_are_present() -> None:
    data = _load_fixture()
    # Observed directly in the live run: Workbook is a real struct with real methods
    # including Save, and NewWorkbook is a real standalone constructor function with a
    # real doc comment, found in the same file.
    workbook = next(e for e in data["types"] if e["name"] == "Workbook")
    assert workbook["kind"] == "type_spec"
    method_names = {m["name"] for m in workbook["methods"]}
    assert {"Save", "ExportToCSV", "ImportFromCSV"} <= method_names

    new_workbook = next(e for e in data["types"] if e["name"] == "NewWorkbook")
    assert new_workbook["kind"] == "function"
    assert new_workbook["file"] == "aspose/cells_foss/workbook.go"
