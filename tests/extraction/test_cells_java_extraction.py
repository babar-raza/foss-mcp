"""Offline tests against the committed cells/java fixture - no network, no live clone.

TC-304's re-pin: the prior fixture (TC-232) predated TC-261 (``inherited_from`` provenance
tagging) and TC-252 (centrality-ranked truncation) landing on main. A 2026-10-09 full-fleet
reconciliation found this fixture had real classes with non-empty ``bases`` but ZERO
``inherited_from`` tags anywhere - the exact defect TC-261 was supposed to close. Re-running
the same extraction now that both TC-252 and TC-261 are on main surfaces real provenance tags:
14 real inherited methods (constructors copied from a parent exception class into a child
exception class), verified live during this card's run, and independently hand-verified
against the real pinned upstream source at commit ``c65329e7257b1311abb9686d7e4957a8cc002955``
(see ``test_a_real_inherited_member_carries_the_correct_inherited_from_tag`` below).

Unlike the slides/java sibling pilot (TC-287), this repository's real inheritance graph is
only one level deep everywhere it exists: every class that has children (``CellsException``,
``PackageStructureException``) itself extends only the external, not-in-repo
``RuntimeException`` - confirmed by reading the real source files at the pinned commit. There
is no genuine multi-level chain in this repository to prove a "rooted to the true original
declarer, not an intermediate" case against; the two tests below instead prove that the one
level of real inheritance that does exist is tagged correctly.

Java-platform extraction support was already proven by the pdf/java onboarding (see
``tests/extraction/test_pdf_java_extraction.py``); this card is a straight run-and-pin
against a second, real, public Java repository
(``aspose-cells-foss/Aspose.Cells-FOSS-for-Java``) - no engine change at all.

The real repository's full public surface is only 191 types - well under the CLI's
default ``--max-types 300`` cap - so this fixture is the complete surface, not a
sub-sample: ``type_count == reduced_type_count == 191`` and ``truncated is False``.

The observed ``kind`` vocabulary is ``{"class_declaration", "enum_declaration",
"interface_declaration"}`` - the same three of Java's top-level type-declaration forms
pdf/java's own fixture exercises.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_java" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "java.yaml"

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
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Java` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "c65329e7257b1311abb9686d7e4957a8cc002955"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 191 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 191
    assert data["truncated"] is False


def test_the_fixture_contains_real_java_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Cells-FOSS-for-Java exported types, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"AutoFilter", "AutoFilterColorFilter", "AlignmentValue"} <= names
    # Observed directly in the live run: this Java port's package is `org.aspose.cells_foss`,
    # not `Aspose.Cells` (the .NET namespace) - not reconstructed from a pattern.
    assert all(entry.get("class_import", "").startswith("org.aspose.cells_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".java") for entry in data["types"])


def test_every_entry_is_a_real_java_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    assert kinds <= {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    }
    assert kinds


def test_real_inheritance_now_carries_inherited_from_provenance() -> None:
    # TC-304's headline assertion: this fixture's real inheritance predates TC-261's
    # inherited_from provenance fix, so before this regeneration every copied member was
    # untagged (confirmed: the prior committed fixture had 19 types with non-empty bases
    # and zero inherited_from tags anywhere). After this live re-run, 14 real copied
    # constructor methods carry the tag; zero properties do, because none of the classes
    # that participate in real inheritance here (Java exception types) declare any
    # properties - only constructors.
    data = _load_fixture()
    inherited_methods = [
        (entry["name"], m["name"], m["inherited_from"])
        for entry in data["types"]
        for m in entry.get("methods", [])
        if m.get("inherited_from")
    ]
    inherited_properties = [
        (entry["name"], p["name"], p["inherited_from"])
        for entry in data["types"]
        for p in entry.get("properties", [])
        if p.get("inherited_from")
    ]
    assert len(inherited_methods) == 14
    assert inherited_properties == []


def test_a_real_inherited_member_carries_the_correct_inherited_from_tag() -> None:
    # Hand-verified against the real pinned upstream source at commit
    # c65329e7257b1311abb9686d7e4957a8cc002955:
    #   org/aspose/cells_foss/CellsException.java declares two constructors directly and
    #   extends java.lang.RuntimeException (not a type in this repository, so there is no
    #   further, deeper level to this chain).
    #   org/aspose/cells_foss/FormulaException.java extends CellsException and declares
    #   only its own one-argument constructor - the two CellsException constructors it
    #   inherits must be tagged back to CellsException itself.
    data = _load_fixture()
    by_name: dict[str, list[dict]] = {}
    for entry in data["types"]:
        by_name.setdefault(entry["name"], []).append(entry)

    cells_exception = by_name["CellsException"][0]
    assert cells_exception["bases"] == ["RuntimeException"]
    own_ctors = [m for m in cells_exception["methods"] if m["name"] == "CellsException"]
    assert len(own_ctors) == 2
    assert all("inherited_from" not in m for m in own_ctors)

    formula_exception = by_name["FormulaException"][0]
    assert formula_exception["bases"] == ["CellsException"]
    formula_methods = [
        m for m in formula_exception["methods"] if m["name"] in {"FormulaException", "CellsException"}
    ]
    own = [m for m in formula_methods if m["name"] == "FormulaException"]
    inherited = [m for m in formula_methods if m["name"] == "CellsException"]
    assert len(own) == 1
    assert "inherited_from" not in own[0]
    assert len(inherited) == 2
    # Rooted to the real declaring class's own import path, not just its bare name.
    assert all(m["inherited_from"] == "org.aspose.cells_foss.CellsException" for m in inherited)


def test_a_second_inheritance_family_also_carries_the_correct_tag() -> None:
    # Hand-verified against the real pinned upstream source: org/aspose/cells_foss/
    # packaging/PackageStructureException.java extends java.lang.RuntimeException directly
    # (again, not a type in this repository) and declares one constructor;
    # MissingPartException extends it and declares no constructor of its own, so its one
    # inherited constructor must be tagged back to PackageStructureException.
    data = _load_fixture()
    by_name: dict[str, list[dict]] = {}
    for entry in data["types"]:
        by_name.setdefault(entry["name"], []).append(entry)

    package_structure_exception = by_name["PackageStructureException"][0]
    assert package_structure_exception["bases"] == ["RuntimeException"]

    missing_part_exception = by_name["MissingPartException"][0]
    assert missing_part_exception["bases"] == ["PackageStructureException"]
    inherited = [m for m in missing_part_exception["methods"] if m["name"] == "PackageStructureException"]
    assert len(inherited) == 1
    assert inherited[0]["inherited_from"] == "org.aspose.cells_foss.packaging.PackageStructureException"
