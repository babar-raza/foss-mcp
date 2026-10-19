"""TC-522: which committed Python fixtures predate the fidelity fix, and proof the gap is real.

Of the eleven committed python fixtures, five were produced before TC-522 (``extractor_version`` absent, i.e. 1;
3d_python, html_python, pdf_python, slides_python and words_python were re-extracted by TC-550 at version 5 and
left ``KNOWN_GAP``, and tex_python by TC-555): none of their method parameters has a ``default``, none of their methods a ``static`` / ``classmethod``
modifier, and none of their re-export shells a ``reexport_of``. ``KNOWN_GAP`` documents that, one line per
pilot, and the tests make the documentation self-checking in both directions:

* a fixture below ``REQUIRED_VERSION`` MUST be listed in ``KNOWN_GAP`` (and for the two pilots the
  reviewer measured, the documented gap must still be real);
* a fixture at or above ``REQUIRED_VERSION`` must NOT be listed, and for those two pilots it must show
  defaults, modifiers and resolvable ``reexport_of`` keys.

The re-extraction card regenerates the fixtures and deletes the matching ``KNOWN_GAP`` entries; that is the
point of the mechanism, not a skipped test. TC-532 regenerated the last five (barcode, cells, email, note and
page): ``KNOWN_GAP`` is empty and every fixture takes the "at or above the required version" branch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

REQUIRED_VERSION = 4

PYTHON_PILOTS = (
    "3d_python",
    "barcode_python",
    "cells_python",
    "email_python",
    "html_python",
    "note_python",
    "page_python",
    "pdf_python",
    "slides_python",
    "tex_python",
    "words_python",
)

# The pilots whose gap the independent review measured against the upstream sources.
# re-pinned by TC-551: REVIEWED = ("cells_python", "<removed pilot>", "pdf_python") -> REVIEWED = ("cells_python", "pdf_python")
REVIEWED = ("cells_python", "pdf_python")

# TC-532: all eleven fixtures were regenerated at extractor version 5, so nothing is documented as a gap any more.
# The mechanism stays: a future fixture below REQUIRED_VERSION must be listed here with a one-line reason, and the
# tests below fail while a listed fixture is current.
KNOWN_GAP: dict[str, str] = {}


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name / "api_surface.json").read_text(encoding="utf-8"))


def _version(fixture: dict[str, Any]) -> int:
    value = fixture.get("extractor_version", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) else 1


def _methods(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [method for entry in fixture["types"] for method in entry.get("methods") or []]


def _defaults(fixture: dict[str, Any]) -> int:
    return sum(
        1 for method in _methods(fixture) for param in method.get("params") or [] if "default" in param
    )


def _decorated(fixture: dict[str, Any]) -> int:
    return sum(
        1 for method in _methods(fixture) if {"static", "classmethod"} & set(method.get("modifiers") or [])
    )


def _unresolved_reexports(fixture: dict[str, Any]) -> list[str]:
    imports = {entry.get("class_import") for entry in fixture["types"]}
    return [
        f"{entry['class_import']} -> {entry['reexport_of']}"
        for entry in fixture["types"]
        if entry.get("reexport_of")
        and entry["reexport_of"] not in imports
        and not any(part.startswith("_") for part in entry["reexport_of"].split("."))
    ]


@pytest.mark.parametrize("name", PYTHON_PILOTS)
def test_the_fixture_is_current_or_a_documented_gap(name: str) -> None:
    fixture = _load(name)
    assert fixture["language"] == "python"
    if _version(fixture) < REQUIRED_VERSION:
        assert name in KNOWN_GAP, (
            f"{name} predates extractor version {REQUIRED_VERSION} and is not documented"
        )
        if name in REVIEWED:
            assert _defaults(fixture) == 0, f"{name}: the documented gap (no defaults) is no longer real"
            assert _decorated(fixture) == 0, (
                f"{name}: the documented gap (no static/classmethod) is no longer real"
            )
    else:
        assert name not in KNOWN_GAP, f"{name} is current: delete its KNOWN_GAP entry"
        assert _unresolved_reexports(fixture) == []
        if name in REVIEWED:
            assert _defaults(fixture) > 0
            assert _decorated(fixture) > 0
            assert any(entry.get("reexport_of") for entry in fixture["types"])


def test_known_gap_is_exactly_the_old_fixtures() -> None:
    old = {name for name in PYTHON_PILOTS if _version(_load(name)) < REQUIRED_VERSION}
    assert set(KNOWN_GAP) == old


def test_known_gap_names_the_eleven_python_fixtures_with_one_line_reasons() -> None:
    assert set(KNOWN_GAP) <= set(PYTHON_PILOTS)
    # re-pinned by TC-551: 12 -> 11
    assert len(PYTHON_PILOTS) == 11
    on_disk = {path.parent.name for path in FIXTURES.glob("*_python/api_surface.json")}
    assert on_disk == set(PYTHON_PILOTS)
    for name, reason in KNOWN_GAP.items():
        assert reason.strip() and "\n" not in reason, name
    # re-extracted by TC-550: pdf_python left KNOWN_GAP, so only the REVIEWED names still listed carry a reviewer note
    for name in REVIEWED:
        if name in KNOWN_GAP:
            assert "reviewer" in KNOWN_GAP[name]


def test_required_version_is_the_extractor_version_that_fixes_the_gap() -> None:
    from foss_mcp.extraction import run_extraction

    assert REQUIRED_VERSION <= run_extraction.EXTRACTOR_VERSION


def test_pdf_python_facade_definition_carries_its_own_methods() -> None:
    """TC-532 (the positive form of the TC-522 gap test): ``aspose_pdf.facades.PdfFileSecurity`` is in the fixture
    with its own ``encrypt_file``, ``decrypt_file`` and ``change_password``, and so is ``PdfFileEditor``; the
    re-export shell no longer stands alone with only the methods of its private base."""
    fixture = _load("pdf_python")
    by_import = {entry.get("class_import"): entry for entry in fixture["types"]}
    definition = by_import["aspose_pdf.facades.PdfFileSecurity"]
    own = {method["name"] for method in definition["methods"] if not method.get("inherited_from")}
    assert {"encrypt_file", "decrypt_file", "change_password"} <= own
    assert by_import["aspose_pdf.PdfFileSecurity"]["reexport_of"] == "aspose_pdf.facades.PdfFileSecurity"
    assert "aspose_pdf.facades.PdfFileEditor" in by_import
