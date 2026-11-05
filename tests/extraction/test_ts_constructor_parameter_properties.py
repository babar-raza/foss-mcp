"""G3/TC-690 (REQ-G3-012, O6): a TypeScript constructor-parameter-property must be served as a
real class property, not silently dropped.

REPRODUCED directly against the real production extractor (not guessed from the plan's prose):
a hand-authored class with a parameter-property constructor
(``constructor(public x: number, private secret: string, readonly z: boolean) {}``) extracted via
the real ``api_surface.extract_api_surface`` entrypoint with ``properties: []`` - completely
empty - even though real TypeScript semantics make ``public x`` and ``readonly z`` genuine public
instance properties (``instance.x``, ``instance.z``), on a par with an ordinary ``x: number;``
field declaration. Root cause: the class-member loop in ``api_surface.py`` only ever iterates the
class body's direct children; a parameter property lives entirely inside the constructor's own
parameter list, a branch the member loop never descended into.

Every test here calls the real ``api_surface.extract_api_surface`` entrypoint on a hand-authored
TypeScript source - never a private helper directly, and never a committed fixture - so this is a
genuine extraction smoke test, not a unit test of implementation details.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface


def _extract(tmp_path: Path, source: str) -> dict[str, dict]:
    """The extracted types of one TypeScript source file, by simple name (the exact calling
    convention ``tests/extraction/tree_sitter_engine/test_ts_fidelity.py`` already uses for the
    real ``extract_api_surface`` entrypoint)."""
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    (root / "index.ts").write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("typescript"), "typescript", root, root, "x")
    return {t["name"]: t for t in types}


SOURCE = """
export class Foo {
  constructor(
    public x: number,
    private secret: string,
    readonly z: boolean,
    protected other: string,
    w: number,
  ) {
    this.log(w);
  }

  log(w: number): void {}
}
"""


def test_public_and_bare_readonly_constructor_parameters_become_properties(tmp_path: Path) -> None:
    # public x and bare-readonly z are real public instance members by TypeScript's own rules -
    # before this fix, `properties` was completely empty for this class.
    types = _extract(tmp_path, SOURCE)
    props = {p["name"]: p for p in types["Foo"]["properties"]}
    assert props["x"]["type"] == "number"
    assert props["x"]["writable"] is True
    assert props["z"]["type"] == "boolean"
    assert props["z"]["writable"] is False


def test_private_and_protected_constructor_parameters_are_excluded(tmp_path: Path) -> None:
    # Negative case, same extraction call: private/protected parameter properties are real
    # parameter properties at the language level, but are not part of the public API surface -
    # mirrors the existing ordinary-field private/protected exclusion in tree_helpers.is_public().
    types = _extract(tmp_path, SOURCE)
    props = {p["name"]: p for p in types["Foo"]["properties"]}
    assert "secret" not in props
    assert "other" not in props


def test_a_plain_non_property_constructor_parameter_is_not_synthesized(tmp_path: Path) -> None:
    # w has neither an accessibility modifier nor a bare `readonly` - it is a completely ordinary
    # constructor parameter, used only inside the constructor's own body, and must not become a
    # property of any kind.
    types = _extract(tmp_path, SOURCE)
    props = {p["name"]: p for p in types["Foo"]["properties"]}
    assert "w" not in props
    assert set(props) == {"x", "z"}


def test_the_constructors_own_params_reporting_is_unaffected(tmp_path: Path) -> None:
    # The constructor's own `methods` entry (its `params` list) must still list every parameter,
    # property or not, exactly as it did before this change - this card only ADDS entries to
    # `properties`, it does not change how the constructor itself is reported.
    types = _extract(tmp_path, SOURCE)
    ctor = next(m for m in types["Foo"]["methods"] if m["name"] == "constructor")
    assert [p["name"] for p in ctor["params"]] == ["x", "secret", "z", "other", "w"]
    assert ctor["is_constructor"] is True


def test_an_ordinary_public_private_field_declaration_class_is_unaffected(tmp_path: Path) -> None:
    # The existing, already-correct case (quoted in this card's own purpose text): a class with NO
    # parameter-property constructor at all, just ordinary private/public field declarations, must
    # keep extracting exactly as it did before this change - only `visible` is public.
    types = _extract(
        tmp_path,
        """
export class Bar {
  private secret: number = 1;
  public visible: string = "a";
}
""",
    )
    props = {p["name"]: p for p in types["Bar"]["properties"]}
    assert set(props) == {"visible"}
