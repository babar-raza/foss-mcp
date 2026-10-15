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

TC-382 (REQ-G3-005) made the text honest: it renders only what the extraction stated (no
invented ``void``, no ``name: `` with an empty type, no ``: : string``), drops what is not public
API (inherited base-class constructors and destructors, test-file symbols), marks deprecation,
gives a Python re-export stub the members of its definition, makes colliding FQNs unique, and
states how much of the API surface is indexed (``API-Coverage:``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

_REASON_LIMIT = 200
_TEST_BASENAME_SUFFIXES = ("_test.go", "_test.py", ".test.ts", ".spec.ts", ".test.js", ".spec.js")
_TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__"})
_NAME_SEPARATORS = re.compile(r"\.|::")


def _is_test_source(file: object) -> bool:
    """TC-382 (rule 7): True when *file* is a test source, which is not public API and so is never
    published. An absent or non-string ``file`` is never a test source.
    """
    if not isinstance(file, str) or not file:
        return False
    parts = file.replace("\\", "/").split("/")
    base = parts[-1]
    if base.endswith(_TEST_BASENAME_SUFFIXES):
        return True
    if base.startswith("test_") and base.endswith(".py"):
        return True
    return any(segment in _TEST_DIRECTORIES for segment in parts[:-1])


def _clean_type(value: object) -> str:
    """A type string as the extraction stated it: surrounding whitespace removed, and the
    declaration colon some TypeScript extractions leave in front of it (``": string"``) removed.
    Empty when the extraction recorded no type.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith(":"):
        text = text[1:].strip()
    return text


def _reason(entry: Mapping[str, object]) -> str:
    reason = entry.get("deprecated_reason")
    if not reason:
        return ""
    return " ".join(str(reason).split())[:_REASON_LIMIT]


def _is_deprecated(entry: Mapping[str, object]) -> bool:
    return entry.get("deprecated") is True


def _deprecated_suffix(entry: Mapping[str, object]) -> str:
    """TC-382 (rule 6): `` (deprecated)`` or `` (deprecated: <reason>)``; empty when not deprecated."""
    if not _is_deprecated(entry):
        return ""
    reason = _reason(entry)
    return f" (deprecated: {reason})" if reason else " (deprecated)"


def _inherited_from_suffix(entry: Mapping[str, object]) -> str:
    """TC-261 (C3): the "(inherited from X)" suffix, mirroring
    ``_property_writability``/``_property_line``'s existing optional-suffix style. An entry
    with no "inherited_from" key - every genuinely own-declared member, and every existing
    fixture's content before this card lands - renders this as an empty string, so its caller's
    rendered line is completely unchanged.
    """
    inherited_from = entry.get("inherited_from")
    return f"  (inherited from {inherited_from})" if inherited_from else ""


def _is_inherited_constructor_or_destructor(member: Mapping[str, object]) -> bool:
    """TC-382 (rule 5): an inherited member that is a constructor or destructor of the base class.
    A subclass does not have its base class's constructors, so listing them as inherited is wrong.
    """
    inherited_from = member.get("inherited_from")
    if not inherited_from:
        return False
    if member.get("is_constructor") is True:
        return True
    name = str(member.get("name") or "")
    if name.startswith("~") or name in ("constructor", "__init__"):
        return True
    base_name = _NAME_SEPARATORS.split(str(inherited_from).strip())[-1]
    return bool(base_name) and name == base_name


def _param_text(param: Mapping[str, object]) -> str:
    name = str(param.get("name") or "")
    param_type = _clean_type(param.get("type"))
    text = f"{name}: {param_type}" if param_type else name
    default = param.get("default")
    if default is None or not str(default).strip():
        return text
    return f"{text} = {str(default).strip()}"


def _modifiers_suffix(entry: Mapping[str, object]) -> str:
    """TC-396: `` [static]`` / `` [static, async]`` from the extraction's additive ``modifiers`` list.

    Empty when the key is absent or empty, so every member without it renders exactly as before.
    The suffix is only ever placed after a member's name and type, never before the name.
    """
    modifiers = [str(item).strip() for item in _items(entry.get("modifiers")) if str(item).strip()]
    return f" [{', '.join(modifiers)}]" if modifiers else ""


def _method_line(method: Mapping[str, object]) -> str:
    params = ", ".join(_param_text(param) for param in _items(method.get("params")))
    return_type = _clean_type(method.get("return_type"))
    arrow = f" -> {return_type}" if return_type else ""
    return (
        f"  - {method.get('name', '')}({params}){arrow}"
        f"{_modifiers_suffix(method)}{_inherited_from_suffix(method)}{_deprecated_suffix(method)}"
    )


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
    prop_type = _clean_type(prop.get("type"))
    typed = f": {prop_type}" if prop_type else ""
    return (
        f"  - {prop.get('name', '')}{typed}{_modifiers_suffix(prop)}{suffix}"
        f"{_inherited_from_suffix(prop)}{_deprecated_suffix(prop)}"
    )


def _member_line(member: Mapping[str, object]) -> str:
    value = member.get("value")
    if value is None or str(value) == "":
        return f"  - {member.get('name', '')}"
    return f"  - {member.get('name', '')} = {value}"


def _method_lines(methods: Sequence[Mapping[str, object]]) -> list[str]:
    """Rules 4 and 5: no inherited constructor or destructor, and an identical line only once."""
    lines: list[str] = []
    seen: set[str] = set()
    for method in methods:
        if _is_inherited_constructor_or_destructor(method):
            continue
        line = _method_line(method)
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def _property_lines(properties: Sequence[Mapping[str, object]]) -> list[str]:
    """Rule 4: a property name is rendered once per type; the first occurrence wins."""
    lines: list[str] = []
    seen: set[object] = set()
    for prop in properties:
        name = prop.get("name", "")
        if name in seen:
            continue
        seen.add(name)
        lines.append(_property_line(prop))
    return lines


def _items(value: object) -> list[Any]:
    """*value* as a list when the extraction recorded a collection there, else an empty list."""
    return list(value) if isinstance(value, list | tuple) else []


def _entry_fqn(entry: Mapping[str, object]) -> str:
    return str(entry.get("class_import") or entry.get("name", ""))


def _has_members(entry: Mapping[str, object]) -> bool:
    return bool(entry.get("methods") or entry.get("properties") or entry.get("enum_members"))


def _format_type_text(
    entry: Mapping[str, object],
    *,
    fqn: str | None = None,
    members_from: Mapping[str, object] | None = None,
    defined_in: str | None = None,
    exported_as: Sequence[str] = (),
    coverage: str | None = None,
) -> str:
    """The real ``FQN:``/``Kind:``/``Bases:``/``Methods:``/``Properties:``/``Members:``
    convention ``get_symbol``/``list_members`` parse, built from *entry*'s real fields. Each
    section (including its header) is omitted entirely when its source list is empty -
    ``get_symbol``'s own ``_extract_block`` already handles an absent header correctly by
    returning an empty tuple, so an empty section is never emitted just to be discarded later.

    TC-382: *fqn* overrides the entry's own name when it had to be qualified to be unique;
    *members_from* is the definition a Python re-export stub is rendered with (*defined_in* is
    that definition's FQN); *exported_as* lists the re-export stubs naming this definition;
    *coverage* is the ``API-Coverage:`` line's value.
    """
    source = entry if members_from is None else members_from
    lines = [f"FQN: {fqn if fqn is not None else _entry_fqn(entry)}", f"Kind: {entry.get('kind', '')}"]
    if _is_deprecated(entry):
        lines.append(f"Deprecated: {_reason(entry) or 'yes'}")
    if defined_in:
        lines.append(f"Defined-In: {defined_in}")
    lines.extend(f"Exported-As: {name}" for name in exported_as)

    doc = entry.get("doc") or (source.get("doc") if members_from is not None else "") or ""
    if doc:
        lines.append(str(doc))

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

    bases = _items(source.get("bases"))
    if bases:
        lines.append("")
        lines.append("Bases:")
        lines.extend(f"  - {base}" for base in bases)

    # pdf_go, cells_rust, and pdf_typescript's real free-function entries carry
    # params/return_type directly on the type entry itself (kind == "function", methods == []) -
    # e.g. pdf_go's real AddFontFile. Treat such an entry as its own single method so its real
    # signature is not silently dropped down to a bare "Kind: function" header.
    methods = _items(source.get("methods"))
    if not methods and ("params" in source or "return_type" in source):
        methods = [source]
    method_lines = _method_lines(methods)
    if method_lines:
        lines.append("")
        lines.append("Methods:")
        lines.extend(method_lines)

    property_lines = _property_lines(_items(source.get("properties")))
    if property_lines:
        lines.append("")
        lines.append("Properties:")
        lines.extend(property_lines)

    enum_members = _items(source.get("enum_members"))
    if enum_members:
        lines.append("")
        lines.append("Members:")
        lines.extend(_member_line(member) for member in enum_members)

    if coverage is not None:
        lines.append("")
        lines.append(f"API-Coverage: {coverage}")

    return "\n".join(lines)


def _qualifier(entry: Mapping[str, object], fqn: str) -> str:
    receiver = entry.get("receiver_type")
    if receiver:
        return str(receiver)
    namespace = entry.get("canonical_namespace")
    if namespace and not fqn.startswith(str(namespace)):
        return str(namespace)
    file = str(entry.get("file") or "").replace("\\", "/").rsplit("/", 1)[-1]
    return file.rsplit(".", 1)[0] if "." in file else file


def _unique_fqns(entries: Sequence[Mapping[str, object]]) -> list[str]:
    """TC-382 (rule 9): the FQN each entry is published under. FQNs that collide are all qualified
    (receiver type, else namespace, else file base name); a collision that survives that gets
    ``@<line>``. Entries whose FQN does not collide keep it untouched.
    """
    fqns = [_entry_fqn(entry) for entry in entries]
    counts: dict[str, int] = {}
    for fqn in fqns:
        counts[fqn] = counts.get(fqn, 0) + 1
    qualified = list(fqns)
    for index, entry in enumerate(entries):
        if counts[fqns[index]] > 1:
            name = str(entry.get("name") or fqns[index])
            qualified[index] = f"{_qualifier(entry, fqns[index])}.{name}"
    final_counts: dict[str, int] = {}
    for fqn in qualified:
        final_counts[fqn] = final_counts.get(fqn, 0) + 1
    result = list(qualified)
    for index, entry in enumerate(entries):
        if final_counts[qualified[index]] > 1:
            line = entry.get("line")
            result[index] = f"{qualified[index]}@{line if line is not None else index}"
    # A last guard so the published FQNs are unique even for entries identical in every field.
    seen: dict[str, int] = {}
    for index, fqn in enumerate(result):
        seen[fqn] = seen.get(fqn, 0) + 1
        if seen[fqn] > 1:
            result[index] = f"{fqn}#{seen[fqn]}"
    return result


def _reexport_pairs(entries: Sequence[Mapping[str, object]]) -> dict[int, int]:
    """TC-382 (rule 8): ``{stub index: definition index}`` for each Python re-export stub - an
    entry from a package ``__init__.py`` with no members, for which exactly one other entry of the
    same name, under the stub's package, has members.
    """
    pairs: dict[int, int] = {}
    for index, entry in enumerate(entries):
        file = str(entry.get("file") or "")
        class_import = entry.get("class_import")
        if not file.endswith("__init__.py") or not class_import or _has_members(entry):
            continue
        package = str(class_import).rpartition(".")[0]
        if not package:
            continue
        prefix = package + "."
        candidates = [
            other_index
            for other_index, other in enumerate(entries)
            if other_index != index
            and other.get("name") == entry.get("name")
            and str(other.get("class_import") or "").startswith(prefix)
            and _has_members(other)
        ]
        if len(candidates) == 1:
            pairs[index] = candidates[0]
    return pairs


# TC-520: the share of source files that may fail to parse before the coverage line, the
# Extraction notice and the freshness report say so. Tree-sitter is error tolerant and flags files
# whose symbols are still extracted, so its threshold is higher than the stdlib ``ast``'s.
PARSE_FAILURE_NOTE_THRESHOLD = {"ast": 0.10, "tree-sitter": 0.25}


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_failures_over_threshold(parse_failures: object) -> bool:
    """True only for a ``parse_failures`` dict with integer ``failed`` and ``total``, ``total > 0``
    and ``failed / total`` strictly greater than the engine's threshold. Anything malformed, or of
    an unknown engine, is False."""
    if not isinstance(parse_failures, Mapping):
        return False
    threshold = PARSE_FAILURE_NOTE_THRESHOLD.get(str(parse_failures.get("engine")))
    failed, total = parse_failures.get("failed"), parse_failures.get("total")
    if threshold is None or not _is_count(failed) or not _is_count(total):
        return False
    failed_count, total_count = cast(int, failed), cast(int, total)
    return total_count > 0 and failed_count / total_count > threshold


def _syntax_error_files(fixture: Mapping[str, object]) -> set[str]:
    """The distinct source files an ``unresolved`` ``:syntax-error:`` entry names (the text between
    the marker and the next colon)."""
    files: set[str] = set()
    unresolved = fixture.get("unresolved")
    for entry in unresolved if isinstance(unresolved, list) else []:
        if isinstance(entry, str) and ":syntax-error:" in entry:
            files.add(entry.partition(":syntax-error:")[2].partition(":")[0])
    return files


def _coverage_line(fixture: Mapping[str, object], entries: Sequence[Mapping[str, object]]) -> str | None:
    """The ``API-Coverage:`` value of every symbol chunk of *fixture*.

    Legacy rendering (``{kept} of {type_count} types``) when the fixture has none of
    ``symbol_counts``, ``parse_failures`` and a ``:syntax-error:`` entry. Otherwise one line of
    clauses joined by "; ", the types clause first: kept types of the types the library has, kept
    functions of its functions, and how many source files could not be parsed.
    """
    type_count = fixture.get("type_count")
    legacy = f"{len(entries)} of {type_count} types" if _is_count(type_count) else None
    counts = fixture.get("symbol_counts")
    failures = fixture.get("parse_failures")
    syntax_files = _syntax_error_files(fixture)
    if counts is None and failures is None and not syntax_files:
        return legacy
    kept_functions = sum(1 for entry in entries if entry.get("kind") == "function")
    clauses: list[str] = []
    if isinstance(counts, Mapping) and _is_count(counts.get("types")):
        clauses.append(f"{len(entries) - kept_functions} of {counts['types']} types")
    elif legacy is not None:
        clauses.append(legacy)
    else:
        return None
    if isinstance(counts, Mapping) and _is_count(counts.get("functions")) and counts["functions"] > 0:
        clauses.append(f"{kept_functions} of {counts['functions']} functions")
    if parse_failures_over_threshold(failures):
        failed_files = cast(Mapping[str, int], failures)
        clauses.append(
            f"{failed_files['failed']} of {failed_files['total']} source files could not be parsed"
        )
    elif failures is None and syntax_files:
        clauses.append(f"at least {len(syntax_files)} source files could not be parsed")
    return "; ".join(clauses)


def build_chunks_from_api_surface(
    fixture: Mapping[str, object], *, title: str, max_types: int = 300
) -> list[Chunk]:
    """A real, bounded slice of an ``api_surface.json`` extraction, normalized and chunked
    through TC-014's own pipeline - the first real content a generation publishes for a pilot.
    """
    entries = [entry for entry in _items(fixture["types"]) if not _is_test_source(entry.get("file"))][
        :max_types
    ]
    fqns = _unique_fqns(entries)
    pairs = _reexport_pairs(entries)
    exported_as: dict[int, list[str]] = {}
    for stub_index, definition_index in pairs.items():
        exported_as.setdefault(definition_index, []).append(fqns[stub_index])
    coverage = _coverage_line(fixture, entries)
    sections = []
    for index, entry in enumerate(entries):
        target = pairs.get(index)
        text = _format_type_text(
            entry,
            fqn=fqns[index],
            members_from=entries[target] if target is not None else None,
            defined_in=fqns[target] if target is not None else None,
            exported_as=exported_as.get(index, ()),
            coverage=coverage,
        )
        sections.append(f"## {fqns[index]}{_deprecated_suffix(entry)}\n\n{text}")
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(
            repository=cast(str, fixture["source_repository"]),
            commit=cast(str, fixture["source_commit"]),
            path="api_surface.json",
        ),
        evidence_refs=(f"{fixture['source_repository']}@{fixture['source_commit']}:api_surface.json",),
        title=title,
        body="\n\n".join(sections),
    )
    return chunk_document(doc)
