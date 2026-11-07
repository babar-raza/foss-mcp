"""KD2 (G3/REQ-G3-015): wiring TC-636's known-defects registry into get_symbol/list_members.

No real defect data exists yet (KD6-KD9's job) - every test here proves the WIRING itself is
correct against a real, synthetic, ``tmp_path``-scoped registry file, so a real defect surfaces
automatically once one lands, with no further code change. Never writes a real
``config/known_defects/*.yaml`` file: ``known_defects.REGISTRY_DIR`` is monkeypatched to a
``tmp_path`` directory for the duration of each test, the same mechanism TC-636's own
``tests/indexing/test_known_defects.py`` uses via ``registry_path_for(..., base_dir=...)``.

Offline and deterministic: in-memory generations built from hand-written type chunks, the same
pattern ``tests/mcp/tools/test_member_lookup.py`` (TC-540) already established.

**Added (TC-711, 2026-11-07):** a real end-to-end section at the bottom of this module proves the
real, committed ``config/known_defects/words__net.yaml`` entry (``WORDS-NET-001``, the phantom
``Node.GetAncestorOf<T>()`` method disclosed for 42 real ``Aspose.Words``-namespace types)
surfaces through a live ``get_symbol`` call against the real, committed
``infra/demo-manifests/words__net__self_extracted`` corpus - never a synthetic stand-in, and never
the ``registry_dir``/``REGISTRY_DIR``-monkeypatched fixture every other test above uses, since the
whole point is to prove the REAL registry file this card wrote actually wires through, exactly the
same real-corpus end-to-end pattern TC-706/TC-707 already established in
``tests/indexing/test_known_defects_data_3d.py`` for ``3D-JAVA-001``/``3D-NET-001``.
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
from foss_mcp.mcp.tools.get_symbol import NotFound, ResolvedSignature, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.conformance.corpus import load_demo_manifests_corpus

SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc639"

DOCUMENT_FQN = "Aspose.Pdf.Document"
PAGE_FQN = "Aspose.Pdf.Page"
COMMIT_MATCH = "a" * 40
COMMIT_OTHER = "b" * 40


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore - the
    exact pattern ``test_member_lookup.py`` (TC-540) already established."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="tc639"),
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
    ``test_freshness_provenance.py``'s own ``_symbol`` helper."""
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
        "subject": {"kind": "method", "refs": [ref]},
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


# --------------------------------------------------------------------------------------------
# commit scoping
# --------------------------------------------------------------------------------------------


def test_commit_scoped_defect_surfaces_when_chunk_commit_matches(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-001", DOCUMENT_FQN, indexed_commit=COMMIT_MATCH)])

    result = get_symbol(store, SCOPE, DOCUMENT_FQN)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert len(result.known_defects) == 1
    assert "Summary describing PDF-NET-001." in result.known_defects[0]
    assert "Recommended action for PDF-NET-001." in result.known_defects[0]
    assert "Repro steps for PDF-NET-001." in result.known_defects[0]


def test_commit_scoped_defect_does_not_surface_when_chunk_commit_differs(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-002", DOCUMENT_FQN, indexed_commit=COMMIT_OTHER)])

    result = get_symbol(store, SCOPE, DOCUMENT_FQN)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert result.known_defects == ()


def test_package_version_scoped_defect_always_surfaces_regardless_of_commit(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-003", DOCUMENT_FQN, package_versions=["26.7.0"])])

    result = get_symbol(store, SCOPE, DOCUMENT_FQN)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert len(result.known_defects) == 1
    assert "Summary describing PDF-NET-003." in result.known_defects[0]


# --------------------------------------------------------------------------------------------
# subject.refs filtering and the no-match default
# --------------------------------------------------------------------------------------------


def test_defect_with_non_matching_subject_ref_never_surfaces(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(
        registry_dir,
        [_defect("PDF-NET-004", "Aspose.Pdf.SomeOtherType", indexed_commit=COMMIT_MATCH)],
    )

    result = get_symbol(store, SCOPE, DOCUMENT_FQN)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert result.known_defects == ()


def test_symbol_with_no_matching_defects_has_known_defects_empty_tuple_by_default(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    # No registry file at all is written - registry_dir exists but is empty, which load_known_defects
    # treats as "no defects recorded for this pilot" (not an error).
    result = get_symbol(store, SCOPE, PAGE_FQN)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    assert result.known_defects == ()


# --------------------------------------------------------------------------------------------
# NotFound never gains or looks up defects
# --------------------------------------------------------------------------------------------


def test_notfound_result_never_looks_up_defects_at_all(store: _InMemoryStore, registry_dir: Path) -> None:
    # A deliberately INVALID registry file for this pilot: if get_symbol's NotFound path ever
    # called load_known_defects, this would raise KnownDefectsError. It must not: a NotFound is
    # resolved before any registry lookup happens.
    path = registry_path_for("pdf", "net", base_dir=registry_dir)
    path.write_text("not: [valid, {schema", encoding="utf-8")

    result = get_symbol(store, SCOPE, "Aspose.Pdf.NoSuchType")  # type: ignore[arg-type]

    assert isinstance(result, NotFound)
    assert result.found is False
    assert not hasattr(result, "known_defects")


# --------------------------------------------------------------------------------------------
# a resolved member name's defects are looked up under the OWNING type's FQN
# --------------------------------------------------------------------------------------------


def test_resolved_member_name_gets_the_owning_types_known_defects(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-005", DOCUMENT_FQN, package_versions=["26.7.0"])])

    result = get_symbol(store, SCOPE, f"{DOCUMENT_FQN}.Save")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == DOCUMENT_FQN
    assert len(result.known_defects) == 1
    assert "Summary describing PDF-NET-005." in result.known_defects[0]


# --------------------------------------------------------------------------------------------
# list_members' own surfacing mechanism
# --------------------------------------------------------------------------------------------


def test_list_members_prepends_the_type_level_known_defect_line(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-006", DOCUMENT_FQN, package_versions=["26.7.0"])])

    members = list_members(store, SCOPE, DOCUMENT_FQN)  # type: ignore[arg-type]

    assert isinstance(members, tuple)
    assert len(members) == 2  # the known-defect line, then the one declared method
    assert "Summary describing PDF-NET-006." in members[0]
    assert members[1].startswith("Save(")


def test_list_members_for_a_resolved_member_name_excludes_the_type_level_known_defect(
    store: _InMemoryStore, registry_dir: Path
) -> None:
    _write_registry(registry_dir, [_defect("PDF-NET-007", DOCUMENT_FQN, package_versions=["26.7.0"])])

    members = list_members(store, SCOPE, f"{DOCUMENT_FQN}.Save")  # type: ignore[arg-type]

    assert isinstance(members, tuple)
    assert members == ("Save(path: string) -> void",)
    assert all("Summary describing PDF-NET-007." not in line for line in members)


# --------------------------------------------------------------------------------------------
# TC-711 (WORDS-NET-001): real end-to-end coverage against the REAL, committed
# ``infra/demo-manifests/words__net__self_extracted`` corpus and the REAL, committed
# ``config/known_defects/words__net.yaml`` registry file - never the ``registry_dir``-monkeypatched
# fixture every test above uses, and never a synthetic/in-memory store. ``REGISTRY_DIR`` is left at
# its real default here, so ``get_symbol``/``list_members`` read the actual file this card wrote,
# exactly the same real-corpus end-to-end pattern TC-706/TC-707 already established in
# ``tests/indexing/test_known_defects_data_3d.py`` for ``3D-JAVA-001``/``3D-NET-001``.
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _real_words_net_corpus(tmp_path_factory: pytest.TempPathFactory):
    """The real, committed ``infra/demo-manifests`` corpus, loaded through a temp-directory copy -
    the exact ``load_demo_manifests_corpus`` pattern ``tests/indexing/test_known_defects_completeness.py``'s
    own TC-702 fixture already established. Nothing in the real repository is ever written to."""
    root = tmp_path_factory.mktemp("tc711-known-defects-in-get-symbol-words-net-corpus")
    return load_demo_manifests_corpus(root)


def test_get_symbol_end_to_end_surfaces_the_real_written_words_net_001_defect(_real_words_net_corpus) -> None:
    """``Aspose.Words.Body`` is one of the 42 real owning-type FQNs TC-711 independently
    re-confirmed both declare the phantom ``GetAncestorOf`` method in the indexed source and are
    genuinely absent of it from the real published ``Aspose.Words.FOSS 26.3.0`` package, so
    ``get_symbol`` against the real, committed ``words__net.yaml`` this card wrote must surface
    ``WORDS-NET-001``'s real disclosure text for it."""
    pilot = _real_words_net_corpus.pilots["words__net"]

    result = get_symbol(_real_words_net_corpus.store, pilot.scope, "Aspose.Words.Body")

    assert isinstance(result, SymbolSignature)
    assert result.found is True
    assert len(result.known_defects) == 1
    assert "does not exist in the real published Aspose.Words.FOSS 26.3.0 package" in result.known_defects[0]


def test_list_members_end_to_end_surfaces_the_real_written_words_net_001_defect(
    _real_words_net_corpus,
) -> None:
    """Same real corpus, same real registry entry, through ``list_members`` instead of
    ``get_symbol`` - proving the disclosure surfaces through BOTH tools, per this card's own
    title (``get_symbol``/``list_members`` both disclose the phantom method with zero caveat)."""
    pilot = _real_words_net_corpus.pilots["words__net"]

    members = list_members(_real_words_net_corpus.store, pilot.scope, "Aspose.Words.Body")

    assert isinstance(members, tuple)
    assert len(members) >= 1
    assert "does not exist in the real published Aspose.Words.FOSS 26.3.0 package" in members[0]


def test_get_symbol_end_to_end_surfaces_the_real_defect_for_node_itself(_real_words_net_corpus) -> None:
    """``Aspose.Words.Node`` is the base type round-2's own finding names first; confirming it
    separately from a subclass (``Body``, above) guards against a ref list that only matches
    subclasses while silently missing the base type itself."""
    pilot = _real_words_net_corpus.pilots["words__net"]

    result = get_symbol(_real_words_net_corpus.store, pilot.scope, "Aspose.Words.Node")

    assert isinstance(result, SymbolSignature)
    assert result.found is True
    assert len(result.known_defects) == 1
    assert "GetAncestorOf" in result.known_defects[0]
    assert "does not exist in the real published Aspose.Words.FOSS 26.3.0 package" in result.known_defects[0]
