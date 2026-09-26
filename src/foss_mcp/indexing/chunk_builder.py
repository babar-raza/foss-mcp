"""Build real ``Chunk`` objects from a raw ``api_surface.json`` extraction fixture.

Before this module existed, this exact logic lived only inline inside
``tests/indexing/test_publisher.py``'s ``_pdf_net_chunks()`` (the 2026-09-25 audit's finding) -
a real production ingestion run had nothing to call. Callers pass the already-parsed fixture
dict - matching ``infra/ingest.py``'s own ``--api-surface`` flag (TC-060) - rather than a path,
since loading the JSON is the caller's concern, not this function's.

TC-065 fixed a second, deeper defect the same audit found: this function's per-type text used
to be a bare ``"{name} is a {kind}."`` sentence plus an optional comma-joined method-name list -
none of which carries the ``FQN:``/``Kind:``/``Methods:``/``Properties:`` line convention
``get_symbol``/``list_members`` (``src/foss_mcp/mcp/tools/get_symbol.py``) actually parse. A real
published generation's chunks were therefore silently unfindable by ``get_symbol`` - confirmed
live against ``Aspose.Pdf.AFRelationship`` returning ``NotFound`` - even though ``get_symbol``'s
own tests passed, because those tests built their own disconnected, hand-shaped chunk text
rather than calling this function. The text below now emits the real convention, carrying
forward every field the raw extraction fixture already has: base types, each method's full
parameter list and return type, each property's type and writability, and an enum's member
values.
"""

from __future__ import annotations

from collections.abc import Mapping

from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document


def _method_line(method: Mapping[str, object]) -> str:
    params = ", ".join(
        f"{param.get('name', '')}: {param.get('type', '')}" for param in method.get("params") or []
    )
    return_type = method.get("return_type") or "void"
    return f"  - {method.get('name', '')}({params}) -> {return_type}"


def _property_writability(prop: Mapping[str, object]) -> str | None:
    """Resolve a property's writability honestly across the three real field shapes the
    2026-09-26 six-fixture audit found: pdf_net's real bool ``writable``; cells_rust/pdf_go's
    real string ``access_mode`` (the real, observed value in both fixtures is exclusively
    ``"readwrite"`` - no ``"readonly"``/``"read_only"`` value was found in either, but one is
    still mapped here rather than assumed impossible); and pdf_java's real property dicts,
    most of which carry neither key at all. Returns ``None`` when the real fixture data says
    nothing about writability, so a genuine absence is never silently written up as
    "read-only" the way the old bare ``prop.get('writable')`` check did.
    """
    if "writable" in prop:
        return "writable" if prop.get("writable") else "read-only"
    access_mode = prop.get("access_mode")
    if access_mode == "readwrite":
        return "writable"
    if access_mode in ("readonly", "read_only"):
        return "read-only"
    return None


def _property_line(prop: Mapping[str, object]) -> str:
    writability = _property_writability(prop)
    suffix = f" ({writability})" if writability is not None else ""
    return f"  - {prop.get('name', '')}: {prop.get('type', '')}{suffix}"


def _member_line(member: Mapping[str, object]) -> str:
    return f"  - {member.get('name', '')} = {member.get('value', '')}"


def _format_type_text(entry: Mapping[str, object]) -> str:
    """The real ``FQN:``/``Kind:``/``Bases:``/``Methods:``/``Properties:``/``Members:``
    convention ``get_symbol``/``list_members`` parse, built from *entry*'s real fields. Each
    section (including its header) is omitted entirely when its source list is empty -
    ``get_symbol``'s own ``_extract_block`` already handles an absent header correctly by
    returning an empty tuple, so an empty section is never emitted just to be discarded later.
    """
    fqn = entry.get("class_import") or entry.get("name", "")
    lines = [f"FQN: {fqn}", f"Kind: {entry.get('kind', '')}"]

    doc = entry.get("doc") or ""
    if doc:
        lines.append(doc)

    # pdf_typescript's real "constant" entries (e.g. AES_WRAP_OID) carry a real type/value
    # directly on the entry itself - previously silently dropped entirely. Checking for a real
    # "value" key (rather than only kind == "constant") catches this robustly even if some
    # other pilot's constant-shaped entry omits the kind label.
    if entry.get("kind") == "constant" or "value" in entry:
        raw_value = entry.get("value", "")
        raw_value = "" if raw_value is None else str(raw_value)
        if len(raw_value) > 200:
            raw_value = raw_value[:200] + "..."
        lines.append("")
        lines.append(f"Value: {entry.get('type', '')} = {raw_value}")

    bases = entry.get("bases") or []
    if bases:
        lines.append("")
        lines.append("Bases:")
        lines.extend(f"  - {base}" for base in bases)

    # pdf_go, cells_rust, and pdf_typescript's real free-function entries carry
    # params/return_type directly on the type entry itself (kind == "function", methods == []) -
    # e.g. pdf_go's real AddFontFile. Treat such an entry as its own single method so its real
    # signature is not silently dropped down to a bare "Kind: function" header.
    methods = entry.get("methods") or []
    if not methods and ("params" in entry or "return_type" in entry):
        methods = [entry]
    if methods:
        lines.append("")
        lines.append("Methods:")
        lines.extend(_method_line(method) for method in methods)

    properties = entry.get("properties") or []
    if properties:
        lines.append("")
        lines.append("Properties:")
        lines.extend(_property_line(prop) for prop in properties)

    enum_members = entry.get("enum_members") or []
    if enum_members:
        lines.append("")
        lines.append("Members:")
        lines.extend(_member_line(member) for member in enum_members)

    return "\n".join(lines)


def build_chunks_from_api_surface(
    fixture: Mapping[str, object], *, title: str, max_types: int = 20
) -> list[Chunk]:
    """A real, bounded slice of an ``api_surface.json`` extraction, normalized and chunked
    through TC-014's own pipeline - the first real content a generation publishes for a pilot.
    """
    sections = []
    for entry in fixture["types"][:max_types]:
        name = entry.get("class_import") or entry.get("name", "")
        sections.append(f"## {name}\n\n{_format_type_text(entry)}")
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
