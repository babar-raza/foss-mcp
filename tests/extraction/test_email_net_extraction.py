"""Offline tests against the committed email/net fixture - no network, no live clone, no engine.

``gatectl`` verifies this card with the network proxied to a dead port (TC-169 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/email_net/api_surface.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "email_net" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "email" / "net.yaml"

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
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]


def test_the_fixture_contains_real_dotnet_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Email.Foss types (CFB container + MSG/MAPI layers), not placeholders -
    # would not survive an empty or synthetic fixture.
    assert {"CfbDocument", "MapiMessage", "MsgReader"} <= names
    assert all(entry.get("class_import", "").startswith("Aspose.Email.Foss") for entry in data["types"])
    assert all(entry["file"].endswith(".cs") for entry in data["types"])


def test_every_entry_is_a_real_csharp_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    assert kinds <= {
        "class_declaration",
        "struct_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
    }
    assert kinds


def test_inherited_members_carry_the_correct_inherited_from_tag() -> None:
    # TC-307 (re-pin after TC-261 landed _flatten_inheritance()'s provenance tagging): this
    # real repository's own inheritance graph is only one level deep - live-verified directly
    # against the pinned upstream source (commit 59125b4732df0eedbc4d4c2ab978698ed4348eb7):
    # CfbNode itself declares no base (`public abstract class CfbNode`), so CfbStorage and
    # CfbStream (both real, observed `CfbNode` subclasses: `CfbStorage : CfbNode`,
    # `CfbStream : CfbNode`) are the only genuine inheritance chains here. There is no real
    # multi-level chain in this pilot's actual data (confirmed by inspecting every entry's
    # "bases": CfbNode's own "bases" is empty), so single-level rooting is the honest ceiling
    # for this repository, not an assumption - mirroring email/cpp's identical
    # CfbNode/CfbStorage/CfbStream lineage in the same product library (TC-302).
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    node = by_name["CfbNode"]
    assert node["bases"] == []
    assert node["class_import"] == "Aspose.Email.Foss.Cfb.CfbNode"

    # CfbNode's own, locally-declared members must never carry the tag.
    for member in node["methods"] + node["properties"]:
        assert "inherited_from" not in member, member["name"]

    own_members = {
        "CfbStorage": {"CfbStorage", "AddStorage", "AddStream", "Children"},
        "CfbStream": {"CfbStream", "Data"},
    }
    for child_name in ("CfbStorage", "CfbStream"):
        child = by_name[child_name]
        assert child["bases"] == ["CfbNode"]
        copied = {
            m["name"]: m["inherited_from"]
            for m in child["methods"] + child["properties"]
            if "inherited_from" in m
        }
        # Real CfbNode members observed copied into this real subclass, each correctly rooted
        # to CfbNode's own fully-qualified class_import - the real declaring ancestor, not
        # merely "some non-empty string".
        for member_name in ("Name", "Clsid", "StateBits", "CreationTime", "ModifiedTime"):
            assert copied.get(member_name) == "Aspose.Email.Foss.Cfb.CfbNode"

        # The child's own, locally-declared members must never carry the tag.
        for member in child["methods"] + child["properties"]:
            if member["name"] in own_members[child_name]:
                assert "inherited_from" not in member, (child_name, member["name"])
