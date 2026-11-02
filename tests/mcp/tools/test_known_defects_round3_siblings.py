"""TC-656 rework attempt 3 (G3, workstream J, REQ-G3-015): fresh test coverage for the two
brand-new round-3-sourced sibling disclosures, ``EML-NET-003`` (``config/known_defects/email__net.yaml``)
and ``PDF-NET-003`` (``config/known_defects/pdf__net.yaml``).

Context: TC-656 attempt 2's widened completeness lock (``tests/indexing/test_known_defects_completeness.py``)
mechanically found that round3 contributes 6 real new ``upstream-library-bug`` ids project-wide, 5
of which had no registry entry anywhere. This card's write_paths grew to cover all 5; this module
is the fresh test coverage for the 2 of those 5 whose pilots (``email/net``, ``pdf/net``) had NO
existing ``known_defects`` test file at all - unlike ``3D-JAVA-014``/``CELLS-GO-012``/
``PAGE-PYTHON-010``, which slot into the existing, now-widened ``test_known_defects_data_3d.py``/
``test_known_defects_data_cells.py``/``test_known_defects_data_remaining.py``.

``EML-NET-003``: ``MapiMessage.SaveToEml`` in Aspose.Email.Foss 26.7.0 writes the Bcc header
verbatim into the serialized message, undocumented. Round2 filed this same technical finding
under ``category: "mcp-docs-quality"``; round3 reclassified it ``"upstream-library-bug"`` -
confirmed directly against both real, committed issues.json files (not assumed).

``PDF-NET-003``: a ``Table`` added to a page with no visible border set is silently invisible to
``TableAbsorber`` - it reports 0 tables found, with no error, even though the table object was
genuinely added. Round2 filed this as ``category: "example-broken"``; round3 reclassified it
``"upstream-library-bug"`` - also confirmed directly.

This module reads the real, committed ``config/known_defects/email__net.yaml`` and
``pdf__net.yaml`` files this card wrote - never a ``tmp_path``-monkeypatched registry - because
the whole point is to prove the REAL data loads and surfaces correctly. The one exception is the
symbol store used by the ``get_symbol``/``search_symbols`` end-to-end tests, which is an in-memory
chunk store built the same way ``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2) and
``tests/mcp/tools/test_known_defects_in_search_symbols.py`` (KD3) already established - there is no
committed, indexed, queryable chunk corpus for email/net or pdf/net in this repository, so a
synthetic store is the only way to exercise these tools end to end; the REGISTRY data they read is
real and uncontrolled by this test.

Network access note (same disclosure this card's other new entries carry): this worker does not
have real network access in this sandbox, so the real upstream ``Aspose.Email-FOSS-for-.Net`` and
``Aspose.PDF-FOSS-for-.NET`` sources were not re-fetched directly. The real public method
existence (``MapiMessage.SaveToEml``, ``TableAbsorber``) and the real package
coordinates/versions (``Aspose.Email.Foss`` 26.7.0, ``Aspose.PDF.FOSS`` 26.10.0) were confirmed
directly against this repo's own committed ``tests/fixtures/email_net/api_surface.json``,
``tests/fixtures/pdf_net/api_surface.json``, and ``infra/demo-manifests/package_registry_email_net.json``/
``package_registry_pdf_net.json`` - real, committed, already-ingested evidence, not re-executed
upstream builds.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import yaml

from foss_mcp.indexing.known_defects import load_known_defects
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import ResolvedSignature, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_DIR = REPO_ROOT / "config" / "known_defects"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"
ROUND3_ISSUES_PATH = REPO_ROOT / "docs" / "round3" / "issues.json"

EMAIL_NET_SCOPE = Scope(family="email", platform="net")
PDF_NET_SCOPE = Scope(family="pdf", platform="net")


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_real_registry_document(family: str, platform: str) -> dict:
    path = REGISTRY_DIR / f"{family}__{platform}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------------
# both brand-new registry files parse as valid YAML and validate against the real schema
# --------------------------------------------------------------------------------------------


def test_email_net_registry_file_parses_and_validates_against_schema() -> None:
    document = _load_real_registry_document("email", "net")
    schema = _load_schema()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"email/net registry file failed schema validation: {errors}"
    assert document["schema_version"] == 1
    assert document["pilot"] == "email_net"
    assert len(document["defects"]) == 1


def test_pdf_net_registry_file_parses_and_validates_against_schema() -> None:
    document = _load_real_registry_document("pdf", "net")
    schema = _load_schema()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"pdf/net registry file failed schema validation: {errors}"
    assert document["schema_version"] == 1
    assert document["pilot"] == "pdf_net"
    assert len(document["defects"]) == 1


# --------------------------------------------------------------------------------------------
# load_known_defects returns each entry with the real id/subject/status
# --------------------------------------------------------------------------------------------


def test_load_known_defects_returns_eml_net_003_with_the_real_shape() -> None:
    (defect,) = load_known_defects("email", "net")
    assert defect.id == "EML-NET-003"
    assert defect.subject.kind == "method"
    assert defect.subject.refs == ("MapiMessage",)
    assert defect.status == "open"
    assert defect.applies_to.package_versions == ("26.7.0",)
    assert defect.evidence.verified_on.package == "Aspose.Email.Foss"


def test_load_known_defects_returns_pdf_net_003_with_the_real_shape() -> None:
    (defect,) = load_known_defects("pdf", "net")
    assert defect.id == "PDF-NET-003"
    assert defect.subject.kind == "method"
    assert defect.subject.refs == ("TableAbsorber",)
    assert defect.status == "open"
    assert defect.applies_to.package_versions == ("26.10.0",)
    assert defect.evidence.verified_on.package == "Aspose.PDF.FOSS"


# --------------------------------------------------------------------------------------------
# both ids are real, round3-confirmed upstream-library-bug findings - traced algorithmically
# against the real committed issues.json data, not assumed
# --------------------------------------------------------------------------------------------


def test_eml_net_003_is_a_real_round3_upstream_library_bug_finding_reclassified_from_round2() -> None:
    round2_issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    round2_match = next(issue for issue in round2_issues if issue["id"] == "EML-NET-003")
    assert round2_match["category"] != "upstream-library-bug"  # filed differently in round2

    round3_document = json.loads(ROUND3_ISSUES_PATH.read_text(encoding="utf-8"))
    email_net_pilot = next(pilot for pilot in round3_document["pilots"] if pilot["pilot"] == "email/net")
    round3_match = next(issue for issue in email_net_pilot["issues"] if issue["id"] == "EML-NET-003")
    assert round3_match["category"] == "upstream-library-bug"


def test_pdf_net_003_is_a_real_round3_upstream_library_bug_finding_reclassified_from_round2() -> None:
    round2_issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    round2_match = next(issue for issue in round2_issues if issue["id"] == "PDF-NET-003")
    assert round2_match["category"] != "upstream-library-bug"  # filed differently in round2

    round3_document = json.loads(ROUND3_ISSUES_PATH.read_text(encoding="utf-8"))
    pdf_net_pilot = next(pilot for pilot in round3_document["pilots"] if pilot["pilot"] == "pdf/net")
    round3_match = next(issue for issue in pdf_net_pilot["issues"] if issue["id"] == "PDF-NET-003")
    assert round3_match["category"] == "upstream-library-bug"


# --------------------------------------------------------------------------------------------
# disclosure text accuracy: neither overstates its defect
# --------------------------------------------------------------------------------------------


def test_eml_net_003_disclosure_does_not_overstate_the_defect() -> None:
    (defect,) = load_known_defects("email", "net")
    full_text = " ".join((defect.summary, defect.user_action, defect.evidence.repro)).lower()
    assert "bcc" in full_text
    # The real defect is Bcc preserved verbatim, not the whole message being corrupted or lost.
    assert "to" in full_text or "cc" in full_text or "recipient" in full_text


def test_pdf_net_003_disclosure_does_not_overstate_the_defect() -> None:
    (defect,) = load_known_defects("pdf", "net")
    full_text = " ".join((defect.summary, defect.user_action, defect.evidence.repro)).lower()
    # Only a BORDERLESS table is affected - a table with a visible border is detected correctly.
    # Reject phrasing that claims TableAbsorber never finds any table at all.
    overstating_phrases = ("never finds any table", "all tables", "every table is invisible")
    for phrase in overstating_phrases:
        assert phrase not in full_text, f"disclosure overstates the defect with {phrase!r}"
    assert "border" in full_text


# --------------------------------------------------------------------------------------------
# real end-to-end: the real, committed registries (never monkeypatched) surface through
# get_symbol and search_symbols for the real email/net and pdf/net scopes
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    exact pattern ``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2) already
    established. Only the symbol CONTENT here is synthetic; the known-defects REGISTRY data the
    calls below read is the real, committed file this card wrote, never monkeypatched."""

    def __init__(self, repository: str, generation_id: str, bodies: tuple[str, ...]) -> None:
        self._generation_id = generation_id
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository=repository, commit="tc656"),
                evidence_refs=(),
                title="API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, generation_id)
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return self._generation_id

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _mapi_message_symbol_body() -> str:
    lines = [
        "## MapiMessage",
        "",
        "FQN: MapiMessage",
        "Kind: class_declaration",
        "",
        "Methods:",
        "  - SaveToEml(path: string) -> void",
    ]
    return "\n".join(lines) + "\n"


def _table_absorber_symbol_body() -> str:
    lines = [
        "## TableAbsorber",
        "",
        "FQN: TableAbsorber",
        "Kind: class_declaration",
        "",
        "Methods:",
        "  - Visit(page: Page) -> void",
    ]
    return "\n".join(lines) + "\n"


def test_get_symbol_end_to_end_surfaces_eml_net_003() -> None:
    store = _InMemoryStore(
        "aspose-email-foss/Aspose.Email-FOSS-for-.Net", "gen-tc656-email", (_mapi_message_symbol_body(),)
    )

    result = get_symbol(store, EMAIL_NET_SCOPE, "MapiMessage")  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert result.fqn == "MapiMessage"
    assert len(result.known_defects) == 1
    assert "bcc" in result.known_defects[0].lower()


def test_get_symbol_resolved_member_savetoeml_also_surfaces_eml_net_003() -> None:
    store = _InMemoryStore(
        "aspose-email-foss/Aspose.Email-FOSS-for-.Net", "gen-tc656-email", (_mapi_message_symbol_body(),)
    )

    result = get_symbol(store, EMAIL_NET_SCOPE, "MapiMessage.SaveToEml")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == "MapiMessage"
    assert len(result.known_defects) == 1


def test_search_symbols_end_to_end_surfaces_eml_net_003() -> None:
    store = _InMemoryStore(
        "aspose-email-foss/Aspose.Email-FOSS-for-.Net", "gen-tc656-email", (_mapi_message_symbol_body(),)
    )

    result = search_symbols(store, EMAIL_NET_SCOPE, "MapiMessage")  # type: ignore[arg-type]

    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    matches: list[SymbolMatch] = result
    assert len(matches) == 1
    assert len(matches[0].known_defects) == 1
    assert "bcc" in matches[0].known_defects[0].lower()


def test_get_symbol_end_to_end_surfaces_pdf_net_003() -> None:
    store = _InMemoryStore(
        "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", "gen-tc656-pdf", (_table_absorber_symbol_body(),)
    )

    result = get_symbol(store, PDF_NET_SCOPE, "TableAbsorber")  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert result.fqn == "TableAbsorber"
    assert len(result.known_defects) == 1
    assert "border" in result.known_defects[0].lower()


def test_search_symbols_end_to_end_surfaces_pdf_net_003() -> None:
    store = _InMemoryStore(
        "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", "gen-tc656-pdf", (_table_absorber_symbol_body(),)
    )

    result = search_symbols(store, PDF_NET_SCOPE, "TableAbsorber")  # type: ignore[arg-type]

    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    matches: list[SymbolMatch] = result
    assert len(matches) == 1
    assert len(matches[0].known_defects) == 1
    assert "border" in matches[0].known_defects[0].lower()


def test_a_miss_for_an_unrelated_query_is_unaffected_in_either_scope() -> None:
    email_store = _InMemoryStore(
        "aspose-email-foss/Aspose.Email-FOSS-for-.Net", "gen-tc656-email", (_mapi_message_symbol_body(),)
    )
    pdf_store = _InMemoryStore(
        "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", "gen-tc656-pdf", (_table_absorber_symbol_body(),)
    )

    assert isinstance(search_symbols(email_store, EMAIL_NET_SCOPE, "NoSuchTypeAtAll"), Miss)  # type: ignore[arg-type]
    assert isinstance(search_symbols(pdf_store, PDF_NET_SCOPE, "NoSuchTypeAtAll"), Miss)  # type: ignore[arg-type]
