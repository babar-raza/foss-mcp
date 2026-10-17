"""TC-523: which committed TypeScript and JavaScript fixtures predate the fidelity fix, and proof the gap is real.

Two of the three committed TypeScript fixtures were produced before TC-523 (``extractor_version``
absent, i.e. 1; pdf_typescript was re-extracted by TC-550 at version 5 and left ``KNOWN_GAP``): every interface property is ``writable`` False (so every one is served "read-only"), no
type alias carries its right-hand side, and no method has a ``static`` / ``async`` / ``optional`` mark.
``KNOWN_GAP`` documents that, one line per pilot, and the tests make the documentation self-checking in
both directions:

* a fixture below ``REQUIRED_VERSION`` MUST be listed in ``KNOWN_GAP`` and the documented gap must still
  be real (every interface property read-only, no method with ``modifiers``);
* a fixture at or above ``REQUIRED_VERSION`` must NOT be listed, and must show writable interface
  properties, a right-hand side on every type alias and no ``@internal`` member.

The re-extraction card regenerates the fixtures and deletes the matching ``KNOWN_GAP`` entries; that is the
point of the mechanism, not a skipped test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

REQUIRED_VERSION = 5

PILOTS = (
    "cells_typescript",
    "pdf_typescript",
    "3d_typescript",
)

KNOWN_GAP: dict[str, str] = {
    "cells_typescript": "reviewer RST-07: 649 of 657 interface properties served read-only (upstream has 0 readonly), 25 type aliases without a right-hand side, static and optional marks missing",
    "3d_typescript": "version-1 fixture: interface properties all read-only, optional/static/async marks and parameter defaults were not extracted",
}

_INTERNAL_TAG = re.compile(r"@internal\b")


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name / "api_surface.json").read_text(encoding="utf-8"))


def _version(fixture: dict[str, Any]) -> int:
    value = fixture.get("extractor_version", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) else 1


def _interface_properties(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        prop
        for entry in fixture["types"]
        if entry.get("kind") == "interface_declaration"
        for prop in entry.get("properties") or []
    ]


def _methods(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [method for entry in fixture["types"] for method in entry.get("methods") or []]


def _aliases(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in fixture["types"] if entry.get("kind") == "type_alias_declaration"]


def _internal_members(fixture: dict[str, Any]) -> list[str]:
    return [
        f"{entry['name']}.{member['name']}"
        for entry in fixture["types"]
        for member in [*(entry.get("methods") or []), *(entry.get("properties") or [])]
        if _INTERNAL_TAG.search(str(member.get("doc") or ""))
    ]


@pytest.mark.parametrize("name", PILOTS)
def test_the_fixture_is_current_or_a_documented_gap(name: str) -> None:
    fixture = _load(name)
    typescript = fixture["language"] == "typescript"
    assert fixture["language"] in ("typescript", "javascript")
    if _version(fixture) < REQUIRED_VERSION:
        assert name in KNOWN_GAP, (
            f"{name} predates extractor version {REQUIRED_VERSION} and is not documented"
        )
        assert not any(method.get("modifiers") for method in _methods(fixture)), (
            f"{name}: the documented gap (no method modifiers) is no longer real"
        )
        if typescript:
            properties = _interface_properties(fixture)
            assert properties, f"{name}: no interface property to document the gap on"
            assert not any(prop.get("writable") for prop in properties), (
                f"{name}: the documented gap (every interface property read-only) is no longer real"
            )
    else:
        assert name not in KNOWN_GAP, f"{name} is current: delete its KNOWN_GAP entry"
        assert _internal_members(fixture) == []
        if typescript:
            assert any(prop.get("writable") for prop in _interface_properties(fixture))
            assert [entry["name"] for entry in _aliases(fixture) if not entry.get("alias_of")] == []


def test_known_gap_is_exactly_the_old_fixtures() -> None:
    old = {name for name in PILOTS if _version(_load(name)) < REQUIRED_VERSION}
    assert set(KNOWN_GAP) == old


def test_known_gap_names_the_three_fixtures_with_one_line_reasons() -> None:
    assert set(KNOWN_GAP) <= set(PILOTS)
    # re-pinned by TC-551: 5 -> 3
    assert len(PILOTS) == 3
    for name, reason in KNOWN_GAP.items():
        assert reason.strip() and "\n" not in reason, name
    # re-extracted by TC-550: pdf_typescript left KNOWN_GAP (version 5), so only the names still listed are checked
    for name in ("cells_typescript", "pdf_typescript"):
        if name in KNOWN_GAP:
            assert "reviewer" in KNOWN_GAP[name]


def test_every_pilot_fixture_exists_on_disk() -> None:
    for name in PILOTS:
        assert (FIXTURES / name / "api_surface.json").is_file(), name


def test_required_version_is_the_extractor_version_that_fixes_the_gap() -> None:
    from foss_mcp.extraction import run_extraction

    assert REQUIRED_VERSION <= run_extraction.EXTRACTOR_VERSION


def test_the_reviewed_cells_gap_is_the_measured_one() -> None:
    """While cells_typescript is a gap, its interface properties are the ones the reviewer counted."""
    fixture = _load("cells_typescript")
    if _version(fixture) >= REQUIRED_VERSION:
        assert "cells_typescript" not in KNOWN_GAP
        return
    properties = _interface_properties(fixture)
    assert len(properties) >= 649
    assert all(prop.get("writable") is False for prop in properties)
    assert len(_aliases(fixture)) >= 25
    assert not any(entry.get("alias_of") for entry in _aliases(fixture))


def test_the_reviewed_pdf_gap_is_the_measured_one() -> None:
    fixture = _load("pdf_typescript")
    if _version(fixture) >= REQUIRED_VERSION:
        assert "pdf_typescript" not in KNOWN_GAP
        return
    assert len(_interface_properties(fixture)) >= 944
    assert len(_aliases(fixture)) >= 84
    assert len(_internal_members(fixture)) > 0


def test_a_current_fixture_may_not_keep_an_internal_member() -> None:
    for name in PILOTS:
        fixture = _load(name)
        if _version(fixture) >= REQUIRED_VERSION:
            assert _internal_members(fixture) == [], name
