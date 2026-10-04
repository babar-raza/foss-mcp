"""Offline tests against the committed slides/java fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-044 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/slides_java/api_surface.json``.

``java`` is an ordinary tree-sitter platform routed through
``tree_sitter_engine.api_surface`` via the ``"java"`` grammar name, the same path as
pdf/java. The real repository has 241 public types - over the CLI's default
``--max-types 300`` cap is not reached, but the ~2MB fixture budget is, so this fixture was
generated with an explicit ``--max-types 150``, landing at 1,317,879 bytes.
``truncated`` is ``True`` and ``reduced_type_count`` is 150, not the full 241.

Unlike pdf/java's reduced subset, this surface does exercise Java's ``record`` form: two
``record_declaration`` entries (``CommentsPartEntry``, ``FontData``). Their presence depends
on the TC-168 fix to the tree-sitter Java record_declaration handling, which is what makes
the kind assertion below meaningful rather than vacuous.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "slides_java" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "slides" / "java.yaml"

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
    # Recorded from the real shallow clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-Java` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "620a2614418854b4a18966a361e6907ddc88c7cb"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]
    assert data["source_repository"] == "aspose-slides-foss/Aspose.Slides-FOSS-for-Java"


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has 241 public types; this fixture was generated with an explicit
    # --max-types 150 to stay under the ~2MB budget.
    assert data["type_count"] == 241
    assert data["reduced_type_count"] == 150
    assert data["truncated"] is True


def test_the_fixture_contains_real_java_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Slides-FOSS-for-Java exported types, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"ISlide", "AutoShape", "BevelPresetType", "FontData", "CommentsPartEntry"} <= names
    assert all(entry["file"].endswith(".java") for entry in data["types"])


def test_every_entry_is_a_real_java_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Java grammar's own node-type vocabulary for
    # this repository, not guessed or invented. Includes record_declaration, which only the
    # TC-168 engine fix makes extractable.
    assert kinds == {
        "class_declaration",
        "enum_declaration",
        "interface_declaration",
        "record_declaration",
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
            "visibility",
            "class_import",
            "canonical_namespace",
        ):
            assert key in entry, (entry.get("name"), key)


def test_a_real_interface_class_enum_and_record_are_present() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # ISlide is a real interface extending IBaseSlide, with real methods.
    slide = by_name["ISlide"]
    assert slide["kind"] == "interface_declaration"
    assert slide["bases"] == ["IBaseSlide"]
    assert len(slide["methods"]) == 16

    # AutoShape is a real class extending GeometryShape and implementing IAutoShape.
    auto_shape = by_name["AutoShape"]
    assert auto_shape["kind"] == "class_declaration"
    assert auto_shape["bases"] == ["GeometryShape", "IAutoShape"]
    method_names = {m["name"] for m in auto_shape["methods"]}
    assert {"getShapeType", "setShapeType"} <= method_names

    # BevelPresetType is a real enum with real members.
    enum = by_name["BevelPresetType"]
    assert enum["kind"] == "enum_declaration"
    enum_member_names = {m["name"] for m in enum["enum_members"]}
    assert {"NOT_DEFINED", "ANGLE", "ART_DECO"} <= enum_member_names

    # FontData is a real record (the TC-168 case) with a real accessor.
    record = by_name["FontData"]
    assert record["kind"] == "record_declaration"
    record_methods = {m["name"]: m for m in record["methods"]}
    assert "getFontName" in record_methods
    assert record_methods["getFontName"]["return_type"] == "String"
