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

Unlike html/python's ``HTMLElement`` chain (TC-294) or slides/python's ``IChart`` chain
(TC-291/TC-293), this pilot's own real upstream source
(``aspose/cells_foss/encryption_params.py`` at the pinned commit, hand-verified directly, not
assumed) shows ``EncryptionParameters`` declares exactly one method, ``__init__`` - excluded
from the public surface by ``python_surface.py``'s own underscore-name filter (the same filter
that drops every dunder) - and both subclasses define their own ``__init__`` anyway, so there
is no public member left for either subclass to inherit. Zero ``inherited_from`` tags anywhere
in this fixture is therefore a verified-genuine absence for this pilot, not a pipeline no-op:
mirrors the TC-301/TC-303/TC-312 tree-sitter-wave precedent for locking in a real absence
instead of fabricating a positive assertion.
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


def test_encryption_parameters_subclasses_have_a_hand_verified_genuine_absence_of_inherited_from() -> None:
    # Unlike html/python's HTMLElement chain (TC-294) or slides/python's IChart chain
    # (TC-291/TC-293), this pilot's own real upstream source
    # (aspose/cells_foss/encryption_params.py at the pinned commit, read directly - not
    # assumed) shows EncryptionParameters declares exactly one method, __init__, which
    # python_surface.py's own underscore-name filter excludes from the public surface (the
    # same filter that drops every dunder everywhere else in this pipeline) - and both real
    # subclasses below define their own __init__ anyway, overriding rather than inheriting it.
    # There is therefore no public member left for either subclass to inherit: zero
    # inherited_from tags for this base relationship is a verified-genuine absence, not a
    # pipeline no-op.
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    encryption_parameters = by_name["EncryptionParameters"]
    assert encryption_parameters["methods"] == []
    assert encryption_parameters["properties"] == []
    for subclass_name in ("AgileEncryptionParameters", "StandardEncryptionParameters"):
        subclass = by_name[subclass_name]
        assert subclass["bases"] == ["EncryptionParameters"]
        assert not any(method.get("inherited_from") for method in subclass["methods"])
        assert not any(prop.get("inherited_from") for prop in subclass["properties"])
    # And genuinely nowhere else in this fixture either - this pilot's only non-stdlib base
    # relationship is the one hand-verified above, and every stdlib Enum/IntEnum base is never
    # itself a kept type in this fixture, so it can never seed an inherited_from tag either.
    assert not any(
        method.get("inherited_from") for entry in data["types"] for method in entry.get("methods", [])
    )
    assert not any(
        prop.get("inherited_from") for entry in data["types"] for prop in entry.get("properties", [])
    )
