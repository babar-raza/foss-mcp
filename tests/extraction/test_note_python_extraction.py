"""Offline tests against the committed note/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/note_python/api_surface.json``.

Like slides/python, this fixture was produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"``. Unlike slides/python's reduced 300-entry truncation, the full note/python
surface (75 types) fit under the fixture-size cap untruncated, so its observed ``kind``
vocabulary is the full ``{"class", "enum", "function"}`` - a free top-level helper
(``write_pdf``) survived here where slides/python's alphabetical truncation dropped every
function.

Regenerated 2026-10-10 (G2/TC-322): this fixture was originally produced by TC-154, the
original onboarding wave, well before TC-265 (real bases/return_type), TC-274/TC-293/TC-294
(recovery of structured data and real methods for an underscore/re-export-origin duplicate
symbol) and TC-281 (``api_surface._flatten_inheritance`` reached for Python-sourced pilots at
all) ever existed - every type carried ``bases: []`` and empty ``properties`` purely because
the pipeline that would have populated them did not exist yet, not because the real upstream
lacked inheritance. A live re-run under the full pipeline (real network, real clone of
``aspose-note-foss/Aspose.Note-FOSS-for-Python``) landed on commit
``0014dbeef9511af360b936d95d47947d6b1d8a4f`` - diverged from the stale pin recorded in
``infra/helm/foss-mcp/values.yaml``/``docker-compose.yml`` (``e459eb50d750f70181cfb23b28a9ab73cb464a8d``),
the same fleet-wide package/commit-divergence shape already tracked as a separate P0 in
``docs/DECISION_LOG.md`` (2026-10-09 entries) and explicitly out of this card's two
``write_paths``. 47 of the 75 kept types now carry a real, non-empty ``bases`` list, and 101
real inherited members across the fixture now carry an ``inherited_from`` tag.

This repository's real source (verified directly, live, against the fresh clone at the exact
commit above: ``src/aspose/note/model.py``) declares a genuine three-level chain -
``class Node:`` (line 134, no base) <- ``class CompositeNode(Node):`` (line 169) <-
``class Document(CompositeNode):`` (line 1257) - and, like html/python's ``HTMLElement`` and
words/python's ``Paragraph`` before it, each real class name is re-exported a second time from
the public ``aspose/note/__init__.py`` (e.g. ``from .model import Document``), so this fixture
carries two entries per re-exported name: the real, fully-structured one at
``aspose.note.model.*`` and a second, bases-only one at the public ``aspose.note.*`` import
site whose own methods are not independently populated. The tests below deliberately select the
``aspose.note.model.*`` entries, mirroring how ``test_html_python_extraction.py`` and
``test_words_python_extraction.py`` already handle the identical shape for their own pilots.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "note_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "note" / "python.yaml"

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
    # Real Aspose.Note-FOSS-for-Python classes/enums, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Document", "AttachedFile", "FileFormat"} <= names
    assert all(entry["class_import"].startswith("aspose.note") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Unlike slides/python,
    # the full surface fit untruncated here, so a top-level function survives too.
    assert kinds == {"class", "enum", "function"}


def test_the_real_node_hierarchy_carries_real_bases() -> None:
    # TC-265's real ``bases`` recovery, confirmed live against this pilot for the first time:
    # this repository's own real node hierarchy (verified directly against
    # ``src/aspose/note/model.py`` at the fixture's pinned commit) is genuinely three levels
    # deep - ``Node`` (no base) <- ``CompositeNode(Node)`` <- ``Document(CompositeNode)`` -
    # not a flat, inheritance-free shape the original TC-154 fixture implied.
    data = _load_fixture()
    by_import = {entry["class_import"]: entry for entry in data["types"]}
    assert by_import["aspose.note.model.Node"]["bases"] == []
    assert by_import["aspose.note.model.CompositeNode"]["bases"] == ["Node"]
    assert by_import["aspose.note.model.Document"]["bases"] == ["CompositeNode"]


def test_composite_node_inherits_real_node_methods_with_inherited_from_tag() -> None:
    # CompositeNode's own locally-declared child-management methods must never carry an
    # ``inherited_from`` tag, while the three methods it never redeclares (``Accept``,
    # ``Document``, ``ParentNode``) are real members of ``Node`` - copied down by
    # ``api_surface._flatten_inheritance`` (reached for a Python-sourced pilot at all only
    # since TC-281) and tagged with their true declaring class.
    data = _load_fixture()
    composite_node = next(
        entry
        for entry in data["types"]
        if entry["name"] == "CompositeNode" and entry["class_import"] == "aspose.note.model.CompositeNode"
    )
    assert composite_node["bases"] == ["Node"]
    own_methods = {m["name"] for m in composite_node["methods"] if not m.get("inherited_from")}
    assert {
        "AppendChildFirst",
        "AppendChildLast",
        "FirstChild",
        "GetChildNodes",
        "GetEnumerator",
        "InsertChild",
        "LastChild",
        "RemoveChild",
    } <= own_methods
    inherited = {m["name"]: m["inherited_from"] for m in composite_node["methods"] if m.get("inherited_from")}
    assert inherited == {
        "Accept": "aspose.note.model.Node",
        "Document": "aspose.note.model.Node",
        "ParentNode": "aspose.note.model.Node",
    }


def test_document_inherits_real_multi_level_provenance_from_both_ancestors() -> None:
    # The real multi-level-chain proof this card exists to produce: Document's own real
    # methods (declared directly on Document itself) carry no ``inherited_from`` tag; its 8
    # methods real-declared on its IMMEDIATE parent CompositeNode are tagged
    # "aspose.note.model.CompositeNode"; and its 3 methods real-declared on its GRANDPARENT
    # Node are tagged "aspose.note.model.Node" directly - not mis-rooted to the intermediate
    # CompositeNode, which never redeclares them either. Confirmed live against the real
    # upstream source: ``Node.Accept``/``Node.ParentNode`` are declared inside ``class Node``
    # (model.py lines 134-168) and ``CompositeNode.AppendChildFirst`` is declared inside
    # ``class CompositeNode`` (model.py line 190) - Document (line 1257) redeclares neither.
    data = _load_fixture()
    document = next(
        entry
        for entry in data["types"]
        if entry["name"] == "Document" and entry["class_import"] == "aspose.note.model.Document"
    )
    assert document["bases"] == ["CompositeNode"]
    own_methods = {m["name"] for m in document["methods"] if not m.get("inherited_from")}
    assert own_methods == {"DetectLayoutChanges", "FileFormat", "GetPageHistory", "Save"}
    inherited = {m["name"]: m["inherited_from"] for m in document["methods"] if m.get("inherited_from")}
    assert inherited == {
        "AppendChildFirst": "aspose.note.model.CompositeNode",
        "AppendChildLast": "aspose.note.model.CompositeNode",
        "FirstChild": "aspose.note.model.CompositeNode",
        "GetChildNodes": "aspose.note.model.CompositeNode",
        "GetEnumerator": "aspose.note.model.CompositeNode",
        "InsertChild": "aspose.note.model.CompositeNode",
        "LastChild": "aspose.note.model.CompositeNode",
        "RemoveChild": "aspose.note.model.CompositeNode",
        "Accept": "aspose.note.model.Node",
        "Document": "aspose.note.model.Node",
        "ParentNode": "aspose.note.model.Node",
    }
