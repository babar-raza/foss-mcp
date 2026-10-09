"""Offline tests against the committed pdf/go fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-038 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/pdf_go/api_surface.json``.

Like rust (and unlike python), "go" is not special-cased in
``run_extraction._LANGUAGE_BY_PLATFORM`` and goes through the ordinary tree-sitter path
(grammar name literally ``"go"``) via ``tree_sitter_engine.api_surface``. Its ``language``
field reads literally ``"go"``, and the observed ``kind`` vocabulary among the reduced fixture
is the tree-sitter Go grammar's own node-type names: ``{"type_spec", "function"}`` - Go has no
class/struct/enum/trait keywords of its own, so every named type declaration (struct, interface,
or defined type) surfaces as ``type_spec`` and every func declaration (free function or method
with a receiver) surfaces as ``function``.

Unlike cells/rust (219 real types, under the CLI's default ``--max-types 300`` cap so the
fixture is the full surface), this real repository has 2192 public types - far over the cap -
so the committed fixture is a genuinely REDUCED, deterministic (sorted-by-name) subset of 300,
and ``truncated`` is ``True`` here, not ``False``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_go" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "pdf" / "go.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "go"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture (regenerated under
    # TC-262 with TC-252's centrality-ranked reduce_fixture() - the repository had advanced
    # past the old, pre-TC-252 pin, so this is a fresh commit, not the stale one) -
    # re-verify by hand with
    # `git ls-remote https://github.com/aspose-pdf-foss/Aspose-PDF-FOSS-for-Go` if this ever
    # needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "cdf43df10c8c565ecaa978428b1fe66ad6685f8d"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has 2313 public types (re-measured under TC-262's live
    # re-extraction; the repository moved forward since the old, pre-TC-252 pin) - far over
    # the CLI's default --max-types 300 cap - so this fixture is a genuinely reduced subset,
    # unlike cells/rust's untruncated one. Selection is centrality-ranked (TC-252), not
    # alphabetical.
    assert data["type_count"] == 2313
    assert data["reduced_type_count"] == 300
    assert data["truncated"] is True


def test_the_fixture_contains_real_go_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose-PDF-FOSS-for-Go exported types, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"Document", "Annotation", "AnnotationCollection", "AFRelationship"} <= names
    assert all(entry["file"].endswith(".go") for entry in data["types"])


def test_the_centrality_ranked_selection_keeps_page() -> None:
    # Headline regression for TC-262: under the OLD, pre-TC-252 alphabetical selection, the
    # 300-type cut dropped "Page" - one of the most central types in a PDF library ("P" falls
    # well past an alphabetical cut of 300 out of 2313 names) - and a third independent audit
    # (2026-10-08, C4) named it explicitly as still missing from this exact fixture. TC-252's
    # centrality-ranked reduce_fixture() (ranking by how many other types reference a type's
    # bare name, not alphabetical order) now keeps it. Confirmed directly in this card's own
    # live re-extraction: "Page" is present among the 300 kept types.
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    assert "Page" in names
    page = next(entry for entry in data["types"] if entry["name"] == "Page")
    assert page["kind"] == "type_spec"
    assert page["file"] == "page.go"
    method_names = {m["name"] for m in page["methods"]}
    # Observed directly in the live run: Page has real methods with real doc comments.
    assert "RenderBMP" in method_names


def test_every_entry_is_a_real_go_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Go grammar's own node-type vocabulary for
    # this repository, not guessed or invented. Go has no class/struct/enum/trait keywords of
    # its own: every named type declaration surfaces as "type_spec" and every func declaration
    # (free function or method) surfaces as "function".
    assert kinds == {"type_spec", "function"}


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    for entry in data["types"]:
        for key in ("name", "kind", "file", "line", "doc", "methods", "properties", "bases", "reachable"):
            assert key in entry, (entry.get("name"), key)


def test_a_real_method_and_a_real_free_function_are_present() -> None:
    data = _load_fixture()
    # Observed directly in the live run: AFRelationship is a defined int type with a real
    # String() method (Go's idiomatic Stringer implementation), and AddFontFile is a real
    # standalone function with a real doc comment and a real string parameter.
    af_relationship = next(e for e in data["types"] if e["name"] == "AFRelationship")
    assert af_relationship["kind"] == "type_spec"
    assert af_relationship["bases"] == ["int"]
    method_names = {m["name"] for m in af_relationship["methods"]}
    assert "String" in method_names

    add_font_file = next(e for e in data["types"] if e["name"] == "AddFontFile")
    assert add_font_file["kind"] == "function"
    assert add_font_file["file"] == "render_fontrepo.go"
