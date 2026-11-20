"""G3/TC-811 (REQ-G3-015): round 3's own #1 finding (docs/round3/HANDOFF3.md) says a known-defect
entry must cover every real sibling symbol of its defect, not only the one symbol its original audit
finding happened to cite. TC-658 built ``subject.refs`` (a list) but each entry still named just that
one symbol. This card extends 29 real registry entries across 16 pilots with 48 verified sibling refs.

How each added ref was chosen (the rule this module's tests pin, so a later edit cannot silently
undo it):

* Every added ref is a real indexed FQN of its own pilot (checked against the real, committed
  ``infra/demo-manifests`` corpus, in that registry's own spelling) AND the entry's described defect
  really applies to that symbol: either the audit's own prose names it as affected (e.g.
  ``FileFormat`` for the 3d/java STL entries, ``Table`` for PDF-NET-003, the note/python
  ``Table``/``TableRow``/``TableCell``/``AttachedFile``/``NumberList`` types), or it is the
  canonical definition chunk a re-export alias resolves to (e.g.
  ``aspose.threed.formats.obj.ObjSaveOptions.ObjSaveOptions`` for 3D-PYTHON-001, which a bare
  ``ObjSaveOptions`` search reaches and which the entry's existing alias ref never matched).
* ``get_symbol`` on a Python re-export shell already returns the disclosures of the shell AND its
  canonical definition (TC-794), so a pure alias that is already reached through that existing
  mechanism is NOT added (3D-PYTHON-006, CELLS-PY-005, TEX-PYTHON-001's ``aspose_tex.TeXJob``,
  WORDS-PY-008's ``aspose.words_foss.Document``); adding it would only be redundant.
* Deliberately NOT added, because the defect does not demonstrably apply: the empty placeholder
  ``aspose.threed.formats.ObjSaveOptions``/``GltfSaveOptions`` (the real index marks them
  ``Shadowed-By`` the working class), ``aspose_barcode_foss.UpceOptions`` (the constraint is on
  ``upce()``'s input, not on its options object), the cells/go ``Workbook`` (a generic container that
  only appears in the defect chain), and ``aspose.words_foss.light_document_model.Document`` for
  WORDS-PY-008 (that class has no ``get_text``; only WORDS-PY-013's ``page_count`` field lives there).

Everything below reads the REAL, committed ``config/known_defects/*.yaml`` and the REAL, committed
``infra/demo-manifests`` (copied to a temp directory first; the repository is never written to).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, NamedTuple

import pytest

from foss_mcp.indexing.known_defects import KnownDefect, load_known_defects
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.tools import get_symbol as get_symbol_module
from foss_mcp.mcp.tools.get_symbol import (
    NotFound,
    SymbolSignature,
    extract_defined_in,
    extract_fqn,
    known_defect_lines,
)
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus


class _Case(NamedTuple):
    """One real registry entry: its pre-TC-811 ``subject.refs`` and the refs this card added."""

    pilot: str
    defect_id: str
    baseline_refs: tuple[str, ...]
    added_refs: tuple[str, ...]


_CASES: tuple[_Case, ...] = (
    _Case(
        "3d__java",
        "3D-JAVA-002",
        ("com.aspose.threed.Scene",),
        ("com.aspose.threed.FileFormat",),
    ),
    _Case(
        "3d__java",
        "3D-JAVA-003",
        ("com.aspose.threed.Scene",),
        ("com.aspose.threed.FileFormat",),
    ),
    _Case(
        "3d__java",
        "3D-JAVA-004",
        ("org.aspose:aspose-3d-foss",),
        ("com.aspose.threed.Scene", "com.aspose.threed.FileFormat", "com.aspose.threed.SaveOptions"),
    ),
    _Case(
        "3d__net",
        "3D-NET-001",
        ("Aspose.3D.FOSS",),
        ("Aspose.ThreeD.Scene", "Aspose.ThreeD.FileFormat"),
    ),
    _Case(
        "3d__net",
        "3D-NET-004",
        ("Aspose.3D.FOSS",),
        ("Aspose.ThreeD.Scene",),
    ),
    _Case(
        "3d__net",
        "3D-NET-006",
        ("Aspose.3D.FOSS",),
        ("Aspose.ThreeD.Scene",),
    ),
    _Case(
        "3d__net",
        "3D-NET-008",
        ("Aspose.3D.FOSS",),
        ("Aspose.ThreeD.Scene",),
    ),
    _Case(
        "3d__python",
        "3D-PYTHON-001",
        (
            "aspose-3d-foss",
            "aspose.threed.Scene.Scene",
            "aspose.threed.Scene",
            "aspose.threed.formats.obj.ObjSaveOptions",
        ),
        ("aspose.threed.formats.obj.ObjSaveOptions.ObjSaveOptions",),
    ),
    _Case(
        "3d__python",
        "3D-PYTHON-010",
        (
            "aspose-3d-foss",
            "aspose.threed.Scene.Scene",
            "aspose.threed.Scene",
            "aspose.threed.formats.obj.ObjSaveOptions",
        ),
        ("aspose.threed.formats.obj.ObjSaveOptions.ObjSaveOptions",),
    ),
    _Case(
        "3d__python",
        "3D-PYTHON-002",
        (
            "aspose-3d-foss",
            "aspose.threed.Scene.Scene",
            "aspose.threed.Scene",
            "aspose.threed.formats.gltf.GltfSaveOptions",
        ),
        ("aspose.threed.formats.gltf.GltfSaveOptions.GltfSaveOptions",),
    ),
    _Case(
        "3d__python",
        "3D-PYTHON-013",
        ("aspose.threed.entities.Mesh", "aspose.threed.Node"),
        (
            "aspose.threed.entities.Mesh.Mesh",
            "aspose.threed.Node.Node",
            "aspose.threed.utilities.BoundingBox.BoundingBox",
        ),
    ),
    _Case(
        "barcode__python",
        "BARCODE-PY-006",
        ("aspose_barcode_foss.upce",),
        ("aspose_barcode_foss.api.upce",),
    ),
    _Case(
        "cells__go",
        "CELLS-GO-001",
        ("Cell",),
        ("Cells", "Load"),
    ),
    _Case(
        "cells__net",
        "CELLS-NET-002",
        ("Aspose.Cells_FOSS.Workbook", "Aspose.Cells_FOSS.SaveOptions", "Aspose.Cells_FOSS.PdfSaveOptions"),
        ("Aspose.Cells_FOSS.SaveFormat",),
    ),
    _Case(
        "cells__typescript",
        "CELLS-TYPESCRIPT-004",
        ("aspose_cells.cell.Cell",),
        ("aspose_cells.worksheet.Worksheet", "aspose_cells.hyperlink.HyperlinkCollection"),
    ),
    _Case(
        "cells__typescript",
        "CELLS-TYPESCRIPT-005",
        ("aspose_cells.worksheet.Worksheet",),
        (
            "aspose_cells.validation.DataValidation",
            "aspose_cells.types.DataValidation",
            "aspose_cells.comment.Comment",
            "aspose_cells.types.Comment",
            "aspose_cells.conditionalformat.ConditionalFormat",
        ),
    ),
    _Case(
        "cells__typescript",
        "CELLS-TYPESCRIPT-008",
        ("aspose_cells.style.Style",),
        ("aspose_cells.types.Style",),
    ),
    _Case(
        "email__python",
        "EMAIL-PY-006",
        ("aspose.email_foss.msg.MapiMessage",),
        ("aspose.email_foss.msg.mapi_message.MapiMessage",),
    ),
    _Case(
        "imaging__net",
        "IMAGING-NET-002",
        ("Aspose.Imaging.Foss.ImageProbe",),
        ("Aspose.Imaging.Foss.ImageInfo",),
    ),
    _Case(
        "imaging__net",
        "IMAGING-NET-008",
        ("Aspose.Imaging.Foss.ImageProbe",),
        ("Aspose.Imaging.Foss.ImageInfo",),
    ),
    _Case(
        "note__python",
        "NOTE-PY-002",
        ("aspose.note.DocumentVisitor",),
        (
            "aspose.note.model.DocumentVisitor",
            "aspose.note.model.Table",
            "aspose.note.model.TableRow",
            "aspose.note.model.TableCell",
            "aspose.note.model.AttachedFile",
            "aspose.note.model.NumberList",
        ),
    ),
    _Case(
        "note__python",
        "NOTE-PY-008",
        ("aspose.note.RichText",),
        ("aspose.note.model.RichText",),
    ),
    _Case(
        "page__python",
        "PAGE-PYTHON-010",
        ("aspose.page.ps.PsDocument", "aspose.page.ps.document.PsDocument"),
        ("aspose.page.ps.output.to_pdf", "aspose.page.ps.output.to_image"),
    ),
    _Case(
        "page__python",
        "PAGE-PYTHON-011",
        ("aspose.page.ps.PsDocument", "aspose.page.ps.document.PsDocument"),
        ("aspose.page.ps.output.to_pdf", "aspose.page.ps.output.to_image"),
    ),
    _Case(
        "pdf__java",
        "PDF-JAVA-001",
        ("org.aspose.pdf.WatermarkArtifact",),
        ("org.aspose.pdf.Page",),
    ),
    _Case(
        "pdf__net",
        "PDF-NET-003",
        ("Aspose.Pdf.Text.TableAbsorber",),
        ("Aspose.Pdf.Table",),
    ),
    _Case(
        "slides__net",
        "SLIDES-NET-006",
        ("Aspose.Slides.Foss.ILayoutSlide",),
        ("Aspose.Slides.Foss.LayoutSlide",),
    ),
    # G3/TC-853: the tex__python / TEX-PYTHON-001 case was removed - tex/python was re-pinned at the upstream release 26.11.1
    # (07db3203), where the library parses and imports, and TEX-PYTHON-001 was retired on re-verification.
    _Case(
        "words__python",
        "WORDS-PY-013",
        ("aspose.words_foss.Document", "aspose.words_foss.document.Document"),
        ("aspose.words_foss.light_document_model.Document",),
    ),
)

_PILOTS = sorted({case.pilot for case in _CASES})
_ADDED_PAIRS = [(case.pilot, case.defect_id, ref) for case in _CASES for ref in case.added_refs]
_PAIR_IDS = [f"{pilot}:{defect_id}:{ref}" for pilot, defect_id, ref in _ADDED_PAIRS]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    root = tmp_path_factory.mktemp("tc811-sibling-refs-corpus")
    return load_demo_manifests_corpus(root)


def _family_platform(pilot: str) -> tuple[str, str]:
    family, platform = pilot.split("__")
    return family, platform


def _defects(pilot: str) -> dict[str, KnownDefect]:
    return {defect.id: defect for defect in load_known_defects(*_family_platform(pilot))}


def _real_fqns(corpus: ConformanceCorpus, pilot: str) -> frozenset[str]:
    fqns: set[str] = set()
    for chunk in corpus.pilots[pilot].symbol_chunks():
        fqn = extract_fqn(chunk["text"])
        if fqn:
            fqns.add(fqn)
    return frozenset(fqns)


def _carries(lines: tuple[str, ...], defect: KnownDefect) -> bool:
    """Whether a rendered disclosure tuple carries *defect* (the line embeds the entry's own,
    always-unique ``summary`` text)."""
    return any(defect.summary in line for line in lines)


def _server_tool(corpus: ConformanceCorpus, pilot: str, wrap: Callable[..., Callable[..., Any]]) -> Any:
    return wrap(corpus.store, corpus.pilots[pilot].scope)


# --------------------------------------------------------------------------------------------
# 1. the registry data: each touched entry is exactly its old refs plus the added ones
# --------------------------------------------------------------------------------------------


def test_the_card_touches_28_entries_and_adds_46_refs() -> None:
    # re-pinned by TC-853: 29 -> 28 entries and 48 -> 46 refs (TEX-PYTHON-001 and its two create_input_source refs were retired).
    assert len(_CASES) == 28
    assert len(_ADDED_PAIRS) == 46
    assert len(set(_ADDED_PAIRS)) == 46


@pytest.mark.parametrize("case", _CASES, ids=[f"{c.pilot}:{c.defect_id}" for c in _CASES])
def test_entry_refs_are_exactly_the_baseline_plus_the_added_refs(case: _Case) -> None:
    defect = _defects(case.pilot)[case.defect_id]
    assert set(defect.subject.refs) == set(case.baseline_refs) | set(case.added_refs)
    assert len(defect.subject.refs) == len(set(defect.subject.refs)), "a ref is listed twice"
    assert not set(case.baseline_refs) & set(case.added_refs), "an added ref was already a ref"


# --------------------------------------------------------------------------------------------
# 2. every added ref is a real indexed FQN and now carries the entry's disclosure line
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("pilot", "defect_id", "ref"), _ADDED_PAIRS, ids=_PAIR_IDS)
def test_added_ref_is_a_real_indexed_fqn(
    corpus: ConformanceCorpus, pilot: str, defect_id: str, ref: str
) -> None:
    assert ref in _real_fqns(corpus, pilot), f"{ref!r} is not an indexed FQN of {pilot}"


@pytest.mark.parametrize(("pilot", "defect_id", "ref"), _ADDED_PAIRS, ids=_PAIR_IDS)
def test_get_symbol_on_the_added_ref_carries_the_entrys_disclosure(
    corpus: ConformanceCorpus, pilot: str, defect_id: str, ref: str
) -> None:
    result = _server_tool(corpus, pilot, server_module._wrap_get_symbol)(fqn=ref)
    assert isinstance(result, SymbolSignature) and result.found, f"get_symbol({ref!r}) -> {result!r}"
    assert _carries(result.known_defects, _defects(pilot)[defect_id])


@pytest.mark.parametrize(("pilot", "defect_id", "ref"), _ADDED_PAIRS, ids=_PAIR_IDS)
def test_list_members_on_the_added_ref_carries_the_entrys_disclosure(
    corpus: ConformanceCorpus, pilot: str, defect_id: str, ref: str
) -> None:
    result = _server_tool(corpus, pilot, server_module._wrap_list_members)(fqn=ref)
    assert not isinstance(result, NotFound), f"list_members({ref!r}) -> {result!r}"
    assert _carries(tuple(result), _defects(pilot)[defect_id])


# --------------------------------------------------------------------------------------------
# 3. no other symbol's disclosures changed: re-evaluate every indexed chunk of every touched
# pilot against the registry as it was before this card (refs = baseline) and as it is now
# --------------------------------------------------------------------------------------------


def _registry_with_baseline_refs(pilot: str) -> list[KnownDefect]:
    baseline = {case.defect_id: case.baseline_refs for case in _CASES if case.pilot == pilot}
    return [
        dataclasses.replace(defect, subject=dataclasses.replace(defect.subject, refs=baseline[defect.id]))
        if defect.id in baseline
        else defect
        for defect in load_known_defects(*_family_platform(pilot))
    ]


def _chunk_texts(corpus: ConformanceCorpus, pilot: str) -> dict[str, str]:
    texts: dict[str, str] = {}
    for chunk in corpus.pilots[pilot].symbol_chunks():
        fqn = extract_fqn(chunk["text"])
        if fqn:
            texts.setdefault(fqn, chunk["text"])
    return texts


def _definition_chain(texts: dict[str, str], fqn: str) -> list[str]:
    """The FQNs a re-export shell's ``Defined-In:`` pointers lead to (TC-794), nearest first."""
    chain: list[str] = []
    target = extract_defined_in(texts[fqn])
    while target is not None and target in texts and target not in chain and len(chain) < 8:
        chain.append(target)
        target = extract_defined_in(texts[target])
    return chain


def _disclosed_ids_by_fqn(
    corpus: ConformanceCorpus,
    pilot: str,
    registry: list[KnownDefect],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, frozenset[str]]:
    """For every indexed symbol chunk, the registry ids ``get_symbol`` discloses for it: the chunk's
    own refs plus, for a re-export shell, its canonical definition's (TC-794)."""
    monkeypatch.setattr(get_symbol_module, "load_known_defects", lambda _family, _platform: registry)
    scope = corpus.pilots[pilot].scope
    texts = _chunk_texts(corpus, pilot)
    by_summary = {defect.summary: defect.id for defect in registry}
    disclosed: dict[str, frozenset[str]] = {}
    for fqn, text in texts.items():
        lines = known_defect_lines(scope, fqn, text)
        for target in _definition_chain(texts, fqn):
            lines += known_defect_lines(scope, target, texts[target])
        disclosed[fqn] = frozenset(
            defect_id for summary, defect_id in by_summary.items() if any(summary in line for line in lines)
        )
    return disclosed


@pytest.mark.parametrize("pilot", _PILOTS)
def test_only_the_added_refs_and_their_re_export_shells_gained_a_disclosure(
    corpus: ConformanceCorpus, pilot: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _disclosed_ids_by_fqn(corpus, pilot, _registry_with_baseline_refs(pilot), monkeypatch)
    after = _disclosed_ids_by_fqn(corpus, pilot, load_known_defects(*_family_platform(pilot)), monkeypatch)
    assert before.keys() == after.keys()

    changed = {fqn for fqn in after if after[fqn] != before[fqn]}
    assert all(before[fqn] <= after[fqn] for fqn in changed), "a disclosure was LOST, not only gained"

    added = {case.defect_id: set(case.added_refs) for case in _CASES if case.pilot == pilot}
    every_added_ref = {ref for refs in added.values() for ref in refs}
    texts = _chunk_texts(corpus, pilot)
    for fqn in changed:
        reached_through_added = fqn in every_added_ref
        shell_of_added = any(target in every_added_ref for target in _definition_chain(texts, fqn))
        assert reached_through_added or shell_of_added, (
            f"{pilot}: {fqn!r} gained {sorted(after[fqn] - before[fqn])} but is neither an added "
            "ref nor a re-export shell of one"
        )

    # and every added ref itself really did gain its entry's disclosure (it was not already reached)
    for defect_id, refs in added.items():
        for ref in refs:
            assert defect_id in after[ref]
            assert defect_id not in before[ref], f"{ref!r} already carried {defect_id} before this card"
