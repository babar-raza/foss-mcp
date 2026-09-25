"""Bridge TC-062's real chunk builder to infra/ingest.py's ``--chunks`` flag.

``foss_mcp.indexing.chunk_builder.build_chunks_from_api_surface`` (TC-062) returns real
``Chunk`` dataclass objects, not JSON - before this script existed, nothing in the repo ever
called it outside of tests (the 2026-09-25 audit's finding: a real production ingestion run had
no CLI step that could turn a raw ``api_surface.json`` extraction fixture into a
``--chunks PATH`` file ``infra/ingest.py``'s own ``_load_chunks()`` can read). This script is
that bridge: load ``--api-surface``, call ``build_chunks_from_api_surface`` with the exact
prescribed call shape, serialize the result to ``{"chunks": [...]}`` via ``dataclasses.asdict``
(never hand-duplicated field names), write it to ``--out``.

Each chunk's ``text`` also gets one appended ``Source-Commit: <sha>`` line here. This is this
project's own already-established convention for recovering a generation's indexed source
commit from chunk text (``foss_mcp.mcp.tools.get_symbol``'s ``FQN:`` line is the same pattern;
``foss_mcp.mcp.tools.report_index_freshness``'s own docstring names this exact convention,
because the lexical index persists each chunk's text but not its ``Provenance``).
``build_chunks_from_api_surface`` itself does not add this line - it is shared, generic
chunk-building logic reused verbatim by several pilots' inline builders, none of which add it
either - so it is added here, once, at the ingestion-adapter boundary this script owns, using
each chunk's own ``provenance.commit`` (already set by ``build_chunks_from_api_surface`` from
the fixture's ``source_commit``) so it can never drift from what the chunk was actually built
from.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.normalization.chunker import Chunk


def _with_source_commit_line(chunk: Chunk) -> Chunk:
    return dataclasses.replace(chunk, text=f"{chunk.text}\nSource-Commit: {chunk.provenance.commit}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-surface", type=Path, required=True, help="raw extraction fixture JSON")
    parser.add_argument("--title", required=True)
    parser.add_argument("--max-types", type=int, default=20)
    parser.add_argument("--out", type=Path, required=True, help="where to write the --chunks JSON")
    args = parser.parse_args()

    fixture = json.loads(args.api_surface.read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title=args.title, max_types=args.max_types)
    chunks = [_with_source_commit_line(chunk) for chunk in chunks]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"chunks": [dataclasses.asdict(chunk) for chunk in chunks]}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(chunks)} chunks to {args.out}")


if __name__ == "__main__":
    main()
