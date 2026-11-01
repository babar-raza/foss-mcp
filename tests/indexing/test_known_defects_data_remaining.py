"""KD9 (G3/TC-647, workstream J, REQ-G3-015): the round-2 audit's 15 remaining confirmed
upstream-library-bug findings across the eight remaining families, written as real,
schema-conforming ``known_defects`` entries - closing KD6-KD9's full coverage of the audit's own
47-issue ``upstream-library-bug`` category.

KD6/KD7/KD8 (TC-644/TC-645/TC-646) wrote the 3d, cells, and pdf/slides families' own data. This
module proves the eight files this card writes (``config/known_defects/barcode__python.yaml``,
``email__cpp.yaml``, ``email__python.yaml``, ``imaging__net.yaml``, ``note__python.yaml``,
``page__python.yaml``, ``tex__python.yaml``, ``words__python.yaml``) are real,
schema-conforming, loader-validated, and that every entry's ``id`` is traceable, algorithmically
(not a hand-copied id list that could silently drift from either file), back to a real
``category: "upstream-library-bug"`` finding in ``docs/round2/issues.json`` for one of these eight
families - the round-2 audit's own already-committed evidence.

``IMAGING-NET-004`` is the round-2 audit's own second headline example (the schema's own ``id``
docstring example): the exact commit ``get_product_reference(install)`` tells users to build
(``c5e77bdc7b4a5c19190b6dfae5ae9d420b5886fa``) has upstream-acknowledged hang/CPU/memory-blowup
bugs fixed 5 commits later. Unlike every other entry in this card, it is ``indexed_commit``-scoped
(``applies_to.indexed_commit``), never ``package_versions``-scoped, since it is a claim about one
specific commit, not a published package version range - the schema's own ``fixed_upstream``
non-contradiction rule makes exactly this distinction. This module proves that scoping choice
structurally, proves ``defect_is_stale`` is never even asked about it (the production
``report_index_freshness.known_defect_lines`` ``continue``s past commit-scoped defects before
reaching the ``defect_is_stale`` branch at all), and proves a REAL end-to-end
``report_index_freshness`` call surfaces it only when the indexed commit matches.

This module reads the real, committed ``config/known_defects/`` directory - never a
``tmp_path``-monkeypatched one - because the whole point is to prove the REAL data this card wrote
loads and surfaces correctly, not a synthetic fixture. The one exception is the generation store
used by the ``report_index_freshness`` end-to-end test, which is a real ``GenerationManifestStore``
built the same way ``tests/mcp/tools/test_freshness_provenance.py`` (KD5's own tests) already
established for its own ``pdf/net`` scope; here it is built for the real ``imaging/net`` scope
instead, since ``test_freshness_provenance._publish`` is hardcoded to the ``pdf/net`` scope key and
cannot be reused across families. The REGISTRY data that end-to-end test reads is real and
uncontrolled by this test; only the symbol chunk content and generation plumbing are synthetic
(there is no committed, indexed, queryable chunk corpus for imaging/net in this repository).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.known_defects import defect_is_stale, load_known_defects
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.report_index_freshness import (
    known_defect_lines as freshness_known_defect_lines,
)
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_DIR = REPO_ROOT / "config" / "known_defects"
ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"

# The eight (family, platform) pilots this card writes, and the real registry filename for each -
# confirmed against known_defects.registry_path_for's own ``<family>__<platform>.yaml`` pattern.
_PILOTS: tuple[tuple[str, str], ...] = (
    ("barcode", "python"),
    ("email", "cpp"),
    ("email", "python"),
    ("imaging", "net"),
    ("note", "python"),
    ("page", "python"),
    ("tex", "python"),
    ("words", "python"),
)

# The real entry count this card wrote per pilot (1+3+1+5+2+1+1+1 = 15, matching the 15 real
# round-2 findings named in this card).
_EXPECTED_COUNTS: dict[tuple[str, str], int] = {
    ("barcode", "python"): 1,
    ("email", "cpp"): 3,
    ("email", "python"): 1,
    ("imaging", "net"): 5,
    ("note", "python"): 2,
    ("page", "python"): 1,
    ("tex", "python"): 1,
    ("words", "python"): 1,
}

IMAGING_NET_COMMIT = "c5e77bdc7b4a5c19190b6dfae5ae9d420b5886fa"


def _registry_path(family: str, platform: str) -> Path:
    return REGISTRY_DIR / f"{family}__{platform}.yaml"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_real_registry_document(family: str, platform: str) -> dict:
    return yaml.safe_load(_registry_path(family, platform).read_text(encoding="utf-8"))


def _real_round2_upstream_library_bug_ids() -> set[str]:
    """The real, algorithmic answer to "which round-2 findings is this card about" - every
    ``docs/round2/issues.json`` entry whose ``pilot`` starts with one of this card's eight family
    prefixes and whose ``category`` is ``"upstream-library-bug"``. Computed directly from the
    audit's own committed data, never a hand-copied id list, so drift in either file is caught by
    the tests below."""
    issues = json.loads(ISSUES_PATH.read_text(encoding="utf-8"))
    family_prefixes = tuple(f"{family}/" for family, _platform in _PILOTS)
    return {
        issue["id"]
        for issue in issues
        if issue.get("category") == "upstream-library-bug"
        and issue.get("pilot", "").startswith(family_prefixes)
    }


def _all_written_defects() -> list:
    return [defect for family, platform in _PILOTS for defect in load_known_defects(family, platform)]


# --------------------------------------------------------------------------------------------
# each real, committed registry file parses as valid YAML and validates against the real schema
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("family,platform", _PILOTS)
def test_real_registry_file_parses_as_valid_yaml_and_validates_against_schema(
    family: str, platform: str
) -> None:
    document = _load_real_registry_document(family, platform)
    schema = _load_schema()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"{family}/{platform} registry file failed schema validation: {errors}"
    assert document["pilot"] == f"{family}_{platform}"


# --------------------------------------------------------------------------------------------
# load_known_defects returns exactly the real number of entries written per pilot
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("family,platform", _PILOTS)
def test_load_known_defects_returns_the_real_written_count_for_this_pilot(family: str, platform: str) -> None:
    defects = load_known_defects(family, platform)
    expected = _EXPECTED_COUNTS[(family, platform)]
    assert len(defects) == expected, (
        f"{family}/{platform}: expected {expected} real written entries, got {len(defects)}"
    )


def test_fifteen_entries_total_across_all_eight_pilots() -> None:
    assert len(_all_written_defects()) == 15


def test_no_duplicate_ids_across_any_of_the_eight_registry_files() -> None:
    all_ids = [defect.id for defect in _all_written_defects()]
    assert len(all_ids) == len(set(all_ids)), "a defect id is repeated across these registry files"


# --------------------------------------------------------------------------------------------
# every written id is one of the real round-2 upstream-library-bug findings for these eight
# families, and every such real finding has a written entry - proven algorithmically against
# docs/round2/issues.json itself, not a hand-copied id list
# --------------------------------------------------------------------------------------------


def test_every_written_defect_id_is_a_real_round2_upstream_library_bug_finding() -> None:
    expected_ids = _real_round2_upstream_library_bug_ids()
    written_ids = {defect.id for defect in _all_written_defects()}
    assert written_ids <= expected_ids, (
        f"written ids not found among real round-2 upstream-library-bug findings: "
        f"{written_ids - expected_ids}"
    )


def test_every_real_round2_upstream_library_bug_finding_has_a_written_entry() -> None:
    expected_ids = _real_round2_upstream_library_bug_ids()
    written_ids = {defect.id for defect in _all_written_defects()}
    assert expected_ids <= written_ids, (
        f"real round-2 upstream-library-bug findings with no written entry: {expected_ids - written_ids}"
    )
    # Both sets equal, and both have exactly 15 members - the real count named in this card.
    assert expected_ids == written_ids
    assert len(expected_ids) == 15


def test_every_written_defect_id_is_one_of_the_fifteen_named_ids() -> None:
    """The exact 15 ids this card's own instructions named - a second, independent cross-check
    against a hand-written (not algorithmically derived) list, so a mistake in the algorithmic
    filter above cannot silently hide a wrong id."""
    named_ids = {
        "BARCODE-PY-006",
        "EMAIL-CPP-001",
        "EMAIL-CPP-002",
        "EMAIL-CPP-003",
        "EMAIL-PY-006",
        "IMAGING-NET-001",
        "IMAGING-NET-002",
        "IMAGING-NET-003",
        "IMAGING-NET-004",
        "IMAGING-NET-008",
        "NOTE-PY-002",
        "NOTE-PY-008",
        "PAGE-PYTHON-006",
        "TEX-PYTHON-001",
        "WORDS-PY-008",
    }
    written_ids = {defect.id for defect in _all_written_defects()}
    assert written_ids == named_ids


# --------------------------------------------------------------------------------------------
# IMAGING-NET-004 is specifically indexed_commit-scoped with the real commit sha from the issue's
# own text - never package_versions-scoped, unlike every other entry in this card
# --------------------------------------------------------------------------------------------


def test_imaging_net_004_is_indexed_commit_scoped_with_the_real_commit_sha() -> None:
    defects = {defect.id: defect for defect in load_known_defects("imaging", "net")}
    defect = defects["IMAGING-NET-004"]
    assert defect.applies_to.indexed_commit == IMAGING_NET_COMMIT
    assert defect.applies_to.package_versions is None
    assert defect.subject.kind == "commit"
    assert defect.subject.ref == IMAGING_NET_COMMIT
    assert defect.status == "open"


def test_every_other_imaging_net_entry_is_package_versions_scoped_not_commit_scoped() -> None:
    """Every imaging/net entry OTHER than IMAGING-NET-004 is ``package_versions``-scoped, per this
    card's own instruction that IMAGING-NET-004 alone is the ``indexed_commit``-scoped exception."""
    for defect in load_known_defects("imaging", "net"):
        if defect.id == "IMAGING-NET-004":
            continue
        assert defect.applies_to.package_versions, f"{defect.id}: expected package_versions to be set"


# --------------------------------------------------------------------------------------------
# defect_is_stale behaves correctly for IMAGING-NET-004 if given a package-version comparison it
# does not apply to - it should not even be asked, since this defect has no package_versions
# --------------------------------------------------------------------------------------------


def test_defect_is_stale_is_never_asked_about_a_commit_only_scoped_defect_in_production() -> None:
    """``report_index_freshness.known_defect_lines`` ``continue``s past a commit-scoped defect
    (``commit_scope is not None``) before it ever reaches the ``defect_is_stale`` branch - proven
    here by giving a ``registry_served_version`` that WOULD make this defect look stale if it were
    (wrongly) treated as package-version-scoped, and confirming the disclosure line carries none of
    ``_render_stale_known_defect``'s "last verified broken on" wording when the commit matches."""
    defects = load_known_defects("imaging", "net")
    defect = next(d for d in defects if d.id == "IMAGING-NET-004")
    assert defect.applies_to.package_versions is None

    lines = freshness_known_defect_lines([defect], IMAGING_NET_COMMIT, (9999, 0, 0))
    assert len(lines) == 1
    assert "last verified broken on" not in lines[0]
    assert "not re-verified on" not in lines[0]


def test_defect_is_stale_would_raise_nothing_useful_if_misapplied_but_is_structurally_unreachable() -> None:
    """Calling ``defect_is_stale`` directly on IMAGING-NET-004 is possible (the function itself has
    no commit-vs-package guard - that guard lives in the CALLER, ``known_defect_lines``), which is
    exactly why the production wiring must never ask: this defect's own
    ``evidence.verified_on.version`` is the commit sha, not a real package version, so any answer
    ``defect_is_stale`` gives for it is meaningless. This test documents that boundary rather than
    asserting a specific nonsensical return value."""
    defects = load_known_defects("imaging", "net")
    defect = next(d for d in defects if d.id == "IMAGING-NET-004")
    # defect_is_stale parses a leading digit run out of evidence.verified_on.version; it does not
    # raise, but the comparison is meaningless for a commit-scoped defect - which is exactly why
    # the production caller never performs it (proven above).
    result = defect_is_stale(defect, (999,))
    assert isinstance(result, bool)


# --------------------------------------------------------------------------------------------
# real end-to-end: the real, committed imaging/net registry (never monkeypatched) surfaces
# IMAGING-NET-004 through a real report_index_freshness call only when the indexed commit matches
# --------------------------------------------------------------------------------------------


def _symbol_chunk_text(fqn: str, commit: str) -> str:
    return (
        "\n".join(
            [
                f"## {fqn}",
                "",
                f"FQN: {fqn}",
                "Kind: class_declaration",
                "",
                f"Source-Commit: {commit}",
            ]
        )
        + "\n"
    )


def _publish_imaging_net(root: Path, commit: str) -> GenerationManifestStore:
    """Publish one real ``imaging/net`` generation whose single symbol chunk records *commit* as
    its ``Source-Commit:`` - the exact mechanism ``test_freshness_provenance._publish`` already
    established, rebuilt here for the real ``imaging/net`` scope key (``_publish`` itself is
    hardcoded to ``pdf::net::self_extracted`` and cannot be reused across families)."""
    store = GenerationManifestStore(root)
    text = _symbol_chunk_text("Aspose.Imaging.Foss.ImageProbe", commit)
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="aspose-imaging-foss/Aspose.Imaging-FOSS-for-.NET", commit=commit),
        evidence_refs=(),
        title="imaging/net synthetic",
        body=f"## section 0\n\n{text}",
    )
    chunks = chunk_document(doc)
    lease = store.acquire_lease("imaging::net::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family="imaging",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    return store


def test_imaging_net_004_surfaces_through_report_index_freshness_when_commit_matches(
    tmp_path: Path,
) -> None:
    """A real ``report_index_freshness`` call for the real ``imaging/net`` scope, reading the REAL
    committed ``config/known_defects/imaging__net.yaml`` this card wrote (nothing monkeypatched),
    surfaces IMAGING-NET-004 when the generation's own indexed commit equals the defect's
    ``applies_to.indexed_commit``."""
    store = _publish_imaging_net(tmp_path / "gen", IMAGING_NET_COMMIT)
    scope = Scope(family="imaging", platform="net")

    report = report_index_freshness(store, scope, "self_extracted", IMAGING_NET_COMMIT)

    assert report.indexed_source_commit == IMAGING_NET_COMMIT
    assert any("hang" in line.lower() or "memory" in line.lower() for line in report.known_defects), (
        report.known_defects
    )
    # IMAGING-NET-004's own summary text (never its bare id, which _render_known_defect never
    # emits) must be among the surfaced lines since the indexed commit matches exactly.
    assert any("5131c44a" in line for line in report.known_defects), report.known_defects


def test_imaging_net_004_does_not_surface_through_report_index_freshness_when_commit_differs(
    tmp_path: Path,
) -> None:
    """The same real registry, the same real tool, but a DIFFERENT indexed commit: IMAGING-NET-004
    must not surface, proving the commit-scoping is a real equality check, not a tautology."""
    other_commit = "d947d11cc6b282443231c2025ee43bf974a335dd"
    store = _publish_imaging_net(tmp_path / "gen", other_commit)
    scope = Scope(family="imaging", platform="net")

    report = report_index_freshness(store, scope, "self_extracted", other_commit)

    assert report.indexed_source_commit == other_commit
    assert not any("5131c44a" in line for line in report.known_defects), report.known_defects
