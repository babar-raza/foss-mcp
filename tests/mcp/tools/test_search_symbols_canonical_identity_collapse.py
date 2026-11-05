"""G3/TC-686 (I5, REQ-G3-012): ``search_symbols`` collapses a re-export shell by canonical identity.

Before this card, `search_symbols._collapse_aliases` keyed its dedup on `(short.casefold(),
entry.body_hash)` - a hash of the whole rendered chunk BODY. Once I4 thins a shell's body to a
pointer, its hash stops matching its definition's entirely, so the two would no longer collapse at
all - surfacing a spurious, near-empty, duplicate "symbol" in search results. This module proves
`_collapse_aliases` instead keys on each `_Entry`'s own canonical identity (a chunk's `Defined-In:`
VALUE when it is a shell, else its own FQN), and that the kept result is always the real definition
(never whichever doc_id happened to sort first) even though the two chunks' bodies now differ (thin
vs. full) - while a memberless type with no real alias at all is unaffected, and a pilot with no
alias at all is byte-for-byte unaffected (Decision E).

Offline, in-memory generations - the same pattern ``test_search_symbols_memberless_last.py`` already
uses. The shell fixtures below are built THIN on purpose (no member content at all), matching
exactly what I4's `_format_shell_pointer_text` now renders for a real shell.
"""

from __future__ import annotations

from types import SimpleNamespace

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, Miss, search_symbols

SCOPE = Scope(family="pkg", platform="python")
_GENERATION_ID = "gen-tc686"


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, *bodies: str) -> None:
        from foss_mcp.normalization.chunker import chunk_document
        from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="org/repo", commit="tc686"),
                evidence_refs=(),
                title="pkg/python API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        self.lexical = build_lexical_index(chunks, chunk_ids, _GENERATION_ID)
        self._manifest = SimpleNamespace(payload={"lexical_index": self.lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _chunk(
    fqn: str, *, kind: str = "class", methods: tuple[str, ...] = (), header: tuple[str, ...] = ()
) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", f"Kind: {kind}", *header]
    if methods:
        lines += ["", "Methods:", *(f"  - {method}" for method in methods)]
    return "\n".join(lines) + "\n"


def _thin_shell(fqn: str, *, defined_in: str) -> str:
    """Exactly I4's `_format_shell_pointer_text` convention - no member content at all."""
    return _chunk(fqn, header=(f"Defined-In: {defined_in}",))


def test_a_thin_shell_is_listed_as_an_alias_of_the_definitions_single_result() -> None:
    store = _InMemoryStore(
        _thin_shell("pkg.Workbook", defined_in="pkg.workbook.Workbook"),
        _chunk(
            "pkg.workbook.Workbook",
            methods=("save() -> None",),
            header=("Exported-As: pkg.Workbook",),
        ),
    )
    result = search_symbols(store, SCOPE, "Workbook")  # type: ignore[arg-type]
    assert isinstance(result, list)
    fqns = [extract_fqn(match.text) for match in result]
    # One result, not two near-duplicates - the thin shell is never surfaced as its own,
    # now-almost-empty separate match.
    assert fqns == ["pkg.workbook.Workbook"]
    kept = result[0]
    assert kept.match_quality == MATCH_EXACT
    assert kept.aliases == ("pkg.Workbook",)
    assert "Methods:" in kept.text  # the real definition's own full content, never the thin body


def test_the_real_definition_is_kept_even_when_the_shell_sorts_first() -> None:
    """Fixture order deliberately puts the shell BEFORE its definition - the "which one is kept"
    fix must not depend on which one a caller happened to list first."""
    store = _InMemoryStore(
        _thin_shell("pkg.Facade", defined_in="pkg.facades.Facade"),
        _chunk("pkg.facades.Facade", methods=("bind() -> None",), header=("Exported-As: pkg.Facade",)),
    )
    result = search_symbols(store, SCOPE, "Facade")  # type: ignore[arg-type]
    assert isinstance(result, list) and len(result) == 1
    assert extract_fqn(result[0].text) == "pkg.facades.Facade"
    assert result[0].aliases == ("pkg.Facade",)


def test_an_exact_fqn_query_on_the_shell_itself_is_never_hijacked_by_a_weaker_match() -> None:
    """Measured directly against the real committed `slides_python` fixture
    (`aspose.slides_foss.slideshow.TransitionMorphType`, whose own definition's dotted name happens
    to repeat the shell's short name, so it ALSO bag-of-words-matches the identical query): an
    exact, literal full-FQN query must always answer with the chunk that IS that exact FQN, never a
    different, only-weakly-matched chunk that merely shares a canonical id with it."""
    store = _InMemoryStore(
        _thin_shell("pkg.Code", defined_in="pkg.Code.Code"),
        _chunk("pkg.Code.Code", methods=("run() -> None",), header=("Exported-As: pkg.Code",)),
    )
    result = search_symbols(store, SCOPE, "pkg.Code")  # type: ignore[arg-type]
    assert isinstance(result, list)
    assert extract_fqn(result[0].text) == "pkg.Code"
    assert result[0].match_kind == "exact_fqn"
    assert result[0].aliases == ("pkg.Code.Code",)


def test_a_memberless_type_with_no_real_alias_is_unaffected() -> None:
    store = _InMemoryStore(_chunk("pkg.Marker", kind="enum"))
    result = search_symbols(store, SCOPE, "Marker")  # type: ignore[arg-type]
    assert isinstance(result, list) and len(result) == 1
    assert result[0].aliases == ()


def test_a_pilot_with_no_alias_at_all_is_byte_for_byte_unaffected() -> None:
    """Decision E: no chunk carries a `Defined-In:` line, so every entry's canonical id is its own
    FQN - `_collapse_aliases` behaves exactly as the old body-hash mechanism did."""
    store = _InMemoryStore(
        _chunk("pkg.Alpha", methods=("run() -> None",)),
        _chunk("pkg.Beta", methods=("run() -> None",)),  # same body, no Defined-In anywhere
    )
    result = search_symbols(store, SCOPE, "run")  # type: ignore[arg-type]
    assert isinstance(result, list)
    fqns = {extract_fqn(match.text) for match in result}
    assert fqns == {"pkg.Alpha", "pkg.Beta"}
    for match in result:
        assert match.aliases == ()


def test_a_genuine_miss_is_still_a_miss() -> None:
    store = _InMemoryStore(_chunk("pkg.Alpha"))
    assert isinstance(search_symbols(store, SCOPE, "NoSuchSymbolAtAll"), Miss)  # type: ignore[arg-type]
