"""TC-758 (G3, workstream J, REQ-G3-015): the two disclosed defects of the pinned 3d/typescript library.

``config/known_defects/3d__typescript.yaml`` discloses two upstream defects of ``@aspose/3d``
24.12.0 at commit ``66cb26df3b031f0bf6976d4e88dc89721983afa4`` that no audit round recorded:

- ``3D-TYPESCRIPT-STL-001``: ``Scene.save`` to ``.stl`` writes polygon vertex indices instead of
  vertex coordinates.
- ``3D-TYPESCRIPT-FBX-001``: ``Scene.save`` to ``.fbx`` falls through to the OBJ exporter.

These tests read the committed registry and the committed API-surface fixture only. They never
import or run the library.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "config" / "known_defects" / "3d__typescript.yaml"
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
API_SURFACE_PATH = REPO_ROOT / "tests" / "fixtures" / "3d_typescript" / "api_surface.json"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"
ROUND3_ISSUES_PATH = REPO_ROOT / "docs" / "round3" / "issues.json"

STL_ID = "3D-TYPESCRIPT-STL-001"
FBX_ID = "3D-TYPESCRIPT-FBX-001"
NEW_IDS = (STL_ID, FBX_ID)


def _registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def _defect(defect_id: str) -> dict:
    matches = [defect for defect in _registry()["defects"] if defect["id"] == defect_id]
    assert len(matches) == 1, f"expected exactly one {defect_id} entry, found {len(matches)}"
    return matches[0]


def _api_surface_fqns() -> set[str]:
    surface = json.loads(API_SURFACE_PATH.read_text(encoding="utf-8"))
    fqns: set[str] = set()
    for entry in surface["types"]:
        if entry.get("class_import"):
            fqns.add(entry["class_import"])
        if entry.get("canonical_namespace") and entry.get("name"):
            fqns.add(f"{entry['canonical_namespace']}.{entry['name']}")
    return fqns


def test_registry_file_validates_against_the_known_defects_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    document = _registry()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"3d__typescript.yaml fails the known-defects schema: {errors}"


def test_stl_entry_exists_and_names_the_index_export_defect() -> None:
    defect = _defect(STL_ID)
    summary = defect["summary"]
    assert "indices" in summary, summary
    assert ".stl" in summary, summary
    assert "Scene.save to .stl writes polygon vertex indices instead of vertex coordinates" in summary
    assert defect["status"] == "open"
    assert defect["applies_to"] == {"package_versions": ["24.12.0"]}
    assert defect["evidence"]["verified_on"]["package"] == "@aspose/3d"
    assert defect["evidence"]["verified_on"]["version"] == "24.12.0"
    assert len(summary) <= 240


def test_fbx_entry_exists_and_names_the_obj_fall_through_defect() -> None:
    defect = _defect(FBX_ID)
    summary = defect["summary"]
    assert ".fbx" in summary, summary
    assert "OBJ" in summary, summary
    assert "Scene.save to .fbx falls through to the OBJ exporter" in summary
    assert defect["status"] == "open"
    assert defect["applies_to"] == {"package_versions": ["24.12.0"]}
    assert defect["evidence"]["verified_on"]["package"] == "@aspose/3d"
    assert defect["evidence"]["verified_on"]["version"] == "24.12.0"
    assert len(summary) <= 240


def test_every_new_subject_ref_resolves_in_the_api_surface_fixture() -> None:
    fqns = _api_surface_fqns()
    for defect_id in NEW_IDS:
        defect = _defect(defect_id)
        assert defect["subject"]["kind"] == "method"
        for ref in defect["subject"]["refs"]:
            assert ref in fqns, f"{defect_id}: subject ref {ref!r} does not resolve in api_surface.json"


def test_both_new_ids_are_unique_in_the_registry_file() -> None:
    ids = [defect["id"] for defect in _registry()["defects"]]
    for defect_id in NEW_IDS:
        assert ids.count(defect_id) == 1, f"{defect_id} appears {ids.count(defect_id)} times"
    assert len(ids) == len(set(ids)), "a defect id is repeated in 3d__typescript.yaml"


def test_new_ids_do_not_collide_with_any_round2_or_round3_audit_id() -> None:
    # The two audit files use different shapes (round2 is a list, round3 a dict of pilots), so this
    # checks the raw committed text for the literal id instead of parsing either structure.
    audit_texts = {
        path.name: path.read_text(encoding="utf-8") for path in (ROUND2_ISSUES_PATH, ROUND3_ISSUES_PATH)
    }
    for defect_id in NEW_IDS:
        hits = [name for name, text in audit_texts.items() if f'"{defect_id}"' in text]
        assert hits == [], f"{defect_id} collides with a recorded audit issue id in {hits}"
