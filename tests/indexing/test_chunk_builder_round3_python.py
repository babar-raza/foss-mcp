"""TC-532 (Round 3): how the chunk builder says what the Python extraction found.

Synthetic fixtures built by hand, offline. An exported class name that a LATER import bound to another,
empty class says so on both sides (``Shadows`` and the placeholder note, ``Shadowed-By``) and is never
paired with the class it replaced; a dataclass constructor, ``auto()`` enum members and a member inherited
from a private base are rendered as the extraction states them.
"""

from __future__ import annotations

import re
from typing import Any

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

REAL = "pkg.fbx.Opts.Opts"
PLACEHOLDER_DEFINITION = "pkg.Opts.Opts"
EXPORTED = "pkg.Opts"


def _entry(class_import: str, file: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": class_import.rsplit(".", 1)[1],
        "kind": "class",
        "file": file,
        "class_import": class_import,
        "bases": [],
        "methods": [],
        "properties": [],
        **extra,
    }


def _method(name: str, params: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"name": name, "params": params or [], "return_type": "", **extra}


def _shadow_fixture(placeholder_has_members: bool = False) -> dict[str, Any]:
    placeholder = _entry(PLACEHOLDER_DEFINITION, "pkg/Opts.py")
    if placeholder_has_members:
        placeholder["methods"] = [_method("noop")]
    real = _entry(REAL, "pkg/fbx/Opts.py", shadowed_by=EXPORTED)
    real["methods"] = [_method("__init__", [{"name": "fmt", "type": "", "default": "None"}]), _method("work")]
    exported = _entry(EXPORTED, "pkg/__init__.py", reexport_of=PLACEHOLDER_DEFINITION, shadows=REAL)
    return {
        "source_repository": "org/repo",
        "source_commit": "c0ffee",
        "type_count": 3,
        "types": [exported, placeholder, real],
    }


def _texts(fixture: dict[str, Any], max_types: int = 1000) -> dict[str, str]:
    texts: dict[str, str] = {}
    for chunk in build_chunks_from_api_surface(fixture, title="t", max_types=max_types):
        match = re.search(r"^FQN: (.+)$", chunk.text, re.MULTILINE)
        assert match is not None
        texts[match.group(1)] = chunk.text
    return texts


def test_the_exported_placeholder_says_it_shadows_the_working_class_and_is_empty() -> None:
    text = _texts(_shadow_fixture())[EXPORTED]
    lines = text.splitlines()
    assert f"Shadows: {REAL}" in lines
    assert (
        f"Note: this exported name is an empty placeholder at runtime; the working class is {REAL}" in lines
    )


def test_the_exported_placeholder_is_not_given_the_working_classes_members() -> None:
    text = _texts(_shadow_fixture())[EXPORTED]
    assert "Methods:" not in text
    assert "work(" not in text
    assert f"Defined-In: {PLACEHOLDER_DEFINITION}" in text  # it is paired with ITS definition only


def test_the_placeholder_definition_is_not_paired_with_the_working_class_by_name() -> None:
    text = _texts(_shadow_fixture())[PLACEHOLDER_DEFINITION]
    assert "Defined-In" not in text
    assert "Methods:" not in text


def test_the_working_class_says_which_exported_name_replaced_it() -> None:
    text = _texts(_shadow_fixture())[REAL]
    assert f"Shadowed-By: {EXPORTED}" in text.splitlines()
    assert "  - work()" in text and "  - __init__(fmt = None)" in text
    assert "Exported-As" not in text  # the exported name is not an alias of this class


def test_a_shadowing_name_with_members_gets_no_placeholder_note() -> None:
    text = _texts(_shadow_fixture(placeholder_has_members=True))[EXPORTED]
    assert f"Shadows: {REAL}" in text.splitlines()
    assert "empty placeholder" not in text
    assert "  - noop()" in text


def test_an_unmarked_fixture_renders_without_any_shadow_line() -> None:
    fixture = _shadow_fixture()
    for entry in fixture["types"]:
        entry.pop("shadows", None)
        entry.pop("shadowed_by", None)
    for text in _texts(fixture).values():
        assert "Shadows:" not in text and "Shadowed-By:" not in text


def test_the_placeholder_and_the_working_class_share_no_slot() -> None:
    texts = _texts(_shadow_fixture(), max_types=1)
    assert (
        EXPORTED in texts and PLACEHOLDER_DEFINITION in texts
    )  # the exported name and ITS definition: one slot
    assert REAL not in texts  # the working class is a slot of its own


# --------------------------------------------------------------------------- constructor, enum, inheritance


def _fixture(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"source_repository": "org/repo", "source_commit": "c0ffee", "types": list(entries)}


def test_a_generated_dataclass_constructor_renders_defaults_and_the_keyword_only_marker() -> None:
    entry = _entry("pkg.mod.C", "pkg/mod.py")
    entry["methods"] = [
        _method(
            "__init__",
            [
                {"name": "a", "type": "int"},
                {"name": "b", "type": "str", "default": "'x'"},
                {"name": "mode", "type": "str", "default": "'r'", "kind": "keyword_only"},
            ],
            is_constructor=True,
        )
    ]
    text = _texts(_fixture(entry))["pkg.mod.C"]
    assert "  - __init__(a: int, b: str = 'x', *, mode: str = 'r')" in text.splitlines()


def test_auto_enum_members_render_with_their_marker() -> None:
    entry = _entry("pkg.mod.Mode", "pkg/mod.py", kind="enum")
    entry["enum_members"] = [{"name": "AUTO", "value": "auto()"}, {"name": "FAST", "value": "auto()"}]
    lines = _texts(_fixture(entry))["pkg.mod.Mode"].splitlines()
    assert "Members:" in lines
    assert "  - AUTO = auto()" in lines and "  - FAST = auto()" in lines


def test_a_member_of_a_private_base_is_marked_inherited_and_an_override_is_not() -> None:
    entry = _entry("pkg.mod.Facade", "pkg/mod.py")
    entry["methods"] = [
        _method("own"),
        _method("bind", [{"name": "path", "type": "", "default": "None"}], inherited_from="pkg.mod._Base"),
    ]
    lines = _texts(_fixture(entry))["pkg.mod.Facade"].splitlines()
    assert "  - own()" in lines
    assert "  - bind(path = None)  (inherited from pkg.mod._Base)" in lines


def test_a_property_literal_is_rendered_after_its_type() -> None:
    entry = _entry("pkg.mod.K", "pkg/mod.py")
    entry["properties"] = [
        {"name": "MAX", "type": "int", "writable": False, "value": "5"},
        {"name": "NAME", "type": "str", "writable": False, "value": "'n'"},
        {"name": "X_AXIS", "type": "", "writable": False, "value": "None"},
        {"name": "count", "type": "", "writable": True, "value": "0"},
    ]
    lines = _texts(_fixture(entry))["pkg.mod.K"].splitlines()
    assert "  - MAX: int = 5 (read-only)" in lines
    assert "  - NAME: str = 'n' (read-only)" in lines
    assert "  - X_AXIS = None (read-only)" in lines
    assert "  - count = 0 (writable)" in lines


def test_a_property_without_a_literal_renders_as_before() -> None:
    entry = _entry("pkg.mod.K", "pkg/mod.py")
    entry["properties"] = [
        {"name": "size", "type": "int", "writable": True},
        {"name": "label", "type": "str", "writable": False, "value": ""},
    ]
    lines = _texts(_fixture(entry))["pkg.mod.K"].splitlines()
    assert "  - size: int (writable)" in lines
    assert "  - label: str (read-only)" in lines
