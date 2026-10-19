"""G3/TC-534 (REQ-G3-009, REQ-G3-008; Round-3 rows RD3-020 to RD3-023): the four regenerated C++ fixtures say what the headers say.

Offline and deterministic. Every expectation comes from an INDEPENDENT oracle, never from the extractor:

* ``public_text_facts`` reads raw header TEXT (comments and strings blanked, class bodies split into depth-0
  statements honouring ``public:``/``private:``) with the committed text audit of ``test_cpp_fidelity_gate``; it
  finds the ``static``/``const``/``constexpr`` data members and the nested type definitions of a header without a
  parser. Its own tests below feed it hand-written headers whose answer follows from the language rules.
* The numbers pinned for the four pilots were counted with that oracle (and with a count made by hand on the
  upstream ``color.h``) on the clones at the chart pins; the vendored cells snapshot
  (``tests/fixtures/cells_cpp_pinned_source``, exactly the cells pin) is audited live.

Verified against the upstream clones at the pins when this card was written; where the round-3 audit's figure
differed it is stated: the slides ``Color`` class has 141 ``static const Color`` declarations in ``color.h``
(the audit's "about 286" is the oracle's count over every slides header: 141 plus 145 in the ``_internal/``
headers, which are not API); 11 of the 14 cells constants the
audit called missing are public (``static constexpr const char*`` in ``DefinedNameUtility.h`` and
``PackagingConventions.h``), the other three (``PageSetup::kCentimetersPerInch``,
``XlsxWorkbookPageSetup::MaxSpreadsheetRow`` and ``MaxSpreadsheetColumn``) are ``private`` and correctly not served.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface, package_root
from tests.extraction.tree_sitter_engine import test_cpp_fidelity_gate as gate
from tests.extraction.tree_sitter_engine.test_consolidate_classes_free_functions import (
    VENDORED_DIR,
    verify_vendored_snapshot,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PILOTS = ("cells_cpp", "pdf_cpp", "slides_cpp", "email_cpp")


@cache
def _fixture(pilot: str) -> dict[str, Any]:
    return json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))


def _entries(pilot: str) -> list[dict[str, Any]]:
    return _fixture(pilot)["types"]


# --- the independent text oracle ------------------------------------------------------------------------------

_TYPE_RE = re.compile(
    r"^(?:enum\s+(?:class|struct)\s+|enum\s+|class\s+|struct\s+)(?:\[\[[^\]]*\]\]\s*)?(\w+)\s*(?:final\s*)?(?::(?!:)[^{;]*)?\{"
)
_TOP_CLASS_RE = re.compile(
    r"\b(class|struct)\s+(?:\w+\s+)*?(\w+(?:::\w+)*)\s*(?:final\s*)?(?::(?!:)[^{;]*)?\{"
)


def _body_statements(body: str, default_access: str) -> list[tuple[str, str]]:
    """``(access, statement)`` for each depth-0 statement of a class body."""
    access = default_access
    found: list[tuple[str, str]] = []
    for statement in gate._statements(body):
        match = gate._ACCESS_RE.match(statement)
        if match:
            access = match.group(1)
            statement = statement[match.end() :]
        if statement.strip():
            found.append((access, statement.strip()))
    return found


def _is_static_const_data_member(statement: str) -> bool:
    """``static [inline] const|constexpr <type> <name> [= value]`` -- a data member, not a function."""
    head = statement.split("=", 1)[0]
    if "(" in head or ")" in head:
        return False
    words = re.findall(r"[A-Za-z_]\w*", head)
    return "static" in words and ("const" in words or "constexpr" in words)


def public_text_facts(text: str) -> dict[str, list[str]]:
    """``{"static_const": ["Class.member", ...], "nested": ["Outer::Inner", ...]}`` of the PUBLIC part of one header."""
    cleaned = gate._clean_text(text)
    static_const: list[str] = []
    nested: list[str] = []

    def walk(name: str, body: str, kind: str) -> None:
        for access, statement in _body_statements(body, "public" if kind == "struct" else "private"):
            if access != "public":
                continue
            type_match = _TYPE_RE.match(statement)
            if type_match:
                inner = f"{name}::{type_match.group(1)}"
                nested.append(inner)
                if not statement.startswith("enum"):
                    open_index = statement.index("{", type_match.end() - 1)
                    close = gate._matching_brace(statement, open_index)
                    inner_kind = "struct" if statement.startswith("struct") else "class"
                    walk(inner, statement[open_index + 1 : close], inner_kind)
                continue
            if _is_static_const_data_member(statement):
                member = re.findall(r"[A-Za-z_]\w*", statement.split("=", 1)[0])[-1]
                static_const.append(f"{name}.{member}")

    taken_until = -1
    for match in _TOP_CLASS_RE.finditer(cleaned):
        if match.start() < taken_until or re.search(r"\benum\s+$", cleaned[: match.start()]):
            continue
        open_index = match.end() - 1
        close = gate._matching_brace(cleaned, open_index)
        taken_until = close
        name = match.group(2)
        if "::" in name:
            nested.append(name)  # `class A::Iterator { ... }`, defined after its enclosing class
            continue
        walk(name, cleaned[open_index + 1 : close], match.group(1))
    return {"static_const": static_const, "nested": nested}


def test_the_oracle_finds_static_const_members_only_in_public_sections() -> None:
    header = """
class A {
public:
    static const int One = 1;
    static constexpr double Two = 2.0;
    static inline const std::string Name = "n";
    static int counter;
    static int helper(int x);
    const int plain = 0;
private:
    static const int Hidden = 3;
};
struct B {
    static constexpr const char* Key = "k";
};
"""
    assert public_text_facts(header)["static_const"] == ["A.One", "A.Two", "A.Name", "B.Key"]


def test_the_oracle_finds_public_nested_types_at_every_depth() -> None:
    header = """
class A {
public:
    struct P { struct Q {}; };
    enum class Kind { X };
    class Fwd;
private:
    struct Hidden {};
};
class A::Fwd { public: int v; };
struct S { class T {}; };
enum class Top { Y };
"""
    assert public_text_facts(header)["nested"] == ["A::P", "A::P::Q", "A::Kind", "A::Fwd", "S::T"]


def test_the_oracle_ignores_comments_and_string_literals() -> None:
    header = '// struct Fake { };\nclass A { public: /* static const int Nope = 1; */ static const char* K = "struct Z {};"; };\n'
    assert public_text_facts(header) == {"static_const": ["A.K"], "nested": []}


# --- the vendored cells snapshot, audited live ---------------------------------------------------------------


def _hidden(relative: str) -> bool:
    parts = relative.split("/")
    return (
        parts[-1].endswith((".cpp", ".cc"))
        or any(p.lstrip("_").lower() in ("internal", "detail") for p in parts[:-1])
        or not parts[-1].endswith((".h", ".hpp"))
    )


@cache
def _snapshot_types() -> list[dict[str, Any]]:
    verify_vendored_snapshot(VENDORED_DIR)
    pkg_root = package_root.detect_package_root(VENDORED_DIR, "cpp")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", pkg_root, VENDORED_DIR, "cells")
    return types


@cache
def _snapshot_facts() -> dict[str, list[str]]:
    facts: dict[str, list[str]] = {"static_const": [], "nested": []}
    for path in sorted(VENDORED_DIR.rglob("*")):
        relative = path.relative_to(VENDORED_DIR).as_posix()
        if _hidden(relative) or not path.is_file():
            continue
        for key, values in public_text_facts(path.read_text(encoding="utf-8", errors="replace")).items():
            facts[key].extend(values)
    return facts


def test_cells_snapshot_every_static_const_member_is_served_read_only_with_its_qualifiers() -> None:
    expected = sorted(_snapshot_facts()["static_const"])
    served = sorted(
        f"{t['name'].rsplit('::', 1)[-1]}.{p['name']}"
        for t in _snapshot_types()
        for p in t["properties"]
        if "static" in p.get("modifiers", []) and ("const" in p["modifiers"])
    )
    assert expected, "the oracle read the snapshot"
    assert served == expected
    assert all(
        p["writable"] is False
        for t in _snapshot_types()
        for p in t["properties"]
        if "const" in p.get("modifiers", [])
    )


def test_cells_snapshot_every_public_nested_type_is_served() -> None:
    expected = sorted(_snapshot_facts()["nested"])
    served = sorted(
        t["name"] for t in _snapshot_types() if "::" in t["name"] and t["kind"] != "alias_declaration"
    )
    assert expected, "the oracle read the snapshot"
    assert served == expected


def test_cells_snapshot_serves_no_type_declared_only_in_a_source_file() -> None:
    types = _snapshot_types()
    assert [t["name"] for t in types if not t["file"].endswith((".h", ".hpp"))] == []
    names = {t["name"] for t in types}
    # the .cpp-local classes of the snapshot (hand-read from src/): none is a public type
    assert names.isdisjoint(
        {"BitReader", "HuffmanTable", "ParsedNumericFormat", "XmlParser", "Workbook::Impl"}
    )


# --- the four regenerated fixtures ---------------------------------------------------------------------------


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_fixture_carries_the_current_extractor_keys(pilot: str) -> None:
    fixture = _fixture(pilot)
    assert fixture["extractor_version"] == 5
    assert set(fixture["symbol_counts"]) == {"types", "functions"}
    assert set(fixture["counts"]) == {"types", "functions", "methods"}
    assert "parse_failures" in fixture
    assert fixture["truncated"] is False
    assert fixture["reduced_type_count"] == fixture["type_count"] == len(fixture["types"])


@pytest.mark.parametrize("pilot", PILOTS)
def test_no_entry_is_implementation(pilot: str) -> None:
    for entry in _entries(pilot):
        parts = entry["file"].split("/")
        assert not entry["file"].endswith((".cpp", ".cc")), entry["name"]
        assert not any(p.lstrip("_").lower() in ("internal", "detail") for p in parts[:-1]), entry["name"]
        segments = [s.lstrip("_").lower() for s in re.split(r"::", entry.get("class_import", ""))]
        assert "internal" not in segments and "detail" not in segments, entry["class_import"]
        assert entry.get("visibility", "public") != "internal", entry["name"]


# slides ---------------------------------------------------------------------------------------------------------


def test_slides_color_constants_are_static_const_and_read_only() -> None:
    color = next(
        t for t in _entries("slides_cpp") if t["class_import"] == "Aspose::Slides::Foss::Drawing::Color"
    )
    constants = [p for p in color["properties"] if p.get("modifiers") == ["static", "const"]]
    # upstream drawing/color.h declares 141 `static const Color <name>;` lines (counted by hand); the round-3
    # audit's "about 286" is every static const member of the repository, 145 more of them in the _internal/ headers
    assert len(constants) == 141
    assert all(p["writable"] is False and p["type"] == "Color" for p in constants)
    assert {p["name"] for p in constants} >= {"alice_blue", "black", "white", "yellow_green"}
    assert len(color["properties"]) == 141  # and nothing else in the class is a data member


def test_slides_no_type_or_function_of_the_internal_namespace_is_served() -> None:
    entries = _entries("slides_cpp")
    assert [e["class_import"] for e in entries if "Internal::" in e["class_import"]] == []
    assert [e["file"] for e in entries if "_internal/" in e["file"]] == []
    # the old fixture held 55 (30 types and 25 functions), 28 of the types labelled public
    assert len(entries) == 326


def test_slides_public_nested_iterator_classes_and_aliases_are_served() -> None:
    names = {t["name"] for t in _entries("slides_cpp")}
    for outer in ("GlobalLayoutSlideCollection", "MasterLayoutSlideCollection"):
        assert {f"{outer}::iterator", f"{outer}::const_iterator"} <= names
    for outer in ("ParagraphCollection", "PortionCollection"):
        assert {f"{outer}::Iterator", f"{outer}::ConstIterator"} <= names
    # `using iterator = std::vector<...>::iterator;` in the nine collections that declare it, with its const pair
    aliases = sorted(t["name"] for t in _entries("slides_cpp") if t["kind"] == "alias_declaration")
    assert len(aliases) == 18
    assert "SlideCollection::iterator" in aliases and "SlideCollection::const_iterator" in aliases


# pdf ------------------------------------------------------------------------------------------------------------

PDF_NESTED = {
    "Aspose::Pdf::Annotations::RichMediaAnnotation::ActivationEvent",
    "Aspose::Pdf::Annotations::RichMediaAnnotation::ContentType",
    "Aspose::Pdf::BitmapInfo::PixelFormat",
    "Aspose::Pdf::Document::OutlineNode",
    "Aspose::Pdf::Document::SignatureInfo",
    "Aspose::Pdf::Document::SignatureRequest",
    "Aspose::Pdf::Document::TextStampReq",
    "Aspose::Pdf::Facades::PdfFileEditor::ConcatenateCorruptedFileAction",
    "Aspose::Pdf::Facades::PdfFileEditor::ContentsResizeParameters",
    "Aspose::Pdf::Facades::PdfFileEditor::ContentsResizeValue",
    "Aspose::Pdf::Facades::PdfFileEditor::CorruptedItem",
    "Aspose::Pdf::Facades::PdfFileEditor::PageBreak",
    "Aspose::Pdf::Forms::Form::FlattenSettings",
    "Aspose::Pdf::Forms::Form::SignDependentElementsRenderingModes",
}


def test_pdf_nested_types_are_served() -> None:
    served = {t["class_import"] for t in _entries("pdf_cpp") if t["name"].count("::") >= 1}
    # 21 nested definitions in the headers (a tree-sitter count), 7 of them private or protected: 14 are public
    # (the text oracle, run over every header of the clone at the pin, finds the same 14)
    assert served == PDF_NESTED
    editor = next(t for t in _entries("pdf_cpp") if t["name"] == "PdfFileEditor::ContentsResizeParameters")
    assert editor["kind"] == "class_specifier"
    assert sum(1 for m in editor["methods"] if m["is_constructor"]) == 1
    # one constructor and eight getter/setter pairs, pdf_file_editor.hpp:110-136
    assert len(editor["methods"]) == 17


def test_pdf_static_const_members_keep_their_qualifiers() -> None:
    constants = [
        (t["name"], p)
        for t in _entries("pdf_cpp")
        for p in t["properties"]
        if "static" in p.get("modifiers", []) and "const" in p["modifiers"]
    ]
    # 50 `static constexpr` and 6 `static inline const std::string` in the public headers
    assert len(constants) == 56
    assert sum(1 for _, p in constants if "constexpr" in p["modifiers"]) == 50
    assert all(p["writable"] is False for _, p in constants)


def test_pdf_nothing_under_include_internal_is_served() -> None:
    entries = _entries("pdf_cpp")
    assert [e["file"] for e in entries if "/internal/" in "/" + e["file"]] == []
    assert len(entries) == 282  # 359 before: the 91 declarations of include/internal/ are gone


# cells ----------------------------------------------------------------------------------------------------------

CELLS_POINTER_CONSTANTS = {
    ("DefinedNameUtility", "PrintAreaDefinedName"),
    ("DefinedNameUtility", "PrintTitlesDefinedName"),
    ("DefinedNameUtility", "FilterDatabaseDefinedName"),
    ("PackagingConventions", "ContentTypesPart"),
    ("PackagingConventions", "RootRelationshipsPart"),
    ("PackagingConventions", "WorkbookPart"),
    ("PackagingConventions", "WorkbookRelationshipsPart"),
    ("PackagingConventions", "OfficeDocumentRelationship"),
    ("PackagingConventions", "WorksheetRelationship"),
    ("PackagingConventions", "StylesRelationship"),
    ("PackagingConventions", "SharedStringsRelationship"),
}


def test_cells_the_public_static_constexpr_pointer_constants_the_audit_found_missing_are_served() -> None:
    served = {
        (t["name"], p["name"]): p
        for t in _entries("cells_cpp")
        for p in t["properties"]
        if p.get("modifiers") == ["static", "constexpr", "const"]
    }
    assert set(served) == CELLS_POINTER_CONSTANTS
    assert all(p["type"] == "const char*" and p["writable"] is False for p in served.values())
    # the other three of the audit's 14 are private (PageSetup.h:202, XlsxWorkbookPageSetup.h:68-69): not served
    all_names = {p["name"] for t in _entries("cells_cpp") for p in t["properties"]}
    assert all_names.isdisjoint({"kCentimetersPerInch", "MaxSpreadsheetRow", "MaxSpreadsheetColumn"})


def test_cells_classes_that_exist_only_in_cpp_files_are_not_served() -> None:
    entries = _entries("cells_cpp")
    assert [e["file"] for e in entries if not e["file"].endswith(".h")] == []
    # the eight class/struct definitions of src/**/*.cpp at the pin (BitReader, CdEntry, HuffmanTable, NsScope,
    # ParsedNumericFormat, TempDir, XmlParser, Workbook::Impl): none is a public type
    names = {e["name"] for e in entries}
    assert names.isdisjoint(
        {
            "BitReader",
            "CdEntry",
            "HuffmanTable",
            "NsScope",
            "ParsedNumericFormat",
            "TempDir",
            "XmlParser",
            "Workbook::Impl",
        }
    )
    assert len(entries) == 196


def test_cells_xml_element_nested_struct_is_served() -> None:
    names = {e["class_import"] for e in _entries("cells_cpp")}
    assert "Aspose::Cells_FOSS::XmlNodeData::Attr" in names


# email ----------------------------------------------------------------------------------------------------------


def test_email_is_reextracted_with_the_current_extractor_and_keeps_its_members() -> None:
    entries = _entries("email_cpp")
    # 26 before (extractor_version absent), plus the public nested enum cfb_node::kind
    assert len(entries) == 27
    assert "cfb_node::kind" in {e["name"] for e in entries}
    constants = [
        p
        for e in entries
        for p in e["properties"]
        if "static" in p.get("modifiers", []) and "const" in p["modifiers"]
    ]
    assert len(constants) == 6  # mapi_message.hpp:23-28, `static constexpr` (six declarations)
    assert all(p["writable"] is False and "constexpr" in p["modifiers"] for p in constants)
    assert sum(1 for e in entries for m in e["methods"] if m["is_constructor"]) >= 12


# --- member-level fidelity (RD3-023): the committed text audit against the regenerated fixtures ---------------

# `test_cpp_fidelity_gate.audit_header_text` summed over the headers each fixture serves, at the chart pin.
AUDIT = {
    "cells_cpp": {
        "constructors": 159,
        "static": 188,
        "const": 790,
        "ref_ptr_params": 359,
        "ref_ptr_returns": 342,
        "methods": 1917,
    },
    "pdf_cpp": {
        "constructors": 345,
        "static": 195,
        "const": 791,
        "ref_ptr_params": 653,
        "ref_ptr_returns": 299,
        "methods": 2398,
    },
    "slides_cpp": {
        "constructors": 138,
        "static": 11,
        "const": 1156,
        "ref_ptr_params": 269,
        "ref_ptr_returns": 829,
        "methods": 2787,
    },
    "email_cpp": {
        "constructors": 12,
        "static": 28,
        "const": 76,
        "ref_ptr_params": 40,
        "ref_ptr_returns": 60,
        "methods": 163,
    },
}


@pytest.mark.parametrize("pilot", PILOTS)
def test_member_counters_match_the_text_audit_within_five_percent(pilot: str) -> None:
    extracted = gate.extractor_counters(_entries(pilot))
    assert gate.disagreements(AUDIT[pilot], extracted) == [], (AUDIT[pilot], extracted)


@pytest.mark.parametrize("pilot", PILOTS)
def test_the_member_gate_rejects_a_fixture_without_constructors(pilot: str) -> None:
    stripped = [
        {**e, "methods": [m for m in e.get("methods", []) if not m.get("is_constructor")]}
        for e in _entries(pilot)
    ]
    failures = gate.disagreements(AUDIT[pilot], gate.extractor_counters(stripped))
    assert any(f.startswith("constructors:") for f in failures), failures


@pytest.mark.parametrize("pilot", PILOTS)
def test_every_static_const_member_of_a_fixture_is_read_only(pilot: str) -> None:
    for entry in _entries(pilot):
        for prop in entry["properties"]:
            modifiers = prop.get("modifiers", [])
            if "const" in modifiers:
                assert prop["writable"] is False, (entry["name"], prop["name"])
            if "static" in modifiers and "const" not in modifiers:
                assert prop["writable"] is True, (entry["name"], prop["name"])
