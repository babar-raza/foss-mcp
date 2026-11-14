"""G3/TC-822 (REQ-G3-012, RD2-208): a .NET answer explains the ``[event]`` / ``[static, readonly]`` member tags.

A .NET chunk lists events and (static) readonly fields among the ``Properties:`` and tells them apart only by a
bracket tag (``OnBeforePageGenerate: BeforePageGenerate? [event]``, ``Artwork: X [static, readonly]``), which
reads like a properties list holding non-properties, with nothing explaining the convention. ``get_symbol`` now
carries one ``notes`` line, and ``list_members`` leads with the same line, but ONLY when the answer really
contains a tagged member on a .NET scope. Every other answer keeps its exact shape: no ``notes`` key, no new line.
"""

from __future__ import annotations

import dataclasses
import re
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.server import _to_jsonable
from foss_mcp.mcp.tools.get_symbol import (
    NotedResolvedSignature,
    NotedSignature,
    NotFound,
    ResolvedSignature,
    SymbolSignature,
    extract_fqn,
    get_symbol,
    net_member_tag_note,
)
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

NET = Scope(family="pkg", platform="net")
PYTHON = Scope(family="pkg", platform="python")
_GENERATION_ID = "gen-tc822"
_EVENT_LINE = "OnChanged: ChangedHandler? [event]"
_FIELD_LINE = "Empty: Widget [static, readonly] (read-only)"
_PLAIN_PROPERTY = "Name: string (writable)"


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="org/repo", commit="tc822"),
                evidence_refs=(),
                title="pkg API surface",
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


def _type(fqn: str, *, methods: tuple[str, ...] = (), properties: tuple[str, ...] = ()) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", "Kind: class"]
    for title, items in (("Methods:", methods), ("Properties:", properties)):
        if items:
            lines += ["", title, *(f"  - {item}" for item in items)]
    return "\n".join(lines) + "\n"


def _store() -> _InMemoryStore:
    return _InMemoryStore(
        (
            _type(
                "pkg.Tagged",
                methods=("Run() -> None",),
                properties=(_PLAIN_PROPERTY, _EVENT_LINE, _FIELD_LINE),
            ),
            _type("pkg.EventOnly", properties=(_EVENT_LINE,)),
            _type("pkg.ReadonlyOnly", properties=("Cache: Widget [readonly] (read-only)",)),
            _type(
                "pkg.Plain",
                methods=("Run() -> None",),
                properties=(_PLAIN_PROPERTY, "Zero: Widget [static] (read-only)"),
            ),
            _type(
                "pkg.Indexed", properties=("this[int]: Widget (read-only)", "Count: int [new] (read-only)")
            ),
        )
    )


def test_a_net_type_with_tagged_members_gains_one_notes_line() -> None:
    answer = get_symbol(_store(), NET, "pkg.Tagged")  # type: ignore[arg-type]
    assert isinstance(answer, NotedSignature) and isinstance(answer, SymbolSignature)
    assert len(answer.notes) == 1
    note = answer.notes[0]
    assert "[event] = event" in note
    assert "[static, readonly] = static readonly field" in note
    assert "listed with the properties" in note
    assert "[readonly] =" not in note  # only the tags actually present are explained
    # additive: every pre-existing key keeps its exact value
    assert answer.properties == (_PLAIN_PROPERTY, _EVENT_LINE, _FIELD_LINE)
    assert answer.methods == ("Run() -> None",)
    assert answer.fqn == "pkg.Tagged" and answer.found is True and answer.kind == "class"


def test_only_the_tags_present_are_explained() -> None:
    event_only = get_symbol(_store(), NET, "pkg.EventOnly")  # type: ignore[arg-type]
    assert isinstance(event_only, NotedSignature)
    assert "[event] = event" in event_only.notes[0] and "readonly" not in event_only.notes[0]
    readonly_only = get_symbol(_store(), NET, "pkg.ReadonlyOnly")  # type: ignore[arg-type]
    assert isinstance(readonly_only, NotedSignature)
    assert "[readonly] = readonly field" in readonly_only.notes[0] and "event" not in readonly_only.notes[0]


@pytest.mark.parametrize("fqn", ["pkg.Plain", "pkg.Indexed"])
def test_a_net_type_without_tagged_members_is_untouched(fqn: str) -> None:
    answer = get_symbol(_store(), NET, fqn)  # type: ignore[arg-type]
    assert type(answer) is SymbolSignature  # not even the noted subclass: no `notes` key in its JSON
    assert "notes" not in _to_jsonable(answer)
    members = list_members(_store(), NET, fqn)  # type: ignore[arg-type]
    assert not isinstance(members, NotFound)
    assert not any(line.startswith("Note:") for line in members)


def test_a_non_net_scope_never_gets_the_line_even_with_the_same_tags() -> None:
    answer = get_symbol(_store(), PYTHON, "pkg.Tagged")  # type: ignore[arg-type]
    assert type(answer) is SymbolSignature
    assert "notes" not in _to_jsonable(answer)
    members = list_members(_store(), PYTHON, "pkg.Tagged")  # type: ignore[arg-type]
    assert members == ("Run() -> None", _PLAIN_PROPERTY, _EVENT_LINE, _FIELD_LINE)


def test_existing_keys_are_unchanged_and_notes_is_the_only_addition() -> None:
    noted = get_symbol(_store(), NET, "pkg.Tagged")  # type: ignore[arg-type]
    store = _store()
    # the same chunk through a non-.NET scope is the byte-for-byte "before" answer
    before = get_symbol(store, PYTHON, "pkg.Tagged")  # type: ignore[arg-type]
    assert isinstance(noted, NotedSignature) and isinstance(before, SymbolSignature)
    after_json = _to_jsonable(noted)
    before_json = _to_jsonable(before)
    after_json.pop("notes")
    # `scope` is the only thing that differs between the two calls
    for key in ("scope",):
        after_json.pop(key)
        before_json.pop(key)
    assert after_json == before_json
    assert _to_jsonable(noted)["notes"] == [noted.notes[0]]


def test_list_members_leads_with_the_same_line_then_the_unchanged_members() -> None:
    answer = get_symbol(_store(), NET, "pkg.Tagged")  # type: ignore[arg-type]
    assert isinstance(answer, NotedSignature)
    members = list_members(_store(), NET, "pkg.Tagged")  # type: ignore[arg-type]
    assert not isinstance(members, NotFound)
    assert members[0] == answer.notes[0]
    assert members[1:] == ("Run() -> None", _PLAIN_PROPERTY, _EVENT_LINE, _FIELD_LINE)


def test_a_resolved_member_name_gets_the_line_only_when_the_member_itself_is_tagged() -> None:
    tagged_member = get_symbol(_store(), NET, "Tagged.OnChanged")  # type: ignore[arg-type]
    assert isinstance(tagged_member, NotedResolvedSignature) and isinstance(tagged_member, ResolvedSignature)
    assert tagged_member.member == "OnChanged" and tagged_member.member_lines == (_EVENT_LINE,)
    assert "[event] = event" in tagged_member.notes[0]
    members = list_members(_store(), NET, "Tagged.OnChanged")  # type: ignore[arg-type]
    assert members == (tagged_member.notes[0], _EVENT_LINE)
    # a plain member of a type that ALSO has tagged members: the answer holds no tagged member
    plain_member = get_symbol(_store(), NET, "Tagged.Name")  # type: ignore[arg-type]
    assert type(plain_member) is ResolvedSignature
    assert list_members(_store(), NET, "Tagged.Name") == (_PLAIN_PROPERTY,)  # type: ignore[arg-type]
    # a type resolved by its short name, no member: the type's members are in the answer
    short = get_symbol(_store(), NET, "Tagged")  # type: ignore[arg-type]
    assert isinstance(short, NotedResolvedSignature) and short.member == ""


def test_a_miss_is_still_the_plain_not_found() -> None:
    assert isinstance(get_symbol(_store(), NET, "pkg.Missing"), NotFound)  # type: ignore[arg-type]
    assert isinstance(list_members(_store(), NET, "pkg.Missing"), NotFound)  # type: ignore[arg-type]


def test_the_note_detector_ignores_indexers_generics_and_method_like_text() -> None:
    assert net_member_tag_note(NET, ("this[int]: Widget (read-only)",)) is None
    assert net_member_tag_note(NET, ("Items: List<byte[]> (read-only)",)) is None
    assert net_member_tag_note(NET, ("Count: int [new] (read-only)", "Z: int [static] (read-only)")) is None
    assert net_member_tag_note(NET, ()) is None
    assert net_member_tag_note(PYTHON, (_EVENT_LINE,)) is None
    line = net_member_tag_note(NET, (_EVENT_LINE, _FIELD_LINE))
    assert line is not None and line.startswith("Note:")


def test_the_noted_classes_add_exactly_one_field_to_their_parents() -> None:
    parent = {field.name for field in dataclasses.fields(SymbolSignature)}
    assert {field.name for field in dataclasses.fields(NotedSignature)} == parent | {"notes"}
    resolved = {field.name for field in dataclasses.fields(ResolvedSignature)}
    assert {field.name for field in dataclasses.fields(NotedResolvedSignature)} == resolved | {"notes"}


# --- the measurement over the real, committed corpus ---------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("tc822_corpus") / "manifests")


_TAGGED = re.compile(r"\s\[(?:event|readonly|static, readonly)\]")


def _property_lines(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    if "Properties:" not in lines:
        return ()
    items: list[str] = []
    for line in lines[lines.index("Properties:") + 1 :]:
        if not line.startswith("  - "):
            break
        items.append(line[4:].strip())
    return tuple(items)


def test_real_pdf_net_types_with_events_and_static_readonly_fields_explain_the_tags(
    corpus: ConformanceCorpus,
) -> None:
    pilot = corpus.pilots["pdf__net"]
    page = get_symbol(corpus.store, pilot.scope, "Aspose.Pdf.Page")
    assert isinstance(page, NotedSignature)
    assert any(line.endswith("[event]") for line in page.properties)
    assert "[event] = event" in page.notes[0]
    scheme = get_symbol(corpus.store, pilot.scope, "Aspose.Pdf.Annotations.PDF3DLightingScheme")
    assert isinstance(scheme, NotedSignature)
    assert "[static, readonly] = static readonly field" in scheme.notes[0]
    members = list_members(corpus.store, pilot.scope, "Aspose.Pdf.Page")
    assert not isinstance(members, NotFound)
    assert members[0] == page.notes[0] and members[1:] == page.methods + page.properties + page.members
    # a real .NET type with no tagged property: unchanged
    plain = get_symbol(corpus.store, pilot.scope, "Aspose.Pdf.Annotations.PopupAnnotation")
    assert isinstance(plain, SymbolSignature) and "notes" not in _to_jsonable(plain)


def test_the_line_appears_on_exactly_the_net_types_that_hold_tagged_members_and_nowhere_else(
    corpus: ConformanceCorpus,
) -> None:
    gained: dict[str, int] = {}
    for name, pilot in sorted(corpus.pilots.items()):
        expected = 0
        for document in pilot.symbol_chunks():
            text = document["text"]
            fqn = extract_fqn(text)
            if fqn is None:
                continue
            tagged = any(_TAGGED.search(line) for line in _property_lines(text))
            note = net_member_tag_note(pilot.scope, _property_lines(text))
            assert (note is not None) == (tagged and pilot.scope.platform == "net"), (name, fqn)
            expected += note is not None
        gained[name] = expected
    for name, count in gained.items():
        if not name.endswith("__net"):
            assert count == 0, (name, count)  # every non-.NET pilot is byte-identical
    net_pilots = sorted(name for name in gained if name.endswith("__net"))
    assert len(net_pilots) == 7, net_pilots
    assert gained["pdf__net"] > 0 and gained["slides__net"] > 0 and gained["words__net"] > 0
    assert gained["3d__net"] > 0
