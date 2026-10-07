"""Offline tests against the committed jmap/cpp fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_cpp/api_surface.json``.

C++-platform extraction support was already proven by the cells/cpp onboarding (after
TC-092's consolidate_classes() fix); this card is a straight run-and-pin against a second,
real, public C++ repository (``aspose-email-foss/Aspose.JMAP-FOSS-for-Cpp``) - no engine
change at all. "cpp" goes through the ordinary tree-sitter path (grammar name literally
``"cpp"``) via ``tree_sitter_engine.api_surface``. Its ``language`` field reads literally
``"cpp"``.

Unlike cells/cpp's nested ``include``/``src`` layout, this real repository is a
header-only library: every real entry's ``file`` ends in ``.hpp`` (none of this fixture's
types live in a ``.cpp`` translation unit), so ``package_root._detect_cpp_root()`` found a
top-level ``include`` directory directly.

The real repository's full public surface is only 39 types - well under the CLI's default
--max-types 300 cap - so this fixture is the complete surface, not a sub-sample:
``type_count == reduced_type_count == 39`` and ``truncated is False``.

The observed ``kind`` vocabulary is ``{"class_specifier", "struct_specifier"}`` - this
repository's public surface has no top-level free functions or enums in the reduced set.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_cpp" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "cpp.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "cpp"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Cpp` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "559c962081323468039dc75cf38676499f32b20e"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 39 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 39
    assert data["truncated"] is False


def test_the_fixture_contains_real_cpp_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Cpp types, not placeholders - would not survive an empty or
    # synthetic fixture.
    assert {"Account", "Comparator", "CoreCapability", "EmailAddress"} <= names
    # Header-only: every real entry lives in a .hpp file, no .cpp translation unit.
    assert all(entry["file"].endswith(".hpp") for entry in data["types"])


def test_every_entry_is_a_real_cpp_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter C++ grammar's own node-type vocabulary
    # for this repository, not guessed or invented.
    assert kinds == {"class_specifier", "struct_specifier"}
