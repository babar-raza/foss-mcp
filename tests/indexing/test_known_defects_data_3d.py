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
loads and surfaces correctly, not a synthetic fixture. The one exception is the symbol store used
by the ``get_symbol`` end-to-end test, which is an in-memory chunk store built the same way
``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2's own tests) already established -
there is no committed, indexed, queryable chunk corpus for 3d/java in this repository, so a
synthetic store is the only way to exercise ``get_symbol`` itself end to end; the REGISTRY data it
reads is real and uncontrolled by this test.
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
ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"

_FILES_BY_PLATFORM = {
    "java": "3d__java.yaml",
    "net": "3d__net.yaml",
    "python": "3d__python.yaml",
    "typescript": "3d__typescript.yaml",
}
# The real entry count this card wrote per pilot (4 + 4 + 5 + 1 = 14, matching the 14 real
# round-2 findings selected below).
_EXPECTED_COUNTS = {"java": 4, "net": 4, "python": 5, "typescript": 1}


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_real_registry_document(platform: str) -> dict:
    path = REGISTRY_DIR / _FILES_BY_PLATFORM[platform]
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _real_round2_3d_upstream_library_bug_ids() -> set[str]:
    """The real, algorithmic answer to "which round-2 findings is this card about" - every
    ``docs/round2/issues.json`` entry whose ``pilot`` is one of the four ``3d/*`` pilots and whose
    ``category`` is ``"upstream-library-bug"``. Computed directly from the audit's own committed
    data, never a hand-copied id list, so drift in either file is caught by the tests below."""
    issues = json.loads(ISSUES_PATH.read_text(encoding="utf-8"))
    return {
        issue["id"]
        for issue in issues
        if issue.get("pilot", "").startswith("3d/") and issue.get("category") == "upstream-library-bug"
    }


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


def test_fourteen_entries_total_across_all_four_3d_pilots() -> None:
    total = sum(
        len(load_known_defects("3d", platform)) for platform in ("java", "net", "python", "typescript")
    )
    assert total == 14


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


def test_every_written_defect_id_is_a_real_round2_3d_upstream_library_bug_finding() -> None:
    expected_ids = _real_round2_3d_upstream_library_bug_ids()
    written_ids = {
        defect.id
        for platform in ("java", "net", "python", "typescript")
        for defect in load_known_defects("3d", platform)
    }
    assert written_ids <= expected_ids, (
        f"written ids not found among real round-2 3d upstream-library-bug findings: "
        f"{written_ids - expected_ids}"
    )


def test_every_real_round2_3d_upstream_library_bug_finding_has_a_written_entry() -> None:
    expected_ids = _real_round2_3d_upstream_library_bug_ids()
    written_ids = {
        defect.id
        for platform in ("java", "net", "python", "typescript")
        for defect in load_known_defects("3d", platform)
    }
    assert expected_ids <= written_ids, (
        f"real round-2 3d upstream-library-bug findings with no written entry: {expected_ids - written_ids}"
    )
    # Both sets equal, and both have exactly 14 members - the real count named in this card.
    assert expected_ids == written_ids
    assert len(expected_ids) == 14


# --------------------------------------------------------------------------------------------
# structural invariants this card's own instructions required for all 14 entries
# --------------------------------------------------------------------------------------------


def test_every_entry_is_status_open_and_scoped_by_package_versions_only() -> None:
    """None of these 14 findings has a confirmed upstream fix, and every one is confirmed against
    a specific published package version (never an ``indexed_commit``-only scope) - per this
    card's own instructions."""
    for platform in ("java", "net", "python", "typescript"):
        for defect in load_known_defects("3d", platform):
            assert defect.status == "open", f"{defect.id}: expected status 'open', got {defect.status}"
            assert defect.applies_to.package_versions, f"{defect.id}: expected package_versions to be set"
            assert defect.applies_to.indexed_commit is None, (
                f"{defect.id}: expected no indexed_commit scope (version-scoped findings only)"
            )


def test_every_subject_kind_is_method_or_package() -> None:
    """This card used only ``method`` (one specific implicated symbol) or ``package`` (a
    whole-library behavioral claim) - never ``class``/``commit``, matching the judgment calls
    recorded in this card's closeout report."""
    for platform in ("java", "net", "python", "typescript"):
        for defect in load_known_defects("3d", platform):
            assert defect.subject.kind in ("method", "package"), (
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
