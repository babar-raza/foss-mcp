"""Offline guard: every class a pilot's own examples use survives into its committed fixture.

The extraction proof harnesses pick their probe from the already-cut type list, so a cut that
silently drops a pilot's central class (``Presentation`` for slides/python, ``DocumentBuilder``
for words/net) is invisible to them. This test closes that hole at fixture level: for each pilot
in ``PILOTS`` it reads the committed fixture, the pilot's furnished page and the pinned commit in
``infra/helm/foss-mcp/values.yaml`` - committed files only, no network, no clone.

To cover another pilot, append its fixture directory name (``<family>_<platform>``) to
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

PILOTS = ["slides_python", "words_net"]

_ROOT = Path(__file__).parents[2]
_FIXTURES = _ROOT / "tests" / "fixtures"
_VALUES = _ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

# The class every example of the pilot opens with; asserted by name, not only via the page scan.
_CENTRAL = {"slides_python": "Presentation", "words_net": "DocumentBuilder"}


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


def test_the_pilot_list_covers_the_two_refreshed_pilots() -> None:
    assert {"slides_python", "words_net"} <= set(PILOTS)


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_class_the_examples_use_is_in_the_fixture(pilot: str) -> None:
    data = _fixture(pilot)
    present = {entry["name"] for entry in data["types"]}
    used = names_used_in_examples(_furnished_page(pilot))
    assert used, f"{pilot}: the furnished page names no classes - the scan is broken"
    # The artifact is not available offline, so "the library defines it" is read from the fixture
    # itself: a used name that some kept entry references (as a base or a member type) is a real
    # library type and must be a kept entry too. The central class is additionally pinned by name.
    central = _CENTRAL[pilot]
    assert central in used, f"{pilot}: {central} not found in the furnished page's examples"
    assert central in present, f"{pilot}: {central} was cut from the fixture"
    missing_central = sorted(name for name in used if name in _defined_names(pilot) and name not in present)
    assert not missing_central, f"{pilot}: example classes cut from the fixture: {missing_central}"


def _defined_names(pilot: str) -> set[str]:
    """Names used in the pilot's examples that some kept fixture entry references in a base, a
    method return/parameter type or a property type (so they are real types of the library)."""
    data = _fixture(pilot)
    referenced: set[str] = set()
    for entry in data["types"]:
        referenced.update(str(base) for base in entry.get("bases") or [])
        for method in entry.get("methods") or []:
            referenced.add(str(method.get("return_type") or ""))
            referenced.update(str(p) for p in method.get("param_types") or [])
        for prop in entry.get("properties") or []:
            referenced.add(str(prop.get("type") or ""))
    words = set()
    for text in referenced:
        # A bare name only: a namespace segment (``Drawing`` in ``Drawing.Shape``) is not a type.
        words.update(re.findall(r"(?<![\w.])[A-Za-z_]\w*(?![\w.])", text))
    return words & set(names_used_in_examples(_furnished_page(pilot)))


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_central_class_is_present_by_name_and_has_members(pilot: str) -> None:
    central = _CENTRAL[pilot]
    entries = [entry for entry in _fixture(pilot)["types"] if entry["name"] == central]
    assert entries, f"{pilot}: {central} is not in the committed fixture"
    assert any(_has_members(entry) for entry in entries), f"{pilot}: {central} carries no members"


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_fixture_is_extracted_at_the_pinned_commit(pilot: str) -> None:
    pinned = _pinned_commit(pilot)
    assert re.fullmatch(r"[0-9a-f]{40}", pinned), pinned
    assert _fixture(pilot)["source_commit"] == pinned


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_entry_comes_from_a_test_file(pilot: str) -> None:
    offenders = [entry["class_import"] for entry in _fixture(pilot)["types"] if _is_test_entry(entry)]
    assert not offenders, f"{pilot}: entries read from test sources: {offenders[:5]}"


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_python_stub_is_split_from_its_definition(pilot: str) -> None:
    """A member-less ``__init__.py`` re-export stub must travel with its same-name definition."""
    data = _fixture(pilot)
    if data.get("language") != "python":
        # Stubs are a python re-export idiom; for other languages the invariant is vacuously true.
        assert all(not entry["file"].endswith("__init__.py") for entry in data["types"])
        return
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in data["types"]:
        by_name.setdefault(entry["name"], []).append(entry)
    lone_stubs = sorted(
        group[0]["class_import"]
        for group in by_name.values()
        if len(group) == 1 and group[0]["file"].endswith("__init__.py") and not _has_members(group[0])
    )
    assert not lone_stubs, f"{pilot}: re-export stubs kept without their definition: {lone_stubs[:5]}"
