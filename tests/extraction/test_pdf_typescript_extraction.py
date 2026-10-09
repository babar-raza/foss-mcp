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
actually is to the library's public API.

TC-252 (commit 86b4ea5, on ``main``) fixed ``reduce_fixture`` to rank by a CENTRALITY SCORE
- how many other types in the artifact reference a type's bare name - instead of
alphabetical order. TC-255 re-ran the real extraction against that fix, but TC-261 (the C3
fix adding explicit ``inherited_from`` provenance tagging to every copied inherited
method/property) landed on ``main`` the day AFTER TC-255's run, so that fixture carried real
inheritance (non-empty ``bases``) with ZERO ``inherited_from`` tags anywhere - the exact
defect TC-261 exists to close. TC-285 re-ran the SAME real extraction now that both TC-252
and TC-261 are genuinely on ``main``, closing that regeneration-ordering gap.

The live repository has moved on again since TC-255's run (5173 -> 5274 public types, and a
new HEAD commit) - re-running the real extraction naturally re-pins both the fixture's
``source_commit`` and ``type_count`` to whatever the live repository's HEAD genuinely was at
run time, same as every previous re-run of this pilot's extraction.

Each name/value asserted on below was read directly from this fixture after TC-285's real
live run (commit ``e0f4fe99c48e44690686df559a488bfc3623e995``), not invented. ``Page``,
``Document``, ``PdfDict``, ``PdfObject`` and ``StructElement`` are still the five
highest-centrality entries of the real 5274-type surface (order among the five shifted
slightly from TC-255's run, but the set is unchanged).

``ButtonField`` (``src/formfield.ts``) is a real class whose only base is ``Field``
(``src/formfield.ts``, itself a root with no bases) - confirmed directly from this fixture
after the live run. Its copied ``storeValue`` method now carries
``"inherited_from": "formfield.Field"``, correctly rooted to the real declaring ancestor,
while ``Field``'s own ``storeValue`` entry (the genuinely locally-declared original) carries
no ``inherited_from`` key at all - exactly the provenance TC-261 exists to add and TC-255's
pre-TC-261 run was missing entirely.
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
    # Recorded from the real live clone that produced this fixture (TC-285's re-run, now with
    # BOTH TC-252's fixed, centrality-ranked reduce_fixture AND TC-261's inherited_from
    # provenance tagging on main - a different commit from TC-255's prior run, because the
    # repository has moved on since). Re-verify by hand with
    # `git ls-remote https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-TypeScript` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "e0f4fe99c48e44690686df559a488bfc3623e995"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository now has 5274 public types (grown again since TC-255's 5173) - far
    # over the CLI's real default --max-types 300 cap, which this run used unmodified (no
    # --max-types override, per this card's own instructions).
    assert data["type_count"] == 5274
    assert data["reduced_type_count"] == 300
    assert data["truncated"] is True


def test_the_fixture_contains_real_typescript_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # The five highest-centrality types in the real 5274-type surface - confirmed directly
    # from this fixture's own content after the live run, not guessed (their relative order
    # shifted slightly from TC-255's run, so this asserts the set, not a fixed order). "Page"
    # in particular is the exact kind of central symbol TC-252's centrality-ranked
    # reduce_fixture fix exists to stop losing to an alphabetical cut.
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
    # bare name in the entire 5274-type surface, yet itself an internal (non-reachable) alias
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
    # (line 1481 after TC-285's re-run), building a raster image element.
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


def test_a_real_inherited_member_carries_the_correct_inherited_from_tag() -> None:
    """TC-261 (C3): every method/property copied into a child class by
    ``_flatten_inheritance`` must carry an explicit ``inherited_from`` key naming the real
    declaring ancestor; a genuinely locally-declared entry must never gain that key at all.

    ``ButtonField`` (src/formfield.ts) is a real class with a single real base, ``Field``
    (src/formfield.ts, itself a root with no bases) - confirmed directly from this fixture
    after TC-285's live re-run. TC-255's prior run (before TC-261 landed) produced this exact
    inheritance relationship with zero ``inherited_from`` tags anywhere in the fixture; this
    re-run is the first to carry them.

    Looked up by ``class_import`` rather than bare ``name``: the fixture also carries an
    unrelated ``structattr.Field`` interface with the same bare name, and a bare-name lookup
    silently resolves to whichever of the two happens to sort last - the same namespace
    collision ``_flatten_inheritance`` itself guards against by preferring ``class_import``.
    """
    data = _load_fixture()
    by_import = {entry["class_import"]: entry for entry in data["types"] if entry.get("class_import")}

    button_field = by_import["formfield.ButtonField"]
    assert button_field["bases"] == ["Field"]
    field = by_import["formfield.Field"]
    assert field["bases"] == []

    # `storeValue` is copied from `Field` into `ButtonField` by `_flatten_inheritance` and must
    # be rooted to `Field`'s own class_import - the real declaring ancestor, confirmed directly
    # from this fixture, not guessed.
    button_field_methods = {m["name"]: m for m in button_field["methods"]}
    copied = button_field_methods["storeValue"]
    assert copied["inherited_from"] == "formfield.Field"
    assert copied["params"][0]["type"] == "string | string[] | boolean"

    # `Field`'s own `storeValue` is the genuine, locally-declared original: it must carry no
    # `inherited_from` key at all.
    field_methods = {m["name"]: m for m in field["methods"]}
    original = field_methods["storeValue"]
    assert "inherited_from" not in original
