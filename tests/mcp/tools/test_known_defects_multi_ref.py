"""TC-658 (G3/REQ-G3-015): ``known_defects``' ``subject.ref`` (a single string) becomes
``subject.refs`` (a non-empty tuple of strings) - the round-3 independent audit's own #1
recommended, highest-leverage fix (``docs/round3/HANDOFF3.md``, "#1 finding"): a disclosed
upstream-library bug can now name every real sibling symbol it affects, not only the one its
original finding happened to cite, mirroring the exact "one or several" array shape
``AppliesTo.package_versions`` already uses for the identical semantic.

This card proves the MECHANISM works for a list of one (today's real registry content, migrated
mechanically) and for a list of more than one (a synthetic fixture, since populating real sibling
names is deliberately separate, future content-authoring work, out of this card's scope) - never
a content judgment call.

Three disjoint concerns, each covered below:

1. A synthetic multi-ref ``KnownDefect`` is found by ``get_symbol``'s and ``search_symbols``' own
   ``known_defect_lines`` under EITHER real name it names, not just the first (the literal round-3
   finding: pdf/java's real ``PDF-JAVA-001`` entry's own ``user_action`` prose already names
   ``TextStamp`` as a real sibling of ``WatermarkArtifact``, but nothing surfaced it before this
   mechanism existed).
2. Every one of this card's own migrated, already-committed, real single-ref entries (pdf/java,
   cells/typescript, note/python - three different pilots) behaves IDENTICALLY to its
   pre-migration single-``ref`` form: the single most important property proven here, since a
   regression on any of the 23 real migrated files would violate AGENTS.md's "no pilot ever gets
   worse" rule.
3. The schema itself: a non-empty ``refs`` array is required, an empty one is rejected, and the
   OLD ``ref:`` singular key name is now rejected outright (``additionalProperties: false`` on
   ``subject``, combined with ``refs`` now being the only recognized key), so a future authoring
   mistake that reverts to the pre-migration shape cannot silently pass.

Offline, deterministic, no network: every test either reads this project's own real, committed
``config/known_defects/*.yaml`` files (never mutated here) or writes a synthetic fixture under a
``tmp_path``-monkeypatched ``REGISTRY_DIR``, the exact mechanism KD2/KD3's own test files
(``test_known_defects_in_get_symbol.py``, ``test_known_defects_in_search_symbols.py``) already
established.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from foss_mcp.indexing import known_defects as known_defects_module
from foss_mcp.indexing.known_defects import (
    KnownDefectsError,
    Subject,
    load_known_defects,
    registry_path_for,
    validate_known_defects,
)
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)
from foss_mcp.mcp.tools.get_symbol import known_defect_lines as get_symbol_known_defect_lines
from foss_mcp.mcp.tools.search_symbols import known_defect_lines as search_symbols_known_defect_lines

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _symbol_text(fqn: str) -> str:
    """One minimal type chunk's text in this project's own ``## <fqn>`` heading convention - no
    ``Source-Commit:`` trailer, matching every real migrated entry below (all package-version-
    scoped, never indexed-commit-scoped)."""
    return f"## {fqn}\n\nFQN: {fqn}\nKind: class_declaration\n"


# -------------------------------------------------------------------------------------------
# fixtures: a tmp_path-scoped stand-in for the real, committed config/known_defects/ directory -
# the exact mechanism KD2/KD3's own test files already established, never the real one.
# -------------------------------------------------------------------------------------------


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "known_defects"
    directory.mkdir()
    monkeypatch.setattr(known_defects_module, "REGISTRY_DIR", directory)
    return directory


def _synthetic_defect(defect_id: str, refs: list[str], *, kind: str = "method") -> dict[str, Any]:
    return {
        "id": defect_id,
        "subject": {"kind": kind, "refs": refs},
        "applies_to": {"package_versions": ["1.0.0"]},
        "summary": f"Summary describing {defect_id}.",
        "user_action": f"Recommended action for {defect_id}.",
        "evidence": {
            "repro": f"Repro steps for {defect_id}.",
            "verified_on": {"package": "example-pkg", "version": "1.0.0", "date": "2026-11-02"},
        },
        "status": "open",
    }


def _write_registry(
    registry_dir: Path, defects: list[dict[str, Any]], *, family: str = "example", platform: str = "pkg"
) -> None:
    document = {"schema_version": 1, "pilot": f"{family}_{platform}", "defects": defects}
    path = registry_path_for(family, platform, base_dir=registry_dir)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


# -------------------------------------------------------------------------------------------
# 1. a synthetic multi-ref entry is found under EITHER real name, through BOTH tools
# -------------------------------------------------------------------------------------------


def test_get_symbol_matches_a_multi_ref_entry_under_its_first_name(registry_dir: Path) -> None:
    scope = Scope(family="example", platform="pkg")
    _write_registry(registry_dir, [_synthetic_defect("EX-001", ["WatermarkArtifact", "TextStamp"])])

    lines = get_symbol_known_defect_lines(scope, "WatermarkArtifact", _symbol_text("WatermarkArtifact"))

    assert len(lines) == 1
    assert "Summary describing EX-001." in lines[0]


def test_get_symbol_matches_the_same_multi_ref_entry_under_its_second_name(registry_dir: Path) -> None:
    """The literal round-3 finding, proven directly: a sibling symbol named only in the entry's
    own ``refs`` list - never the first/"original" name - surfaces identically."""
    scope = Scope(family="example", platform="pkg")
    _write_registry(registry_dir, [_synthetic_defect("EX-001", ["WatermarkArtifact", "TextStamp"])])

    lines = get_symbol_known_defect_lines(scope, "TextStamp", _symbol_text("TextStamp"))

    assert len(lines) == 1
    assert "Summary describing EX-001." in lines[0]


def test_get_symbol_does_not_match_a_name_outside_the_multi_ref_list(registry_dir: Path) -> None:
    scope = Scope(family="example", platform="pkg")
    _write_registry(registry_dir, [_synthetic_defect("EX-001", ["WatermarkArtifact", "TextStamp"])])

    lines = get_symbol_known_defect_lines(scope, "SomeOtherType", _symbol_text("SomeOtherType"))

    assert lines == ()


def test_search_symbols_matches_a_multi_ref_entry_under_its_first_name(registry_dir: Path) -> None:
    scope = Scope(family="example", platform="pkg")
    _write_registry(registry_dir, [_synthetic_defect("EX-002", ["Stamp", "CryptoAlgorithm"])])

    lines = search_symbols_known_defect_lines(scope, "Stamp", _symbol_text("Stamp"))

    assert len(lines) == 1
    assert "Summary describing EX-002." in lines[0]


def test_search_symbols_matches_the_same_multi_ref_entry_under_its_second_name(registry_dir: Path) -> None:
    scope = Scope(family="example", platform="pkg")
    _write_registry(registry_dir, [_synthetic_defect("EX-002", ["Stamp", "CryptoAlgorithm"])])

    lines = search_symbols_known_defect_lines(scope, "CryptoAlgorithm", _symbol_text("CryptoAlgorithm"))

    assert len(lines) == 1
    assert "Summary describing EX-002." in lines[0]


def test_get_product_reference_multi_ref_entry_names_every_ref_not_just_one(registry_dir: Path) -> None:
    _write_registry(
        registry_dir,
        [_synthetic_defect("EX-003", ["aspose.pdf.cpp", "aspose.pdf.cpp.legacy"], kind="package")],
        family="example",
        platform="pkg",
    )
    inputs = ProductReferenceInputs(platform="pkg", family="example", readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=None)

    assert isinstance(result, ReferenceContent)
    assert "aspose.pdf.cpp, aspose.pdf.cpp.legacy" in result.text
    assert "Summary describing EX-003." in result.text


# -------------------------------------------------------------------------------------------
# 2. every real migrated single-ref entry (three different, already-committed pilots) behaves
#    identically to its pre-migration single-ref form - the single most important property this
#    card proves, against the REAL config/known_defects/ directory (no monkeypatching here).
# -------------------------------------------------------------------------------------------


def test_real_pdf_java_pdf_java_001_subject_refs_is_the_migrated_single_value() -> None:
    defects = {defect.id: defect for defect in load_known_defects("pdf", "java")}
    defect = defects["PDF-JAVA-001"]

    # TC-811 added the sibling ``Page`` (``Page.getArtifacts().add()`` is where the artifact is added).
    assert defect.subject.refs == ("org.aspose.pdf.WatermarkArtifact", "org.aspose.pdf.Page")


def test_real_pdf_java_pdf_java_001_still_surfaces_through_get_symbol_after_migration() -> None:
    """No regression: this real, already-committed entry must keep matching its one real symbol
    through get_symbol's known_defect_lines exactly as it did before this card's migration."""
    scope = Scope(family="pdf", platform="java")
    fqn = "org.aspose.pdf.WatermarkArtifact"

    lines = get_symbol_known_defect_lines(scope, fqn, _symbol_text(fqn))

    assert len(lines) == 1
    assert "no watermark anywhere in the saved PDF" in lines[0]
    assert "TextStamp on a reopened" in lines[0]


def test_real_cells_typescript_013_still_surfaces_through_get_symbol_after_migration() -> None:
    """A second real, already-committed, independently-migrated pilot - cells/typescript's
    CELLS-TYPESCRIPT-013, the pilot the round-3 audit scored worst (F) for exactly this defect."""
    scope = Scope(family="cells", platform="typescript")
    fqn = "aspose_cells.workbook.Workbook"

    lines = get_symbol_known_defect_lines(scope, fqn, _symbol_text(fqn))

    # TC-796 later added CELLS-TYPESCRIPT-006/009 to this same Workbook symbol, so it now carries
    # three disclosures; the migrated CELLS-TYPESCRIPT-013 line must still be exactly one of them.
    matching = [line for line in lines if "sheet index" in line and "3rd+ worksheet" in line]
    assert len(matching) == 1


def test_real_note_python_entries_still_surface_through_get_symbol_after_migration() -> None:
    """A third real, already-committed pilot with TWO separate single-ref entries on two
    different symbols (``DocumentVisitor``, ``RichText``) - proving the migration did not
    collapse, merge, or cross-wire distinct entries."""
    scope = Scope(family="note", platform="python")

    visitor_lines = get_symbol_known_defect_lines(
        scope, "aspose.note.DocumentVisitor", _symbol_text("aspose.note.DocumentVisitor")
    )
    richtext_lines = get_symbol_known_defect_lines(
        scope, "aspose.note.RichText", _symbol_text("aspose.note.RichText")
    )

    assert len(visitor_lines) == 1
    assert len(richtext_lines) == 1
    assert visitor_lines != richtext_lines


def test_real_pdf_java_pdf_java_002_does_not_cross_match_pdf_java_001s_symbol() -> None:
    """pdf/java has two distinct single-ref entries (``WatermarkArtifact``, ``Page``); migrating
    both independently must not make either one match the other's symbol."""
    scope = Scope(family="pdf", platform="java")

    page_lines = get_symbol_known_defect_lines(
        scope, "org.aspose.pdf.Page", _symbol_text("org.aspose.pdf.Page")
    )
    watermark_lines = get_symbol_known_defect_lines(
        scope, "org.aspose.pdf.WatermarkArtifact", _symbol_text("org.aspose.pdf.WatermarkArtifact")
    )

    # TC-811 added ``Page`` as a sibling ref of PDF-JAVA-001 (the watermark is added through
    # ``Page.getArtifacts().add()``), so ``Page`` now carries both entries and ``WatermarkArtifact``
    # still carries only its own: neither entry loses its original symbol.
    assert len(page_lines) == 2
    assert len(watermark_lines) == 1
    assert watermark_lines[0] in page_lines
    assert any("addStamp" in line for line in page_lines)


# -------------------------------------------------------------------------------------------
# 3. the schema: refs is required and non-empty; the old singular "ref" key is now rejected
# -------------------------------------------------------------------------------------------


def _valid_multi_ref_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pilot": "example_pkg",
        "defects": [_synthetic_defect("EX-004", ["SymbolOne", "SymbolTwo"])],
    }


def test_schema_accepts_a_document_with_a_two_entry_refs_array() -> None:
    schema = _load_schema()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(_valid_multi_ref_document()))
    assert errors == [], f"valid multi-ref fixture failed schema validation: {errors}"


def test_schema_rejects_an_empty_refs_array() -> None:
    schema = _load_schema()
    document = copy.deepcopy(_valid_multi_ref_document())
    document["defects"][0]["subject"]["refs"] = []
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors != [], "schema accepted an empty subject.refs array"


def test_schema_rejects_the_old_singular_ref_key_name() -> None:
    """``additionalProperties: false`` on ``subject``, combined with ``refs`` being the only
    recognized key and ``required``, must reject the pre-migration shape outright - a schema that
    still silently accepted ``ref:`` alongside (or instead of) ``refs:`` would hide a future
    authoring mistake that reverts to the old, single-symbol-only shape."""
    schema = _load_schema()
    document = copy.deepcopy(_valid_multi_ref_document())
    del document["defects"][0]["subject"]["refs"]
    document["defects"][0]["subject"]["ref"] = "SymbolOne"
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors != [], "schema accepted the old singular 'ref' key"


def test_loader_rejects_an_empty_refs_array(registry_dir: Path) -> None:
    """The loader's own hand-written validation (``_subject_problems``) must independently catch
    this too, not only the JSON Schema document - mirroring how ``applies_to.package_versions``'
    own non-empty-list rule is enforced in both places."""
    document = _valid_multi_ref_document()
    document["defects"][0]["subject"]["refs"] = []
    _write_registry(registry_dir, document["defects"], family="example", platform="pkg")

    with pytest.raises(KnownDefectsError, match="non-empty list"):
        load_known_defects("example", "pkg", base_dir=registry_dir)


def test_loader_rejects_the_old_singular_ref_key_name(registry_dir: Path) -> None:
    document = _valid_multi_ref_document()
    del document["defects"][0]["subject"]["refs"]
    document["defects"][0]["subject"]["ref"] = "SymbolOne"
    _write_registry(registry_dir, document["defects"], family="example", platform="pkg")

    with pytest.raises(KnownDefectsError) as exc_info:
        load_known_defects("example", "pkg", base_dir=registry_dir)
    message = str(exc_info.value)
    assert "missing key 'refs'" in message
    assert "unexpected key 'ref'" in message


def test_loader_rejects_duplicate_refs_within_one_entry(registry_dir: Path) -> None:
    document = _valid_multi_ref_document()
    document["defects"][0]["subject"]["refs"] = ["SymbolOne", "SymbolOne"]
    _write_registry(registry_dir, document["defects"], family="example", platform="pkg")

    with pytest.raises(KnownDefectsError, match="must be unique"):
        load_known_defects("example", "pkg", base_dir=registry_dir)


def test_subject_refs_field_is_a_tuple_not_a_list() -> None:
    """``Subject.refs`` (frozen dataclass) must be a ``tuple``, matching
    ``AppliesTo.package_versions``'s own established immutable-field convention - never a
    mutable ``list`` leaking out of a frozen dataclass."""
    defect = load_known_defects("pdf", "java")[0]
    assert isinstance(defect.subject, Subject)
    assert isinstance(defect.subject.refs, tuple)


def test_validate_known_defects_accepts_a_real_multi_ref_synthetic_document_directly() -> None:
    document = _valid_multi_ref_document()
    assert validate_known_defects(document, pilot="example_pkg") == []
