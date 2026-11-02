"""KD3 (G3/TC-642, workstream J, REQ-G3-015): wiring TC-636's known-defects registry into
``search_symbols.SymbolMatch`` - the real gap TC-641's own investigation found and reported:
``SymbolMatch`` (and, through it, ``lookup()``'s own ``symbols`` field, which carries
``SymbolMatch`` instances directly) had no ``known_defects`` field at all and was structurally
disconnected from ``get_symbol.SymbolSignature.known_defects`` (G3/TC-639, KD2) - a caller using
``search_symbols``/``lookup`` never saw a method/class-scoped defect that ``get_symbol`` would
have shown for the exact same symbol.

No real defect data exists yet (KD6-KD9's job) - every test here proves the WIRING itself is
correct against a real, synthetic, ``tmp_path``-scoped registry file, so a real defect surfaces
automatically once one lands, with no further code change. Never writes a real
``config/known_defects/*.yaml`` file: ``known_defects.REGISTRY_DIR`` is monkeypatched to a
``tmp_path`` directory for the duration of each test, the exact mechanism
``tests/mcp/tools/test_known_defects_in_get_symbol.py`` (KD2's own tests) already established.

Offline and deterministic: in-memory generations built from hand-written type chunks, the same
pattern ``test_known_defects_in_get_symbol.py`` and ``tests/mcp/tools/test_member_lookup.py``
(TC-540) already established.

This file also records this card's own ``lookup.py`` investigation finding, with its own test
(``test_lookup_named_symbols_path_never_carries_known_defects`` below): ``lookup.py``'s own
``_named_symbols`` helper (out of this card's write_paths) does NOT reuse ``search_symbols()``'s
own constructed ``SymbolMatch`` instances for a query that merely NAMES a type - it builds fresh
``SymbolMatch`` instances directly from raw chunk documents, computing none of this card's new
``known_defects`` field, so a symbol reached ONLY through that path keeps ``known_defects == ()``
regardless of real registry data, even though the identical fqn/scope passed through the real
``search_symbols()`` carries the disclosure correctly. Fixing ``_named_symbols`` itself is out of
this card's write_paths; see ``lookup.py``'s own module docstring for the full note.
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
from foss_mcp.mcp.tools.lookup import _named_symbols
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc642"

DOCUMENT_FQN = "Aspose.Pdf.Document"
PAGE_FQN = "Aspose.Pdf.Page"
COMMIT_MATCH = "a" * 40
COMMIT_OTHER = "b" * 40


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    exact pattern ``test_known_defects_in_get_symbol.py`` (KD2's own tests) already established."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="tc642"),
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


def _symbol(fqn: str, *, methods: tuple[str, ...] = (), commit: str | None = None) -> str:
    """One type chunk's text, in the project's own ``## <fqn>`` heading convention - a
    ``Source-Commit:`` trailer line is appended last when *commit* is given, matching
    ``test_known_defects_in_get_symbol.py``'s own ``_symbol`` helper."""
    lines = [f"## {fqn}", "", f"FQN: {fqn}", "Kind: class_declaration"]
    if methods:
        lines += ["", "Methods:", *(f"  - {item}" for item in methods)]
    if commit is not None:
        lines += ["", f"Source-Commit: {commit}"]
    return "\n".join(lines) + "\n"


def _bodies() -> tuple[str, ...]:
    return (
        _symbol(DOCUMENT_FQN, methods=("Save(path: string) -> void",), commit=COMMIT_MATCH),
        _symbol(PAGE_FQN, methods=("Rotate(degrees: int) -> void",), commit=COMMIT_MATCH),
    )


@pytest.fixture(scope="module")
def store() -> _InMemoryStore:
    return _InMemoryStore(_bodies())


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``tmp_path``-scoped stand-in for the real, committed ``config/known_defects/`` directory -
    never the real one. ``REGISTRY_DIR`` is read fresh on every ``load_known_defects`` call, so
    monkeypatching the module attribute is enough; no file exists here unless a test writes one."""
    directory = tmp_path / "known_defects"
    directory.mkdir()
    monkeypatch.setattr(known_defects_module, "REGISTRY_DIR", directory)
    return directory


def _defect(
    defect_id: str,
    ref: str,
    *,
    kind: str = "method",
    indexed_commit: str | None = None,
    package_versions: list[str] | None = None,
) -> dict[str, Any]:
    applies_to: dict[str, Any] = {}
    if indexed_commit is not None:
        applies_to["indexed_commit"] = indexed_commit
    if package_versions is not None:
        applies_to["package_versions"] = package_versions
    return {
        "id": defect_id,
        "subject": {"kind": kind, "refs": [ref]},
        "applies_to": applies_to,
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


def _symbol_matches(result: list[SymbolMatch] | Miss) -> list[SymbolMatch]:
    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    return result


# --------------------------------------------------------------------------------------------
# a real method/class-scoped defect surfaces on a real search_symbols hit for the matching FQN
# --------------------------------------------------------------------------------------------


def test_method_scoped_defect_surfaces_when_chunk_commit_matches(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-001", DOCUMENT_FQN, indexed_commit=COMMIT_MATCH)])

    matches = _symbol_matches(search_symbols(store, SCOPE, DOCUMENT_FQN))  # type: ignore[arg-type]

    assert len(matches) == 1
    assert len(matches[0].known_defects) == 1
    assert "Summary describing PDF-NET-001." in matches[0].known_defects[0]
    assert "Recommended action for PDF-NET-001." in matches[0].known_defects[0]
    assert "Repro steps for PDF-NET-001." in matches[0].known_defects[0]


def test_class_scoped_defect_also_surfaces(store: _InMemoryStore, registry_dir: Path) -> None:
    _write_registry(
        registry_dir, [_defect("PDF-NET-002", DOCUMENT_FQN, kind="class", package_versions=["26.7.0"])]
    )

    matches = _symbol_matches(search_symbols(store, SCOPE, DOCUMENT_FQN))  # type: ignore[arg-type]

    assert len(matches[0].known_defects) == 1
    assert "Summary describing PDF-NET-002." in matches[0].known_defects[0]


# --------------------------------------------------------------------------------------------
# commit-match/mismatch behaves the same as KD2's own tests
# --------------------------------------------------------------------------------------------


def test_commit_scoped_defect_does_not_surface_when_chunk_commit_differs(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-003", DOCUMENT_FQN, indexed_commit=COMMIT_OTHER)])

    matches = _symbol_matches(search_symbols(store, SCOPE, DOCUMENT_FQN))  # type: ignore[arg-type]

    assert matches[0].known_defects == ()


def test_package_version_scoped_defect_always_surfaces_regardless_of_commit(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-004", DOCUMENT_FQN, package_versions=["26.7.0"])])

    matches = _symbol_matches(search_symbols(store, SCOPE, DOCUMENT_FQN))  # type: ignore[arg-type]

    assert len(matches[0].known_defects) == 1
    assert "Summary describing PDF-NET-004." in matches[0].known_defects[0]


# --------------------------------------------------------------------------------------------
# a package/commit-scoped defect does NOT surface here - that's KD4's get_product_reference path
# --------------------------------------------------------------------------------------------


def test_package_kind_defect_never_surfaces_through_search_symbols(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(
        registry_dir,
        [_defect("PDF-NET-005", DOCUMENT_FQN, kind="package", package_versions=["26.7.0"])],
    )

    matches = _symbol_matches(search_symbols(store, SCOPE, DOCUMENT_FQN))  # type: ignore[arg-type]

    assert matches[0].known_defects == ()


def test_commit_kind_defect_never_surfaces_through_search_symbols(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(
        registry_dir,
        [_defect("PDF-NET-006", DOCUMENT_FQN, kind="commit", indexed_commit=COMMIT_MATCH)],
    )

    matches = _symbol_matches(search_symbols(store, SCOPE, DOCUMENT_FQN))  # type: ignore[arg-type]

    assert matches[0].known_defects == ()


# --------------------------------------------------------------------------------------------
# subject.refs filtering and the no-match default
# --------------------------------------------------------------------------------------------


def test_defect_with_non_matching_subject_ref_never_surfaces(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(
        registry_dir,
        [_defect("PDF-NET-007", "Aspose.Pdf.SomeOtherType", indexed_commit=COMMIT_MATCH)],
    )

    matches = _symbol_matches(search_symbols(store, SCOPE, DOCUMENT_FQN))  # type: ignore[arg-type]

    assert matches[0].known_defects == ()


def test_symbol_with_no_matching_defect_has_known_defects_empty_tuple_by_default(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    # No registry file at all is written - registry_dir exists but is empty, which load_known_defects
    # treats as "no defects recorded for this pilot" (not an error). Proves every existing
    # search_symbols test (none of which ever set up a registry file) stays unaffected.
    matches = _symbol_matches(search_symbols(store, SCOPE, PAGE_FQN))  # type: ignore[arg-type]

    assert matches[0].known_defects == ()


def test_a_miss_is_unaffected_by_known_defects_wiring(store: _InMemoryStore, registry_dir: Path) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-008", DOCUMENT_FQN, package_versions=["26.7.0"])])

    result = search_symbols(store, SCOPE, "Aspose.Pdf.NoSuchType")  # type: ignore[arg-type]

    assert isinstance(result, Miss)
    assert not hasattr(result, "known_defects")


# --------------------------------------------------------------------------------------------
# this card's own lookup.py investigation finding, proven directly
# --------------------------------------------------------------------------------------------


def test_lookup_named_symbols_path_never_carries_known_defects(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    """The real finding: ``lookup.py``'s own ``_named_symbols`` (out of this card's write_paths)
    builds its own fresh ``SymbolMatch`` instances directly from raw chunk documents rather than
    reusing ``search_symbols()``'s own construction, so it never computes this card's new
    ``known_defects`` field at all - even for the exact same fqn/scope ``search_symbols()`` itself
    correctly discloses the defect for. ``TaskAnswer.symbols`` only carries the new field
    automatically for a symbol ``search_symbols()``/``explained_symbols()`` itself returns; a
    symbol reached ONLY through ``_named_symbols`` keeps ``known_defects == ()`` regardless of
    real registry data."""
    _write_registry(registry_dir, [_defect("PDF-NET-009", DOCUMENT_FQN, package_versions=["26.7.0"])])

    direct = _symbol_matches(search_symbols(store, SCOPE, "Document"))  # type: ignore[arg-type]
    assert direct[0].known_defects != ()
    assert "Summary describing PDF-NET-009." in direct[0].known_defects[0]

    named = _named_symbols(store, SCOPE, "Document")  # type: ignore[arg-type]
    assert named, "the query names the indexed Document type; _named_symbols must find it"
    assert named[0].known_defects == (), (
        "_named_symbols builds its own SymbolMatch directly and never computes known_defects - "
        "this assertion documents that gap; it is expected to start FAILING, as a welcome signal, "
        "only once a future card wires _named_symbols itself (out of this card's write_paths)"
    )
