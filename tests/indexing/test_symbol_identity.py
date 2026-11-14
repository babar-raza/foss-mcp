"""G3/TC-612 (I1, REQ-G3-012): unit tests for `symbol_identity.compute_canonical_identities`.

`symbol_identity.py` is built on top of `chunk_builder._reexport_pairs`/`_slot_units` (the real,
already-tuned pairing engine - TC-382/522/532/541), not a reimplementation of it. These tests therefore
cover: (a) each of `_reexport_pairs`'s three real grouping rules, reproduced synthetically, grouped
correctly under one canonical id with the right alias forms; (b) a REAL regression case against the
real, committed `slides__python` generation, showing a significant reduction in canonical-id count vs
raw entry count; (c) a no-aliases case, where every entry is its own unaliased unit; (d) the shadow and
cycle guards `_reexport_pairs`'s own docstring documents, proving they still hold through this new layer.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from foss_mcp.indexing.chunk_builder import _is_test_source, _items
from foss_mcp.indexing.symbol_identity import SymbolIdentity, compute_canonical_identities
from tests.conformance.corpus import load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


def _method(name: str = "m") -> dict[str, Any]:
    return {"name": name, "params": [], "return_type": ""}


def _entry(
    name: str,
    class_import: str,
    file: str,
    *,
    kind: str = "class",
    members: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "file": file,
        "class_import": class_import,
        "bases": [],
        "methods": [],
        "properties": [],
        **extra,
    }
    if members:
        if kind == "enum":
            entry["enum_members"] = [{"name": "ALPHA", "value": "1"}]
        else:
            entry["methods"] = [_method("save")]
    return entry


# ---------------------------------------------------------------------------
# (a) The three real `_reexport_pairs` rules, reproduced synthetically.
# ---------------------------------------------------------------------------


def test_explicit_reexport_of_chain_groups_under_the_definitions_canonical_id() -> None:
    """TC-522 rule: an entry whose `reexport_of` names another entry's `class_import` pairs with it,
    whatever members the shell itself has. A two-hop chain (shell -> mid -> real definition) is
    followed to the final definition."""
    entries = [
        _entry("Shape", "pkg.shells.Shape", "pkg/shells/__init__.py", reexport_of="pkg.mid.Shape"),
        _entry("Shape", "pkg.mid.Shape", "pkg/mid.py", reexport_of="pkg.real.Shape"),
        _entry("Shape", "pkg.real.Shape", "pkg/real.py", members=True),
    ]
    identities = compute_canonical_identities(entries)
    assert len(identities) == 3
    # All three collapse to the same unit, named for the real definition (index 2), not any shell.
    assert {identity.canonical_id for identity in identities} == {"pkg.real.Shape"}
    assert identities[0].is_definition is False
    assert identities[1].is_definition is False
    assert identities[2].is_definition is True
    # Every entry's own class_import is present as an alias form of the shared unit.
    assert set(identities[0].aliases) >= {"pkg.shells.Shape", "pkg.mid.Shape", "pkg.real.Shape"}
    assert identities[0].aliases == identities[1].aliases == identities[2].aliases


def test_package_init_stub_pairs_with_its_one_definition() -> None:
    """TC-382 rule 8: a member-less stub re-exported from a package `__init__.py` pairs with the one
    other entry of the same name whose `class_import` lives under that package and that has members."""
    entries = [
        _entry("Foo", "pkg.Foo", "pkg/__init__.py", members=False),
        _entry("Foo", "pkg.sub.Foo", "pkg/sub/foo.py", members=True),
        _entry("Bar", "pkg.Bar", "pkg/bar.py", members=True),  # unrelated, untouched
    ]
    identities = compute_canonical_identities(entries)
    assert identities[0].canonical_id == "pkg.sub.Foo"
    assert identities[1].canonical_id == "pkg.sub.Foo"
    assert identities[0].is_definition is False
    assert identities[1].is_definition is True
    assert set(identities[0].aliases) == {"pkg.Foo", "pkg.sub.Foo"}
    # The unrelated entry is its own, unaliased unit.
    assert identities[2].canonical_id == "pkg.Bar"
    assert identities[2].aliases == ("pkg.Bar",)
    assert identities[2].is_definition is True


def test_memberless_same_name_class_falls_back_to_the_one_definition_anywhere() -> None:
    """TC-541: a member-less `class`/`enum` of a `.py` file that the earlier rules did not pair pairs
    with the ONE `.py` entry of the same name and kind that has members, wherever it lives."""
    entries = [
        _entry("Widget", "other.Widget", "other/mod.py", kind="class", members=False),
        _entry("Widget", "real.Widget", "real/mod.py", kind="class", members=True),
    ]
    identities = compute_canonical_identities(entries)
    assert identities[0].canonical_id == "real.Widget"
    assert identities[1].canonical_id == "real.Widget"
    assert identities[0].is_definition is False
    assert identities[1].is_definition is True
    assert set(identities[0].aliases) == {"other.Widget", "real.Widget"}


# ---------------------------------------------------------------------------
# (b) Real regression: the committed slides__python generation.
# ---------------------------------------------------------------------------


def test_slides_python_real_fixture_canonical_ids_significantly_fewer_than_raw_entries() -> None:
    """Loads the real, committed `tests/fixtures/slides_python/api_surface.json` the same way
    `chunk_builder.build_chunks_from_api_surface` does (filtering test sources via `_is_test_source`,
    the same filter the real pipeline applies before calling `_reexport_pairs`), and confirms the
    canonical-id count this module computes is significantly lower than the raw entry count.

    Cross-checked against `tests/conformance/corpus.py`'s loader over the real, committed
    `infra/demo-manifests` generation: that generation's own rendered symbol-chunk count for this same
    pilot is used only as an independent sanity check that the raw entry count below is the real,
    currently-served population, not a stale fixture.
    """
    fixture_path = FIXTURES_DIR / "slides_python" / "api_surface.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    entries = [entry for entry in _items(fixture["types"]) if not _is_test_source(entry.get("file"))]
    raw_entry_count = len(entries)

    with tempfile.TemporaryDirectory() as tmp:
        corpus = load_demo_manifests_corpus(Path(tmp))
        pilot = corpus.pilots["slides__python"]
        served_symbol_chunk_count = len(pilot.symbol_chunks())

    identities = compute_canonical_identities(entries)
    assert len(identities) == raw_entry_count
    canonical_ids = {identity.canonical_id for identity in identities}

    # Real, measured numbers (2026-10-30, against this worktree's committed fixture): 1023 raw entries,
    # 517 distinct canonical ids - a ~49.5% reduction, consistent with TC-607's own "320 duplicate-body
    # groups across 764 of 1023 symbol chunks" finding (TC-607 measured duplication via rendered-body
    # hashing; this module measures it via the real pairing engine's own fields, so the two numbers are
    # related, not required to be identical).
    # TC-841: slides_python's fixture was regenerated (1023 -> 1025 entries: 2 module-level constants) ahead of its baked
    # generation, which tests/deploy/test_fleet_consistency.py lists in STALE_BAKED_PILOTS until the next cohort cards
    # re-bake it. While listed, the baked chunk count may only be BELOW the fixture's (the 2 constants it lacks); the
    # equality is restored the moment the entry is removed, and this check then fails closed.
    import importlib

    stale = importlib.import_module("tests.deploy.test_fleet_consistency").STALE_BAKED_PILOTS
    if "slides_python" in stale:
        assert served_symbol_chunk_count == raw_entry_count - 2, (served_symbol_chunk_count, raw_entry_count)
    else:
        assert raw_entry_count == served_symbol_chunk_count, (
            "the fixture's raw entry count should match the real generation's served symbol-chunk count "
            f"for this pilot - got {raw_entry_count} raw entries vs {served_symbol_chunk_count} served chunks"
        )
    assert len(canonical_ids) < raw_entry_count * 0.75, (
        f"expected a significant reduction, got {len(canonical_ids)} canonical ids "
        f"from {raw_entry_count} raw entries"
    )
    definition_count = sum(1 for identity in identities if identity.is_definition)
    assert definition_count == len(canonical_ids)


# ---------------------------------------------------------------------------
# (c) No re-exports at all: every entry is its own unaliased unit.
# ---------------------------------------------------------------------------


def test_no_reexports_every_entry_is_its_own_unaliased_unit() -> None:
    entries = [
        _entry("Alpha", "pkg.Alpha", "pkg/alpha.py", members=True),
        _entry("Beta", "pkg.Beta", "pkg/beta.py", members=True),
        _entry("Gamma", "pkg.Gamma", "pkg/gamma.py", kind="enum", members=True),
    ]
    identities = compute_canonical_identities(entries)
    assert len(identities) == 3
    assert [identity.canonical_id for identity in identities] == ["pkg.Alpha", "pkg.Beta", "pkg.Gamma"]
    assert all(identity.is_definition for identity in identities)
    assert identities[0].aliases == ("pkg.Alpha",)
    assert identities[1].aliases == ("pkg.Beta",)
    assert identities[2].aliases == ("pkg.Gamma",)
    # Every entry is its own canonical id - no collapsing at all.
    assert len({identity.canonical_id for identity in identities}) == 3


def test_identity_result_is_pure_and_deterministic() -> None:
    """Calling `compute_canonical_identities` twice on the same (never mutated) input returns equal
    results, and the input entries are left untouched."""
    entries = [
        _entry("Alpha", "pkg.shell.Alpha", "pkg/shell/__init__.py", reexport_of="pkg.real.Alpha"),
        _entry("Alpha", "pkg.real.Alpha", "pkg/real.py", members=True),
    ]
    snapshot = json.dumps(entries, sort_keys=True)
    first = compute_canonical_identities(entries)
    second = compute_canonical_identities(entries)
    assert json.dumps(entries, sort_keys=True) == snapshot
    assert first == second
    assert len(first) == 2
    assert all(isinstance(identity, SymbolIdentity) for identity in first)
    # Real grouping must actually have happened, not merely produced two equal, empty results.
    assert first[0].canonical_id == "pkg.real.Alpha"
    assert first[1].canonical_id == "pkg.real.Alpha"
    assert first[1].is_definition is True


# ---------------------------------------------------------------------------
# (d) Shadow and cycle guards still hold through this new layer.
# ---------------------------------------------------------------------------


def test_shadowing_entry_is_never_paired_as_a_memberless_stub() -> None:
    """TC-532: an entry that itself `shadows` another is never treated as a member-less stub to be
    paired away (rule 8/TC-541 both `continue` when `entry.get("shadows")` is truthy) - it stays its own
    unit even though a same-name, member-bearing entry exists elsewhere."""
    entries = [
        _entry(
            "Baz",
            "pkg.exported.Baz",
            "pkg/exported.py",
            kind="class",
            members=False,
            shadows="pkg.old.OldBaz",
        ),
        _entry("Baz", "pkg.real.Baz", "pkg/real.py", kind="class", members=True),
    ]
    identities = compute_canonical_identities(entries)
    assert identities[0].canonical_id == "pkg.exported.Baz"
    assert identities[0].is_definition is True
    assert identities[0].aliases == ("pkg.exported.Baz", "pkg.old.OldBaz")
    assert identities[1].canonical_id == "pkg.real.Baz"
    assert identities[1].is_definition is True


def test_shadowed_by_entry_is_never_used_as_a_fallback_definition() -> None:
    """TC-532: an entry another entry's `shadowed_by` names is excluded from the member-less-fallback
    candidate pool (`_reexport_pairs`'s own `definitions` dict skips any entry carrying
    `shadowed_by`), so a member-less same-name entry is left unpaired rather than wrongly grouped with
    the replaced class."""
    entries = [
        _entry(
            "Qux",
            "pkg.old.Qux",
            "pkg/old.py",
            kind="class",
            members=True,
            shadowed_by="pkg.new.Qux",
        ),
        _entry("Qux", "pkg.stub.Qux", "pkg/stub.py", kind="class", members=False),
    ]
    identities = compute_canonical_identities(entries)
    # Neither entry is paired with the other: the only candidate definition is excluded by its own
    # shadowed_by marker, so the member-less entry stays its own unit.
    assert identities[0].canonical_id == "pkg.old.Qux"
    assert identities[1].canonical_id == "pkg.stub.Qux"
    assert identities[0].is_definition is True
    assert identities[1].is_definition is True


def test_reexport_cycle_leaves_both_entries_unpaired_without_crashing() -> None:
    """`_reexport_pairs`'s own docstring: a chain that loops is not paired. Two entries whose
    `reexport_of` each name the other must not pair, must not crash, and must each remain their own,
    unaliased unit."""
    entries = [
        _entry("A", "pkg.A", "pkg/a.py", reexport_of="pkg.B"),
        _entry("B", "pkg.B", "pkg/b.py", reexport_of="pkg.A"),
    ]
    identities = compute_canonical_identities(entries)
    assert identities[0].canonical_id == "pkg.A"
    assert identities[1].canonical_id == "pkg.B"
    assert identities[0].is_definition is True
    assert identities[1].is_definition is True
    assert identities[0].aliases == ("pkg.A", "pkg.B")
    assert identities[1].aliases == ("pkg.B", "pkg.A")


def test_reexport_of_naming_itself_is_not_paired() -> None:
    """`_reexport_pairs`'s own docstring: "an entry that names itself" is not paired - the single-entry
    degenerate case of the cycle guard."""
    entries = [_entry("A", "pkg.A", "pkg/a.py", reexport_of="pkg.A", members=True)]
    identities = compute_canonical_identities(entries)
    assert len(identities) == 1
    assert identities[0].canonical_id == "pkg.A"
    assert identities[0].is_definition is True
    assert identities[0].aliases == ("pkg.A",)
