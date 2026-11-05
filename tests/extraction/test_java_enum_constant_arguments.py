"""G3/TC-689 (REQ-G3-012, O5): a Java enum constant declared with constructor arguments must
not be served with an empty ``value``.

REPRODUCED directly against the real production extractor (not guessed from the plan's
prose): ``tree_helpers.py``'s ``_extract_enum_members`` reads only a node's ``value`` field
for every language uniformly, but tree-sitter-java's ``enum_constant`` grammar node has no
``value`` field at all - only ``name``, ``arguments`` and ``body``. The lookup always missed,
so a constant declared with constructor arguments (``RED(255, 0, 0)``) was always served with
``"value": ""`` despite the real ``argument_list`` node being present in the parse tree, across
all 4 real Java pilots with committed ``enum_members`` fixtures (2,477 members total, zero
exceptions).

Every test here calls the real ``api_surface.extract_api_surface`` entrypoint on a hand-authored
Java source - never the private ``_extract_enum_members`` helper directly, and never a committed
fixture - so this is a genuine extraction smoke test, not a unit test of implementation details.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface


def _extract(tmp_path: Path, source: str) -> dict[str, dict]:
    """The extracted types of one source file, by simple name (same convention as
    ``tests/extraction/test_round3_java_fidelity.py``'s own ``_extract`` helper)."""
    package = tmp_path / "src" / "p"
    package.mkdir(parents=True)
    (package / "Sample.java").write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("java"), "java", package, tmp_path, "widget")
    return {t["name"]: t for t in types}


def test_a_constant_with_constructor_arguments_carries_its_real_argument_text(tmp_path: Path) -> None:
    # RED(255, 0, 0) declares a real argument_list in the parse tree; before this fix it was
    # always dropped and served as "value": "".
    types = _extract(
        tmp_path,
        """
package p;
public enum Color {
    RED(255, 0, 0);

    Color(int r, int g, int b) {}
}
""",
    )
    members = {m["name"]: m for m in types["Color"]["enum_members"]}
    assert members["RED"]["value"] == "(255, 0, 0)"


def test_a_sibling_constant_with_no_arguments_in_the_same_enum_stays_empty(tmp_path: Path) -> None:
    # Negative case in the same enum, same extraction call: proves the fix is additive to the
    # one constant that actually has arguments, not a blanket change to every member.
    types = _extract(
        tmp_path,
        """
package p;
public enum Color {
    RED(255, 0, 0), PLAIN;

    Color() {}
    Color(int r, int g, int b) {}
}
""",
    )
    members = {m["name"]: m for m in types["Color"]["enum_members"]}
    assert members["RED"]["value"] == "(255, 0, 0)"
    assert members["PLAIN"]["value"] == ""


def test_a_constant_with_an_empty_argument_list_stays_empty(tmp_path: Path) -> None:
    # PLAIN() with explicit-but-empty parens must not synthesize a fake non-empty value either.
    types = _extract(
        tmp_path,
        """
package p;
public enum Color {
    PLAIN();

    Color() {}
}
""",
    )
    members = {m["name"]: m for m in types["Color"]["enum_members"]}
    assert members["PLAIN"]["value"] == ""


def test_a_plain_enum_with_only_parameterless_constants_is_unaffected(tmp_path: Path) -> None:
    # The common case, modeled on the real pdf_java fixture's HorizontalAlignment (Left,
    # Right, Center - no constructor arguments on any constant): extracted live here (not read
    # from the committed tests/fixtures/pdf_java/api_surface.json), every member's value must
    # still be "", exactly as before this fix.
    types = _extract(
        tmp_path,
        """
package p;
public enum HorizontalAlignment {
    Left,
    Right,
    Center
}
""",
    )
    members = {m["name"]: m for m in types["HorizontalAlignment"]["enum_members"]}
    assert {"Left", "Right", "Center"} == set(members)
    assert all(m["value"] == "" for m in members.values())
