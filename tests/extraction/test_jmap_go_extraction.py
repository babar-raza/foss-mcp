"""Offline tests against the committed jmap/go fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_go/api_surface.json``.

Go-platform extraction support was already proven by the pdf/go and cells/go onboardings;
this card is a straight run-and-pin against a third, real, public Go repository
(``aspose-email-foss/Aspose.JMAP-FOSS-for-Go``) - no engine change at all. Like every other
Go pilot, "go" is not special-cased in ``run_extraction._LANGUAGE_BY_PLATFORM`` and goes
through the ordinary tree-sitter path (grammar name literally ``"go"``) via
``tree_sitter_engine.api_surface``. Its ``language`` field reads literally ``"go"``, and the
observed ``kind`` vocabulary among the fixture is the tree-sitter Go grammar's own
node-type names: ``{"type_spec", "function"}`` - Go has no class/struct/enum/trait keywords
of its own, so every named type declaration surfaces as ``type_spec`` and every func
declaration (free function or method with a receiver) surfaces as ``function``.

The real repository's full public surface is only 149 types - well under the CLI's default
--max-types 300 cap - so this fixture is the complete surface, not a sub-sample:
``type_count == reduced_type_count == 149`` and ``truncated is False``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_go" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "go.yaml"

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
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Go` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "327dda39116ba02657fa49ec6dbeb69965628447"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 149 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 149
    assert data["truncated"] is False


def test_the_fixture_contains_real_go_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Go exported identifiers, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Account", "Address", "ClientOptions"} <= names
    assert all(entry["file"].endswith(".go") for entry in data["types"])


def test_every_entry_is_a_real_go_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Go grammar's own node-type vocabulary for
    # this repository, not guessed or invented. Go has no class/struct/enum/trait keywords of
    # its own: every named type declaration surfaces as "type_spec" and every func
    # declaration (free function or method) surfaces as "function".
    assert kinds == {"type_spec", "function"}
