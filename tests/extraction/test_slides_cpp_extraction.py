"""Offline tests against the committed slides/cpp fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-165 sets
``network: false``, same as TC-011/TC-025/TC-094), so nothing here may reach out;
everything is read from the fixture ``run_extraction.py`` already produced and
committed at ``tests/fixtures/slides_cpp/api_surface.json``.

slides/cpp is a new platform within the already-pinned slides family, on the
already-proven C++ platform (cells/cpp, pdf/cpp) - beyond-pilot onboarding per
REQ-G2-047, not a new pilot. This card does not modify the extraction engine, the
tree-sitter grammar wiring, or run_extraction.py/package_root.py at all - run-and-pin
only, exactly like TC-057/TC-094's own instructions.

"cpp" goes through the ordinary tree-sitter path (grammar name literally ``"cpp"``) via
``tree_sitter_engine.api_surface``. Its ``language`` field reads literally ``"cpp"``, and
the observed ``kind`` vocabulary in the committed (reduced, default ``--max-types 300``-capped)
fixture is the tree-sitter C++ grammar's own node-type names:
``{"function", "class_specifier", "enum_specifier", "struct_specifier"}``.

Unlike cells/cpp (whose real include/src layout is nested one level down, so its fixture
carries an ``Aspose.Cells.Foss.Cpp/...`` prefix on every file), this repository's real
``include`` directory sits directly at the repo root, so ``package_root._detect_cpp_root()``
detects it correctly and every ``file`` value here is relative to ``include/...`` with no
extra prefix. This repository is header-only for every observed public type in this
reduced fixture - every entry's ``file`` ends in ``.h``, never ``.cpp``.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "slides_cpp" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "slides" / "cpp.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

# Fields present on every entry regardless of kind - kind-specific fields like
# `enum_members`, `class_import`, `params`, or `return_type` are deliberately not
# asserted here since they vary between function/class/enum/struct entries.
_COMMON_FIELDS = ("name", "kind", "file", "line", "doc", "methods", "properties", "bases", "reachable")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "cpp"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-Cpp` if this
    # ever needs re-pinning; do not change it to make a test pass.
    #
    # TC-272: regenerated against the real repository's current HEAD, now that TC-252
    # (centrality-ranked reduce_fixture()) and TC-259 (the C1 member-naming fix) are both
    # integrated - this pilot was deliberately deferred out of the TC-255-258/TC-262-264
    # waves until TC-259 landed, to avoid re-baking the fabricated-member-name bug into a
    # freshly-regenerated fixture.
    assert data["source_commit"] == "469ce77a07b988c49deb930632b8276c89ec6b66"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]
    # The real repository (at this fixture's pinned commit, TC-272) has 323 raw public
    # types - over the CLI's default --max-types 300 cap, so this fixture is a genuine,
    # deterministic REDUCED subset, not the full surface.
    assert data["type_count"] == 323
    assert data["reduced_type_count"] == 300
    assert data["truncated"] is True


def test_the_fixture_contains_real_cpp_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Slides-FOSS-for-Cpp types, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"Presentation", "Slide", "SlideCollection", "Shape", "AutoShape", "PortionFormat"} <= names
    # Unlike cells/cpp's nested layout, this repository's `include` directory is at the
    # repo root, so package_root._detect_cpp_root() finds it directly - no extra prefix.
    assert all(entry["file"].startswith("include/") for entry in data["types"])
    # This reduced fixture's public surface is header-only - no `.cpp` entries observed.
    assert all(entry["file"].endswith(".h") for entry in data["types"])


def test_presentation_survives_the_centrality_truncation() -> None:
    """TC-272: this pilot was deliberately deferred out of the TC-255-258/TC-262-264
    regeneration waves until TC-259 (the C1 fabricated-member-name fix) landed. Both
    slides/python and slides/java independently needed that exact fix to recover this
    exact symbol - "Presentation" is this product family's own central type, and a
    truncation that drops it is not a representative fixture.
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    assert "Presentation" in by_name
    presentation = by_name["Presentation"]
    assert presentation["kind"] == "class_specifier"
    assert presentation["file"] == "include/Aspose/Slides/Foss/presentation.h"
    assert presentation["bases"] == ["IPresentation"]


def test_reference_and_pointer_returning_accessors_are_named_after_themselves_not_their_type() -> None:
    """TC-259 fixed C1: a C++ member's reference/pointer return type was mistakenly
    extracted as the member's own name instead of the real accessor name. Spot-checked
    directly against the real upstream header source at this fixture's pinned commit:

    - ``include/Aspose/Slides/Foss/i_base_portion_format.h``:
      ``virtual LineFormat& line_format() = 0;`` / ``virtual FillFormat& fill_format() = 0;``
    - ``include/Aspose/Slides/Foss/i_base_slide.h``:
      ``virtual IPresentation* presentation() = 0;``
    - ``include/Aspose/Slides/Foss/shape.h``:
      ``const ShapeFrame& frame() const override { return frame_; }``

    Each is a genuine reference/pointer-returning accessor whose real name is a snake_case
    verb/noun, never the bare return type - the exact shape C1 fabricated.
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    def method_names_for(type_name: str, return_type: str) -> set[str]:
        entry = by_name[type_name]
        return {m["name"] for m in entry["methods"] if m["return_type"] == return_type}

    assert "line_format" in method_names_for("IPortionFormat", "LineFormat")
    assert "fill_format" in method_names_for("IPortionFormat", "FillFormat")
    assert "presentation" in method_names_for("IBaseSlide", "IPresentation")
    assert {"frame", "raw_frame"} <= method_names_for("Shape", "ShapeFrame")

    # The fabrication bug's own signature: a method literally named after its return
    # type (e.g. a method called "LineFormat" that returns "LineFormat"). None may exist
    # anywhere in this fixture.
    fabricated = [
        (entry["name"], m["name"])
        for entry in data["types"]
        for m in entry.get("methods", [])
        if m.get("name") and m.get("return_type") and m["name"] == m["return_type"]
    ]
    assert fabricated == []


def test_every_entry_is_a_real_cpp_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter C++ grammar's own node-type vocabulary
    # for this repository, not guessed or invented.
    assert kinds == {"function", "class_specifier", "enum_specifier", "struct_specifier"}


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    for entry in data["types"]:
        for key in _COMMON_FIELDS:
            assert key in entry, (entry.get("name"), key)


def test_at_least_one_entry_has_non_empty_bases() -> None:
    data = _load_fixture()
    # Genuine interface-implementation pairs observed in the live run, each deriving
    # from a real base recorded by this fixture.
    with_bases = {entry["name"]: tuple(entry["bases"]) for entry in data["types"] if entry["bases"]}
    assert with_bases, "expected at least one entry with non-empty bases"
    assert with_bases.get("AdjustValueCollection") == ("IAdjustValueCollection",)
    assert with_bases.get("AutoShape") == ("GeometryShape", "IAutoShape")


def test_a_real_cross_file_free_function_survives_consolidation() -> None:
    """TC-092 fixed consolidate_classes() silently collapsing same-named free functions
    across different files down to a single surviving entry. This fixture is real,
    live-extracted data, produced well after that fix - so a genuine free function
    repeated across files must survive as multiple distinct entries, one per file.

    ``to_string_view`` is a real free helper function that recurs verbatim, spot-checked
    directly from this fixture's own committed content, across 20 distinct real header
    files in the live run - this family's own enum-to-string helper pattern, repeated
    once per enum header.
    """
    data = _load_fixture()
    functions_by_name: dict[str, set[str]] = defaultdict(set)
    for entry in data["types"]:
        if entry["kind"] == "function":
            functions_by_name[entry["name"]].add(entry["file"])

    files = functions_by_name.get("to_string_view", set())
    assert len(files) >= 2, files
    assert "include/Aspose/Slides/Foss/bevel_preset_type.h" in files
    assert "include/Aspose/Slides/Foss/line_cap_style.h" in files
