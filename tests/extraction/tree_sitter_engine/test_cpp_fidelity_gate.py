"""TC-524 GATE (REQ-G3-008, REQ-G3-005): the C++ extraction is audited against the header TEXT.

This mirrors the independent reviewer's `cpp_audit.py`. A small audit written in this file reads the raw
header text, never the extractor: it strips comments and string literals, finds each `class|struct Name {`
body by brace matching, splits the body into depth-0 statements honouring `public:`/`private:`/`protected:`
(a `class` is private by default, a `struct` public), skips inline bodies, `using`, `typedef`, `friend`,
`static_assert`, nested type definitions and anything `= delete`, and parses each remaining statement as
`[specifiers] <return> <name>(<params>) <tail>`. It counts, per class: constructors, static methods,
trailing-`const` methods, methods with `&` or `*` in the parameter list, methods whose return text ends with
`&` or `*`, and methods. The same six counters are computed from the extractor's output and compared.

The gate can fail: `test_gate_audit_rejects_a_constructor_less_extraction` feeds the comparison an
extraction with every constructor removed (the reviewer's measured defect) and asserts it is rejected.
Offline and deterministic: the committed `tests/fixtures/cpp_sample` and the checksummed vendored
`tests/fixtures/cells_cpp_pinned_source` snapshot.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface, package_root
from tests.extraction.tree_sitter_engine.test_consolidate_classes_free_functions import (
    VENDORED_DIR,
    verify_vendored_snapshot,
)

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "cpp_sample"
COUNTERS = ("constructors", "static", "const", "ref_ptr_params", "ref_ptr_returns", "methods")
TOLERANCE = 0.05

# --- the independent audit: header text in, counters out -----------------------------------------------

_STRIP_RE = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.DOTALL)
_CLASS_RE = re.compile(r"\b(class|struct)\s+(?:\w+\s+)*?(\w+)\s*(?:final\s*)?(?::[^{;]*)?\{")
_ACCESS_RE = re.compile(r"^(public|private|protected)\s*:\s*")
_NAME_AT_END_RE = re.compile(r"(?:~?[A-Za-z_]\w*|operator\s*(?:[^\s\w(]+|\(\))|operator\s+[\w:<>*& ]+?)\s*$")
_SPECIFIER_RE = re.compile(r"\b(static|virtual|inline|explicit|constexpr|consteval|friend|extern)\b")
_SKIPPED_STARTS = (
    "using",
    "typedef",
    "friend",
    "static_assert",
    "template",
    "enum",
    "class",
    "struct",
    "union",
    "namespace",
)
_BODY_ENDERS_RE = re.compile(r"(?:\)|\bconst|\bnoexcept|\boverride|\bfinal|\bvolatile)\s*$")


def _blank(match: re.Match[str]) -> str:
    return '""' if match.group(0).startswith(('"', "'")) else " "


def _clean_text(text: str) -> str:
    text = _STRIP_RE.sub(_blank, text)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _matching_brace(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(text) - 1


def _statements(body: str) -> list[str]:
    """The depth-0 statements of a class body. An inline function body is dropped, its statement kept."""
    statements: list[str] = []
    current: list[str] = []
    paren = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char == "(":
            paren += 1
        elif char == ")":
            paren -= 1
        if char == "{" and paren == 0:
            close = _matching_brace(body, index)
            so_far = "".join(current).strip()
            is_function_body = bool(_BODY_ENDERS_RE.search(so_far)) and not so_far.startswith(_SKIPPED_STARTS)
            if is_function_body:
                statements.append(so_far)
                current = []
                index = close + 1
                if body[index : index + 1] == ";":
                    index += 1
                continue
            current.append(body[index : close + 1])
            index = close + 1
            continue
        if char == ";" and paren == 0:
            statements.append("".join(current).strip())
            current = []
        elif char == ":" and paren == 0 and _ACCESS_RE.match("".join(current).strip() + ":"):
            statements.append("".join(current).strip() + ":")
            current = []
        else:
            current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _parse_function(statement: str, class_name: str) -> dict[str, Any] | None:
    """`[specifiers] <return> <name>(<params>) <tail>` -> its parts, or None for anything that is not one."""
    statement = re.sub(r"\[\[.*?\]\]", " ", statement).strip()
    if not statement or statement.startswith(_SKIPPED_STARTS) or re.search(r"=\s*delete\b", statement):
        return None
    first_paren = statement.find("(")
    if first_paren < 0:
        return None
    pre = statement[:first_paren].rstrip()
    if re.search(r"operator\s*$", pre) and statement[first_paren : first_paren + 2] == "()":
        pre = statement[: first_paren + 2].rstrip()
        first_paren = statement.find("(", first_paren + 1)
        if first_paren < 0:
            return None
    if "=" in re.sub(r"operator\s*[^\s\w(]+", "operator", pre) or pre.count("<") != pre.count(">"):
        return None
    name_match = _NAME_AT_END_RE.search(pre)
    if name_match is None:
        return None
    name = name_match.group(0).strip()
    depth = 0
    close = -1
    for index in range(first_paren, len(statement)):
        if statement[index] == "(":
            depth += 1
        elif statement[index] == ")":
            depth -= 1
            if depth == 0:
                close = index
                break
    if close < 0:
        return None
    params = statement[first_paren + 1 : close]
    if params.lstrip().startswith(("*", "&")):
        return None  # a function-pointer data member
    head = pre[: name_match.start()]
    specifiers = set(_SPECIFIER_RE.findall(head))
    return_text = _SPECIFIER_RE.sub(" ", head).strip()
    return {
        "name": name,
        "constructor": name == class_name and not return_text,
        "static": "static" in specifiers,
        "const": bool(re.search(r"\bconst\b", statement[close + 1 :])),
        "ref_ptr_params": "&" in params or "*" in params,
        "ref_ptr_return": return_text.endswith(("&", "*")),
    }


def audit_header_text(text: str) -> dict[str, dict[str, int]]:
    """Per-class counters (class name -> counters) from the raw text of one header."""
    cleaned = _clean_text(text)
    per_class: dict[str, dict[str, int]] = {}
    for match in _CLASS_RE.finditer(cleaned):
        if re.search(r"\benum\s+$", cleaned[: match.start()]):
            continue
        kind, name = match.group(1), match.group(2)
        open_index = match.end() - 1
        body = cleaned[open_index + 1 : _matching_brace(cleaned, open_index)]
        access = "public" if kind == "struct" else "private"
        counts = dict.fromkeys(COUNTERS, 0)
        for statement in _statements(body):
            access_match = _ACCESS_RE.match(statement)
            if access_match:
                access = access_match.group(1)
                statement = statement[access_match.end() :]
            if access != "public":
                continue
            parsed = _parse_function(statement, name)
            if parsed is None:
                continue
            counts["methods"] += 1
            counts["constructors"] += parsed["constructor"]
            counts["static"] += parsed["static"]
            counts["const"] += parsed["const"]
            counts["ref_ptr_params"] += parsed["ref_ptr_params"]
            counts["ref_ptr_returns"] += parsed["ref_ptr_return"]
        existing = per_class.setdefault(name, dict.fromkeys(COUNTERS, 0))
        for counter in COUNTERS:
            existing[counter] += counts[counter]
    return per_class


def audit_headers(root: Path) -> dict[str, int]:
    """The counters summed over every class of every `.h`/`.hpp` file below *root*."""
    totals = dict.fromkeys(COUNTERS, 0)
    for header in sorted(path for path in root.rglob("*") if path.suffix in (".h", ".hpp")):
        for counts in audit_header_text(header.read_text(encoding="utf-8", errors="replace")).values():
            for counter in COUNTERS:
                totals[counter] += counts[counter]
    return totals


# --- the same counters from the extractor's output -------------------------------------------------------


def extractor_counters(types: list[dict[str, Any]], classes: set[str] | None = None) -> dict[str, int]:
    """The six counters over the extracted class and struct entries declared in a header: own members only
    (an inherited member is another class's), deleted members excluded."""
    totals = dict.fromkeys(COUNTERS, 0)
    for entry in types:
        if entry.get("kind") not in ("class_specifier", "struct_specifier"):
            continue
        if not str(entry.get("file", "")).endswith((".h", ".hpp")):
            continue
        if classes is not None and entry["name"] not in classes:
            continue
        for method in entry.get("methods", []):
            modifiers = method.get("modifiers", [])
            if method.get("inherited_from") or "deleted" in modifiers:
                continue
            totals["methods"] += 1
            totals["constructors"] += bool(method.get("is_constructor"))
            totals["static"] += "static" in modifiers
            totals["const"] += "const" in modifiers
            totals["ref_ptr_params"] += any(
                "&" in str(p.get("type", "")) or "*" in str(p.get("type", ""))
                for p in method.get("params", [])
            )
            totals["ref_ptr_returns"] += str(method.get("return_type", "")).rstrip().endswith(("&", "*"))
    return totals


def disagreements(
    audit: Mapping[str, int], extracted: Mapping[str, int], tolerance: float = TOLERANCE
) -> list[str]:
    """Every counter on which the extractor is further than *tolerance* (relative) from the audit."""
    failures = []
    for counter in COUNTERS:
        expected, actual = audit[counter], extracted[counter]
        if abs(expected - actual) > tolerance * max(expected, actual, 1):
            failures.append(f"{counter}: audit {expected} vs extractor {actual}")
    return failures


# --- fixtures --------------------------------------------------------------------------------------------


def _extract(root: Path, family: str) -> list[dict[str, Any]]:
    pkg_root = package_root.detect_package_root(root, "cpp")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", pkg_root, root, family)
    return types


@pytest.fixture(scope="module")
def sample_types() -> list[dict[str, Any]]:
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", SAMPLE_DIR, SAMPLE_DIR, "cells")
    return types


@pytest.fixture(scope="module")
def snapshot_types() -> list[dict[str, Any]]:
    verify_vendored_snapshot(VENDORED_DIR)
    return _extract(VENDORED_DIR, "cells")


# --- the gate --------------------------------------------------------------------------------------------


def test_cpp_sample_counts_equal_the_hand_counted_expectation(sample_types: list[dict[str, Any]]) -> None:
    expected = {
        "constructors": 4,
        "static": 1,
        "const": 4,
        "ref_ptr_params": 5,
        "ref_ptr_returns": 3,
        "methods": 12,
    }
    assert extractor_counters(sample_types) == expected
    assert audit_headers(SAMPLE_DIR) == expected


def test_cpp_sample_counts_match_the_audit_within_five_percent(sample_types: list[dict[str, Any]]) -> None:
    assert disagreements(audit_headers(SAMPLE_DIR), extractor_counters(sample_types)) == []


def test_vendored_snapshot_counts_match_the_audit_within_five_percent(
    snapshot_types: list[dict[str, Any]],
) -> None:
    audit = audit_headers(VENDORED_DIR)
    extracted = extractor_counters(snapshot_types)
    assert audit["constructors"] > 100, audit  # the audit really read the headers
    assert disagreements(audit, extracted) == [], (audit, extracted)


def test_vendored_snapshot_free_functions_come_only_from_public_headers(
    snapshot_types: list[dict[str, Any]],
) -> None:
    functions = [t for t in snapshot_types if t.get("kind") == "function"]
    assert [f["file"] for f in functions if not f["file"].endswith((".h", ".hpp"))] == []
    assert not [f for f in functions if f["file"].endswith(".cpp")]


def test_vendored_snapshot_workbook_constructors_are_complete(snapshot_types: list[dict[str, Any]]) -> None:
    header = next(path for path in sorted(VENDORED_DIR.rglob("Workbook.h")) if "cells_foss" in path.parts)
    audited = audit_header_text(header.read_text(encoding="utf-8"))["Workbook"]["constructors"]
    workbook = next(t for t in snapshot_types if t["name"] == "Workbook" and t["file"].endswith("Workbook.h"))
    extracted = [
        m
        for m in workbook["methods"]
        if m["is_constructor"] and "deleted" not in m.get("modifiers", []) and not m.get("inherited_from")
    ]
    assert len(extracted) == audited
    assert len(extracted) > 1


def test_gate_audit_rejects_a_constructor_less_extraction(
    sample_types: list[dict[str, Any]], snapshot_types: list[dict[str, Any]]
) -> None:
    for types, audit in (
        (sample_types, audit_headers(SAMPLE_DIR)),
        (snapshot_types, audit_headers(VENDORED_DIR)),
    ):
        assert disagreements(audit, extractor_counters(types)) == []
        stripped = copy.deepcopy(types)
        for entry in stripped:
            entry["methods"] = [m for m in entry.get("methods", []) if not m.get("is_constructor")]
        failures = disagreements(audit, extractor_counters(stripped))
        assert any(failure.startswith("constructors:") for failure in failures), failures
