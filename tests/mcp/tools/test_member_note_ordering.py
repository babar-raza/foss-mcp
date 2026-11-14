"""G3/TC-840 (REQ-G3-012): the .NET member-tag note never precedes a limitation or known-defect line.

TC-822 made ``list_members`` lead with ``Note: [static, readonly] = ...`` for a .NET type holding tagged
members, which pushed the ``Limitation: not implemented in this build ...`` line (and the ``Known defect:``
line) of an unsupported type off the front of the answer. An explanatory convention note must come AFTER every
limitation / disclosure line, still ahead of the member lines (TC-822's own position for a note-only answer).
``get_symbol`` keeps the note in its own ``notes`` field, so its ``limitations`` / ``known_defects`` never
contain it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from foss_mcp.indexing import known_defects as known_defects_module
from foss_mcp.indexing.chunk_builder import NOT_IMPLEMENTED_PHRASE, build_chunks_from_api_surface
from foss_mcp.indexing.known_defects import registry_path_for
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import (
    NotedResolvedSignature,
    NotedSignature,
    NotFound,
    SymbolSignature,
    get_symbol,
)
from foss_mcp.mcp.tools.list_members import list_members

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NET = Scope(family="pkg", platform="net")
_GENERATION_ID = "gen-tc840"
_EVENT_LINE = "OnChanged: ChangedHandler? [event]"
_FIELD_LINE = "Empty: Widget [static, readonly] (read-only)"
_LIMITATION = f"Limitation: {NOT_IMPLEMENTED_PHRASE}: the library only declares this type"
_NOTE_PREFIX = "Note: "
_FQN = "pkg.Flagged"
_NOTE_ONLY_FQN = "pkg.NoteOnly"
_FLAGGED_MEMBER = f"Run() -> None  ({NOT_IMPLEMENTED_PHRASE}: throws)"
_FLAGGED_EVENT = f"OnStub: StubHandler? [event]  ({NOT_IMPLEMENTED_PHRASE}: never raised)"


class _Store:
    """One active generation of in-memory chunk texts, standing in for GenerationManifestStore."""

    def __init__(self, texts: list[str]) -> None:
        chunks = [SimpleNamespace(text=text) for text in texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _chunk(fqn: str, *, limitation: str | None, methods: tuple[str, ...], properties: tuple[str, ...]) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", "Kind: class"]
    if limitation is not None:
        lines.append(limitation)
    for title, items in (("Methods:", methods), ("Properties:", properties)):
        if items:
            lines += ["", title, *(f"  - {item}" for item in items)]
    return "\n".join(lines) + "\n"


@pytest.fixture
def store() -> _Store:
    return _Store(
        [
            _chunk(
                _FQN,
                limitation=_LIMITATION,
                methods=(_FLAGGED_MEMBER,),
                properties=(_EVENT_LINE, _FIELD_LINE),
            ),
            _chunk("pkg.FlaggedEvent", limitation=None, methods=(), properties=(_FLAGGED_EVENT,)),
            _chunk(_NOTE_ONLY_FQN, limitation=None, methods=("Run() -> None",), properties=(_EVENT_LINE,)),
        ]
    )


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "known_defects"
    directory.mkdir()
    monkeypatch.setattr(known_defects_module, "REGISTRY_DIR", directory)
    return directory


def _write_defect(registry_dir: Path, ref: str) -> None:
    defect: dict[str, Any] = {
        "id": "PKG-NET-001",
        "subject": {"kind": "class", "refs": [ref]},
        "applies_to": {"package_versions": ["1.0.0"]},
        "summary": "Summary describing PKG-NET-001.",
        "user_action": "Recommended action for PKG-NET-001.",
        "evidence": {
            "repro": "Repro steps for PKG-NET-001.",
            "verified_on": {"package": "Pkg", "version": "1.0.0", "date": "2026-11-14"},
        },
        "status": "open",
    }
    document = {"schema_version": 1, "pilot": "pkg_net", "defects": [defect]}
    path = registry_path_for("pkg", "net", base_dir=registry_dir)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _is_note(line: str) -> bool:
    return line.startswith(_NOTE_PREFIX) and "[event] = event" in line


def test_list_members_puts_limitation_and_known_defect_before_the_tag_note(
    store: _Store, registry_dir: Path
) -> None:
    _write_defect(registry_dir, _FQN)
    members = list_members(store, NET, _FQN)  # type: ignore[arg-type]
    assert not isinstance(members, NotFound)
    assert members[0].startswith("Limitation: " + NOT_IMPLEMENTED_PHRASE)
    assert members[1].startswith("Known defect: ")
    assert _is_note(members[2]) and "[static, readonly] = static readonly field" in members[2]
    assert members[3:] == (_FLAGGED_MEMBER, _EVENT_LINE, _FIELD_LINE)


def test_list_members_limitation_only_still_leads_the_note(store: _Store) -> None:
    members = list_members(store, NET, _FQN)  # type: ignore[arg-type]
    assert not isinstance(members, NotFound)
    assert members[0].startswith("Limitation: " + NOT_IMPLEMENTED_PHRASE)
    assert _is_note(members[1])
    assert members[2:] == (_FLAGGED_MEMBER, _EVENT_LINE, _FIELD_LINE)


def test_get_symbol_keeps_limitations_and_known_defects_apart_from_the_note(
    store: _Store, registry_dir: Path
) -> None:
    _write_defect(registry_dir, _FQN)
    answer = get_symbol(store, NET, _FQN)  # type: ignore[arg-type]
    assert isinstance(answer, NotedSignature)
    assert answer.limitations[0].startswith("Limitation: " + NOT_IMPLEMENTED_PHRASE)
    assert len(answer.known_defects) == 1 and answer.known_defects[0].startswith("Known defect: ")
    assert len(answer.notes) == 1 and _is_note(answer.notes[0])
    assert not any(_is_note(line) for line in (*answer.limitations, *answer.known_defects))


def test_a_tagged_member_that_is_not_implemented_keeps_its_own_line_ahead_of_the_note(store: _Store) -> None:
    tagged = get_symbol(store, NET, "FlaggedEvent.OnStub")  # type: ignore[arg-type]
    assert isinstance(tagged, NotedResolvedSignature)
    assert tagged.member_lines == (_FLAGGED_EVENT,)
    assert list_members(store, NET, "FlaggedEvent.OnStub") == (_FLAGGED_EVENT, tagged.notes[0])  # type: ignore[arg-type]
    # an untagged member of a type that also has tagged ones carries no note at all
    assert list_members(store, NET, "Flagged.Run") == (_FLAGGED_MEMBER,)  # type: ignore[arg-type]
    # a plain tagged member: the note leads its single line (TC-822's position, no limitation to precede it)
    plain = get_symbol(store, NET, "Flagged.OnChanged")  # type: ignore[arg-type]
    assert isinstance(plain, NotedResolvedSignature)
    assert list_members(store, NET, "Flagged.OnChanged") == (plain.notes[0], _EVENT_LINE)  # type: ignore[arg-type]


def test_a_type_with_only_the_note_keeps_it_first_among_the_member_lines(store: _Store) -> None:
    answer = get_symbol(store, NET, _NOTE_ONLY_FQN)  # type: ignore[arg-type]
    assert isinstance(answer, NotedSignature) and answer.limitations == () and answer.known_defects == ()
    members = list_members(store, NET, _NOTE_ONLY_FQN)  # type: ignore[arg-type]
    assert not isinstance(members, NotFound)
    assert members == (answer.notes[0], "Run() -> None", _EVENT_LINE)


@pytest.fixture(scope="module")
def real_3d_net_store() -> _Store:
    fixture = json.loads((FIXTURES / "3d_net" / "api_surface.json").read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title="3d", max_types=1000)
    return _Store([chunk.text for chunk in chunks])


def test_real_3d_net_unsupported_types_lead_with_the_limitation_in_both_tools(
    real_3d_net_store: _Store,
) -> None:
    scope = Scope(family="3d", platform="net")
    for fqn in ("Aspose.ThreeD.Entities.PolygonModifier", "Aspose.ThreeD.Formats.PdfFormat"):
        answer = get_symbol(real_3d_net_store, scope, fqn)  # type: ignore[arg-type]
        assert isinstance(answer, SymbolSignature)
        assert answer.limitations[0].startswith("Limitation: " + NOT_IMPLEMENTED_PHRASE)
        members = list_members(real_3d_net_store, scope, fqn)  # type: ignore[arg-type]
        assert not isinstance(members, NotFound)
        assert members[0] == answer.limitations[0]
        for note in getattr(answer, "notes", ()):
            assert members.index(note) > members.index(answer.limitations[0])
