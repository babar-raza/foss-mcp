"""G3/TC-670 (REQ-G3-011): wiring the known-defects registry into ``find_examples`` - the one
surface among ``get_symbol``/``search_symbols``/``get_product_reference``/
``report_index_freshness``/``find_examples`` that had ZERO wiring before this card (confirmed by
grepping ``known_defect`` in ``find_examples.py`` before this card: no hits).

Mirrors the real testing PATTERN already established in ``test_known_defects_in_get_symbol.py``
and ``test_known_defects_in_search_symbols.py``: in-memory generations built from hand-written
chunks, a ``tmp_path``-scoped registry file (``known_defects.REGISTRY_DIR`` monkeypatched, never
the real, committed ``config/known_defects/*.yaml``) - so every test here proves the WIRING itself
against data this file fully controls, with no dependency on which real defects happen to be
committed on any given day.

The one real design question this card's own text left to the worker: an ``ExampleMatch``'s own
``fqn`` is a pseudo ``Example: <title>`` name (never a real symbol FQN that any real registry
entry's ``subject.refs`` could ever name), so a literal "look up this match's own fqn" approach
would be permanently dormant against any real data. Instead, ``find_examples._example_known_defects``
scans the matched example's own CODE SNIPPET (never surrounding prose - the identical scoping
``relevance.code_anchored`` already enforces) for a real symbol name (the same mechanism as this
card's widened code anchor, ``relevance.known_symbol_names``), and discloses every known-defect
entry that applies to it through the real ``get_symbol.known_defect_lines`` - tried both as the
symbol's bare short name and as its real, fully-qualified FQN, because the real committed registry
uses either form depending on the pilot (confirmed directly against ``config/known_defects/*.yaml``:
cells/typescript's own entries use the bare ``Workbook``, cells/net's own use the qualified
``Aspose.Cells_FOSS.Workbook``, for the identical real class).

Offline and deterministic: no clock, no network, no embedding gateway (the lexical branch alone is
exercised; ``find_examples``'s own vector fallback degrades to its honest ``not_configured``/empty
result when no vector index is published, exactly as a real lexical-only deployment does).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from foss_mcp.indexing import known_defects as known_defects_module
from foss_mcp.indexing.known_defects import registry_path_for
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import ExampleMatch, find_examples
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc670"

DOCUMENT_FQN = "Aspose.Pdf.Document"
PAGE_FQN = "Aspose.Pdf.Page"
COMMIT_MATCH = "a" * 40
COMMIT_OTHER = "b" * 40

TITLE = "Assemble a Document Bundle"
CODE = 'var document = new Document();\ndocument.Save("out.pdf");'
# "document bundle" shares two words with the title - admitted through the existing title anchor,
# never through the code anchor - so these tests prove known_defects wiring independent of which
# admission tier let the match through, exactly as the card asks for both the exact AND the
# lexical branch.
LEXICAL_QUERY = "document bundle"


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    exact pattern ``test_known_defects_in_get_symbol.py``/``test_known_defects_in_search_symbols.py``
    already established."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="tc670"),
                evidence_refs=(),
                title="pdf/net API surface",
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


def _symbol(fqn: str) -> str:
    """A minimal real symbol chunk's text - its own ``FQN:`` line is all ``_real_symbol_fqns`` reads."""
    return f"## {fqn}\n\nFQN: {fqn}\nKind: class_declaration\n"


def _example(title: str, code: str, *, prose: str = "", commit: str = COMMIT_MATCH) -> str:
    """One verified-example chunk's text, optionally with a PROSE line before the ``Example:``
    marker - ``_extract_example`` only ever returns text AFTER the marker, so prose is part of the
    chunk's own tokens but never part of the code the known-defects wiring (and the code anchor)
    scans."""
    prose_line = f"{prose}\n" if prose else ""
    return f"FQN: Example: {title}\nKind: verified_example\n{prose_line}Example:\n{code}\nSource-Commit: {commit}\n"


@pytest.fixture(scope="module")
def store() -> _InMemoryStore:
    return _InMemoryStore((_symbol(DOCUMENT_FQN), _symbol(PAGE_FQN), _example(TITLE, CODE)))


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``tmp_path``-scoped stand-in for the real, committed ``config/known_defects/`` directory -
    never the real one. ``REGISTRY_DIR`` is read fresh on every ``load_known_defects`` call, so
    monkeypatching the module attribute is enough; no file exists here unless a test writes one."""
    directory = tmp_path / "known_defects"
    directory.mkdir()
    monkeypatch.setattr(known_defects_module, "REGISTRY_DIR", directory)
    return directory


def _defect(defect_id: str, ref: str, *, package_versions: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": defect_id,
        "subject": {"kind": "class", "refs": [ref]},
        "applies_to": {"package_versions": package_versions or ["26.7.0"]},
        "summary": f"Summary describing {defect_id}.",
        "user_action": f"Recommended action for {defect_id}.",
        "evidence": {
            "repro": f"Repro steps for {defect_id}.",
            "verified_on": {"package": "Aspose.Pdf", "version": "26.7.0", "date": "2026-10-15"},
        },
        "status": "open",
    }


def _write_registry(
    registry_dir: Path, defects: list[dict[str, Any]], *, family: str = "pdf", platform: str = "net"
) -> None:
    document = {"schema_version": 1, "pilot": f"{family}_{platform}", "defects": defects}
    path = registry_path_for(family, platform, base_dir=registry_dir)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _one_match(result: object) -> ExampleMatch:
    assert isinstance(result, list) and len(result) == 1, result
    return result[0]


# --------------------------------------------------------------------------------------------
# the exact-title branch surfaces a known defect for the REAL symbol the example's own code names
# --------------------------------------------------------------------------------------------


def test_exact_branch_surfaces_known_defect_via_the_qualified_fqn(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-001", DOCUMENT_FQN)])

    match = _one_match(find_examples(store, SCOPE, "Example: " + TITLE))  # type: ignore[arg-type]

    assert match.source == "exact"
    assert len(match.known_defects) == 1
    assert "Summary describing PDF-NET-001." in match.known_defects[0]
    assert "Recommended action for PDF-NET-001." in match.known_defects[0]


def test_exact_branch_surfaces_known_defect_via_the_bare_short_name(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    """The real committed registry uses EITHER form depending on the pilot (cells/typescript's own
    entries use the bare short name) - this must work too, not only the qualified form above."""
    _write_registry(registry_dir, [_defect("PDF-NET-002", "Document")])

    match = _one_match(find_examples(store, SCOPE, "Example: " + TITLE))  # type: ignore[arg-type]

    assert len(match.known_defects) == 1
    assert "Summary describing PDF-NET-002." in match.known_defects[0]


# --------------------------------------------------------------------------------------------
# the lexical branch wires it too - a SEPARATE real call, not the same one asserted twice
# --------------------------------------------------------------------------------------------


def test_lexical_branch_surfaces_known_defect_via_the_qualified_fqn(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-003", DOCUMENT_FQN)])

    match = _one_match(find_examples(store, SCOPE, LEXICAL_QUERY))  # type: ignore[arg-type]

    assert match.source == "lexical"
    assert len(match.known_defects) == 1
    assert "Summary describing PDF-NET-003." in match.known_defects[0]


def test_lexical_branch_surfaces_known_defect_via_the_bare_short_name(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-004", "Document")])

    match = _one_match(find_examples(store, SCOPE, LEXICAL_QUERY))  # type: ignore[arg-type]

    assert match.source == "lexical"
    assert len(match.known_defects) == 1
    assert "Summary describing PDF-NET-004." in match.known_defects[0]


# --------------------------------------------------------------------------------------------
# the empty-by-default case, and scoping: a defect for a DIFFERENT symbol never surfaces
# --------------------------------------------------------------------------------------------


def test_match_with_no_disclosed_defect_has_known_defects_empty_tuple(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    # registry_dir exists but no file is written - load_known_defects treats that as "none recorded".
    match = _one_match(find_examples(store, SCOPE, "Example: " + TITLE))  # type: ignore[arg-type]
    assert match.known_defects == ()


def test_a_defect_for_a_different_symbol_never_surfaces(store: _InMemoryStore, registry_dir: Path) -> None:
    """PAGE_FQN is a real symbol in this generation's own index, but the example's code never
    names it - its defect must never leak onto an unrelated example."""
    _write_registry(registry_dir, [_defect("PDF-NET-005", PAGE_FQN)])

    match = _one_match(find_examples(store, SCOPE, "Example: " + TITLE))  # type: ignore[arg-type]

    assert match.known_defects == ()


# --------------------------------------------------------------------------------------------
# code-only scoping: a symbol name present only in PROSE (never the code) does not disclose -
# the identical discipline relevance.code_anchored already enforces for the admission tier itself.
# --------------------------------------------------------------------------------------------


def test_symbol_name_present_only_in_prose_does_not_disclose(registry_dir: Path) -> None:
    prose_title = "Assemble a Report Bundle"
    prose_only_store = _InMemoryStore(
        (
            _symbol(DOCUMENT_FQN),
            _example(
                prose_title,
                'page = file.Open()\npage.Save("out.pdf")',
                prose="This uses a Document somewhere in its implementation.",
            ),
        )
    )
    _write_registry(registry_dir, [_defect("PDF-NET-006", DOCUMENT_FQN)])

    match = _one_match(find_examples(prose_only_store, SCOPE, "Example: " + prose_title))  # type: ignore[arg-type]

    assert match.known_defects == ()


# --------------------------------------------------------------------------------------------
# commit scoping still works through this new wiring - the identical rule known_defect_lines
# already enforces for get_symbol/search_symbols.
# --------------------------------------------------------------------------------------------


def test_commit_scoped_defect_respects_the_examples_own_source_commit(registry_dir: Path) -> None:
    commit_store = _InMemoryStore((_symbol(DOCUMENT_FQN), _example(TITLE, CODE, commit=COMMIT_MATCH)))
    _write_registry(
        registry_dir,
        [_defect("PDF-NET-007", DOCUMENT_FQN) | {"applies_to": {"indexed_commit": COMMIT_OTHER}}],
    )

    match = _one_match(find_examples(commit_store, SCOPE, "Example: " + TITLE))  # type: ignore[arg-type]

    assert match.known_defects == ()


def test_commit_scoped_defect_surfaces_when_the_examples_own_commit_matches(registry_dir: Path) -> None:
    commit_store = _InMemoryStore((_symbol(DOCUMENT_FQN), _example(TITLE, CODE, commit=COMMIT_MATCH)))
    _write_registry(
        registry_dir,
        [_defect("PDF-NET-008", DOCUMENT_FQN) | {"applies_to": {"indexed_commit": COMMIT_MATCH}}],
    )

    match = _one_match(find_examples(commit_store, SCOPE, "Example: " + TITLE))  # type: ignore[arg-type]

    assert len(match.known_defects) == 1
    assert "Summary describing PDF-NET-008." in match.known_defects[0]


# --------------------------------------------------------------------------------------------
# the vector branch is NOT wired by this card - it has no vector index published here at all, so
# this is also a regression guard that the lexical/exact disclosure above never silently depends
# on a vector match existing.
# --------------------------------------------------------------------------------------------


def test_no_vector_index_published_is_unaffected_by_this_cards_wiring(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-009", DOCUMENT_FQN)])
    result = find_examples(store, SCOPE, "a query naming nothing real at all")  # type: ignore[arg-type]
    from foss_mcp.mcp.tools.find_examples import NoExampleFound

    assert isinstance(result, NoExampleFound)
