"""TC-620 (I3, REQ-G3-012): `run_extraction.summarize_symbols`'s canonical-identity denominator.

TC-614 (I2) already deduplicated the `API-Coverage:` NUMERATOR (`chunk_builder._coverage_line`)
using I1's canonical-identity model (`symbol_identity.compute_canonical_identities`, TC-612), so a
pilot with real aliases honestly shows a ratio below 100% for the first time. The DENOMINATOR
(`run_extraction.summarize_symbols`'s own `symbol_counts.types`) was deliberately left inflated by
TC-614 - it still counts every raw extracted entry, aliases included. This card closes that gap:
`summarize_symbols` grows an optional `identities` parameter that, when given, counts each
canonical unit once, mirroring TC-614's exact dedup logic.

No mocks for the real-pilot cases: this file reads the real, committed `tests/fixtures/slides_python`
extraction fixture and writes nothing to it.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction
from foss_mcp.indexing import chunk_builder
from foss_mcp.indexing.symbol_identity import compute_canonical_identities

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


# --------------------------------------------------------------------------- (a) backward compat


def test_summarize_symbols_old_one_argument_form_is_unchanged() -> None:
    """The exact entries/expectation from
    `test_run_extraction_coverage_policy.py::test_symbol_counts_separate_types_from_functions` -
    calling `summarize_symbols` with the OLD one-argument form must produce IDENTICAL output to
    before this card."""
    types = [
        {"name": "A", "kind": "type_spec", "file": "a.go"},
        {"name": "B", "kind": "type_spec", "file": "b.go"},
        {"name": "C", "kind": "type_spec", "file": "c.go"},
        {"name": "F", "kind": "function", "file": "a.go", "receiver_type": "A"},
        {"name": "G", "kind": "function", "file": "b.go", "receiver_type": "B"},
        {"name": "TestF", "kind": "function", "file": "a_test.go"},
    ]
    assert run_extraction.summarize_symbols(types) == {"types": 3, "functions": 2}
    assert run_extraction.summarize_symbols([]) == {"types": 0, "functions": 0}


def test_summarize_symbols_identities_none_default_matches_no_argument_call() -> None:
    """`identities=None` (the default) must preserve the EXACT prior behaviour byte for byte, so
    every existing one-argument caller is unaffected - explicit `identities=None` must be
    indistinguishable from omitting the argument entirely, on a fixture that DOES carry a real
    alias pair (so the no-op is proven on the exact shape this card changes, not only on a fixture
    with nothing to dedupe)."""
    definition = {
        "name": "Widget",
        "kind": "class",
        "file": "pkg/core.py",
        "class_import": "pkg.core.Widget",
        "methods": [{"name": "m", "params": [], "return_type": ""}],
        "properties": [],
    }
    shell = {
        "name": "Widget",
        "kind": "class",
        "file": "pkg/__init__.py",
        "class_import": "pkg.Widget",
        "reexport_of": "pkg.core.Widget",
        "methods": [],
        "properties": [],
    }
    types = [definition, shell]
    no_arg = run_extraction.summarize_symbols(types)
    explicit_none = run_extraction.summarize_symbols(types, identities=None)
    assert no_arg == explicit_none == {"types": 2, "functions": 0}


# --------------------------------------------------------------------------- (b) synthetic dedup


def _reexport_pair() -> list[dict[str, Any]]:
    """A real shell/definition pair matching `_reexport_pairs`' rule 1 (TC-522, the explicit
    `reexport_of` chain): a package `__init__.py` shell naming the `class_import` of the
    member-bearing definition it re-exports."""
    definition = {
        "name": "Widget",
        "kind": "class",
        "file": "pkg/core.py",
        "class_import": "pkg.core.Widget",
        "methods": [{"name": "m", "params": [], "return_type": ""}],
        "properties": [],
    }
    shell = {
        "name": "Widget",
        "kind": "class",
        "file": "pkg/__init__.py",
        "class_import": "pkg.Widget",
        "reexport_of": "pkg.core.Widget",
        "methods": [{"name": "m", "params": [], "return_type": ""}],  # TC-522: shell may carry members
        "properties": [],
    }
    return [shell, definition]


def test_reexport_pair_is_actually_paired_by_the_real_grouping_engine() -> None:
    """Confirms the synthetic fixture really exercises `chunk_builder._reexport_pairs`' rule 1,
    rather than accidentally being a unit of two unrelated entries."""
    types = _reexport_pair()
    pairs = chunk_builder._reexport_pairs(types)
    assert pairs == {0: 1}  # shell (index 0) pairs with its definition (index 1)


def test_identities_given_dedupes_the_types_count_below_the_undeduped_count() -> None:
    types = _reexport_pair()
    without = run_extraction.summarize_symbols(types)
    identities = compute_canonical_identities(types)
    with_identities = run_extraction.summarize_symbols(types, identities=identities)
    assert without == {"types": 2, "functions": 0}
    assert with_identities == {"types": 1, "functions": 0}
    assert with_identities["types"] < without["types"]
    # the function count, and every entry that is not part of an alias group, is untouched
    types_with_function = types + [{"name": "f", "kind": "function", "file": "pkg/core.py"}]
    identities2 = compute_canonical_identities(types_with_function)
    deduped2 = run_extraction.summarize_symbols(types_with_function, identities=identities2)
    assert deduped2 == {"types": 1, "functions": 1}


def test_identities_given_excludes_test_entries_same_as_without() -> None:
    """The test-entry filter still applies identically whether or not identities is given - a
    re-export pair recorded under a `tests/` directory is still excluded from both counts."""
    shell, definition = _reexport_pair()
    shell["file"] = "pkg/tests/__init__.py"
    definition["file"] = "pkg/tests/core.py"
    types = [shell, definition]
    identities = compute_canonical_identities(types)
    assert run_extraction.summarize_symbols(types) == {"types": 0, "functions": 0}
    assert run_extraction.summarize_symbols(types, identities=identities) == {"types": 0, "functions": 0}


# --------------------------------------------------------------------------- (c) real regression: slides_python


def _slides_python_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "slides_python" / "api_surface.json").read_text(encoding="utf-8"))


def test_slides_python_denominator_drops_from_the_old_inflated_count() -> None:
    """The real regression TC-614's own closeout predicted but deliberately left open: run
    `summarize_symbols` with real `compute_canonical_identities` output against the real,
    committed `slides_python` extraction fixture, and confirm the real denominator drops from the
    old, inflated count to the real, deduplicated one.

    Measured (stated here, not assumed): the OLD, un-deduplicated `types` count, recomputed fresh
    from the fixture's own `types` array with the one-argument (no-identities) form, is 1022 - this
    was the fixture's own stored `symbol_counts` value before TC-729 backfilled it (TC-729, 2026-11-
    09: this pilot's fixture predated TC-620 and carried this stale value until then). The NEW,
    canonically-deduplicated count is 516 - the exact same number TC-614 already measured for the
    numerator of this same pilot ("516 of 1022 types") - and TC-729 backfilled that NEW value
    directly onto the fixture's own committed `symbol_counts`, so the pilot's own denominator and
    numerator now agree in the fixture itself, not only at render time.
    """
    fixture = _slides_python_fixture()
    types = fixture["types"]

    old = run_extraction.summarize_symbols(types)
    # TC-841: 1022 -> 1024 and 516 -> 518 below: the fixture was regenerated with the current extractor, which adds the two
    # module-level constants of the package (C15_EXT_URI, C15_NS; TC-700) as entries; both are canonical, so both counts move by 2.
    assert old == {"types": 1024, "functions": 1}

    identities = compute_canonical_identities(types)
    new = run_extraction.summarize_symbols(types, identities=identities)
    assert new == fixture["symbol_counts"] == {"types": 518, "functions": 1}
    assert new["types"] < old["types"]
    assert new["functions"] == old["functions"]  # functions are never touched by the dedup


def test_extract_pinned_repository_wires_identities_into_summarize_symbols() -> None:
    """AGENTS.md liveness: a function nobody calls is not a fix. The real call site inside
    `extract_pinned_repository` must compute identities over the FULL, raw, un-cut `types` list
    and pass them through, proven against the committed source rather than only against
    `summarize_symbols` in isolation."""
    source = inspect.getsource(run_extraction.extract_pinned_repository)
    assert "compute_canonical_identities(types)" in source
    assert "summarize_symbols(types, identities=identities)" in source


# --------------------------------------------------------------------------- (d) combined I2+I3 end-to-end


def test_slides_python_combined_numerator_and_denominator_coverage_line() -> None:
    """End-to-end: with BOTH I2's already-landed numerator fix (`chunk_builder._coverage_line`,
    reached through the real `build_chunks_from_api_surface`) and this card's denominator fix
    combined (the `symbol_counts` a real `extract_pinned_repository` run would now produce),
    the SAME slides/python pilot's rendered `API-Coverage:` line is measured fresh here, not
    assumed to be exactly "N of N" for every pilot in general.

    Measured: "API-Coverage: 516 of 516 types; 1 of 1 functions" - for this specific pilot the
    combined numerator (I2, chunk_builder's own dedup over the post-slot-cut entries) and the
    combined denominator (I3, this card's dedup over the full raw list) land on the exact same
    516, so the line is fully honest end to end. (The card's own contract warns the two dedup
    rules are similar but not byte-identical in every edge case - this is what is actually
    measured for slides_python, not an assumption generalized from TC-614's numerator alone.)

    TC-729 (2026-11-09): the real, committed fixture's own `symbol_counts` is now ALREADY this
    backfilled value (TC-729 landed it directly onto the fixture, closing the gap this test
    originally demonstrated as still-open) - confirmed below before building on it, so
    `combined_fixture` is proven identical to the real committed data, not a fabrication. The I2-alone
    contrast is reconstructed from the one-argument recount instead, since the real committed fixture
    no longer carries the old, un-deduplicated value to contrast against directly.
    """
    fixture = _slides_python_fixture()
    types = fixture["types"]
    identities = compute_canonical_identities(types)
    new_symbol_counts = run_extraction.summarize_symbols(types, identities=identities)
    # TC-841: 516 -> 518 (see the first test of this section).
    assert fixture["symbol_counts"] == new_symbol_counts == {"types": 518, "functions": 1}

    # Exactly what a real extract_pinned_repository run now produces for this fixture's own types -
    # identical to the real, committed fixture already has it (confirmed above).
    combined_fixture = {**fixture, "symbol_counts": new_symbol_counts}

    chunks = chunk_builder.build_chunks_from_api_surface(
        combined_fixture, title="Slides Python", max_types=10_000
    )
    coverage_lines = {match for chunk in chunks for match in re.findall(r"API-Coverage: [^\n]+", chunk.text)}
    # TC-841: 516 of 516 types; 1 of 1 functions -> the regenerated fixture's 2 module-level constants (TC-820 grammar) are
    # named apart: 516 types + 2 constants/variables = the 518 canonical symbols of ``symbol_counts.types``; still honest end to end.
    assert coverage_lines == {"API-Coverage: 516 of 516 types; 2 of 2 constants/variables; 1 of 1 functions"}

    # Contrast: I2 alone (the OLD, un-deduplicated denominator TC-614 deliberately left open, before
    # TC-620/TC-729 backfilled it onto this fixture) still shows the inflated denominator -
    # reconstructed here, since the real committed fixture no longer carries that stale value.
    old_symbol_counts = run_extraction.summarize_symbols(types)
    old_fixture = {**fixture, "symbol_counts": old_symbol_counts}
    chunks_i2_alone = chunk_builder.build_chunks_from_api_surface(
        old_fixture, title="Slides Python", max_types=10_000
    )
    coverage_lines_i2_alone = {
        match for chunk in chunks_i2_alone for match in re.findall(r"API-Coverage: [^\n]+", chunk.text)
    }
    # TC-841: the un-deduplicated 1024 entries are 1022 types + the 2 constants/variables, which the line names apart.
    assert coverage_lines_i2_alone == {
        "API-Coverage: 516 of 1022 types; 2 of 2 constants/variables; 1 of 1 functions"
    }
