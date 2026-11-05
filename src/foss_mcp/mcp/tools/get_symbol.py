"""get_symbol: an exact signature for one fully-qualified name, or an explicit 404.

Deterministic by design: this tool never guesses and never returns a near match. A miss is a
miss - a caller asking for a symbol that does not exist gets ``NotFound``, never the closest
thing this index happens to contain.

The published generation's lexical-index payload (``foss_mcp.indexing.lexical_index_writer``,
out of this card's write_paths) persists each chunk's ``text`` but not its ``section_title`` -
so the exact FQN a chunk describes has to be recoverable from the text itself. Each
self-extracted chunk's text is expected to open with an ``FQN: <name>`` line (this project's
own convention, not a schema anything upstream enforces); a chunk that doesn't carry one
simply never matches anything, honestly.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.known_defects import KnownDefect, load_known_defects
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.coverage_note import coverage_note_from_text
from foss_mcp.mcp.tools.search_symbols import (
    _NON_SYMBOL_FQN_PREFIXES,
    NOT_IMPLEMENTED_PHRASE,
    SOURCE_KIND,
    _member_name,
    scope_key,
    suggest_similar_fqns,
)

_NOT_FOUND_REASON = "no symbol with this exact FQN is in this index"
_NO_GENERATION_REASON = "no published generation for this scope"
_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_KIND_LINE = re.compile(r"^Kind:\s*(.+)$", re.MULTILINE)
_BULLET_PREFIX = "  - "
_DEFINED_IN_LINE = re.compile(r"^Defined-In:\s*(\S.*)$", re.MULTILINE)
_MEMBER_BLOCKS = ("Methods:", "Properties:", "Members:")
_ENUM_VALUE_SEPARATOR = " = "
_NAME_END = re.compile(r"[\s\[]")
_ASYNC_SUFFIX = "async"
_SUGGESTION_LIMIT = 5
# TC-559: the type-level line of a chunk whose type is not implemented, and the member-line marker that follows
# an enum value (``  - Pdf  (not implemented in this build: ...)``), which is no part of the value's name.
_TYPE_LIMITATION_PREFIX = "Limitation:"
_MARKER_START = re.compile(r"\s*\(" + re.escape(NOT_IMPLEMENTED_PHRASE))
# TC-639 (KD2, G3/REQ-G3-015): the exact pattern report_index_freshness._SOURCE_COMMIT_LINE already
# established for reading a chunk's own ``Source-Commit:`` line. Reimplemented locally, verbatim,
# rather than imported - report_index_freshness (and find_examples) already import FROM this module
# (``extract_fqn``), so importing their constant back here would be a circular import.
_SOURCE_COMMIT_LINE = re.compile(r"^Source-Commit:\s*(\S+)$", re.MULTILINE)


class _Omitted:
    """TC-688 (3D-NET-014, RT3): marks ``raw_text`` as deliberately left out of a response by
    ``get_symbol(..., include_raw_text=False)`` - never equal to ``""`` or ``None``, so a caller
    checking either can never mistake "opted out" for "field happened to be empty" or "field never
    existed before this card". Identity (``is``), never equality, is what matters: ``server.
    _to_jsonable`` drops any dict entry whose value IS this exact sentinel (by identity) before a
    response is rendered to JSON, so an MCP caller who never asked for ``raw_text`` never sees the
    key at all. Nothing else in this codebase ever constructs or compares against it."""

    def __repr__(self) -> str:
        return "<raw_text omitted: pass include_raw_text=True to get_symbol to include it>"


# One shared instance - this sentinel's only job is identity, so there is never a reason for two.
RAW_TEXT_OMITTED: Final = _Omitted()


@dataclass(frozen=True)
class SymbolSignature:
    scope: Scope
    generation_id: str
    doc_id: str
    chunk_id: str
    fqn: str
    kind: str
    bases: tuple[str, ...]
    methods: tuple[str, ...]
    properties: tuple[str, ...]
    members: tuple[str, ...]
    # TC-688: real chunk text when a caller opts in (``include_raw_text=True``), else
    # ``RAW_TEXT_OMITTED`` - never ``""``, never silently absent-looking. See ``_Omitted`` above.
    raw_text: str | _Omitted
    found: bool = True
    # TC-559 (additive): the lines of the chunk that say the symbol is "not implemented in this build" - the
    # type-level ``Limitation: ...`` line, if any, then each member line that carries the phrase. Empty for a
    # symbol that works. For a resolved member name only the type-level line and that member's own lines.
    limitations: tuple[str, ...] = ()
    # TC-639 (KD2, G3/REQ-G3-015, additive): a rendered disclosure line for every committed known-defect
    # entry whose subject.refs contains this symbol's own fqn (G3/TC-658: refs, plural - the OWNING
    # type's FQN for a resolved member - see ResolvedSignature below), in registry file order. Empty when
    # no defect data is recorded for this pilot yet (KD6-KD9's job), or none of it matches this symbol.
    known_defects: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedSignature(SymbolSignature):
    """A name that is not an indexed FQN but resolves to one (TC-540, G3/REQ-G3-008).

    ``fqn`` is the OWNING type's real FQN. ``resolved_from`` is the name asked for; ``member`` is the
    member name read off it (empty when the whole name was a type); ``member_lines`` is every rendered
    line of that member, all overloads, in chunk order. An exact FQN hit stays a plain
    ``SymbolSignature``, so its JSON gains none of these keys.
    """

    resolved_from: str = ""
    member: str = ""
    member_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotFound:
    """An explicit 404. Never a near match - the whole point of this tool being deterministic.

    ``suggestions`` (TC-143, G2/REQ-G2-047) is purely additive: a real "did you mean" hint
    computed from the FQNs actually published in this same generation, never a substitute for
    the 404 itself and never present unless genuinely computed from real published content.

    ``found`` (always ``False``), ``reason`` and ``coverage_note`` are additive (TC-386): the miss
    shape the tool descriptions promise says it is a miss and why. ``coverage_note`` is set only
    when a symbol chunk states that the API index holds fewer types than the library has.
    """

    scope: Scope
    fqn: str
    suggestions: tuple[str, ...] = ()
    found: bool = False
    reason: str = _NOT_FOUND_REASON
    coverage_note: str | None = None


def extract_fqn(text: str) -> str | None:
    """The exact FQN a self-extracted chunk's ``FQN:`` line names, or ``None`` if it has none."""
    match = _FQN_LINE.search(text)
    return match.group(1).strip() if match is not None else None


def extract_defined_in(text: str) -> str | None:
    """TC-686 (I5, REQ-G3-012): the real VALUE of a chunk's own ``Defined-In:`` line - its
    canonical definition's FQN - or ``None`` when the chunk carries no such line at all (it is not
    a re-export shell). Reused by ``_SymbolChunk.defined_in`` and ``search_symbols._Entry`` so
    both readers parse this one convention identically."""
    match = _DEFINED_IN_LINE.search(text)
    return match.group(1).strip() if match is not None else None


def _extract_block(text: str, header: str) -> tuple[str, ...]:
    lines = text.splitlines()
    if header not in lines:
        return ()
    start = lines.index(header) + 1
    items: list[str] = []
    for line in lines[start:]:
        if not line.startswith(_BULLET_PREFIX):
            break
        items.append(line[len(_BULLET_PREFIX) :].strip())
    return tuple(items)


def limitation_lines(text: str, member_lines: Sequence[str] | None = None) -> tuple[str, ...]:
    """TC-559: the lines of the chunk *text* that carry ``not implemented in this build``: the type-level
    ``Limitation:`` line, then the member lines of the ``Methods:`` / ``Properties:`` / ``Members:`` blocks
    (or only *member_lines*, when a single member was asked for) that carry it."""
    found: list[str] = []
    for line in text.splitlines():
        if line.startswith(_TYPE_LIMITATION_PREFIX) and NOT_IMPLEMENTED_PHRASE in line.lower():
            found.append(line.strip())
    if member_lines is None:
        member_lines = [item for header in _MEMBER_BLOCKS for item in _extract_block(text, header)]
    found.extend(item for item in member_lines if NOT_IMPLEMENTED_PHRASE in item.lower())
    return tuple(found)


def _render_known_defect(defect: KnownDefect) -> str:
    """One factual disclosure line built from *defect*'s own recorded ``summary``/``user_action``/
    ``evidence.repro`` - no editorializing, this is the defect's own recorded wording, assembled."""
    return f"Known defect: {defect.summary} {defect.user_action} (repro: {defect.evidence.repro})"


def known_defect_lines(scope: Scope, fqn: str, text: str) -> tuple[str, ...]:
    """Every known-defect disclosure line that applies to *fqn*, in registry file order (KD2,
    G3/REQ-G3-015).

    A defect applies when *fqn* is a member of its ``subject.refs`` (G3/TC-658: a non-empty tuple,
    since the same real upstream bug can affect several real sibling symbols in the identical
    causal chain) - the OWNING type's FQN for a resolved member, never the raw member name,
    matching how a resolved name is already treated elsewhere in this module - and, when it is
    scoped by ``applies_to.indexed_commit``, that commit equals *text*'s own ``Source-Commit:``
    line. A package-version-scoped defect (no ``indexed_commit``) always applies here regardless
    of commit: this tool has no live registry check against a currently served package version -
    that staleness-aware check is KD5's job on ``report_index_freshness`` specifically, not this
    one.
    """
    defects = load_known_defects(scope.family, scope.platform)
    if not defects:
        return ()
    commit_match = _SOURCE_COMMIT_LINE.search(text)
    chunk_commit = commit_match.group(1) if commit_match is not None else None
    lines: list[str] = []
    for defect in defects:
        if fqn not in defect.subject.refs:
            continue
        indexed_commit = defect.applies_to.indexed_commit
        if indexed_commit is not None and indexed_commit != chunk_commit:
            continue
        lines.append(_render_known_defect(defect))
    return tuple(lines)


def _parse_signature(
    scope: Scope,
    generation_id: str,
    doc_id: str,
    chunk_id: str,
    text: str,
    *,
    include_raw_text: bool = False,
) -> SymbolSignature:
    kind_match = _KIND_LINE.search(text)
    fqn = extract_fqn(text) or ""
    return SymbolSignature(
        scope=scope,
        generation_id=generation_id,
        doc_id=doc_id,
        chunk_id=chunk_id,
        fqn=fqn,
        kind=kind_match.group(1).strip() if kind_match else "",
        bases=_extract_block(text, "Bases:"),
        methods=_extract_block(text, "Methods:"),
        properties=_extract_block(text, "Properties:"),
        members=_extract_block(text, "Members:"),
        # TC-688 (3D-NET-014): real text only when the caller opted in - see RAW_TEXT_OMITTED.
        raw_text=text if include_raw_text else RAW_TEXT_OMITTED,
        limitations=limitation_lines(text),
        known_defects=known_defect_lines(scope, fqn, text),
    )


def _coverage_note(text: str) -> str | None:
    """The partial-coverage caveat a symbol chunk's ``API-Coverage:`` line states (types, functions
    and source files that could not be parsed), or ``None`` when the line is absent or states
    nothing partial. One helper shared with ``report_index_freshness`` (TC-520)."""
    return coverage_note_from_text(text)


def generation_coverage_note(documents: Mapping[str, Mapping[str, str]]) -> str | None:
    """The partial-coverage caveat a generation's symbol chunks state, or ``None``.

    Iterates *documents* in order, skips a chunk with no FQN and a ``Doc: ``/``Example: `` pseudo-FQN
    chunk, and returns the note of the first remaining chunk whose ``API-Coverage:`` line states one
    (TC-515: ``get_symbol``'s own miss, ``search_symbols``' and ``lookup``'s misses all say it through
    this one function, which is a thin wrapper over ``coverage_note.coverage_note_from_text``).
    """
    for document in documents.values():
        text = document["text"]
        document_fqn = extract_fqn(text)
        if document_fqn is None or document_fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        note = _coverage_note(text)
        if note is not None:
            return note
    return None


@dataclass(frozen=True)
class _SymbolChunk:
    doc_id: str
    chunk_id: str
    fqn: str
    text: str

    @property
    def defined_in(self) -> str | None:
        """TC-686 (I5): the real VALUE of this chunk's own ``Defined-In:`` line - its canonical
        definition's FQN - or ``None`` when it carries none at all."""
        return extract_defined_in(self.text)

    @property
    def is_reexport_shell(self) -> bool:
        return self.defined_in is not None

    @property
    def canonical_id(self) -> str:
        """TC-686 (I5): this chunk's own canonical identity - its ``Defined-In:`` value when it is
        a re-export shell, else its own ``fqn``. Two chunks sharing a canonical id are one unit."""
        return self.defined_in or self.fqn


def _declared_members(text: str) -> dict[str, list[str]]:
    """Member name -> its rendered lines (every overload) over the ``Methods:``, ``Properties:`` and
    ``Members:`` blocks of one type chunk, in chunk order."""
    declared: dict[str, list[str]] = {}
    for header in _MEMBER_BLOCKS:
        for item in _extract_block(text, header):
            if header == "Members:":
                name = _MARKER_START.split(item.split(_ENUM_VALUE_SEPARATOR, 1)[0], maxsplit=1)[0].strip()
            else:
                name = _NAME_END.split(_member_name(item), maxsplit=1)[0]
            if name:
                declared.setdefault(name, []).append(item)
    return declared


def _separator(fqn: str) -> str:
    return "::" if "::" in fqn else "."


def _owners(
    symbols: Sequence[_SymbolChunk], owner_text: str
) -> tuple[list[_SymbolChunk], list[_SymbolChunk]]:
    """``(owners, matched)``: the type chunks *owner_text* names, and every chunk that matched before
    collapsing.

    The chunk whose FQN equals *owner_text* is the owner; otherwise every chunk whose FQN ends with it
    at a dot or ``::`` boundary. TC-686 (I5, REQ-G3-012): two chunks sharing a canonical id - a
    chunk's own ``Defined-In:`` value when it is a re-export shell, else its own ``fqn`` (I1's
    `symbol_identity.compute_canonical_identities`, consumed here via each chunk's own
    `canonical_id`, never by re-parsing rendered member content) - count as one type, kept as the
    real definition whenever both a shell and its definition are both present.
    """
    matched = [chunk for chunk in symbols if chunk.fqn == owner_text] or [
        chunk for chunk in symbols if chunk.fqn.endswith(("." + owner_text, "::" + owner_text))
    ]
    owners: list[_SymbolChunk] = []
    owner_ids: list[str] = []
    for chunk in matched:
        canonical_id = chunk.canonical_id
        twin = next((index for index, other_id in enumerate(owner_ids) if other_id == canonical_id), None)
        if twin is None:
            owners.append(chunk)
            owner_ids.append(canonical_id)
        elif owners[twin].is_reexport_shell and not chunk.is_reexport_shell:
            owners[twin] = chunk
    return owners, matched


def _signature_of(
    scope: Scope,
    generation_id: str,
    chunk: _SymbolChunk,
    requested: str,
    member: str,
    *,
    include_raw_text: bool = False,
) -> ResolvedSignature:
    base = _parse_signature(
        scope,
        generation_id,
        chunk.doc_id,
        chunk.chunk_id,
        chunk.text,
        include_raw_text=include_raw_text,
    )
    member_lines = tuple(_declared_members(chunk.text).get(member, ())) if member else ()
    return ResolvedSignature(
        scope=base.scope,
        generation_id=base.generation_id,
        doc_id=base.doc_id,
        chunk_id=base.chunk_id,
        fqn=base.fqn,
        kind=base.kind,
        bases=base.bases,
        methods=base.methods,
        properties=base.properties,
        members=base.members,
        raw_text=base.raw_text,
        limitations=limitation_lines(chunk.text, member_lines if member else None),
        known_defects=base.known_defects,
        resolved_from=requested,
        member=member,
        member_lines=member_lines,
    )


def _async_twin(name: str) -> str:
    folded = name.casefold()
    if folded.endswith(_ASYNC_SUFFIX):
        return folded[: -len(_ASYNC_SUFFIX)]
    return folded + _ASYNC_SUFFIX


def _did_you_mean_members(
    owner: _SymbolChunk,
    matched: Sequence[_SymbolChunk],
    symbols: Sequence[_SymbolChunk],
    member: str,
) -> tuple[str, ...]:
    """At most five ``<TypeFQN><sep><member>`` names: the owner's own case-insensitive or ``Async``
    twin match, then other indexed types (FQN order) declaring such a name, then the nearest
    spellings among the owner's own member names."""
    wanted = {member.casefold(), _async_twin(member)} - {""}
    found: list[str] = []

    def add(type_fqn: str, name: str) -> None:
        candidate = f"{type_fqn}{_separator(type_fqn)}{name}"
        if candidate not in found:
            found.append(candidate)

    own = _declared_members(owner.text)
    for name in own:
        if name.casefold() in wanted:
            add(owner.fqn, name)
    skipped = {id(chunk) for chunk in matched}
    for chunk in sorted(symbols, key=lambda candidate: candidate.fqn):
        if id(chunk) in skipped:
            continue
        for name in _declared_members(chunk.text):
            if name.casefold() in wanted:
                add(chunk.fqn, name)
    for name in difflib.get_close_matches(member, list(own), n=3, cutoff=0.6):
        add(owner.fqn, name)
    return tuple(found[:_SUGGESTION_LIMIT])


def _resolve_name(
    scope: Scope,
    generation_id: str,
    symbols: Sequence[_SymbolChunk],
    requested: str,
    coverage_note: str | None,
    *,
    include_raw_text: bool = False,
) -> ResolvedSignature | NotFound | None:
    """Resolve a name that is not an indexed FQN (TC-540): the whole name read as a type, else
    ``<owner>.<member>`` / ``<owner>::<member>``. ``None`` means nothing to add to today's NotFound.
    Matching is by names only - never a substring or fuzzy match for a hit."""

    def shared(name: str, owners: Sequence[_SymbolChunk], suggestions: Sequence[str]) -> NotFound:
        return NotFound(
            scope,
            requested,
            suggestions=tuple(suggestions[:_SUGGESTION_LIMIT]),
            reason=f"'{name}' names {len(owners)} different types in this index; use one of the full names listed",
            coverage_note=coverage_note,
        )

    owners, _matched = _owners(symbols, requested)
    if len(owners) == 1:
        return _signature_of(
            scope, generation_id, owners[0], requested, "", include_raw_text=include_raw_text
        )
    if len(owners) > 1:
        return shared(requested, owners, sorted(chunk.fqn for chunk in owners))

    cut = max(requested.rfind("."), requested.rfind("::"))
    if cut < 0:
        return None
    width = 2 if requested.startswith("::", cut) else 1
    owner_text, member = requested[:cut], requested[cut + width :]
    if not owner_text or not member:
        return None
    owners, matched = _owners(symbols, owner_text)
    if not owners:
        return None
    if len(owners) > 1:
        declaring = [chunk for chunk in owners if member in _declared_members(chunk.text)] or owners
        return shared(
            owner_text,
            owners,
            sorted(f"{chunk.fqn}{_separator(chunk.fqn)}{member}" for chunk in declaring),
        )
    owner = owners[0]
    declared = _declared_members(owner.text)
    if member in declared:
        return _signature_of(
            scope, generation_id, owner, requested, member, include_raw_text=include_raw_text
        )
    return NotFound(
        scope,
        requested,
        suggestions=_did_you_mean_members(owner, matched, symbols, member),
        reason=f"{owner.fqn} is in this index, but it declares no member named '{member}'",
        coverage_note=coverage_note,
    )


def get_symbol(
    store: GenerationManifestStore,
    scope: Scope,
    fqn: str,
    include_raw_text: bool = False,
) -> SymbolSignature | ResolvedSignature | NotFound:
    """The exact signature published for *fqn* in ``scope``'s currently active generation.

    Matching is EXACT string equality against each chunk's own ``FQN:`` line - never a
    substring, never a ranked search, never a fallback to a similarly-named symbol. Example and
    documentation chunks (``Example: ...`` / ``Doc: ...`` pseudo-FQNs) are not symbols: they
    never match, and they are never offered as suggestions (TC-386).

    TC-688 (3D-NET-014, RT3): *include_raw_text* defaults to ``False`` - the FULL duplicate chunk
    text (already available, field for field, through ``methods``/``properties``/``members``) is
    no longer attached by default. Pass ``True`` for byte-for-byte identical output to every prior
    release (``raw_text`` carries the real chunk text); the default omits it (see
    ``RAW_TEXT_OMITTED``) so a typical response is roughly half the size. This is a deliberate
    default-response-SHAPE change, not a bug fix with a compatible default - see
    ``docs/DECISION_LOG.md``'s 2026-11-01 "Workstream K" entry and this card (G3/TC-688) for why
    it was deferred, then taken, on purpose.
    """
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return NotFound(scope, fqn, reason=_NO_GENERATION_REASON)

    manifest = store.read_generation(key, active_generation_id)
    documents = (manifest.payload.get("lexical_index") or {}).get("documents") or {}
    known_fqns = []
    symbols: list[_SymbolChunk] = []
    for doc_id, document in documents.items():
        document_fqn = extract_fqn(document["text"])
        if document_fqn is None or document_fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        if document_fqn == fqn:
            return _parse_signature(
                scope,
                active_generation_id,
                doc_id,
                document["chunk_id"],
                document["text"],
                include_raw_text=include_raw_text,
            )
        known_fqns.append(document_fqn)
        symbols.append(_SymbolChunk(doc_id, document["chunk_id"], document_fqn, document["text"]))
    coverage_note = generation_coverage_note(documents)
    # TC-540: an ADDITIVE step after the exact match - a member-level or short type name resolves to
    # its owning type, and a miss says what it was looking for.
    resolved = _resolve_name(
        scope,
        active_generation_id,
        symbols,
        fqn,
        coverage_note,
        include_raw_text=include_raw_text,
    )
    if resolved is not None:
        return resolved
    return NotFound(
        scope,
        fqn,
        suggestions=suggest_similar_fqns(known_fqns, fqn),
        coverage_note=coverage_note,
    )
