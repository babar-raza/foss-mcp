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

import re
from dataclasses import dataclass

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.search_symbols import SOURCE_KIND, scope_key

_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_KIND_LINE = re.compile(r"^Kind:\s*(.+)$", re.MULTILINE)
_BULLET_PREFIX = "  - "


@dataclass(frozen=True)
class SymbolSignature:
    scope: Scope
    generation_id: str
    doc_id: str
    chunk_id: str
    fqn: str
    kind: str
    methods: tuple[str, ...]
    properties: tuple[str, ...]
    raw_text: str


@dataclass(frozen=True)
class NotFound:
    """An explicit 404. Never a near match - the whole point of this tool being deterministic."""

    scope: Scope
    fqn: str


def extract_fqn(text: str) -> str | None:
    """The exact FQN a self-extracted chunk's ``FQN:`` line names, or ``None`` if it has none."""
    match = _FQN_LINE.search(text)
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


def _parse_signature(scope: Scope, generation_id: str, doc_id: str, chunk_id: str, text: str) -> SymbolSignature:
    kind_match = _KIND_LINE.search(text)
    return SymbolSignature(
        scope=scope,
        generation_id=generation_id,
        doc_id=doc_id,
        chunk_id=chunk_id,
        fqn=extract_fqn(text) or "",
        kind=kind_match.group(1).strip() if kind_match else "",
        methods=_extract_block(text, "Methods:"),
        properties=_extract_block(text, "Properties:"),
        raw_text=text,
    )


def get_symbol(store: GenerationManifestStore, scope: Scope, fqn: str) -> SymbolSignature | NotFound:
    """The exact signature published for *fqn* in ``scope``'s currently active generation.

    Matching is EXACT string equality against each chunk's own ``FQN:`` line - never a
    substring, never a ranked search, never a fallback to a similarly-named symbol.
    """
    key = scope_key(scope, SOURCE_KIND)
    active_generation_id = store.read_active(key)
    if active_generation_id is None:
        return NotFound(scope, fqn)

    manifest = store.read_generation(key, active_generation_id)
    documents = (manifest.payload.get("lexical_index") or {}).get("documents") or {}
    for doc_id, document in documents.items():
        if extract_fqn(document["text"]) == fqn:
            return _parse_signature(scope, active_generation_id, doc_id, document["chunk_id"], document["text"])
    return NotFound(scope, fqn)
