"""TC-571 (G3/REQ-G3-009, reopens RST-14): ``search_symbols`` recognises a Go struct-embedding
member chunk_builder.py renders as ``(promoted from X)`` as non-declaring, exactly like the
original ``(inherited from X)`` marker.

Review finding: ``chunk_builder.py``'s ``_inherited_from_suffix`` (TC-535, RD3-027) renders a
promoted member with the Go-correct term ``(promoted from X)`` rather than ``(inherited from
X)``, but ``search_symbols.py`` never learned the new marker, so a type that only PROMOTES a
member through struct embedding (never declares it) was scored as if it DECLARED that member.
Measured on the real re-ingested pdf/go data: query ``SetPassword`` on scope pdf/go ranked
``PasswordBoxField`` (promotes the member from ``TextBoxField``) above ``Document``/
``TextBoxField`` (the real declaring types) and the other inheriting field types - see
``tests/mcp/tools/test_search_symbols_forgiveness.py`` for the real-data case. This file pins
the fix with hand-written synthetic chunks mirroring that file's existing ``(inherited from)``
case exactly, but with ``(promoted from)``.
"""

from __future__ import annotations

from types import SimpleNamespace

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import (
    SymbolMatch,
    _declared_member_names,
    _is_non_declaring_item,
    _member_declaration,
    _member_name,
    search_symbols,
)

SYNTHETIC_SCOPE = Scope(family="sample", platform="python")
_GENERATION_ID = "gen-tc571-promoted"


class _InMemoryStore:
    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _type_chunk(fqn: str, *members: str) -> str:
    lines = [f"FQN: {fqn}", "Kind: class_declaration"]
    if members:
        lines.append("Methods:")
        lines.extend(f"  - {member}" for member in members)
    return "\n".join(lines) + "\n"


def _names(result: object) -> list[str]:
    assert isinstance(result, list), f"expected symbols, got {result!r}"
    assert all(isinstance(item, SymbolMatch) for item in result)
    return [(extract_fqn(item.text) or "").split(".")[-1] for item in result]


# ---- _member_declaration / _declared_member_names recognise the promoted marker ------------------


def test_member_declaration_is_false_for_a_promoted_member() -> None:
    b_text = _type_chunk("Pkg.B", "Reset()  (promoted from A)")

    assert _member_declaration("Reset", "Pkg.B", b_text) is False


def test_member_declaration_is_true_for_the_declaring_type() -> None:
    a_text = _type_chunk("Pkg.A", "Reset()")

    assert _member_declaration("Reset", "Pkg.A", a_text) is True


def test_declared_member_names_excludes_a_promoted_item() -> None:
    a_text = _type_chunk("Pkg.A", "Reset()")
    b_text = _type_chunk("Pkg.B", "Reset()  (promoted from A)")

    assert "reset" in _declared_member_names(a_text)
    assert "reset" not in _declared_member_names(b_text)


def test_is_non_declaring_item_recognises_both_markers() -> None:
    assert _is_non_declaring_item("Reset()  (promoted from A)") is True
    assert _is_non_declaring_item("Reset()  (inherited from A)") is True
    assert _is_non_declaring_item("Reset()") is False


def test_member_name_strips_the_promoted_suffix_even_without_a_following_paren_or_colon() -> None:
    # A defensive case _MEMBER_NAME_END's "(inherited|promoted) from" alternative exists for -
    # chunk_builder.py's two real conventions always render "(" or ":" first, but the regex
    # itself must still recognise the promoted spelling, not only the inherited one.
    assert _member_name("Count  (promoted from A)") == "Count"
    assert _member_name("Count  (inherited from A)") == "Count"


# ---- search_symbols ranks the declaring type above the promoting type ----------------------------


def test_a_declaring_type_outranks_a_promoting_type_with_a_better_bm25_score() -> None:
    # Mirrors test_search_symbols_forgiveness.py's
    # test_a_declaring_type_outranks_inheriting_types_with_a_better_bm25_score exactly, but with
    # the Go-correct "(promoted from)" marker instead of "(inherited from)".
    store = _InMemoryStore(
        _type_chunk("Pkg.Base", "Reset()"),
        _type_chunk("Pkg.Child", "Reset()  (promoted from Base)"),
        _type_chunk("Pkg.Other", "Reset()  (promoted from Base)", "Other filler words here"),
    )

    result = search_symbols(store, SYNTHETIC_SCOPE, "Reset")  # type: ignore[arg-type]

    assert _names(result)[0] == "Base"


def test_search_symbols_ranks_the_declaring_type_before_both_promoting_and_inheriting_types() -> None:
    # A single chunk set that mixes a declaring type, a promoting type (Go embedding) and an
    # inheriting type (ordinary inheritance), so the fix is proven against both markers at once.
    store = _InMemoryStore(
        _type_chunk("Pkg.Base", "Reset()"),
        _type_chunk("Pkg.Promoter", "Reset()  (promoted from Base)"),
        _type_chunk("Pkg.Inheritor", "Reset()  (inherited from Base)"),
    )

    names = _names(search_symbols(store, SYNTHETIC_SCOPE, "Reset"))  # type: ignore[arg-type]

    assert names[0] == "Base"
    assert names.index("Base") < names.index("Promoter")
    assert names.index("Base") < names.index("Inheritor")
