"""G3/TC-693 (O8, second half, REQ-G3-012): a per-pilot regression ratchet for the independent
oracle (``scripts/release/source_differential.py``), additive alongside the existing per-LANGUAGE
``evaluate_ceilings``/``LANGUAGE_ERROR_CEILINGS`` mechanism -- neither of those is touched by this
card.

``evaluate_ceilings`` is per-LANGUAGE and, at its default ceiling of 0, fails identically whether a
real, already-disclosed per-pilot disagreement (e.g. ``pdf_cpp``'s own known ``extra=2406``,
``OQ-022``) stays exactly where it was last measured or silently regresses further -- there is no
per-PILOT floor that can only shrink. This card adds exactly that: a committed, per-pilot snapshot
of the independent oracle's last REAL, already-verified disagreement counts
(``tests/fixtures/oracle/snapshot.json``), loaded by ``load_pilot_snapshot``, and a second,
additive gate, ``evaluate_pilot_snapshot_ratchet``, wired into ``verify_pilot`` through a new
optional ``pilot_snapshot`` parameter.

As the card's own ``purpose`` states (investigated, two prior forks this session, same
conclusion): the real oracle needs real upstream clones (network) and Docker (Go/Rust) to
re-derive real disagreement counts -- there is no way to do that offline. So "offline CI test (pure
Python, seconds)" here means proving the RATCHET MECHANISM is correct against synthetic data (and
that the real, committed snapshot loads and matches its own pinned ground truth) -- never re-running
the real oracle. Every test below is pure Python against in-memory data or the one committed JSON
file: no Docker, no network, runs in seconds.

Six cases, exactly as the card's own ``inputs`` section describes them:

  (a) a synthetic ``DiffCounts`` whose counts exactly match a synthetic pilot's snapshot entry
      produces NO ratchet reasons.
  (b) a ``DiffCounts`` with one category 1 higher than the snapshot produces exactly one reason
      naming that category, pilot, new count, and snapshot count.
  (c) a pilot ABSENT from the snapshot is treated as an all-zero floor -- any nonzero count for it
      produces a reason.
  (d) ``load_pilot_snapshot`` rejects a malformed file (a non-dict pilot value, a negative count, an
      unknown category key) with a clear exception, never a silent corruption.
  (e) the REAL committed ``tests/fixtures/oracle/snapshot.json`` loads cleanly via
      ``load_pilot_snapshot`` and its own counts, for all 32 pilots, exactly match hardcoded
      ground-truth values PINNED IN THIS TEST FILE (never read back from
      ``docs/ORACLE_TRIAGE.md``/the commit message at test time -- mirroring TC-691's own
      test-design rule -- so this test catches future drift in the committed snapshot rather than
      merely proving self-consistency).
  (f) ``verify_pilot`` wired with the real snapshot correctly passes for a synthetic ``DiffCounts``
      matching a real pilot's real recorded counts, and correctly fails when one category exceeds
      them.

Key format note (a deliberate, disclosed departure from the card's own illustrative example key,
``"pdf/cpp"``): the snapshot is keyed by the REAL runtime pilot identifier,
``scripts/deploy/pilots.py``'s own ``pilot_key()`` format (``family_platform``, e.g. ``"pdf_cpp"``,
underscore-joined -- confirmed directly against that function's own source and against
``chart.load_pilots()``'s real output, not assumed), because that is the exact string ``main()``
passes as ``verify_pilot``'s ``pilot=`` argument at runtime. Keying the snapshot by the slash-form
used in TC-595's prose (and the card's own illustrative example) would make
``evaluate_pilot_snapshot_ratchet`` look up every real pilot as "absent" when actually wired via
``main()`` -- silently defeating the whole mechanism this card exists to add. ``tests/fixtures/``'s
own directory names (e.g. ``tests/fixtures/pdf_cpp/``) already use this identical underscore
convention, confirming this is the project's one real pilot-identifier format, not a new one
invented for this card.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
SNAPSHOT_FILE = REPO_ROOT / "tests" / "fixtures" / "oracle" / "snapshot.json"

sys.path.insert(0, str(RELEASE_DIR))

import source_differential as sd  # noqa: E402

DC = sd.DiffCounts

# --- (e)'s own ground truth -------------------------------------------------------------------------
# Transcribed independently, directly from the two real, already-committed, cross-checked sources
# this card's own ``inputs`` section names -- never read back from the snapshot file itself, and
# never re-derived: TC-595's commit message (``git log --format=%B -n 1 5c8bc6fe``) for all 29
# non-clean pilots, cross-checked against docs/ORACLE_TRIAGE.md for the 7 Go/Rust/C++ pilots among
# them. The 3 clean pilots (cells_go, cells_typescript, tex_python) are all-zero. This dict is this
# TEST's own source of truth: if a future edit to the committed snapshot drifts from these real,
# verified numbers, this test catches it -- it does not merely confirm the snapshot agrees with
# itself.
REAL_PILOT_GROUND_TRUTH: dict[str, dict[str, int]] = {
    "pdf_net": {"missing": 239, "extra": 6287, "signature": 292, "static": 6, "default": 0, "enum_value": 9},
    "slides_python": {
        "missing": 5754,
        "extra": 2223,
        "signature": 46,
        "static": 0,
        "default": 0,
        "enum_value": 0,
    },
    "pdf_typescript": {
        "missing": 258,
        "extra": 29,
        "signature": 1,
        "static": 0,
        "default": 0,
        "enum_value": 0,
    },
    "pdf_go": {"missing": 0, "extra": 480, "signature": 0, "static": 0, "default": 0, "enum_value": 0},
    "pdf_java": {
        "missing": 499,
        "extra": 16301,
        "signature": 277,
        "static": 0,
        "default": 0,
        "enum_value": 542,
    },
    "cells_rust": {"missing": 341, "extra": 0, "signature": 14, "static": 0, "default": 0, "enum_value": 0},
    "pdf_cpp": {"missing": 4, "extra": 2406, "signature": 694, "static": 0, "default": 0, "enum_value": 0},
    "words_python": {
        "missing": 234,
        "extra": 921,
        "signature": 5,
        "static": 0,
        "default": 0,
        "enum_value": 7,
    },
    "words_net": {
        "missing": 10539,
        "extra": 3351,
        "signature": 64,
        "static": 0,
        "default": 0,
        "enum_value": 0,
    },
    "slides_java": {
        "missing": 519,
        "extra": 2627,
        "signature": 78,
        "static": 0,
        "default": 0,
        "enum_value": 747,
    },
    "cells_go": {"missing": 0, "extra": 0, "signature": 0, "static": 0, "default": 0, "enum_value": 0},
    "cells_typescript": {
        "missing": 0,
        "extra": 0,
        "signature": 0,
        "static": 0,
        "default": 0,
        "enum_value": 0,
    },
    "cells_cpp": {"missing": 0, "extra": 0, "signature": 13, "static": 0, "default": 0, "enum_value": 0},
    "cells_net": {"missing": 0, "extra": 48, "signature": 16, "static": 0, "default": 0, "enum_value": 0},
    "cells_python": {
        "missing": 1174,
        "extra": 382,
        "signature": 2,
        "static": 0,
        "default": 0,
        "enum_value": 2,
    },
    "pdf_python": {
        "missing": 1061,
        "extra": 1334,
        "signature": 91,
        "static": 0,
        "default": 1,
        "enum_value": 54,
    },
    "slides_cpp": {"missing": 0, "extra": 153, "signature": 37, "static": 0, "default": 0, "enum_value": 0},
    "slides_net": {"missing": 0, "extra": 723, "signature": 34, "static": 0, "default": 0, "enum_value": 0},
    "email_python": {"missing": 204, "extra": 0, "signature": 1, "static": 0, "default": 0, "enum_value": 0},
    "email_cpp": {"missing": 0, "extra": 24, "signature": 7, "static": 0, "default": 0, "enum_value": 0},
    "email_net": {"missing": 0, "extra": 64, "signature": 4, "static": 0, "default": 0, "enum_value": 0},
    "barcode_python": {
        "missing": 53,
        "extra": 60,
        "signature": 4,
        "static": 0,
        "default": 0,
        "enum_value": 0,
    },
    "note_python": {"missing": 189, "extra": 107, "signature": 0, "static": 0, "default": 0, "enum_value": 0},
    "html_python": {
        "missing": 1470,
        "extra": 13885,
        "signature": 47,
        "static": 0,
        "default": 0,
        "enum_value": 0,
    },
    "page_python": {"missing": 41, "extra": 624, "signature": 0, "static": 0, "default": 0, "enum_value": 3},
    "imaging_net": {"missing": 0, "extra": 1, "signature": 2, "static": 0, "default": 0, "enum_value": 0},
    "cells_java": {"missing": 0, "extra": 926, "signature": 23, "static": 0, "default": 0, "enum_value": 11},
    "3d_python": {"missing": 1797, "extra": 1489, "signature": 7, "static": 0, "default": 1, "enum_value": 0},
    "3d_typescript": {
        "missing": 182,
        "extra": 110,
        "signature": 14,
        "static": 11,
        "default": 0,
        "enum_value": 0,
    },
    "3d_net": {"missing": 7, "extra": 2036, "signature": 75, "static": 0, "default": 0, "enum_value": 0},
    "3d_java": {"missing": 0, "extra": 3299, "signature": 154, "static": 1, "default": 0, "enum_value": 0},
    "tex_python": {"missing": 0, "extra": 0, "signature": 0, "static": 0, "default": 0, "enum_value": 0},
}

# Cross-check against TC-595's own fleet-wide sums, restated verbatim in its commit message: "72,648
# real members compared fleet-wide; summed across the 29 failing pilots: 24,565 missing, 59,890
# extra, 2,002 signature, 18 static, 2 default, 1,375 enum_value." This is an independent arithmetic
# check on the ground truth above, not a re-derivation of any single number.
REAL_FLEET_WIDE_SUMS = {
    "missing": 24565,
    "extra": 59890,
    "signature": 2002,
    "static": 18,
    "default": 2,
    "enum_value": 1375,
}


def test_real_pilot_ground_truth_sums_match_tc595s_own_commit_message():
    sums = {category: 0 for category in sd.CATEGORIES}
    for counts in REAL_PILOT_GROUND_TRUTH.values():
        for category in sd.CATEGORIES:
            sums[category] += counts[category]
    assert sums == REAL_FLEET_WIDE_SUMS


# --- (a)/(b)/(c): evaluate_pilot_snapshot_ratchet, pure, synthetic -----------------------------------


def test_matching_counts_produce_no_ratchet_reasons():
    """(a): a synthetic DiffCounts whose counts exactly match a synthetic pilot's snapshot entry."""
    snapshot = {"family_platform": {"missing": 4, "extra": 2406, "signature": 694}}
    counts = DC(missing=4, extra=2406, signature=694, static=0, default=0, enum_value=0, checked=5000)
    reasons = sd.evaluate_pilot_snapshot_ratchet("family_platform", counts, snapshot)
    assert reasons == []


def test_one_category_one_higher_produces_exactly_one_named_reason():
    """(b): one category 1 higher than the snapshot -- exactly one reason, naming the category,
    pilot, new count, and snapshot count."""
    snapshot = {"family_platform": {"missing": 4, "extra": 2406, "signature": 694}}
    counts = DC(missing=4, extra=2407, signature=694, static=0, default=0, enum_value=0, checked=5000)
    reasons = sd.evaluate_pilot_snapshot_ratchet("family_platform", counts, snapshot)
    assert len(reasons) == 1
    (reason,) = reasons
    assert "family_platform" in reason
    assert "extra" in reason
    assert "2407" in reason  # the new count
    assert "2406" in reason  # the snapshot's recorded count


def test_a_pilot_absent_from_the_snapshot_is_an_all_zero_floor():
    """(c): a pilot ABSENT from the snapshot is treated as an all-zero floor -- any nonzero count
    for it produces a reason. The correct, conservative default for a newly onboarded pilot with no
    snapshot entry yet."""
    snapshot = {"some_other_pilot": {"missing": 100}}
    counts = DC(missing=0, extra=1, signature=0, static=0, default=0, enum_value=0, checked=10)
    reasons = sd.evaluate_pilot_snapshot_ratchet("never_onboarded_pilot", counts, snapshot)
    assert len(reasons) == 1
    assert "extra" in reasons[0] and "never_onboarded_pilot" in reasons[0] and "1" in reasons[0]

    # all-zero counts for an absent pilot: no regression against an all-zero floor.
    zero_counts = DC(missing=0, extra=0, signature=0, static=0, default=0, enum_value=0, checked=10)
    assert sd.evaluate_pilot_snapshot_ratchet("never_onboarded_pilot", zero_counts, snapshot) == []


def test_a_category_absent_from_a_present_pilots_entry_is_also_an_all_zero_floor():
    """A category the snapshot's pilot entry never mentions is 0, not "unlimited" -- any nonzero
    count for that category is a regression too."""
    snapshot = {"family_platform": {"missing": 4}}  # "static" never mentioned for this pilot
    counts = DC(missing=4, extra=0, signature=0, static=1, default=0, enum_value=0, checked=10)
    reasons = sd.evaluate_pilot_snapshot_ratchet("family_platform", counts, snapshot)
    assert len(reasons) == 1
    assert "static" in reasons[0]


def test_multiple_categories_regressing_each_produce_their_own_reason():
    snapshot = {"family_platform": {"missing": 4, "extra": 2406}}
    counts = DC(missing=5, extra=2500, signature=0, static=0, default=0, enum_value=0, checked=10)
    reasons = sd.evaluate_pilot_snapshot_ratchet("family_platform", counts, snapshot)
    assert len(reasons) == 2
    joined = " ".join(reasons)
    assert "missing" in joined and "extra" in joined


def test_ratchet_reason_wording_is_distinguishable_from_evaluate_ceilings():
    """The card's own requirement: the message names "a regression against the last recorded real
    oracle run", distinguishing it from ``evaluate_ceilings``'s own "exceeds the ceiling" wording."""
    snapshot = {"family_platform": {"missing": 0}}
    counts = DC(missing=1, extra=0, signature=0, static=0, default=0, enum_value=0, checked=1)
    (reason,) = sd.evaluate_pilot_snapshot_ratchet("family_platform", counts, snapshot)
    assert "regression against the last recorded real oracle run" in reason
    assert "exceeds the ceiling" not in reason


# --- (d): load_pilot_snapshot rejects malformed input -------------------------------------------------


def test_load_pilot_snapshot_rejects_a_non_dict_top_level(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    with pytest.raises(sd.Refused):
        sd.load_pilot_snapshot(path)


def test_load_pilot_snapshot_rejects_a_non_dict_pilot_value(tmp_path):
    """A malformed shape: a pilot's value is a bare int, not a {category: count} object."""
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"pdf_cpp": 2406}), encoding="utf-8")
    with pytest.raises(sd.Refused):
        sd.load_pilot_snapshot(path)


def test_load_pilot_snapshot_rejects_a_negative_count(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"pdf_cpp": {"missing": -1}}), encoding="utf-8")
    with pytest.raises(sd.Refused):
        sd.load_pilot_snapshot(path)


def test_load_pilot_snapshot_rejects_an_unknown_category_key(tmp_path):
    """A key shape outside CATEGORIES is almost certainly a typo of a real category name, never a
    new, legitimate one -- CATEGORIES is this module's own closed set, unchanged by this card."""
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"pdf_cpp": {"missign": 4}}), encoding="utf-8")
    with pytest.raises(sd.Refused):
        sd.load_pilot_snapshot(path)


def test_load_pilot_snapshot_rejects_a_non_int_count(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"pdf_cpp": {"missing": "4"}}), encoding="utf-8")
    with pytest.raises(sd.Refused):
        sd.load_pilot_snapshot(path)


def test_load_pilot_snapshot_accepts_a_well_formed_file(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"pdf_cpp": {"missing": 4, "extra": 2406}}), encoding="utf-8")
    loaded = sd.load_pilot_snapshot(path)
    assert loaded == {"pdf_cpp": {"missing": 4, "extra": 2406}}


# --- (e): the REAL committed snapshot loads cleanly and matches pinned ground truth -------------------


def test_real_committed_snapshot_loads_cleanly_and_matches_pinned_ground_truth():
    loaded = sd.load_pilot_snapshot(SNAPSHOT_FILE)
    assert set(loaded.keys()) == set(REAL_PILOT_GROUND_TRUTH.keys())
    assert len(loaded) == 32
    for pilot, expected in REAL_PILOT_GROUND_TRUTH.items():
        actual = loaded[pilot]
        for category in sd.CATEGORIES:
            assert actual.get(category, 0) == expected[category], (
                f"{pilot}.{category}: snapshot has {actual.get(category, 0)!r}, "
                f"real ground truth is {expected[category]!r}"
            )


def test_real_committed_snapshot_default_path_constant_points_at_it():
    assert sd.DEFAULT_PILOT_SNAPSHOT_FILE == SNAPSHOT_FILE


# --- (f): verify_pilot wired with the real snapshot ----------------------------------------------------


_GENEROUS_CPP_CEILINGS = {
    "cpp": {"missing": 10, "extra": 3000, "signature": 1000, "static": 0, "default": 0, "enum_value": 0}
}


def test_verify_pilot_with_the_real_snapshot_passes_when_counts_match_a_real_pilots_recorded_counts():
    """(f), part 1: a synthetic DiffCounts matching a REAL pilot's real recorded counts (pdf_cpp:
    missing=4, extra=2406, signature=694) must pass the ratchet -- it is not a regression, it is
    exactly the last recorded run. ``ceilings`` is deliberately generous (far above 0) so this test
    isolates the ratchet, never ``evaluate_ceilings``, which would otherwise fail this at its own
    default-0 ceiling regardless of the ratchet."""
    real_snapshot = sd.load_pilot_snapshot(SNAPSHOT_FILE)
    report = sd.verify_pilot(
        pilot="pdf_cpp",
        language="cpp",
        fixture={"extractor_version": 5, "types": [_widget_type_with_exact_counts()]},
        symbols=[_oracle_symbol_with_exact_counts()],
        ceilings=_GENEROUS_CPP_CEILINGS,
        require_extractor_version=True,
        current_extractor_version=5,
        pilot_snapshot=real_snapshot,
    )
    assert report.counts["missing"] == 4
    assert report.counts["extra"] == 2406
    assert report.counts["signature"] == 694
    assert report.passed is True, report.reasons


def test_verify_pilot_with_the_real_snapshot_fails_when_one_category_exceeds_the_recorded_count():
    """(f), part 2: the same shape but one category (extra) now exceeds pdf_cpp's own recorded
    2406 -- the ratchet must fail it, even though a generous --ceilings-file override would
    otherwise let it through."""
    real_snapshot = sd.load_pilot_snapshot(SNAPSHOT_FILE)
    report = sd.verify_pilot(
        pilot="pdf_cpp",
        language="cpp",
        fixture={"extractor_version": 5, "types": [_widget_type_with_exact_counts(extra=2407)]},
        symbols=[_oracle_symbol_with_exact_counts()],
        ceilings=_GENEROUS_CPP_CEILINGS,
        require_extractor_version=True,
        current_extractor_version=5,
        pilot_snapshot=real_snapshot,
    )
    assert report.counts["extra"] == 2407
    assert report.passed is False
    assert any(
        "extra" in reason and "regression against the last recorded real oracle run" in reason
        for reason in report.reasons
    )


def test_verify_pilot_without_a_pilot_snapshot_is_unaffected_by_this_card():
    """``pilot_snapshot`` defaults to None -- byte-for-byte the behavior verify_pilot had before
    this card when no snapshot is passed at all."""
    fixture = {
        "extractor_version": 5,
        "types": [
            {
                "name": "Widget",
                "kind": "class_declaration",
                "class_import": "Widget",
                "methods": [{"name": "build", "params": []}],
                "properties": [],
                "enum_members": [],
            }
        ],
    }
    symbols = [sd.OracleSymbol("Widget", "class", members=(sd.OracleMember("build", "method"),))]
    report = sd.verify_pilot(
        pilot="pdf_cpp",
        language="cpp",
        fixture=fixture,
        symbols=symbols,
        ceilings=sd.LANGUAGE_ERROR_CEILINGS,
        require_extractor_version=True,
        current_extractor_version=5,
    )
    assert report.passed is True


def _widget_type_with_exact_counts(*, extra: int = 2406) -> dict:
    """A baked fixture type shaped to produce EXACTLY ``missing=4, signature=694, extra=extra``
    against ``_oracle_symbol_with_exact_counts()`` below, matching ``pdf_cpp``'s own real recorded
    shape (4, 694, 2406) by default. ``diff_symbol`` matches members by bare name via a dict
    (``baked_methods``/the oracle's own name set) -- every name below is therefore distinct, or
    duplicates would silently collapse and the exact counts this test pins would drift."""
    sig_mismatch_methods = [{"name": f"Sig{i}", "params": []} for i in range(694)]  # oracle wants ["x"]
    extra_methods = [{"name": f"Phantom{i}", "params": []} for i in range(extra)]  # oracle never names these
    return {
        "name": "Annotation",
        "kind": "class_declaration",
        "class_import": "Annotation",
        "methods": sig_mismatch_methods + extra_methods,
        "properties": [],
        "enum_members": [],
    }


def _oracle_symbol_with_exact_counts() -> sd.OracleSymbol:
    """The oracle counterpart to ``_widget_type_with_exact_counts()``: 4 real members the baked
    fixture never serves at all (-> missing=4) and 694 real members the baked fixture serves under
    the same name but with a mismatched parameter list (-> signature=694, since the oracle expects
    one param, ``"x"``, and the baked entry declares none)."""
    missing_members = tuple(sd.OracleMember(f"Missing{i}", "method") for i in range(4))
    signature_mismatch_members = tuple(
        sd.OracleMember(f"Sig{i}", "method", params=(sd.OracleParam("x"),)) for i in range(694)
    )
    return sd.OracleSymbol("Annotation", "class", members=missing_members + signature_mismatch_members)


# --- CLI wiring: --pilot-snapshot-file ----------------------------------------------------------------


def test_parse_args_default_pilot_snapshot_file_points_at_the_real_committed_file():
    args = sd.parse_args([])
    assert Path(args.pilot_snapshot_file) == SNAPSHOT_FILE


def test_parse_args_accepts_an_explicit_disable_value():
    args = sd.parse_args(["--pilot-snapshot-file", "none"])
    assert args.pilot_snapshot_file == "none"
    args2 = sd.parse_args(["--pilot-snapshot-file", ""])
    assert args2.pilot_snapshot_file == ""
