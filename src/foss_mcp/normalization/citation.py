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

from foss_mcp.extraction.claim_id_bridge import (
    Declaration,
    Resolution,
    SymbolIndex,
    anchor_resolves,
    resolve_anchor,
)
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


def _is_real_compile_verified_example(chunk: Chunk) -> bool:
    """True exactly for a chunk built by infra/build_chunks.py's
    ``_build_verified_example_chunks`` - a real example whose CODE already really compiled
    and ran against the real pinned reference library (REQ-G2-048). ``content_type`` is the
    real, structured field ``make_document``/``chunk_document`` set from that call site's own
    ``content_type="example"`` - never text-sniffed from the body's ``Kind: verified_example``
    line, which is prose, not a contract.
    """
    return chunk.content_type == "example"


def validate_chunk(chunk: Chunk, symbol_index: SymbolIndex, known_counts: Mapping[str, int]) -> Chunk:
    """*chunk* with ``validation`` set to the worst verdict any claim inside it earned.

    A genuinely compile-verified example chunk (``_is_real_compile_verified_example``) skips
    the symbol-anchor check: its prose description routinely cites a method by a bare name in
    an inline code span (e.g. `` `add_auto_shape()` ``) as a completely normal writing
    convention, which ``symbol_index_from_api_surface`` can never resolve since it only ever
    registers qualified ``ClassName.MethodName`` anchors (correctly - a bare name could be
    ambiguous across classes). Requiring the description to ALSO clear the citation-anchor bar
    would penalize prose for a fact (the code) that real compilation has already verified more
    strongly than anchor resolution ever could.

    The numeric-claim check still applies even to an example chunk: it guards a different kind
    of claim - a library-wide count such as "805 classes" - that compiling one example's code
    says nothing about, and a real example's description (see
    tests/fixtures/furnished/slides_python/pages/_index.md's "Create a Presentation and Add a
    Shape" block) is procedural prose about the steps being taken, not a claim about the
    library's total surface, so this check is orthogonal and stays on for every chunk kind.

    A bare-name anchor (no dot in it, e.g. `` `AddWatermarkAnnotation` ``) that fails the
    ordinary exact-match check still resolves when it is GLOBALLY UNAMBIGUOUS - exactly one
    qualified declaration across the whole index carries that member name
    (``symbol_index.unambiguous_qualified_anchor_for_bare_member``). Ordinary furnished prose
    routinely cites a method conversationally by its bare name with no independent
    verification behind it (unlike a compile-verified example's description, which is exempted
    above entirely), so the anchor-resolution mechanism itself must recognize this case rather
    than mark real, correct prose unsupported. A bare name shared by two or more classes stays
    unresolved, exactly as before - this generalizes the existing ambiguity rule, never weakens
    it.
    """
    problems: list[ValidationResult] = []
    if not _is_real_compile_verified_example(chunk):
        for anchor in find_symbol_anchors(chunk.text):
            if anchor_resolves(anchor, symbol_index):
                continue
            if "." not in anchor and symbol_index.unambiguous_qualified_anchor_for_bare_member(anchor):
                continue
            problems.append(
                ValidationResult(verdict=UNSUPPORTED, detail=f"anchor `{anchor}` does not resolve")
            )
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


def describe_unresolved_anchors(
    chunks: Sequence[Chunk], symbol_index: SymbolIndex
) -> list[tuple[str, Resolution]]:
    """One ``(section_title, Resolution)`` entry per anchor that caused an ``UNSUPPORTED``
    chunk's exclusion - purely diagnostic, giving an operator the signal
    ``validate_chunk``'s own flat "anchor `X` does not resolve" detail string does not:
    whether the anchor is a near-miss (``PARTIALLY_SUPPORTED`` - one or more declarations share
    the member name, but the anchor itself does not resolve unambiguously, likely a typo or
    stale doc) or a genuinely absent symbol (``UNSUPPORTED`` with zero matches anywhere).

    Re-walks each ``UNSUPPORTED`` chunk's own ``find_symbol_anchors(chunk.text)``, mirroring
    ``validate_chunk``'s own two skip conditions exactly - an anchor that already resolves via
    ``anchor_resolves``, or resolves through the bare-member exemption
    (``unambiguous_qualified_anchor_for_bare_member``) - including the
    ``_is_real_compile_verified_example`` exemption that skips the anchor check entirely for a
    genuinely compile-verified example chunk, so only anchors that actually caused the real
    exclusion are reported here (never an anchor that happens to appear in a chunk excluded for
    an unrelated numeric-claim reason).

    Purely additive: never called from ``validate_chunk``, ``validate_document``, or
    ``citable_chunks`` themselves, and never changes any of their return values.
    """
    described: list[tuple[str, Resolution]] = []
    for chunk in chunks:
        if chunk.validation.verdict != UNSUPPORTED:
            continue
        if _is_real_compile_verified_example(chunk):
            continue
        for anchor in find_symbol_anchors(chunk.text):
            if anchor_resolves(anchor, symbol_index):
                continue
            if "." not in anchor and symbol_index.unambiguous_qualified_anchor_for_bare_member(anchor):
                continue
            described.append((chunk.section_title, resolve_anchor(anchor, symbol_index)))
    return described
