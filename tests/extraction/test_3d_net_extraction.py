"""Offline tests against the committed 3d/net fixture - no network, no live clone, no engine.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/3d_net/api_surface.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "3d_net" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "3d" / "net.yaml"

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
    # Real Aspose.3D-FOSS-for-.NET types, not placeholders - would not survive an empty or
    # synthetic fixture.
    assert {"A3DObject", "AssetInfo", "ExtrapolationType"} <= names
    assert all(entry.get("class_import", "").startswith("Aspose") for entry in data["types"])
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


def test_a_real_inherited_member_carries_the_correct_inherited_from_tag() -> None:
    """TC-299: this fixture predated TC-261 (adds ``inherited_from`` provenance tagging to
    ``_flatten_inheritance()``) entirely - the old fixture had real classes with non-empty
    ``bases`` but zero ``inherited_from`` tags anywhere. ``Node`` genuinely declares
    ``bases == ["SceneObject"]`` and really does inherit ``SceneObject``'s direct parent
    ``A3DObject``'s own real members (``RemoveProperty``/``GetProperty``/``SetProperty``/
    ``FindProperty``/``Name``/``Properties``), each tagged "inherited_from" the real,
    member-bearing declaring class "Aspose.ThreeD.A3DObject"."""
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    node = by_name["Node"]
    assert node["bases"] == ["SceneObject"]

    inherited_methods = {m["name"] for m in node["methods"] if "inherited_from" in m}
    assert {"RemoveProperty", "GetProperty", "SetProperty", "FindProperty"} <= inherited_methods
    for member in node["methods"]:
        if member["name"] in {"RemoveProperty", "GetProperty", "SetProperty", "FindProperty"}:
            assert member["inherited_from"] == "Aspose.ThreeD.A3DObject"


def test_a_real_transitively_inherited_member_is_rooted_to_the_real_grandparent() -> None:
    """A real, live multi-level chain confirmed directly against this fixture (mirroring
    TC-261's own pdf/cpp PopupAnnotation/Annotation/BaseParagraph proof, and 3d/python's
    identical Node/SceneObject/A3DObject lineage in the same product library): ``Node``
    declares ``bases == ["SceneObject"]`` (its direct parent only), and ``SceneObject`` itself
    declares ``bases == ["A3DObject"]``. ``FindProperty`` is genuinely declared by the
    grandparent ``A3DObject``, copied into ``SceneObject`` first (parents resolve before
    children), then copied again into ``Node`` - and must still be rooted to ``A3DObject``,
    the class that actually declares it, never to ``SceneObject``, the intermediate parent it
    passed through on the way."""
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    node = by_name["Node"]
    scene_object = by_name["SceneObject"]
    assert node["bases"] == ["SceneObject"]
    assert scene_object["bases"] == ["A3DObject"]

    find_property = next(m for m in node["methods"] if m["name"] == "FindProperty")
    assert find_property["inherited_from"] == "Aspose.ThreeD.A3DObject"
    assert find_property["inherited_from"] != "Aspose.ThreeD.SceneObject"

    # Node's own genuinely-declared members must never carry the key.
    add_child_node = next(m for m in node["methods"] if m["name"] == "AddChildNode")
    assert "inherited_from" not in add_child_node
