"""Build real ``Chunk`` objects from a raw ``api_surface.json`` extraction fixture.

Before this module existed, this exact logic lived only inline inside
``tests/indexing/test_publisher.py``'s ``_pdf_net_chunks()`` (the 2026-09-25 audit's finding) -
a real production ingestion run had nothing to call. This is that logic, promoted verbatim: same
section text, same document envelope, same chunking. Callers pass the already-parsed fixture
dict - matching ``infra/ingest.py``'s own ``--api-surface`` flag (TC-060) - rather than a path,
since loading the JSON is the caller's concern, not this function's.
"""

from __future__ import annotations

from collections.abc import Mapping

from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document


def build_chunks_from_api_surface(
    fixture: Mapping[str, object], *, title: str, max_types: int = 20
) -> list[Chunk]:
    """A real, bounded slice of an ``api_surface.json`` extraction, normalized and chunked
    through TC-014's own pipeline - the first real content a generation publishes for a pilot.
    """
    sections = []
    for entry in fixture["types"][:max_types]:
        name = entry.get("class_import") or entry.get("name", "")
        methods = ", ".join(m.get("name", "") for m in entry.get("methods") or [])
        text = f"{name} is a {entry.get('kind', '')}."
        if methods:
            text += f" Methods: {methods}."
        sections.append(f"## {name}\n\n{text}")
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(
            repository=fixture["source_repository"], commit=fixture["source_commit"], path="api_surface.json"
        ),
        evidence_refs=(f"{fixture['source_repository']}@{fixture['source_commit']}:api_surface.json",),
        title=title,
        body="\n\n".join(sections),
    )
    return chunk_document(doc)
