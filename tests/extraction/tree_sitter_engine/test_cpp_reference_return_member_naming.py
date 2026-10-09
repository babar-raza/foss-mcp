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
