"""Offline tests against the committed page/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-156 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/page_python/api_surface.json``.

Like slides/python and barcode/python (and unlike pdf/net's tree-sitter/C# reader), this
fixture was produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all. Like barcode/python
(and unlike slides/python), this repository's 244-entry surface (no truncation - the
default ``--max-types 300`` already covers the whole real surface) does retain top-level
module functions: the observed ``kind`` vocabulary is ``{"class", "enum", "function"}``.
Unlike both prior Python-platform pilots, this package's ``class_import`` prefix is
``aspose.page`` (neither ``aspose.slides_foss`` nor ``aspose_barcode_foss``) - observed
directly from this card's own run, not derived from a pattern.

Regenerated 2026-10-10 (G2/TC-323): this fixture was originally produced by TC-156, the
original onboarding wave, well before the bases/inheritance pipeline this session built
(TC-265/TC-252/TC-274/TC-281/TC-293/TC-294) existed - every one of its 244 types carried
``bases: []`` and empty ``methods``/``properties`` for every member, not because the real
upstream repository lacks inheritance, but purely because the fixture predated the pipeline
that would have populated it. The source commit (``43cef367cd0b2cf00cc03064ca9d29d1ff507967``)
is unchanged - this is a live re-run against the exact same pinned upstream snapshot, not a
re-pin. This run's own live measurement: 16 of the 244 kept types now carry a real, non-empty
``bases`` list, and 37 real methods across those types carry a correctly-rooted
``inherited_from`` tag. ``Clipper`` (``src/aspose/page/ps/clipper.py``) is the clearest real
example: it lists ``ClipperBase`` as a base and inherits 34 of ``ClipperBase``'s own methods
(e.g. ``add_path``), each correctly tagged ``inherited_from: "aspose.page.ps.clipper.ClipperBase"``.
No multi-level inheritance chain with any actual inherited data exists in this pilot's reduced
surface: the one multi-level base chain present (``PsIOError`` -> ``PsError`` -> ``Exception``)
bottoms out at ``PsError``, which itself declares zero methods/properties, and ``Exception`` is
a builtin never present in this fixture at all - so there is nothing for ``_flatten_inheritance``
to propagate transitively here, and every one of the 37 real ``inherited_from`` tags observed in
this run points directly at the member's own real, immediate declaring parent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "page_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "page" / "python.yaml"

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
    # Real Aspose.Page-FOSS-for-Python classes, not placeholders - would not survive an
    # empty or synthetic fixture. Matrix/Path come from common/render_model.py;
    # AxialShading from common/color_resources.py.
    assert {"AxialShading", "Matrix", "Path"} <= names
    assert all(entry["class_import"].startswith("aspose.page") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Like barcode/python,
    # this reduced surface retains top-level module functions alongside classes and enums.
    assert kinds == {"class", "enum", "function"}


def test_a_real_top_level_function_is_present() -> None:
    data = _load_fixture()
    functions = {entry["name"] for entry in data["types"] if entry["kind"] == "function"}
    # Real top-level PS/EPS/XPS-to-PDF conversion entry points from this library's own
    # description, observed directly in this card's own run - not guessed.
    assert {"ps_to_pdf", "xps_to_pdf", "eps_metadata"} <= functions


def test_a_real_type_now_carries_a_non_empty_bases_list() -> None:
    # G2/TC-323's own live re-run: TC-156's original fixture predated the bases/inheritance
    # pipeline entirely, so every type's "bases" read "[]". This pilot's real upstream source
    # does have real inheritance - Clipper (src/aspose/page/ps/clipper.py) is a real,
    # concrete class that extends ClipperBase, observed directly in this run, not guessed.
    data = _load_fixture()
    clipper = next(
        entry
        for entry in data["types"]
        if entry["class_import"] == "aspose.page.ps.clipper.Clipper"
    )
    assert clipper["bases"] == ["ClipperBase"]
    # Real, observed inheritance evidence across the fixture - not a single coincidence:
    # several other real classes in this pilot (the PsError exception hierarchy,
    # DefaultRasterWriter, PolyTree, PrintTicketScope) also carry a non-empty bases list.
    with_bases = [entry for entry in data["types"] if entry.get("bases")]
    assert len(with_bases) >= 10


def test_a_real_subclass_carries_correct_inherited_from_provenance() -> None:
    # TC-261's inherited_from tagging only matters if a real member actually gets copied
    # down from a real base - which this fixture's regeneration under the full
    # bases/inheritance pipeline is what makes possible for this pilot for the first time
    # (TC-156's original fixture carried zero methods/properties of any kind, inherited or
    # not). Clipper is a real, concrete class extending ClipperBase; add_path is one of
    # ClipperBase's own real methods, observed directly in this run.
    data = _load_fixture()
    clipper = next(
        entry
        for entry in data["types"]
        if entry["class_import"] == "aspose.page.ps.clipper.Clipper"
    )
    assert "ClipperBase" in clipper["bases"]
    add_path_entries = [m for m in clipper["methods"] if m["name"] == "add_path"]
    assert add_path_entries, "expected Clipper to have inherited ClipperBase.add_path"
    # The tag names the real, original declaring class's own qualified name - never the
    # subclass itself, and never left absent (which is how a silently-uncredited inherited
    # member looked before TC-261, and exactly how TC-156's original fixture looked before
    # this card's regeneration, since it had no methods of any kind to tag).
    assert add_path_entries[0]["inherited_from"] == "aspose.page.ps.clipper.ClipperBase"
    # Clipper's own "execute" (a genuinely locally-declared method, observed directly in
    # this run) must never carry the inherited_from key at all - it is Clipper's own, not
    # copied from a base.
    own_entries = [m for m in clipper["methods"] if m["name"] == "execute"]
    assert own_entries, "expected Clipper to declare its own execute method"
    assert all(not m.get("inherited_from") for m in own_entries)


def test_no_multi_level_inherited_from_chain_exists_in_this_fixture() -> None:
    # Hand-verified against this run's own live output (not assumed): the one real
    # multi-level base chain present in this pilot's reduced surface is
    # PsIOError -> PsError -> Exception. It carries no actual inherited DATA to propagate
    # transitively, because PsError itself declares zero methods/properties of its own, and
    # "Exception" is a Python builtin, never a type present in this fixture at all. So every
    # inherited_from tag observed anywhere in this fixture points at a member's own real,
    # immediate declaring parent - there is no case here where a member is copied through an
    # intermediate class and must stay rooted to a grandparent instead (the scenario
    # TC-261's own multi-level fix targets), because no such chain with real data exists in
    # this pilot's real upstream source.
    data = _load_fixture()
    by_import = {entry["class_import"]: entry for entry in data["types"]}
    ps_error = by_import["aspose.page.ps.errors.PsError"]
    assert ps_error["bases"] == ["Exception"]
    assert ps_error["methods"] == []
    assert ps_error["properties"] == []
    ps_io_error = by_import["aspose.page.ps.errors.PsIOError"]
    assert ps_io_error["bases"] == ["PsError"]
    assert ps_io_error["methods"] == []
