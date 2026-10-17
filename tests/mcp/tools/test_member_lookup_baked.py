"""Member-level lookup against the REAL baked generations (TC-540, G3/REQ-G3-008).

Every call goes through the real ``tools/call`` handler of a server built by ``create_server`` over a
COPY of the committed scope directories of infra/demo-manifests, so the proof covers the entrypoint
registry and not only the helper. Expectations are derived from the committed generations at test
time; no member name is hard-coded. Offline: the embedding gateway settings are removed.
"""

from __future__ import annotations

import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import create_server
from foss_mcp.mcp.tools.search_symbols import _NON_SYMBOL_FQN_PREFIXES, scope_key

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
PILOTS = sorted(path.name for path in DEMO_MANIFESTS.glob("*__self_extracted") if path.is_dir())
GUARD_SECONDS = 120
SAMPLES_PER_PILOT = 5
MEMBER_BLOCKS = ("Methods:", "Properties:", "Members:")
_FQN = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_DEFINED_IN = re.compile(r"^Defined-In:\s*(.+)$", re.MULTILINE)
_NAME_END = re.compile(r"[\s(\[:]")
_FQN_SEGMENT = re.compile(r"\.|::")
# The one baked pilot whose extraction found no class member at all (it has no method or property).
NO_MEMBER_PILOT = "tex__python__self_extracted"


def _declared(text: str) -> dict[str, list[str]]:
    """Member name -> rendered lines, parsed straight from a chunk's text (independent of the product)."""
    found: dict[str, list[str]] = {}
    block = ""
    for line in text.splitlines():
        if line in MEMBER_BLOCKS:
            block = line
        elif block and line.startswith("  - "):
            item = line[4:].strip()
            name = _NAME_END.split(item, maxsplit=1)[0]
            if name:
                found.setdefault(name, []).append(item)
        else:
            block = ""
    return found


@dataclass(frozen=True)
class _Type:
    fqn: str
    text: str
    members: dict[str, list[str]]

    @property
    def sep(self) -> str:
        return "::" if "::" in self.fqn else "."

    @property
    def short(self) -> str:
        return _FQN_SEGMENT.split(self.fqn)[-1]

    @property
    def defined_in(self) -> str | None:
        match = _DEFINED_IN.search(self.text)
        return match.group(1).strip() if match else None


class _Baked:
    def __init__(self, root: Path) -> None:
        self.store = GenerationManifestStore(root)
        self._servers: dict[str, Any] = {}
        self._types: dict[str, list[_Type]] = {}

    @staticmethod
    def scope(pilot: str) -> Scope:
        family, platform, _kind = pilot.split("__")
        return Scope(family=family, platform=platform)

    def types(self, pilot: str) -> list[_Type]:
        if pilot not in self._types:
            key = scope_key(self.scope(pilot), "self_extracted")
            generation_id = self.store.read_active(key)
            assert generation_id is not None, pilot
            documents = self.store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
            found = []
            for document in documents.values():
                match = _FQN.search(document["text"])
                if match is None or match.group(1).strip().startswith(_NON_SYMBOL_FQN_PREFIXES):
                    continue
                found.append(_Type(match.group(1).strip(), document["text"], _declared(document["text"])))
            self._types[pilot] = found
        return self._types[pilot]

    def call(self, pilot: str, tool: str, fqn: str) -> Any:
        if pilot not in self._servers:
            scope = self.scope(pilot)
            self._servers[pilot] = create_server(
                DeploymentConfig(family=scope.family, platform=scope.platform), self.store
            )
        entry = self._servers[pilot].get_request_handler("tools/call")
        assert entry is not None

        async def run() -> CallToolResult:
            with anyio.fail_after(GUARD_SECONDS):
                return await entry.handler(None, CallToolRequestParams(name=tool, arguments={"fqn": fqn}))

        result = anyio.run(run)
        assert result.is_error is False, result.content
        assert result.structured_content is not None
        return result.structured_content["result"]


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def baked(tmp_path_factory: pytest.TempPathFactory) -> _Baked:
    root = tmp_path_factory.mktemp("baked-scopes")
    for pilot in PILOTS:
        shutil.copytree(DEMO_MANIFESTS / pilot, root / pilot)
    return _Baked(root)


def _is_resolved(answer: Any) -> bool:
    return isinstance(answer, dict) and answer.get("found", True) is not False and "member" in answer


def _samples(baked: _Baked, pilot: str) -> list[tuple[_Type, str]]:
    pool = [(kind, name) for kind in baked.types(pilot) for name in kind.members]
    return random.Random(540).sample(pool, min(SAMPLES_PER_PILOT, len(pool)))


def test_there_are_thirty_two_baked_pilots() -> None:
    # re-pinned by TC-551: 40 -> 32
    assert len(PILOTS) == 32


def test_sampled_member_names_resolve_in_both_forms_through_call_tool(baked: _Baked) -> None:
    sampled = 0
    short_resolved = 0
    for pilot in PILOTS:
        for kind, member in _samples(baked, pilot):
            sampled += 1
            full = baked.call(pilot, "get_symbol", f"{kind.fqn}{kind.sep}{member}")
            assert _is_resolved(full), (pilot, kind.fqn, member, full)
            assert full["member"] == member and full["fqn"] == kind.fqn, (pilot, full)
            assert full["member_lines"], (pilot, kind.fqn, member)

            short = baked.call(pilot, "get_symbol", f"{kind.short}{kind.sep}{member}")
            if _is_resolved(short):
                short_resolved += 1
                assert short["member"] == member and short["member_lines"], (pilot, short)
                continue
            # A shared short name is an explicit miss that lists the full name of the type asked for
            # (or the type a re-export of it is defined in), never a silent miss.
            assert short["found"] is False, (pilot, short)
            accepted = {f"{kind.fqn}{kind.sep}{member}"}
            if kind.defined_in:
                accepted.add(f"{kind.defined_in}{kind.sep}{member}")
            assert accepted & set(short["suggestions"]), (pilot, kind.fqn, member, short)
            assert short["reason"], (pilot, short)
    # re-pinned by TC-551: 195 -> 150
    assert sampled >= 150
    assert short_resolved >= 0.9 * sampled


def test_the_member_with_the_most_overloads_returns_all_its_lines_in_both_forms(baked: _Baked) -> None:
    best: tuple[int, str, _Type, str] | None = None
    for pilot in PILOTS:
        types = baked.types(pilot)
        carried: dict[str, int] = {}
        for kind in types:
            carried[kind.short] = carried.get(kind.short, 0) + 1
        for kind in types:
            if carried[kind.short] != 1:
                continue  # a short name shared by two types is a legitimate miss, not this test's subject
            for name, lines in kind.members.items():
                if best is None or len(lines) > best[0]:
                    best = (len(lines), pilot, kind, name)
    assert best is not None
    count, pilot, kind, member = best
    assert count >= 2
    lines = kind.members[member]
    for name in (f"{kind.fqn}{kind.sep}{member}", f"{kind.short}{kind.sep}{member}"):
        answer = baked.call(pilot, "get_symbol", name)
        assert _is_resolved(answer), (pilot, name, answer)
        assert answer["member_lines"] == lines
        assert answer["fqn"] == kind.fqn
        assert baked.call(pilot, "list_members", name) == lines


def test_a_python_reexport_shell_and_its_definition_resolve_by_short_name(baked: _Baked) -> None:
    cases: list[tuple[str, _Type, _Type]] = []
    for pilot in PILOTS:
        if not pilot.endswith("__python__self_extracted"):
            continue
        types = baked.types(pilot)
        for shell in types:
            if not shell.defined_in or not shell.members:
                continue
            carriers = [
                kind for kind in types if kind.fqn == shell.short or kind.fqn.endswith((f".{shell.short}",))
            ]
            definition = next((kind for kind in carriers if kind.fqn == shell.defined_in), None)
            if definition is not None and len(carriers) == 2 and definition.members == shell.members:
                cases.append((pilot, shell, definition))
    assert cases, "no baked Python pilot carries a re-export shell next to its definition"
    for pilot, shell, definition in cases[:: max(1, len(cases) // 20)]:
        member = next(iter(shell.members))
        answer = baked.call(pilot, "get_symbol", f"{shell.short}.{member}")
        assert _is_resolved(answer), (pilot, shell.fqn, member, answer)
        assert answer["fqn"] == definition.fqn  # counted as one type, answered with the definition
        assert answer["member_lines"] == definition.members[member]


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_first_declared_member_of_every_pilot_resolves_by_its_full_name(
    baked: _Baked, pilot: str
) -> None:
    types = baked.types(pilot)
    assert types, pilot
    with_members = [kind for kind in types if kind.members]
    if not with_members:
        # The one pilot whose extraction declares no member: its types still answer by their own FQN.
        assert pilot == NO_MEMBER_PILOT
        exact = baked.call(pilot, "get_symbol", types[0].fqn)
        assert exact["fqn"] == types[0].fqn and "member" not in exact
        return
    kind = with_members[0]
    member = next(iter(kind.members))
    answer = baked.call(pilot, "get_symbol", f"{kind.fqn}{kind.sep}{member}")
    assert _is_resolved(answer), (pilot, answer)
    assert (answer["fqn"], answer["member"]) == (kind.fqn, member)
    assert answer["member_lines"] == kind.members[member]
    assert baked.call(pilot, "list_members", f"{kind.fqn}{kind.sep}{member}") == kind.members[member]
