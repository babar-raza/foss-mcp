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

TC-316 then found a SEPARATE engine defect, cross-cutting from its own pilot (cells/typescript):
``tree_helpers.py``'s ``_extract_bases()`` recognized TypeScript's class-level
``class_heritage``/``extends_clause``/``implements_clause`` nodes but not ``extends_type_clause``
- the real node type for an INTERFACE extending another interface
(``interface_declaration -> type_identifier, extends_type_clause[extends, type_identifier],
interface_body``). TC-316's own worker live-confirmed this pilot has 57 real
``interface X extends Y`` relationships, 56 of which extracted as ``bases: []`` before the fix.
TC-317 re-runs the SAME real extraction now that TC-316's fix is genuinely on ``main``, closing
that gap for this pilot.

The live repository has moved on again since TC-285's run (5274 -> 5342 public types, and a
new HEAD commit) - re-running the real extraction naturally re-pins both the fixture's
``source_commit`` and ``type_count`` to whatever the live repository's HEAD genuinely was at
run time, same as every previous re-run of this pilot's extraction. (Re-run twice, minutes
apart, during TC-317: both runs independently landed on the identical commit and type count,
confirming the live repository was not mid-push.)

Each name/value asserted on below was read directly from this fixture after TC-317's real
live run (commit ``c672b3918318ccd0c13bc9dc619620cac2674c9e``), not invented. ``Page``,
``Document``, ``PdfDict``, ``PdfObject`` and ``StructElement`` are still the five
highest-centrality entries of the real 5342-type surface (order among the five shifted
slightly from TC-285's run, but the set is unchanged).

``ButtonField`` (``src/formfield.ts``) is a real class whose only base is ``Field``
(``src/formfield.ts``, itself a root with no bases) - confirmed directly from this fixture
after the live run, unchanged from TC-285's run. Its copied ``storeValue`` method still carries
``"inherited_from": "formfield.Field"``, correctly rooted to the real declaring ancestor,
while ``Field``'s own ``storeValue`` entry (the genuinely locally-declared original) carries
no ``inherited_from`` key at all - the class-level provenance TC-261 added, confirmed still
intact after TC-317's re-run.

TC-317's own target defect (TC-316's ``extends_type_clause`` fix) is proven by a real,
genuine 4-level INTERFACE-extends-INTERFACE chain now present in this fixture:
``ComboBoxInit -> ChoiceInit -> FieldInit -> FieldStyle -> WidgetStyle`` (all real interfaces
in ``src/formcreate.ts``/``src/fieldstyle.ts``). None of the three example relationships
TC-316's own worker cited (``FileAttachmentOptions``/``PopupOptions``/``LineMarker``) survived
this run's top-300 centrality cut - the live repository grew again and the cut shifted - but
this chain did, and demonstrates the identical fix: before TC-316 landed, every one of these
four real relationships extracted as ``bases: []``.

TC-550 re-extracted this fixture at the CHART PIN ``155bfc7a33f0ba23fb4252b6ba201828b02a5b9d`` (one
consistent commit wins over freshness: the furnished page, the compile-verified examples and the sidecar
are all at that commit) with the cap raised to the total, so it is the COMPLETE 4393-entry surface
(``truncated`` false). The facts above that named c672b39-only symbols (``storeValue``, the constant
``M``) now read their equivalents at the pin; the five central types and the four-level interface chain
are unchanged.
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
    # Recorded from the real live clone that produced this fixture (TC-317's re-run, now with
    # TC-316's extends_type_clause fix also on main - a different commit from TC-285's prior
    # run, because the repository has moved on since). Re-verify by hand with
    # `git ls-remote https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-TypeScript` if this
    # ever needs re-pinning; do not change it to make a test pass.
    # re-extracted by TC-550: c672b3918318ccd0c13bc9dc619620cac2674c9e -> the chart pin 155bfc7a33f0 (the
    # four compile-verified examples, the furnished page and the sidecar are all at that commit).
    assert data["source_commit"] == "155bfc7a33f0ba23fb4252b6ba201828b02a5b9d"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_complete() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # re-extracted by TC-550 at the chart pin 155bfc7: the public surface there is 4393 entries
    # (was 5342 at c672b39), all kept - the cap was raised to the total.
    # TC-533: 4393 -> 541: only the symbols the package entry (src/index.ts) exports are served; the other
    # internals (2,967 of the 3,060 functions among them) nobody can import. 541 = 530 distinct exported
    # names + 11 further same-name declarations of 10 of them in other files (the barrel exports by name).
    assert data["type_count"] == 541
    assert data["reduced_type_count"] == data["type_count"]  # re-extracted by TC-550: 300 -> 4393
    assert data["truncated"] is False  # re-extracted by TC-550: True -> False


def test_the_fixture_contains_real_typescript_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # The five highest-centrality types in the real 5274-type surface - confirmed directly
    # from this fixture's own content after the live run, not guessed (their relative order
    # shifted slightly from TC-255's run, so this asserts the set, not a fixed order). "Page"
    # in particular is the exact kind of central symbol TC-252's centrality-ranked
    # reduce_fixture fix exists to stop losing to an alphabetical cut.
    # TC-533: {Page, Document, PdfDict, PdfObject, StructElement} -> {Page, Document, StructElement}: PdfDict
    # and PdfObject are not exported from src/index.ts, so they are no longer served.
    assert {"Page", "Document", "StructElement"} <= names
    assert not {"PdfDict", "PdfObject"} & names
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
        # TC-533: "enum_declaration" removed: the three enums are internal (not exported from src/index.ts)
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

    # `PdfDict` is a real top-level type alias in src/types.ts, an internal alias that the package index
    # does not re-export. TC-533: reachable False -> not served at all (the fixture holds only the export
    # surface); `StructElement` (exported from src/index.ts) stands for the exported side.
    assert "PdfDict" not in by_name
    assert by_name["StructElement"]["reachable"] is True


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

    # re-extracted by TC-550: `M` (src/wmlns.ts) does not exist at the chart pin 155bfc7; the guard read
    # `AP_FONT_KEY`, an internal constant typed `string`. TC-533: `AP_FONT_KEY` -> `MATHML_NS`
    # (src/structns.ts, exported from the package index): the internal constant is no longer served.
    assert "AP_FONT_KEY" not in by_name
    const = by_name["MATHML_NS"]
    assert const["kind"] == "constant"
    assert const["file"] == "src/structns.ts"
    assert const["type"] == "string"
    assert const["reachable"] is True


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

    # re-extracted by TC-550: `storeValue` does not exist at the chart pin 155bfc7; the same guard reads
    # `SetActions`, copied from `Field` into `ButtonField` by `_flatten_inheritance` and rooted to
    # `Field`'s own class_import - the real declaring ancestor, read from the regenerated fixture.
    button_field_methods = {m["name"]: m for m in button_field["methods"]}
    copied = button_field_methods["SetActions"]
    assert copied["inherited_from"] == "formfield.Field"
    assert copied["params"][0]["type"] == "FieldActionsUpdate"

    # `Field`'s own `SetActions` is the genuine, locally-declared original: it must carry no
    # `inherited_from` key at all.
    field_methods = {m["name"]: m for m in field["methods"]}
    original = field_methods["SetActions"]
    assert "inherited_from" not in original


def test_a_real_interface_extends_interface_chain_carries_correctly_rooted_inherited_from_tags() -> None:
    """TC-316/TC-317: ``tree_helpers.py``'s ``_extract_bases()`` previously recognized only
    TypeScript's class-level ``class_heritage``/``extends_clause``/``implements_clause`` nodes,
    missing ``extends_type_clause`` - the real node type for an INTERFACE extending another
    interface. Before TC-316's fix, every one of this pilot's 56 real
    ``interface X extends Y`` relationships extracted as ``bases: []``.

    This fixture (TC-317's re-run, the first against TC-316's fix) carries a real, genuine
    4-level interface-extends-interface chain - confirmed directly from this fixture, not
    guessed: ``ComboBoxInit -> ChoiceInit -> FieldInit -> FieldStyle -> WidgetStyle`` (all real
    interfaces, ``src/formcreate.ts``/``src/fieldstyle.ts``). It exercises TC-261's
    multi-level-chain rooting rule end to end for this node type for the first time: a member
    copied transitively through an intermediate parent must still be rooted to the class that
    actually DECLARES it, never the intermediate parent it was copied through.
    """
    data = _load_fixture()
    by_import = {entry["class_import"]: entry for entry in data["types"] if entry.get("class_import")}

    combo_box_init = by_import["formcreate.ComboBoxInit"]
    choice_init = by_import["formcreate.ChoiceInit"]
    field_init = by_import["formcreate.FieldInit"]
    field_style = by_import["fieldstyle.FieldStyle"]

    assert combo_box_init["bases"] == ["ChoiceInit"]
    assert choice_init["bases"] == ["FieldInit"]
    assert field_init["bases"] == ["FieldStyle"]
    assert field_style["bases"] == ["WidgetStyle"]

    combo_box_init_props = {p["name"]: p for p in combo_box_init["properties"]}

    # `editable` and `options`/`value` are genuinely ComboBoxInit's/ChoiceInit's own: `options`
    # and `value` are copied one level up from `ChoiceInit` (the direct parent), so they carry
    # `inherited_from` rooted there.
    assert "inherited_from" not in combo_box_init_props["editable"]
    assert combo_box_init_props["options"]["inherited_from"] == "formcreate.ChoiceInit"
    assert combo_box_init_props["value"]["inherited_from"] == "formcreate.ChoiceInit"

    # `name`/`page` etc. are copied transitively through `ChoiceInit` but genuinely DECLARED by
    # the grandparent `FieldInit` - correctly rooted there, not to the intermediate `ChoiceInit`.
    assert combo_box_init_props["name"]["inherited_from"] == "formcreate.FieldInit"
    assert combo_box_init_props["page"]["inherited_from"] == "formcreate.FieldInit"

    # `font`/`fontSize`/`textColor` are copied through two intermediates (`ChoiceInit`,
    # `FieldInit`) but genuinely declared three levels up by `FieldStyle` - correctly rooted
    # there, confirming the multi-level-chain rule holds for this node type.
    assert combo_box_init_props["font"]["inherited_from"] == "fieldstyle.FieldStyle"
    assert combo_box_init_props["fontSize"]["inherited_from"] == "fieldstyle.FieldStyle"
    assert combo_box_init_props["textColor"]["inherited_from"] == "fieldstyle.FieldStyle"

    # `backgroundColor`/`borderColor` are copied through three intermediates but genuinely
    # declared four levels up, at the root `WidgetStyle` - correctly rooted there, never to
    # `FieldStyle` (the parent they were actually copied through at this step).
    assert combo_box_init_props["backgroundColor"]["inherited_from"] == "fieldstyle.WidgetStyle"
    assert combo_box_init_props["borderColor"]["inherited_from"] == "fieldstyle.WidgetStyle"

    # `FieldStyle`'s own `font`/`fontSize`/`textColor` are the genuine, locally-declared
    # originals: they must carry no `inherited_from` key at all.
    field_style_props = {p["name"]: p for p in field_style["properties"]}
    assert "inherited_from" not in field_style_props["font"]
    assert "inherited_from" not in field_style_props["fontSize"]
    assert "inherited_from" not in field_style_props["textColor"]
