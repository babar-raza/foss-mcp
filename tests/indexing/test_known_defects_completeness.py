"""KD10 (G3/TC-649, workstream J, REQ-G3-015): the disclosure program's completeness lock.

TC-644/645/646/647 (KD6-KD9) together wrote real, schema-conforming ``known_defects`` entries for
every ``category: "upstream-library-bug"`` issue in ``docs/round2/issues.json`` - one entry per
issue id, across eight pilot registry files (14 3d-family + 12 cells-family + 6 pdf/slides-family +
15 remaining-family = 47 real findings as of 2026-11-01, split across 23 real
``config/known_defects/*.yaml`` files, since several of those files record zero entries for a pilot
with no recorded upstream-library-bug finding).

This module writes the permanent completeness lock those four cards' own tests never asserted as a
cross-cutting whole: that the two data sources - the round-2 audit's own
``docs/round2/issues.json`` and this project's own ``config/known_defects/`` registry - agree
EXACTLY, with no drift possible in either direction, regardless of which family a future id lands
in. Every assertion here is derived algorithmically from the real, committed data at test-collection
or test-run time; nothing here is a hand-copied id list that could silently fall out of sync with
either file.

Deviation from this card's own text, noted for the record: TC-649's own ``inputs`` instruct a
sanity test asserting "the real count of files found is 8" for the glob in point 1. That figure is
TC-647's own per-card file count (the eight files TC-647 itself wrote), not the real total file
count under ``config/known_defects/`` once TC-644/645/646/647 are all present - which is 23 as of
today (4 3d + 6 cells + 2 pdf + 3 slides + 8 remaining). This module's own file-count sanity test
therefore asserts the real current total, 23, with the same "not a floor on the future" caveat the
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
ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"


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
    issues = json.loads(ISSUES_PATH.read_text(encoding="utf-8"))
    assert isinstance(issues, list), "docs/round2/issues.json must be a top-level JSON array"
    return issues


def _real_upstream_library_bug_ids() -> set[str]:
    """The real, algorithmic answer to "which round-2 findings must have a known-defect entry" -
    every ``docs/round2/issues.json`` entry whose ``category`` is ``"upstream-library-bug"``,
    computed directly from the audit's own committed data. Never a hand-copied id list, so drift
    in either file is caught below."""
    return {issue["id"] for issue in _real_round2_issues() if issue.get("category") == "upstream-library-bug"}


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
# issue count - both derived from the real data at test time, never hardcoded to 47 as the actual
# assertion (only this module's docstring and the sanity comment below name 47, the real count as
# of today, 2026-11-01)
# --------------------------------------------------------------------------------------------


def test_registry_entry_count_equals_upstream_library_bug_issue_count() -> None:
    upstream_issue_ids = _real_upstream_library_bug_ids()
    registry_ids = _all_registry_ids()

    # Every registry id is unique (proven by the global no-duplicate test above), so the raw
    # entry count and the id-set size agree; comparing both derived counts directly here, not the
    # literal 47, is what makes this assertion non-tautological.
    assert len(registry_ids) == len(upstream_issue_ids), (
        f"registry entry count ({len(registry_ids)}) does not equal the real upstream-library-bug "
        f"issue count ({len(upstream_issue_ids)})"
    )

    # Sanity anchor only, not a floor on the future: as of today (2026-11-01) this real count is
    # 47. A future card adding or retiring a confirmed upstream-library-bug finding (and its
    # matching entry) is expected to move both sides together and may need to update this comment
    # and number; the equality assertion above is the one that must hold forever.
    assert len(upstream_issue_ids) == 47


# --------------------------------------------------------------------------------------------
# point 1 sanity check: the glob actually finds every real file currently under
# config/known_defects/ - today that is 23 real pilot files (see this module's own docstring for
# why this is 23, not the "8" named in this card's own inputs text, which is TC-647's own
# per-card file count, not the real running total)
# --------------------------------------------------------------------------------------------


def test_glob_finds_the_real_current_registry_file_count() -> None:
    files = _registry_files()
    # Not a floor on the future: a later card adding a 24th family file is expected and welcome.
    # This only proves the glob pattern itself is correct against today's real, committed tree.
    assert len(files) == 23, f"expected 23 real registry files today, found {len(files)}: {files}"


# --------------------------------------------------------------------------------------------
# additional input: every registry id matches the exact string format used in
# docs/round2/issues.json - no incidental whitespace, no case difference silently tolerated
# --------------------------------------------------------------------------------------------


def test_every_registry_id_matches_its_real_round2_issue_id_exactly() -> None:
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
                f"{path.name}: id {raw_id!r} matches a real round-2 issue id only after "
                "case-folding - the exact case must match docs/round2/issues.json verbatim"
            )
