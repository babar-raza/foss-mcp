"""Offline guard: TC-400's seven re-extracted .NET fixtures are pinned at fixture level.

Companion to ``tests/extraction/test_central_classes_indexed.py`` (TC-395's pattern for
slides/python and words/net): this module covers the seven .NET pilots TC-400 re-extracted
at their PINNED library commits with the corrected extractor (TC-392's "constructors are not
inherited; test files are not API" and TC-396's C# return-type/params-array/modifier/indexer
fixes) and selection. Everything here reads committed files only - no network, no clone, no
engine.

To cover another .NET pilot, append its fixture directory name (``<family>_net``) to
``PILOTS`` once its fixture has been re-extracted with ``--keep-names-from``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.extraction.run_extraction import _is_test_entry, names_used_in_examples

# re-pinned by TC-551: PILOTS = ["3d_net", "cells_net", "email_net", "imaging_ne... -> PILOTS = ["3d_net", "cells_net", "email_net", "imaging_ne...
PILOTS = ["3d_net", "cells_net", "email_net", "imaging_net", "pdf_net", "slides_net"]

_ROOT = Path(__file__).parents[2]
_FIXTURES = _ROOT / "tests" / "fixtures"
_VALUES = _ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

# The central class(es) each pilot's examples open with - asserted by name, per the taskcard.
# Not every pilot has an entry: email_net and imaging_net carry no central-class pin here (the
# card lists exactly these five), though every pilot is still covered by the other checks below.
_CENTRAL: dict[str, list[str]] = {
    "pdf_net": ["Document", "Page"],
    "cells_net": ["Workbook"],
    "slides_net": ["Presentation"],
    "3d_net": ["Scene"],
}


def _fixture(pilot: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


def _furnished_page(pilot: str) -> Path:
    return _FIXTURES / "furnished" / pilot / "pages" / "_index.md"


def _pinned_commit(pilot: str) -> str:
    values = yaml.safe_load(_VALUES.read_text(encoding="utf-8"))
    wanted = f"/app/fixtures/{pilot}/api_surface.json"
    for entry in values["ingestion"]["pilots"]:
        if entry.get("apiSurface") == wanted:
            return str(entry["library"]["commit"])
    raise AssertionError(f"no pilot in values.yaml has apiSurface {wanted}")


def _has_members(entry: dict[str, Any]) -> bool:
    return bool(entry.get("methods") or entry.get("properties") or entry.get("enum_members"))


def _bare_words(texts: set[str]) -> set[str]:
    words: set[str] = set()
    for text in texts:
        # A bare name only: a namespace segment ("Drawing" in "Drawing.Shape") is not a type.
        words.update(re.findall(r"(?<![\w.])[A-Za-z_]\w*(?![\w.])", text))
    return words


def _example_classes_the_full_extraction_defines(pilot: str) -> set[str]:
    """Names used in *pilot*'s examples that are provably real, in-repo library types rather
    than BCL/framework types (``MemoryStream``, ``Uri``, ``DateTime``...) that merely show up
    as a parameter or return type somewhere.

    The full, unreduced extraction is not available offline (TC-400 is network-only at
    extraction time), so - as ``test_central_classes_indexed.py`` already does for its own two
    pilots - "the library defines it" is read back from the committed fixture itself. That
    proxy over-includes for these seven pilots if it is widened to method/property/param types:
    several of this pilot set's examples also name real BCL types (``MemoryStream``, ``Uri``,
    ``DateTime``) that are themselves referenced as a parameter or return type by some kept
    entry, which would wrongly flag them as cut library types. Restricting the cross-reference
    to ``bases`` only (a real in-repo inheritance edge is never a bare BCL name used this way in
    any of these seven fixtures - verified directly against all seven) avoids that false
    positive while still catching a genuinely cut base class.
    """
    data = _fixture(pilot)
    referenced = {str(base) for entry in data["types"] for base in entry.get("bases") or []}
    used = names_used_in_examples(_furnished_page(pilot))
    return _bare_words(referenced) & used


def test_the_pilot_list_covers_the_six_refreshed_pilots() -> None:
    # re-pinned by TC-551: assert {"3d_net", "cells_net", "email_net", "imaging_net"... -> assert {"3d_net", "cells_net", "email_net", "imaging_net"...
    assert {"3d_net", "cells_net", "email_net", "imaging_net", "pdf_net", "slides_net"} == set(PILOTS)


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_fixture_is_extracted_at_the_pinned_commit(pilot: str) -> None:
    pinned = _pinned_commit(pilot)
    assert re.fullmatch(r"[0-9a-f]{40}", pinned), pinned
    assert _fixture(pilot)["source_commit"] == pinned


@pytest.mark.parametrize("pilot", sorted(_CENTRAL))
def test_the_central_classes_are_present_and_have_members(pilot: str) -> None:
    data = _fixture(pilot)
    present = {entry["name"]: entry for entry in data["types"]}
    for central in _CENTRAL[pilot]:
        assert central in present, f"{pilot}: {central} was cut from the fixture"
        assert _has_members(present[central]), f"{pilot}: {central} carries no members"


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_example_class_the_full_extraction_defines_survives_the_cut(pilot: str) -> None:
    data = _fixture(pilot)
    present = {entry["name"] for entry in data["types"]}
    defined = _example_classes_the_full_extraction_defines(pilot)
    missing = sorted(name for name in defined if name not in present)
    assert not missing, f"{pilot}: example classes cut from the fixture: {missing}"


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_entry_comes_from_a_test_file(pilot: str) -> None:
    offenders = [entry["class_import"] for entry in _fixture(pilot)["types"] if _is_test_entry(entry)]
    assert not offenders, f"{pilot}: entries read from test sources: {offenders[:5]}"


@pytest.mark.parametrize("pilot", ["pdf_net", "cells_net"])
def test_every_declared_method_has_a_non_empty_return_type(pilot: str) -> None:
    """TC-396's C# return-type fix: a declared (non-constructor) method's ``return_type`` must
    never come back empty for these two fixtures."""
    data = _fixture(pilot)
    offenders = [
        (entry["name"], method["name"], method.get("line"))
        for entry in data["types"]
        for method in entry.get("methods", [])
        if not method.get("is_constructor") and not method.get("return_type")
    ]
    assert not offenders, f"{pilot}: methods with no return_type: {offenders[:5]}"


def test_cells_net_has_at_least_one_indexer_property() -> None:
    """TC-396's C# indexer fix: a real ``this[...]`` indexer must survive into cells_net."""
    data = _fixture("cells_net")
    indexers = [
        (entry["name"], prop["name"])
        for entry in data["types"]
        for prop in entry.get("properties", [])
        if prop["name"].startswith("this[")
    ]
    assert indexers, "cells_net: no indexer property (this[...]) found in the fixture"
