"""TC-525 (REQ-G3-008): the Go fidelity GATE, mirroring the reviewer's ``go_audit.py``.

An independent audit reads the Go SOURCE TEXT of ``tests/fixtures/go_sample`` with regular
expressions (it never calls the extractor) and the result is compared with what
``extract_api_surface`` produced. The last test proves the comparison can fail: with every
``enum_members`` removed from a copy of the output the constants no longer match.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

GO_SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "go_sample"

_FUNC = re.compile(r"^func ([A-Z]\w*)(?:\[[^\]]*\])?\(", re.MULTILINE)
_METHOD = re.compile(r"^func \((?:\w+\s+)?\*?(\w+)(?:\[[^\]]*\])?\)\s+(\w+)\(", re.MULTILINE)
_TYPE = re.compile(r"^type ([A-Z]\w*)(?:\[[^\]]*\])?\s+(?:struct|interface|\w)", re.MULTILINE)
_CONST_BLOCK = re.compile(r"^const \((.*?)^\)", re.MULTILINE | re.DOTALL)
_CONST_LINE = re.compile(r"^\t([A-Za-z_]\w*)(?:\s+([A-Za-z_][\w.]*))?(?:\s*=.*)?$")


def _sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(GO_SAMPLE.glob("*.go")))


def _extract() -> list[dict[str, Any]]:
    types, *_ = api_surface.extract_api_surface(get_parser("go"), "go", GO_SAMPLE, GO_SAMPLE, "pdf")
    return types


def _audit_functions(text: str) -> set[str]:
    return set(_FUNC.findall(text))


def _audit_methods(text: str) -> set[tuple[str, str]]:
    """Methods whose receiver and name are both exported."""
    return {(recv, name) for recv, name in _METHOD.findall(text) if recv[:1].isupper() and name[:1].isupper()}


def _audit_all_method_names(text: str) -> set[str]:
    return {name for _, name in _METHOD.findall(text)}


def _audit_types(text: str) -> set[str]:
    return set(_TYPE.findall(text))


def _audit_constants(text: str) -> set[tuple[str, str]]:
    """``(type, name)`` of every exported constant that carries a type, per ``const (`` group."""
    found: set[tuple[str, str]] = set()
    for block in _CONST_BLOCK.findall(text):
        current = ""
        for raw in block.splitlines():
            line = raw.split("//", 1)[0].rstrip() if not raw.lstrip().startswith("//") else ""
            match = _CONST_LINE.match(line)
            if not match:
                continue
            name, declared = match.group(1), match.group(2)
            if declared:
                current = declared
            if current and name[:1].isupper():
                found.add((current, name))
    return found


def _extracted_constants(types: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (entry["name"], member["name"])
        for entry in types
        if entry.get("kind") == "type_spec"
        for member in entry.get("enum_members", [])
    }


def _constants_match(text: str, types: list[dict[str, Any]]) -> bool:
    return _audit_constants(text) == _extracted_constants(types)


def test_every_exported_package_function_is_extracted() -> None:
    extracted = {
        entry["name"]
        for entry in _extract()
        if entry["kind"] == "function" and not entry.get("receiver_type")
    }
    assert _audit_functions(_sources()) == extracted
    assert extracted  # the audit found something to compare


def test_every_exported_method_is_attached_or_marked() -> None:
    types = _extract()
    attached = {(entry["name"], method["name"]) for entry in types for method in entry.get("methods", [])}
    marked = {
        (entry["receiver_type"], entry["name"])
        for entry in types
        if entry["kind"] == "function" and entry.get("receiver_type")
    }
    assert _audit_methods(_sources()) == attached | marked
    assert {"Save", "Close", "Swap", "Run"} == {name for _, name in attached | marked}


def test_no_method_is_published_as_a_receiverless_function() -> None:
    bare = {
        entry["name"]
        for entry in _extract()
        if entry["kind"] == "function" and not entry.get("receiver_type")
    }
    assert _audit_all_method_names(_sources())  # the audit found methods to check
    assert bare.isdisjoint(_audit_all_method_names(_sources()))


def test_every_exported_type_is_extracted() -> None:
    extracted = {entry["name"] for entry in _extract() if entry["kind"] == "type_spec"}
    assert _audit_types(_sources()) == extracted


def test_every_typed_exported_constant_is_a_member_of_its_type() -> None:
    audit = _audit_constants(_sources())
    assert ("HAlign", "HAlignCenter") in audit
    assert ("Flags", "FlagB") in audit
    assert _constants_match(_sources(), _extract())


def test_gate_audit_rejects_an_extraction_without_constants() -> None:
    stripped = copy.deepcopy(_extract())
    for entry in stripped:
        entry.pop("enum_members", None)
    assert not _constants_match(_sources(), stripped)
    assert _constants_match(_sources(), _extract())
