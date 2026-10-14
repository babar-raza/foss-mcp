"""Offline tests against the committed cells/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-025/TC-093 set
``network: false`` for the first slides/cells pilots; this card follows the same pattern),
so nothing here may reach out; everything is read from the fixture ``run_extraction.py``
already produced and committed at ``tests/fixtures/cells_python/api_surface.json``.

Like slides/python (see tests/extraction/test_slides_python_extraction.py), this fixture was
produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all. Unlike slides/python,
this repository's observed ``kind`` vocabulary among the (untruncated, 204-entry) reduced
fixture is ``{"class", "enum", "function"}`` - a top-level ``"function"`` entry did survive
here (e.g. module-level helpers like ``is_encrypted_file``), observed directly from the live
run, not guessed.

Regenerated G2/TC-319: this fixture was last produced by TC-160, the original onboarding
wave, well before TC-265/TC-252/TC-274/TC-281/TC-293/TC-294 built the real bases/inheritance
pipeline - every one of its 204 types had ``bases: []``. A real re-run against the same
pinned commit (``4f6768a7b349a1309644f456eb43bc35f70c16d7`` - unchanged; no commit-pin
divergence this time) now carries 35 non-empty ``bases`` lists: 24 ``IntEnum``/7 ``Enum``
subclasses (real Python-stdlib bases, never themselves kept types in this fixture, so no
``inherited_from`` applies to them), plus ``AgileEncryptionParameters`` and
``StandardEncryptionParameters``, both real subclasses of this pilot's own
``EncryptionParameters`` (``aspose.cells_foss.encryption_params``).

Regenerated again under TC-401 (G3 pilot-completeness re-extraction) now that TC-393
(``__init__`` is the constructor, every declared parameter kept) and TC-473 (a
``self.<name>`` assignment inside ``__init__`` is a writable ``property``) are both on
main - neither existed when TC-319 last regenerated this fixture and hand-verified that
``EncryptionParameters`` declared no public member to inherit at all. This live re-run
against the same pinned commit (still unchanged) now recovers a real
``__init__(self, encryption_type, cipher_algorithm, hash_algorithm, spin_count)``
constructor and four real properties on ``EncryptionParameters`` - genuinely inherited by
both ``AgileEncryptionParameters`` and ``StandardEncryptionParameters``, each correctly
tagged "inherited_from" the base. Both subclasses still define their own ``__init__``
(overriding, never inheriting, the base constructor), so that one member alone stays
untagged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cells_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "cells" / "python.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "python"


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


def test_the_fixture_contains_real_python_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Cells-FOSS-for-Python classes/enums/functions, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Cell", "Cells", "Chart", "ChartType", "AgileEncryptionParameters"} <= names
    assert all(entry["class_import"].startswith("aspose.cells_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Unlike slides/python,
    # this repository's reduced fixture also contains top-level module functions.
    assert kinds == {"class", "enum", "function"}


def test_the_fixture_now_carries_real_bases_under_the_full_inheritance_pipeline() -> None:
    # TC-319's own closing proof: the pre-TC-319 fixture (TC-160, the original onboarding
    # wave) had EVERY one of its 204 types at bases == [] - it predated TC-265/TC-274/TC-281/
    # TC-293/TC-294 entirely. This real re-run against the same pinned commit now carries 35
    # non-empty bases lists - observed directly, not guessed.
    data = _load_fixture()
    with_bases = [entry for entry in data["types"] if entry.get("bases")]
    assert len(with_bases) >= 30
    by_name = {entry["name"]: entry for entry in data["types"]}
    # Real stdlib-rooted enum inheritance - every IntEnum/Enum subclass in this corpus.
    assert by_name["ChartType"]["bases"] == ["IntEnum"]
    assert by_name["CipherAlgorithm"]["bases"] == ["Enum"]
    # Real in-repository inheritance: AgileEncryptionParameters/StandardEncryptionParameters
    # both genuinely subclass this pilot's own EncryptionParameters
    # (aspose.cells_foss.encryption_params.EncryptionParameters) - confirmed directly against
    # the real pinned upstream source, not assumed from the base name alone.
    assert by_name["AgileEncryptionParameters"]["bases"] == ["EncryptionParameters"]
    assert by_name["StandardEncryptionParameters"]["bases"] == ["EncryptionParameters"]
    assert by_name["EncryptionParameters"]["class_import"] == (
        "aspose.cells_foss.encryption_params.EncryptionParameters"
    )


def test_encryption_parameters_subclasses_now_inherit_real_properties_with_provenance() -> None:
    # TC-401's own re-run, under TC-393 (``__init__`` is the constructor, every declared
    # parameter kept) and TC-473 (a ``self.<name>`` assignment inside ``__init__`` is a
    # writable ``property``) - neither on main when this fixture was last regenerated.
    # This pilot's own real upstream source (aspose/cells_foss/encryption_params.py at the
    # pinned commit, read directly - not assumed) shows EncryptionParameters declares a
    # real ``__init__(self, encryption_type, cipher_algorithm, hash_algorithm, spin_count)``
    # that assigns exactly those four names onto ``self`` - previously invisible entirely
    # (the dunder-name filter excluded ``__init__``, and no property-recovery existed), now
    # a real constructor plus four real properties, inherited by both real subclasses with
    # correct "inherited_from" provenance. Each subclass still declares its own ``__init__``
    # (overriding, never inheriting it), exactly as before.
    data = _load_fixture()
    by_import = {entry["class_import"]: entry for entry in data["types"] if entry.get("class_import")}
    encryption_parameters = by_import["aspose.cells_foss.encryption_params.EncryptionParameters"]
    assert [m["name"] for m in encryption_parameters["methods"]] == ["__init__"]
    assert encryption_parameters["methods"][0]["is_constructor"] is True
    assert [p["name"] for p in encryption_parameters["methods"][0]["params"]] == [
        "encryption_type",
        "cipher_algorithm",
        "hash_algorithm",
        "spin_count",
    ]
    base_property_names = {"cipher_algorithm", "encryption_type", "hash_algorithm", "spin_count"}
    assert {p["name"] for p in encryption_parameters["properties"]} == base_property_names
    assert not any(method.get("inherited_from") for method in encryption_parameters["methods"])
    assert not any(prop.get("inherited_from") for prop in encryption_parameters["properties"])

    for subclass_name in ("AgileEncryptionParameters", "StandardEncryptionParameters"):
        subclass = by_import[f"aspose.cells_foss.encryption_params.{subclass_name}"]
        assert subclass["bases"] == ["EncryptionParameters"]
        # The subclass's own __init__ overrides, never inherits, the base constructor.
        own_methods = {m["name"] for m in subclass["methods"] if not m.get("inherited_from")}
        assert own_methods == {"__init__"}
        assert not any(method.get("inherited_from") for method in subclass["methods"])
        # The four base properties ARE genuinely inherited now, each correctly rooted to
        # EncryptionParameters - never fabricated, never left untagged.
        inherited_properties = {
            prop["name"]: prop["inherited_from"]
            for prop in subclass["properties"]
            if prop.get("inherited_from")
        }
        assert base_property_names <= set(inherited_properties)
        for name in base_property_names:
            assert inherited_properties[name] == "aspose.cells_foss.encryption_params.EncryptionParameters"
