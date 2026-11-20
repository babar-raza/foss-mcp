"""TC-520 (REQ-G3-005, REQ-G3-008): the coverage line of every symbol chunk.

``API-Coverage:`` separates types from functions and, when the extraction recorded source files it
could not parse, says how many. A fixture without the new keys renders exactly as before. Fully
offline: synthetic fixtures plus the committed ones, which are only read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing.chunk_builder import (
    PARSE_FAILURE_NOTE_THRESHOLD,
    _coverage_line,
    _is_test_source,
    build_chunks_from_api_surface,
    parse_failures_over_threshold,
)
from foss_mcp.indexing.symbol_identity import compute_canonical_identities
from foss_mcp.mcp.tools.coverage_note import coverage_note_from_text

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
LEGACY_LINE = re.compile(r"^API-Coverage:\s*(\d+)\s+of\s+(\d+)\s+types\b", re.MULTILINE)


def _fixture(types: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": types, **extra}


def _texts(fixture: Mapping[str, object]) -> list[str]:
    return [chunk.text for chunk in build_chunks_from_api_surface(fixture, title="t", max_types=10_000)]


def _real(pilot: str, name: str = "api_surface.json") -> dict[str, object]:
    return json.loads((FIXTURES_DIR / pilot / name).read_text(encoding="utf-8"))


# G3/TC-853: tex/python was re-pinned at the upstream release 26.11.1 (07db3203), where every file parses. The clause mechanism
# ("N of M source files could not be parsed") is still held against the frozen extraction of 181015d2 (35 of 45 files unparseable,
# 11 of 45 unreadable after the repair): the previously committed fixture, byte for byte.
_TEX_FROZEN = "api_surface_181015d2_repaired.json"


def _last_lines(fixture: Mapping[str, object]) -> set[str]:
    return {text.splitlines()[-1] for text in _texts(fixture)}


def _go_like(extra: dict[str, Any]) -> dict[str, object]:
    types: list[dict[str, object]] = [
        {"name": "Alpha", "kind": "type_spec", "file": "a.go"},
        {"name": "Beta", "kind": "type_spec", "file": "b.go"},
        {"name": "Run", "kind": "function", "file": "a.go", "params": [], "return_type": ""},
    ]
    return _fixture(types, type_count=3, **extra)


def _failures(engine: str, failed: int, total: int) -> dict[str, object]:
    return {"engine": engine, "failed": failed, "total": total, "examples": ["src/p/m.py:2: bad"]}


# ---------------------------------------------------------------- the threshold


@pytest.mark.parametrize(
    ("parse_failures", "expected"),
    [
        ({"engine": "ast", "failed": 11, "total": 100, "examples": []}, True),
        ({"engine": "ast", "failed": 10, "total": 100, "examples": []}, False),
        ({"engine": "tree-sitter", "failed": 20, "total": 100, "examples": []}, False),
        ({"engine": "tree-sitter", "failed": 30, "total": 100, "examples": []}, True),
        ({"engine": "ast", "failed": 0, "total": 0, "examples": []}, False),
        ({"engine": "ast", "failed": "many", "total": 10}, False),
        ({"engine": "ast", "failed": True, "total": 10}, False),
        ({"engine": "ast", "failed": 5}, False),
        ({"engine": "regex", "failed": 90, "total": 100}, False),
        ({"failed": 90, "total": 100}, False),
        ("35 of 45", False),
        (None, False),
    ],
)
def test_threshold_rules(parse_failures: object, expected: bool) -> None:
    assert parse_failures_over_threshold(parse_failures) is expected


def test_threshold_constant_is_the_documented_pair() -> None:
    assert PARSE_FAILURE_NOTE_THRESHOLD == {"ast": 0.10, "tree-sitter": 0.25}


# ---------------------------------------------------------------- the legacy rendering stays


def test_legacy_line_is_unchanged_for_fixtures_without_the_new_keys() -> None:
    fixture = _fixture(
        [
            {"name": "Real", "kind": "function", "file": "real.go", "line": 1},
            {"name": "Helper", "kind": "function", "file": "real_test.go", "line": 2},
            {"name": "NoFile", "kind": "function"},
        ],
        type_count=3,
    )
    assert _last_lines(fixture) == {"API-Coverage: 2 of 3 types"}


def test_committed_pdf_net_fixture_renders_901_of_901_types() -> None:
    # re-extracted by TC-530: "API-Coverage: 300 of 901 types" -> the complete surface
    # TC-558: 901 of 901 -> 915 of 915 (the 14 delegate types of pdf/net are types of the surface; RD3-054)
    assert _last_lines(_real("pdf_net")) == {"API-Coverage: 915 of 915 types"}


def test_no_coverage_line_without_a_type_count_or_symbol_counts() -> None:
    assert not any("API-Coverage" in text for text in _texts(_fixture([{"name": "A", "kind": "class"}])))


# ---------------------------------------------------------------- types and functions


def test_types_and_functions_clauses_on_a_go_like_fixture() -> None:
    fixture = _go_like({"symbol_counts": {"types": 259, "functions": 2054}})
    assert _last_lines(fixture) == {"API-Coverage: 2 of 259 types; 1 of 2054 functions"}


def test_functions_clause_is_omitted_when_there_are_no_functions() -> None:
    fixture = _fixture(
        [{"name": "Alpha", "kind": "class_specifier", "file": "a.hpp"}],
        type_count=9,
        symbol_counts={"types": 9, "functions": 0},
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 9 types"}


def test_symbol_counts_without_types_fall_back_to_the_legacy_types_clause() -> None:
    fixture = _go_like({"symbol_counts": {"functions": 7}})
    assert _last_lines(fixture) == {"API-Coverage: 3 of 3 types; 1 of 7 functions"}


# ---------------------------------------------------------------- unparsed source files


def test_parse_failure_clause_states_the_files_over_the_threshold() -> None:
    fixture = _fixture(
        [{"name": "A", "kind": "class", "file": "a.py"}],
        type_count=4,
        symbol_counts={"types": 4, "functions": 0},
        parse_failures=_failures("ast", 35, 45),
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 4 types; 35 of 45 source files could not be parsed"}


def test_no_failure_clause_at_or_below_the_threshold() -> None:
    fixture = _fixture(
        [{"name": "A", "kind": "class", "file": "a.py"}],
        type_count=1,
        symbol_counts={"types": 1, "functions": 0},
        parse_failures=_failures("ast", 10, 100),
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 1 types"}
    noisy = {**fixture, "parse_failures": _failures("tree-sitter", 20, 100)}
    assert _last_lines(noisy) == {"API-Coverage: 1 of 1 types"}


def test_syntax_error_entries_without_parse_failures_say_at_least() -> None:
    fixture = _fixture(
        [{"name": "A", "kind": "class", "file": "a.py"}],
        type_count=1,
        unresolved=[
            "pkg.a:3:syntax-error:src/pkg/a.py:bad",
            "pkg.a:9:syntax-error:src/pkg/a.py:worse",  # the same file twice: one file
            "pkg.b:1:syntax-error:src/pkg/b.py:bad",
            "pkg:1:unresolved-reexport:pkg.a.X",
        ],
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 1 types; at least 2 source files could not be parsed"}


def test_parse_failures_key_wins_over_the_unresolved_fallback() -> None:
    fixture = _fixture(
        [{"name": "A", "kind": "class", "file": "a.py"}],
        type_count=1,
        unresolved=["pkg.a:3:syntax-error:src/pkg/a.py:bad"],
        parse_failures=_failures("ast", 1, 100),
    )
    assert _last_lines(fixture) == {"API-Coverage: 1 of 1 types"}


def test_every_clause_in_order() -> None:
    fixture = _go_like(
        {"symbol_counts": {"types": 10, "functions": 5}, "parse_failures": _failures("tree-sitter", 40, 100)}
    )
    assert _last_lines(fixture) == {
        "API-Coverage: 2 of 10 types; 1 of 5 functions; 40 of 100 source files could not be parsed"
    }


# ---------------------------------------------------------------- the committed tex_python fixture


def test_frozen_tex_python_fixture_states_the_unparsed_files_in_every_chunk() -> None:
    fixture = _real("tex_python", _TEX_FROZEN)
    texts = _texts(fixture)
    # TC-555: 4 chunks -> 23 (21 types, 2 functions) and "at least 2 source files" -> the measured
    # parse_failures of the committed fixture (the files the indentation repair reads are no failure; the 11
    # private files it cannot repair are: TC-555 rework 2, 35 of 45 -> 11 of 45).
    # TC-614 (I2): the types numerator now counts each alias group once - tex/python has real
    # shell/definition pairs too (not only slides/python), measured fresh here: 21 -> 14.
    # TC-729 (2026-11-09): tex_python's own `symbol_counts.types` was backfilled to the identical
    # dedup count (21 -> 14), so the denominator now agrees with the numerator: 14 of 14, not 14 of
    # 21.
    # TC-841: 23 chunks -> 25 and 14 of 14 -> 16 of 16 after the fixture was regenerated with the current extractor, whose
    # package-root re-export recovery adds the two classes FontManager and InputSource (TC-818).
    assert len(texts) == 25
    expected = "API-Coverage: 16 of 16 types; 2 of 2 functions; 11 of 45 source files could not be parsed"
    assert all(text.splitlines()[-1] == expected for text in texts)


def test_committed_tex_python_fixture_at_26_11_1_states_no_unparsed_file() -> None:
    """TC-853: every one of the 59 files parses at 07db3203, so no chunk carries a "could not be parsed" clause."""
    fixture = _real("tex_python")
    texts = _texts(fixture)
    assert len(texts) == 31
    assert all(text.splitlines()[-1] == "API-Coverage: 16 of 16 types; 8 of 8 functions" for text in texts)
    assert not any("could not be parsed" in text for text in texts)


def test_the_line_still_matches_the_legacy_regex_with_the_types_clause_first() -> None:
    fixtures = [
        _go_like({"symbol_counts": {"types": 259, "functions": 2054}}),
        _real("tex_python"),
        _real("tex_python", _TEX_FROZEN),
        _fixture(
            [{"name": "A", "kind": "class", "file": "a.py"}],
            type_count=4,
            parse_failures=_failures("ast", 35, 45),
        ),
    ]
    for fixture in fixtures:
        for text in _texts(fixture):
            match = LEGACY_LINE.search(text)
            assert match is not None, text
            assert text.splitlines()[-1].startswith(match.group(0))


def test_every_symbol_chunk_carries_the_clause_not_only_the_first() -> None:
    fixture = _fixture(
        [{"name": f"T{index}", "kind": "class", "file": f"t{index}.py"} for index in range(6)],
        type_count=6,
        parse_failures=_failures("ast", 5, 10),
    )
    texts = _texts(fixture)
    assert len(texts) == 6
    assert all(text.splitlines()[-1].endswith("5 of 10 source files could not be parsed") for text in texts)


def test_the_line_stays_the_last_line_before_the_source_commit_is_appended() -> None:
    fixture = _real("tex_python", _TEX_FROZEN)
    for text in _texts(fixture):
        appended = f"{text}\nSource-Commit: {fixture['source_commit']}"
        assert appended.splitlines()[
            -2
        ].startswith(
            "API-Coverage: 16 of 16 types"
        )  # TC-841: 14 of 14 -> 16 of 16 (FontManager, InputSource recovered); TC-555: 4 of 4 -> 21 of 21; TC-614: 21 of 21 -> 14 of 21 (dedup); TC-729: 14 of 21 -> 14 of 14
        # (denominator backfilled to the identical dedup)


# ---------------------------------------------------------------- TC-614 (I2, REQ-G3-012): dedup
#
# I1's canonical-identity model (`symbol_identity.compute_canonical_identities`) threaded into the
# numerator so each alias group (a Python re-export shell and its definition) is counted ONCE, not
# once per entry. `identities=None` (the default) must be byte-for-byte identical to the prior
# behaviour - case (a) below. The DENOMINATOR side (`run_extraction.py`'s own, still-inflated
# `symbol_counts.types`) is explicitly out of scope for this card (I3's job).


def test_a_identities_none_is_byte_for_byte_identical_to_the_old_two_argument_form() -> None:
    """(a) The backward-compatibility contract: the OLD two-argument form of `_coverage_line`
    (no `identities`, and `identities=None` spelled out explicitly) renders exactly as before this
    card, for both the `symbol_counts` path and the no-`symbol_counts` legacy path."""
    types = [
        {"name": "Alpha", "kind": "type_spec", "file": "a.go"},
        {"name": "Beta", "kind": "type_spec", "file": "b.go"},
        {"name": "Run", "kind": "function", "file": "a.go", "params": [], "return_type": ""},
    ]
    fixture = _fixture(types, type_count=3, symbol_counts={"types": 259, "functions": 2054})
    assert _coverage_line(fixture, types) == "2 of 259 types; 1 of 2054 functions"
    assert _coverage_line(fixture, types, None) == "2 of 259 types; 1 of 2054 functions"
    assert _coverage_line(fixture, types) == _coverage_line(fixture, types, None)

    legacy_fixture = _fixture([{"name": "A", "kind": "class", "file": "a.py"}], type_count=4)
    legacy_entries = legacy_fixture["types"]
    assert isinstance(legacy_entries, list)
    assert _coverage_line(legacy_fixture, legacy_entries) == "1 of 4 types"
    assert _coverage_line(legacy_fixture, legacy_entries, None) == "1 of 4 types"


def test_b_identities_deduplicate_the_numerator_for_a_synthetic_shell_definition_pair() -> None:
    """(b) A synthetic TC-382-rule-8 shape (reused from `test_symbol_identity.py`'s own synthetic
    shapes): a member-less package `__init__.py` stub paired with the one other entry of the same
    name that has members. Without `identities` the numerator counts both entries (the shell
    inflates it); with `identities` it counts the unit once."""
    entries = [
        {
            "name": "Foo",
            "kind": "class",
            "file": "pkg/__init__.py",
            "class_import": "pkg.Foo",
            "bases": [],
            "methods": [],
            "properties": [],
        },
        {
            "name": "Foo",
            "kind": "class",
            "file": "pkg/sub/foo.py",
            "class_import": "pkg.sub.Foo",
            "bases": [],
            "methods": [{"name": "m", "params": [], "return_type": ""}],
            "properties": [],
        },
        {
            "name": "Bar",
            "kind": "class",
            "file": "pkg/bar.py",
            "class_import": "pkg.Bar",
            "bases": [],
            "methods": [{"name": "m", "params": [], "return_type": ""}],
            "properties": [],
        },
    ]
    identities = compute_canonical_identities(entries)
    # Sanity on the identity model itself, before touching `_coverage_line` at all: entry 0 (the
    # stub) is an alias, not a definition; the real two definitions (entries 1 and 2) each are.
    assert [identity.is_definition for identity in identities] == [False, True, True]

    counted_fixture = _fixture(entries, type_count=3, symbol_counts={"types": 3, "functions": 0})
    assert _coverage_line(counted_fixture, entries) == "3 of 3 types"
    assert _coverage_line(counted_fixture, entries, identities) == "2 of 3 types"

    # The legacy clause (no `symbol_counts`) dedups the same way (TC-614 instruction 2).
    legacy_fixture = _fixture(entries, type_count=3)
    assert _coverage_line(legacy_fixture, entries) == "3 of 3 types"
    assert _coverage_line(legacy_fixture, entries, identities) == "2 of 3 types"


def test_c_real_slides_python_regression_numerator_drops_with_identities() -> None:
    """(c) The real, measured regression against the committed `slides_python` fixture end to end,
    through the real rendering path (`build_chunks_from_api_surface`, now always threading
    `compute_canonical_identities` output into `_coverage_line`). `max_types=10_000` (via `_texts`)
    keeps the slot cut from also moving these numbers, matching the real, already-committed
    generation manifest's rendered line (`infra/demo-manifests/slides__python__self_extracted/...`:
    ``API-Coverage: 1022 of 1022 types; 1 of 1 functions``) for the BEFORE state.

    Measured fresh for this card (does NOT assume TC-612's own 517/1023 finding, which measured
    differently): 1023 non-test entries, 517 canonical units, 516 of which are non-function
    definitions (the fixture's one function entry keeps its own, separate, unaffected clause) -
    AFTER this card, the numerator is 516, not 517, because `kind == "function"` is excluded from
    the types-clause dedup count exactly as REQ-G3-012 specifies.

    TC-729 (2026-11-09): the fixture's own `symbol_counts.types` was backfilled onto the identical
    dedup count (1022 -> 516), so the AFTER line is now fully honest ("516 of 516", not "516 of
    1022"). The BEFORE reconstruction below is built with the fixture's OLD, un-deduplicated
    `symbol_counts` (the real value it carried until TC-729), since the real committed fixture no
    longer carries it - this is the exact real line the already-committed generation manifest
    carried before TC-614/TC-729.
    """
    fixture = _real("slides_python")
    after_lines = _last_lines(fixture)
    # TC-841: the regenerated fixture's 2 module-level constants (TC-700) are named apart (TC-820 grammar).
    assert after_lines == {"API-Coverage: 516 of 516 types; 2 of 2 constants/variables; 1 of 1 functions"}

    # The BEFORE rendering (pre-TC-614: `identities=None`; pre-TC-729: the fixture's own
    # un-backfilled `symbol_counts`), reconstructed against the same, real, non-test-filtered
    # entries this module itself computes.
    candidates = [entry for entry in fixture["types"] if not _is_test_source(entry.get("file"))]
    functions = sum(1 for entry in candidates if entry.get("kind") == "function")
    old_fixture = {
        **fixture,
        "symbol_counts": {"types": len(candidates) - functions, "functions": functions},
    }
    before = _coverage_line(old_fixture, candidates)
    # TC-841: 1022 -> 1024 entries, of which the 2 constants/variables are named apart.
    assert before == "1022 of 1022 types; 2 of 2 constants/variables; 1 of 1 functions"


def test_d_coverage_note_from_text_now_fires_for_slides_python_after_the_fix() -> None:
    """(d) End-to-end revival proof: `coverage_note.coverage_note_from_text` (read-only to this
    card, UNMODIFIED) already re-parses whatever `_coverage_line` renders and already emits a
    caveat whenever the parsed numerator is below the parsed denominator - it needs no code change,
    it was simply never exercised on a real pilot before this card, because the numerator was never
    honestly below 100% for slides/python. Calling the real, unmodified function against the real
    rendered AFTER line proves the revival; calling it against the real rendered BEFORE line proves
    it correctly returned `None` previously (the ratio was 100% "by construction").

    TC-729 (2026-11-09): the real, committed fixture's own `symbol_counts` is now ALSO backfilled
    (TC-620's denominator convention, TC-729's backfill), so the real, live rendered line is fully
    honest ("516 of 516") and `coverage_note_from_text` correctly returns `None` against it directly
    - proven below with no reconstruction needed. The caveat-FIRING demonstration (I2's numerator
    dedup landed, I3/TC-729's denominator backfill not yet applied - the exact regression this card
    closes) is reconstructed from the fixture's OLD, un-deduplicated `symbol_counts`, since the real
    committed fixture no longer carries it.
    """
    fixture = _real("slides_python")
    candidates = [entry for entry in fixture["types"] if not _is_test_source(entry.get("file"))]
    functions = sum(1 for entry in candidates if entry.get("kind") == "function")
    old_fixture = {
        **fixture,
        "symbol_counts": {"types": len(candidates) - functions, "functions": functions},
    }

    partial_text = next(iter(_texts(old_fixture)))
    partial_note = coverage_note_from_text(partial_text)
    assert partial_note is not None
    assert "516 of 1022 types" in partial_note
    assert "a type missing from it may still exist upstream" in partial_note

    complete_text = next(iter(_texts(fixture)))
    assert coverage_note_from_text(complete_text) is None
