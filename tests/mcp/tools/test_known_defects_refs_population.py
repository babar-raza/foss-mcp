"""G3/TC-660 (REQ-G3-015): real end-to-end coverage for this card's own two changes.

1. ``search_symbols.py``'s ``_SYMBOL_DEFECT_SUBJECT_KINDS`` widened from ``{"method", "class"}`` to
   ``{"method", "class", "package"}`` (the architectural fix: a ``package``-kind known-defect entry's
   ``subject.refs`` was silently unreachable from ``search_symbols``/``lookup`` before, even though
   ``get_symbol.known_defect_lines`` has no such kind filter and already surfaced it).
2. 9 real, already-committed ``known_defects`` entries across 9 pilots gained real sibling-symbol
   names in their own ``subject.refs`` arrays, confirmed live against each pilot's real baked
   ``infra/demo-manifests`` generation (never a guess at the FQN string - this card's own text warns
   a wrong string is worse than a missing one), plus the independent ``CELLS-GO-001``/``CELLS-GO-011``
   correction (Go's real FQN convention has no package prefix, unlike the two always-broken refs the
   registry carried before).

Every test below reads the REAL, committed ``config/known_defects/*.yaml`` registry files this card
wrote (``load_known_defects`` uses its own default, production ``REGISTRY_DIR`` - nothing here is
monkeypatched) and queries REAL symbol content loaded from the real, committed
``infra/demo-manifests`` directory via ``tests.conformance.corpus.load_demo_manifests_corpus`` (copied
to a temp directory first; the repository itself is never written to). Calls go through
``foss_mcp.mcp.server``'s own ``_wrap_get_symbol``/``_wrap_search_symbols`` closures - the exact
callables a real deployed server registers as its ``get_symbol``/``search_symbols`` tools - rather than
calling ``foss_mcp.mcp.tools.get_symbol.get_symbol``/``search_symbols.search_symbols`` directly, so this
module also proves the real server wiring, not just the underlying functions.

Ambiguous-FQN findings worth recording here (this card's own "ambiguous-FQN note" anticipated exactly
this): for several symbols, ``get_symbol`` and ``search_symbols`` resolve a bare name to DIFFERENT
primary FQN strings when the real registry has both a canonical module-qualified form and a
re-export/alias shell for the same type (``PdfDevice``/``TeXOptions`` in tex/python, ``Scene`` in
3d/python, ``SlideCollection``/``NotesSlideManager`` in slides/python all show this). Confirmed live,
not assumed: both forms were added to the relevant entry's own ``refs`` array so the disclosure
reaches whichever surface a caller uses, regardless of which form that surface's own alias-collapsing
logic happens to prefer. ``TeXJob`` (tex/python) and every ``cells``/``pdf`` method/class addition
below did NOT show this disagreement - both tools already agree on one primary form - so only that one
form was added for those.

``ObjSaveOptions``/``GltfSaveOptions`` (3d/python) are genuinely, unresolvably ambiguous bare names in
the real registry (``get_symbol`` itself reports "names 2 different types in this index" for the bare
query) - two real, distinct, non-aliased types share the same short name. The entry's own repro text
names the exact import path (``from aspose.threed.formats.obj import ObjSaveOptions``), so the
qualified, unambiguous form matching that import was used, confirmed to resolve to exactly one real
type on both surfaces.
"""

from __future__ import annotations

import pytest

from foss_mcp.indexing.known_defects import load_known_defects
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.tools.get_symbol import NotFound, SymbolSignature, extract_fqn
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - the exact
    pattern ``tests/mcp/tools/test_lookup_table_guard_and_exact_predicate.py`` already established.
    Nothing in the real repository is ever written to; reading it is always allowed."""
    root = tmp_path_factory.mktemp("tc660-refs-population-corpus")
    return load_demo_manifests_corpus(root)


def _pilot_get_symbol(corpus: ConformanceCorpus, pilot_name: str, fqn: str):
    pilot = corpus.pilots[pilot_name]
    tool = server_module._wrap_get_symbol(corpus.store, pilot.scope)
    return tool(fqn=fqn)


def _pilot_search_symbols(corpus: ConformanceCorpus, pilot_name: str, query: str):
    pilot = corpus.pilots[pilot_name]
    tool = server_module._wrap_search_symbols(corpus.store, pilot.scope)
    return tool(query=query)


def _defect_ids(known_defect_lines: tuple[str, ...], registry_defects) -> set[str]:
    """Which real registry ``id``s a rendered ``known_defects`` tuple corresponds to, matched by each
    defect's own (always-unique) ``summary`` substring - the rendered line never carries the bare id
    itself, only ``summary``/``user_action``/``evidence.repro`` text (see ``_render_known_defect``)."""
    ids = set()
    for defect in registry_defects:
        if any(defect.summary in line for line in known_defect_lines):
            ids.add(defect.id)
    return ids


# --------------------------------------------------------------------------------------------
# Group A: method/class-kind additions (cells/net, cells/java, cells/python, pdf/java, pdf/cpp) -
# real end-to-end get_symbol AND search_symbols calls on the NEW ref name, through the real server
# wrapper, against the real infra/demo-manifests data.
# --------------------------------------------------------------------------------------------


def test_cells_net_save_options_surfaces_cells_net_002_via_get_symbol(corpus: ConformanceCorpus) -> None:
    result = _pilot_get_symbol(corpus, "cells__net", "SaveOptions")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("cells", "net")
    assert _defect_ids(result.known_defects, defects) == {"CELLS-NET-002"}


def test_cells_net_pdf_save_options_surfaces_cells_net_002_via_search_symbols(
    corpus: ConformanceCorpus,
) -> None:
    result = _pilot_search_symbols(corpus, "cells__net", "PdfSaveOptions")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("cells", "net")
    hit_ids: set[str] = set()
    for match in result:
        hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"CELLS-NET-002"}


def test_cells_java_save_options_surfaces_cells_java_001_via_get_symbol(corpus: ConformanceCorpus) -> None:
    result = _pilot_get_symbol(corpus, "cells__java", "SaveOptions")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("cells", "java")
    assert _defect_ids(result.known_defects, defects) == {"CELLS-JAVA-001"}


def test_cells_java_save_options_surfaces_cells_java_001_via_search_symbols(
    corpus: ConformanceCorpus,
) -> None:
    result = _pilot_search_symbols(corpus, "cells__java", "SaveOptions")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("cells", "java")
    hit_ids = set()
    for match in result:
        hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"CELLS-JAVA-001"}


def test_cells_python_cell_surfaces_cells_py_005_via_get_symbol(corpus: ConformanceCorpus) -> None:
    result = _pilot_get_symbol(corpus, "cells__python", "Cell")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("cells", "python")
    assert _defect_ids(result.known_defects, defects) == {"CELLS-PY-005"}


def test_cells_python_cell_surfaces_cells_py_005_via_search_symbols(corpus: ConformanceCorpus) -> None:
    result = _pilot_search_symbols(corpus, "cells__python", "Cell")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("cells", "python")
    hit_ids = set()
    for match in result:
        hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"CELLS-PY-005"}


def test_pdf_java_text_stamp_surfaces_pdf_java_002_only_via_get_symbol(corpus: ConformanceCorpus) -> None:
    """Confirms both the addition (PDF-JAVA-002) and the explicit non-addition this card calls for:
    PDF-JAVA-001 (WatermarkArtifact) recommends TextStamp only as a workaround, never as another
    affected symbol, so querying TextStamp must never surface PDF-JAVA-001."""
    result = _pilot_get_symbol(corpus, "pdf__java", "org.aspose.pdf.TextStamp")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("pdf", "java")
    assert _defect_ids(result.known_defects, defects) == {"PDF-JAVA-002"}


def test_pdf_java_text_stamp_surfaces_pdf_java_002_only_via_search_symbols(corpus: ConformanceCorpus) -> None:
    result = _pilot_search_symbols(corpus, "pdf__java", "org.aspose.pdf.TextStamp")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("pdf", "java")
    hit_ids = set()
    for match in result:
        hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"PDF-JAVA-002"}


def test_pdf_cpp_text_absorber_surfaces_pdf_cpp_001_via_get_symbol(corpus: ConformanceCorpus) -> None:
    result = _pilot_get_symbol(corpus, "pdf__cpp", "Aspose::Pdf::Text::TextAbsorber")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("pdf", "cpp")
    assert _defect_ids(result.known_defects, defects) == {"PDF-CPP-001"}


def test_pdf_cpp_crypto_algorithm_surfaces_pdf_cpp_001_via_search_symbols(corpus: ConformanceCorpus) -> None:
    result = _pilot_search_symbols(corpus, "pdf__cpp", "Aspose::Pdf::CryptoAlgorithm")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("pdf", "cpp")
    hit_ids = set()
    for match in result:
        hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"PDF-CPP-001"}


# --------------------------------------------------------------------------------------------
# Group B: package-kind additions (tex/python, slides/python, 3d/python) - at least one test per
# pilot proving search_symbols now surfaces the defect (the architectural fix itself) and at least
# one proving get_symbol still works too (it never needed the fix; regression guard).
# --------------------------------------------------------------------------------------------


def test_tex_python_tex_job_surfaces_tex_python_001_via_search_symbols(corpus: ConformanceCorpus) -> None:
    """Proves the architectural fix itself: TEX-PYTHON-001 is subject.kind "package", so before
    widening _SYMBOL_DEFECT_SUBJECT_KINDS this would have been silently dropped regardless of refs."""
    result = _pilot_search_symbols(corpus, "tex__python", "TeXJob")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("tex", "python")
    hit_ids = set()
    for match in result:
        hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"TEX-PYTHON-001"}


def test_tex_python_tex_job_still_surfaces_tex_python_001_via_get_symbol(corpus: ConformanceCorpus) -> None:
    """Regression guard: get_symbol never had the kind filter, so this already worked and must keep
    working unaffected by the search_symbols-only widening."""
    result = _pilot_get_symbol(corpus, "tex__python", "TeXJob")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("tex", "python")
    assert _defect_ids(result.known_defects, defects) == {"TEX-PYTHON-001"}


def test_slides_python_notes_slide_manager_surfaces_slides_py_001_via_search_symbols(
    corpus: ConformanceCorpus,
) -> None:
    result = _pilot_search_symbols(corpus, "slides__python", "NotesSlideManager")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("slides", "python")
    hit_ids = set()
    for match in result:
        hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"SLIDES-PY-001"}


def test_slides_python_slide_collection_still_surfaces_slides_py_001_via_get_symbol(
    corpus: ConformanceCorpus,
) -> None:
    result = _pilot_get_symbol(corpus, "slides__python", "SlideCollection")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("slides", "python")
    assert _defect_ids(result.known_defects, defects) == {"SLIDES-PY-001"}


def test_3d_python_scene_surfaces_all_five_package_entries_via_search_symbols(
    corpus: ConformanceCorpus,
) -> None:
    """All 5 real 3d/python package-kind entries name Scene (export/reload behavior) - a query that
    reaches the real Scene chunk must surface all 5, not merely one, now that search_symbols no
    longer drops package-kind entries."""
    result = _pilot_search_symbols(corpus, "3d__python", "Scene")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("3d", "python")
    hit_ids: set[str] = set()
    for match in result:
        if extract_fqn(match.text) in ("aspose.threed.Scene", "aspose.threed.Scene.Scene"):
            hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"3D-PYTHON-001", "3D-PYTHON-002", "3D-PYTHON-006", "3D-PYTHON-008", "3D-PYTHON-010"}


def test_3d_python_scene_still_surfaces_all_five_package_entries_via_get_symbol(
    corpus: ConformanceCorpus,
) -> None:
    result = _pilot_get_symbol(corpus, "3d__python", "Scene")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("3d", "python")
    assert _defect_ids(result.known_defects, defects) == {
        "3D-PYTHON-001",
        "3D-PYTHON-002",
        "3D-PYTHON-006",
        "3D-PYTHON-008",
        "3D-PYTHON-010",
    }


def test_3d_python_obj_save_options_surfaces_3d_python_001_and_010_via_search_symbols(
    corpus: ConformanceCorpus,
) -> None:
    """ObjSaveOptions is genuinely ambiguous as a bare name (2 real, non-aliased types share it) - the
    exact import-path-qualified form the entries' own repro text names is used, and resolves to
    exactly one real type carrying both affected entries."""
    result = _pilot_search_symbols(corpus, "3d__python", "aspose.threed.formats.obj.ObjSaveOptions")
    assert isinstance(result, list) and result, f"expected a real symbol hit, got {result!r}"
    defects = load_known_defects("3d", "python")
    hit_ids: set[str] = set()
    for match in result:
        if extract_fqn(match.text) == "aspose.threed.formats.obj.ObjSaveOptions":
            hit_ids |= _defect_ids(match.known_defects, defects)
    assert hit_ids == {"3D-PYTHON-001", "3D-PYTHON-010"}


def test_3d_python_gltf_save_options_surfaces_3d_python_002_via_get_symbol(corpus: ConformanceCorpus) -> None:
    result = _pilot_get_symbol(corpus, "3d__python", "aspose.threed.formats.gltf.GltfSaveOptions")
    assert isinstance(result, SymbolSignature) and result.found
    defects = load_known_defects("3d", "python")
    assert "3D-PYTHON-002" in _defect_ids(result.known_defects, defects)


# --------------------------------------------------------------------------------------------
# Group C: no-regression check - a pilot's existing method/class-kind entry, unaffected by the
# architectural change, still behaves identically.
# --------------------------------------------------------------------------------------------


def test_pdf_java_watermark_artifact_method_kind_entry_unaffected_by_the_architectural_change(
    corpus: ConformanceCorpus,
) -> None:
    """PDF-JAVA-001 (``org.aspose.pdf.WatermarkArtifact``, method-scoped, untouched by this card -
    its own text explicitly says TextStamp is a workaround, never another affected symbol) must
    still surface identically through both tools - the kind-set widening only ADDS "package", so a
    method-scoped entry's own behavior is untouched. Also re-confirms PDF-JAVA-001 never gained the
    TextStamp ref this card added to PDF-JAVA-002 only."""
    defects = load_known_defects("pdf", "java")

    gs_result = _pilot_get_symbol(corpus, "pdf__java", "org.aspose.pdf.WatermarkArtifact")
    assert isinstance(gs_result, SymbolSignature) and gs_result.found
    assert _defect_ids(gs_result.known_defects, defects) == {"PDF-JAVA-001"}

    ss_result = _pilot_search_symbols(corpus, "pdf__java", "org.aspose.pdf.WatermarkArtifact")
    assert isinstance(ss_result, list) and ss_result
    ss_hit_ids: set[str] = set()
    for match in ss_result:
        ss_hit_ids |= _defect_ids(match.known_defects, defects)
    assert ss_hit_ids == {"PDF-JAVA-001"}


# --------------------------------------------------------------------------------------------
# Group D: the independent cells/go correction - CELLS-GO-001/CELLS-GO-011's refs never matched
# anything (wrong FQN convention) until this card's correction; now they do.
# --------------------------------------------------------------------------------------------


def test_cells_go_001_corrected_ref_now_matches_a_real_get_symbol_cell_call(
    corpus: ConformanceCorpus,
) -> None:
    """Before this card's correction, CELLS-GO-001's ref was ``cells_foss.Cell`` - Go's real FQN
    convention (``chunk_builder._entry_fqn``: the bare name, no package prefix, confirmed against the
    real cells/go fixture and generation data) meant this literally never matched any real symbol, in
    any version of this registry. This proves the corrected bare ``Cell`` ref now does."""
    result = _pilot_get_symbol(corpus, "cells__go", "Cell")
    assert isinstance(result, SymbolSignature) and result.found
    assert result.fqn == "Cell"
    defects = load_known_defects("cells", "go")
    assert "CELLS-GO-001" in _defect_ids(result.known_defects, defects)


def test_cells_go_011_corrected_ref_now_matches_a_real_get_symbol_calculate_formula_call(
    corpus: ConformanceCorpus,
) -> None:
    result = _pilot_get_symbol(corpus, "cells__go", "CalculateFormula")
    assert isinstance(result, SymbolSignature) and result.found
    assert result.fqn == "CalculateFormula"
    defects = load_known_defects("cells", "go")
    assert "CELLS-GO-011" in _defect_ids(result.known_defects, defects)


def test_cells_go_001_ref_would_never_have_matched_under_the_old_wrong_fqn_form(
    corpus: ConformanceCorpus,
) -> None:
    """Documents the real, independent bug this card fixed: the OLD ref value
    (``cells_foss.Cell``) is not a real FQN in this pilot's index at all - proving the correction was
    necessary, not cosmetic."""
    result = _pilot_get_symbol(corpus, "cells__go", "cells_foss.Cell")
    assert isinstance(result, NotFound)
    assert result.found is False


# --------------------------------------------------------------------------------------------
# Sanity: an unrelated query in an affected pilot carries no known_defects at all.
# --------------------------------------------------------------------------------------------


def test_an_unrelated_cells_net_symbol_carries_no_known_defects(corpus: ConformanceCorpus) -> None:
    result = _pilot_get_symbol(corpus, "cells__net", "Color")
    assert isinstance(result, SymbolSignature) and result.found
    assert result.known_defects == ()
