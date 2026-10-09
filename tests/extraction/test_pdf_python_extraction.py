"""Offline tests against the committed pdf/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-157 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/pdf_python/api_surface.json``.

Like slides/python, this fixture was produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all.

Regenerated under TC-290 (a pure re-pin, no new root-cause work) because the
previous regeneration (TC-268) ran before TC-281's Python inheritance-flattening
fix (``run_extraction._extract_python_surface`` calling
``api_surface._flatten_inheritance(types)``) landed on main: that left 135 types
with non-empty ``bases`` but zero ``inherited_from`` tags. TC-252's
centrality-ranked ``reduce_fixture()`` and TC-265's real
``bases``/``return_type``/``param_types`` population are unchanged and still
integrated (confirmed by reading the source before this run). The reduced
300-entry sample for this repository still keeps only ``"class"`` and ``"enum"``
kinds, and centrality ranking still recovers ``"Page"`` - both observed directly
from this live run, not assumed from the sibling platform's fixture or from this
file's own prior content. New in this fixture: real inherited members now carry
a correct ``inherited_from`` tag (e.g. ``PdfStream`` inherits ``get``/``pop``
from its real base ``PdfDictionary``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "pdf" / "python.yaml"

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
    # Real Aspose-PDF-FOSS-for-Python classes/enums, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Action", "Annotation", "AnnotationFlags", "DuplexMode"} <= names
    assert all(entry["class_import"].startswith("aspose_pdf") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Unlike the pre-TC-268
    # fixture, this centrality-ranked 300-entry sample keeps only classes and enums; no
    # top-level function scored highly enough to survive the cut.
    assert kinds == {"class", "enum"}


def test_the_fixture_keeps_page_after_centrality_ranking() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # TC-252's centrality-ranked reduce_fixture(), now effective for this Python-sourced
    # pilot because TC-265 populates real bases/return_type/param_types, recovers "Page" -
    # the same top-level page type every sibling pdf/* pilot (go, java, net, cpp,
    # typescript) needed this exact fix to stop losing to alphabetical truncation.
    assert "Page" in names


def test_a_real_inherited_member_carries_the_correct_inherited_from_tag() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    # TC-290: TC-281's api_surface._flatten_inheritance() now runs for every
    # Python-sourced pilot (run_extraction._extract_python_surface calls it before
    # returning). Observed directly from this live run: PdfStream's real base is
    # PdfDictionary (see entry["bases"] below), and _flatten_inheritance copies
    # PdfDictionary's "get"/"pop" methods onto PdfStream, each tagged with the
    # base's real qualified class_import as inherited_from - not a guessed name,
    # and not present at all in the pre-TC-281 fixture this replaces (zero
    # inherited_from tags existed anywhere in it).
    pdf_stream = by_name["PdfStream"]
    assert pdf_stream["bases"] == ["PdfDictionary"]
    inherited = {m["name"]: m["inherited_from"] for m in pdf_stream["methods"] if "inherited_from" in m}
    assert inherited["get"] == "aspose_pdf.engine.cos.PdfDictionary"
    assert inherited["pop"] == "aspose_pdf.engine.cos.PdfDictionary"


def test_inherited_from_tags_are_not_vacuous() -> None:
    data = _load_fixture()
    # Falsifier guard for the regeneration itself: the pre-TC-281 fixture this
    # replaces had 135 types with non-empty bases but exactly zero inherited_from
    # tags anywhere. At least one real inherited member must now carry one.
    total_inherited = sum(
        1 for entry in data["types"] for m in entry.get("methods", []) if "inherited_from" in m
    )
    assert total_inherited > 0
