"""G3/TC-631 (REQ-G3-012): `chunk_builder._reexport_pairs` must never pair a production entry's
identity to a test-source candidate, regardless of which caller's input still contains test
entries.

Background (FAILED_INTERNAL, found by an independent post-integration review of TC-612/614/620):
the API-Coverage NUMERATOR (`chunk_builder.build_chunks_from_api_surface`, TC-614) filters
test-source entries out of its `candidates` list BEFORE calling `_reexport_pairs`; the DENOMINATOR
(`run_extraction.summarize_symbols`, TC-620, fed by `extract_pinned_repository`) calls
`symbol_identity.compute_canonical_identities` over the FULL, unfiltered `types` list first, and
only filters test entries out afterward, when counting. `_reexport_pairs`'s own TC-382
(`__init__.py` stub) and TC-541 (member-less class/enum fallback) heuristic rules had no
test-source exclusion of their own, so on the denominator's unfiltered input a member-less
PRODUCTION entry could be paired (for identity purposes only) to a has-members TEST-FIXTURE entry
of the same name/kind, and the production entry would then read as "aliased" (`is_definition is
False`) and be excluded from the denominator - while the exact same entry was still counted
normally by the numerator, which never saw the test entry at all. Net effect: a real production
symbol could silently vanish from the coverage denominator only, so `N of M` could read `M` too
small and even `> 100%`.

These tests prove, with synthetic in-memory entries (no fixture file needed - the bug is a pure
logic gap in `_reexport_pairs` itself):

  (a) the exact repro case - a member-less production entry is no longer paired to a has-members
      test-dir entry of the same name/kind - under BOTH heuristic rules (one case per rule);
  (b) the same scenario, but with the "definition" entry moved to a non-test directory, still
      pairs correctly (proving the fix did not just disable either rule outright);
  (c) the NUMERATOR path (`build_chunks_from_api_surface`, which already pre-filters) and the
      DENOMINATOR path (`run_extraction.summarize_symbols`, fed via `compute_canonical_identities`
      over the SAME unfiltered entries) now agree on whether the production entry counts, for the
      exact repro case - the card's own real acceptance bar (numerator/denominator consistency),
      compared directly between the two real functions' own real outputs.
"""

from __future__ import annotations

from typing import Any

from foss_mcp.extraction.run_extraction import summarize_symbols
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.symbol_identity import compute_canonical_identities

# ---------------------------------------------------------------------------
# Shared synthetic entries: a member-less production `Shape` (TC-541's fallback rule) and a
# member-less-stub `Foo` re-exported from a package `__init__.py` (TC-382's stub rule), each with a
# would-be "definition" candidate of the same name/kind that starts out living in a test directory.
# ---------------------------------------------------------------------------


def _shape_production(**extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "Shape",
        "kind": "class",
        "file": "app/shapes.py",
        "class_import": "app.shapes.Shape",
        "methods": [],
        "properties": [],
    }
    entry.update(extra)
    return entry


def _shape_definition(**extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "Shape",
        "kind": "class",
        "file": "app/tests/helpers.py",
        "class_import": "app.tests.helpers.Shape",
        "methods": [{"name": "area", "params": [], "return_type": "float"}],
        "properties": [],
    }
    entry.update(extra)
    return entry


def _foo_init_stub(**extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "Foo",
        "kind": "class",
        "file": "pkg/__init__.py",
        "class_import": "pkg.Foo",
        "methods": [],
        "properties": [],
    }
    entry.update(extra)
    return entry


def _foo_definition(**extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "Foo",
        "kind": "class",
        "file": "pkg/tests/foo.py",
        "class_import": "pkg.tests.Foo",
        "methods": [{"name": "m", "params": [], "return_type": ""}],
        "properties": [],
    }
    entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# (a) The repro case: a member-less production entry must not pair to a has-members test-dir
# entry of the same name/kind, under either heuristic rule.
# ---------------------------------------------------------------------------


def test_tc541_memberless_production_class_not_paired_to_test_dir_definition() -> None:
    """TC-541's fallback rule (member-less class/enum, matched anywhere by name+kind): the exact
    repro the card describes - production `Shape` in `app/shapes.py`, a has-members `Shape` in the
    test directory `app/tests/helpers.py`."""
    entries = [_shape_production(), _shape_definition()]
    identities = compute_canonical_identities(entries)
    assert identities[0].is_definition is True, "the production entry must stay its own unit"
    assert identities[1].is_definition is True, "a test entry is never used as a fallback definition"
    assert identities[0].canonical_id != identities[1].canonical_id


def test_tc382_init_stub_not_paired_to_test_dir_definition() -> None:
    """TC-382's stub rule (a package `__init__.py` stub paired with its one member-bearing
    definition under the same package): the definition candidate living in a test directory must
    not be selected."""
    entries = [_foo_init_stub(), _foo_definition()]
    identities = compute_canonical_identities(entries)
    assert identities[0].is_definition is True, "the init stub must stay its own unit"
    assert identities[1].is_definition is True, "a test entry is never used as the stub's definition"
    assert identities[0].canonical_id != identities[1].canonical_id


def test_tc541_memberless_enum_not_paired_to_test_dir_definition() -> None:
    """The same TC-541 fallback rule, over the ``enum`` kind it also covers (not just ``class``),
    confirming the fix is not accidentally scoped to one kind only."""
    entries = [
        {
            "name": "Color",
            "kind": "enum",
            "file": "app/colors.py",
            "class_import": "app.colors.Color",
            "enum_members": [],
        },
        {
            "name": "Color",
            "kind": "enum",
            "file": "app/tests/fixtures.py",
            "class_import": "app.tests.fixtures.Color",
            "enum_members": [{"name": "RED", "value": "1"}],
        },
    ]
    identities = compute_canonical_identities(entries)
    assert identities[0].is_definition is True
    assert identities[1].is_definition is True
    assert identities[0].canonical_id != identities[1].canonical_id


# ---------------------------------------------------------------------------
# (b) Positive control: moving the definition to a non-test directory must still pair normally -
# proving the fix excludes test sources specifically, not either rule outright.
# ---------------------------------------------------------------------------


def test_tc541_memberless_class_still_pairs_to_a_non_test_definition() -> None:
    entries = [
        _shape_production(),
        _shape_definition(file="app/generated/shapes.py", class_import="app.generated.shapes.Shape"),
    ]
    identities = compute_canonical_identities(entries)
    assert identities[0].is_definition is False
    assert identities[1].is_definition is True
    assert identities[0].canonical_id == identities[1].canonical_id == "app.generated.shapes.Shape"


def test_tc382_init_stub_still_pairs_to_a_non_test_definition() -> None:
    entries = [
        _foo_init_stub(),
        _foo_definition(file="pkg/sub/foo.py", class_import="pkg.sub.Foo"),
    ]
    identities = compute_canonical_identities(entries)
    assert identities[0].is_definition is False
    assert identities[1].is_definition is True
    assert identities[0].canonical_id == identities[1].canonical_id == "pkg.sub.Foo"


# ---------------------------------------------------------------------------
# (c) The card's own real acceptance bar: the numerator and denominator must now agree on whether
# the production entry counts, for the exact repro case - compared directly between the two real
# functions' own real outputs, not merely `_reexport_pairs` in isolation.
# ---------------------------------------------------------------------------


def test_numerator_and_denominator_agree_on_the_production_entry_count() -> None:
    entries = [_shape_production(), _shape_definition()]

    # NUMERATOR: build_chunks_from_api_surface filters test entries out of `candidates` *before*
    # calling `_reexport_pairs`, so the test entry never enters the pairing pool at all for this
    # path - it is tested here exactly as the real pipeline calls it, fixture and all.
    fixture = {"source_repository": "org/repo", "source_commit": "c0ffee", "types": entries}
    numerator_chunks = build_chunks_from_api_surface(fixture, title="t", max_types=10_000)
    numerator_text = "\n".join(chunk.text for chunk in numerator_chunks)
    numerator_count = numerator_text.count("FQN: app.shapes.Shape")
    assert numerator_count == 1, "the numerator's own already-filtered path must count it once"

    # DENOMINATOR: identities computed over the FULL, unfiltered list, exactly as
    # `run_extraction.extract_pinned_repository` calls `compute_canonical_identities(types)`, then
    # fed into `summarize_symbols` exactly as `run_extraction` does.
    identities = compute_canonical_identities(entries)
    denominator_counts = summarize_symbols(entries, identities)
    assert denominator_counts["types"] == numerator_count, (
        "the denominator must now agree with the numerator on whether the production entry "
        f"counts - numerator counted it {numerator_count} time(s), denominator counted "
        f"{denominator_counts['types']}"
    )
    assert denominator_counts["types"] == 1


def test_denominator_alone_counts_the_production_entry_exactly_once() -> None:
    """A direct check on `summarize_symbols`'s own output (not just the cross-path comparison
    above): before this fix, the production entry's identity came back `is_definition=False`
    (paired to the test entry) and was excluded from the denominator entirely (`types == 0`)."""
    entries = [_shape_production(), _shape_definition()]
    identities = compute_canonical_identities(entries)
    assert identities[0].is_definition is True
    counts = summarize_symbols(entries, identities)
    assert counts == {"types": 1, "functions": 0}


def test_numerator_path_is_a_complete_no_op_for_this_fix() -> None:
    """`build_chunks_from_api_surface` already filters test entries out of `candidates` before
    calling `_reexport_pairs` (TC-614's own pre-filter), so this fix - which only changes
    `_reexport_pairs`'s own two heuristic rules - must be a complete no-op for it: the rendered
    chunks are byte-for-byte identical whether or not the test-dir definition entry is present in
    the raw `types` list at all."""
    fixture_with_test_entry = {
        "source_repository": "org/repo",
        "source_commit": "c0ffee",
        "types": [_shape_production(), _shape_definition()],
    }
    fixture_without_test_entry = {
        "source_repository": "org/repo",
        "source_commit": "c0ffee",
        "types": [_shape_production()],
    }
    texts_with = [
        chunk.text
        for chunk in build_chunks_from_api_surface(fixture_with_test_entry, title="t", max_types=10_000)
    ]
    texts_without = [
        chunk.text
        for chunk in build_chunks_from_api_surface(fixture_without_test_entry, title="t", max_types=10_000)
    ]
    assert texts_with == texts_without
