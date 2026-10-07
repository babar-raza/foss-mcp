"""Offline tests against the committed 3d/java fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/3d_java/api_surface.json``.

Java is an ordinary tree-sitter platform (not special-cased like ``python``), routed through
``tree_sitter_engine.api_surface`` via the ``"java"`` grammar name - already proven by the
pdf/java onboarding; this card is a straight run-and-pin against a second, real, public Java
repository (``aspose-3d-foss/Aspose.3D-FOSS-for-Java``).

The real repository's full public surface is only 266 types - under the CLI's default
``--max-types 300`` cap - so this fixture was generated with the default cap and is the
complete surface, not a sub-sample: ``type_count == reduced_type_count == 266`` and
``truncated is False``.

The observed ``kind`` vocabulary among the fixture is the tree-sitter Java grammar's own
node-type names: ``{"class_declaration", "enum_declaration", "interface_declaration"}`` -
Java's three top-level type-declaration forms, the same closed set pdf/java's fixture
exercises (this repository's public surface happens to contain no ``record`` or
``annotation_type_declaration`` entries either).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "3d_java" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "3d" / "java.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "java"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-3d-foss/Aspose.3D-FOSS-for-Java` if this ever
    # needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "3d2ed6be91f5abdd1c52ecbcfa192719883cb1b3"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository's complete public surface is only 266 types - under the CLI's
    # default --max-types 300 cap - so this fixture is the full surface, not a sub-sample.
    assert data["type_count"] == 266
    assert data["reduced_type_count"] == 266
    assert data["truncated"] is False


def test_the_fixture_contains_real_java_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.3D-FOSS-for-Java exported types, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"A3DObject", "IBuffer", "AlphaSource"} <= names
    assert all(entry["file"].endswith(".java") for entry in data["types"])


def test_every_entry_is_a_real_java_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Java grammar's own node-type vocabulary for
    # this repository, not guessed or invented: the same three of Java's top-level
    # type-declaration forms that pdf/java's fixture also exercises.
    assert kinds == {"class_declaration", "enum_declaration", "interface_declaration"}


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
            "visibility",
            "class_import",
            "canonical_namespace",
        ):
            assert key in entry, (entry.get("name"), key)


def test_a_real_class_a_real_interface_and_a_real_enum_are_present() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # A3DObject is a real class that implements INamedObject and has real two-constructor and
    # getName()/setName() accessors.
    document = by_name["A3DObject"]
    assert document["kind"] == "class_declaration"
    assert document["bases"] == ["INamedObject"]
    methods = {m["name"] for m in document["methods"]}
    assert "getName" in methods
    assert "setName" in methods
    get_name = next(m for m in document["methods"] if m["name"] == "getName" and not m["params"])
    assert get_name["return_type"] == "String"
    assert get_name["is_constructor"] is False

    # IBuffer is a real marker interface with no methods of its own.
    interface = by_name["IBuffer"]
    assert interface["kind"] == "interface_declaration"
    assert interface["methods"] == []

    # AlphaSource is a real enum with real members.
    enum = by_name["AlphaSource"]
    assert enum["kind"] == "enum_declaration"
    enum_member_names = {m["name"] for m in enum["enum_members"]}
    assert {"NONE", "PIXEL_ALPHA", "FIXED_VALUE"} <= enum_member_names
