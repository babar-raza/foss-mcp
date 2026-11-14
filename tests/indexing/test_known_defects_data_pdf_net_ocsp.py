"""G3/TC-828 (REQ-G3-015): disclosure of pdf/net's silently ignored ``OcspSettings`` configuration.

``Aspose.Pdf.OcspSettings`` (``ServerUrl``, ``RequestTimeout``) and the ``OcspSettings`` property of
``Aspose.Pdf.Forms.Signature`` (documented as "honoured by the signer when set") are accepted without a
diagnostic, but a grep of all of ``src`` in the real upstream source
(``aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET`` at the pilot's pinned ``b7172877``, package 26.9.0, and
unchanged at tags ``v26.10.0`` and ``v26.11.0``) finds no read of ``OcspSettings``, its ``ServerUrl``
or its ``RequestTimeout`` outside the declarations, and ``Forms/Signature.cs`` itself states that
revocation validation (OCSP/CRL) is not implemented in this FOSS build. Reflection over the published
``Aspose.PDF.FOSS`` 26.9.0, 26.10.0 and 26.11.0 NuGet packages shows both types/properties exist. So an
agent that configures an OCSP responder gets a silently ignored setting. This card is disclosure-only
(Decision A): one ``config/known_defects/pdf__net.yaml`` entry, ``PDF-NET-009`` - the round-3 audit's
own finding id ("known_defects ... empty ..., including the clearest candidate (OcspSettings)").

Everything below reads the REAL, committed ``config/known_defects/pdf__net.yaml`` and the REAL,
committed ``infra/demo-manifests`` corpus (copied to a temp directory first; the repository is never
written to), through the real server wrappers.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import jsonschema
import pytest
import yaml

from foss_mcp.indexing.known_defects import KnownDefect, load_known_defects
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.tools.get_symbol import (
    NotFound,
    SymbolSignature,
    extract_defined_in,
    extract_fqn,
    known_defect_lines,
)
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_PATH = REPO_ROOT / "config" / "known_defects" / "pdf__net.yaml"

DEFECT_ID = "PDF-NET-009"
PILOT = "pdf__net"
REFS = ("Aspose.Pdf.OcspSettings", "Aspose.Pdf.Forms.Signature")


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("tc828-pdf-net-ocsp-corpus"))


def _defects() -> list[KnownDefect]:
    return load_known_defects("pdf", "net")


def _defect() -> KnownDefect:
    (match,) = [defect for defect in _defects() if defect.id == DEFECT_ID]
    return match


def _carries(lines: tuple[str, ...]) -> bool:
    summary = _defect().summary
    return any(summary in line for line in lines)


def _real_fqns(corpus: ConformanceCorpus) -> frozenset[str]:
    fqns: set[str] = set()
    for chunk in corpus.pilots[PILOT].symbol_chunks():
        fqn = extract_fqn(chunk["text"])
        if fqn:
            fqns.add(fqn)
    return frozenset(fqns)


# --------------------------------------------------------------------------------------------
# 1. the registry entry: schema-valid, correctly shaped, honest
# --------------------------------------------------------------------------------------------


def test_registry_file_validates_against_the_schema_with_the_new_entry() -> None:
    document = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"pdf/net registry file failed schema validation: {errors}"
    assert document["pilot"] == "pdf_net"
    ids = [defect["id"] for defect in document["defects"]]
    assert ids.count(DEFECT_ID) == 1
    assert len(ids) == len(set(ids))


def test_entry_shape() -> None:
    defect = _defect()
    assert defect.subject.kind in {"method", "class"}
    assert defect.subject.refs == REFS
    assert defect.status == "open"
    # verified at source (b7172877 / 26.9.0, v26.10.0, v26.11.0) and by reflection on all three packages
    assert defect.applies_to.package_versions == ("26.9.0", "26.10.0", "26.11.0")
    assert defect.evidence.verified_on.package == "Aspose.PDF.FOSS"
    assert defect.evidence.verified_on.version == "26.11.0"
    assert date.fromisoformat(defect.evidence.verified_on.date).isoformat() == "2026-11-14"
    assert len(defect.summary) <= 240


def test_user_action_present_and_actionable() -> None:
    defect = _defect()
    assert defect.user_action.strip()
    text = defect.user_action.lower()
    assert "ocsp" in text
    assert "revocation" in text
    assert "no effect" in text or "ignored" in text


def test_disclosure_states_the_verified_facts_and_does_not_overstate() -> None:
    defect = _defect()
    summary = defect.summary.lower()
    assert "ocspsettings" in summary
    assert "ignored" in summary or "never read" in summary
    repro = defect.evidence.repro
    # the evidence names the pinned source, the unread properties, and the upstream self-disclosure
    assert "b7172877" in repro
    assert "ServerUrl" in repro and "RequestTimeout" in repro
    assert "not implemented in this FOSS build" in repro
    # the only ServerUrl actually used by signing is the timestamp one - the disclosure must not
    # tell agents that timestamping is also broken
    full = " ".join((defect.summary, defect.user_action, repro)).lower()
    for overstated in ("timestamp is ignored", "timestampsettings is ignored", "signing fails"):
        assert overstated not in full


def test_the_entry_is_a_real_round3_audit_finding_id() -> None:
    round3 = json.loads((REPO_ROOT / "docs" / "round3" / "issues.json").read_text(encoding="utf-8"))
    pilot = next(entry for entry in round3["pilots"] if entry["pilot"] == "pdf/net")
    match = next(issue for issue in pilot["issues"] if issue["id"] == DEFECT_ID)
    assert "OcspSettings" in match["summary"]


# --------------------------------------------------------------------------------------------
# 2. every ref is a real indexed FQN and surfaces the disclosure through the real server tools
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ref", REFS)
def test_ref_is_a_real_indexed_fqn(corpus: ConformanceCorpus, ref: str) -> None:
    assert ref in _real_fqns(corpus), f"{ref!r} is not an indexed FQN of {PILOT}"


def test_the_indexed_signature_chunk_lists_the_ocsp_settings_property(corpus: ConformanceCorpus) -> None:
    """The member the disclosure is about really is on the indexed ``Signature`` chunk."""
    text = next(
        chunk["text"]
        for chunk in corpus.pilots[PILOT].symbol_chunks()
        if extract_fqn(chunk["text"]) == "Aspose.Pdf.Forms.Signature"
    )
    assert "OcspSettings: Aspose.Pdf.OcspSettings" in text


@pytest.mark.parametrize("ref", REFS)
def test_get_symbol_on_the_ref_carries_the_disclosure_line(corpus: ConformanceCorpus, ref: str) -> None:
    tool = server_module._wrap_get_symbol(corpus.store, corpus.pilots[PILOT].scope)
    result = tool(fqn=ref)
    assert isinstance(result, SymbolSignature) and result.found, f"get_symbol({ref!r}) -> {result!r}"
    assert _carries(result.known_defects)


@pytest.mark.parametrize("ref", REFS)
def test_list_members_on_the_ref_carries_the_disclosure_line(corpus: ConformanceCorpus, ref: str) -> None:
    tool = server_module._wrap_list_members(corpus.store, corpus.pilots[PILOT].scope)
    result = tool(fqn=ref)
    assert not isinstance(result, NotFound), f"list_members({ref!r}) -> {result!r}"
    assert _carries(tuple(result))


# --------------------------------------------------------------------------------------------
# 3. no other symbol of the pilot gains the line
# --------------------------------------------------------------------------------------------


def test_no_other_symbol_of_the_pilot_gains_the_disclosure_line(corpus: ConformanceCorpus) -> None:
    scope = corpus.pilots[PILOT].scope
    texts: dict[str, str] = {}
    for chunk in corpus.pilots[PILOT].symbol_chunks():
        fqn = extract_fqn(chunk["text"])
        if fqn:
            texts.setdefault(fqn, chunk["text"])
    assert len(texts) > 100, "the real pdf/net corpus should index hundreds of symbols"

    carrying: set[str] = set()
    for fqn, text in texts.items():
        lines = known_defect_lines(scope, fqn, text)
        target = extract_defined_in(text)
        if target is not None and target in texts:
            lines += known_defect_lines(scope, target, texts[target])
        if _carries(lines):
            carrying.add(fqn)

    assert carrying == set(REFS), f"unexpected symbols carry the OcspSettings disclosure: {sorted(carrying)}"
