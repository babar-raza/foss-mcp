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

TC-313 re-ran this same extraction live (same pinned commit, ``d8fdf9e8f3367424c3a900271eb3bd
0b99c6195e``, confirming the pin was never divergent) now that TC-252 (centrality-ranked
``reduce_fixture()``) and TC-261 (``inherited_from`` provenance tagging in
``_flatten_inheritance()``) are both on main. Unlike cells_rust and jmap_go (both verified
genuinely flat in an earlier wave), this repository's real public surface DOES have a
non-trivial local base class: ``JmapError`` (``src/models/CommonTypes.ts``) declares a real
``constructor``, and both ``JmapNetworkError`` and ``JmapProtocolError`` extend it without
overriding that constructor's inherited copy - the live run tags that copied member
``"inherited_from": "models.CommonTypes.JmapError"`` on both subclasses, correctly rooted to
the true declaring ancestor's fully-qualified ``class_import``. There is no real 3-level
local chain to verify in this repository: ``JmapError`` itself extends the built-in global
``Error``, which is not a locally declared type, so the inheritance graph among this
repository's own declared classes is only ever one level deep. ``FetchTransport`` extends
``JmapTransport`` but declares its own concrete ``send`` override, so that member correctly
carries no ``inherited_from`` tag - only a member the subclass does NOT itself declare is
copied down with the tag.
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


def _find_type(data: dict, name: str) -> dict:
    for entry in data["types"]:
        if entry["name"] == name:
            return entry
    raise AssertionError(f"type {name!r} not found in fixture")


def test_a_real_inherited_member_carries_inherited_from_correctly_rooted() -> None:
    # Live-verified under TC-313: this repository's real public surface is NOT flat like
    # cells_rust/jmap_go turned out to be - JmapError (src/models/CommonTypes.ts) is a real,
    # non-trivial local base class, and JmapNetworkError/JmapProtocolError both extend it
    # without overriding its constructor. TC-261's _flatten_inheritance() copies that
    # constructor down and tags it with the parent's fully-qualified class_import, rooted to
    # the true declaring ancestor.
    data = _load_fixture()
    for child_name in ("JmapNetworkError", "JmapProtocolError"):
        child = _find_type(data, child_name)
        assert child["bases"] == ["JmapError"]
        inherited = [
            m
            for m in child["methods"]
            if m["name"] == "constructor" and m.get("inherited_from")
        ]
        assert len(inherited) == 1, child_name
        assert inherited[0]["inherited_from"] == "models.CommonTypes.JmapError"
        # The child's own constructor (with its own, different parameter list) is kept
        # alongside the inherited one rather than being replaced by it.
        own = [
            m
            for m in child["methods"]
            if m["name"] == "constructor" and not m.get("inherited_from")
        ]
        assert len(own) == 1, child_name

    parent = _find_type(data, "JmapError")
    assert parent["class_import"] == "models.CommonTypes.JmapError"
    # JmapError's own base is the built-in global `Error`, not a locally declared type, so
    # there is no real 3-level local chain in this repository to verify a deeper rooting
    # against - the inheritance graph among this repository's own declared classes is only
    # ever one level deep.
    assert parent["bases"] == ["Error"]


def test_an_overridden_member_carries_no_inherited_from_tag() -> None:
    # FetchTransport extends JmapTransport but declares its own concrete `send`, so
    # _flatten_inheritance() must not copy JmapTransport's `send` down onto it - only members
    # the subclass does not itself declare get tagged and copied.
    data = _load_fixture()
    child = _find_type(data, "FetchTransport")
    assert child["bases"] == ["JmapTransport"]
    sends = [m for m in child["methods"] if m["name"] == "send"]
    assert len(sends) == 1
    assert not sends[0].get("inherited_from")
