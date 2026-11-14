"""KD (G3/TC-656, workstream J, REQ-G3-015): disclosing CELLS-TYPESCRIPT-013 - the round-3
independent re-audit's highest-priority new finding (``docs/round3/issues.json``, P0, category
``upstream-library-bug``): ``Workbook.save()`` in the real upstream ``excel-cells`` TypeScript
library silently drops every worksheet past the 2nd from the saved file's own ``xl/workbook.xml``
sheet index. The data IS physically written into the saved zip (``writeToZip()`` loops over every
real worksheet), but ``generateWorkbook()``'s ``<sheets>`` index is built from exactly two
hand-written, unconditional ``if (this._sheets.worksheets.length > N)`` blocks (N=0,1) with no loop
for index >= 2 - so a 3rd+ worksheet is invisible to Excel and any other real OOXML reader, with
zero exception and zero warning.

This card is data-only: the disclosure mechanism itself (``load_known_defects``, and its wiring
into ``get_symbol``, ``search_symbols``, ``get_product_reference``, and
``report_index_freshness``) already exists and is already correct (KD1-KD5, TC-636/639/641/642/643,
independently re-verified by the supervisor before authoring TC-656). This module proves the one
real, committed entry this card adds to ``config/known_defects/cells__typescript.yaml`` parses,
validates, loads, and surfaces end-to-end through ``get_symbol`` and ``search_symbols`` for the
real ``cells/typescript`` scope - reusing the exact real-registry (never ``tmp_path``-monkeypatched)
pattern ``tests/indexing/test_known_defects_data_cells.py`` (TC-645, KD7) and the exact in-memory
chunk-store pattern ``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2) and
``tests/mcp/tools/test_known_defects_in_search_symbols.py`` (KD3) already established.

Worker's own note on process step 1 (``inputs``): this worker does not have real network access in
this sandbox, so the upstream ``aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript`` source
(``aspose_cells/workbook.ts``) was not re-fetched directly here. The hardcoded-2-sheet /
loops-all-sheets asymmetry is trusted from TC-656's own card text, which states it was already
independently verified by the supervisor before authoring the card, and is corroborated by this
same project's own ``docs/round3/HANDOFF3.md`` executive summary and ``docs/round3/issues.json``
(CELLS-TYPESCRIPT-013, severity P0, round3_verdict "NEW") - both read fresh by this worker.

Worker's own note on a real check-level conflict found while completing this card (reported in
full in this worker's closeout report, not weakened or worked around here): adding this one real,
correct entry to ``config/known_defects/cells__typescript.yaml`` makes two PRE-EXISTING test files
outside this card's ``write_paths`` fail -
``tests/indexing/test_known_defects_data_cells.py`` (TC-645, KD7) hardcodes an exact-12-entries,
round-2-issues.json-only invariant for the ``cells`` family, and
``tests/indexing/test_known_defects_completeness.py`` (TC-649, KD10) hardcodes a permanent,
whole-registry "the two data sources agree EXACTLY" lock scoped only to
``docs/round2/issues.json``. Neither test file's own scope admits a round-3-sourced id by
construction; both are out of this card's ``write_paths``, so this module cannot and does not
alter them (AGENTS.md worker rule 5: never weaken a check to make it pass).
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
from foss_mcp.mcp.tools.get_symbol import ResolvedSignature, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
REGISTRY_PATH = REPO_ROOT / "config" / "known_defects" / "cells__typescript.yaml"

DEFECT_ID = "CELLS-TYPESCRIPT-013"
SCOPE = Scope(family="cells", platform="typescript")
_GENERATION_ID = "gen-tc656"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_real_registry_document() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def _real_defect() -> KnownDefect:
    defects = load_known_defects("cells", "typescript")
    (match,) = [defect for defect in defects if defect.id == DEFECT_ID]
    return match


# --------------------------------------------------------------------------------------------
# the real, committed file still parses as valid YAML and validates against the real schema
# --------------------------------------------------------------------------------------------


def test_real_registry_file_still_validates_against_schema_with_the_new_entry() -> None:
    document = _load_real_registry_document()
    schema = _load_schema()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"cells/typescript registry file failed schema validation: {errors}"
    assert document["pilot"] == "cells_typescript"


def test_the_new_entry_is_present_exactly_once_in_the_raw_document() -> None:
    document = _load_real_registry_document()
    ids = [defect["id"] for defect in document["defects"]]
    assert ids.count(DEFECT_ID) == 1


# --------------------------------------------------------------------------------------------
# load_known_defects returns the new entry, correctly shaped
# --------------------------------------------------------------------------------------------


def test_load_known_defects_returns_an_entry_with_the_real_id_and_subject_refs() -> None:
    defect = _real_defect()
    assert defect.id == DEFECT_ID
    assert defect.subject.refs == ("aspose_cells.workbook.Workbook",)
    assert defect.subject.kind == "method"
    assert defect.status == "open"


def test_no_duplicate_id_within_the_file() -> None:
    defects = load_known_defects("cells", "typescript")
    ids = [defect.id for defect in defects]
    assert ids.count(DEFECT_ID) == 1
    assert len(ids) == len(set(ids)), "a defect id is repeated within cells__typescript.yaml"


def test_verified_on_date_is_a_real_calendar_date() -> None:
    defect = _real_defect()
    # load_known_defects would already have raised KnownDefectsError on a fake calendar date
    # (e.g. 2026-13-40) via its own _is_real_date check; this test additionally proves the
    # recorded date round-trips through date.fromisoformat directly, independent of that.
    parsed = date.fromisoformat(defect.evidence.verified_on.date)
    assert parsed.isoformat() == defect.evidence.verified_on.date


# --------------------------------------------------------------------------------------------
# the disclosure text is factual and does not overstate the defect: only >= 3 worksheets are
# affected, never every save() call
# --------------------------------------------------------------------------------------------


def test_disclosure_text_does_not_claim_every_save_call_fails() -> None:
    defect = _real_defect()
    full_text = " ".join((defect.summary, defect.user_action, defect.evidence.repro)).lower()
    # The real trigger is >= 3 worksheets; a 1- or 2-worksheet save is unaffected. Reject
    # overstated phrasing a careless disclosure could use instead of the qualified claim.
    overstating_phrases = (
        "every save() call",
        "every call to save()",
        "save() always",
        "always drops",
        "any save()",
    )
    for phrase in overstating_phrases:
        assert phrase not in full_text, f"disclosure overstates the defect with {phrase!r}"
    # The qualifying condition (3+ / past the 2nd) must be present somewhere in the disclosure.
    assert "3" in full_text or "2nd" in full_text or "third" in full_text


def test_disclosure_names_the_real_trigger_and_safe_pattern() -> None:
    defect = _real_defect()
    assert "save" in defect.summary.lower() or "save" in defect.user_action.lower()
    assert "worksheet" in defect.summary.lower()
    # The safe pattern: verify by re-opening the saved file, not by trusting in-memory state.
    assert "re-opening" in defect.user_action.lower() or "reopening" in defect.user_action.lower()


# --------------------------------------------------------------------------------------------
# real end-to-end: the real, committed registry (never monkeypatched) surfaces through
# get_symbol and search_symbols for the real cells/typescript scope
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    exact pattern ``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2) and
    ``tests/indexing/test_known_defects_data_cells.py`` (KD7) already established. Only the
    symbol CONTENT here is synthetic (this repo has no committed, indexed chunk corpus for
    cells/typescript to query offline); the known-defects REGISTRY data the calls below read is
    the real, committed file this card wrote, never monkeypatched."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(
                    repository="aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript", commit="tc656"
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


def _line_for(lines: tuple[str, ...], marker: str) -> str:
    """The one rendered known-defect line carrying *marker*. G3/TC-796 added two more
    Workbook-scoped entries (``CELLS-TYPESCRIPT-006`` and ``CELLS-TYPESCRIPT-009``) beside this
    card's ``CELLS-TYPESCRIPT-013``, and known-defect lines are matched on the OWNING type's fqn, so
    ``Workbook`` now surfaces three lines; ``CELLS-TYPESCRIPT-013`` is picked out by its own
    distinctive wording ("past the 2nd"), not by position or by assuming it is the only one."""
    matching = [line for line in lines if marker in line.lower()]
    assert len(matching) == 1, (marker, lines)
    return matching[0]


def _workbook_symbol_body() -> str:
    lines = [
        "## Workbook",
        "",
        "FQN: aspose_cells.workbook.Workbook",
        "Kind: class_declaration",
        "",
        "Methods:",
        "  - save(filePath: string, options?: { password?: string; saveFormat?: SaveFormat }) -> Promise<void>",
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def store() -> _InMemoryStore:
    return _InMemoryStore((_workbook_symbol_body(),))


def test_get_symbol_end_to_end_surfaces_the_real_written_defect(store: _InMemoryStore) -> None:
    """``get_symbol(fqn='aspose_cells.workbook.Workbook')`` for the real ``cells/typescript`` scope
    must carry the real ``CELLS-TYPESCRIPT-013`` disclosure, loaded from the REAL, committed
    registry file this card wrote - nothing monkeypatched."""
    result = get_symbol(store, SCOPE, "aspose_cells.workbook.Workbook")  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert result.fqn == "aspose_cells.workbook.Workbook"
    # G3/TC-796: three Workbook-scoped entries now (013, plus 006 and 009); 013 is still there,
    # exactly once.
    assert len(result.known_defects) == 3
    line = _line_for(result.known_defects, "past the 2nd")
    assert "save" in line.lower()
    assert "worksheet" in line.lower()


def test_get_symbol_resolved_member_save_also_surfaces_the_real_defect(store: _InMemoryStore) -> None:
    """Resolving the member name ``Workbook.save`` (TC-540's member-resolution path) must carry
    the same disclosure, since ``known_defect_lines`` matches on the OWNING type's fqn."""
    result = get_symbol(store, SCOPE, "aspose_cells.workbook.Workbook.save")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == "aspose_cells.workbook.Workbook"
    assert len(result.known_defects) == 3
    assert "save" in _line_for(result.known_defects, "past the 2nd").lower()


def test_search_symbols_end_to_end_surfaces_the_real_written_defect(store: _InMemoryStore) -> None:
    """``search_symbols('aspose_cells.workbook.Workbook')`` for the real ``cells/typescript`` scope
    must carry the same real ``CELLS-TYPESCRIPT-013`` disclosure on its
    ``SymbolMatch.known_defects`` field (KD3), loaded from the REAL, committed registry file -
    nothing monkeypatched."""
    result = search_symbols(store, SCOPE, "aspose_cells.workbook.Workbook")  # type: ignore[arg-type]

    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    matches: list[SymbolMatch] = result
    assert len(matches) == 1
    assert len(matches[0].known_defects) == 3
    line = _line_for(matches[0].known_defects, "past the 2nd")
    assert "save" in line.lower()
    assert "worksheet" in line.lower()


def test_search_symbols_by_save_also_surfaces_the_real_written_defect(store: _InMemoryStore) -> None:
    """``search_symbols('save')`` must also resolve to the ``Workbook`` type and carry the same
    disclosure - proving the defect surfaces regardless of whether the caller searched by type
    name or by the affected method name."""
    result = search_symbols(store, SCOPE, "save")  # type: ignore[arg-type]

    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    matches: list[SymbolMatch] = result
    assert any(match.known_defects for match in matches), (
        "search_symbols('save') must surface the Workbook-scoped CELLS-TYPESCRIPT-013 disclosure"
    )


def test_a_miss_for_an_unrelated_query_is_unaffected(store: _InMemoryStore) -> None:
    result = search_symbols(store, SCOPE, "NoSuchTypeAtAll")  # type: ignore[arg-type]
    assert isinstance(result, Miss)
