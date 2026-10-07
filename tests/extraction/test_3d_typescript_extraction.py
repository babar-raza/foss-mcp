"""Offline tests against the committed 3d/typescript fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/3d_typescript/api_surface.json``.

TypeScript-platform extraction support was already proven by the pdf/typescript and
cells/typescript onboardings; this card is a straight run-and-pin against a third, real,
public TypeScript repository (``aspose-3d-foss/Aspose.3D-FOSS-for-TypeScript``) - no engine
change at all.

The real repository's full public surface is only 153 types - under the CLI's default
``--max-types 300`` cap - so this fixture was generated with the default cap and is the
complete surface, not a sub-sample: ``type_count == reduced_type_count == 153`` and
``truncated is False``.

The observed ``kind`` vocabulary is ``{"class_declaration", "abstract_class_declaration",
"interface_declaration", "enum_declaration", "function"}`` - richer than cells/typescript's
``{"class_declaration", "function", "interface_declaration", "type_alias_declaration",
"enum_declaration"}`` because this repository declares several abstract base classes (for
example ``FileFormat``) rather than any type alias. ``abstract_class_declaration`` is a real,
distinct tree-sitter node type for TypeScript (see
``tree_sitter_engine/tree_helpers.py``'s ``TC-MT040-10`` note), not an invented kind.

Each name asserted on below was independently checked against the live run's own output (not
invented): ``A3DObject`` is a real exported class in ``src/aspose/threed/A3DObject.ts`` with a
``constructor`` and a ``findProperty(propertyName: string): Property | null`` method;
``copysign`` is a real top-level exported function in
``src/aspose/threed/utilities/Quaternion.ts`` (line 4) taking ``(x: number, y: number):
number``; ``INamedObject`` is a real exported interface in
``src/aspose/threed/INamedObject.ts`` whose first property is ``name: string``;
``ExtrapolationType`` is a real exported enum in
``src/aspose/threed/animation/ExtrapolationType.ts`` with a member ``CONSTANT = 0``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "3d_typescript" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "3d" / "typescript.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "typescript"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-3d-foss/Aspose.3D-FOSS-for-TypeScript` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "66cb26df3b031f0bf6976d4e88dc89721983afa4"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository's complete public surface is only 153 types - under the CLI's
    # default --max-types 300 cap - so this fixture is the full surface, not a sub-sample.
    assert data["type_count"] == 153
    assert data["reduced_type_count"] == 153
    assert data["truncated"] is False


def test_the_fixture_contains_real_typescript_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.3D-FOSS-for-TypeScript exported declarations, not placeholders - would not
    # survive an empty or synthetic fixture. Each was independently observed in this fixture's
    # own live-run output: `A3DObject` (class, src/aspose/threed/A3DObject.ts), `copysign`
    # (function, src/aspose/threed/utilities/Quaternion.ts), `INamedObject` (interface,
    # src/aspose/threed/INamedObject.ts), `ExtrapolationType` (enum,
    # src/aspose/threed/animation/ExtrapolationType.ts).
    assert {"A3DObject", "copysign", "INamedObject", "ExtrapolationType"} <= names
    assert all(entry["file"].endswith(".ts") for entry in data["types"])


def test_every_entry_is_a_real_typescript_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live run over this repository's real, complete public
    # surface, not guessed or invented: classes and an abstract base class describing the 3D
    # object model, alongside interfaces, enums, and a handful of module-level helper
    # functions.
    assert kinds == {
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "enum_declaration",
        "function",
    }


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    for entry in data["types"]:
        for key in (
            "name",
            "kind",
            "file",
            "line",
            "doc",
            "methods",
            "properties",
            "bases",
            "reachable",
        ):
            assert key in entry, (entry.get("name"), key)


def test_a_real_class_a_real_function_an_interface_and_an_enum_have_real_fields() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # `A3DObject` is a real exported class in src/aspose/threed/A3DObject.ts - confirmed in
    # the live checkout: it declares a constructor and a `findProperty(propertyName: string):
    # Property | null` method.
    cls = by_name["A3DObject"]
    assert cls["kind"] == "class_declaration"
    assert cls["file"] == "src/aspose/threed/A3DObject.ts"
    method_names = [m["name"] for m in cls["methods"]]
    assert "constructor" in method_names
    assert "findProperty" in method_names
    find_method = next(m for m in cls["methods"] if m["name"] == "findProperty")
    assert find_method["return_type"] == "Property | null"
    assert [p["name"] for p in find_method["params"]] == ["propertyName"]

    # `copysign` is a real top-level exported function in
    # src/aspose/threed/utilities/Quaternion.ts (line 4) - confirmed in the live checkout at
    # `function copysign(x: number, y: number): number`.
    func = by_name["copysign"]
    assert func["kind"] == "function"
    assert func["file"] == "src/aspose/threed/utilities/Quaternion.ts"
    assert func["line"] == 4
    assert [p["name"] for p in func["params"]] == ["x", "y"]
    assert func["return_type"] == "number"

    # `INamedObject` is a real exported interface in src/aspose/threed/INamedObject.ts - its
    # first declared property is `name: string`.
    iface = by_name["INamedObject"]
    assert iface["kind"] == "interface_declaration"
    assert iface["file"] == "src/aspose/threed/INamedObject.ts"
    assert iface["properties"][0]["name"] == "name"
    assert "string" in iface["properties"][0]["type"]

    # `ExtrapolationType` is a real exported enum in
    # src/aspose/threed/animation/ExtrapolationType.ts with a genuine `CONSTANT = 0` member -
    # confirmed in the live checkout, not invented.
    enum = by_name["ExtrapolationType"]
    assert enum["kind"] == "enum_declaration"
    member_names = {m["name"] for m in enum["enum_members"]}
    assert "CONSTANT" in member_names
    constant_member = next(m for m in enum["enum_members"] if m["name"] == "CONSTANT")
    assert constant_member["value"] == "0"
