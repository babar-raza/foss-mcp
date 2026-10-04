"""Offline tests against the committed jmap/rust fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-032 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_rust/api_surface.json``.

"rust" is not special-cased in ``run_extraction._LANGUAGE_BY_PLATFORM`` and goes through the
ordinary tree-sitter path (grammar name literally ``"rust"``). Its ``language`` field reads
literally ``"rust"``, and the observed ``kind`` vocabulary among the fixture is the tree-sitter
Rust grammar's own node-type names: ``{"struct_item", "enum_item", "trait_item", "function"}``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_rust" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "rust.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "rust"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Rust` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "d7286100735e6d518837fde8639e8de694a08ef6"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]
    # The real repository has exactly 45 public types - well under the CLI's default
    # --max-types cap, so this fixture is the full surface, not a truncated subset.
    assert data["type_count"] == 45
    assert data["truncated"] is False


def test_the_fixture_contains_real_rust_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Rust structs/enums/traits, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"JmapClient", "ClientOptions", "JmapError", "Transport", "Mailbox", "Email"} <= names
    assert all(entry["file"].endswith(".rs") for entry in data["types"])


def test_every_entry_is_a_real_rust_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Rust grammar's own node-type vocabulary for
    # this repository, not guessed or invented.
    assert kinds == {"struct_item", "enum_item", "trait_item", "function"}


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    # Fields present on every entry regardless of kind.
    for entry in data["types"]:
        for key in ("name", "kind", "file", "line", "doc", "methods", "properties", "bases", "reachable"):
            assert key in entry, (entry.get("name"), key)


def test_the_trait_and_enum_and_free_functions_observed_are_real() -> None:
    data = _load_fixture()
    trait_items = {e["name"] for e in data["types"] if e["kind"] == "trait_item"}
    enum_items = {e["name"] for e in data["types"] if e["kind"] == "enum_item"}
    functions = {e["name"] for e in data["types"] if e["kind"] == "function"}
    # Observed directly in the live run.
    assert trait_items == {"Transport"}
    assert enum_items == {"JmapError"}
    assert {"list_mailboxes", "fetch_message", "move_message"} <= functions
