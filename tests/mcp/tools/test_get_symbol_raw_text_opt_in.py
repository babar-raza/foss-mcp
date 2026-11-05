"""get_symbol's raw_text opt-in (TC-688, RT3, 3D-NET-014, G3/REQ-G3-016).

Before this card, ``SymbolSignature``/``ResolvedSignature`` always carried ``raw_text``: the full
chunk text, byte for byte, duplicating everything ``bases``/``methods``/``properties``/``members``
already parse out of it - roughly doubling response size for zero additional information. This
was a REAL, reproduced finding (3D-NET-014) ``docs/DECISION_LOG.md``'s 2026-11-01 "Workstream K"
entry deliberately deferred, by name, "not now, in the final hours before a public demo
deployment" - not dropped. This card is that deferred work, now taken.

``get_symbol(..., include_raw_text=False)`` - the new default - omits ``raw_text`` from the
response ENTIRELY: not an empty string, not ``null``, the key itself is absent, so a caller
checking for an empty string can never mistake "I did not ask for this" for "the field exists and
happens to be empty". Mechanism (this card's own choice, reported here): ``get_symbol.py`` defines
a private sentinel class, ``_Omitted`` (one shared instance, ``RAW_TEXT_OMITTED``), and
``server._to_jsonable`` drops any dataclass field whose value IS that exact sentinel (by identity,
``is``, never by equality) before a response is serialised. The alternative this card's own text
allowed - setting the field to a documented sentinel VALUE instead of omitting the key - was not
taken: full key omission is closer to the literal request ("omit raw_text from the response
entirely") and strictly stronger (a caller checking ``"raw_text" in response`` gets the right
answer either way).

``include_raw_text=True`` reproduces today's always-on behaviour byte for byte: this file proves
that directly, by comparing against the identical chunk text the store holds.

Offline and deterministic: an in-memory store built from hand-written type chunks, the same
pattern ``test_member_lookup.py`` already uses for this module.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.server import GetSymbolInput, _to_jsonable, create_server, tool_input_schema
from foss_mcp.mcp.tools.get_symbol import RAW_TEXT_OMITTED, SymbolSignature, get_symbol
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc688"

DOCUMENT_BODY = (
    "## Aspose.Pdf.Document\n"
    "\n"
    "FQN: Aspose.Pdf.Document\n"
    "Kind: class_declaration\n"
    "\n"
    "Methods:\n"
    "  - Save(path: string) -> void\n"
    "\n"
    "Properties:\n"
    "  - Title: string (writable)\n"
)


class _InMemoryStore:
    """One active generation of in-memory chunks - the identical pattern test_member_lookup.py's
    own ``_InMemoryStore`` already uses for this module, reimplemented locally so this file does
    not depend on another test module's private helper."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="tc688"),
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


@pytest.fixture
def store() -> _InMemoryStore:
    return _InMemoryStore((DOCUMENT_BODY,))


def _real_chunk_text(store: _InMemoryStore) -> str:
    """The real, real-published chunk text for Aspose.Pdf.Document - what ``raw_text`` must equal
    when a caller opts in, read directly from the same manifest ``get_symbol`` itself reads."""
    manifest = store.read_generation("x", _GENERATION_ID)
    documents = manifest.payload["lexical_index"]["documents"]
    (text,) = {d["text"] for d in documents.values() if "FQN: Aspose.Pdf.Document" in d["text"].splitlines()}
    return text


# --------------------------------------------------------------------------------------------
# (a) the opt-in itself: get_symbol()
# --------------------------------------------------------------------------------------------


def test_the_default_omits_raw_text_as_an_attribute_too(store: _InMemoryStore) -> None:
    """Calling get_symbol() with no include_raw_text argument at all (the real default, not just
    an explicit False) leaves raw_text as the sentinel, not real text and not "" - on the
    dataclass itself, before any JSON rendering happens."""
    result = get_symbol(store, SCOPE, "Aspose.Pdf.Document")  # type: ignore[arg-type]
    assert type(result) is SymbolSignature
    assert result.raw_text is RAW_TEXT_OMITTED
    assert result.raw_text != ""


def test_explicit_false_is_identical_to_the_default(store: _InMemoryStore) -> None:
    default_result = get_symbol(store, SCOPE, "Aspose.Pdf.Document")  # type: ignore[arg-type]
    explicit_result = get_symbol(store, SCOPE, "Aspose.Pdf.Document", include_raw_text=False)  # type: ignore[arg-type]
    assert default_result == explicit_result


def test_include_raw_text_true_is_byte_for_byte_identical_to_todays_always_on_behaviour(
    store: _InMemoryStore,
) -> None:
    result = get_symbol(store, SCOPE, "Aspose.Pdf.Document", include_raw_text=True)  # type: ignore[arg-type]
    assert type(result) is SymbolSignature
    assert isinstance(result.raw_text, str)
    assert result.raw_text == _real_chunk_text(store)
    # Every OTHER field is unaffected by the flag - only raw_text's own value changes.
    omitted = get_symbol(store, SCOPE, "Aspose.Pdf.Document")  # type: ignore[arg-type]
    assert dataclasses.replace(result, raw_text=RAW_TEXT_OMITTED) == omitted


def test_the_sentinel_is_never_an_empty_string_or_none(store: _InMemoryStore) -> None:
    """The exact distinction this card's own text requires: a caller checking for "" or checking
    for None would BOTH wrongly conclude the field exists and is merely empty - the sentinel is
    neither, and compares equal to neither."""
    result = get_symbol(store, SCOPE, "Aspose.Pdf.Document")  # type: ignore[arg-type]
    assert result.raw_text != ""
    assert result.raw_text is not None
    assert repr(result.raw_text) != ""


# --------------------------------------------------------------------------------------------
# (b) the response-shape proof: _to_jsonable (what every MCP caller actually receives)
# --------------------------------------------------------------------------------------------


def test_the_omitted_sentinel_is_dropped_from_the_jsonable_response(store: _InMemoryStore) -> None:
    result = get_symbol(store, SCOPE, "Aspose.Pdf.Document")  # type: ignore[arg-type]
    payload = _to_jsonable(result)
    assert "raw_text" not in payload
    # Every other field a plain SymbolSignature carries is still present - this is additive
    # omission of one key, never a change to any other field.
    assert set(payload) == {field.name for field in dataclasses.fields(SymbolSignature)} - {"raw_text"}


def test_opting_in_puts_raw_text_back_in_the_jsonable_response(store: _InMemoryStore) -> None:
    result = get_symbol(store, SCOPE, "Aspose.Pdf.Document", include_raw_text=True)  # type: ignore[arg-type]
    payload = _to_jsonable(result)
    assert payload["raw_text"] == _real_chunk_text(store)
    assert set(payload) == {field.name for field in dataclasses.fields(SymbolSignature)}


def test_a_miss_carries_no_sentinel_and_is_unaffected(store: _InMemoryStore) -> None:
    """NotFound never had a raw_text field at all - the flag has nothing to do and nothing to
    break for a miss, with or without include_raw_text."""
    from foss_mcp.mcp.tools.get_symbol import NotFound

    miss = get_symbol(store, SCOPE, "Aspose.Pdf.Nothing", include_raw_text=True)  # type: ignore[arg-type]
    assert isinstance(miss, NotFound)
    assert "raw_text" not in _to_jsonable(miss)


# --------------------------------------------------------------------------------------------
# (c) the MCP surface: GetSymbolInput's schema and the real server wiring
# --------------------------------------------------------------------------------------------


def test_get_symbol_input_defaults_include_raw_text_to_false_and_it_is_optional() -> None:
    model = GetSymbolInput.model_validate({"fqn": "Aspose.Pdf.Document"})
    assert model.include_raw_text is False
    explicit = GetSymbolInput.model_validate({"fqn": "Aspose.Pdf.Document", "include_raw_text": True})
    assert explicit.include_raw_text is True


def test_the_advertised_schema_carries_include_raw_text_as_optional_boolean() -> None:
    schema = tool_input_schema("get_symbol")
    prop = schema["properties"]["include_raw_text"]
    assert prop["type"] == "boolean"
    assert prop["default"] is False
    assert "include_raw_text" not in schema.get("required", [])
    assert prop["description"]


def test_server_construction_still_succeeds_with_the_new_parameter(store: _InMemoryStore) -> None:
    """The schema (built from GetSymbolInput) and the real wrapper's own signature
    (_wrap_get_symbol's get_symbol_tool) must agree, or create_server itself raises - this is the
    same guard test_tool_surface_truthful.py's test_server_construction_rejects_a_schema_that_
    disagrees_with_its_wrapper exercises for an injected mismatch; here it is the real, unmodified
    pair, proving the two were kept in sync by this card's own change."""
    from foss_mcp.mcp.routing import DeploymentConfig

    server = create_server(DeploymentConfig(family="pdf", platform="net"), store)  # type: ignore[arg-type]
    assert server is not None
