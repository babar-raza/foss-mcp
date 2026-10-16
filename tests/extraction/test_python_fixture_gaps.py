"""TC-522: which committed Python fixtures predate the fidelity fix, and proof the gap is real.

Of the twelve committed python fixtures, seven were produced before TC-522 (``extractor_version`` absent, i.e. 1;
3d_python, html_python, pdf_python, slides_python and words_python were re-extracted by TC-550 at version 5 and
left ``KNOWN_GAP``): none of their method parameters has a ``default``, none of their methods a ``static`` / ``classmethod``
modifier, and none of their re-export shells a ``reexport_of``. ``KNOWN_GAP`` documents that, one line per
pilot, and the tests make the documentation self-checking in both directions:

* a fixture below ``REQUIRED_VERSION`` MUST be listed in ``KNOWN_GAP`` (and for the three pilots the
  reviewer measured, the documented gap must still be real);
* a fixture at or above ``REQUIRED_VERSION`` must NOT be listed, and for those three pilots it must show
  defaults, modifiers and resolvable ``reexport_of`` keys.

The re-extraction card regenerates the fixtures and deletes the matching ``KNOWN_GAP`` entries; that is the
point of the mechanism, not a skipped test.
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
    "jmap_python",
    "note_python",
    "page_python",
    "pdf_python",
    "slides_python",
    "tex_python",
    "words_python",
)

# The pilots whose gap the independent review measured against the upstream sources.
REVIEWED = ("cells_python", "jmap_python", "pdf_python")

KNOWN_GAP: dict[str, str] = {
    "barcode_python": "version-1 fixture: parameter defaults, keyword-only markers and static/classmethod marks were not extracted",
    "cells_python": "reviewer RST-05: 420 upstream parameters with a default served as required, 51 static/classmethods unmarked, Workbook.__init__ password shown required",
    "email_python": "version-1 fixture: parameter defaults, keyword-only markers and static/classmethod marks were not extracted",
    "jmap_python": "reviewer RST-05: 8 upstream parameters with a default served as required, 45 static/classmethods unmarked",
    "note_python": "version-1 fixture: parameter defaults, keyword-only markers and static/classmethod marks were not extracted",
    "page_python": "version-1 fixture: parameter defaults, keyword-only markers and static/classmethod marks were not extracted",
    "tex_python": "version-1 fixture: parameter defaults, keyword-only markers and static/classmethod marks were not extracted",
}


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


def test_known_gap_names_the_twelve_python_fixtures_with_one_line_reasons() -> None:
    assert set(KNOWN_GAP) <= set(PYTHON_PILOTS)
    assert len(PYTHON_PILOTS) == 12
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


def test_pdf_python_facade_gap_is_documented() -> None:
    """``aspose_pdf.facades.PdfFileSecurity`` is absent from the committed fixture today (its definition
    was cut by the symbol cap while its re-export shell, listing only inherited methods, was kept). This
    goes red the day the fixture is regenerated without updating KNOWN_GAP and this test."""
    fixture = _load("pdf_python")
    by_import = {entry.get("class_import"): entry for entry in fixture["types"]}
    in_gap = "pdf_python" in KNOWN_GAP
    assert ("aspose_pdf.facades.PdfFileSecurity" in by_import) is (not in_gap)
    shell = by_import["aspose_pdf.PdfFileSecurity"]
    own = {method["name"] for method in shell.get("methods") or []}
    if in_gap:
        assert "encrypt_file" not in own
        assert "bind_pdf" in own
    else:
        definition = by_import["aspose_pdf.facades.PdfFileSecurity"]
        assert {"encrypt_file", "decrypt_file", "change_password"} <= {
            method["name"] for method in definition["methods"]
        }
        assert "aspose_pdf.facades.PdfFileEditor" in by_import
