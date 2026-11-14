"""TC-795 (G3/REQ-G3-015): known-defect disclosures are alias-aware and member-bound.

Before this card a ``config/known_defects`` entry matched a chunk only by an EXACT FQN in
``subject.refs``. A defect that names the real defining class (or only the public re-export alias)
did not surface when a user reached the symbol by the OTHER name: email/python ``MapiMessage``
(RD2-136), note/python ``DocumentVisitor`` / ``RichText`` (RD2-159, RD2-165), words/python
``Document.get_text`` (RD2-282), 3d/python ``Node`` / ``Mesh`` (3D-PYTHON-013) and cells/python
``Cell`` / ``Workbook`` (CELLS-PY-005). And a ``find_examples`` answer whose snippet only CALLS a
member (RD2-237: ``pres.getSlides().addClone(slide)``) never carried the defect of the type that
declares it, because the type's own name never appears in the snippet.

Three parts:

(a) unit tests of ``extract_aliases``, of the widened ``known_defect_lines`` match and of
    ``called_member_defect_lines``, over small synthetic chunks and a ``tmp_path`` registry;
(b) the RD2-136/159/165/282/237 cases against the REAL committed corpora and registries;
(c) a whole-registry reachability sweep: for EVERY method/class ref of EVERY committed registry
    entry, the disclosure must surface through ``get_symbol``, ``list_members`` and
    ``search_symbols`` on EVERY name a user can use for that symbol (the ref itself, the definition
    chunk's ``Exported-As:`` names, the shell chunk's ``Defined-In:`` name, and every chunk that
    names the ref that way). ``package``/``commit`` kinds are surfaced through
    ``get_product_reference`` and are out of this sweep.

Offline and deterministic: the real corpora are read through a temp copy, nothing is written to
``infra/demo-manifests`` or ``config/known_defects``.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from foss_mcp.indexing import known_defects as known_defects_module
from foss_mcp.indexing.known_defects import load_known_defects, registry_path_for
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import _example_known_defects, _real_symbol_fqns, find_examples
from foss_mcp.mcp.tools.get_symbol import (
    NotFound,
    SymbolSignature,
    called_member_defect_lines,
    called_member_owners,
    extract_aliases,
    get_symbol,
    known_defect_lines,
)
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.mcp.tools.search_symbols import SymbolMatch, search_symbols
from foss_mcp.mcp.tools.search_symbols import known_defect_lines as search_known_defect_lines
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.conformance.corpus import load_demo_manifests_corpus

SCOPE = Scope(family="demo", platform="python")
_GENERATION_ID = "gen-tc795"

ALIAS = "pkg.api.Widget"
DEFINITION = "pkg.api.widget.Widget"
OTHER = "pkg.api.other.Gadget"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


# --------------------------------------------------------------------------------------------
# synthetic fixtures
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks - the pattern ``test_known_defects_in_get_symbol.py``
    (TC-639) established."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="acme/demo", commit="tc795"),
                evidence_refs=(),
                title="demo/python API surface",
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


def _chunk(
    fqn: str,
    *,
    exported_as: tuple[str, ...] = (),
    defined_in: str | None = None,
    methods: tuple[str, ...] = (),
    commit: str | None = None,
) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", "Kind: class"]
    if defined_in is not None:
        lines.append(f"Defined-In: {defined_in}")
    lines.extend(f"Exported-As: {name}" for name in exported_as)
    if methods:
        lines += ["", "Methods:", *(f"  - {item}" for item in methods)]
    if commit is not None:
        lines += ["", f"Source-Commit: {commit}"]
    return "\n".join(lines) + "\n"


def _bodies() -> tuple[str, ...]:
    return (
        _chunk(
            DEFINITION,
            exported_as=(ALIAS,),
            methods=("spin(speed: int) -> None", "stop() -> None"),
            commit=COMMIT_A,
        ),
        _chunk(ALIAS, defined_in=DEFINITION, commit=COMMIT_A),
        _chunk(OTHER, methods=("stop() -> None", "polish() -> None"), commit=COMMIT_A),
    )


@pytest.fixture(scope="module")
def store() -> _InMemoryStore:
    return _InMemoryStore(_bodies())


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "known_defects"
    directory.mkdir()
    monkeypatch.setattr(known_defects_module, "REGISTRY_DIR", directory)
    return directory


def _defect(
    defect_id: str,
    refs: list[str],
    *,
    summary: str | None = None,
    indexed_commit: str | None = None,
) -> dict[str, Any]:
    applies_to: dict[str, Any] = {"package_versions": ["1.0"]}
    if indexed_commit is not None:
        applies_to = {"indexed_commit": indexed_commit}
    return {
        "id": defect_id,
        "subject": {"kind": "method", "refs": refs},
        "applies_to": applies_to,
        "summary": summary or f"Summary describing {defect_id}.",
        "user_action": f"Recommended action for {defect_id}.",
        "evidence": {
            "repro": f"Repro steps for {defect_id}.",
            "verified_on": {"package": "demo", "version": "1.0", "date": "2026-11-14"},
        },
        "status": "open",
    }


def _write_registry(registry_dir: Path, defects: list[dict[str, Any]]) -> None:
    document = {"schema_version": 1, "pilot": "demo_python", "defects": defects}
    path = registry_path_for("demo", "python", base_dir=registry_dir)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _documents(store: _InMemoryStore) -> dict[str, dict[str, str]]:
    return store._manifest.payload["lexical_index"]["documents"]


# --------------------------------------------------------------------------------------------
# (a) extract_aliases
# --------------------------------------------------------------------------------------------


def test_extract_aliases_of_a_definition_is_its_exported_as_names() -> None:
    text = _chunk(DEFINITION, exported_as=(ALIAS, "pkg.Widget"))

    assert extract_aliases(text) == (ALIAS, "pkg.Widget")


def test_extract_aliases_of_a_shell_is_its_defined_in_name() -> None:
    assert extract_aliases(_chunk(ALIAS, defined_in=DEFINITION)) == (DEFINITION,)


def test_extract_aliases_of_a_plain_chunk_is_empty() -> None:
    assert extract_aliases(_chunk(OTHER)) == ()


def test_extract_aliases_never_includes_the_chunks_own_fqn_and_never_repeats() -> None:
    text = _chunk(DEFINITION, exported_as=(DEFINITION, ALIAS, ALIAS), defined_in=ALIAS)

    assert extract_aliases(text) == (ALIAS,)


# --------------------------------------------------------------------------------------------
# (a) the widened match
# --------------------------------------------------------------------------------------------


def test_known_defect_lines_without_aliases_is_the_exact_pre_tc795_match(registry_dir: Path) -> None:
    _write_registry(registry_dir, [_defect("DEMO-001", [ALIAS])])

    assert known_defect_lines(SCOPE, DEFINITION, _chunk(DEFINITION, exported_as=(ALIAS,))) == ()
    assert len(known_defect_lines(SCOPE, ALIAS, _chunk(ALIAS))) == 1


def test_known_defect_lines_matches_a_defect_naming_an_alias(registry_dir: Path) -> None:
    _write_registry(registry_dir, [_defect("DEMO-001", [ALIAS])])
    text = _chunk(DEFINITION, exported_as=(ALIAS,))

    lines = known_defect_lines(SCOPE, DEFINITION, text, extract_aliases(text))

    assert len(lines) == 1
    assert "Summary describing DEMO-001." in lines[0]


def test_known_defect_lines_matches_a_defect_naming_the_definition_from_a_shell(registry_dir: Path) -> None:
    _write_registry(registry_dir, [_defect("DEMO-002", [DEFINITION])])
    text = _chunk(ALIAS, defined_in=DEFINITION)

    assert len(known_defect_lines(SCOPE, ALIAS, text, extract_aliases(text))) == 1


def test_a_defect_naming_neither_the_fqn_nor_an_alias_never_matches(registry_dir: Path) -> None:
    _write_registry(registry_dir, [_defect("DEMO-003", [OTHER])])
    text = _chunk(DEFINITION, exported_as=(ALIAS,))

    assert known_defect_lines(SCOPE, DEFINITION, text, extract_aliases(text)) == ()


def test_an_entry_naming_both_the_fqn_and_an_alias_is_rendered_once(registry_dir: Path) -> None:
    _write_registry(registry_dir, [_defect("DEMO-004", [ALIAS, DEFINITION])])
    text = _chunk(DEFINITION, exported_as=(ALIAS,))

    assert len(known_defect_lines(SCOPE, DEFINITION, text, extract_aliases(text))) == 1


def test_commit_scoping_still_applies_to_an_alias_match(registry_dir: Path) -> None:
    _write_registry(
        registry_dir,
        [
            _defect("DEMO-005", [ALIAS], indexed_commit=COMMIT_B),
            _defect("DEMO-006", [ALIAS], indexed_commit=COMMIT_A),
        ],
    )
    text = _chunk(DEFINITION, exported_as=(ALIAS,), commit=COMMIT_A)

    lines = known_defect_lines(SCOPE, DEFINITION, text, extract_aliases(text))

    assert len(lines) == 1
    assert "DEMO-006" in lines[0]


def test_search_symbols_known_defect_lines_is_alias_aware_too(registry_dir: Path) -> None:
    _write_registry(registry_dir, [_defect("DEMO-007", [ALIAS])])

    assert search_known_defect_lines(SCOPE, DEFINITION, _chunk(DEFINITION)) == ()
    assert len(search_known_defect_lines(SCOPE, DEFINITION, _chunk(DEFINITION), (ALIAS,))) == 1


# --------------------------------------------------------------------------------------------
# (a) through the tools, over a synthetic generation
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ref", [ALIAS, DEFINITION])
@pytest.mark.parametrize("asked", [ALIAS, DEFINITION])
def test_get_symbol_discloses_through_either_name(
    store: _InMemoryStore, registry_dir: Path, ref: str, asked: str
) -> None:
    _write_registry(registry_dir, [_defect("DEMO-010", [ref])])

    result = get_symbol(store, SCOPE, asked)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert len(result.known_defects) == 1, (ref, asked)
    assert "Summary describing DEMO-010." in result.known_defects[0]


@pytest.mark.parametrize("ref", [ALIAS, DEFINITION])
@pytest.mark.parametrize("asked", [ALIAS, DEFINITION])
def test_list_members_discloses_through_either_name(
    store: _InMemoryStore, registry_dir: Path, ref: str, asked: str
) -> None:
    _write_registry(registry_dir, [_defect("DEMO-011", [ref])])

    members = list_members(store, SCOPE, asked)  # type: ignore[arg-type]

    assert not isinstance(members, NotFound)
    assert any("Summary describing DEMO-011." in line for line in members), (ref, asked)


@pytest.mark.parametrize("ref", [ALIAS, DEFINITION])
def test_search_symbols_discloses_on_the_hit_whichever_name_the_defect_uses(
    store: _InMemoryStore, registry_dir: Path, ref: str
) -> None:
    _write_registry(registry_dir, [_defect("DEMO-012", [ref])])

    hits = search_symbols(store, SCOPE, "Widget")  # type: ignore[arg-type]

    assert isinstance(hits, list) and hits
    widget_hits = [hit for hit in hits if "Widget" in hit.text.splitlines()[2]]
    assert widget_hits
    for hit in widget_hits:
        assert isinstance(hit, SymbolMatch)
        assert len(hit.known_defects) == 1, hit.text.splitlines()[2]


def test_an_unrelated_type_never_gains_the_defect(store: _InMemoryStore, registry_dir: Path) -> None:
    _write_registry(registry_dir, [_defect("DEMO-013", [ALIAS])])

    result = get_symbol(store, SCOPE, OTHER)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert result.known_defects == ()


def test_a_resolved_member_name_carries_the_owning_types_defect_through_an_alias(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("DEMO-014", [ALIAS])])

    result = get_symbol(store, SCOPE, f"{DEFINITION}.spin")  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert len(result.known_defects) == 1


# --------------------------------------------------------------------------------------------
# (a) member -> owner binding for examples
# --------------------------------------------------------------------------------------------


def test_called_member_owners_binds_an_accessed_member_to_every_declaring_type(store: _InMemoryStore) -> None:
    owners = called_member_owners(_documents(store), "w = make()\nw.spin(3)\nw.stop()")

    assert (DEFINITION, (ALIAS,), "spin") in owners
    assert (DEFINITION, (ALIAS,), "stop") in owners
    assert (OTHER, (), "stop") in owners


def test_called_member_owners_ignores_a_bare_name_and_an_undeclared_member(store: _InMemoryStore) -> None:
    assert called_member_owners(_documents(store), "spin(3)\nw.nothing_here()") == ()


def test_called_member_owners_reads_arrow_and_scope_access(store: _InMemoryStore) -> None:
    owners = called_member_owners(_documents(store), "p->spin(1); Widget::stop();")

    assert {member for _fqn, _aliases, member in owners} == {"spin", "stop"}


def test_a_called_member_discloses_the_defect_of_its_owner_when_the_defect_names_that_member(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(
        registry_dir,
        [_defect("DEMO-020", [ALIAS], summary="Widget.spin() ignores the speed argument.")],
    )
    text = "Example: Spin\n"

    lines = called_member_defect_lines(SCOPE, _documents(store), "w = make()\nw.spin(3)", text)

    assert len(lines) == 1 and "spin()" in lines[0]


def test_a_called_member_the_defect_is_not_about_does_not_attach_it(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    """``stop`` is declared by Widget but the defect is about ``spin``: a snippet that only stops a
    widget must not be warned about spinning (and ``Gadget.stop`` must not drag Widget's defect in)."""
    _write_registry(
        registry_dir,
        [_defect("DEMO-021", [ALIAS], summary="Widget.spin() ignores the speed argument.")],
    )

    assert called_member_defect_lines(SCOPE, _documents(store), "w.stop()", "Example: Stop\n") == ()
    assert called_member_defect_lines(SCOPE, _documents(store), "g.polish()", "Example: Polish\n") == ()


def test_member_binding_keeps_commit_scoping(store: _InMemoryStore, registry_dir: Path) -> None:
    _write_registry(
        registry_dir,
        [_defect("DEMO-022", [DEFINITION], summary="Widget.spin() is broken.", indexed_commit=COMMIT_B)],
    )
    on_a = f"Example: Spin\n\nSource-Commit: {COMMIT_A}\n"
    on_b = f"Example: Spin\n\nSource-Commit: {COMMIT_B}\n"

    assert called_member_defect_lines(SCOPE, _documents(store), "w.spin(1)", on_a) == ()
    assert len(called_member_defect_lines(SCOPE, _documents(store), "w.spin(1)", on_b)) == 1


def test_example_known_defects_appends_member_bound_lines_only_when_documents_are_given(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(
        registry_dir, [_defect("DEMO-023", [ALIAS], summary="Widget.spin() ignores the speed argument.")]
    )
    documents = _documents(store)
    text = "Example: Spin\n"
    code = "w = make()\nw.spin(3)"

    without = _example_known_defects(SCOPE, _real_symbol_fqns(documents), code, text)
    with_members = _example_known_defects(SCOPE, _real_symbol_fqns(documents), code, text, documents)

    assert without == ()
    assert len(with_members) == 1


def test_example_known_defects_does_not_repeat_a_line_the_literal_scan_already_found(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(
        registry_dir, [_defect("DEMO-024", [ALIAS], summary="Widget.spin() ignores the speed argument.")]
    )
    documents = _documents(store)
    code = "w = Widget()\nw.spin(3)"

    lines = _example_known_defects(SCOPE, _real_symbol_fqns(documents), code, "Example: Spin\n", documents)

    assert len(lines) == 1


# --------------------------------------------------------------------------------------------
# (b) the named round-2 rows, against the REAL corpora and registries
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory):
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("tc795-alias-aware-corpus"))


def _first_line_with(lines: tuple[str, ...] | list[str], needle: str) -> str | None:
    return next((line for line in lines if needle in line), None)


def _discloses(corpus, pilot: str, fqn: str, needle: str) -> bool:
    """True when ``get_symbol`` AND ``list_members`` for *fqn* both carry a known-defect line
    containing *needle*."""
    scope = corpus.pilots[pilot].scope
    signature = get_symbol(corpus.store, scope, fqn)
    members = list_members(corpus.store, scope, fqn)
    if isinstance(signature, NotFound) or isinstance(members, NotFound):
        return False
    return _first_line_with(signature.known_defects, needle) is not None and (
        _first_line_with(members, needle) is not None
    )


@pytest.mark.parametrize(
    "fqn",
    ["aspose.email_foss.msg.MapiMessage", "aspose.email_foss.msg.mapi_message.MapiMessage"],
)
def test_rd2_136_mapi_message_discloses_the_bcc_header_defect_through_both_names(corpus, fqn: str) -> None:
    assert _discloses(corpus, "email__python", fqn, "Bcc")


@pytest.mark.parametrize("fqn", ["aspose.note.DocumentVisitor", "aspose.note.model.DocumentVisitor"])
def test_rd2_159_document_visitor_discloses_through_both_names(corpus, fqn: str) -> None:
    ids = _entry_summaries(corpus, "note__python", "NOTE-PY-002")
    assert ids, "NOTE-PY-002 is no longer in the registry"
    assert _discloses(corpus, "note__python", fqn, ids[0][:60])


@pytest.mark.parametrize("fqn", ["aspose.note.RichText", "aspose.note.model.RichText"])
def test_rd2_165_rich_text_discloses_through_both_names(corpus, fqn: str) -> None:
    ids = _entry_summaries(corpus, "note__python", "NOTE-PY-008")
    assert ids, "NOTE-PY-008 is no longer in the registry"
    assert _discloses(corpus, "note__python", fqn, ids[0][:60])


@pytest.mark.parametrize(
    "fqn",
    [
        "aspose.words_foss.Document",
        "aspose.words_foss.document.Document",
    ],
)
def test_rd2_282_document_get_text_defect_discloses_through_both_names(corpus, fqn: str) -> None:
    summary = _entry_summaries(corpus, "words__python", "WORDS-PY-008")
    assert summary, "WORDS-PY-008 is no longer in the registry"
    assert _discloses(corpus, "words__python", fqn, summary[0][:60])


@pytest.mark.parametrize(
    "fqn",
    [
        "aspose.threed.Node",
        "aspose.threed.Node.Node",
        "aspose.threed.entities.Mesh",
        "aspose.threed.entities.Mesh.Mesh",
    ],
)
def test_3d_python_013_discloses_through_the_alias_and_the_definition(corpus, fqn: str) -> None:
    summary = _entry_summaries(corpus, "3d__python", "3D-PYTHON-013")
    assert summary, "3D-PYTHON-013 is no longer in the registry"
    assert _discloses(corpus, "3d__python", fqn, summary[0][:60])


@pytest.mark.parametrize(
    "fqn",
    [
        "aspose.cells_foss.Cell",
        "aspose.cells_foss.cell.Cell",
        "aspose.cells_foss.Workbook",
        "aspose.cells_foss.workbook.Workbook",
    ],
)
def test_cells_py_005_discloses_through_the_alias_and_the_definition(corpus, fqn: str) -> None:
    summary = _entry_summaries(corpus, "cells__python", "CELLS-PY-005")
    assert summary, "CELLS-PY-005 is no longer in the registry"
    assert _discloses(corpus, "cells__python", fqn, summary[0][:60])


def test_cells_py_005_reaches_a_search_symbols_hit_on_the_public_alias(corpus) -> None:
    scope = corpus.pilots["cells__python"].scope
    summary = _entry_summaries(corpus, "cells__python", "CELLS-PY-005")[0][:60]

    hits = search_symbols(corpus.store, scope, "aspose.cells_foss.Cell")

    assert isinstance(hits, list)
    assert any(_first_line_with(hit.known_defects, summary) for hit in hits)


def _entry_summaries(corpus, pilot: str, defect_id: str) -> list[str]:
    family, platform = pilot.split("__")
    return [defect.summary for defect in load_known_defects(family, platform) if defect.id == defect_id]


def test_rd2_237_the_clone_example_carries_the_islide_collection_add_clone_defect(corpus) -> None:
    scope = corpus.pilots["slides__java"].scope
    summary = _entry_summaries(corpus, "slides__java", "SLIDES-JAVA-010B")
    assert summary, "SLIDES-JAVA-010B is no longer in the registry"

    matches = find_examples(corpus.store, scope, "Clone a Slide")

    assert not isinstance(matches, list) or matches
    assert isinstance(matches, list)
    clone = next(match for match in matches if "addClone" in match.snippet)
    assert _first_line_with(clone.known_defects, summary[0][:60]) is not None, clone.known_defects


def test_rd2_237_the_clone_example_found_by_a_lexical_query_carries_it_too(corpus) -> None:
    scope = corpus.pilots["slides__java"].scope
    summary = _entry_summaries(corpus, "slides__java", "SLIDES-JAVA-010B")[0][:60]

    matches = find_examples(corpus.store, scope, "clone")

    assert isinstance(matches, list)
    clone = next(match for match in matches if "addClone" in match.snippet)
    assert _first_line_with(clone.known_defects, summary) is not None


def test_rd2_237_get_symbol_on_the_add_clone_member_also_carries_the_type_defect(corpus) -> None:
    scope = corpus.pilots["slides__java"].scope
    summary = _entry_summaries(corpus, "slides__java", "SLIDES-JAVA-010B")[0][:60]

    result = get_symbol(corpus.store, scope, "org.aspose.slides.foss.ISlideCollection.addClone")

    assert isinstance(result, SymbolSignature)
    assert _first_line_with(result.known_defects, summary) is not None


def test_an_example_with_no_member_a_defect_is_about_gains_no_member_bound_line(corpus) -> None:
    """The same real slides/java registry, a snippet that only reads ``getSlides().get(0)``: neither
    ``get`` nor ``getSlides`` is named by SLIDES-JAVA-010B's disclosure, so it is not attached."""
    scope = corpus.pilots["slides__java"].scope
    documents = {
        str(index): document for index, document in enumerate(corpus.pilots["slides__java"].documents)
    }

    lines = called_member_defect_lines(
        scope, documents, "ISlide slide = pres.getSlides().get(0);", "Example: Read\n"
    )

    assert _first_line_with(lines, "addEmptySlide()/insertEmptySlide()/addClone()") is None


# --------------------------------------------------------------------------------------------
# (c) the whole-registry reachability sweep
# --------------------------------------------------------------------------------------------


def _fqn_of(text: str) -> str | None:
    match = re.search(r"^FQN:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match is not None else None


def _sweep(corpus) -> tuple[int, list[tuple[str, str, str, str, str]], list[tuple[str, str, str]]]:
    """``(pairs checked, unreachable, refs naming a symbol absent from the index)``.

    For every committed registry entry of ``method``/``class`` kind and every one of its refs, the names
    checked are the ref itself, the aliases its own chunk declares (``Exported-As:`` / ``Defined-In:``)
    and every chunk that names the ref as ITS alias - i.e. every FQN a user can reach that symbol by.
    Each name is asked of ``get_symbol``, ``list_members`` and ``search_symbols``."""
    pairs = 0
    unreachable: list[tuple[str, str, str, str, str]] = []
    missing: list[tuple[str, str, str]] = []
    for pilot_name, pilot in sorted(corpus.pilots.items()):
        family, platform = pilot_name.split("__")
        defects = [
            defect
            for defect in load_known_defects(family, platform)
            if defect.subject.kind in ("method", "class")
        ]
        if not defects:
            continue
        by_fqn = {}
        for document in pilot.documents:
            fqn = _fqn_of(document["text"])
            if fqn is not None:
                by_fqn[fqn] = document["text"]
        for defect in defects:
            for ref in defect.subject.refs:
                if ref not in by_fqn:
                    missing.append((pilot_name, defect.id, ref))
                    continue
                names = {ref}
                names.update(name for name in extract_aliases(by_fqn[ref]) if name in by_fqn)
                names.update(fqn for fqn, text in by_fqn.items() if ref in extract_aliases(text))
                for name in sorted(names):
                    pairs += 1
                    for tool, reached in _reached(corpus, pilot.scope, name, defect.summary):
                        if not reached:
                            unreachable.append((tool, pilot_name, defect.id, ref, name))
    return pairs, unreachable, missing


def _reached(corpus, scope: Scope, name: str, summary: str) -> list[tuple[str, bool]]:
    signature = get_symbol(corpus.store, scope, name)
    members = list_members(corpus.store, scope, name)
    results = [
        (
            "get_symbol",
            not isinstance(signature, NotFound)
            and _first_line_with(signature.known_defects, summary) is not None,
        ),
        (
            "list_members",
            not isinstance(members, NotFound) and _first_line_with(members, summary) is not None,
        ),
    ]
    hits = search_symbols(corpus.store, scope, name, top_k=25)
    if isinstance(hits, list):
        mine = [hit for hit in hits if _fqn_of(hit.text) == name]
        results.append(
            (
                "search_symbols",
                bool(mine) and all(_first_line_with(hit.known_defects, summary) is not None for hit in mine),
            )
        )
    else:
        results.append(("search_symbols", False))
    return results


@pytest.fixture(scope="module")
def sweep(corpus):
    return _sweep(corpus)


def test_the_sweep_is_not_vacuous(sweep) -> None:
    pairs, _unreachable, _missing = sweep

    # 142 method/class refs expand to 158 (ref, name) pairs on the 2026-11-14 registry.
    assert pairs >= 150


def test_every_method_and_class_ref_names_a_symbol_in_the_index(sweep) -> None:
    """Reported, never repaired here: a ref that names nothing in the index is a data defect for the
    registry's owner (``tests/indexing/test_known_defects_completeness.py`` pins the same)."""
    _pairs, _unreachable, missing = sweep

    assert missing == []


def test_every_registry_disclosure_is_reachable_through_every_name_of_its_symbol(sweep) -> None:
    _pairs, unreachable, _missing = sweep

    assert unreachable == [], "\n".join(str(item) for item in unreachable)
