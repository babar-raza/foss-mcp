"""Offline tests against the committed pdf/typescript fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-050 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/pdf_typescript/api_surface.json``.

TypeScript's extraction path had never been exercised against a real cloned repository
through the actual extraction entrypoint before TC-050/TC-051 landed it, and that first
fixture was then frozen with ``--max-types 150`` (half the project's actual 300 cap) and
with the OLD, pure-alphabetical ``reduce_fixture`` - so the kept 150 of 4211 real types
were whichever happened to sort first by name, with zero regard for how central a type
actually is to the library's public API. By the time TC-255 ran, the live repository had
also grown to 5173 public types.

TC-252 (commit 86b4ea5, now on ``main``) fixed ``reduce_fixture`` to rank by a CENTRALITY
SCORE - how many other types in the artifact reference a type's bare name - instead of
alphabetical order. This card re-ran the real extraction at the project's real default cap
(``--max-types 300``, i.e. no override at all) against the fixed algorithm. The result is
dramatically different: the previous fixture's entire observed ``kind`` vocabulary was
``{"function", "constant"}`` (an artifact of alphabetical ordering happening to favor
SCREAMING_SNAKE_CASE constants and short lowercase function names); the real, centrality-
ranked top 300 is dominated by classes, interfaces, and type aliases instead - exactly the
central, widely-referenced declarations the fix exists to surface. ``Page`` - the specific
symbol a live investigation confirmed missing even from pdf/go's correctly-sized-but-
unranked fixture - is now present, as the fifth-most-central type in the entire
repository.

Each name/value asserted on below was read directly from this fixture after the real live
run (commit ``0dc807e313400ad8582441fc13171182261de2b4``), not invented. ``Page``,
``Document``, ``PdfDict``, ``PdfObject`` and ``StructElement`` are literally the five
highest-centrality entries (in that order) of the real 5173-type surface.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_typescript" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "pdf" / "typescript.yaml"

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
    # Recorded from the real live clone that produced this fixture (TC-255's re-run against
    # TC-252's fixed, centrality-ranked reduce_fixture - this is a different commit from the
    # original TC-050 run, because the repository has moved on since). Re-verify by hand with
    # `git ls-remote https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-TypeScript` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "0dc807e313400ad8582441fc13171182261de2b4"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository now has 5173 public types (grown since TC-050's original 4211) -
    # far over the CLI's real default --max-types 300 cap, which this run used unmodified
    # (no --max-types override, per TC-255's own instructions).
    assert data["type_count"] == 5173
    assert data["reduced_type_count"] == 300
    assert data["truncated"] is True


def test_the_fixture_contains_real_typescript_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # The five highest-centrality types in the real 5173-type surface, in descending order of
    # how many other types reference each one's bare name - confirmed directly from this
    # fixture's own content after the live run, not guessed. "Page" in particular is the exact
    # kind of central symbol TC-252's centrality-ranked reduce_fixture fix exists to stop
    # losing to an alphabetical cut.
    assert {"Page", "Document", "PdfDict", "PdfObject", "StructElement"} <= names
    assert all(entry["file"].endswith(".ts") for entry in data["types"])


def test_every_entry_is_a_real_typescript_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live run over this repository's real, centrality-ranked top
    # 300 public surface, not guessed or invented. Unlike the old alphabetically-cut fixture
    # (whose kept 150 happened to be entirely functions/constants), the centrality-ranked top
    # 300 is dominated by the declarations most referenced across the rest of the library's
    # public API: classes, interfaces, and type aliases, alongside some functions/constants.
    assert kinds == {
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "function",
        "constant",
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


def test_a_real_class_and_a_real_type_alias_are_present_with_real_fields() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # `Page` is the single most central type in the real repository: a real class in
    # src/page.ts - confirmed in this fixture to carry real methods (e.g. `RemoveAnnotation`,
    # `AddTextNote`) and real properties, and to be genuinely reachable (re-exported as part of
    # the package's actual public surface), unlike the old alphabetically-cut fixture, which
    # never kept it at all.
    page = by_name["Page"]
    assert page["kind"] == "class_declaration"
    assert page["file"] == "src/page.ts"
    assert page["reachable"] is True
    assert len(page["methods"]) > 0
    assert len(page["properties"]) > 0
    method_names = {m["name"] for m in page["methods"]}
    assert {"constructor", "RemoveAnnotation", "AddTextNote"} <= method_names

    # `PdfDict` is a real top-level type alias in src/types.ts - the single most-referenced
    # bare name in the entire 5173-type surface, yet itself an internal (non-reachable) alias
    # rather than something re-exported from the package's own index - exactly the kind of
    # widely-depended-on-but-unexported symbol a pure alphabetical cut has no way to favor.
    pdf_dict = by_name["PdfDict"]
    assert pdf_dict["kind"] == "type_alias_declaration"
    assert pdf_dict["file"] == "src/types.ts"
    assert pdf_dict["reachable"] is False


def test_a_real_function_and_a_real_constant_are_present_with_real_fields() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # `image` is a real, reachable top-level helper function in src/flow.ts - confirmed in this
    # fixture as `function image(data: Uint8Array, options: FlowImageOptions): FlowElement[]`
    # (line 1308), building a raster image flow element.
    func = by_name["image"]
    assert func["kind"] == "function"
    assert func["file"] == "src/flow.ts"
    assert func["return_type"] == "FlowElement[]"
    param_names = [p["name"] for p in func["params"]]
    assert param_names == ["data", "options"]
    assert func["reachable"] is True

    # `M` is a real, internal (non-reachable) top-level exported constant in src/wmlns.ts -
    # confirmed in this fixture to carry `type == "string"`, a real XML-namespace URI value,
    # and never re-exported from the package's own index.
    const = by_name["M"]
    assert const["kind"] == "constant"
    assert const["file"] == "src/wmlns.ts"
    assert const["type"] == "string"
    assert const["reachable"] is False
