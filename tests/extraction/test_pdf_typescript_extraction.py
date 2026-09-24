"""Offline tests against the committed pdf/typescript fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-050 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/pdf_typescript/api_surface.json``.

TypeScript's extraction path had never been exercised against a real cloned repository
through the actual extraction entrypoint before this card - the last remaining untested
tree-sitter language besides cpp. Attempt 1 of this card correctly stopped without
committing: ``api_surface.py`` was fabricating bogus top-level "function" entries from
inline TypeScript object-type-literal properties. That defect was fixed and accepted
separately as TC-051 (commit b9a398242d8c), and this attempt re-runs the real extraction
now that the blocker is gone.

The observed ``kind`` vocabulary among the reduced fixture is ``{"function", "constant"}``
- this repository's public surface is dominated by module-level helper functions and
exported constants rather than classes/interfaces (unlike pdf/java's
class/enum/interface-heavy surface). ``reduce_fixture`` sorts by name, and because
SCREAMING_SNAKE_CASE constant names and short function names sort before most
PascalCase/mixed-case identifiers under plain string ordering, the first 150
(alphabetically) entries of this particular repository happen to contain no
class/interface entries at all - this was verified directly against the live run, not
assumed.

Each name asserted on below was independently checked against the real cloned source at
the pinned commit (see the inline citations) to confirm it is a genuine top-level
``export function`` / ``export const`` declaration - NOT a property key of an inline
TypeScript object-type literal (the exact class of fabrication TC-051 fixed). For
example ``AES_WRAP_OID`` is ``export const AES_WRAP_OID: Record<number, string> = {...}``
at ``src/cmskdf.ts:30`` - a real top-level constant assignment, not a type-literal member.

The real repository has 4211 public types - far over the CLI's default ``--max-types
300`` cap - so this fixture was generated with an explicit smaller ``--max-types 150``,
landing at 58,114 bytes, comfortably under the ~2MB budget. ``truncated`` is ``True`` and
``reduced_type_count`` is 150, not the full 4211.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_typescript" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "pdf" / "typescript.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "typescript"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-TypeScript` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "155bfc7a33f0ba23fb4252b6ba201828b02a5b9d"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty_and_records_real_truncation() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has 4211 public types - far over the CLI's default --max-types 300
    # cap - so this fixture was generated with an explicit smaller --max-types 150.
    assert data["type_count"] == 4211
    assert data["reduced_type_count"] == 150
    assert data["truncated"] is True


def test_the_fixture_contains_real_typescript_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.PDF-FOSS-for-TypeScript exported declarations, not placeholders - would not
    # survive an empty or synthetic fixture. Each was independently verified against the live
    # checkout at the pinned commit to be a genuine top-level `export function`/`export const`
    # declaration - e.g. `export const AES_WRAP_OID: Record<number, string> = {...}` at
    # src/cmskdf.ts:30, and `export const ANGLE_EPS = 0.005;` at src/tableorient.ts:4 - and NOT
    # a property key of an inline object-type literal (the exact class of fabrication TC-051
    # fixed - see this module's docstring).
    assert {"AES_WRAP_OID", "ANGLE_EPS", "ALPHA", "PDF17_NS"} <= names
    assert all(entry["file"].endswith(".ts") for entry in data["types"])


def test_every_entry_is_a_real_typescript_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live run over this repository's real reduced (alphabetically
    # first 150) public surface, not guessed or invented: module-level functions and exported
    # constants dominate this repository's early-alphabet surface, so no class/interface entry
    # falls inside this particular reduced subset.
    assert kinds == {"function", "constant"}


def test_common_fields_are_present_on_every_entry() -> None:
    data = _load_fixture()
    for entry in data["types"]:
        for key in (
            "name",
            "kind",
            "file",
            "line",
            "doc",
            "methods",
            "properties",
            "bases",
            "reachable",
        ):
            assert key in entry, (entry.get("name"), key)


def test_a_real_function_and_a_real_constant_are_present_with_real_fields() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    # `A` is a real top-level helper function in src/svgfilterlight.ts - confirmed in the live
    # checkout at `function A(a: Float32Array, w: number, h: number, x: number, y: number):
    # number {` (line 11). It is NOT a property of any object-type literal.
    func = by_name["A"]
    assert func["kind"] == "function"
    assert func["file"] == "src/svgfilterlight.ts"
    assert func["return_type"] == "number"
    param_names = [p["name"] for p in func["params"]]
    assert param_names == ["a", "w", "h", "x", "y"]

    # `AES_WRAP_OID` is a real top-level exported constant in src/cmskdf.ts - confirmed in the
    # live checkout at `export const AES_WRAP_OID: Record<number, string> = {...}` (line 30).
    # It is a genuine const declaration, NOT an inline object-type-literal member.
    const = by_name["AES_WRAP_OID"]
    assert const["kind"] == "constant"
    assert const["file"] == "src/cmskdf.ts"
    assert const["type"] == "Record<number, string>"

    # `PDF17_NS` is genuinely reachable: confirmed in the live checkout that src/index.ts
    # re-exports it directly (`export { PDF17_NS, PDF20_NS, MATHML_NS } from './structns.js';`),
    # making it part of the package's actual public surface rather than an internal helper.
    reachable_const = by_name["PDF17_NS"]
    assert reachable_const["kind"] == "constant"
    assert reachable_const["reachable"] is True

    # `__canon` is a real internal (non-reachable) helper - present in the fixture but never
    # re-exported from src/index.ts.
    internal = by_name["__canon"]
    assert internal["kind"] == "function"
    assert internal["reachable"] is False
