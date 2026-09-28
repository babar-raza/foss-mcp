"""Offline tests against the committed cells/cpp fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-094 sets
``network: false``, same as TC-011/TC-025/TC-032), so nothing here may reach out;
everything is read from the fixture ``run_extraction.py`` already produced and
committed at ``tests/fixtures/cells_cpp/api_surface.json``.

cells/cpp is the 7th pilot and this project's first real C++ fixture, produced after
TC-092 fixed a real, general bug in ``consolidate_classes()`` that was silently
discarding free functions that shared a name across files. This fixture is the live
proof that the fix holds against a real repository, not just TC-092's own isolated
unit tests: see ``test_a_real_cross_file_free_function_survives_consolidation`` below,
which spot-checks ``EqualsIgnoreCase`` - a genuine free helper (not a constructor-named
function) that recurs verbatim across 7 distinct real source files in this repository.

"cpp" goes through the ordinary tree-sitter path (grammar name literally ``"cpp"``) via
``tree_sitter_engine.api_surface``. Its ``language`` field reads literally ``"cpp"``, and
the observed ``kind`` vocabulary in the committed (reduced, ``--max-types 300``-capped)
fixture is the tree-sitter C++ grammar's own node-type names:
``{"function", "class_specifier", "enum_specifier", "struct_specifier"}``.

Known, non-blocking quirk (confirmed by research ahead of this card, not fixed here):
this repository's real ``include``/``src`` layout is nested one level down under
``Aspose.Cells.Foss.Cpp/{include,src}/aspose/cells_foss/...``, not at the repo root, so
``package_root._detect_cpp_root()`` falls back to the repo root itself (neither
``repo/include`` nor ``repo/src`` exists at top level). Extraction still ran
successfully and produced real, plausible data despite this - every ``file`` value in
this fixture carries that ``Aspose.Cells.Foss.Cpp/...`` prefix rather than being
relative to a detected package root. This is a known quirk in ``package_root.py``, which
is outside this card's ``write_paths`` - not something fixed here.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_cpp" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "cpp.yaml"

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
    # `git ls-remote https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "9f852d0ff1cfdad2d661556d6b87a8eff8c063a2"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]
    # The real repository has 462 raw public types - over the CLI's default
    # --max-types 300 cap, so this fixture is a genuine, deterministic REDUCED subset,
    # not the full surface (unlike cells/rust's 219-type fixture, which fit uncapped).
    assert data["type_count"] == 462
    assert data["reduced_type_count"] == 300
    assert data["truncated"] is True


def test_the_fixture_contains_real_cpp_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Cells-FOSS-for-Cpp types, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"Workbook", "Worksheet", "Cell", "AutoFilter", "Color", "CellsException"} <= names
    assert all(entry["file"].endswith((".h", ".cpp")) for entry in data["types"])
    # Confirms the nested include/src quirk noted above: every file path retains the
    # repo-relative nested prefix rather than being stripped to a detected package root.
    assert all(entry["file"].startswith("Aspose.Cells.Foss.Cpp/") for entry in data["types"])


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
    # Proves TC-010b's inheritance detection still holds for real C++ data: these are
    # genuine exception classes observed in the live run, each deriving from a real base.
    with_bases = {entry["name"]: tuple(entry["bases"]) for entry in data["types"] if entry["bases"]}
    assert with_bases, "expected at least one entry with non-empty bases"
    assert with_bases.get("CellsException") == ("std::exception",)
    assert with_bases.get("FormulaException") == ("CellsException",)


def test_a_real_cross_file_free_function_survives_consolidation() -> None:
    """TC-092 fixed consolidate_classes() silently collapsing same-named free functions
    across different files down to a single surviving entry. This fixture is real,
    live-extracted data, produced AFTER that fix - so a genuine free function repeated
    across files must survive as multiple distinct entries, one per file.

    ``EqualsIgnoreCase`` is a real free helper function (not a constructor sharing a
    class's name, unlike some of this fixture's other repeated names such as ``Color``
    or ``Border``) that recurs verbatim, spot-checked directly from this fixture's own
    committed content, across 7 distinct real source files in the live run.
    """
    data = _load_fixture()
    functions_by_name: dict[str, set[str]] = defaultdict(set)
    for entry in data["types"]:
        if entry["kind"] == "function":
            functions_by_name[entry["name"]].add(entry["file"])

    files = functions_by_name.get("EqualsIgnoreCase", set())
    assert len(files) >= 2, files
    assert "Aspose.Cells.Foss.Cpp/src/aspose/cells_foss/XlsxDocumentProperties.cpp" in files
    assert "Aspose.Cells.Foss.Cpp/src/aspose/cells_foss/XlsxWorkbookSerializer.cpp" in files

    # Not an isolated one-off: several other genuinely repeated free-function/constructor
    # names also survive as multiple distinct entries across this real fixture.
    multi_file_names = {name for name, fs in functions_by_name.items() if len(fs) > 1}
    assert len(multi_file_names) >= 5, multi_file_names
