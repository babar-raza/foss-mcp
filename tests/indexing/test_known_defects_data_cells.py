"""KD7 (G3/TC-645, workstream J, REQ-G3-015): the round-2 audit's 12 confirmed cells-family
upstream-library-bug findings, written as real, schema-conforming ``known_defects`` entries.

KD1-KD6 (TC-636, TC-639, TC-642, TC-641, TC-643, TC-644) built and wired the disclosure mechanism
itself and proved it with the 3d family's own real data; this card is the same pattern applied to
``cells``. This module proves the six files this card writes
(``config/known_defects/cells__go.yaml``, ``cells__java.yaml``, ``cells__net.yaml``,
``cells__python.yaml``, ``cells__rust.yaml``, ``cells__typescript.yaml``) are real,
schema-conforming, loader-validated, and that every entry's ``id`` is traceable, algorithmically
(not by a hand-copied id list that could silently drift from either file), back to a real
``category: "upstream-library-bug"`` finding for the ``cells`` family in
``docs/round2/issues.json`` - the round-2 audit's own already-committed evidence.

**Widened (TC-656 rework, 2026-11-02):** the real "which finding needs an entry" source is now the
UNION of ``docs/round2/issues.json`` and ``docs/round3/issues.json`` (a DIFFERENT real shape - a
dict with a ``"pilots"`` key, each pilot an object with its own ``"issues"`` list), both filtered to
the ``cells`` family and ``category: "upstream-library-bug"``. ``CELLS-TYPESCRIPT-013`` and
``CELLS-GO-012`` (both genuinely new in round3) are this card's own additions. See this module's
own ``_real_cells_upstream_library_bug_ids`` docstring for the full real accounting.

**Rework history, for the record:** attempt 2 of this card widened this file's id-matching logic
but had no ``write_paths`` to disclose ``CELLS-GO-012`` itself, so it reported the gap rather than
working around it (AGENTS.md worker rule 5) and left the two id-set-equality assertions below
failing, correctly, on that one id. Attempt 3 added ``config/known_defects/cells__go.yaml`` to this
card's ``write_paths`` and disclosed it for real; both assertions now pass with zero gaps.

**Widened again (G3/TC-796, 2026-11-14):** ``CELLS-TYPESCRIPT-006`` and ``CELLS-TYPESCRIPT-009``
are real, cited round-2 findings whose ``category`` is ``"mcp-content-missing"`` /
``"mcp-error-handling"`` rather than ``"upstream-library-bug"``; TC-796 independently re-verified
both against the real indexed ``excel-cells`` 1.0.0 source and disclosed them through
``config/known_defects/cells__typescript.yaml``. They are therefore written entries that are NOT in
the category-filtered union - listed explicitly in ``_ROUND2_GAP_DISCLOSURE_IDS`` below (each still
checked against the real ledger), so the written set equals the union PLUS exactly those two ids.
The written total is now 16 (typescript 8), the category-filtered union stays 14.

``CELLS-CPP-001`` is deliberately excluded: its real ``category`` in ``docs/round2/issues.json``
is ``"honesty"`` (a false FAQ claim this project's own content got wrong), not
``"upstream-library-bug"`` - a different program's job entirely (content-truth, not disclosure).
This module proves that exclusion is real and algorithmic: ``cells/cpp`` has zero
``upstream-library-bug`` findings in EITHER round's audit data, has no written registry file, and
``CELLS-CPP-001`` itself never appears among any written entry's id.

This module reads the real, committed ``config/known_defects/`` directory - never a
``tmp_path``-monkeypatched one - because the whole point is to prove the REAL data this card wrote
loads and surfaces correctly, not a synthetic fixture. The one exception is the symbol store used
by the ``get_symbol`` end-to-end test, which is an in-memory chunk store built the same way
``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2's own tests) and
``tests/indexing/test_known_defects_data_3d.py`` (KD6's own tests) already established - there is
no committed, indexed, queryable chunk corpus for cells/java in this repository, so a synthetic
store is the only way to exercise ``get_symbol`` itself end to end; the REGISTRY data it reads is
real and uncontrolled by this test.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
import yaml

from foss_mcp.indexing.known_defects import load_known_defects
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)
from foss_mcp.mcp.tools.get_symbol import ResolvedSignature, get_symbol
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_DIR = REPO_ROOT / "config" / "known_defects"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"
ROUND3_ISSUES_PATH = REPO_ROOT / "docs" / "round3" / "issues.json"

_FILES_BY_PLATFORM = {
    "go": "cells__go.yaml",
    "java": "cells__java.yaml",
    "net": "cells__net.yaml",
    "python": "cells__python.yaml",
    "rust": "cells__rust.yaml",
    "typescript": "cells__typescript.yaml",
}
_ALL_PLATFORMS = ("go", "java", "net", "python", "rust", "typescript")
# The real entry count actually WRITTEN per pilot (3+1+1+2+1+6 = 14): the 12 real round-2 findings
# named in TC-645's own instructions, plus TC-656's own CELLS-TYPESCRIPT-013 (typescript 5 -> 6)
# and CELLS-GO-012 (go 2 -> 3, round-3 rework attempt 3). This written-registry total now equals
# the "findings that need an entry" union total below (both 14) - TC-656 attempt 3 closed the
# CELLS-GO-012 gap attempt 2 found and reported rather than working around.
_EXPECTED_COUNTS = {"go": 3, "java": 1, "net": 1, "python": 2, "rust": 1, "typescript": 8}
# G3/TC-796: two more written typescript entries (typescript 6 -> 8, total 14 -> 16) whose real
# ledger category is not "upstream-library-bug", so they are not in the category-filtered union
# below; see this module's docstring.
_ROUND2_GAP_DISCLOSURE_IDS = frozenset({"CELLS-TYPESCRIPT-006", "CELLS-TYPESCRIPT-009"})


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_real_registry_document(platform: str) -> dict:
    path = REGISTRY_DIR / _FILES_BY_PLATFORM[platform]
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _real_round2_issues() -> list[dict]:
    return json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))


def _real_round3_issues() -> list[dict]:
    """Every real issue recorded in ``docs/round3/issues.json`` - a DIFFERENT real shape from
    round2's flat list: a dict with a ``"pilots"`` key, each pilot object holding its own
    ``"issues"`` list. Confirmed directly against the real, committed file before writing this
    helper (same shape ``tests/indexing/test_known_defects_completeness.py`` already confirmed)."""
    document = json.loads(ROUND3_ISSUES_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict) and "pilots" in document, (
        "docs/round3/issues.json must be a top-level JSON object with a 'pilots' key"
    )
    issues: list[dict] = []
    for pilot in document["pilots"]:
        issues.extend(pilot["issues"])
    return issues


def _real_cells_upstream_library_bug_ids() -> set[str]:
    """The real, algorithmic answer to "which findings, from EITHER committed audit round, is
    this card about" - the union of every ``docs/round2/issues.json`` entry and every
    ``docs/round3/issues.json`` entry whose ``pilot`` is one of the six real ``cells/*`` pilots
    this card writes for, and whose ``category`` is ``"upstream-library-bug"``. Computed directly
    from both audits' own committed data, never a hand-copied id list, so drift in any of the
    three files (round2, round3, the registry) is caught by the tests below. ``cells/cpp`` is
    deliberately excluded from this set in EITHER round (see
    ``test_cells_cpp_has_no_upstream_library_bug_findings_and_one_disclosure_only_entry`` below for why).

    This union is 14 real ids as of 2026-11-02: the 12 round-2 findings TC-645 wrote entries for,
    plus ``CELLS-TYPESCRIPT-013`` (this card's own, genuinely new in round3) and ``CELLS-GO-012``
    (also genuinely new in round3, ``cells/go``'s ``AddPicture`` silently losing an embedded image
    on Save+Load) - the latter has NO written registry entry anywhere yet, since
    ``config/known_defects/cells__go.yaml`` is outside this card's ``write_paths``. See this
    module's own docstring and this worker's closeout report for the full accounting."""
    cells_platforms = {f"cells/{platform}" for platform in _ALL_PLATFORMS}
    round2_ids = {
        issue["id"]
        for issue in _real_round2_issues()
        if issue.get("pilot") in cells_platforms and issue.get("category") == "upstream-library-bug"
    }
    round3_document = json.loads(ROUND3_ISSUES_PATH.read_text(encoding="utf-8"))
    round3_ids = {
        issue["id"]
        for pilot in round3_document["pilots"]
        if pilot.get("pilot") in cells_platforms
        for issue in pilot["issues"]
        if issue.get("category") == "upstream-library-bug"
    }
    return round2_ids | round3_ids


# --------------------------------------------------------------------------------------------
# each real, committed registry file parses as valid YAML and validates against the real schema
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", _ALL_PLATFORMS)
def test_real_registry_file_parses_as_valid_yaml_and_validates_against_schema(platform: str) -> None:
    document = _load_real_registry_document(platform)
    schema = _load_schema()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"cells/{platform} registry file failed schema validation: {errors}"
    assert document["pilot"] == f"cells_{platform}"


# --------------------------------------------------------------------------------------------
# load_known_defects returns exactly the real number of entries written per pilot
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", _ALL_PLATFORMS)
def test_load_known_defects_returns_the_real_written_count_for_this_pilot(platform: str) -> None:
    defects = load_known_defects("cells", platform)
    assert len(defects) == _EXPECTED_COUNTS[platform], (
        f"cells/{platform}: expected {_EXPECTED_COUNTS[platform]} real written entries, got {len(defects)}"
    )


def test_sixteen_entries_total_across_all_six_cells_pilots() -> None:
    """16 real written entries: 14 as of TC-656 attempt 3 (the 12 round-2 findings TC-645 wrote,
    plus ``CELLS-TYPESCRIPT-013`` and ``CELLS-GO-012``) - equal to the real "findings needing an
    entry" union total (also 14, see
    ``test_every_real_cells_upstream_library_bug_finding_has_a_written_entry`` below) - plus G3/TC-796's
    two ``_ROUND2_GAP_DISCLOSURE_IDS`` entries."""
    total = sum(len(load_known_defects("cells", platform)) for platform in _ALL_PLATFORMS)
    assert total == 16
    assert total == len(_real_cells_upstream_library_bug_ids()) + len(_ROUND2_GAP_DISCLOSURE_IDS)


def test_no_duplicate_ids_across_the_entire_cells_family() -> None:
    all_ids = [defect.id for platform in _ALL_PLATFORMS for defect in load_known_defects("cells", platform)]
    assert len(all_ids) == len(set(all_ids)), "a defect id is repeated across cells-family registry files"


# --------------------------------------------------------------------------------------------
# every written id is one of the real cells-family upstream-library-bug findings (round2 UNION
# round3), and every such real finding has a written entry - proven algorithmically against the
# real audit data itself, not a hand-copied id list
# --------------------------------------------------------------------------------------------


def test_every_written_defect_id_is_a_real_cells_upstream_library_bug_finding() -> None:
    expected_ids = _real_cells_upstream_library_bug_ids()
    written_ids = {
        defect.id for platform in _ALL_PLATFORMS for defect in load_known_defects("cells", platform)
    }
    # G3/TC-796: the explicit round-2-gap disclosure ids are admitted too, but each must itself be
    # a real round-2 ledger id for a cells pilot - never an invented id.
    ledger_ids = {
        issue["id"] for issue in _real_round2_issues() if str(issue.get("pilot", "")).startswith("cells/")
    }
    assert _ROUND2_GAP_DISCLOSURE_IDS <= ledger_ids
    assert written_ids <= expected_ids | _ROUND2_GAP_DISCLOSURE_IDS, (
        f"written ids not found among real cells upstream-library-bug findings (round2 union round3) "
        f"or the explicit round-2-gap disclosure ids: "
        f"{written_ids - expected_ids - _ROUND2_GAP_DISCLOSURE_IDS}"
    )


def test_every_real_cells_upstream_library_bug_finding_has_a_written_entry() -> None:
    """The real union of round2+round3 cells-family upstream-library-bug ids is 14:
    ``CELLS-GO-012`` (cells/go, round3-only, ``AddPicture`` silently loses the embedded image on
    Save+Load) and ``CELLS-TYPESCRIPT-013`` are both now disclosed, closing the gap TC-656 attempt
    2 found and reported rather than worked around (AGENTS.md worker rule 5) when
    ``config/known_defects/cells__go.yaml`` was still outside this card's ``write_paths``. The
    assertion itself was never weakened across either rework attempt - only the registry's own
    real content changed."""
    expected_ids = _real_cells_upstream_library_bug_ids()
    written_ids = {
        defect.id for platform in _ALL_PLATFORMS for defect in load_known_defects("cells", platform)
    }
    assert expected_ids <= written_ids, (
        f"real cells upstream-library-bug findings (round2 union round3) with no written entry: "
        f"{expected_ids - written_ids}"
    )
    # The written set equals the union (14 members, the real count as of 2026-11-02) PLUS exactly
    # the two G3/TC-796 round-2-gap disclosure ids - nothing more, nothing less.
    assert expected_ids | _ROUND2_GAP_DISCLOSURE_IDS == written_ids
    assert len(expected_ids) == 14


# --------------------------------------------------------------------------------------------
# CELLS-CPP-001 exclusion: deliberate, not an oversight - proven three ways
# --------------------------------------------------------------------------------------------


def test_cells_cpp_has_no_upstream_library_bug_findings_and_one_disclosure_only_entry() -> None:
    """``CELLS-CPP-001`` is real round-2 data, but its ``category`` is ``"honesty"`` (a false FAQ
    claim), not ``"upstream-library-bug"`` - confirmed directly against the audit's own data, not
    assumed. ``cells/cpp`` has zero upstream-library-bug findings in EITHER round, so none of the
    entries this module's own card wrote is for it (until TC-857 it had no registry file at all, which
    the loader's own docstring says is a valid, defect-free pilot); its one entry today,
    ``CELLS-CPP-004``, is a disclosure-only addition asserted at the bottom of this test. Round3's own
    ``cells/cpp`` record (``CELLS-CPP-002`` through ``CELLS-CPP-011``) was read fresh and confirmed
    to carry zero ``upstream-library-bug`` category entries either."""
    round2_issues = _real_round2_issues()
    cpp_issue = next(issue for issue in round2_issues if issue["id"] == "CELLS-CPP-001")
    assert cpp_issue["category"] == "honesty"
    assert cpp_issue["category"] != "upstream-library-bug"

    cpp_upstream_bugs_round2 = {
        issue["id"]
        for issue in round2_issues
        if issue.get("pilot") == "cells/cpp" and issue.get("category") == "upstream-library-bug"
    }
    assert cpp_upstream_bugs_round2 == set(), (
        "cells/cpp must have zero real round-2 upstream-library-bug findings"
    )

    round3_document = json.loads(ROUND3_ISSUES_PATH.read_text(encoding="utf-8"))
    cpp_pilot = next(pilot for pilot in round3_document["pilots"] if pilot["pilot"] == "cells/cpp")
    cpp_upstream_bugs_round3 = {
        issue["id"] for issue in cpp_pilot["issues"] if issue.get("category") == "upstream-library-bug"
    }
    assert cpp_upstream_bugs_round3 == set(), (
        "cells/cpp must have zero real round-3 upstream-library-bug findings"
    )

    # TC-857: cells/cpp still has zero upstream-library-bug findings in either round, but it now carries ONE
    # registry file, for ``CELLS-CPP-004`` (round-2 category ``"mcp-content-missing"``: no formula-calculation
    # engine, re-verified 2026-11-23 by a static source read at the pinned commit 9f852d0f). That id is the only
    # entry, and it is a disclosure of a finding the audit did not classify as an upstream-library-bug.
    assert (REGISTRY_DIR / "cells__cpp.yaml").exists()
    assert [defect.id for defect in load_known_defects("cells", "cpp")] == ["CELLS-CPP-004"]


def test_cells_cpp_001_never_appears_among_any_written_entry_id() -> None:
    written_ids = {
        defect.id for platform in _ALL_PLATFORMS for defect in load_known_defects("cells", platform)
    }
    assert "CELLS-CPP-001" not in written_ids


# --------------------------------------------------------------------------------------------
# structural invariants this card's own instructions required for all 12 entries
# --------------------------------------------------------------------------------------------


def test_every_entry_is_status_open_and_scoped_by_package_versions_only() -> None:
    """None of these 12 findings has a confirmed upstream fix, and every one is confirmed against
    a specific published package version (never an ``indexed_commit``-only scope)."""
    for platform in _ALL_PLATFORMS:
        for defect in load_known_defects("cells", platform):
            assert defect.status == "open", f"{defect.id}: expected status 'open', got {defect.status}"
            assert defect.applies_to.package_versions, f"{defect.id}: expected package_versions to be set"
            assert defect.applies_to.indexed_commit is None, (
                f"{defect.id}: expected no indexed_commit scope (version-scoped findings only)"
            )


def test_every_subject_kind_is_method_or_package() -> None:
    """This card used only ``method`` (one specific implicated symbol - 11 of the 12 findings
    name a specific type/function whose behavior is wrong) or ``package`` (a whole-library
    capability-gap claim - CELLS-RUST-011, the only finding about the crate's format support as a
    whole) - never ``class``/``commit``."""
    for platform in _ALL_PLATFORMS:
        for defect in load_known_defects("cells", platform):
            assert defect.subject.kind in ("method", "package"), (
                f"{defect.id}: unexpected subject.kind {defect.subject.kind!r}"
            )


def test_exactly_one_package_scoped_entry_the_rust_capability_gap() -> None:
    package_scoped = [
        defect.id
        for platform in _ALL_PLATFORMS
        for defect in load_known_defects("cells", platform)
        if defect.subject.kind == "package"
    ]
    assert package_scoped == ["CELLS-RUST-011"]


# --------------------------------------------------------------------------------------------
# real end-to-end: the real, committed registry (never monkeypatched) surfaces through a real
# MCP tool for a real pilot scope
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    exact pattern ``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2's own tests) and
    ``tests/indexing/test_known_defects_data_3d.py`` (KD6's own tests) already established. Only
    the symbol CONTENT here is synthetic (this repo has no committed, indexed chunk corpus for
    cells/java to query offline); the known-defects REGISTRY data this store's ``get_symbol`` call
    reads is the real, committed file this card wrote, never monkeypatched."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(
                    repository="aspose-cells-foss/Aspose.Cells-FOSS-for-Java", commit="tc645"
                ),
                evidence_refs=(),
                title="cells/java API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, "gen-tc645")
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc645"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _workbook_symbol_body() -> str:
    lines = [
        "## Workbook",
        "",
        "FQN: org.aspose.cells_foss.Workbook",
        "Kind: class_declaration",
        "",
        "Methods:",
        "  - save(fileName: string) -> void",
    ]
    return "\n".join(lines) + "\n"


def test_get_symbol_end_to_end_surfaces_the_real_written_java_workbook_save_defect() -> None:
    """CELLS-JAVA-001 (``org.aspose.cells_foss.Workbook``, method-scoped, G3/TC-668/OQ-028: the
    real namespace-qualified FQN - the bare ``Workbook`` this test used before never matched any
    real symbol) is loaded from the REAL ``config/known_defects/cells__java.yaml`` this card wrote
    (``load_known_defects`` uses its default, production ``REGISTRY_DIR`` here - nothing is
    monkeypatched) and must surface through a real ``get_symbol`` call for the real ``cells/java``
    scope."""
    store = _InMemoryStore((_workbook_symbol_body(),))
    scope = Scope(family="cells", platform="java")

    result = get_symbol(store, scope, "Workbook.save")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == "org.aspose.cells_foss.Workbook"
    assert len(result.known_defects) == 1
    assert (
        "Workbook.save(fileName) in org.aspose:aspose-cells-foss 26.7.0 ignores the file extension"
        in (result.known_defects[0])
    )


def test_get_product_reference_end_to_end_surfaces_real_written_rust_package_defect() -> None:
    """CELLS-RUST-011, the one real written ``subject.kind: "package"`` entry, must surface in
    ``get_product_reference``'s "limitations" section for the real ``cells/rust`` scope, loaded
    from the REAL ``config/known_defects/cells__rust.yaml`` this card wrote - nothing
    monkeypatched."""
    inputs = ProductReferenceInputs(platform="rust", family="cells", readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert "aspose-cells-foss-rust 26.7.0's SaveFormat enum has exactly one variant (Xlsx)" in result.text
    assert "aspose-cells-foss-rust" in result.text
    assert result.untrusted_upstream_content is False
