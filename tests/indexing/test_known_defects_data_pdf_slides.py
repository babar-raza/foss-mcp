"""KD8 (G3/TC-646, workstream J, REQ-G3-015): the round-2 audit's 6 confirmed pdf/slides-family
upstream-library-bug findings, written as real, schema-conforming ``known_defects`` entries.

Same pattern as KD6/TC-644 (3d family) and KD7/TC-645 (cells family, if integrated) - every entry
this card writes to ``config/known_defects/{pdf,slides}__*.yaml`` is proven, algorithmically (not
by a hand-copied id list that could silently drift from the audit's own data), to be a real
``category: "upstream-library-bug"`` finding for the ``pdf`` or ``slides`` families in
``docs/round2/issues.json`` - the round-2 audit's own already-committed evidence. Across all ten
pdf/slides platforms in that file (pdf: cpp/go/java/net/python/typescript; slides:
cpp/java/net/python), exactly these 6 findings carry that category - this card writes one entry
per finding, for the five pilots that have any (``pdf/cpp``, ``pdf/java`` x2, ``slides/cpp``,
``slides/net``, ``slides/python``); ``pdf/go``, ``pdf/net``, ``pdf/python``, ``pdf/typescript``,
and ``slides/java`` have none and correctly have no registry file at all.

This module reads the real, committed ``config/known_defects/`` directory - never a
``tmp_path``-monkeypatched one - because the whole point is to prove the REAL data this card wrote
loads and surfaces correctly, not a synthetic fixture. The one exception is the symbol store used
by the ``get_symbol`` end-to-end test, which is an in-memory chunk store built the same way
``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2's own tests) already established -
there is no committed, indexed, queryable chunk corpus for pdf/cpp in this repository, so a
synthetic store is the only way to exercise ``get_symbol`` itself end to end; the REGISTRY data it
reads is real and uncontrolled by this test.

``PDF-CPP-001`` (the round-2 audit's own headline AES-256 finding) is written with
``subject.kind: "method"`` (``Aspose::Pdf::Document`` - a real, specific encryption method's
output, confirmed as a real public C++ FQN via the same pilot's own ``PDF-CPP-003``/``PDF-CPP-005``
entries in ``docs/round2/issues.json``, which independently confirm the ``Aspose::Pdf`` namespace),
never ``"package"`` - this is about ``Document::Encrypt`` specifically, not a whole-library claim,
matching this card's own instructions and KD6/TC-644's established judgment-call pattern (e.g.
``3D-JAVA-006``'s ``Mesh.triangulate``). Because it is ``method``-scoped, it surfaces through
``get_symbol`` (KD2, G3/TC-639) - ``get_product_reference``'s own ``_LIBRARY_DEFECT_SUBJECT_KINDS``
(G3/TC-641) deliberately excludes ``method``/``class`` kinds from the "limitations" merge point,
restricting it to ``package``/``commit`` - so the required end-to-end proof below uses
``get_symbol``, not ``get_product_reference``, for this entry.
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
from foss_mcp.mcp.tools.get_symbol import ResolvedSignature, get_symbol
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_DIR = REPO_ROOT / "config" / "known_defects"
ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"

_PILOTS = (("pdf", "cpp"), ("pdf", "java"), ("slides", "cpp"), ("slides", "net"), ("slides", "python"))
_FILES_BY_PILOT = {
    ("pdf", "cpp"): "pdf__cpp.yaml",
    ("pdf", "java"): "pdf__java.yaml",
    ("slides", "cpp"): "slides__cpp.yaml",
    ("slides", "net"): "slides__net.yaml",
    ("slides", "python"): "slides__python.yaml",
}
# The real entry count this card wrote per pilot (1 + 2 + 1 + 1 + 1 = 6, matching the 6 real
# round-2 findings selected below).
_EXPECTED_COUNTS = {
    ("pdf", "cpp"): 1,
    ("pdf", "java"): 2,
    ("slides", "cpp"): 1,
    ("slides", "net"): 1,
    ("slides", "python"): 1,
}


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_real_registry_document(family: str, platform: str) -> dict:
    path = REGISTRY_DIR / _FILES_BY_PILOT[(family, platform)]
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _real_round2_pdf_slides_upstream_library_bug_ids() -> set[str]:
    """The real, algorithmic answer to "which round-2 findings is this card about" - every
    ``docs/round2/issues.json`` entry whose ``pilot`` is a ``pdf/*`` or ``slides/*`` pilot and whose
    ``category`` is ``"upstream-library-bug"``. Computed directly from the audit's own committed
    data, never a hand-copied id list, so drift in either file is caught by the tests below."""
    issues = json.loads(ISSUES_PATH.read_text(encoding="utf-8"))
    return {
        issue["id"]
        for issue in issues
        if (issue.get("pilot", "").startswith("pdf/") or issue.get("pilot", "").startswith("slides/"))
        and issue.get("category") == "upstream-library-bug"
    }


def _all_written_defects():
    for family, platform in _PILOTS:
        yield from load_known_defects(family, platform)


# --------------------------------------------------------------------------------------------
# each real, committed registry file parses as valid YAML and validates against the real schema
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("family", "platform"), _PILOTS)
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


@pytest.mark.parametrize(("family", "platform"), _PILOTS)
def test_load_known_defects_returns_the_real_written_count_for_this_pilot(family: str, platform: str) -> None:
    defects = load_known_defects(family, platform)
    assert len(defects) == _EXPECTED_COUNTS[(family, platform)], (
        f"{family}/{platform}: expected {_EXPECTED_COUNTS[(family, platform)]} real written "
        f"entries, got {len(defects)}"
    )


def test_six_entries_total_across_all_five_pdf_slides_pilots() -> None:
    total = sum(len(load_known_defects(family, platform)) for family, platform in _PILOTS)
    assert total == 6


def test_no_duplicate_ids_across_the_entire_pdf_slides_scope() -> None:
    all_ids = [defect.id for defect in _all_written_defects()]
    assert len(all_ids) == len(set(all_ids)), "a defect id is repeated across pdf/slides registry files"


# --------------------------------------------------------------------------------------------
# every written id is one of the real round-2 pdf/slides-family upstream-library-bug findings, and
# every such real finding has a written entry - proven algorithmically against
# docs/round2/issues.json itself, not a hand-copied id list
# --------------------------------------------------------------------------------------------


def test_every_written_defect_id_is_a_real_round2_pdf_slides_upstream_library_bug_finding() -> None:
    expected_ids = _real_round2_pdf_slides_upstream_library_bug_ids()
    written_ids = {defect.id for defect in _all_written_defects()}
    assert written_ids <= expected_ids, (
        f"written ids not found among real round-2 pdf/slides upstream-library-bug findings: "
        f"{written_ids - expected_ids}"
    )


def test_every_real_round2_pdf_slides_upstream_library_bug_finding_has_a_written_entry() -> None:
    expected_ids = _real_round2_pdf_slides_upstream_library_bug_ids()
    written_ids = {defect.id for defect in _all_written_defects()}
    assert expected_ids <= written_ids, (
        f"real round-2 pdf/slides upstream-library-bug findings with no written entry: "
        f"{expected_ids - written_ids}"
    )
    # Both sets equal, and both have exactly 6 members - the real count named in this card.
    assert expected_ids == written_ids
    assert len(expected_ids) == 6


# --------------------------------------------------------------------------------------------
# structural invariants this card's own instructions required for all 6 entries
# --------------------------------------------------------------------------------------------


def test_every_entry_is_status_open() -> None:
    """None of these 6 findings has a confirmed upstream fix."""
    for defect in _all_written_defects():
        assert defect.status == "open", f"{defect.id}: expected status 'open', got {defect.status}"


def test_every_entry_scope_matches_its_own_real_evidence() -> None:
    """The two C++ findings (PDF-CPP-001, SLIDES-CPP-002) are scoped by the real indexed commit the
    issue's own text names (C++ has no published package version for these pilots); the four
    findings with a real published package version in their own issue text are scoped by it."""
    by_id = {defect.id: defect for defect in _all_written_defects()}

    for commit_id in ("PDF-CPP-001", "SLIDES-CPP-002"):
        defect = by_id[commit_id]
        assert defect.applies_to.indexed_commit is not None, f"{commit_id}: expected indexed_commit scope"
        assert defect.applies_to.package_versions is None, f"{commit_id}: expected no package_versions"

    for version_id in ("PDF-JAVA-001", "PDF-JAVA-002", "SLIDES-NET-006", "SLIDES-PY-001"):
        defect = by_id[version_id]
        assert defect.applies_to.package_versions, f"{version_id}: expected package_versions to be set"
        assert defect.applies_to.indexed_commit is None, f"{version_id}: expected no indexed_commit scope"


def test_pdf_cpp_001_is_method_scoped_not_a_whole_library_claim() -> None:
    """The card's own headline AES-256 finding is about one specific method
    (``Document::Encrypt``), not the whole library - ``subject.kind`` must be ``method``, never
    ``package``, matching this card's own instructions."""
    defects = load_known_defects("pdf", "cpp")
    assert len(defects) == 1
    defect = defects[0]
    assert defect.id == "PDF-CPP-001"
    assert defect.subject.kind == "method"
    assert defect.subject.ref == "Aspose::Pdf::Document"


def test_slides_python_001_has_no_confirmed_public_fqn_so_is_package_scoped() -> None:
    """Unlike the other 5 entries, SLIDES-PY-001's own round-2 text names only a private internal
    implementation symbol (``slide_part.py``'s ``_clone_related_part``), never a real public FQN
    for the collection ``add_clone`` is called on - so this one entry is ``subject.kind:
    "package"``, per this card's own judgment-call convention (package when no real FQN is
    confirmed)."""
    defects = load_known_defects("slides", "python")
    assert len(defects) == 1
    defect = defects[0]
    assert defect.id == "SLIDES-PY-001"
    assert defect.subject.kind == "package"
    assert defect.subject.ref == "aspose-slides-foss"


def test_every_other_entrys_subject_kind_is_method() -> None:
    by_id = {defect.id: defect for defect in _all_written_defects()}
    for method_id in ("PDF-JAVA-001", "PDF-JAVA-002", "SLIDES-CPP-002", "SLIDES-NET-006"):
        assert by_id[method_id].subject.kind == "method", f"{method_id}: expected subject.kind 'method'"


# --------------------------------------------------------------------------------------------
# real end-to-end: the real, committed registry (never monkeypatched) surfaces through a real
# MCP tool for the real pilot scope
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    exact pattern ``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2's own tests)
    already established. Only the symbol CONTENT here is synthetic (this repo has no committed,
    indexed chunk corpus for pdf/cpp to query offline); the known-defects REGISTRY data this
    store's ``get_symbol`` call reads is the real, committed file this card wrote, never
    monkeypatched."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-Cpp", commit="tc646"),
                evidence_refs=(),
                title="pdf/cpp API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, "gen-tc646")
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc646"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


_REAL_PDF_CPP_001_COMMIT = "4b83c9fec1e37fd205156770161f6843bac00ceb"


def _document_symbol_body(*, commit: str) -> str:
    lines = [
        "## Aspose::Pdf::Document",
        "",
        "FQN: Aspose::Pdf::Document",
        "Kind: class_declaration",
        "",
        "Methods:",
        "  - Encrypt(userPassword: string, ownerPassword: string, permissions: Permissions, "
        "algorithm: CryptoAlgorithm) -> void",
        "",
        f"Source-Commit: {commit}",
    ]
    return "\n".join(lines) + "\n"


def test_get_symbol_end_to_end_surfaces_the_real_written_pdf_cpp_aes256_defect() -> None:
    """PDF-CPP-001 (``Aspose::Pdf::Document``, method-scoped, indexed-commit-scoped) is loaded
    from the REAL ``config/known_defects/pdf__cpp.yaml`` this card wrote (``load_known_defects``
    uses its default, production ``REGISTRY_DIR`` here - nothing is monkeypatched) and must
    surface through a real ``get_symbol`` call for the real ``pdf/cpp`` scope, when the queried
    chunk's own ``Source-Commit`` matches the defect's real ``applies_to.indexed_commit``."""
    store = _InMemoryStore((_document_symbol_body(commit=_REAL_PDF_CPP_001_COMMIT),))
    scope = Scope(family="pdf", platform="cpp")

    result = get_symbol(store, scope, "Aspose::Pdf::Document.Encrypt")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == "Aspose::Pdf::Document"
    assert len(result.known_defects) == 1
    assert "Document::Encrypt with CryptoAlgorithm::AESx256" in result.known_defects[0]


def test_get_symbol_end_to_end_does_not_surface_the_defect_for_a_different_commit() -> None:
    """The same real defect, loaded from the same real file, must NOT surface when the queried
    chunk's own commit differs from ``applies_to.indexed_commit`` - proving the commit-scoping
    check is real, not a tautology that always matches."""
    store = _InMemoryStore((_document_symbol_body(commit="f" * 40),))
    scope = Scope(family="pdf", platform="cpp")

    result = get_symbol(store, scope, "Aspose::Pdf::Document.Encrypt")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.known_defects == ()
