"""Member-level lookup in get_symbol and list_members (TC-540, G3/REQ-G3-008).

An agent that has read an example calls ``get_symbol`` with the name it saw - ``Document.Save`` - and
must get the owning type's signature plus every line of that member, or a miss that says what it was
looking for. Offline and deterministic: in-memory generations built from hand-written type chunks.
"""

from __future__ import annotations

import dataclasses
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.server import _FIELD_DESCRIPTIONS, _to_jsonable, tool_description, tool_input_schema
from foss_mcp.mcp.tools import get_symbol as get_symbol_module
from foss_mcp.mcp.tools.get_symbol import NotFound, ResolvedSignature, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.mcp.tools.search_symbols import suggest_similar_fqns
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc540"
_TODAYS_REASON = "no symbol with this exact FQN is in this index"


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="tc540"),
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


def _type(
    fqn: str,
    *,
    kind: str = "class_declaration",
    methods: tuple[str, ...] = (),
    properties: tuple[str, ...] = (),
    members: tuple[str, ...] = (),
    header: tuple[str, ...] = (),
    coverage: str | None = None,
) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", f"Kind: {kind}", *header]
    for title, items in (("Methods:", methods), ("Properties:", properties), ("Members:", members)):
        if items:
            lines += ["", title, *(f"  - {item}" for item in items)]
    if coverage is not None:
        lines += ["", f"API-Coverage: {coverage}"]
    return "\n".join(lines) + "\n"


_SAVE_LINES = (
    "Save(path: string) -> void",
    "Save(stream: Stream) -> void",
    "Save(path: string, format: SaveFormat) -> void",
)


def _bodies(coverage: str | None = None) -> tuple[str, ...]:
    return (
        _type(
            "Aspose.Pdf.Document",
            methods=(*_SAVE_LINES, "Close() -> void", "Flush() -> void", "LoadAsync(path: string) -> Task"),
            properties=("Title: string (writable)", "PageCount: int (read-only)"),
            coverage=coverage,
        ),
        _type("Aspose.Pdf.Page", methods=("Rotate(degrees: int) -> void",), coverage=coverage),
        _type("Aspose.Pdf.Facades.Page", methods=("Bind(path: string) -> void",), coverage=coverage),
        _type("Aspose.Pdf.Stamp", methods=("Apply(page: Page) -> void",), coverage=coverage),
        _type("Aspose.Pdf.SaveFormat", kind="enum", members=("Pdf = 0", "Html = 1"), coverage=coverage),
        _type("Aspose.Email.EmailClient", methods=("Connect() -> void",), coverage=coverage),
        _type(
            "Aspose.Email.EmailClientMailExtensions",
            methods=("ListMailboxesAsync(client: EmailClient) -> Task",),
            coverage=coverage,
        ),
        _type("Gfx::Widget", methods=("Draw(canvas: Canvas) -> void", "Draw() -> void"), coverage=coverage),
        "## Example: Save a Document\n\nFQN: Example: Save a Document\nKind: verified_example\nExample:\ndoc.Save(path);\n",
        "## Doc: Document.Save\n\nFQN: Doc: Document.Save\nKind: documentation\nHow saving works.\n",
    )


@pytest.fixture(scope="module")
def store() -> _InMemoryStore:
    return _InMemoryStore(_bodies())


def _resolved(result: Any) -> ResolvedSignature:
    assert isinstance(result, ResolvedSignature), result
    return result


def _missed(result: Any) -> NotFound:
    assert isinstance(result, NotFound), result
    assert result.found is False
    return result


# --------------------------------------------------------------------------------------------
# what does not change
# --------------------------------------------------------------------------------------------


def test_an_exact_fqn_is_still_a_plain_symbol_signature_without_extra_json_keys(
    store: _InMemoryStore,
) -> None:
    result = get_symbol(store, SCOPE, "Aspose.Pdf.Document")  # type: ignore[arg-type]
    assert type(result) is SymbolSignature
    assert set(_to_jsonable(result)) == {field.name for field in dataclasses.fields(SymbolSignature)}
    assert "member_lines" not in _to_jsonable(result)


def test_an_unknown_owner_keeps_todays_notfound_reason_and_suggestions(store: _InMemoryStore) -> None:
    known = [
        "Aspose.Pdf.Document",
        "Aspose.Pdf.Page",
        "Aspose.Pdf.Facades.Page",
        "Aspose.Pdf.Stamp",
        "Aspose.Pdf.SaveFormat",
        "Aspose.Email.EmailClient",
        "Aspose.Email.EmailClientMailExtensions",
        "Gfx::Widget",
    ]
    for name in ("Nothing.Save", "Aspose.Pdf.Nothing", "CompletelyUnrelatedXyzzyWombatTerm"):
        result = _missed(get_symbol(store, SCOPE, name))  # type: ignore[arg-type]
        assert result.reason == _TODAYS_REASON
        assert result.suggestions == suggest_similar_fqns(known, name)


@pytest.mark.parametrize("name", ["Document.", ".Save", "Aspose.Pdf.Document.", "Document::", "::Save", "."])
def test_a_trailing_or_leading_dot_is_todays_notfound(store: _InMemoryStore, name: str) -> None:
    result = _missed(get_symbol(store, SCOPE, name))  # type: ignore[arg-type]
    assert result.reason == _TODAYS_REASON


@pytest.mark.parametrize("name", ["Example: Save a Document", "Doc: Document.Save"])
def test_pseudo_symbols_never_resolve_as_an_owner(store: _InMemoryStore, name: str) -> None:
    only_pseudo = _InMemoryStore(
        (
            "## Example: Widget\n\nFQN: Example: Widget\nKind: verified_example\n\nMethods:\n  - Draw() -> void\n",
        )
    )
    # An example is not a type: neither its whole name nor "Widget.Draw" resolves to it.
    for request in ("Widget", "Widget.Draw", "Example: Widget.Draw"):
        assert _missed(get_symbol(only_pseudo, SCOPE, request)).reason == _TODAYS_REASON  # type: ignore[arg-type]
    # The pseudo FQNs of the main fixture are exact-matched to nothing, as before.
    assert _missed(get_symbol(store, SCOPE, name)).reason == _TODAYS_REASON  # type: ignore[arg-type]


def test_list_members_of_an_exact_fqn_and_of_a_bare_short_type_are_unchanged(store: _InMemoryStore) -> None:
    exact = list_members(store, SCOPE, "Aspose.Pdf.Document")  # type: ignore[arg-type]
    assert exact == (
        *_SAVE_LINES,
        "Close() -> void",
        "Flush() -> void",
        "LoadAsync(path: string) -> Task",
        "Title: string (writable)",
        "PageCount: int (read-only)",
    )
    assert list_members(store, SCOPE, "Document") == exact  # type: ignore[arg-type]
    assert list_members(store, SCOPE, "SaveFormat") == ("Pdf = 0", "Html = 1")  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "member", "lines"),
    [
        ("Aspose.Pdf.Document.Save", "Save", _SAVE_LINES),
        ("Aspose.Pdf.Document.Close", "Close", ("Close() -> void",)),
        ("Aspose.Pdf.Page.Rotate", "Rotate", ("Rotate(degrees: int) -> void",)),
        ("Aspose.Pdf.Facades.Page.Bind", "Bind", ("Bind(path: string) -> void",)),
        ("Aspose.Email.EmailClient.Connect", "Connect", ("Connect() -> void",)),
    ],
)
def test_type_fqn_dot_member_resolves_to_the_owner_with_every_overload_line(
    store: _InMemoryStore, name: str, member: str, lines: tuple[str, ...]
) -> None:
    result = _resolved(get_symbol(store, SCOPE, name))  # type: ignore[arg-type]
    assert name == f"{result.fqn}.{member}"
    assert result.resolved_from == name
    assert result.member == member
    assert result.member_lines == lines
    assert result.kind == "class_declaration"
    assert member in "".join(result.methods)  # the owning type's whole signature rides along
    assert result.raw_text.startswith(f"FQN: {result.fqn}\n")


@pytest.mark.parametrize(
    ("name", "owner", "member"),
    [
        ("Document.Save", "Aspose.Pdf.Document", "Save"),
        ("Stamp.Apply", "Aspose.Pdf.Stamp", "Apply"),
        ("EmailClient.Connect", "Aspose.Email.EmailClient", "Connect"),
        (
            "EmailClientMailExtensions.ListMailboxesAsync",
            "Aspose.Email.EmailClientMailExtensions",
            "ListMailboxesAsync",
        ),
    ],
)
def test_short_type_dot_member_resolves_when_one_fqn_ends_with_it(
    store: _InMemoryStore, name: str, owner: str, member: str
) -> None:
    result = _resolved(get_symbol(store, SCOPE, name))  # type: ignore[arg-type]
    assert (result.fqn, result.member) == (owner, member)
    assert result.member_lines


@pytest.mark.parametrize("name", ["Gfx::Widget::Draw", "Widget::Draw", "Widget.Draw", "Gfx::Widget.Draw"])
def test_double_colon_separators_resolve(store: _InMemoryStore, name: str) -> None:
    result = _resolved(get_symbol(store, SCOPE, name))  # type: ignore[arg-type]
    assert result.fqn == "Gfx::Widget"
    assert result.member == "Draw"
    assert result.member_lines == ("Draw(canvas: Canvas) -> void", "Draw() -> void")


@pytest.mark.parametrize("name", ["Pdf.Document.Save", "Pdf.Facades.Page.Bind", "Facades.Page.Bind"])
def test_a_partially_qualified_owner_resolves(store: _InMemoryStore, name: str) -> None:
    result = _resolved(get_symbol(store, SCOPE, name))  # type: ignore[arg-type]
    assert result.fqn.endswith(name.rsplit(".", 1)[0])
    assert result.member_lines


def test_a_property_and_an_enum_member_resolve(store: _InMemoryStore) -> None:
    title = _resolved(get_symbol(store, SCOPE, "Document.Title"))  # type: ignore[arg-type]
    assert title.member_lines == ("Title: string (writable)",)
    count = _resolved(get_symbol(store, SCOPE, "Aspose.Pdf.Document.PageCount"))  # type: ignore[arg-type]
    assert count.member_lines == ("PageCount: int (read-only)",)
    html = _resolved(get_symbol(store, SCOPE, "SaveFormat.Html"))  # type: ignore[arg-type]
    assert (html.fqn, html.kind, html.member_lines) == ("Aspose.Pdf.SaveFormat", "enum", ("Html = 1",))


def test_a_bare_unique_short_type_name_resolves_with_an_empty_member(store: _InMemoryStore) -> None:
    result = _resolved(get_symbol(store, SCOPE, "Document"))  # type: ignore[arg-type]
    assert (result.fqn, result.member, result.member_lines, result.resolved_from) == (
        "Aspose.Pdf.Document",
        "",
        (),
        "Document",
    )
    assert result.methods == get_symbol(store, SCOPE, "Aspose.Pdf.Document").methods  # type: ignore[arg-type, union-attr]
    assert _resolved(get_symbol(store, SCOPE, "Widget")).fqn == "Gfx::Widget"  # type: ignore[arg-type]


def test_a_python_reexport_shell_and_its_definition_count_as_one_type() -> None:
    shared = ("add_worksheet(name: str) -> Worksheet", "save(path: str) -> None")
    reexport = _InMemoryStore(
        (
            _type("pkg.Workbook", methods=shared, header=("Defined-In: pkg.workbook.Workbook",)),
            _type("pkg.workbook.Workbook", methods=shared, header=("Exported-As: pkg.Workbook",)),
        )
    )
    member = _resolved(get_symbol(reexport, SCOPE, "Workbook.add_worksheet"))  # type: ignore[arg-type]
    assert member.fqn == "pkg.workbook.Workbook"  # the definition, not the shell
    assert member.member_lines == ("add_worksheet(name: str) -> Worksheet",)
    bare = _resolved(get_symbol(reexport, SCOPE, "Workbook"))  # type: ignore[arg-type]
    assert (bare.fqn, bare.member) == ("pkg.workbook.Workbook", "")
    # The exact FQN of the shell is still an exact hit on the shell.
    assert type(get_symbol(reexport, SCOPE, "pkg.Workbook")) is SymbolSignature  # type: ignore[arg-type]


def test_two_memberless_types_with_one_short_name_are_not_collapsed() -> None:
    twins = _InMemoryStore((_type("a.Marker", kind="enum"), _type("b.Marker", kind="enum")))
    result = _missed(get_symbol(twins, SCOPE, "Marker"))  # type: ignore[arg-type]
    assert result.suggestions == ("a.Marker", "b.Marker")


# --------------------------------------------------------------------------------------------
# misses that say what they were looking for
# --------------------------------------------------------------------------------------------


def test_member_matching_is_case_sensitive_and_a_miss_suggests_the_cased_name(store: _InMemoryStore) -> None:
    result = _missed(get_symbol(store, SCOPE, "Document.save"))  # type: ignore[arg-type]
    assert result.reason == "Aspose.Pdf.Document is in this index, but it declares no member named 'save'"
    assert result.suggestions[0] == "Aspose.Pdf.Document.Save"
    lowered = _missed(get_symbol(store, SCOPE, "Document.title"))  # type: ignore[arg-type]
    assert lowered.suggestions[0] == "Aspose.Pdf.Document.Title"


def test_a_missing_member_says_the_type_was_found_and_lists_did_you_mean_members(
    store: _InMemoryStore,
) -> None:
    result = _missed(get_symbol(store, SCOPE, "Aspose.Pdf.Document.Clos"))  # type: ignore[arg-type]
    assert result.reason == "Aspose.Pdf.Document is in this index, but it declares no member named 'Clos'"
    assert result.fqn == "Aspose.Pdf.Document.Clos"
    assert "Aspose.Pdf.Document.Close" in result.suggestions
    assert len(result.suggestions) <= 5
    assert len(set(result.suggestions)) == len(result.suggestions)
    # Nothing near at all: the type is still named, and no suggestion is invented.
    far = _missed(get_symbol(store, SCOPE, "Aspose.Pdf.Document.Qqqqqqqqq"))  # type: ignore[arg-type]
    assert far.reason.startswith("Aspose.Pdf.Document is in this index")
    assert far.suggestions == ()


def test_async_twin_is_suggested_in_both_directions(store: _InMemoryStore) -> None:
    sync_asked = _missed(get_symbol(store, SCOPE, "Document.Load"))  # type: ignore[arg-type]
    assert sync_asked.suggestions[0] == "Aspose.Pdf.Document.LoadAsync"
    async_asked = _missed(get_symbol(store, SCOPE, "Document.FlushAsync"))  # type: ignore[arg-type]
    assert async_asked.suggestions[0] == "Aspose.Pdf.Document.Flush"


def test_a_member_declared_by_another_type_is_suggested_with_its_full_name(store: _InMemoryStore) -> None:
    result = _missed(get_symbol(store, SCOPE, "Stamp.Save"))  # type: ignore[arg-type]
    assert result.reason == "Aspose.Pdf.Stamp is in this index, but it declares no member named 'Save'"
    assert result.suggestions[0] == "Aspose.Pdf.Document.Save"
    # An extension class that declares the Async twin is the nearest, with its full name.
    extension = _missed(get_symbol(store, SCOPE, "EmailClient.ListMailboxes"))  # type: ignore[arg-type]
    assert extension.reason == (
        "Aspose.Email.EmailClient is in this index, but it declares no member named 'ListMailboxes'"
    )
    assert extension.suggestions[0] == "Aspose.Email.EmailClientMailExtensions.ListMailboxesAsync"


def test_a_short_name_shared_by_two_types_is_an_explicit_notfound_listing_the_full_names(
    store: _InMemoryStore,
) -> None:
    declared = _missed(get_symbol(store, SCOPE, "Page.Rotate"))  # type: ignore[arg-type]
    assert declared.reason == "'Page' names 2 different types in this index; use one of the full names listed"
    assert declared.suggestions == ("Aspose.Pdf.Page.Rotate",)
    undeclared = _missed(get_symbol(store, SCOPE, "Page.Missing"))  # type: ignore[arg-type]
    assert undeclared.suggestions == ("Aspose.Pdf.Facades.Page.Missing", "Aspose.Pdf.Page.Missing")


def test_a_bare_shared_short_name_is_a_notfound_listing_the_types(store: _InMemoryStore) -> None:
    result = _missed(get_symbol(store, SCOPE, "Page"))  # type: ignore[arg-type]
    assert result.reason == "'Page' names 2 different types in this index; use one of the full names listed"
    assert result.suggestions == ("Aspose.Pdf.Facades.Page", "Aspose.Pdf.Page")


def test_a_shared_name_lists_at_most_five_full_names() -> None:
    many = _InMemoryStore(
        tuple(_type(f"ns{index}.Thing", methods=(f"Do{index}() -> void",)) for index in range(8))
    )
    result = _missed(get_symbol(many, SCOPE, "Thing"))  # type: ignore[arg-type]
    assert result.reason == "'Thing' names 8 different types in this index; use one of the full names listed"
    assert result.suggestions == tuple(f"ns{index}.Thing" for index in range(5))


def test_every_member_miss_keeps_the_coverage_note() -> None:
    partial = _InMemoryStore(_bodies(coverage="300 of 971 types"))
    expected = "the API index holds 300 of 971 types; a type missing from it may still exist upstream"
    for name in ("Document.Nothing", "Page.Rotate", "Page", "Nothing.Save"):
        result = _missed(get_symbol(partial, SCOPE, name))  # type: ignore[arg-type]
        assert result.coverage_note == expected, name
        assert _missed(list_members(partial, SCOPE, name)).coverage_note == expected  # type: ignore[arg-type]
    complete = _missed(get_symbol(_InMemoryStore(_bodies()), SCOPE, "Document.Nothing"))  # type: ignore[arg-type]
    assert complete.coverage_note is None


# --------------------------------------------------------------------------------------------
# list_members and the wire shape
# --------------------------------------------------------------------------------------------


def test_list_members_of_type_dot_member_returns_only_that_members_lines(store: _InMemoryStore) -> None:
    assert list_members(store, SCOPE, "Document.Save") == _SAVE_LINES  # type: ignore[arg-type]
    assert list_members(store, SCOPE, "Aspose.Pdf.Document.Title") == ("Title: string (writable)",)  # type: ignore[arg-type]
    assert list_members(store, SCOPE, "Widget::Draw") == ("Draw(canvas: Canvas) -> void", "Draw() -> void")  # type: ignore[arg-type]
    assert isinstance(list_members(store, SCOPE, "Document.Save"), tuple)  # type: ignore[arg-type]
    assert isinstance(_missed(list_members(store, SCOPE, "Document.Nothing")), NotFound)  # type: ignore[arg-type]


def test_resolved_signature_json_adds_exactly_three_keys(store: _InMemoryStore) -> None:
    plain = _to_jsonable(get_symbol(store, SCOPE, "Aspose.Pdf.Document"))  # type: ignore[arg-type]
    resolved = _to_jsonable(get_symbol(store, SCOPE, "Document.Save"))  # type: ignore[arg-type]
    assert set(resolved) - set(plain) == {"resolved_from", "member", "member_lines"}
    assert set(plain) <= set(resolved)
    assert resolved["resolved_from"] == "Document.Save"
    assert resolved["member"] == "Save"
    assert resolved["member_lines"] == list(_SAVE_LINES)
    assert resolved["fqn"] == "Aspose.Pdf.Document"


def test_get_symbol_and_list_members_descriptions_and_fqn_fields_name_type_dot_member_and_keep_the_old_phrase() -> (
    None
):
    for tool_name in ("get_symbol", "list_members"):
        text = tool_description(tool_name)
        assert "<Type>.<member>" in text
        assert "no FQN of its own" in text or "has none of its own" in text
        field = tool_input_schema(tool_name)["properties"]["fqn"]["description"]
        assert "<Type>.<member>" in field
        assert "no FQN of its own" in field
        assert "Verify it with search_symbols if unsure." in field
    assert "member_lines" in tool_description("get_symbol")
    assert "'::'" in tool_description("get_symbol")
    assert "<Type>.<member> is accepted." in _FIELD_DESCRIPTIONS["fqn"]
    assert "no FQN of its own" in _FIELD_DESCRIPTIONS["fqn"]
    assert "just that member's lines (all its overloads)" in tool_description("list_members")
    assert "Document.save" not in tool_description("get_symbol") + tool_description("list_members")


def test_the_two_tools_stay_the_real_registry_entrypoints() -> None:
    # The resolution lives in get_symbol, which the real tool registry calls; list_members calls get_symbol.
    assert "get_symbol(store, scope, fqn)" in inspect.getsource(server_module._wrap_get_symbol)
    assert "list_members(store, scope, fqn)" in inspect.getsource(server_module._wrap_list_members)
    assert "get_symbol(store, scope, fqn)" in inspect.getsource(inspect.getmodule(list_members))  # type: ignore[arg-type]


def test_the_member_hit_is_decided_by_the_exact_name_test() -> None:
    source = inspect.getsource(get_symbol_module)
    assert source.count("if member in declared:") == 1
