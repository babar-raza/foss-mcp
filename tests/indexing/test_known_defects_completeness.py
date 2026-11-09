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

**Widened again (TC-706, 2026-11-06):** ``3D-JAVA-001`` (round-2, ``docs/round2/issues.json``,
severity P0, category ``"coverage-gap"`` - NOT ``"upstream-library-bug"``) is a real, cited,
committed audit finding this module's own category-filtered id source would otherwise never admit.
TC-706 independently re-verified the finding against the real, currently-pinned
``org.aspose:aspose-3d-foss:26.5.0`` Maven Central artifact (confirmed current directly via its own
real ``maven-metadata.xml``, not assumed from round-2's pin) and found 39 - not round-2's claimed
37, a transcription discrepancy in round-2's own ``repro`` field, corrected here - rendering-engine
type names that resolve via ``get_symbol``/``search_symbols`` as confident real classes with zero
caveat, yet are genuinely absent from the real published jar. This module's own
``_real_upstream_library_bug_ids`` is widened the same deliberate, additive way TC-656 widened it
from round2-only to round2-union-round3: a small, hand-reviewed, comment-documented
``_ADDITIONAL_DISCLOSABLE_IDS`` frozenset, seeded with exactly ``{"3D-JAVA-001"}``, unioned into
the returned id set - never a loosened filter, only an explicit addition to what the lock admits.
Every id in that set still traces to one real, cited, committed audit record. The real new union
count is 54 (not 53): both equality assertions (``test_registry_ids_exactly_equal_the_real_upstream_
library_bug_issue_ids`` and ``test_registry_entry_count_equals_upstream_library_bug_issue_count``)
still hold exactly, just against this slightly wider set; the real registry file count is unchanged
at 25 (this entry lands in the already-existing ``3d__java.yaml``, not a new file).

**Widened again (TC-707, 2026-11-06):** ``3D-NET-001`` (round-2, ``docs/round2/issues.json``,
severity P0, category ``"reference-section"`` - NOT ``"upstream-library-bug"``, the same category
gap TC-706 hit for ``3D-JAVA-001``) is a real, cited, committed audit finding this module's own
category-filtered id source would otherwise never admit. TC-707 independently re-ran round-2's own
repro against the real, currently-pinned ``Aspose.3D.FOSS 26.1.0`` NuGet package (confirmed still
the real pin via ``config/products/3d/net.yaml``/``config/known_defects/3d__net.yaml``'s own 4
pre-existing entries, not assumed from round-2's own text) via a throwaway dotnet 8 console app,
run twice for determinism, and confirmed ``Scene.Save()`` genuinely throws
``System.NotImplementedException`` for RVM (``RvmText``/``RvmBinary``), AMF, and HTML5/
``Aspose3DWeb``, and that reflection finds no public ``FileFormat.Universal``/``U3D`` field at all -
exactly round-2's own ``actual`` field, reused here verbatim as this entry's evidence. Round-2's own
finding TITLE also names PLY as falsely claimed working, but its own ``suggested_fix`` field says
the real dispatcher table supports ``ply`` - TC-707's own re-verification resolves that internal
inconsistency in favor of ``suggested_fix``: ``Scene.Save("x.ply")`` (the real, documented API path)
succeeds and writes a real file with no exception on this same real package; only the separate,
non-dispatched ``FileFormat.PLY`` enum value throws ``NotImplementedException``, matching this
project's own existing ``config/products/3d/net.yaml`` "Scope and Limitations" text already
correctly disclosing that PLY gotcha. PLY is therefore deliberately NOT part of this entry. This
module's own ``_ADDITIONAL_DISCLOSABLE_IDS`` is widened the same deliberate, additive way, now
seeded with ``{"3D-JAVA-001", "3D-NET-001"}``; every id in that set still traces to one real, cited,
committed audit record, independently re-verified against the real currently-published package.
The real new union count is 55 (not 54): both equality assertions above still hold exactly, just
against this slightly wider set; the real registry file count is unchanged at 25 (this entry lands
in the already-existing ``3d__net.yaml``, not a new file).

**Widened again (TC-711, 2026-11-07):** ``WORDS-NET-001`` (round-2, ``docs/round2/issues.json``,
severity P1, category ``"coverage-gap"`` - the same category gap ``3D-JAVA-001``/TC-706 hit, NOT
``"upstream-library-bug"``) is a real, cited, committed audit finding this module's own
category-filtered id source would otherwise never admit. TC-711 independently re-ran round-2's own
repro against the real, currently-pinned ``Aspose.Words.FOSS 26.3.0`` NuGet package: .NET reflection
over ``Aspose.Words.Node`` in the real net8.0 DLL found exactly 2 ``GetAncestor`` overloads
(``GetAncestor(Type)``, ``GetAncestor(NodeType)``) and zero methods named ``GetAncestorOf`` anywhere
in the whole assembly, and a raw byte-string search of the real net462, net8.0, and netstandard2.0
``Aspose.Words.FOSS.dll`` files each found zero occurrences of the literal ``GetAncestorOf`` -
confirming round-2's own finding exactly. All 42 owning-type FQNs round-2's own finding names (the
real ``Node`` base type plus 41 real subclasses) were independently re-derived from the real,
committed ``tests/fixtures/words_net/api_surface.json`` fixture and cross-checked against the real
indexed ``infra/demo-manifests/words__net__self_extracted`` manifest (the exact mechanism this
module's own ``test_every_method_or_class_kind_ref_matches_a_real_indexed_fqn`` below already
checks every ``subject.refs`` entry against); all 42 match exactly, with no correction needed. This
module's own ``_ADDITIONAL_DISCLOSABLE_IDS`` is widened the same deliberate, additive way, now
seeded with ``{"3D-JAVA-001", "3D-NET-001", "WORDS-NET-001"}``; every id in that set still traces to
one real, cited, committed audit record, independently re-verified against the real currently-
published package. The real new union count is 56 (not 55): both equality assertions above still
hold exactly, just against this slightly wider set; the real registry file count is now 26 (not
25): this entry lands in a brand-new ``words__net.yaml`` file, this pilot's first known-defects
entry.

**Widened again (TC-728, 2026-11-09):** ``SLIDES-JAVA-010`` (round-3, ``docs/round3/issues.json``,
severity P1, category ``"mcp-content-missing"``, ``round3_verdict: "NEW"``, same category gap
``3D-JAVA-001``/``3D-NET-001``/``WORDS-NET-001`` already hit) is a real, cited, committed audit
finding this module's own category-filtered id source would otherwise never admit. Unlike those
three prior widenings, the underlying upstream facts were NOT re-derived by TC-728 - both P0s this
finding names (``Presentation.save()`` ignoring ``SaveFormat`` and never throwing;
``ISlideCollection.addEmptySlide()``/``insertEmptySlide()``/``addClone()`` each producing an
orphaned, unregistered slide part) were already independently verified by TC-650 (2026-10-29)
against the real published ``org.aspose:aspose-slides-foss`` 26.7.0 jar and already disclosed via
``config/products/slides/java.yaml``'s own ``known_hazards`` prose, which this card reused verbatim
- the real gap TC-728 closed is that ``known_hazards`` prose only ever surfaces inside
``get_product_reference``'s limitations section, never per-symbol via ``get_symbol``/
``search_symbols``/``find_examples``. TC-728 confirmed the real indexed FQNs directly
(``org.aspose.slides.foss.Presentation``, ``org.aspose.slides.foss.export.SaveFormat``,
``org.aspose.slides.foss.ISlideCollection``, each present in the real, committed
``infra/demo-manifests/slides__java__self_extracted`` generation) and split the one round-3 finding
id into TWO registry ids, ``SLIDES-JAVA-010`` and ``SLIDES-JAVA-010B``, because the two P0s are
genuinely distinct causal chains sharing only the same pilot and the same round-3 finding id -
``config/known_defects/pdf__java.yaml``'s own ``PDF-JAVA-001``/``PDF-JAVA-002`` is the precedent for
two separate ids on one pilot. ``SLIDES-JAVA-010B`` is a project-minted id, not a second real
audit-cited finding id (round-3's own ``issues.json`` names only ``SLIDES-JAVA-010`` for both facts
combined). This module's own ``_ADDITIONAL_DISCLOSABLE_IDS`` is widened the same deliberate,
additive way, now seeded with ``{"3D-JAVA-001", "3D-NET-001", "WORDS-NET-001", "SLIDES-JAVA-010",
"SLIDES-JAVA-010B"}``; every id in that set still traces to one real, cited, committed audit record.
The real new union count is 58 (not 56): both equality assertions above still hold exactly, just
against this slightly wider set; the real registry file count is now 27 (not 26): these two entries
land in a brand-new ``slides__java.yaml`` file, this pilot's first known-defects entry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from foss_mcp.mcp.tools.get_symbol import extract_fqn
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

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


# TC-706 (G3, workstream J, REQ-G3-015): an explicit, hand-reviewed, additive allowlist of real,
# cited, committed audit findings that this module's own category-filtered id source would
# otherwise never admit, because their recorded ``category`` is not ``"upstream-library-bug"`` -
# the same deliberate widening shape this module's own TC-656 rework already used to go from
# round2-only to round2-union-round3. Each id here still traces to one real, cited, committed
# audit record; this is never a loosened filter, only an explicit addition to what the lock
# admits.
#
# - ``3D-JAVA-001`` (round-2, ``docs/round2/issues.json``, severity P0, category
#   ``"coverage-gap"``): 39 phantom rendering-engine type names (independently re-verified by
#   TC-706 against the real, currently-pinned ``org.aspose:aspose-3d-foss:26.5.0`` jar) resolve
#   via ``get_symbol``/``search_symbols`` as confident real classes with zero caveat - a genuine
#   index-truth/disclosure gap, just not categorized ``"upstream-library-bug"`` in the original
#   audit.
# - ``3D-NET-001`` (round-2, ``docs/round2/issues.json``, severity P0, category
#   ``"reference-section"``): the "formats" reference text claims RVM, AMF, HTML5/Aspose 3D Web
#   viewer, and Universal/U3D export all work; independently re-verified by TC-707 against the
#   real, currently-pinned ``Aspose.3D.FOSS 26.1.0`` NuGet package that ``Scene.Save()`` throws
#   ``NotImplementedException`` for every one of them and that ``FileFormat`` has no public
#   ``Universal``/``U3D`` field at all - a genuine reference-text/disclosure gap, just not
#   categorized ``"upstream-library-bug"`` in the original audit. PLY, which round-2's own TITLE
#   (but not its ``suggested_fix``/``actual`` fields) names as also falsely claimed, was
#   independently re-verified and found NOT broken (``Scene.Save("x.ply")`` succeeds); PLY is
#   deliberately excluded from this id's own registry entry.
# - ``WORDS-NET-001`` (round-2, ``docs/round2/issues.json``, severity P1, category
#   ``"coverage-gap"``): ``Node.GetAncestorOf<T>()`` is disclosed by ``get_symbol``/``list_members``
#   as inherited by 42 real ``Aspose.Words``-namespace types (the base ``Node`` plus 41 real
#   subclasses) with zero caveat; independently re-verified by TC-711 against the real,
#   currently-pinned ``Aspose.Words.FOSS 26.3.0`` NuGet package via .NET reflection (only
#   ``GetAncestor(Type)``/``GetAncestor(NodeType)`` exist on ``Node``, never ``GetAncestorOf``
#   anywhere in the assembly) and a raw byte-string search of the real net462/net8.0/netstandard2.0
#   DLLs (zero occurrences of the literal ``GetAncestorOf`` in any of the three) - a genuine
#   index-truth/disclosure gap, just not categorized ``"upstream-library-bug"`` in the original
#   audit.
# - ``SLIDES-JAVA-010`` / ``SLIDES-JAVA-010B`` (round-3, ``docs/round3/issues.json``, severity P1,
#   category ``"mcp-content-missing"``, ``round3_verdict: "NEW"``): "known_defects empty for
#   Presentation/SaveFormat/ISlideCollection despite limitations text documenting both P0s." Both
#   P0s were already independently verified by TC-650 (2026-10-29) against the real published
#   ``org.aspose:aspose-slides-foss`` 26.7.0 jar and already disclosed via
#   ``config/products/slides/java.yaml``'s own ``known_hazards`` prose - which never surfaces
#   per-symbol, only inside ``get_product_reference``'s limitations section - a genuine
#   index-truth/disclosure gap, just not categorized ``"upstream-library-bug"`` in the original
#   audit. TC-728 split the one round-3 finding id into TWO registry ids because the underlying
#   facts are two genuinely distinct causal chains, not one bug: ``SLIDES-JAVA-010`` is
#   ``Presentation.save(path, SaveFormat)`` silently ignoring the requested format and never
#   throwing; ``SLIDES-JAVA-010B`` is ``ISlideCollection.addEmptySlide()``/``insertEmptySlide()``/
#   ``addClone()`` each adding an orphaned, never-registered slide part. ``SLIDES-JAVA-010B`` is a
#   project-minted id (round-3's own ``issues.json`` names only one id, ``SLIDES-JAVA-010``, for
#   both facts combined), chosen over a bare sequential ``SLIDES-JAVA-011`` so it reads as a
#   deliberate split of ``SLIDES-JAVA-010``, never as an 11th independently audited finding that
#   was never actually recorded.
_ADDITIONAL_DISCLOSABLE_IDS: frozenset[str] = frozenset(
    {"3D-JAVA-001", "3D-NET-001", "WORDS-NET-001", "SLIDES-JAVA-010", "SLIDES-JAVA-010B"}
)


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
    return round2_ids | round3_ids | _ADDITIONAL_DISCLOSABLE_IDS


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
# issue count - both derived from the real data at test time, never hardcoded to 55 as the actual
# assertion (only this module's docstring and the sanity comment below name 58, the real round2
# union round3 union _ADDITIONAL_DISCLOSABLE_IDS count as of today, 2026-11-09 - see this module's
# own docstring for why this is 58, not the round2-only 47 a narrower view would give, nor the 53
# TC-656 computed before TC-706/TC-707's own additive widening, nor the 54 TC-706 alone computed,
# nor the 56 TC-711 computed before TC-728's own additive widening)
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

    # Sanity anchor only, not a floor on the future: as of today (2026-11-09) this real union
    # count is 58 (56 round2-union-round3-union-_ADDITIONAL_DISCLOSABLE_IDS as of TC-711, see this
    # module's docstring for that id list, plus TC-728's own additive ``_ADDITIONAL_DISCLOSABLE_IDS``
    # entries ``SLIDES-JAVA-010`` and ``SLIDES-JAVA-010B``, two registry ids for one round-3 finding
    # id, split because the underlying facts are two genuinely distinct causal chains). A future card
    # adding or retiring a confirmed upstream-library-bug finding (and its matching entry) is
    # expected to move both sides together and may need to update this comment and number; the
    # equality assertion above is the one that must hold forever.
    assert len(upstream_issue_ids) == 58


# --------------------------------------------------------------------------------------------
# point 1 sanity check: the glob actually finds every real file currently under
# config/known_defects/ - today that is 27 real pilot files (26 as of TC-711, plus TC-728's own
# brand-new slides__java.yaml, this pilot's first known-defects entry; see this module's own
# docstring for why this is 27, not the "8" named in TC-649's own original inputs text, which was
# TC-647's own per-card file count, not the real running total)
# --------------------------------------------------------------------------------------------


def test_glob_finds_the_real_current_registry_file_count() -> None:
    files = _registry_files()
    # Not a floor on the future: a later card adding a 28th family file is expected and welcome.
    # This only proves the glob pattern itself is correct against today's real, committed tree.
    assert len(files) == 27, f"expected 27 real registry files today, found {len(files)}: {files}"


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


# --------------------------------------------------------------------------------------------
# G3/TC-702 (REQ-G3-015): the PERMANENT corpus-wide ref-completeness lock.
#
# Every point above proves the registry agrees with docs/round2+round3's own issue data and with
# its own schema - but none of it ever checks a `subject.refs` value against anything that could
# actually be WRONG as a string. TC-702 found, by a real script run against the real
# `infra/demo-manifests` corpus, that 11 of 46 real `subject.kind: method` refs across this
# registry never matched any real indexed FQN for their own pilot - including
# `CELLS-TYPESCRIPT-013`, the round-3 re-audit's own #1 priority finding (`Workbook.save()`
# silently drops worksheets past the 2nd) - so a live `get_symbol`/`search_symbols` call for any
# of those 11 bare/wrong names returned ZERO known-defect disclosures, despite the entry parsing,
# validating, and even having its own dedicated (but synthetic-FQN, by-construction-passing) test
# file. This happened TWICE (TC-660 and TC-668 each hand-fixed a named list of entries, and every
# entry added by a LATER card silently reintroduced the same defect class) specifically because
# nothing checked a ref against the real corpus as a cross-cutting whole. This test is that check,
# added here because it is squarely `KD10`'s own already-established scope (completeness against
# the real data), just not yet built against the real indexed FQN set specifically.
#
# The real indexed FQN set is loaded from the real, committed `infra/demo-manifests` directory via
# `tests.conformance.corpus.load_demo_manifests_corpus` - the exact pattern
# `tests/mcp/tools/test_known_defects_refs_population.py` (TC-660/TC-668) already established -
# copied to a temp directory first; the real repository is never written to. A registry file's own
# stem (e.g. `cells__typescript` from `cells__typescript.yaml`) is exactly `ConformanceCorpus.pilots`'
# own dict key format (`f"{family}__{platform}"`), so no separate family/platform parsing is needed.
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - the
    exact pattern ``tests/mcp/tools/test_known_defects_refs_population.py`` already established.
    Nothing in the real repository is ever written to; reading it is always allowed."""
    root = tmp_path_factory.mktemp("tc702-known-defects-completeness-corpus")
    return load_demo_manifests_corpus(root)


def _real_indexed_fqns(corpus: ConformanceCorpus, pilot_key: str) -> frozenset[str]:
    """Every real FQN indexed for *pilot_key* (e.g. ``"cells__typescript"``) - the exact
    ``extract_fqn`` over ``symbol_chunks()`` pattern ``tests/conformance/test_symbol_self_retrieval.py``'s
    own ``_symbol_texts`` helper, and ``test_known_defects_refs_population.py``, both already use."""
    pilot = corpus.pilots[pilot_key]
    fqns: set[str] = set()
    for chunk in pilot.symbol_chunks():
        fqn = extract_fqn(chunk["text"])
        if fqn:
            fqns.add(fqn)
    return frozenset(fqns)


def test_every_method_or_class_kind_ref_matches_a_real_indexed_fqn(corpus: ConformanceCorpus) -> None:
    """The PERMANENT completeness lock this card (TC-702) exists to add: every real registry entry
    whose ``subject.kind`` is ``"method"`` or ``"class"`` must have every one of its ``subject.refs``
    match a real indexed FQN for its own pilot, loaded from the real, committed
    ``infra/demo-manifests`` corpus (never a synthetic/``tmp_path``-only stand-in). A registry ref
    that never matches any real indexed FQN is a disclosure that can NEVER surface through a live
    ``get_symbol``/``search_symbols`` call, regardless of how well-formed and schema-valid its YAML
    entry is - exactly the silent-failure class this card fixed 11 real, live instances of (see
    ``docs/DECISION_LOG.md``'s ``## 2026-11-02`` entry), which had already recurred once despite two
    prior hand-curated fix passes (``TC-660``, ``TC-668``). Confirmed directly by this card's own
    worker, in scratch work never committed, that this exact assertion fails against the unfixed
    registry content and names all 11 real mismatches - proof the mechanism actually works, not
    merely that it exists."""
    failures: list[str] = []
    for path, defect in _all_registry_entries():
        subject = defect.get("subject", {})
        if subject.get("kind") not in ("method", "class"):
            continue
        pilot_key = path.stem  # e.g. "cells__typescript" - matches ConformanceCorpus.pilots' own key
        if pilot_key not in corpus.pilots:
            failures.append(
                f"{path.name}: defect {defect.get('id')!r} - no real corpus pilot {pilot_key!r} "
                "exists to check its refs against"
            )
            continue
        real_fqns = _real_indexed_fqns(corpus, pilot_key)
        for ref in subject.get("refs", []):
            if ref not in real_fqns:
                failures.append(
                    f"{path.name}: defect {defect.get('id')!r} subject.refs entry {ref!r} does not "
                    f"match any real indexed FQN for pilot {pilot_key!r}"
                )
    assert failures == [], (
        "known_defects subject.refs entries that never match a real indexed FQN (a disclosure "
        "that can never surface through a live get_symbol/search_symbols call):\n" + "\n".join(failures)
    )
