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
    assert data["source_commit"] == "c41f8dddc499fb0058fc9557cb364d70fbd3cef1"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]
    # The real repository has 322 raw public types - over the CLI's default
    # --max-types 300 cap, so this fixture is a genuine, deterministic REDUCED subset,
    # not the full surface.
    assert data["type_count"] == 322
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
