"""TC-259: a C++ class member whose return type is a reference/pointer to a
class type must never be misnamed after that return type.

ROOT CAUSE (C1 of the third independent audit, 2026-10-08 -- see
docs/DECISION_LOG.md's entry of that date): tree_helpers.py's _node_name()
carried a comment (written by TC-058) claiming a C++ class member's
function_definition was "deliberately left returning '' here" so that
api_surface.py's dedicated field_declaration_list member loop could name and
extract members independently, without the generic, language-agnostic
method-collection loop (which also visits class members) fabricating a
second, duplicate/phantom entry for the same inline-bodied method. The code
never actually did this -- it only special-cased genuine free functions
(gated by _cpp_is_free_function) and otherwise fell through to the generic
"first identifier-or-type_identifier direct child" fallback. For a member
whose return type is a reference or pointer to a class type (e.g. the real
``Aspose::Cells_FOSS::Borders::GetLeft`` -> ``const Border& GetLeft() const
noexcept``), tree-sitter-cpp places a bare ``type_identifier`` node for the
return type ("Border") as a direct child of the function_definition, one
level above the reference_declarator/function_declarator holding the real
name ("GetLeft") -- so the fallback matched the return-type node first and
returned it as the method's "name". The generic method loop then appended a
phantom method named after the return type (with that same type as its own
return_type), and _synthesize_cpp_properties turned that same phantom method
into a phantom property too.

This file reproduces that exact shape (a class closely mirroring the real
Borders.h: a reference-returning const accessor, its setter, a
value-returning accessor/setter pair, and a constructor) and proves the fix:
no method or property is ever named after its own zero-parameter return
type, while every real member is still correctly extracted. A second test
proves the free-function path (already correct, untouched by this card) is
completely unaffected by a reference return type at namespace/global scope.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

# Mirrors the real Aspose::Cells_FOSS::Borders shape closely enough to be a
# faithful regression (real class/method names, real reference-return-type
# accessor pattern) without reproducing the whole real header.
CPP_MEMBER_REFERENCE_RETURN = """
class Border {
public:
    Border() {}
};

class Borders {
public:
    Borders() {}

    const Border& GetLeft() const noexcept {
        return _left;
    }

    void SetLeft(const Border& value) noexcept {
        _left = value;
    }

    bool GetDiagonalUp() const noexcept {
        return _diagonalUp;
    }

    void SetDiagonalUp(bool value) noexcept {
        _diagonalUp = value;
    }

private:
    Border _left;
    bool _diagonalUp;
};
"""

# A genuine namespace-scope free function with a reference-returning
# signature, mirroring TC-058's own original real-world case
# (`constexpr Matrix Identity() noexcept`, a by-value bare-identifier return)
# but with a reference return type instead, which is this card's exact bug
# shape for the MEMBER case -- this proves the free-function path (already
# correctly handled by _cpp_is_free_function/_cpp_free_function_name) is
# untouched by the new member-case branch.
CPP_FREE_FUNCTION_REFERENCE_RETURN = """
class Matrix {
public:
    Matrix() {}
};

const Matrix& Identity() {
    static Matrix instance;
    return instance;
}
"""


def _extract(tmp_path: Path, source: str, family: str):
    package = tmp_path / "src"
    package.mkdir()
    (package / "widget.cpp").write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("cpp"), "cpp", package, tmp_path, family)
    return types


def test_cpp_member_with_reference_return_type_is_never_named_after_its_return_type(
    tmp_path: Path,
) -> None:
    types = _extract(tmp_path, CPP_MEMBER_REFERENCE_RETURN, "borders_test")
    by_name = {t["name"]: t for t in types}

    assert "Borders" in by_name
    borders = by_name["Borders"]
    method_names = {m["name"] for m in borders["methods"]}
    property_names = {p["name"] for p in borders["properties"]}

    # The exact TC-259 bug: no method or property is ever named exactly
    # after its own zero-parameter return type.
    assert "Border" not in method_names, method_names
    assert "Border" not in property_names, property_names

    # The real reference-returning getter is still correctly extracted under
    # its real name, with its real return type.
    methods_by_name = {m["name"]: m for m in borders["methods"]}
    assert "GetLeft" in methods_by_name
    assert "Border" in methods_by_name["GetLeft"]["return_type"]

    # The real setter, constructor, and the real value-returning
    # getter/setter pair are all still correctly extracted unchanged.
    assert "SetLeft" in method_names
    assert "Borders" in method_names  # constructor
    assert "GetDiagonalUp" in method_names
    assert "SetDiagonalUp" in method_names


def test_cpp_free_function_with_reference_return_type_is_unaffected(tmp_path: Path) -> None:
    types = _extract(tmp_path, CPP_FREE_FUNCTION_REFERENCE_RETURN, "identity_test")
    by_name = {t["name"]: t for t in types}

    # The free function resolves to its real name, not its return type.
    assert "Identity" in by_name
    assert by_name["Identity"]["kind"] == "function"
    assert "Matrix" not in by_name or by_name["Matrix"]["kind"] != "function"


# TC-276: the identical C1 fabrication shape recurs for an OUT-OF-LINE,
# qualified member definition (`ReturnType ClassName::Method(...) { ... }`
# in a .cpp file) -- textually outside any class body, so
# _cpp_is_free_function()'s ancestor walk never passes through a
# field_declaration_list and (before this card's fix) misreported it as a
# genuine free function. _cpp_free_function_name() then couldn't find a name
# either (the function_declarator's name child is a qualified_identifier,
# e.g. `Worksheet::GetWorkbook`, a node type it doesn't check for), so
# _node_name() fell through to the generic return-type fallback and
# fabricated a phantom top-level entry named after the return type -- live-
# confirmed against cells/cpp as a phantom top-level "Workbook" entry for
# `Workbook& Worksheet::GetWorkbook()` (see docs/DECISION_LOG.md, 2026-10-09).
# Each class below carries its real header-style member *declaration*
# (no body) so the real member is still correctly captured by
# api_surface.py's dedicated field_declaration_list member loop, exactly as
# TC-276 confirmed is already the case for every real out-of-line definition.

CPP_OUT_OF_LINE_REFERENCE_RETURN_MEMBER = """
class Workbook {
public:
    Workbook() {}
};

class Worksheet {
public:
    Worksheet() {}
    Workbook& GetWorkbook();

private:
    Workbook* _workbook;
};

Workbook& Worksheet::GetWorkbook() {
    return *_workbook;
}
"""

CPP_OUT_OF_LINE_VALUE_RETURN_MEMBER = """
enum class OperatorType { Equal, NotEqual };

class FormatCondition {
public:
    FormatCondition() {}
    OperatorType GetOperator();

private:
    OperatorType _operator;
};

OperatorType FormatCondition::GetOperator() {
    return _operator;
}
"""

# A genuine out-of-line CONSTRUCTOR -- its qualified_identifier's final
# component ("Foo") legitimately equals the class name, and (unlike the
# reference/value-return cases above) a constructor has no return type to be
# misnamed after in the first place. This proves the existing constructor
# path -- already unaffected by the original bug, since there is no bare
# return-type child for the fallback to seize on -- is also unaffected by
# this card's fix: still no phantom second "Foo" entry, and the real
# constructor is still correctly captured from the header declaration.
CPP_OUT_OF_LINE_CONSTRUCTOR = """
class Foo {
public:
    Foo(int x);

private:
    int _x;
};

Foo::Foo(int x) : _x(x) {}
"""


def test_cpp_out_of_line_reference_return_member_never_fabricates_phantom_type(
    tmp_path: Path,
) -> None:
    types = _extract(tmp_path, CPP_OUT_OF_LINE_REFERENCE_RETURN_MEMBER, "workbook_test")

    # The exact TC-276 bug: no phantom top-level FUNCTION entry is ever
    # fabricated named after the out-of-line member's return type. (The real
    # class "Workbook" legitimately exists in this source, so the check is
    # on kind, not bare presence.)
    assert not any(t["name"] == "Workbook" and t["kind"] == "function" for t in types), types

    by_name = {t["name"]: t for t in types}
    assert "Worksheet" in by_name
    worksheet = by_name["Worksheet"]
    methods_by_name = {m["name"]: m for m in worksheet["methods"]}

    # The real member is still correctly captured -- from the class's own
    # header-style declaration, exactly as TC-276 confirmed is already the
    # case -- under its real name, with its real return type.
    assert "GetWorkbook" in methods_by_name
    assert "Workbook" in methods_by_name["GetWorkbook"]["return_type"]


def test_cpp_out_of_line_value_return_member_never_fabricates_phantom_type(
    tmp_path: Path,
) -> None:
    types = _extract(tmp_path, CPP_OUT_OF_LINE_VALUE_RETURN_MEMBER, "format_condition_test")

    # The exact TC-276 bug for a VALUE return type: no phantom top-level
    # FUNCTION entry named "OperatorType".
    assert not any(t["name"] == "OperatorType" and t["kind"] == "function" for t in types), types

    by_name = {t["name"]: t for t in types}
    assert "FormatCondition" in by_name
    methods_by_name = {m["name"]: m for m in by_name["FormatCondition"]["methods"]}

    assert "GetOperator" in methods_by_name
    assert "OperatorType" in methods_by_name["GetOperator"]["return_type"]


def test_cpp_out_of_line_constructor_still_handled_correctly(tmp_path: Path) -> None:
    """A constructor has no return type, so the original C1/TC-276
    fabrication mechanism (misnaming a member after its bare return-type
    child) never had anything to seize on for this shape -- confirmed via
    a live parse probe that `Foo::Foo(int x)`'s function_definition has no
    bare identifier/type_identifier direct child at all. This was already
    true before this card's fix (the old, incorrectly-True
    _cpp_is_free_function() result still led to an empty name here, via a
    different route) and must remain true after it: exactly one top-level
    "Foo" entry -- the real class -- never a second, phantom one.
    """
    types = _extract(tmp_path, CPP_OUT_OF_LINE_CONSTRUCTOR, "foo_ctor_test")

    foo_entries = [t for t in types if t["name"] == "Foo"]
    assert len(foo_entries) == 1, types
    assert foo_entries[0]["kind"] != "function"


def test_cpp_out_of_line_member_fix_does_not_disturb_tc259_inline_and_free_function_cases(
    tmp_path: Path,
) -> None:
    """Regression check: combine TC-259's own inline-member and genuine-
    free-function sources with this card's new out-of-line-member shape in a
    single parse, and prove every TC-259 assertion still holds unchanged
    alongside the new fix -- the two code paths (ancestor-walk member check,
    new qualified_identifier declarator check) must not interact.
    """
    combined = (
        CPP_MEMBER_REFERENCE_RETURN
        + CPP_FREE_FUNCTION_REFERENCE_RETURN
        + CPP_OUT_OF_LINE_REFERENCE_RETURN_MEMBER
    )
    types = _extract(tmp_path, combined, "combined_test")
    by_name = {t["name"]: t for t in types}

    # TC-259's inline-member case: still correctly extracted, still never
    # misnamed after its return type.
    assert "Borders" in by_name
    borders = by_name["Borders"]
    method_names = {m["name"] for m in borders["methods"]}
    property_names = {p["name"] for p in borders["properties"]}
    assert "Border" not in method_names, method_names
    assert "Border" not in property_names, property_names
    methods_by_name = {m["name"]: m for m in borders["methods"]}
    assert "GetLeft" in methods_by_name
    assert "Border" in methods_by_name["GetLeft"]["return_type"]
    assert "SetLeft" in method_names
    assert "Borders" in method_names  # constructor
    assert "GetDiagonalUp" in method_names
    assert "SetDiagonalUp" in method_names

    # TC-259's genuine-free-function case: still resolves to its real name.
    assert "Identity" in by_name
    assert by_name["Identity"]["kind"] == "function"
    assert "Matrix" not in by_name or by_name["Matrix"]["kind"] != "function"

    # TC-276's new out-of-line-member case: still no phantom, alongside both
    # of the above.
    assert not any(t["name"] == "Workbook" and t["kind"] == "function" for t in types), types
    assert "Worksheet" in by_name
    worksheet_methods = {m["name"]: m for m in by_name["Worksheet"]["methods"]}
    assert "GetWorkbook" in worksheet_methods
    assert "Workbook" in worksheet_methods["GetWorkbook"]["return_type"]
