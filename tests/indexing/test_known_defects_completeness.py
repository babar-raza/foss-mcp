"""KD10 (G3/TC-649, workstream J, REQ-G3-015): the disclosure program's completeness lock.

TC-644/645/646/647 (KD6-KD9) together wrote real, schema-conforming ``known_defects`` entries for
every ``category: "upstream-library-bug"`` issue in ``docs/round2/issues.json`` - one entry per
issue id, across eight pilot registry files (14 3d-family + 12 cells-family + 6 pdf/slides-family +
15 remaining-family = 47 real findings as of 2026-11-01, split across 23 real
``config/known_defects/*.yaml`` files, since several of those files record zero entries for a pilot
with no recorded upstream-library-bug finding).

This module writes the permanent completeness lock those four cards' own tests never asserted as a
cross-cutting whole: that the real-world data sources - originally just the round-2 audit's own
``docs/round2/issues.json``, widened here (TC-656 rework, 2026-11-02) to the UNION of
``docs/round2/issues.json`` and ``docs/round3/issues.json`` - and this project's own
``config/known_defects/`` registry agree EXACTLY, with no drift possible in either direction,
regardless of which family a future id lands in, and regardless of which committed audit round
first recorded it. Every assertion here is derived algorithmically from the real, committed data at
test-collection or test-run time; nothing here is a hand-copied id list that could silently fall out
of sync with either file.

Why a union, not a replacement: ``docs/round3/issues.json`` re-ran the identical audit protocol
against a later build and reconciled every round-2 finding (FIXED / STILL BROKEN / CHANGED) before
hunting for anything new, so most of its ``upstream-library-bug`` ids are the SAME ids round-2 named
(carried over verbatim). A handful are genuinely new in round3 (``round2_status: null`` in
``docs/round3/issues.json``), including this card's own ``CELLS-TYPESCRIPT-013``. A few ids also
move OUT of the category between rounds in either direction (``CELLS-RUST-011`` was
``upstream-library-bug`` in round2 and is ``coverage-gap``/FIXED in round3; ``EML-NET-003`` and
``PDF-NET-003`` were a different category in round2 and are ``upstream-library-bug`` in round3) -
this lock's real intent (every id ever confirmed as a real upstream-library-bug finding, by EITHER
committed round, has exactly one entry; no orphans) means a later round reclassifying a finding away
from this category does not retroactively erase an earlier round's own real, committed record of it,
so the union is the honest union of both rounds' own id sets, not round3's reconciled view alone.

**Rework history, for the record:** TC-656 attempt 2 widened this module's id source to the
round2-union-round3 set (53 ids, not the 47 a round-2-only view gives - computed directly via
``len(_real_upstream_library_bug_ids())``, never hardcoded) and found, by mechanically applying
that widening rather than cherry-picking it, that the real registry at the time had a written
entry for only ONE of round3's 6 genuinely-new ids (``CELLS-TYPESCRIPT-013``); the other 5 -
``3D-JAVA-014`` (3d/java), ``CELLS-GO-012`` (cells/go), ``EML-NET-003`` (email/net),
``PAGE-PYTHON-010`` (page/python), and ``PDF-NET-003`` (pdf/net) - had no registry entry anywhere,
and their registry files were outside that attempt's ``write_paths``. That attempt reported the
gap rather than working around it (AGENTS.md worker rule 5), leaving this module's two equality
assertions (``test_registry_ids_exactly_equal_the_real_upstream_library_bug_issue_ids`` and
``test_registry_entry_count_equals_upstream_library_bug_issue_count``) failing, correctly - the
completeness lock doing exactly its designed job, not a defect in the widening. Attempt 3 added all
5 missing registry files/entries to this card's ``write_paths`` and disclosed them for real; both
assertions now pass with zero remaining gaps, and neither assertion was ever weakened across either
attempt.

Deviation from this card's own text, noted for the record: TC-649's own ``inputs`` instruct a
sanity test asserting "the real count of files found is 8" for the glob in point 1. That figure is
TC-647's own per-card file count (the eight files TC-647 itself wrote), not the real total file
count under ``config/known_defects/`` once TC-644/645/646/647 are all present - which was 23 as of
2026-11-01 (4 3d + 6 cells + 2 pdf + 3 slides + 8 remaining), and is 25 as of TC-656 attempt 3 (plus
the brand-new ``email__net.yaml`` and ``pdf__net.yaml``). This module's own file-count sanity test
therefore asserts the real current total, 25, with the same "not a floor on the future" caveat the
card's own text uses for the (evidently miscopied) "8".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_DIR = REPO_ROOT / "config" / "known_defects"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"
ROUND3_ISSUES_PATH = REPO_ROOT / "docs" / "round3" / "issues.json"


def _registry_files() -> list[Path]:
    """Every real, committed known-defects registry file, found by glob - never a hardcoded
    filename list, so a future 9th (or 24th) family file is picked up automatically."""
    return sorted(REGISTRY_DIR.glob("*.yaml"))


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_registry_document(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path.name}: top-level YAML document must be a mapping"
    return document


def _all_registry_entries() -> list[tuple[Path, dict[str, Any]]]:
    """Every ``(file path, raw defect mapping)`` pair across every real registry file."""
    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in _registry_files():
        document = _load_registry_document(path)
        for defect in document.get("defects", []):
            entries.append((path, defect))
    return entries


def _all_registry_ids() -> list[str]:
    return [defect["id"] for _path, defect in _all_registry_entries()]


def _real_round2_issues() -> list[dict[str, Any]]:
    issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    assert isinstance(issues, list), "docs/round2/issues.json must be a top-level JSON array"
    return issues


def _real_round3_issues() -> list[dict[str, Any]]:
    """Every real issue recorded in ``docs/round3/issues.json`` - a DIFFERENT real shape from
    round2's flat list: a dict with a ``"pilots"`` key, each pilot object holding its own
    ``"issues"`` list. Confirmed directly against the real, committed file (not assumed) before
    writing this helper."""
    document = json.loads(ROUND3_ISSUES_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict) and "pilots" in document, (
        "docs/round3/issues.json must be a top-level JSON object with a 'pilots' key"
    )
    issues: list[dict[str, Any]] = []
    for pilot in document["pilots"]:
        assert isinstance(pilot, dict) and "issues" in pilot, (
            f"docs/round3/issues.json: pilot entry missing 'issues': {pilot!r}"
        )
        issues.extend(pilot["issues"])
    return issues


def _real_upstream_library_bug_ids() -> set[str]:
    """The real, algorithmic answer to "which findings, from EITHER committed audit round, must
    have a known-defect entry" - the union of every ``docs/round2/issues.json`` entry and every
    ``docs/round3/issues.json`` entry whose ``category`` is ``"upstream-library-bug"``, computed
    directly from both audits' own committed data. Never a hand-copied id list, so drift in any of
    the three files (round2, round3, the registry) is caught below.

    A union, not round3's reconciled view alone: see this module's own docstring for why a finding
    round3 reclassifies OUT of this category does not erase round2's own real record of it, and a
    finding round3 reclassifies INTO it (e.g. ``EML-NET-003``, ``PDF-NET-003``) is included even
    though round2 named it under a different category."""
    round2_ids = {
        issue["id"] for issue in _real_round2_issues() if issue.get("category") == "upstream-library-bug"
    }
    round3_ids = {
        issue["id"] for issue in _real_round3_issues() if issue.get("category") == "upstream-library-bug"
    }
    return round2_ids | round3_ids


# --------------------------------------------------------------------------------------------
# point 3: the two sets of ids are EXACTLY equal, with the real symmetric difference reported on
# failure so a future drift is immediately actionable
# --------------------------------------------------------------------------------------------


def test_registry_ids_exactly_equal_the_real_upstream_library_bug_issue_ids() -> None:
    expected_ids = _real_upstream_library_bug_ids()
    written_ids = set(_all_registry_ids())

    missing_entries = expected_ids - written_ids
    orphan_entries = written_ids - expected_ids

    assert expected_ids == written_ids, (
        "config/known_defects/*.yaml drifted from docs/round2/issues.json's upstream-library-bug "
        f"category. Real round-2 upstream-library-bug issues with NO registry entry: "
        f"{sorted(missing_entries) or 'none'}. Registry entries with NO matching real round-2 "
        f"upstream-library-bug issue: {sorted(orphan_entries) or 'none'}."
    )


# --------------------------------------------------------------------------------------------
# point 4: every entry, in every real registry file, validates against the real schema
# --------------------------------------------------------------------------------------------


def test_every_registry_file_validates_against_the_real_known_defects_schema() -> None:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    failures: list[str] = []
    for path in _registry_files():
        document = _load_registry_document(path)
        errors = list(validator.iter_errors(document))
        if errors:
            failures.append(f"{path.name}: {errors}")
    assert failures == [], "schema validation failed for: " + "; ".join(failures)


# --------------------------------------------------------------------------------------------
# point 5: no id appears in more than one file, GLOBALLY across every real registry file - not
# merely within one file (TC-647's own test already checked only within its own 8 files)
# --------------------------------------------------------------------------------------------


def test_no_id_appears_in_more_than_one_registry_file_globally() -> None:
    locations: dict[str, list[str]] = {}
    for path, defect in _all_registry_entries():
        locations.setdefault(defect["id"], []).append(path.name)

    duplicates = {defect_id: files for defect_id, files in locations.items() if len(files) > 1}
    assert duplicates == {}, f"id(s) duplicated across multiple registry files: {duplicates}"


# --------------------------------------------------------------------------------------------
# point 6: the real, current registry entry count equals the real, current upstream-library-bug
# issue count - both derived from the real data at test time, never hardcoded to 53 as the actual
# assertion (only this module's docstring and the sanity comment below name 53, the real round2
# union round3 count as of today, 2026-11-02 - see this module's own docstring for why this is 53,
# not the round2-only 47 a narrower view would give)
# --------------------------------------------------------------------------------------------


def test_registry_entry_count_equals_upstream_library_bug_issue_count() -> None:
    upstream_issue_ids = _real_upstream_library_bug_ids()
    registry_ids = _all_registry_ids()

    # Every registry id is unique (proven by the global no-duplicate test above), so the raw
    # entry count and the id-set size agree; comparing both derived counts directly here, not the
    # literal 53, is what makes this assertion non-tautological.
    assert len(registry_ids) == len(upstream_issue_ids), (
        f"registry entry count ({len(registry_ids)}) does not equal the real upstream-library-bug "
        f"issue count ({len(upstream_issue_ids)})"
    )

    # Sanity anchor only, not a floor on the future: as of today (2026-11-02) this real union
    # count is 53 (47 round2 + 6 genuinely-new-in-round3, see this module's docstring for the id
    # list). A future card adding or retiring a confirmed upstream-library-bug finding (and its
    # matching entry) is expected to move both sides together and may need to update this comment
    # and number; the equality assertion above is the one that must hold forever.
    assert len(upstream_issue_ids) == 53


# --------------------------------------------------------------------------------------------
# point 1 sanity check: the glob actually finds every real file currently under
# config/known_defects/ - today that is 25 real pilot files (23 as of 2026-11-01, plus TC-656
# attempt 3's two brand-new files, email__net.yaml and pdf__net.yaml; see this module's own
# docstring for why this is 25, not the "8" named in TC-649's own original inputs text, which was
# TC-647's own per-card file count, not the real running total)
# --------------------------------------------------------------------------------------------


def test_glob_finds_the_real_current_registry_file_count() -> None:
    files = _registry_files()
    # Not a floor on the future: a later card adding a 26th family file is expected and welcome.
    # This only proves the glob pattern itself is correct against today's real, committed tree.
    assert len(files) == 25, f"expected 25 real registry files today, found {len(files)}: {files}"


# --------------------------------------------------------------------------------------------
# additional input: every registry id matches the exact string format used in the real issue
# data (round2 or round3) - no incidental whitespace, no case difference silently tolerated
# --------------------------------------------------------------------------------------------


def test_every_registry_id_matches_its_real_issue_id_exactly() -> None:
    exact_issue_ids = _real_upstream_library_bug_ids()
    normalized_issue_ids = {issue_id.strip().casefold() for issue_id in exact_issue_ids}

    for path, defect in _all_registry_entries():
        raw_id = defect["id"]
        assert isinstance(raw_id, str), f"{path.name}: id must be a string, got {raw_id!r}"
        assert raw_id == raw_id.strip(), (
            f"{path.name}: id {raw_id!r} has leading or trailing whitespace that issues.json ids never do"
        )
        if raw_id.casefold() in normalized_issue_ids and raw_id not in exact_issue_ids:
            raise AssertionError(
                f"{path.name}: id {raw_id!r} matches a real round2/round3 issue id only after "
                "case-folding - the exact case must match the audit's own issues.json verbatim"
            )
