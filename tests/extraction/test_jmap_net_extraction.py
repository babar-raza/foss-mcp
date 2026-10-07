"""Offline tests against the committed jmap/net fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_net/api_surface.json``.

.NET-platform extraction support was already proven by the pdf/net and cells/net
onboardings; this card is a straight run-and-pin against a third, real, public .NET
repository (``aspose-email-foss/Aspose.JMAP-FOSS-for-.NET``) - no engine change at all.
"net" (not "dotnet") is the manifest's own platform value, matching every other pilot's
convention; ``run_extraction._LANGUAGE_BY_PLATFORM`` maps both spellings to "csharp".

The real repository's full public surface is only 42 types - well under the CLI's default
--max-types 300 cap - so this fixture is the complete surface, not a sub-sample:
``type_count == reduced_type_count == 42`` and ``truncated is False``.

The observed ``kind`` vocabulary is ``{"class_declaration", "interface_declaration"}`` -
this repository's public surface has no top-level ``struct`` or ``enum`` declarations,
unlike cells/net's fixture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_net" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "net.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "csharp"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-.NET` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "0d5cf17e0e9ee185eea2847f6587b918ac0eb016"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 42 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 42
    assert data["truncated"] is False


def test_the_fixture_contains_real_dotnet_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-.NET types, not placeholders - would not survive an empty
    # or synthetic fixture.
    assert {"Account", "Address", "Comparator", "CoreCapability"} <= names
    assert all(entry["file"].endswith(".cs") for entry in data["types"])


def test_every_entry_is_a_real_csharp_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    assert kinds <= {
        "class_declaration",
        "struct_declaration",
        "interface_declaration",
        "enum_declaration",
    }
    assert kinds
