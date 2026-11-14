"""G3/TC-794 (REQ-G3-012, RD2-254): ``get_symbol`` / ``list_members`` resolve a Python re-export shell.

Since TC-686 a re-export shell (``aspose.slides_foss.Presentation``, the public import path agents
are taught) is a thin pointer chunk - ``FQN``/``Kind``/``Defined-In`` and nothing else - and TC-688
made ``raw_text`` opt-in, so an exact ``get_symbol`` hit on a shell answered ``found=True`` with no
methods, properties or bases: an empty class. These tests prove the exact-match branch now answers a
shell with its DEFINITION's real signature, under the requested ``fqn`` plus an explicit
``canonical_fqn``; that a shell with no resolvable definition behaves exactly as before; and that
every real shell of every real Python pilot now answers with what its definition declares.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import (
    NotFound,
    ResolvedSignature,
    SymbolSignature,
    extract_defined_in,
    extract_fqn,
    get_symbol,
)
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

SCOPE = Scope(family="pkg", platform="python")
_GENERATION_ID = "gen-tc794"


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="org/repo", commit="tc794"),
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
    bases: tuple[str, ...] = (),
    methods: tuple[str, ...] = (),
    properties: tuple[str, ...] = (),
    header: tuple[str, ...] = (),
) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", f"Kind: {kind}", *header]
    for title, items in (("Bases:", bases), ("Methods:", methods), ("Properties:", properties)):
        if items:
            lines += ["", title, *(f"  - {item}" for item in items)]
    return "\n".join(lines) + "\n"


def _thin_shell(fqn: str, *, defined_in: str) -> str:
    """Exactly the I4 pointer convention: FQN/Kind/Defined-In only - no member content at all."""
    return _type(fqn, header=(f"Defined-In: {defined_in}",))


def _definition(fqn: str, *, exported_as: str) -> str:
    return _type(
        fqn,
        bases=("pkg.base.Root",),
        methods=("bind(path: str) -> None", "close() -> None"),
        properties=("name: str",),
        header=(f"Exported-As: {exported_as}",),
    )


def test_a_shell_answers_with_its_definitions_signature_under_the_requested_fqn() -> None:
    store = _InMemoryStore(
        (
            _thin_shell("pkg.Facade", defined_in="pkg.facades.Facade"),
            _definition("pkg.facades.Facade", exported_as="pkg.Facade"),
        )
    )
    answer = get_symbol(store, SCOPE, "pkg.Facade")  # type: ignore[arg-type]
    assert isinstance(answer, SymbolSignature)
    assert answer.found is True
    assert not isinstance(answer, ResolvedSignature)
    assert answer.fqn == "pkg.Facade"  # still the name that was asked for
    assert answer.canonical_fqn == "pkg.facades.Facade"
    assert answer.kind == "class"
    assert answer.bases == ("pkg.base.Root",)
    assert answer.methods == ("bind(path: str) -> None", "close() -> None")
    assert answer.properties == ("name: str",)
    # the definition's own exact hit is untouched: a plain SymbolSignature with the same content
    definition = get_symbol(store, SCOPE, "pkg.facades.Facade")  # type: ignore[arg-type]
    assert type(definition) is SymbolSignature and definition.canonical_fqn is None
    assert (definition.bases, definition.methods, definition.properties, definition.kind) == (
        answer.bases,
        answer.methods,
        answer.properties,
        answer.kind,
    )
    # the content is the definition's, so it cites the definition's chunk
    assert (answer.doc_id, answer.chunk_id) == (definition.doc_id, definition.chunk_id)


def test_raw_text_stays_opt_in_and_is_the_definitions_when_asked_for() -> None:
    store = _InMemoryStore(
        (
            _thin_shell("pkg.Facade", defined_in="pkg.facades.Facade"),
            _definition("pkg.facades.Facade", exported_as="pkg.Facade"),
        )
    )
    default = get_symbol(store, SCOPE, "pkg.Facade")  # type: ignore[arg-type]
    assert isinstance(default, SymbolSignature)
    assert not isinstance(default.raw_text, str)  # still omitted by default (TC-688)
    opted_in = get_symbol(store, SCOPE, "pkg.Facade", include_raw_text=True)  # type: ignore[arg-type]
    assert isinstance(opted_in, SymbolSignature)
    assert isinstance(opted_in.raw_text, str) and "FQN: pkg.facades.Facade" in opted_in.raw_text


def test_list_members_on_a_shell_lists_its_definitions_members() -> None:
    store = _InMemoryStore(
        (
            _thin_shell("pkg.Facade", defined_in="pkg.facades.Facade"),
            _definition("pkg.facades.Facade", exported_as="pkg.Facade"),
        )
    )
    members = list_members(store, SCOPE, "pkg.Facade")  # type: ignore[arg-type]
    assert members == ("bind(path: str) -> None", "close() -> None", "name: str")
    assert members == list_members(store, SCOPE, "pkg.facades.Facade")  # type: ignore[arg-type]


def test_a_shell_whose_definition_is_not_indexed_behaves_exactly_as_before() -> None:
    store = _InMemoryStore((_thin_shell("pkg.Orphan", defined_in="pkg.gone.Orphan"),))
    answer = get_symbol(store, SCOPE, "pkg.Orphan")  # type: ignore[arg-type]
    assert type(answer) is SymbolSignature  # nothing to resolve to
    assert answer.canonical_fqn is None
    assert answer.fqn == "pkg.Orphan"
    assert answer.methods == answer.properties == answer.bases == answer.members == ()
    assert list_members(store, SCOPE, "pkg.Orphan") == ()  # type: ignore[arg-type]


def test_a_chain_of_pointers_is_followed_and_a_cycle_is_not() -> None:
    chain = _InMemoryStore(
        (
            _thin_shell("pkg.A", defined_in="pkg.mid.A"),
            _thin_shell("pkg.mid.A", defined_in="pkg.real.A"),
            _definition("pkg.real.A", exported_as="pkg.A"),
        )
    )
    answer = get_symbol(chain, SCOPE, "pkg.A")  # type: ignore[arg-type]
    assert isinstance(answer, SymbolSignature)
    assert (answer.fqn, answer.canonical_fqn) == ("pkg.A", "pkg.real.A")
    assert answer.methods == ("bind(path: str) -> None", "close() -> None")

    cycle = _InMemoryStore(
        (
            _thin_shell("pkg.X", defined_in="pkg.Y"),
            _thin_shell("pkg.Y", defined_in="pkg.X"),
        )
    )
    looped = get_symbol(cycle, SCOPE, "pkg.X")  # type: ignore[arg-type]
    assert type(looped) is SymbolSignature and looped.canonical_fqn is None
    assert looped.fqn == "pkg.X" and looped.methods == ()


def test_the_exact_match_contract_is_unchanged_no_fuzzy_fallback() -> None:
    store = _InMemoryStore(
        (
            _thin_shell("pkg.Facade", defined_in="pkg.facades.Facade"),
            _definition("pkg.facades.Facade", exported_as="pkg.Facade"),
        )
    )
    assert isinstance(get_symbol(store, SCOPE, "pkg.Facad"), NotFound)  # type: ignore[arg-type]
    assert isinstance(get_symbol(store, SCOPE, "pkg.facades.facade"), NotFound)  # type: ignore[arg-type]
    # a member-level name still resolves through the TC-540 path, to the definition, as before
    member = get_symbol(store, SCOPE, "Facade.bind")  # type: ignore[arg-type]
    assert isinstance(member, ResolvedSignature)
    assert member.fqn == "pkg.facades.Facade"


# --- the sweep over the real, committed corpus ---------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("tc794_corpus") / "manifests")


_ADVISORY_PREFIXES = ("Limitation:", "Known defect:")


def _member_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    """A ``list_members`` answer without its type-level disclosure lines (limitation / known defect),
    which are allowed to name the shell or the definition."""
    return tuple(line for line in lines if not line.startswith(_ADVISORY_PREFIXES))


def test_every_real_python_shell_answers_with_its_definitions_signature(corpus: ConformanceCorpus) -> None:
    python_pilots = sorted(name for name in corpus.pilots if name.endswith("__python"))
    assert len(python_pilots) == 11, python_pilots
    swept = 0
    for name in python_pilots:
        pilot = corpus.pilots[name]
        definitions: set[str] = set()
        shells: list[tuple[str, str]] = []
        for document in pilot.documents:
            fqn = extract_fqn(document["text"])
            if fqn is None or fqn.startswith(("Doc:", "Example:")):
                continue
            defined_in = extract_defined_in(document["text"])
            if defined_in is None:
                definitions.add(fqn)
            else:
                shells.append((fqn, defined_in))
        assert shells, f"{name} has no re-export shell at all - the sweep would be vacuous"
        pilot_swept = pilot_non_empty = 0
        for shell_fqn, definition_fqn in shells:
            if definition_fqn not in definitions:
                continue  # a shell with no indexed definition keeps answering from its own text
            answer = get_symbol(corpus.store, pilot.scope, shell_fqn)
            definition = get_symbol(corpus.store, pilot.scope, definition_fqn)
            assert isinstance(answer, SymbolSignature), (name, shell_fqn)
            assert isinstance(definition, SymbolSignature), (name, definition_fqn)
            assert (answer.fqn, answer.canonical_fqn) == (shell_fqn, definition_fqn)
            assert answer.kind == definition.kind, (name, shell_fqn)
            assert answer.bases == definition.bases, (name, shell_fqn)
            assert answer.methods == definition.methods, (name, shell_fqn)
            assert answer.properties == definition.properties, (name, shell_fqn)
            assert answer.members == definition.members, (name, shell_fqn)
            assert answer.limitations == definition.limitations, (name, shell_fqn)
            shell_members = list_members(corpus.store, pilot.scope, shell_fqn)
            definition_members = list_members(corpus.store, pilot.scope, definition_fqn)
            assert not isinstance(shell_members, NotFound), (name, shell_fqn)
            assert not isinstance(definition_members, NotFound), (name, definition_fqn)
            assert _member_lines(shell_members) == _member_lines(definition_members), (name, shell_fqn)
            pilot_swept += 1
            if definition.methods or definition.properties or definition.bases or definition.members:
                # the defect: this used to be empty for every shell, whatever the definition declared
                assert answer.methods or answer.properties or answer.bases or answer.members, (
                    name,
                    shell_fqn,
                )
                pilot_non_empty += 1
        assert pilot_swept > 0 and pilot_non_empty > 0, name
        swept += pilot_swept
    assert swept > 1000, swept
