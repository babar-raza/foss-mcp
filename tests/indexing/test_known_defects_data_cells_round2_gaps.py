"""G3/TC-796 (REQ-G3-015): registry entries for two cells/typescript round-2 gap rows that are
method-scoped and symbol-reachable - ``CELLS-TYPESCRIPT-006`` (``Workbook.toCsv()``/``toMarkdown()``
export only the first worksheet) and ``CELLS-TYPESCRIPT-009`` (``Workbook.save(path)`` writes an XLSX
zip for every extension except ``.html``/``.htm``).

Both were re-verified on 2026-11-14 against the real upstream source at the indexed commit
``fc186507e5b7124f4664aa6035f25cfd3112367d`` (``excel-cells`` 1.0.0) by reading
``aspose_cells/workbook.ts`` and by running a node/tsx script against it - not trusted from the
round-2 ledger text alone. The ledger's own category for them (``mcp-content-missing`` /
``mcp-error-handling``) is not ``upstream-library-bug``, so both ids are added to
``tests/indexing/test_known_defects_completeness.py``'s ``_ADDITIONAL_DISCLOSABLE_IDS`` (the
TC-706/707/711/728 precedent). The remaining TC-796 rows (CELLS-CPP-004, CELLS-TYPESCRIPT-001,
CELLS-TYPESCRIPT-012, CELLS-GO-009, CELLS-RUST-006) are capability or limitation statements
disclosed through ``known_hazards`` and tested in
``tests/mcp/tools/test_known_hazards_round2_disclosures.py``.

This module proves: the real, committed registry file validates against the real schema with both
new entries; each id equals its ledger issue id and appears exactly once; each ``subject.refs`` value
resolves to a real indexed FQN in the real ``infra/demo-manifests`` corpus; ``user_action`` and
version scoping are present; the disclosure text states the real trigger and safe pattern; and both
entries surface end-to-end through ``get_symbol``/``search_symbols`` using the REAL, committed
registry (never monkeypatched).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
import yaml

from foss_mcp.indexing.known_defects import KnownDefect, load_known_defects
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import (
    ResolvedSignature,
    SymbolSignature,
    extract_fqn,
    get_symbol,
)
from foss_mcp.mcp.tools.search_symbols import SymbolMatch, search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_PATH = REPO_ROOT / "config" / "known_defects" / "cells__typescript.yaml"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"

NEW_IDS = ("CELLS-TYPESCRIPT-006", "CELLS-TYPESCRIPT-009")
WORKBOOK_FQN = "aspose_cells.workbook.Workbook"
SCOPE = Scope(family="cells", platform="typescript")
_GENERATION_ID = "gen-tc796"
VERIFIED_DATE = "2026-11-14"


def _load_registry_document() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def _defect(defect_id: str) -> KnownDefect:
    (match,) = [defect for defect in load_known_defects("cells", "typescript") if defect.id == defect_id]
    return match


def _full_text(defect: KnownDefect) -> str:
    return " ".join((defect.summary, defect.user_action, defect.evidence.repro)).lower()


# --------------------------------------------------------------------------------------------
# schema validity, ids, and the ledger
# --------------------------------------------------------------------------------------------


def test_real_registry_file_validates_against_the_real_schema_with_both_new_entries() -> None:
    document = _load_registry_document()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"cells/typescript registry file failed schema validation: {errors}"
    assert document["pilot"] == "cells_typescript"


@pytest.mark.parametrize("defect_id", NEW_IDS)
def test_each_new_id_equals_a_real_ledger_issue_id_and_appears_exactly_once(defect_id: str) -> None:
    issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    ledger = [issue for issue in issues if issue["id"] == defect_id]
    assert len(ledger) == 1, f"{defect_id} is not a real docs/round2/issues.json id"
    assert ledger[0]["pilot"] == "cells/typescript"

    ids = [defect["id"] for defect in _load_registry_document()["defects"]]
    assert ids.count(defect_id) == 1
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("defect_id", NEW_IDS)
def test_each_entry_is_open_version_scoped_method_kind_with_a_real_date(defect_id: str) -> None:
    defect = _defect(defect_id)
    assert defect.status == "open"
    assert defect.subject.kind == "method"
    assert defect.applies_to.package_versions == ("1.0.0",)
    assert defect.evidence.verified_on.package == "excel-cells"
    assert defect.evidence.verified_on.version == "1.0.0"
    assert defect.evidence.verified_on.date == VERIFIED_DATE
    assert date.fromisoformat(defect.evidence.verified_on.date).isoformat() == VERIFIED_DATE
    assert len(defect.summary) <= 240
    assert defect.user_action.strip(), "user_action must be present"
    # The verification is pinned to the indexed commit, never presented as current upstream.
    assert "fc186507" in defect.evidence.repro


# --------------------------------------------------------------------------------------------
# refs resolve in the REAL corpus
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("tc796-cells-typescript-corpus"))


def test_every_ref_resolves_to_a_real_indexed_fqn_in_the_real_corpus(corpus: ConformanceCorpus) -> None:
    pilot = corpus.pilots["cells__typescript"]
    real_fqns = {fqn for chunk in pilot.symbol_chunks() if (fqn := extract_fqn(chunk["text"])) is not None}
    for defect_id in NEW_IDS:
        for ref in _defect(defect_id).subject.refs:
            assert ref in real_fqns, f"{defect_id}: ref {ref!r} is not a real indexed cells/typescript FQN"


# --------------------------------------------------------------------------------------------
# the disclosure text states the real trigger and safe pattern, and does not overstate
# --------------------------------------------------------------------------------------------


def test_006_names_the_first_worksheet_trigger_and_the_toJson_safe_pattern() -> None:
    defect = _defect("CELLS-TYPESCRIPT-006")
    text = _full_text(defect)
    assert "tocsv" in text and "tomarkdown" in text
    assert "first worksheet" in text or "worksheets[0]" in text
    assert "tojson" in defect.user_action.lower()
    # toJson() DOES include every sheet; the disclosure must not claim it is affected too.
    assert "toJson() includes every sheet" in defect.summary
    assert "tojson() also" not in text


def test_009_names_every_unrecognised_extension_trigger_and_the_safe_pattern() -> None:
    defect = _defect("CELLS-TYPESCRIPT-009")
    text = _full_text(defect)
    assert "save" in text
    for extension in (".pdf", ".csv", ".json", ".md"):
        assert extension in text, extension
    assert ".html" in text
    assert "xlsx" in text
    # Safe pattern: write the exporter strings yourself, and check the saved file.
    assert "tocsv()" in defect.user_action.lower()
    assert "byte-check" in defect.user_action.lower() or "re-open" in defect.user_action.lower()


# --------------------------------------------------------------------------------------------
# real end-to-end: the REAL, committed registry surfaces through get_symbol and search_symbols
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks standing in for GenerationManifestStore - the exact
    pattern ``test_known_defects_data_cells_typescript_013.py`` (TC-656) established. Only the
    symbol content is synthetic; the registry the calls below read is the real, committed file."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(
                    repository="aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript", commit="tc796"
                ),
                evidence_refs=(),
                title="cells/typescript API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, _GENERATION_ID)
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _workbook_symbol_body() -> str:
    lines = [
        "## Workbook",
        "",
        f"FQN: {WORKBOOK_FQN}",
        "Kind: class_declaration",
        "",
        "Methods:",
        "  - toCsv() -> string",
        "  - toMarkdown() -> string",
        "  - save(filePath: string, options?: { password?: string; saveFormat?: SaveFormat }) -> Promise<void>",
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def store() -> _InMemoryStore:
    return _InMemoryStore((_workbook_symbol_body(),))


def _joined(lines: tuple[str, ...]) -> str:
    return " ".join(lines).lower()


def test_get_symbol_surfaces_both_new_disclosures_for_the_workbook_type(store: _InMemoryStore) -> None:
    result = get_symbol(store, SCOPE, WORKBOOK_FQN)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    text = _joined(result.known_defects)
    assert "tocsv" in text and "first worksheet" in text  # CELLS-TYPESCRIPT-006
    assert ".pdf" in text and "xlsx" in text  # CELLS-TYPESCRIPT-009
    assert "worksheet" in text and "save" in text  # CELLS-TYPESCRIPT-013 is still there too


@pytest.mark.parametrize("member", ["toCsv", "toMarkdown", "save"])
def test_get_symbol_resolved_member_surfaces_the_workbook_scoped_disclosures(
    store: _InMemoryStore, member: str
) -> None:
    result = get_symbol(store, SCOPE, f"{WORKBOOK_FQN}.{member}")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == WORKBOOK_FQN
    text = _joined(result.known_defects)
    assert "first worksheet" in text and "xlsx" in text


def test_search_symbols_surfaces_the_new_disclosures_on_the_workbook_match(store: _InMemoryStore) -> None:
    result = search_symbols(store, SCOPE, WORKBOOK_FQN)  # type: ignore[arg-type]

    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    matches: list[SymbolMatch] = result
    assert len(matches) == 1
    text = _joined(matches[0].known_defects)
    assert "first worksheet" in text and "xlsx" in text
