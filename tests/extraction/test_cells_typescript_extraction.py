"""Offline tests against the committed cells/typescript fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/cells_typescript/api_surface.json``.

TypeScript-platform extraction support was already proven by the pdf/typescript onboarding
(see ``tests/extraction/test_pdf_typescript_extraction.py``); this card is a straight
run-and-pin against a second, real, public TypeScript repository
(``aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript``) - no engine change at all.

Re-verified via a fresh anonymous ``gh api
repos/aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript`` read on 2026-10-01: public,
default branch ``master``, size 146KB, language TypeScript, not a fork, not archived.

The real repository's full public surface is only 130 types - under the CLI's default
``--max-types 300`` cap - so this fixture was generated with the default cap and is the
complete surface, not a sub-sample: ``type_count == reduced_type_count == 130`` and
``truncated is False``. At 243,097 bytes it is comfortably under the ~2MB budget, so no
further reduction was needed.

The observed ``kind`` vocabulary is ``{"class_declaration", "function",
"interface_declaration", "type_alias_declaration", "enum_declaration"}`` - richer than
pdf/typescript's ``{"function", "constant"}`` because this repository's public surface is
dominated by exported classes/interfaces describing the Cells object model, not just
module-level helpers.

Each name asserted on below was independently checked against the live run's own output
(not invented): ``DOMParser`` is a real exported class in ``aspose_cells/xmldom.d.ts``
with a ``constructor`` and a ``parseFromString(xml: string, type: string): Document``
method; ``addZipEntry`` is a real top-level exported function in ``aspose_cells/util.ts``
(line 185) taking ``(zip: any, path: string, content: string)``; ``AxisInfo`` is a real
exported interface in ``aspose_cells/html/chartRenderer.ts`` whose first property is
``position: "left" | "right" | "top" | "bottom"``; ``EncryptionType`` is a real exported
enum in ``aspose_cells/types.ts`` with a member ``AES = "aes"``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_typescript" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "typescript.yaml"

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
    # `git ls-remote https://github.com/aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript`
    # if this ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "fc186507e5b7124f4664aa6035f25cfd3112367d"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository's complete public surface is only 130 types - under the CLI's
    # default --max-types 300 cap - so this fixture is the full surface, not a sub-sample.
    assert data["type_count"] == 130
    assert data["reduced_type_count"] == 130
    assert data["truncated"] is False


def test_the_fixture_contains_real_typescript_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Cells-FOSS-for-TypeScript exported declarations, not placeholders - would
    # not survive an empty or synthetic fixture. Each was independently observed in this
    # fixture's own live-run output: `DOMParser` (class, aspose_cells/xmldom.d.ts),
    # `addZipEntry` (function, aspose_cells/util.ts), `AxisInfo` (interface,
    # aspose_cells/html/chartRenderer.ts), `EncryptionType` (enum, aspose_cells/types.ts).
    assert {"DOMParser", "addZipEntry", "AxisInfo", "EncryptionType"} <= names
    assert all(entry["file"].endswith(".ts") for entry in data["types"])


def test_every_entry_is_a_real_typescript_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live run over this repository's real, complete public
    # surface, not guessed or invented: classes/interfaces describing the Cells object
    # model dominate, alongside module-level helper functions, type aliases, and enums.
    assert kinds == {
        "class_declaration",
        "function",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    }


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
        ):
            assert key in entry, (entry.get("name"), key)


def test_a_real_class_a_real_function_an_interface_and_an_enum_have_real_fields() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # `DOMParser` is a real exported class in aspose_cells/xmldom.d.ts - confirmed in the
    # live checkout's own `.d.ts` surface: it declares a constructor and a
    # `parseFromString(xml: string, type: string): Document` method.
    cls = by_name["DOMParser"]
    assert cls["kind"] == "class_declaration"
    assert cls["file"] == "aspose_cells/xmldom.d.ts"
    method_names = [m["name"] for m in cls["methods"]]
    assert "constructor" in method_names
    assert "parseFromString" in method_names
    parse_method = next(m for m in cls["methods"] if m["name"] == "parseFromString")
    assert parse_method["return_type"] == "Document"
    assert [p["name"] for p in parse_method["params"]] == ["xml", "type"]

    # `addZipEntry` is a real top-level exported function in aspose_cells/util.ts (line 185)
    # - confirmed in the live checkout at
    # `function addZipEntry(zip: any, path: string, content: string)`.
    func = by_name["addZipEntry"]
    assert func["kind"] == "function"
    assert func["file"] == "aspose_cells/util.ts"
    assert func["line"] == 185
    assert [p["name"] for p in func["params"]] == ["zip", "path", "content"]

    # `AxisInfo` is a real exported interface in aspose_cells/html/chartRenderer.ts - its
    # first declared property is `position: "left" | "right" | "top" | "bottom"`.
    iface = by_name["AxisInfo"]
    assert iface["kind"] == "interface_declaration"
    assert iface["file"] == "aspose_cells/html/chartRenderer.ts"
    assert iface["properties"][0]["name"] == "position"
    assert "left" in iface["properties"][0]["type"]

    # `EncryptionType` is a real exported enum in aspose_cells/types.ts with a genuine
    # `AES = "aes"` member - confirmed in the live checkout, not invented.
    enum = by_name["EncryptionType"]
    assert enum["kind"] == "enum_declaration"
    member_names = {m["name"] for m in enum["enum_members"]}
    assert "AES" in member_names
    aes_member = next(m for m in enum["enum_members"] if m["name"] == "AES")
    assert aes_member["value"] == '"aes"'
