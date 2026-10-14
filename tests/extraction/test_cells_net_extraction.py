"""Offline tests against the committed cells/net fixture - no network, no live clone, no engine.

``gatectl`` verifies this card with the network proxied to a dead port (TC-163 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/cells_net/api_surface.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_net" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "net.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)


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


def test_the_fixture_contains_real_dotnet_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Cells types, not placeholders - would not survive an empty or synthetic fixture.
    assert {"AutoFilter", "AutoFilterColorFilter", "AutoShapeType"} <= names
    assert all(entry.get("class_import", "").startswith("Aspose.Cells") for entry in data["types"])
    assert all(entry["file"].endswith(".cs") for entry in data["types"])


def test_every_entry_is_a_real_csharp_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    assert kinds <= {
        "class_declaration",
        "struct_declaration",
        "interface_declaration",
        "enum_declaration",
    }
    assert kinds


def _by_name(data: dict) -> dict:
    return {entry["name"]: entry for entry in data["types"]}


def test_real_inherited_members_carry_provenance_rooted_to_the_true_declaring_class() -> None:
    # TC-400 re-extraction (with TC-392 "constructors are not inherited" on main): this
    # pilot's real upstream commit has two genuine in-repo inheritance edges:
    # FormulaException/InvalidFileFormatException/StyleException/
    # UnsupportedFeatureException/WorkbookLoadException/WorkbookSaveException
    # -> CellsException, and PdfSaveOptions -> SaveOptions. Each exception subclass
    # declares its own constructor directly in the real source (verified against the
    # pinned commit) - a constructor is never a copied/inherited member, so
    # FormulaException's own constructor must carry no "inherited_from" tag. Neither
    # CellsException nor SaveOptions themselves have an in-repo base (CellsException's
    # own base is the BCL's System.Exception; SaveOptions has no base at all).
    data = _load_fixture()
    by_name = _by_name(data)

    formula_exc = by_name["FormulaException"]
    assert formula_exc["bases"] == ["CellsException"]
    formula_ctor = next(m for m in formula_exc["methods"] if m["is_constructor"])
    assert "inherited_from" not in formula_ctor

    # The root itself must carry no "inherited_from" on its own members - it is
    # the genuine declaring class, not another copy.
    cells_exc = by_name["CellsException"]
    assert all(not m.get("inherited_from") for m in cells_exc["methods"])

    pdf_save_options = by_name["PdfSaveOptions"]
    inherited_props = {
        p["name"]: p["inherited_from"] for p in pdf_save_options["properties"] if p.get("inherited_from")
    }
    assert inherited_props == {
        "SaveFormat": "Aspose.Cells_FOSS.SaveOptions",
        "UseSharedStrings": "Aspose.Cells_FOSS.SaveOptions",
        "ValidateBeforeSave": "Aspose.Cells_FOSS.SaveOptions",
        "CompactStyles": "Aspose.Cells_FOSS.SaveOptions",
        "PreserveRecoveryMetadata": "Aspose.Cells_FOSS.SaveOptions",
    }

    save_options = by_name["SaveOptions"]
    assert save_options["bases"] == []
    assert all(not p.get("inherited_from") for p in save_options["properties"])


def test_other_cellsexception_subclasses_declare_their_own_constructors() -> None:
    # Confirms the "constructors are not inherited" fix (TC-392) holds systematically
    # across every real sibling subclass sharing the same base, not a one-off on a
    # single hand-picked type: each subclass's own constructor carries no
    # "inherited_from" tag.
    data = _load_fixture()
    by_name = _by_name(data)
    siblings = [
        "InvalidFileFormatException",
        "StyleException",
        "UnsupportedFeatureException",
        "WorkbookLoadException",
        "WorkbookSaveException",
    ]
    for name in siblings:
        cls = by_name[name]
        assert cls["bases"] == ["CellsException"]
        assert any(m["is_constructor"] for m in cls["methods"]), name
        inherited = [m for m in cls["methods"] if m.get("inherited_from")]
        assert inherited == [], name
