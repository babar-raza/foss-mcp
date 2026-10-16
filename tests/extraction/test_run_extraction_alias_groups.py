"""G3/TC-550: ``run_extraction._alias_groups`` loses no entry (REQ-G3-008). Offline, deterministic, no skips.

The defect: an explicit re-export shell (``reexport_of``) names a member-less module entry, and the name-matching
pass then pairs that module entry as a shell of a member-bearing name twin. The explicit shell hung off an index that
is no group head, so its group was never emitted and ``reduce_fixture`` dropped it (3d_python kept 690 of 697).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction
from tests.extraction import reextraction_checks as rc
from tests.extraction.docs_identifiers import PILOTS as ALL_PILOTS

_MEMBERS = [{"name": "run", "doc": "Runs."}]


def _cls(
    class_import: str,
    *,
    members: bool = False,
    reexport_of: str | None = None,
    file: str | None = None,
) -> dict[str, Any]:
    name = class_import.rsplit(".", 1)[-1]
    entry: dict[str, Any] = {
        "name": name,
        "class_import": class_import,
        "kind": "class",
        "file": file or class_import.replace(".", "/") + ".py",
        "methods": list(_MEMBERS) if members else [],
    }
    if reexport_of:
        entry["reexport_of"] = reexport_of
    return entry


def _covered(types: list[dict[str, Any]]) -> list[int]:
    return sorted(index for group in run_extraction._alias_groups(types) for index in group)


def _legacy_alias_groups(types: list[dict[str, Any]]) -> list[list[int]]:
    """``_alias_groups`` as it was before TC-550 (without the final owner settling)."""
    owner = run_extraction._explicit_shell_owners(types)
    definitions: dict[str, list[int]] = {}
    for index, entry in enumerate(types):
        if index in owner:
            continue
        if run_extraction._is_python_type(entry) and run_extraction._has_members(entry) and entry.get("name"):
            definitions.setdefault(entry["name"], []).append(index)

    def class_import(index: int) -> str:
        return types[index].get("class_import") or ""

    for index, entry in enumerate(types):
        if index in owner or not run_extraction._is_python_type(entry) or run_extraction._has_members(entry):
            continue
        candidates = definitions.get(entry.get("name") or "")
        if not candidates:
            continue
        package = run_extraction._package_of(entry)
        under = [c for c in candidates if package and class_import(c).startswith(package + ".")]
        owner[index] = min(under or candidates, key=lambda c: (class_import(c), c))
    members: dict[int, list[int]] = {}
    for shell, definition in sorted(owner.items()):
        members.setdefault(definition, []).append(shell)
    return [[i, *members.get(i, [])] for i in range(len(types)) if i not in owner]


def _chain() -> list[dict[str, Any]]:
    """A = shell in __init__ naming B; B = member-less module entry; C = member-bearing twin of B."""
    return [
        _cls("pkg.formats.Opts", reexport_of="pkg.formats.Opts.Opts", file="pkg/formats/__init__.py"),
        _cls("pkg.formats.Opts.Opts", file="pkg/formats/Opts.py"),
        _cls("pkg.formats.fbx.Opts.Opts", members=True, file="pkg/formats/fbx/Opts.py"),
    ]


# -- the exact chain ----------------------------------------------------------------------------------------------------------


def test_chain_emits_shell_module_entry_and_definition_in_one_group() -> None:
    groups = run_extraction._alias_groups(_chain())
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1, 2]
    assert groups[0][0] == 2, "the member-bearing definition leads its group"


def test_chain_loses_no_entry() -> None:
    assert _covered(_chain()) == [0, 1, 2]


def test_legacy_function_really_lost_the_chain_shell() -> None:
    """Guards the guard: the pre-TC-550 logic drops index 0, so the tests above can fail."""
    legacy = sorted(i for g in _legacy_alias_groups(_chain()) for i in g)
    assert legacy == [1, 2]


def test_chain_group_costs_one_slot_in_reduce_fixture() -> None:
    types = _chain() + [_cls("pkg.other.Thing", members=True)]
    reduced = run_extraction.reduce_fixture({"types": types, "language": "python"}, max_types=1)
    assert reduced["reduced_type_count"] in {3, 1}
    both = run_extraction.reduce_fixture({"types": types, "language": "python"}, max_types=100)
    assert both["reduced_type_count"] == len(types)
    assert both["truncated"] is False


def test_seven_chains_lose_nothing() -> None:
    types: list[dict[str, Any]] = []
    for name in ("Fbx", "Gltf", "Obj", "Stl", "Ply", "Dae", "Usd"):
        types.append(
            _cls(
                f"p.formats.{name}Options",
                reexport_of=f"p.formats.{name}Options.{name}Options",
                file="p/formats/__init__.py",
            )
        )
        types.append(_cls(f"p.formats.{name}Options.{name}Options", file=f"p/formats/{name}Options.py"))
        types.append(_cls(f"p.formats.{name.lower()}.{name}Options.{name}Options", members=True))
    assert _covered(types) == list(range(21))
    assert len(run_extraction._alias_groups(types)) == 7


# -- entries that are not shells, and cycles ---------------------------------------------------------------------------------


def test_a_plain_class_is_its_own_group() -> None:
    types = [_cls("pkg.A", members=True), _cls("pkg.B", members=True)]
    assert run_extraction._alias_groups(types) == [[0], [1]]


def test_a_member_less_class_without_a_twin_is_its_own_group() -> None:
    types = [_cls("pkg.A"), _cls("pkg.B", members=True)]
    assert run_extraction._alias_groups(types) == [[0], [1]]


def test_a_two_hop_name_pairing_without_an_explicit_shell_is_unchanged() -> None:
    types = [_cls("pkg.Widget"), _cls("pkg.impl.Widget", members=True)]
    assert run_extraction._alias_groups(types) == [[1, 0]]
    assert run_extraction._alias_groups(types) == _legacy_alias_groups(types)


def test_an_explicit_chain_to_a_member_bearing_definition_is_unchanged() -> None:
    types = [
        _cls("pkg.A", reexport_of="pkg.B"),
        _cls("pkg.B", reexport_of="pkg.C"),
        _cls("pkg.C", members=True),
    ]
    assert run_extraction._alias_groups(types) == [[2, 0, 1]]
    assert run_extraction._alias_groups(types) == _legacy_alias_groups(types)


def test_a_reexport_cycle_terminates_and_loses_nothing() -> None:
    types = [_cls("pkg.A", reexport_of="pkg.B"), _cls("pkg.B", reexport_of="pkg.A")]
    assert run_extraction._alias_groups(types) == [[0], [1]]


def test_a_cycle_through_a_member_less_twin_terminates_and_loses_nothing() -> None:
    types = [
        _cls("pkg.A", reexport_of="pkg.B"),
        _cls("pkg.B", reexport_of="pkg.A"),
        _cls("pkg.deep.A", members=True),
    ]
    assert _covered(types) == [0, 1, 2]


def test_self_reexport_is_left_alone() -> None:
    assert run_extraction._alias_groups([_cls("pkg.A", reexport_of="pkg.A", members=True)]) == [[0]]


def test_a_private_origin_is_left_to_the_name_pairing() -> None:
    types = [_cls("pkg.A", reexport_of="elsewhere.A"), _cls("pkg.impl.A", members=True)]
    assert run_extraction._alias_groups(types) == [[1, 0]]


def test_two_chains_sharing_one_definition_land_in_one_group() -> None:
    types = _chain() + [_cls("pkg.api.Opts", reexport_of="pkg.formats.Opts.Opts", file="pkg/api/__init__.py")]
    groups = run_extraction._alias_groups(types)
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1, 2, 3]


def test_every_index_lands_in_exactly_one_group() -> None:
    types = _chain() + [_cls("pkg.x.Y", members=True), _cls("pkg.Y")]
    flat = [index for group in run_extraction._alias_groups(types) for index in group]
    assert sorted(flat) == list(range(len(types)))
    assert len(flat) == len(set(flat))


# -- the committed fixtures --------------------------------------------------------------------------------------------------


def _python_keys() -> list[str]:
    return [key for key in ALL_PILOTS if rc.load_fixture(key).get("language") == "python"]


@pytest.mark.parametrize("key", _python_keys())
def test_committed_python_fixture_loses_no_entry_in_its_groups(key: str) -> None:
    types = rc.load_fixture(key)["types"]
    assert _covered(types) == list(range(len(types)))


@pytest.mark.parametrize("key", _python_keys())
def test_committed_python_fixture_groups_equal_the_legacy_groups_or_extend_them(key: str) -> None:
    types = rc.load_fixture(key)["types"]
    new = run_extraction._alias_groups(types)
    old = _legacy_alias_groups(types)
    if new != old:
        old_members = {i for g in old for i in g}
        assert old_members < {i for g in new for i in g}, key
    else:
        assert new == old


def test_the_python_fixtures_are_listed() -> None:
    keys = _python_keys()
    assert "3d_python" in keys and "pdf_python" in keys and len(keys) >= 10
    assert json.dumps(sorted(keys)) == json.dumps(sorted(set(keys)))
