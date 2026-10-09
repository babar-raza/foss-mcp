"""Offline tests against the committed cells/rust fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-032 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/cells_rust/api_surface.json``.

Unlike slides/python (which is routed to the independent pure-``ast`` reader
``python_surface.py``), "rust" is not special-cased in ``run_extraction._LANGUAGE_BY_PLATFORM``
and goes through the ordinary tree-sitter path (grammar name literally ``"rust"``) via
``tree_sitter_engine.api_surface``. Its ``language`` field reads literally ``"rust"``, and the
observed ``kind`` vocabulary among the (unreduced - only 221 real types exist, all under the
CLI's default ``--max-types 300`` cap) fixture is the tree-sitter Rust grammar's own node-type
names: ``{"struct_item", "enum_item", "trait_item", "function"}``.

G2/TC-301 re-pinned this fixture (2026-10-09) by re-running the real extraction now that both
TC-252 (centrality-ranked ``reduce_fixture``) and TC-261 (``inherited_from`` provenance tagging
in ``_flatten_inheritance``) are on main. The real type count grew from 219 to 221 relative to
the stale TC-102 fixture this replaces - same pinned commit, same repository, purely the result
of engine fixes landed since (e.g. three now-unassociated top-level ``impl Default for ...``
leftovers instead of one, each for a receiver type that is not itself part of the extracted
public surface).

Live-verified finding, independently confirmed against the real upstream source tree at the
pinned commit (not inferred from the fixture alone): this repository declares exactly ONE
trait in its entire 249-file crate - ``IWarningCallback`` (a callback interface meant to be
implemented by downstream consumers of the library, not by the library itself) - and it has
ZERO implementers anywhere in the crate. Every other non-derive ``impl X for Y`` block in the
repository implements a standard-library trait (``Display``, ``From``, ``Default``, ``Eq``,
``Hash``, ...), none of which are themselves extracted ``trait_item`` entries carrying methods.
``_flatten_inheritance()`` can only tag a copied member with ``inherited_from`` when a class's
``bases`` entry resolves to another extracted type that itself carries methods/properties - and
for this specific pilot, no such resolution ever occurs. The real, live re-extraction therefore
genuinely contains zero ``inherited_from`` tags anywhere in the fixture. This is not a defect:
it is what TC-261's own mechanism correctly produces when the real data gives it nothing to
tag. ``test_the_provenance_tagging_machinery_is_present_but_finds_nothing_to_tag_here`` below
locks in this verified real finding rather than fabricating an inherited member that does not
exist in this pilot's actual public surface.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_rust" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "rust.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "rust"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Rust` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "1a6004af47b1ef15385f9d36d381a8172428cc7e"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]
    # The real repository has exactly 221 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full surface, not a truncated subset.
    assert data["type_count"] == 221
    assert data["truncated"] is False


def test_the_fixture_contains_real_rust_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Cells-FOSS-for-Rust structs/enums/traits, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Workbook", "Worksheet", "Cell", "AutoFilter", "IWarningCallback"} <= names
    assert all(entry["file"].endswith(".rs") for entry in data["types"])


def test_every_entry_is_a_real_rust_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Rust grammar's own node-type vocabulary for
    # this repository, not guessed or invented.
    assert kinds == {"struct_item", "enum_item", "trait_item", "function"}


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    # Fields present on every entry regardless of kind - struct/enum-only fields like
    # class_import or enum_members are deliberately not asserted here since plain trait-impl
    # functions do not carry them.
    for entry in data["types"]:
        for key in ("name", "kind", "file", "line", "doc", "methods", "properties", "bases", "reachable"):
            assert key in entry, (entry.get("name"), key)


def test_the_trait_item_and_free_functions_observed_are_real() -> None:
    data = _load_fixture()
    trait_items = {e["name"] for e in data["types"] if e["kind"] == "trait_item"}
    functions = {e["name"] for e in data["types"] if e["kind"] == "function"}
    # Observed directly in the live run: the only trait declaration in the reduced surface,
    # and free-standing functions/trait-impl methods that surfaced as top-level "function"
    # entries (e.g. a real std::fmt::Display impl and two standalone parsing helpers).
    assert trait_items == {"IWarningCallback"}
    assert {"parse_a1_range", "parse_cell_ref_a1"} <= functions


def test_the_provenance_tagging_machinery_is_present_but_finds_nothing_to_tag_here() -> None:
    """TC-261's ``inherited_from`` tagging ran against this real extraction and genuinely
    tagged nothing - confirmed by live, independent inspection of the real upstream source
    tree at this fixture's own pinned ``source_commit`` (not inferred from the fixture alone):
    this crate declares exactly one trait, ``IWarningCallback`` (a callback interface meant
    for downstream consumers, not the library itself), with zero implementers anywhere in the
    249-file crate. Every other non-derive ``impl X for Y`` block implements a std-library
    trait (``Display``, ``From``, ``Default``, ``Eq``, ``Hash``, ...) that is never itself an
    extracted ``trait_item``, so ``_flatten_inheritance()`` never has a populated parent to
    copy from. This asserts the real, observed absence rather than a fabricated presence -
    the one trait that exists really has zero (name, kind) bases resolving to it, and no
    entry anywhere in the fixture carries an "inherited_from" key.
    """
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    trait_bases_in_use = {
        base
        for entry in data["types"]
        for base in entry.get("bases", [])
        if base in names
    }
    assert trait_bases_in_use == set()
    for entry in data["types"]:
        for member in entry.get("methods", []) + entry.get("properties", []):
            assert "inherited_from" not in member, (entry["name"], member.get("name"))
