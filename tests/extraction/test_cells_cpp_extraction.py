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

TC-271 regenerated this fixture with TC-252's centrality-ranked ``reduce_fixture()``,
after confirming both TC-259 and TC-276 (the two halves of the C1 fabricated-member-name
fix: inline class-body members and out-of-line qualified ``ClassName::Method(...)``
definitions, respectively) are integrated. Before TC-276, this same live extraction
fabricated 109 phantom top-level entries (named after a method's return type instead of
its real accessor name, e.g. a real ``Workbook& Worksheet::GetWorkbook()`` producing a
fake top-level ``Workbook`` "function" entry) - raising ``type_count`` from the post-fix
346 to a false 462. ``test_no_member_is_fabricated_from_its_own_return_type`` below is
the regression test for that finding, swept across the entire kept 300, not just a
handful of spot-checked classes.
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
    # The real repository has 346 raw public types (post-TC-276; before TC-276 fixed the
    # out-of-line qualified-member fabrication, this same live run produced a false 462 --
    # 116 of them phantom entries, see the module docstring) - over the CLI's default
    # --max-types 300 cap, so this fixture is a genuine, deterministic REDUCED subset,
    # not the full surface (unlike cells/rust's 219-type fixture, which fit uncapped).
    assert data["type_count"] == 346
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


def test_the_highest_centrality_type_survives_truncation() -> None:
    """TC-252's reduce_fixture() keeps the 300 types with the highest centrality score
    (how many other types in the FULL 346-type artifact reference this type's bare name),
    not an alphabetical slice. Measured directly against this fixture's own real data (no
    audit-named symbol exists for this pilot): ``LoadDiagnostics`` is the single type with
    the highest centrality score (15 - computed the same way reduce_fixture() does, by
    counting whole-word references to its name across every other type's bases/
    return_type/params/properties). This is the concrete proof the fix kept the type that
    matters most for this pilot, not a coincidental alphabetical survivor.
    """
    data = _load_fixture()
    load_diagnostics = [e for e in data["types"] if e["name"] == "LoadDiagnostics"]
    assert len(load_diagnostics) == 1, load_diagnostics
    assert load_diagnostics[0]["kind"] == "class_specifier"
    assert load_diagnostics[0]["class_import"] == "Aspose::Cells_FOSS::LoadDiagnostics"


def test_no_member_is_fabricated_from_its_own_return_type() -> None:
    """The concrete C1 regression check for this pilot, swept across the ENTIRE kept 300
    (not just a handful of spot-checked classes): no top-level entry's ``name`` is a bare
    fabrication of some method's return type rather than the real accessor name.

    TC-259 fixed this for inline class-body members (``const Border& GetLeft() const
    noexcept { ... }`` inside the class body - verified below via ``Borders.GetLeft`` and
    ``Border.GetColor``, both genuine reference-returning accessors). TC-276 fixed the
    SAME fabrication for out-of-line, qualified member definitions
    (``Workbook& Worksheet::GetWorkbook() { ... }`` in a .cpp file, outside the class body)
    - verified below directly: before TC-276, this exact real method produced a phantom
    top-level entry named ``Workbook`` (its return type), not ``GetWorkbook`` (its real
    name); that phantom must not exist in this fixture, while the real ``GetWorkbook``
    method must still be present on ``Worksheet``.

    The sweep below is general, not limited to these spot-checks: ANY top-level
    ``"function"``-kind entry whose name collides with a real class/struct/enum name
    already present in this same kept set is exactly the fabrication shape TC-259/TC-276
    fixed (a member - inline or out-of-line - mis-named after its own return type, which
    is itself some other real type in this artifact), so zero such entries may exist.
    """
    data = _load_fixture()
    by_name: dict[str, list[dict]] = defaultdict(list)
    for entry in data["types"]:
        by_name[entry["name"]].append(entry)

    def methods_of(class_name: str) -> dict[str, str]:
        for entry in by_name.get(class_name, []):
            if entry["kind"] in ("class_specifier", "struct_specifier"):
                return {m["name"]: m.get("return_type", "") for m in entry["methods"]}
        return {}

    # Inline-bodied members (TC-259 shape) - real reference-returning accessors, named
    # correctly, not after their own return type.
    borders_methods = methods_of("Borders")
    assert borders_methods.get("GetLeft") == "Border"
    assert "Border" not in borders_methods  # would be the fabricated name, not a real method

    border_methods = methods_of("Border")
    assert border_methods.get("GetColor") == "Color"

    # Out-of-line, qualified member definitions (TC-276 shape) - the exact real-world
    # reproduction found in TC-271's own first attempt.
    worksheet_methods = methods_of("Worksheet")
    assert worksheet_methods.get("GetWorkbook") == "Workbook"

    class_or_enum_names = {
        entry["name"]
        for entry in data["types"]
        if entry["kind"] in ("class_specifier", "struct_specifier", "enum_specifier")
    }
    phantom_candidates = [
        entry
        for entry in data["types"]
        if entry["kind"] == "function" and entry["name"] in class_or_enum_names
    ]
    assert phantom_candidates == [], phantom_candidates
