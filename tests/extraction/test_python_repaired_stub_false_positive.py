"""G3/TC-847 (REQ-G3-012): a ``pass`` the syntax repair wrote is never read as the author's empty body.

tex/python's upstream ``presentation/__init__.py`` (and ``font_manager.py``, ``reader.py``) do not parse: their
indentation is collapsed, so a ``def`` is followed by its docstring at the same column. The extractor repairs
such a file by closing every header that lacks a body with `` pass`` (disclosed by the ``repaired-syntax`` entry
of ``unresolved``). The stub detector then read that synthesised ``pass`` as the author's own empty body and
the regenerated fixture carried 38 false ``stub: true`` / "empty body (pass)" members (``DviDevice.finalize``,
``get_bytes``, ``OutputDevice.destination``, 14 ``FontManager`` methods ...), each of which has a real body at
the pinned commit. The served disclosure "not implemented in this build" would have been false.

The micro-library below is hand-authored: no previous run of the extractor is consulted. The fixture assertion
holds the regenerated tex/python surface to the same rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from foss_mcp.extraction import run_extraction

# G3/TC-853: the committed fixture is now the extraction at the upstream release 26.11.1 (07db3203), where nothing is repaired; the
# repaired-file rule is still held against the frozen extraction of 181015d2 (the previously committed fixture, byte for byte).
_TEX_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "tex_python" / "api_surface_181015d2_repaired.json"
)
_TEX_COMMITTED = Path(__file__).resolve().parents[1] / "fixtures" / "tex_python" / "api_surface.json"

# The collapsed-indentation module: ``Device`` has its methods' docstrings at the column of their ``def`` (the
# repair closes each such header with ``pass``). ``Plain`` sits in the same file but parses as written.
_COLLAPSED = (
    "class Device:\n"
    ' """A device whose indentation was collapsed upstream."""\n'
    "\n"
    " def finalize(self) -> None:\n"
    ' """Writes the postamble."""\n'
    " self._done = True\n"
    "\n"
    " @property\n"
    " def destination(self):\n"
    ' """Where the output goes."""\n'
    " return self._dest\n"
    "\n"
    "\n"
    "class Plain:\n"
    "    def genuine_pass(self):\n"
    "        pass\n"
    "\n"
    "    def genuine_ellipsis(self): ...\n"
    "\n"
    "    def genuine_raise(self):\n"
    "        raise NotImplementedError\n"
    "\n"
    "    def real(self):\n"
    "        return 1\n"
)


def _extract(root: Path, body: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    package = root / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mod.py").write_text(body, encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\n', encoding="utf-8")
    types, unresolved = run_extraction._extract_python_surface(root)
    return {entry["name"]: entry for entry in types}, unresolved


def _members(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {member["name"]: member for member in [*entry.get("methods", []), *entry.get("properties", [])]}


def test_the_micro_library_really_is_repaired(tmp_path: Path) -> None:
    """Guard the fixture of this test: the file must go through the repair path, or the rest proves nothing."""
    _entries, unresolved = _extract(tmp_path, _COLLAPSED)
    assert any(":repaired-syntax:" in entry and "mod.py" in entry for entry in unresolved), unresolved


def test_a_pass_the_repair_synthesised_is_not_an_empty_body_stub(tmp_path: Path) -> None:
    entries, _unresolved = _extract(tmp_path, _COLLAPSED)
    members = _members(entries["Device"])
    assert {"finalize", "destination"} <= set(members), sorted(members)
    for name in ("finalize", "destination"):
        assert not members[name].get("stub"), (name, members[name])
        assert "stub_reason" not in members[name], (name, members[name])


def test_a_written_pass_ellipsis_and_raise_in_the_same_repaired_file_stay_stubs(tmp_path: Path) -> None:
    entries, _unresolved = _extract(tmp_path, _COLLAPSED)
    members = _members(entries["Plain"])
    assert members["genuine_pass"].get("stub") is True
    assert members["genuine_pass"]["stub_reason"] == "empty body (pass)"
    assert members["genuine_ellipsis"].get("stub") is True
    assert members["genuine_ellipsis"]["stub_reason"] == "empty body (...)"
    assert members["genuine_raise"].get("stub") is True
    assert members["genuine_raise"]["stub_reason"] == "raises NotImplementedError"
    assert not members["real"].get("stub")


def test_a_genuine_pass_in_a_file_that_needs_no_repair_is_still_a_stub(tmp_path: Path) -> None:
    entries, unresolved = _extract(
        tmp_path,
        "class Plain:\n"
        "    def genuine_pass(self):\n"
        "        pass\n"
        "\n"
        "    def genuine_raise(self):\n"
        "        raise NotImplementedError('later')\n"
        "\n"
        "    def real(self):\n"
        "        return 1\n",
    )
    assert not any(":repaired-syntax:" in entry for entry in unresolved), unresolved
    members = _members(entries["Plain"])
    assert members["genuine_pass"].get("stub_reason") == "empty body (pass)"
    assert members["genuine_raise"].get("stub_reason") == "raises NotImplementedError"
    assert not members["real"].get("stub")


def _flagged_members(surface: dict[str, Any]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for entry in surface["types"]:
        for member in [*entry.get("methods", []), *entry.get("properties", [])]:
            if member.get("stub"):
                found.append((entry["name"], member["name"], str(member.get("stub_reason"))))
    return found


def test_the_tex_fixture_carries_no_empty_body_pass_stub_from_a_repaired_file() -> None:
    """Every ``empty body (pass)`` member of tex/python must be a ``pass`` the upstream text really holds.

    The three repaired upstream modules (``presentation/__init__.py``, ``font_manager.py``, ``reader.py``) were
    read at the pinned commit: none contains a written ``pass``-only function, so no tex member may carry the
    flag. (Their two ``...`` bodies, ``_create_backend`` and ``finalize``, are ``@abc.abstractmethod``
    contracts, never stubs.)"""
    surface = json.loads(_TEX_FIXTURE.read_text(encoding="utf-8"))
    repaired = [entry for entry in surface["unresolved"] if ":repaired-syntax:" in entry]
    assert len(repaired) == 3, repaired
    assert [flag for flag in _flagged_members(surface) if flag[2] == "empty body (pass)"] == []


def test_the_committed_tex_fixture_at_26_11_1_has_nothing_repaired_and_no_pass_stub() -> None:
    """TC-853: at 07db3203 every file parses as written, so no ``repaired-syntax`` entry exists and no member is a stub."""
    surface = json.loads(_TEX_COMMITTED.read_text(encoding="utf-8"))
    assert surface["source_commit"] == "07db320334a0a58e11107b6ca8092b2e5047c1f8"
    assert not [entry for entry in surface.get("unresolved", []) if ":repaired-syntax:" in entry]
    assert _flagged_members(surface) == []
