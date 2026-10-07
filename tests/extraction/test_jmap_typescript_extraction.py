"""Offline tests against the committed jmap/typescript fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_typescript/api_surface.json``.

TypeScript-platform extraction support was already proven by the pdf/typescript and
cells/typescript onboardings; this card is a straight run-and-pin against a third, real,
public TypeScript repository (``aspose-email-foss/Aspose.JMAP-FOSS-for-TypeScript``) - no
engine change at all. The real checkout's source is genuine ``.ts`` (not plain JavaScript):
every ``file`` value in this fixture ends in ``.ts``.

The real repository's full public surface is only 60 types - well under the CLI's default
``--max-types 300`` cap - so this fixture is the complete surface, not a sub-sample:
``type_count == reduced_type_count == 60`` and ``truncated is False``.

The observed ``kind`` vocabulary is ``{"class_declaration", "function",
"interface_declaration", "type_alias_declaration"}`` - this repository's public surface has
no top-level ``enum`` declarations, unlike cells/typescript's fixture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_typescript" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "typescript.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "typescript"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-TypeScript` if
    # this ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "d8fdf9e8f3367424c3a900271eb3bd0b99c6195e"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 60 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 60
    assert data["truncated"] is False


def test_the_fixture_contains_real_typescript_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-TypeScript exported declarations, not placeholders - would
    # not survive an empty or synthetic fixture.
    assert {"Account", "Email", "DeliveryStatus", "CoreCapability"} <= names
    assert all(entry["file"].endswith(".ts") for entry in data["types"])


def test_every_entry_is_a_real_typescript_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live run over this repository's real, complete public
    # surface, not guessed or invented.
    assert kinds == {
        "class_declaration",
        "function",
        "interface_declaration",
        "type_alias_declaration",
    }
