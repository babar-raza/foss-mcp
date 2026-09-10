"""Resolve every claim a chunk makes against foss-mcp's own extraction - traceability, not
correctness (plan 8.10, TC-010a's claim-ID bridge).

Two claim shapes get resolved, honestly:

- a symbol anchor - an inline code span like `` `Document.Open` `` - must resolve,
  unambiguously, against the self-extracted symbol index
  (``foss_mcp.extraction.claim_id_bridge.anchor_resolves``).
- a numeric claim - a sentence asserting a specific count, e.g. "805 classes" - must match a
  count the raw-knowledge tier CONFIDENTLY has. OQ-001: the furnished pdf/net page asserts
  "805 classes"; TC-011's own extraction found 899 TYPES, and the *reduced* fixture's kind
  breakdown is not a representative sample of the full population, so it cannot confirm or
  deny a narrower, specific count like "805 classes" either way. A reduced or truncated
  extraction that simply lacks a confident count for the claimed unit is not corroboration -
  the number might happen to be right - so the honest verdict is ``insufficient_evidence``,
  never a silent pass.

A chunk carrying any unresolved claim is never silently retained as an unqualified fact: its
own ``validation`` is set to the worst verdict any claim earned, and ``citable_chunks`` drops
it from the citable set entirely - both remediations the card allows (excluded, or explicitly
qualified) are available to a caller.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

from foss_mcp.extraction.claim_id_bridge import Declaration, SymbolIndex, anchor_resolves
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import ValidationResult

SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"

_INLINE_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_CALL_SUFFIX = re.compile(r"\(.*\)\s*$")
_ANCHOR_SHAPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

_COUNTABLE_UNIT_KIND = {
    "class_declaration": "classes",
    "struct_declaration": "structs",
    "interface_declaration": "interfaces",
    "enum_declaration": "enums",
}
_NUMERIC_CLAIM = re.compile(
    r"\b(?P<number>\d{1,3}(?:,\d{3})*)\s+"
    r"(?P<unit>classes|types|methods|functions|interfaces|enums|structs|properties)\b",
    re.IGNORECASE,
)


def symbol_index_from_api_surface(types: Sequence[Mapping[str, object]]) -> SymbolIndex:
    """A SymbolIndex to resolve anchors against, built from api_surface's raw type entries
    (TC-010's engine output / TC-011's fixture) - one declaration per class, one per member,
    named ``ClassName`` or ``ClassName.MemberName``.
    """
    declarations: list[Declaration] = []
    for entry in types:
        name = str(entry.get("name") or "")
        if not name:
            continue
        declarations.append(Declaration(anchor=name))
        for method in entry.get("methods") or []:
            method_name = method.get("name") if isinstance(method, Mapping) else None
            if method_name:
                declarations.append(Declaration(anchor=f"{name}.{method_name}"))
        for prop in entry.get("properties") or []:
            prop_name = prop.get("name") if isinstance(prop, Mapping) else None
            if prop_name:
                declarations.append(Declaration(anchor=f"{name}.{prop_name}"))
    return SymbolIndex(declarations)


def known_counts_from_fixture(fixture: Mapping[str, object]) -> dict[str, int]:
    """Counts the raw-knowledge tier can confidently vouch for.

    ``type_count`` is measured before any reduction, so the total is confident even when the
    fixture's own ``types`` list was truncated afterward. A per-kind breakdown (classes,
    structs, ...) is confident only when the fixture was NOT reduced - a truncated list's own
    breakdown is a property of an arbitrary prefix, not of the population, and using it to
    corroborate a narrower claim would be exactly the false confidence OQ-001 exists to name.
    """
    counts: dict[str, int] = {}
    if not fixture.get("truncated", False):
        for entry in fixture.get("types") or []:
            unit = _COUNTABLE_UNIT_KIND.get(str(entry.get("kind", "")))
            if unit:
                counts[unit] = counts.get(unit, 0) + 1
    if "type_count" in fixture:
        counts["types"] = int(fixture["type_count"])
    return counts


def find_symbol_anchors(text: str) -> list[str]:
    """Every inline code span in *text* shaped like a citable symbol anchor - a call's
    trailing ``(...)`` is stripped, since the anchor is the symbol, not the invocation.
    """
    anchors = []
    for span in _INLINE_CODE_SPAN.findall(text):
        candidate = _CALL_SUFFIX.sub("", span).strip()
        if _ANCHOR_SHAPE.match(candidate):
            anchors.append(candidate)
    return anchors


def find_numeric_claims(text: str) -> list[tuple[int, str]]:
    """(claimed number, unit) for every countable-fact claim in *text* - "805 classes", etc."""
    return [
        (int(match.group("number").replace(",", "")), match.group("unit").lower())
        for match in _NUMERIC_CLAIM.finditer(text)
    ]


def validate_numeric_claim(number: int, unit: str, known_counts: Mapping[str, int]) -> ValidationResult:
    known = known_counts.get(unit)
    if known is None:
        return ValidationResult(
            verdict=INSUFFICIENT_EVIDENCE,
            detail=f"raw knowledge has no confirmed count of {unit}; cannot corroborate {number}",
        )
    if known != number:
        return ValidationResult(
            verdict=UNSUPPORTED, detail=f"claims {number} {unit}; raw knowledge counts {known}"
        )
    return ValidationResult(verdict=SUPPORTED, detail=f"matches raw knowledge's {known} {unit}")


def validate_chunk(
    chunk: Chunk, symbol_index: SymbolIndex, known_counts: Mapping[str, int]
) -> Chunk:
    """*chunk* with ``validation`` set to the worst verdict any claim inside it earned."""
    problems: list[ValidationResult] = []
    for anchor in find_symbol_anchors(chunk.text):
        if not anchor_resolves(anchor, symbol_index):
            problems.append(ValidationResult(verdict=UNSUPPORTED, detail=f"anchor `{anchor}` does not resolve"))
    for number, unit in find_numeric_claims(chunk.text):
        result = validate_numeric_claim(number, unit, known_counts)
        if result.verdict != SUPPORTED:
            problems.append(result)
    if not problems:
        return replace(chunk, validation=ValidationResult(verdict=SUPPORTED, detail="every claim resolves"))
    worst = next((p for p in problems if p.verdict == UNSUPPORTED), problems[0])
    return replace(chunk, validation=worst)


def validate_document(
    chunks: Sequence[Chunk], symbol_index: SymbolIndex, known_counts: Mapping[str, int]
) -> list[Chunk]:
    """Every chunk, each with its own validation set - never left unqualified."""
    return [validate_chunk(chunk, symbol_index, known_counts) for chunk in chunks]


def citable_chunks(chunks: Sequence[Chunk]) -> list[Chunk]:
    """Only the chunks whose every claim resolved - the "excluded" half of the card's rule."""
    return [chunk for chunk in chunks if chunk.validation.verdict == SUPPORTED]
