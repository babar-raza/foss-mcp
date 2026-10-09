"""Offline tests against the committed jmap/rust fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-032 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_rust/api_surface.json``.

"rust" is not special-cased in ``run_extraction._LANGUAGE_BY_PLATFORM`` and goes through the
ordinary tree-sitter path (grammar name literally ``"rust"``). Its ``language`` field reads
literally ``"rust"``, and the observed ``kind`` vocabulary among the fixture is the tree-sitter
Rust grammar's own node-type names: ``{"struct_item", "enum_item", "trait_item", "function"}``.

TC-312 re-ran this extraction live (network access, real default --max-types 300, no
truncation) now that both TC-252 (centrality-ranked ``reduce_fixture()``) and TC-261
(``inherited_from`` provenance tagging in ``_flatten_inheritance()``) are on main. The
commit pin (``d7286100735e6d518837fde8639e8de694a08ef6``) and repository
(``aspose-email-foss/Aspose.JMAP-FOSS-for-Rust``) came back unchanged and match
``infra/helm/foss-mcp/values.yaml`` and ``docker-compose.yml`` exactly - no commit-pin
divergence here. Every one of the 45 types' own content (methods/properties/bases/doc) is
byte-identical to the pre-TC-312 fixture; only the list order changed (TC-252's centrality
ranking, inert for ranking purposes since ``truncated is False`` but still reorders the list).

TC-312 also hand-verified, against a fresh clone of the pinned commit, why this real
re-run still carries zero ``inherited_from`` tags. ``tree_helpers._extract_bases()`` for
Rust (see its own docstring) populates a struct's/enum's ``"bases"`` from two sources that
are NOT real inheritance: (a) trait names pulled out of a ``#[derive(...)]`` attribute
(``Debug``, ``Clone``, ``PartialEq``, ``Serialize``, ``Deserialize``, etc. - these never
match any extracted type's own name, so ``_flatten_inheritance()``'s ``by_name.get(base_name)``
is always ``None`` for them and nothing is ever copied) and (b) real ``impl Trait for Type``
relationships added by ``_associate_rust_impl_methods``. The ONLY member of case (b) in this
entire repository's public surface is ``UreqTransport`` implementing the ``Transport`` trait
(``src/transport.rs``, hand-read at the pinned commit): ``Transport`` declares exactly three
methods (``send``, ``box_clone``, ``as_any``), all bare signatures with no default body, and
``UreqTransport`` is the only in-crate implementor (the four other ``impl Transport for
FakeTransport`` blocks hand-found in ``tests/*.rs`` are test doubles outside ``src/``, never
part of the extracted public surface) and overrides all three with identical signatures.
``_flatten_inheritance()``'s own dedup-by-``(name, param-types)`` key therefore already finds
every one of ``Transport``'s three methods present in ``UreqTransport`` before it ever
considers copying, so correctly nothing is copied and nothing is tagged. This is accurate
extraction of this repository's real shape, not evidence of stale tagging: a trait WITH a
default method that some implementor leaves un-overridden would exercise the copy-and-tag
path, but no such trait exists anywhere in this crate's public surface.
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


def test_the_only_real_trait_implementation_is_rooted_correctly() -> None:
    """``UreqTransport`` is the only real ``impl Trait for Type`` relationship in this
    repository's public surface (hand-verified against a fresh clone of the pinned commit:
    ``src/transport.rs`` is the only file with a non-test ``impl Transport for ...`` block).
    Every other type's non-empty "bases" entries are ``#[derive(...)]`` trait names, never a
    real extracted type - confirmed by checking that no base name other than "Transport"
    matches any type's own "name" anywhere in this fixture.
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    names = set(by_name)
    assert "Transport" in by_name["UreqTransport"]["bases"]
    for entry in data["types"]:
        if entry["name"] == "UreqTransport":
            continue
        assert not (set(entry["bases"]) & names), (entry["name"], entry["bases"])


def test_no_inherited_from_tag_exists_because_the_trait_has_no_default_methods() -> None:
    """``Transport`` (``src/transport.rs``, hand-read at the pinned commit
    ``d7286100735e6d518837fde8639e8de694a08ef6``) declares exactly three methods - ``send``,
    ``box_clone``, ``as_any`` - every one a bare signature with no default body. Its only
    real in-crate implementor, ``UreqTransport``, overrides all three with identical
    (name, param-types) signatures. TC-261's ``_flatten_inheritance()`` copies a parent
    method down only when the child does not already declare one with the same
    ``(name, param-types)`` key; here the child already declares all three, so correctly
    nothing is copied and nothing anywhere in this real fixture carries "inherited_from".
    This is accurate extraction of this repository's real shape, not evidence that TC-261's
    tagging is stale: a trait with an un-overridden default method would exercise the
    copy-and-tag path (see e.g. pdf/cpp's own fixture/test for that case), but no such trait
    exists in this crate's public surface.
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    transport = by_name["Transport"]
    ureq_transport = by_name["UreqTransport"]
    transport_method_keys = {
        (m["name"], tuple(p.get("type", "") for p in m.get("params", []))) for m in transport["methods"]
    }
    ureq_method_keys = {
        (m["name"], tuple(p.get("type", "") for p in m.get("params", []))) for m in ureq_transport["methods"]
    }
    assert transport_method_keys <= ureq_method_keys
    for entry in data["types"]:
        for member in entry["methods"] + entry["properties"]:
            assert member.get("inherited_from") is None
