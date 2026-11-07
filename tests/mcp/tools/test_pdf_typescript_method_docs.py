"""G3/TC-727 (PDF-TS-002, REQ-G3-013): `get_symbol`/`list_members` now surface a method's own
real doc summary (JSDoc/docstring) via `chunk_builder._doc_suffix`, closing the round-2 finding
that method-level doc text was extracted correctly but never rendered, for any language, any
pilot. Proven against the real, committed `tests/fixtures/pdf_typescript/api_surface.json`
fixture - no network, no container - following the same real-data template as
`tests/indexing/test_round3_unsupported_markers.py`'s own `real_3d_net_store` fixture.

``Document.New`` is the ORIGINAL round-2 case: its real `doc` field ("Create a new document from
scratch: zero pages when `format` is omitted, or one blank page of that size when given.") is the
exact behavior distinction that caused a round-1 agent to need a runtime fix-iteration after
being surprised by an unexpected blank first page. ``Document.RemovePage`` is a real sibling
method of the SAME type with an empty `doc` field in the same real fixture, proving the fix is
additive: a method with no doc renders exactly as it did before this card.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import NotFound, ResolvedSignature, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.list_members import list_members

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SCOPE = Scope(family="pdf", platform="typescript")

# The real, committed fixture's own `document.Document.New` doc field (confirmed word-for-word
# against the real upstream JSDoc the round-2 finding quotes, src/document.ts:512-517).
REAL_DOC_TEXT = (
    "Create a new document from scratch: zero pages when `format` is omitted, or one blank "
    "page of that size when given."
)


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


class _Store:
    """The two calls the tools make of a generation store, over an in-memory lexical index."""

    def __init__(self, texts: list[str]) -> None:
        chunks = [SimpleNamespace(text=text) for text in texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], "gen-tc727")  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc727"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


@pytest.fixture(scope="module")
def real_pdf_typescript_store() -> _Store:
    fixture = json.loads((FIXTURES / "pdf_typescript" / "api_surface.json").read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title="pdf", max_types=len(fixture["types"]))
    return _Store([chunk.text for chunk in chunks])


@pytest.fixture(scope="module")
def real_pdf_typescript_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "pdf_typescript" / "api_surface.json").read_text(encoding="utf-8"))


def test_real_fixture_still_carries_document_new_s_doc_field(
    real_pdf_typescript_fixture: dict[str, Any],
) -> None:
    """Reproduces the round-2 before-state's own ground truth: the real committed fixture's
    `document.Document.New` entry has a real, non-empty `doc` field, word-for-word the source
    JSDoc the finding quotes. If this ever goes empty, every assertion below about REAL_DOC_TEXT
    is testing a stale literal, not the real fixture - so this is checked directly, not assumed.
    """
    document = next(
        t for t in real_pdf_typescript_fixture["types"] if t.get("class_import") == "document.Document"
    )
    new_method = next(m for m in document["methods"] if m["name"] == "New")
    assert " ".join(new_method["doc"].split()) == REAL_DOC_TEXT


def test_get_symbol_document_new_now_surfaces_its_real_doc_summary(
    real_pdf_typescript_store: _Store,
) -> None:
    """The ORIGINAL round-2 case (PDF-TS-002): `get_symbol` for `document.Document.New` now
    carries its real doc summary on the member line, closing the gap that previously left an
    agent with only the bare signature `New(format?: PageFormat) -> Document [static]` and no way
    to learn - without running the code - that omitting `format` yields zero pages while passing
    one yields a single blank page of that size.
    """
    resolved = get_symbol(real_pdf_typescript_store, SCOPE, "document.Document.New")  # type: ignore[arg-type]
    assert isinstance(resolved, ResolvedSignature) and resolved.member == "New"
    assert len(resolved.member_lines) == 1
    assert resolved.member_lines[0] == f"New(format?: PageFormat) -> Document [static]  ({REAL_DOC_TEXT})"
    assert resolved.limitations == ()


def test_list_members_document_new_also_surfaces_the_same_doc_summary(
    real_pdf_typescript_store: _Store,
) -> None:
    """`list_members` for the same member agrees exactly with `get_symbol` - the same real doc
    text, through the other tool that reads member lines.
    """
    lines = list_members(real_pdf_typescript_store, SCOPE, "document.Document.New")  # type: ignore[arg-type]
    assert not isinstance(lines, NotFound) and len(lines) == 1
    assert lines[0] == f"New(format?: PageFormat) -> Document [static]  ({REAL_DOC_TEXT})"


def test_get_symbol_document_lists_new_with_its_doc_among_the_full_member_set(
    real_pdf_typescript_store: _Store,
) -> None:
    """`get_symbol` on the whole `Document` type still carries the doc-suffixed `New` line among
    its full `methods` tuple - the additive suffix reaches the bulk, multi-member view too, not
    only the single-member resolution above.
    """
    result = get_symbol(real_pdf_typescript_store, SCOPE, "document.Document")  # type: ignore[arg-type]
    assert isinstance(result, SymbolSignature)
    assert f"New(format?: PageFormat) -> Document [static]  ({REAL_DOC_TEXT})" in result.methods


def test_a_sibling_method_with_no_doc_field_renders_with_no_suffix_exactly_as_before(
    real_pdf_typescript_store: _Store,
    real_pdf_typescript_fixture: dict[str, Any],
) -> None:
    """`document.Document.RemovePage` is a real sibling method of the SAME type, in the SAME
    real fixture, whose own `doc` field is empty (confirmed directly against the fixture, not
    assumed) - proving the fix is purely additive: a method with no doc summary renders its line
    exactly as it always has, with no new, empty, or placeholder suffix of any kind.
    """
    document = next(
        t for t in real_pdf_typescript_fixture["types"] if t.get("class_import") == "document.Document"
    )
    remove_page = next(m for m in document["methods"] if m["name"] == "RemovePage")
    assert remove_page.get("doc") in (None, "")

    resolved = get_symbol(real_pdf_typescript_store, SCOPE, "document.Document.RemovePage")  # type: ignore[arg-type]
    assert isinstance(resolved, ResolvedSignature) and resolved.member == "RemovePage"
    assert resolved.member_lines == ("RemovePage(target: number | Page) -> void",)

    lines = list_members(real_pdf_typescript_store, SCOPE, "document.Document.RemovePage")  # type: ignore[arg-type]
    assert not isinstance(lines, NotFound)
    assert lines == ("RemovePage(target: number | Page) -> void",)
