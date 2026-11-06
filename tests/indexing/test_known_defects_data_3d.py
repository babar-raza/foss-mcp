"""KD6 (G3/TC-644, workstream J, REQ-G3-015): the round-2 audit's 14 confirmed 3d-family
upstream-library-bug findings, written as real, schema-conforming ``known_defects`` entries.

KD1-KD5 (TC-636, TC-639, TC-642, TC-641, TC-643) built and wired the disclosure mechanism itself
(schema, loader, ``get_symbol``/``list_members``/``search_symbols``/``get_product_reference``/
``report_index_freshness``) but every registry file was empty - no card had yet written real
defect data. This module proves the four files this card writes
(``config/known_defects/3d__java.yaml``, ``3d__net.yaml``, ``3d__python.yaml``,
``3d__typescript.yaml``) are real, schema-conforming, loader-validated, and that every entry's
``id`` is traceable, algorithmically (not by a hand-copied id list that could silently drift from
either file), back to a real ``category: "upstream-library-bug"`` finding for the ``3d`` family in
``docs/round2/issues.json`` - the round-2 audit's own already-committed evidence.

This module reads the real, committed ``config/known_defects/`` directory - never a
``tmp_path``-monkeypatched one - because the whole point is to prove the REAL data this card wrote
loads and surfaces correctly, not a synthetic fixture. TC-644's own end-to-end test used an
in-memory chunk store built the same way ``tests/mcp/tools/test_known_defects_in_get_symbol.py``
(KD2's own tests) already established, on the stated belief that no committed, indexed, queryable
chunk corpus existed for 3d/java. TC-706 (REPRODUCE FIRST) found that belief is no longer true: a
real, committed ``infra/demo-manifests/3d__java__self_extracted`` corpus exists today (the exact
one ``tests/indexing/test_known_defects_completeness.py``'s own TC-702 fixture already loads via
``tests.conformance.corpus.load_demo_manifests_corpus``), so TC-706's own two new end-to-end tests
below use that REAL corpus, never a synthetic stand-in, for both ``get_symbol`` and
``search_symbols`` - the REGISTRY data both read is real and uncontrolled by this test either way.

**Widened (TC-656 rework attempt 3, 2026-11-02):** the real "which finding needs an entry" source
is now the UNION of ``docs/round2/issues.json`` and ``docs/round3/issues.json`` (a DIFFERENT real
shape - a dict with a ``"pilots"`` key, each pilot an object with its own ``"issues"`` list), both
filtered to the ``3d`` family and ``category: "upstream-library-bug"``. Confirmed directly: the
real union is 15 ids, not 14 - ``3D-JAVA-014`` (``Scene.open()`` on a valid OBJ silently imports 0
meshes/0 geometry) is genuinely new in round3 and is this card's own addition.
``3D-NET-006``/``3D-NET-008`` are real round-2 ids simply absent from round3's own per-pilot issue
table (not reclassified, not retired) - they keep their existing registry entries unchanged; a
union, never a replacement, means an earlier round's own real record is never erased by a later
round's silence.

**Widened again (TC-706, 2026-11-06):** ``3D-JAVA-001`` (round-2, ``docs/round2/issues.json``,
severity P0, category ``"coverage-gap"`` - NOT ``"upstream-library-bug"``) is a real, cited,
committed audit finding this module's own algorithmic id source would otherwise never admit, since
it filters strictly on category. TC-706 independently re-verified the finding against the real,
currently-pinned ``org.aspose:aspose-3d-foss:26.5.0`` Maven Central artifact (confirmed current via
its own real ``maven-metadata.xml``, not assumed from round-2's pin) and found 39 - not round-2's
claimed 37, a transcription discrepancy in round-2's own ``repro`` field, corrected here - rendering-
engine type names that resolve via ``get_symbol``/``search_symbols`` as confident real classes with
zero caveat, yet are genuinely absent from the real published jar (``jar tf`` + ``javap -public``
each confirm "class not found"); all 39 ARE present in this project's own indexed
``infra/demo-manifests`` corpus for 3d/java, which is exactly why the phantom resolution happens.
This module's own ``_real_3d_upstream_library_bug_ids`` function is widened, the same deliberate,
additive, explicit way ``tests/indexing/test_known_defects_completeness.py``'s own TC-656 rework
widened its sibling function from round2-only to round2-union-round3: a small, hand-reviewed,
comment-documented ``_ADDITIONAL_DISCLOSABLE_3D_IDS`` frozenset, seeded with exactly
``{"3D-JAVA-001"}``, unioned into the returned id set. This never weakens either exact-equality
assertion below - every id in that set still traces to one real, cited, committed audit record
(never invented) - it only widens what the lock admits. The real new total is 16 entries (15 + this
card's own ``3D-JAVA-001``), all in the ``java`` pilot file (5 -> 6)."""

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
from foss_mcp.mcp.tools.get_symbol import ResolvedSignature, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.search_symbols import search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.conformance.corpus import load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_DIR = REPO_ROOT / "config" / "known_defects"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"
ROUND3_ISSUES_PATH = REPO_ROOT / "docs" / "round3" / "issues.json"

_FILES_BY_PLATFORM = {
    "java": "3d__java.yaml",
    "net": "3d__net.yaml",
    "python": "3d__python.yaml",
    "typescript": "3d__typescript.yaml",
}
# The real entry count actually WRITTEN per pilot (6 + 4 + 5 + 1 = 16): the 14 real round-2
# findings TC-644 wrote, plus TC-656's own 3D-JAVA-014 (java 4 -> 5), plus this card's (TC-706)
# own 3D-JAVA-001 (java 5 -> 6).
_EXPECTED_COUNTS = {"java": 6, "net": 4, "python": 5, "typescript": 1}

# TC-706 (G3, workstream J, REQ-G3-015): an explicit, hand-reviewed, additive allowlist of real,
# cited, committed audit findings that this module's own category-filtered id source would
# otherwise never admit, because their recorded ``category`` is not ``"upstream-library-bug"`` -
# the same deliberate widening shape ``test_known_defects_completeness.py``'s own TC-656 rework
# already established at the corpus-wide level. Each id here still traces to one real, cited,
# committed audit record; this is never a loosened filter, only an explicit addition to what the
# lock admits.
#
# - ``3D-JAVA-001`` (round-2, ``docs/round2/issues.json``, severity P0, category
#   ``"coverage-gap"``): 39 phantom rendering-engine type names (independently re-verified by
#   TC-706 against the real, currently-pinned ``org.aspose:aspose-3d-foss:26.5.0`` jar) resolve
#   via ``get_symbol``/``search_symbols`` as confident real classes with zero caveat - a genuine
#   index-truth/disclosure gap, just not categorized ``"upstream-library-bug"`` in the original
#   audit.
_ADDITIONAL_DISCLOSABLE_3D_IDS: frozenset[str] = frozenset({"3D-JAVA-001"})


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_real_registry_document(platform: str) -> dict:
    path = REGISTRY_DIR / _FILES_BY_PLATFORM[platform]
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _real_3d_upstream_library_bug_ids() -> set[str]:
    """The real, algorithmic answer to "which findings, from EITHER committed audit round, is
    this card about" - the union of every ``docs/round2/issues.json`` entry and every
    ``docs/round3/issues.json`` entry whose ``pilot`` is one of the four ``3d/*`` pilots and whose
    ``category`` is ``"upstream-library-bug"``. Computed directly from both audits' own committed
    data, never a hand-copied id list, so drift in any of the three files (round2, round3, the
    registry) is caught by the tests below."""
    round2_issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    round2_ids = {
        issue["id"]
        for issue in round2_issues
        if issue.get("pilot", "").startswith("3d/") and issue.get("category") == "upstream-library-bug"
    }
    round3_document = json.loads(ROUND3_ISSUES_PATH.read_text(encoding="utf-8"))
    round3_ids = {
        issue["id"]
        for pilot in round3_document["pilots"]
        if pilot.get("pilot", "").startswith("3d/")
        for issue in pilot["issues"]
        if issue.get("category") == "upstream-library-bug"
    }
    return round2_ids | round3_ids | _ADDITIONAL_DISCLOSABLE_3D_IDS


# --------------------------------------------------------------------------------------------
# each real, committed registry file parses as valid YAML and validates against the real schema
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["java", "net", "python", "typescript"])
def test_real_registry_file_parses_as_valid_yaml_and_validates_against_schema(platform: str) -> None:
    document = _load_real_registry_document(platform)
    schema = _load_schema()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"3d/{platform} registry file failed schema validation: {errors}"
    assert document["pilot"] == f"3d_{platform}"


# --------------------------------------------------------------------------------------------
# load_known_defects returns exactly the real number of entries written per pilot
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["java", "net", "python", "typescript"])
def test_load_known_defects_returns_the_real_written_count_for_this_pilot(platform: str) -> None:
    defects = load_known_defects("3d", platform)
    assert len(defects) == _EXPECTED_COUNTS[platform], (
        f"3d/{platform}: expected {_EXPECTED_COUNTS[platform]} real written entries, got {len(defects)}"
    )


def test_sixteen_entries_total_across_all_four_3d_pilots() -> None:
    total = sum(
        len(load_known_defects("3d", platform)) for platform in ("java", "net", "python", "typescript")
    )
    assert total == 16


def test_no_duplicate_ids_across_the_entire_3d_family() -> None:
    all_ids = [
        defect.id
        for platform in ("java", "net", "python", "typescript")
        for defect in load_known_defects("3d", platform)
    ]
    assert len(all_ids) == len(set(all_ids)), "a defect id is repeated across 3d-family registry files"


# --------------------------------------------------------------------------------------------
# every written id is one of the real round-2 3d-family upstream-library-bug findings, and every
# such real finding has a written entry - proven algorithmically against docs/round2/issues.json
# itself, not a hand-copied id list
# --------------------------------------------------------------------------------------------


def test_every_written_defect_id_is_a_real_3d_upstream_library_bug_finding() -> None:
    expected_ids = _real_3d_upstream_library_bug_ids()
    written_ids = {
        defect.id
        for platform in ("java", "net", "python", "typescript")
        for defect in load_known_defects("3d", platform)
    }
    assert written_ids <= expected_ids, (
        f"written ids not found among real 3d upstream-library-bug findings (round2 union round3): "
        f"{written_ids - expected_ids}"
    )


def test_every_real_3d_upstream_library_bug_finding_has_a_written_entry() -> None:
    expected_ids = _real_3d_upstream_library_bug_ids()
    written_ids = {
        defect.id
        for platform in ("java", "net", "python", "typescript")
        for defect in load_known_defects("3d", platform)
    }
    assert expected_ids <= written_ids, (
        f"real 3d upstream-library-bug findings (round2 union round3) with no written entry: "
        f"{expected_ids - written_ids}"
    )
    # Both sets equal, and both have exactly 16 members - the real union count as of 2026-11-06
    # (15 as of 2026-11-02, plus this card's own additive 3D-JAVA-001).
    assert expected_ids == written_ids
    assert len(expected_ids) == 16


# --------------------------------------------------------------------------------------------
# structural invariants this card's own instructions required for all 15 entries (the original
# 14 round-2 findings plus TC-656's own 3D-JAVA-014)
# --------------------------------------------------------------------------------------------


def test_every_entry_is_status_open_and_scoped_by_package_versions_only() -> None:
    """None of these 15 findings has a confirmed upstream fix, and every one is confirmed against
    a specific published package version (never an ``indexed_commit``-only scope) - per this
    card's own instructions."""
    for platform in ("java", "net", "python", "typescript"):
        for defect in load_known_defects("3d", platform):
            assert defect.status == "open", f"{defect.id}: expected status 'open', got {defect.status}"
            assert defect.applies_to.package_versions, f"{defect.id}: expected package_versions to be set"
            assert defect.applies_to.indexed_commit is None, (
                f"{defect.id}: expected no indexed_commit scope (version-scoped findings only)"
            )


def test_every_subject_kind_is_method_or_package_or_class() -> None:
    """TC-644 used only ``method`` (one specific implicated symbol) or ``package`` (a
    whole-library behavioral claim) - never ``class``/``commit``, matching the judgment calls
    recorded in that card's closeout report. TC-706 widened this to also allow ``class``: its own
    ``3D-JAVA-001`` entry names 39 whole phantom TYPES, not one method and not a whole-library
    claim, so ``class`` is the correct ``subject.kind`` for it - never ``commit``, still. Every one
    of the original 15 entries must still pass this widened assertion unchanged."""
    for platform in ("java", "net", "python", "typescript"):
        for defect in load_known_defects("3d", platform):
            assert defect.subject.kind in ("method", "package", "class"), (
                f"{defect.id}: unexpected subject.kind {defect.subject.kind!r}"
            )


# --------------------------------------------------------------------------------------------
# real end-to-end: the real, committed registry (never monkeypatched) surfaces through a real
# MCP tool for the real pilot scope
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    exact pattern ``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2's own tests)
    already established. Only the symbol CONTENT here is synthetic (this repo has no committed,
    indexed chunk corpus for 3d/java to query offline); the known-defects REGISTRY data this
    store's ``get_symbol`` call reads is the real, committed file this card wrote, never
    monkeypatched."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="aspose-3d-foss/Aspose.3D-FOSS-for-Java", commit="tc644"),
                evidence_refs=(),
                title="3d/java API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, "gen-tc644")
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc644"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _mesh_symbol_body() -> str:
    lines = [
        "## com.aspose.threed.Mesh",
        "",
        "FQN: com.aspose.threed.Mesh",
        "Kind: class_declaration",
        "",
        "Methods:",
        "  - triangulate() -> Mesh",
    ]
    return "\n".join(lines) + "\n"


def test_get_symbol_end_to_end_surfaces_the_real_written_java_triangulate_defect() -> None:
    """3D-JAVA-006 (``com.aspose.threed.Mesh.triangulate``, method-scoped) is loaded from the
    REAL ``config/known_defects/3d__java.yaml`` this card wrote (``load_known_defects`` uses its
    default, production ``REGISTRY_DIR`` here - nothing is monkeypatched) and must surface through
    a real ``get_symbol`` call for the real ``3d/java`` scope."""
    store = _InMemoryStore((_mesh_symbol_body(),))
    scope = Scope(family="3d", platform="java")

    result = get_symbol(store, scope, "com.aspose.threed.Mesh.triangulate")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == "com.aspose.threed.Mesh"
    assert len(result.known_defects) == 1
    assert "Mesh.triangulate() is a no-op in aspose-3d-foss 26.5.0" in result.known_defects[0]


def test_get_product_reference_end_to_end_surfaces_real_written_python_package_defects() -> None:
    """All five 3d/python entries this card wrote are ``subject.kind: "package"``, so every one
    must surface in ``get_product_reference``'s "limitations" section for the real ``3d/python``
    scope, loaded from the REAL ``config/known_defects/3d__python.yaml`` this card wrote - nothing
    monkeypatched."""
    inputs = ProductReferenceInputs(platform="python", family="3d", readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert "Scene.save() to OBJ in aspose-3d-foss 26.1.0 always raises TypeError" in result.text
    assert "aspose-3d-foss" in result.text
    assert result.untrusted_upstream_content is False


# --------------------------------------------------------------------------------------------
# TC-706 (3D-JAVA-001): real end-to-end coverage against the REAL, committed
# ``infra/demo-manifests`` corpus (not a synthetic/in-memory stand-in - TC-706's own
# REPRODUCE-FIRST investigation found this corpus genuinely exists for 3d/java today, unlike when
# TC-644 first wrote this module). Both ``get_symbol`` and ``search_symbols`` must surface the
# real, committed ``3D-JAVA-001`` entry for a confirmed-phantom name.
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _real_3d_java_corpus(tmp_path_factory: pytest.TempPathFactory):
    """The real, committed ``infra/demo-manifests`` corpus, loaded through a temp-directory copy -
    the exact ``load_demo_manifests_corpus`` pattern ``test_known_defects_completeness.py``'s own
    TC-702 fixture already established. Nothing in the real repository is ever written to."""
    root = tmp_path_factory.mktemp("tc706-known-defects-data-3d-corpus")
    return load_demo_manifests_corpus(root)


def test_get_symbol_end_to_end_surfaces_the_real_written_3d_java_001_defect(_real_3d_java_corpus) -> None:
    """``com.aspose.threed.CShape`` is one of the 39 names TC-706 independently re-confirmed absent
    from the real published jar; it IS present in the real indexed corpus (that is the whole point
    of the disclosure), so ``get_symbol`` against the real, committed ``3d__java.yaml`` this card
    wrote must surface ``3D-JAVA-001``'s real disclosure text for it."""
    pilot = _real_3d_java_corpus.pilots["3d__java"]

    result = get_symbol(_real_3d_java_corpus.store, pilot.scope, "com.aspose.threed.CShape")

    assert isinstance(result, SymbolSignature)
    assert result.found is True
    assert len(result.known_defects) == 1
    assert "absent from the published org.aspose:aspose-3d-foss 26.5.0 jar" in result.known_defects[0]


def test_search_symbols_end_to_end_surfaces_the_real_written_3d_java_001_defect(
    _real_3d_java_corpus,
) -> None:
    """Same real corpus, same real registry entry, through ``search_symbols`` instead of
    ``get_symbol`` - proving the disclosure surfaces through BOTH tools, per this card's own
    instructions."""
    pilot = _real_3d_java_corpus.pilots["3d__java"]

    result = search_symbols(_real_3d_java_corpus.store, pilot.scope, "com.aspose.threed.CShape")

    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    assert len(result) == 1
    assert len(result[0].known_defects) == 1
    assert "absent from the published org.aspose:aspose-3d-foss 26.5.0 jar" in result[0].known_defects[0]
