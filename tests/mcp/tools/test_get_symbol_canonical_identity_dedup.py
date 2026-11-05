"""G3/TC-686 (I5, REQ-G3-012): ``get_symbol`` dedupes a re-export shell by canonical identity.

Before this card, `get_symbol._owners` grouped matched chunks into "the same type" by
`_declared_members(chunk.text)` equality - the member dict re-parsed back out of rendered text.
Once I4 thins a shell's chunk to a pointer (no declared members at all), that equality check would
treat the now-empty shell as a SEPARATE type from its full definition, producing a wrong, spurious
"ambiguous" answer for a symbol that has exactly one real definition. This module proves `_owners`
instead groups by each chunk's own canonical identity (a chunk's `Defined-In:` VALUE when it is a
shell, else its own FQN) - so a shell and its definition are recognized as one type even though
their rendered bodies now differ (thin vs. full) - while a genuinely ambiguous short name (two
different real types, no alias relationship at all) is still correctly reported as ambiguous, and a
pilot with no alias at all is byte-for-byte unaffected (Decision E).

Offline, in-memory generations built from hand-written chunk text - the same pattern
``test_member_lookup.py`` already uses. The shell fixtures below are built THIN on purpose (no
member content at all), matching exactly what I4's `_format_shell_pointer_text` now renders for a
real shell, rather than today's old full-duplicate convention.
"""

from __future__ import annotations

from types import SimpleNamespace

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import NotFound, ResolvedSignature, SymbolSignature, get_symbol
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

SCOPE = Scope(family="pkg", platform="python")
_GENERATION_ID = "gen-tc686"


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
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
        lexical = build_lexical_index(chunks, chunk_ids, _GENERATION_ID)
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _type(
    fqn: str,
    *,
    kind: str = "class",
    methods: tuple[str, ...] = (),
    properties: tuple[str, ...] = (),
    header: tuple[str, ...] = (),
) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", f"Kind: {kind}", *header]
    for title, items in (("Methods:", methods), ("Properties:", properties)):
        if items:
            lines += ["", title, *(f"  - {item}" for item in items)]
    return "\n".join(lines) + "\n"


def _thin_shell(fqn: str, *, defined_in: str, exported: bool = False) -> str:
    """Exactly I4's `_format_shell_pointer_text` convention: FQN/Kind/Defined-In only - no member
    content at all, confirming `_owners` never needs to re-parse rendered text to find one."""
    header = (f"Defined-In: {defined_in}",)
    if exported:
        header = (*header, f"Exported-As: {fqn}")
    return _type(fqn, header=header)


def test_a_query_naming_the_shells_own_fqn_prefix_resolves_to_the_real_definitions_members() -> None:
    """(b): a thin shell carries NO declared members at all - the old, content-equality mechanism
    would see `{}` on the shell and a non-empty dict on the definition and treat them as two
    different types. The canonical-id mechanism unifies them via `Defined-In:` instead."""
    store = _InMemoryStore(
        (
            _thin_shell("pkg.Facade", defined_in="pkg.facades.Facade"),
            _type(
                "pkg.facades.Facade",
                methods=("bind(path: str) -> None",),
                header=("Exported-As: pkg.Facade",),
            ),
        )
    )
    answer = get_symbol(store, SCOPE, "Facade.bind")  # type: ignore[arg-type]
    assert isinstance(answer, ResolvedSignature)
    assert answer.fqn == "pkg.facades.Facade"  # the real definition, not the (now-empty) shell
    assert answer.member_lines == ("bind(path: str) -> None",)

    bare = get_symbol(store, SCOPE, "Facade")  # type: ignore[arg-type]
    assert isinstance(bare, ResolvedSignature)
    assert (bare.fqn, bare.member) == ("pkg.facades.Facade", "")
    assert bare.methods == ("bind(path: str) -> None",)


def test_a_genuinely_ambiguous_short_name_is_still_reported_as_ambiguous() -> None:
    """(b): two real, DIFFERENT types that merely share a short name (no `Defined-In:` on either)
    must never be unified just because neither carries a canonical id pointing at the other - each
    chunk's own canonical id is its own FQN, which differs, so they stay two separate owners."""
    store = _InMemoryStore(
        (
            _type("pkg.a.Marker", kind="enum"),
            _type("pkg.b.Marker", kind="enum"),
        )
    )
    result = get_symbol(store, SCOPE, "Marker")  # type: ignore[arg-type]
    assert isinstance(result, NotFound)
    assert result.suggestions == ("pkg.a.Marker", "pkg.b.Marker")


def test_a_shell_and_definition_that_both_declare_a_member_prefer_the_real_definition() -> None:
    """The existing precedence (`owners[twin].is_reexport_shell and not chunk.is_reexport_shell`)
    is unchanged - only what decides "same type" moved to canonical id. Still true even when a
    shell happens to carry its OWN (stale) content alongside `Defined-In:`."""
    store = _InMemoryStore(
        (
            _type(
                "pkg.Workbook",
                methods=("save() -> None",),
                header=("Defined-In: pkg.workbook.Workbook",),
            ),
            _type(
                "pkg.workbook.Workbook",
                methods=("save() -> None", "close() -> None"),
                header=("Exported-As: pkg.Workbook",),
            ),
        )
    )
    answer = get_symbol(store, SCOPE, "Workbook.close")  # type: ignore[arg-type]
    assert isinstance(answer, ResolvedSignature)
    assert answer.fqn == "pkg.workbook.Workbook"
    assert answer.member_lines == ("close() -> None",)
    exact = get_symbol(store, SCOPE, "pkg.Workbook")  # type: ignore[arg-type]
    assert type(exact) is SymbolSignature  # the exact FQN of the shell is still an exact hit on it


def test_a_pilot_with_no_alias_at_all_is_byte_for_byte_unaffected() -> None:
    """Decision E: no chunk carries a `Defined-In:` line, so every chunk's canonical id is its own
    FQN - `_owners` behaves exactly as the old content-equality mechanism did."""
    store = _InMemoryStore(
        (
            _type("pkg.Alpha", methods=("run() -> None",)),
            _type("pkg.Beta", properties=("x: int",)),
        )
    )
    alpha = get_symbol(store, SCOPE, "pkg.Alpha")  # type: ignore[arg-type]
    assert isinstance(alpha, SymbolSignature) and alpha.methods == ("run() -> None",)
    beta = get_symbol(store, SCOPE, "Beta.x")  # type: ignore[arg-type]
    assert isinstance(beta, ResolvedSignature) and beta.member_lines == ("x: int",)
