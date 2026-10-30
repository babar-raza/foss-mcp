"""G3/TC-612 (I1, REQ-G3-012): a canonical symbol-identity model.

`chunk_builder._reexport_pairs` already encodes the real, carefully-tuned rules (TC-382/522/532/541)
for deciding when two extracted entries are "the same symbol": an explicit `reexport_of` chain
(TC-522), a package `__init__.py` stub paired with its one member-bearing definition (TC-382 rule 8),
and a member-less Python `class`/`enum` paired with the one member-bearing entry of the same name and
kind (TC-541) - each guarded against chains, cycles and TC-532's shadow/shadowed-by markers. This
module does not re-implement any of that: it REUSES `chunk_builder._reexport_pairs` and
`chunk_builder._slot_units` directly (deliberate, acknowledged reuse of module-internal pairing logic
from a sibling module in the same package - see the import below) as the sole grouping engine, and
adds exactly what that engine does not provide:

  (a) a STABLE, NAME-BASED canonical id per unit, derived from the unit's own definition entry (never
      from a `_slot_units` positional index, which is an artifact of one extraction run's entry order);
  (b) a complete, de-duplicated alias-form list per unit, gathered from every entry in the unit.

`chunk_builder.py`, `get_symbol.py`'s `_owners` and `search_symbols.py`'s `_collapse_aliases` are three
separate, currently-divergent mechanisms for the same underlying question ("is this the same symbol?"):
`_reexport_pairs` works offline, before rendering, directly on the extractor's own fields
(`reexport_of`/`class_import`/`shadows`/`shadowed_by`) and only for Python; `_owners` and
`_collapse_aliases` work AFTER rendering, on already-built chunk text (declared members parsed back out
of rendered `Methods:`/`Properties:` blocks, or a hash of the whole rendered body), and apply to every
language because they never look at the extractor's own fields at all. This module's later siblings
(I2-I5) are what will let `get_symbol`/`search_symbols` consume the identity this module computes
instead of re-deriving it from rendered text.

This module is pure: no I/O, no mutation of its input, and deterministic given the same input. It does
rely on dict insertion order in one place (`_slot_units`'s own internal grouping dict, not anything
declared here) - under CPython 3.7+, which this repository's pinned toolchain guarantees fleet-wide,
dict order is insertion order, so that reliance is safe; this module's own OUTPUT order never depends on
it regardless, because the list this module returns is always built by indexing directly over *entries*
in the caller's own order, never by iterating a dict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# TC-612: deliberate reuse of chunk_builder's module-internal pairing engine. `_reexport_pairs` and
# `_slot_units` are underscore-prefixed (module-internal) but this import is intentional: they already
# encode real, carefully-tuned grouping rules this module must not re-implement. `_entry_fqn` is reused
# for the same reason - it is the exact "class_import, else name" fallback this card's own contract
# describes as "the definition entry's own class_import or equivalent FQN-shaped field".
from foss_mcp.indexing.chunk_builder import _entry_fqn, _reexport_pairs, _slot_units

__all__ = ["SymbolIdentity", "compute_canonical_identities"]


@dataclass(frozen=True)
class SymbolIdentity:
    """One *entries[index]*'s identity: *canonical_id* and *aliases* are shared by every entry of its
    unit (a definition and every shell paired to it by `chunk_builder._reexport_pairs`); *is_definition*
    is True only for the one entry in that unit that `_reexport_pairs` never lists as a shell (a pair's
    key) - every other entry of the unit is an alias of it. An entry `_reexport_pairs` never paired at
    all is a unit of one: its own canonical id, a single-element alias list, `is_definition=True`.
    """

    canonical_id: str
    aliases: tuple[str, ...]
    is_definition: bool


def _alias_forms(entry: Mapping[str, object]) -> list[str]:
    """Every name-shaped field a single *entry* carries, de-duplicated, in the order checked.

    (1) the entry's own identifier - `class_import`, else plain `name` (the same fallback
    `chunk_builder._entry_fqn` uses); (2) the `package.name` qualified form `chunk_builder._package_lines`
    synthesizes for a Go-shaped entry (one with a `package` field instead of `class_import` - Go has no
    `class_import` at all, confirmed against the real committed `tests/fixtures/pdf_go/api_surface.json`);
    (3) `reexport_of`, `shadows`, `shadowed_by`, `alias_of` - each confirmed present on real, committed
    fixtures (`reexport_of`: slides_python, 3d_python; `shadows`/`shadowed_by`: 3d_python; `alias_of`:
    pdf_typescript, cells_rust), each included only when the real entry actually carries it.
    """
    forms: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        text = str(value).strip() if value is not None else ""
        if text and text not in seen:
            seen.add(text)
            forms.append(text)

    add(_entry_fqn(entry))
    package = " ".join(str(entry.get("package") or "").split())
    name = str(entry.get("name") or "")
    if package and name:
        add(f"{package}.{name}")
    for field in ("reexport_of", "shadows", "shadowed_by", "alias_of"):
        add(entry.get(field))
    return forms


def compute_canonical_identities(entries: Sequence[Mapping[str, object]]) -> list[SymbolIdentity]:
    """One `SymbolIdentity` per entry of *entries*, in the same order, built on top of
    `chunk_builder._reexport_pairs` / `_slot_units` (the real grouping engine) rather than
    re-deriving any pairing logic here.

    *entries* is read, never mutated; the result never aliases *entries*' own mutable values. Pure and
    deterministic: calling this twice on equal input returns equal output.
    """
    pairs = _reexport_pairs(entries)
    unit_of = _slot_units(pairs, len(entries))

    groups: dict[int, list[int]] = {}
    for index, unit in enumerate(unit_of):
        groups.setdefault(unit, []).append(index)

    # Per unit: (canonical id, alias-form list, the index of the unit's real definition entry).
    # `_slot_units` names a unit by its group's SMALLEST numeric index, which is a shell's index
    # whenever a shell happens to sort before its definition - never trust that numeric name as the
    # definition. The real definition is instead the one entry in the group `_reexport_pairs` never
    # lists as a shell (never a key of `pairs`); every rule that adds to `pairs` only ever maps a shell
    # index to a definition index that is not itself already a shell, so exactly one such entry exists
    # per group (the `group[0]` fallback below is only reached for a defensive edge case this module has
    # not observed against any real or synthetic fixture).
    identity_of_unit: dict[int, tuple[str, tuple[str, ...], int]] = {}
    for unit, group in groups.items():
        definition_index = next((index for index in group if index not in pairs), group[0])
        canonical_id = _entry_fqn(entries[definition_index])
        forms: list[str] = []
        seen: set[str] = set()
        for index in group:
            for form in _alias_forms(entries[index]):
                if form not in seen:
                    seen.add(form)
                    forms.append(form)
        identity_of_unit[unit] = (canonical_id, tuple(forms), definition_index)

    result: list[SymbolIdentity] = []
    for index, unit in enumerate(unit_of):
        canonical_id, aliases, definition_index = identity_of_unit[unit]
        result.append(
            SymbolIdentity(
                canonical_id=canonical_id,
                aliases=aliases,
                is_definition=(index == definition_index),
            )
        )
    return result
