"""Offline tests against the committed email/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-151 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/email_python/api_surface.json``.

Like slides/python (and unlike pdf/net, a tree-sitter/C# reader), this fixture was
produced by the independent pure-``ast`` reader
(``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"``. Unlike slides/python's reduced 300-entry fixture, this repository's full,
unreduced surface is only 63 types, so its observed ``kind`` vocabulary includes a
top-level ``"function"`` entry alongside ``{"class", "enum"}`` - observed directly from
the live reader's output for this repository, not guessed.

TC-320 re-ran this extraction live (network access, real default --max-types 300, no
truncation) now that TC-252/TC-261/TC-265/TC-274/TC-281/TC-293/TC-294 (the full Python
bases/inheritance pipeline) are all on main - this fixture had never been regenerated
since TC-151's original onboarding wave, which predates every one of those cards. The
commit pin (``10a906b48c0c11005c4d93b524e4431901c9717c``) and repository
(``aspose-email-foss/Aspose.Email-FOSS-for-Python``) came back unchanged and match
``infra/helm/foss-mcp/values.yaml`` and ``docker-compose.yml`` exactly - no commit-pin
divergence here, unlike several sibling pilots fixed earlier this session. The 33
distinct type names and 63 total entries are identical to the old fixture too; what
changed is the data each entry now carries, and entry order (TC-252's centrality
ranking, now computed over real ``bases``/``return_type``/``params[].type`` data this
pure-``ast`` reader never populated before TC-265).

TC-320 also hand-verified, against a fresh clone of the pinned commit, that this
repository's real public surface contains ZERO custom-class-to-custom-class
inheritance: every single ``class``/``enum`` definition in the whole repository
(``grep -rn "^class " --include="*.py" .``, excluding tests) either declares no base at
all, or extends a stdlib builtin directly - ``IntEnum`` (``SectorMarker``,
``DirectoryObjectType``, ``DirectoryColorFlag``, ``PropertyId``,
``CommonMessagePropertyId``, ``PropertyTypeCode``) or ``Exception`` (``CFBError``,
``MsgError``). Unlike barcode/python's real ``class SymbologyNotFoundError(BarcodeError)``
underscore-origin re-export pattern (the scoping pass that motivated re-running this
pilot), nothing in this repository extends another of its own classes - there is no
multi-level chain, and no underscore-origin re-export module exists here either (every
``__init__.py`` re-export in this repository forwards from a plain, non-underscore
submodule - ``reader.py``, ``writer.py``, ``mapi_message.py``, ``types.py`` - the
ordinary ``ImportFrom`` resolution path ``_module_symbols()`` already handles, never the
underscore-origin path TC-274/TC-294 built). So ``_flatten_inheritance()`` (TC-281)
correctly produces zero ``inherited_from`` tags anywhere in this fixture: TC-265 now
correctly populates ``bases`` with the real stdlib ancestor name for 16 of the 63
entries (8 distinct types, each appearing twice - once as the package-level re-export
shell TC-293's short-name-collision fix resolves against, once as the real submodule
definition), but ``IntEnum``/``Exception`` are never themselves entries in this fixture's
``types`` list for ``_flatten_inheritance()`` to copy members from. That is accurate
extraction of this repository's real shape, not evidence of a stale or broken pipeline -
mirroring the TC-301/TC-303/TC-312 precedent (jmap/go, cells/rust, jmap/rust) of a
genuine, hand-verified absence of cross-type inheritance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "email_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "email" / "python.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

# The 8 distinct type names whose real base, hand-verified against the pinned clone, is a
# stdlib builtin - never another type in this fixture. Each appears twice in "types": once
# as the package-level __init__.py re-export shell, once as the real submodule definition
# (TC-293's short-name-collision shape).
_STDLIB_DERIVED_BASES = {
    "SectorMarker": "IntEnum",
    "DirectoryObjectType": "IntEnum",
    "DirectoryColorFlag": "IntEnum",
    "PropertyId": "IntEnum",
    "CommonMessagePropertyId": "IntEnum",
    "PropertyTypeCode": "IntEnum",
    "CFBError": "Exception",
    "MsgError": "Exception",
}


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
    # TC-320: re-ran live and hand-confirmed this exact commit is still the real upstream
    # HEAD - no commit-pin divergence for this pilot, unlike several sibling pilots this
    # session hit (infra/helm/foss-mcp/values.yaml, docker-compose.yml both agree).
    assert data["source_commit"] == "10a906b48c0c11005c4d93b524e4431901c9717c"


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]


def test_the_fixture_contains_real_python_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Email-FOSS-for-Python classes/functions, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"CFBDocument", "CFBReader", "DirectoryEntry"} <= names
    assert all(entry["class_import"].startswith("aspose.email_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Unlike slides/python's
    # reduced 300-entry fixture, this repository's unreduced 63-entry surface includes a
    # top-level function alongside classes and enums.
    assert kinds == {"class", "enum", "function"}


def test_real_bases_are_populated_for_the_stdlib_derived_types() -> None:
    """TC-265 now reads real ``bases`` for every Python-sourced entry (this fixture
    predates that pipeline entirely, per TC-320's purpose). Hand-verified against a fresh
    clone of the pinned commit: exactly 8 distinct types declare a base at all, each one a
    stdlib builtin (``IntEnum`` or ``Exception``) - never another type this repository
    itself defines. Every other entry genuinely has no base.
    """
    data = _load_fixture()
    with_bases = [entry for entry in data["types"] if entry.get("bases")]
    assert len(with_bases) == 2 * len(_STDLIB_DERIVED_BASES)
    for entry in with_bases:
        assert entry["name"] in _STDLIB_DERIVED_BASES
        assert entry["bases"] == [_STDLIB_DERIVED_BASES[entry["name"]]]
    others = [entry for entry in data["types"] if entry["name"] not in _STDLIB_DERIVED_BASES]
    assert len(others) == len(data["types"]) - len(with_bases)
    assert all(entry["bases"] == [] for entry in others)


def test_no_inherited_from_tag_exists_because_the_real_repository_has_no_custom_inheritance() -> None:
    """Hand-verified against a fresh clone of the pinned commit
    (``10a906b48c0c11005c4d93b524e4431901c9717c``): ``grep -rn "^class "`` over every
    ``.py`` file in the real repository (excluding tests) shows every class/enum either
    has no base, or extends a stdlib builtin (``IntEnum``/``Exception``) directly - never
    one of this repository's own classes. There is no multi-level chain and no
    underscore-origin re-export module (unlike barcode/python's real
    ``SymbologyNotFoundError(BarcodeError)`` pattern that motivated re-running this
    pilot); every ``__init__.py`` re-export here forwards from an ordinary, non-underscore
    submodule. ``_flatten_inheritance()`` (TC-281) therefore correctly produces zero
    ``inherited_from`` tags anywhere in this fixture - there is nothing for it to flatten,
    not a sign the pipeline failed to reach this pilot. Mirrors the TC-301/TC-303/TC-312
    precedent of a genuine, hand-verified absence of cross-type inheritance.
    """
    data = _load_fixture()
    assert "IntEnum" not in {entry["name"] for entry in data["types"]}
    assert "Exception" not in {entry["name"] for entry in data["types"]}
    for entry in data["types"]:
        for member in entry.get("methods", []) + entry.get("properties", []):
            assert member.get("inherited_from") is None
