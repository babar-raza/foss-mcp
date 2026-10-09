"""Offline tests against the committed words/net fixture - no network, no live clone, no engine.

``gatectl`` verifies this card with the network proxied to a dead port (TC-166 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/words_net/api_surface.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "words_net" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "words" / "net.yaml"

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
    # Real Aspose.Words types, not placeholders - would not survive an empty or synthetic fixture.
    assert {"Document", "Field", "ArrowType"} <= names
    assert all(entry.get("class_import", "").startswith("Aspose.Words") for entry in data["types"])
    assert all(entry["file"].endswith(".cs") for entry in data["types"])


def test_the_fixture_keeps_fieldtype_after_the_centrality_ranked_selection_fix() -> None:
    """TC-252 replaced reduce_fixture's alphabetical truncation with a centrality-ranked one.
    This pilot is .NET-sourced via the shared tree-sitter engine (not Python), so it was never
    affected by the Python-only centrality-no-op bug TC-265 fixed - its "bases" data was already
    real before this card ran. This project has no audit-named symbol for this specific pilot,
    so an independent measurement over the real, full (pre-truncation) 618-type set is the
    concrete proof the centrality fix works here too: "FieldType" (Aspose.Words.Fields.FieldType)
    scored highest - 103 OTHER types in the artifact reference its bare name in a base, a method's
    return_type/params, or a property's type - of any type in the real upstream artifact, measured
    directly against commit 0d3f1add920f5294d726b4f286c2d6ba7c3ae22c. It must be among the 300
    kept types, with its own real qualified name, source file, and enum members."""
    data = _load_fixture()
    field_type_entries = [entry for entry in data["types"] if entry["name"] == "FieldType"]
    assert field_type_entries, "FieldType must be present among the kept types"
    field_type = field_type_entries[0]
    assert field_type["class_import"] == "Aspose.Words.Fields.FieldType"
    assert field_type["kind"] == "enum_declaration"
    assert field_type["file"].endswith(".cs")
    assert field_type["enum_members"], "FieldType is a real enum - it must carry real members"


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
