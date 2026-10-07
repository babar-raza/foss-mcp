"""Offline tests against the committed cells/java fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/cells_java/api_surface.json``.

Java-platform extraction support was already proven by the pdf/java onboarding (see
``tests/extraction/test_pdf_java_extraction.py``); this card is a straight run-and-pin
against a second, real, public Java repository
(``aspose-cells-foss/Aspose.Cells-FOSS-for-Java``) - no engine change at all.

The real repository's full public surface is only 183 types - well under the CLI's
default ``--max-types 300`` cap - so this fixture is the complete surface, not a
sub-sample: ``type_count == reduced_type_count == 183`` and ``truncated is False``.

The observed ``kind`` vocabulary is ``{"class_declaration", "enum_declaration",
"interface_declaration"}`` - the same three of Java's top-level type-declaration forms
pdf/java's own fixture exercises.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_java" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "java.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-Java` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "c65329e7257b1311abb9686d7e4957a8cc002955"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 183 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 183
    assert data["truncated"] is False


def test_the_fixture_contains_real_java_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Cells-FOSS-for-Java exported types, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"AutoFilter", "AutoFilterColorFilter", "AlignmentValue"} <= names
    # Observed directly in the live run: this Java port's package is `org.aspose.cells_foss`,
    # not `Aspose.Cells` (the .NET namespace) - not reconstructed from a pattern.
    assert all(entry.get("class_import", "").startswith("org.aspose.cells_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".java") for entry in data["types"])


def test_every_entry_is_a_real_java_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    assert kinds <= {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    }
    assert kinds
