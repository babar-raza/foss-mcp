"""Offline tests against the committed html/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-155 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/html_python/api_surface.json``.

Like slides/python (and unlike pdf/net's tree-sitter/C# reader), this fixture was produced
by the independent pure-``ast`` reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``,
adapted in ``run_extraction._python_types_from_surface``): its ``language`` field reads
literally ``"python"`` and its observed ``kind`` vocabulary among the reduced 300 entries is
``{"class", "function"}`` - unlike slides/python, a handful of module-level functions (e.g.
``element_matches``, ``select``, ``detect_encoding``) survived the centrality-ranked truncation
here, so no top-level ``"enum"`` entry appears and there is no tree-sitter grammar name at all.

Regenerated 2026-10-09 (G2/TC-267, rework attempt 2) once TC-274 fixed the real gap TC-267's
own first attempt found and stopped on: this repository's near-universal "real class in a
leading-underscore impl module, re-exported via a public package __init__.py" convention
(101 of 104 real .py files) meant ``bases``/``return_type``/``param_types`` were discarded for
almost every type here even after TC-265. With TC-274 integrated, 218 of the full 323 types now
carry a real, non-empty ``bases`` list and the centrality ranking is a genuine measurement
rather than a near-total no-op (15 of 323 types score above zero, versus 3 before TC-274).

Regenerated again 2026-10-09 (G2/TC-297) once TC-293 (the ``_flatten_inheritance`` short-name-
collision fix) and TC-294 (real methods recovered for an underscore-origin re-export) both
landed on main. TC-289's own live measurement against the pre-TC-294 fixture found only 2 of
323 types carried any method at all, and HTMLElement - the #1-centrality anchor, real base of
156 of this fixture's own kept subclass entries (78 distinct names, each appearing twice: once
re-exported from ``aspose_html.dom`` and once from ``aspose_html.dom.html``) - had zero. This
run's own live measurement: HTMLElement now carries 27 genuine own methods (``click``,
``focus``, ``blur``, ``title``, ``tab_index`` among them, read straight from its real upstream
definition in ``dom/_html_element.py``) plus 119 more copied in from its own bases (``Element``,
``Node``, ``EventTarget``) by ``_flatten_inheritance`` - each of those 119 correctly carrying an
``inherited_from`` tag pointing at the real declaring class, never at HTMLElement itself. 221 of
the 300 kept types now carry at least one method (own or inherited), versus TC-289's measured 2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "html_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "html" / "python.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "python"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]


def test_the_fixture_contains_real_python_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.HTML-FOSS-for-Python classes, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"HTMLDocument", "DOMParser", "CSSMediaRule"} <= names
    assert all(entry["class_import"].startswith("aspose_html") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. TC-297's own live rerun
    # added "enum" to this set versus TC-267's rework-attempt-2 fixture (an Enum/IntEnum/Flag
    # subclass now survives the centrality-ranked truncation that did not before).
    assert kinds == {"class", "function", "enum"}


def test_the_real_highest_centrality_type_survived_truncation() -> None:
    # Measured directly over this fixture's own live run's FULL 323-type artifact (before
    # the 300-cap truncation), using the project's own _centrality_scores(): HTMLElement
    # scores 156 - by a wide margin the single most-referenced type, since every concrete
    # HTML element class (HTMLDivElement, HTMLButtonElement, HTMLMediaElement, ...) names it
    # as a base. This is only a meaningful measurement, rather than a structural no-op, since
    # TC-274 fixed python_surface.py to recover bases/return_type/param_types for a class
    # defined in a leading-underscore impl module and re-exported via a public package
    # __init__.py (this repository's own near-universal convention: 101 of 104 real .py
    # files) - confirmed live here by 218 of the full 323 types carrying a non-empty
    # ``bases`` list, and 15 of 323 scoring above zero, versus just 3 before that fix.
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    assert "HTMLElement" in by_name
    assert by_name["HTMLElement"]["class_import"] == "aspose_html.dom.HTMLElement"
    # Real, observed inheritance evidence - not a placeholder: a handful of real concrete
    # HTML element classes this same fixture carries do name "HTMLElement" as a base.
    referencing = [
        entry["name"]
        for entry in data["types"]
        if entry is not by_name["HTMLElement"] and "HTMLElement" in (entry.get("bases") or [])
    ]
    assert referencing, "expected at least one kept type to list HTMLElement as a base"


def test_html_element_now_carries_real_own_methods() -> None:
    # TC-289's own live measurement against the pre-TC-294 fixture found HTMLElement had
    # ZERO methods of any kind - the underscore-origin re-export convention (its real
    # definition lives in dom/_html_element.py, re-exported via dom/__init__.py) meant
    # python_surface.py's per-file scan never reached its class body at all. TC-294 fixed
    # that; this is HTMLElement's own live, observed method list from THIS run, not an
    # invented or assumed one.
    data = _load_fixture()
    html_element = next(entry for entry in data["types"] if entry["name"] == "HTMLElement")
    assert html_element["class_import"] == "aspose_html.dom.HTMLElement"
    own_methods = [m for m in html_element["methods"] if not m.get("inherited_from")]
    own_names = {m["name"] for m in own_methods}
    # Real, observed upstream members of dom/_html_element.py's own HTMLElement class -
    # not guessed: TC-289's own prior investigation of the live upstream source named
    # exactly this set as the expected recovery target.
    assert {"click", "focus", "blur", "title", "tab_index"} <= own_names
    # Near-zero before TC-294 (0 observed by TC-289); comfortably non-trivial now.
    assert len(own_methods) >= 20


def test_a_real_subclass_carries_correct_inherited_from_provenance() -> None:
    # TC-261's inherited_from tagging only matters if a real member actually gets copied
    # down from a real base - which TC-293's short-name-collision fix and TC-294's method
    # recovery are what make possible for HTMLElement's own subclasses for the first time.
    # HTMLMediaElement is a real, concrete Aspose.HTML-FOSS-for-Python class (dom/__init__.py
    # re-exports it from dom/_html_media_element.py) that lists HTMLElement as a base.
    data = _load_fixture()
    media_element = next(
        entry
        for entry in data["types"]
        if entry["name"] == "HTMLMediaElement" and entry["class_import"] == "aspose_html.dom.HTMLMediaElement"
    )
    assert "HTMLElement" in media_element["bases"]
    click_entries = [m for m in media_element["methods"] if m["name"] == "click"]
    assert click_entries, "expected HTMLMediaElement to have inherited HTMLElement.click"
    # The tag names the real, original declaring class's public qualified name - never the
    # intermediate subclass, and never left absent (which is how a silently-uncredited
    # inherited member looked before TC-261).
    assert click_entries[0]["inherited_from"] == "aspose_html.dom.HTMLElement"
