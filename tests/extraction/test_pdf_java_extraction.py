"""Offline tests against the committed pdf/java fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-044 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/pdf_java/api_surface.json``.

Java's extraction path had never been exercised against a real cloned repository through
the actual extraction entrypoint before this card - unlike Go/Rust, which had at least
shallow synthetic coverage. It went through cleanly: ``java`` is an ordinary tree-sitter
platform (not special-cased like ``python``), routed through
``tree_sitter_engine.api_surface`` via the ``"java"`` grammar name.

The observed ``kind`` vocabulary among the reduced fixture is the tree-sitter Java
grammar's own node-type names: ``{"class_declaration", "enum_declaration",
"interface_declaration"}`` - Java's three top-level type-declaration forms (this
repository's committed public surface happens to contain no ``record`` or
``annotation_type_declaration`` entries in the reduced subset).

The real repository has 1158 public types - far over the CLI's default ``--max-types
300`` cap (which itself produces a fixture over the ~2MB budget: 2,758,641 bytes for
300 types) - so this fixture was generated with an explicit smaller
``--max-types 150``, landing at 1,110,510 bytes, well under budget. ``truncated`` is
``True`` and ``reduced_type_count`` is 150, not the full 1158.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_java" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "pdf" / "java.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "java"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-Java` if this ever
    # needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "db2d3f0622f035825419c6d46727022064f39f15"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has 1158 public types - far over the CLI's default --max-types 300
    # cap, and even that cap alone produces a fixture over the ~2MB budget (2,758,641 bytes for
    # 300 types) - so this fixture was generated with an explicit smaller --max-types 150.
    assert data["type_count"] == 1158
    assert data["reduced_type_count"] == 150
    assert data["truncated"] is True


def test_the_fixture_contains_real_java_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.PDF-FOSS-for-Java exported types, not placeholders - would not survive an
    # empty or synthetic fixture.
    assert {"Document", "Page", "PageCollection", "IAppointment", "HtmlSaveOptions"} <= names
    assert all(entry["file"].endswith(".java") for entry in data["types"])


def test_every_entry_is_a_real_java_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Java grammar's own node-type vocabulary for
    # this repository, not guessed or invented: the reduced subset's public surface only
    # exercises these three of Java's top-level type-declaration forms.
    assert kinds == {"class_declaration", "enum_declaration", "interface_declaration"}


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    for entry in data["types"]:
        for key in (
            "name",
            "kind",
            "file",
            "line",
            "doc",
            "methods",
            "properties",
            "bases",
            "reachable",
            "visibility",
            "class_import",
            "canonical_namespace",
        ):
            assert key in entry, (entry.get("name"), key)


def test_a_real_class_a_real_interface_and_a_real_enum_are_present() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # Observed directly in the live run: Document is a real class that implements
    # java.io.Closeable and has a real getPages() accessor returning PageCollection.
    document = by_name["Document"]
    assert document["kind"] == "class_declaration"
    assert document["bases"] == ["Closeable"]
    methods = {m["name"]: m for m in document["methods"]}
    assert "getPages" in methods
    assert methods["getPages"]["return_type"] == "PageCollection"
    assert methods["getPages"]["is_constructor"] is False

    # IAppointment is a real marker interface with no methods of its own.
    interface = by_name["IAppointment"]
    assert interface["kind"] == "interface_declaration"
    assert interface["methods"] == []

    # AntialiasingProcessingType is a real enum with two real members.
    enum = by_name["AntialiasingProcessingType"]
    assert enum["kind"] == "enum_declaration"
    enum_member_names = {m["name"] for m in enum["enum_members"]}
    assert {"NoAdditionalProcessing", "TryCorrectResultHtml"} <= enum_member_names
