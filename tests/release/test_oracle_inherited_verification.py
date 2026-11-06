"""G3/TC-624 (O1 part 2, REQ-G3-012): verify an inherited baked member against its NAMED
ANCESTOR's own oracle symbol, instead of flagging it "extra" purely because it is absent from the
CURRENT class's own oracle entry.

Root cause (confirmed by reading the real code): ``diff_symbol``'s trailing three "extra" loops
used to treat EVERY baked member the same way, whether or not it carried ``inherited_from`` -- but
the single ``oracle: OracleSymbol`` ``diff_symbol`` receives is only ever ONE class's OWN declared
members (``enumerate_python`` collects one ``OracleSymbol`` per public class-def, each only from
that class's own body -- it never walks a class's bases). A real, correctly-flattened, correctly-
labelled inherited member was therefore NEVER findable in ``oracle.members`` no matter how
faithfully the extractor recorded it. This card asks the RIGHT question instead: does the NAMED
ANCESTOR's own oracle symbol (a DIFFERENT, real ``OracleSymbol`` the SAME enumerator run already
produced, now threaded through as ``oracle_by_name``) really declare a member of that name AND
kind. This is deliberately narrower than full "qualified keys" (a separate O1 sub-task): the
ancestor lookup is by BARE name, the same ambiguity TC-623 closed for the PRIMARY symbol lookup but
not (yet) for this SECONDARY, ancestor lookup -- a strict improvement over "never checked at all"
regardless.

Six cases, exactly as the card's own ``inputs`` section describes them, plus one extra
confirmation test:

  (a) a synthetic inherited member whose named ancestor's oracle symbol DOES declare it -- zero
      "extra", zero entries in either new field: fully verified, silent.
  (b) the same shape but the named ancestor's oracle symbol does NOT declare it -- lands in the new
      ``inherited_mismatch`` field, never the old ``extra`` field; the finding's ``category`` names
      the new category and its ``detail`` names the real ancestor and the real mismatch.
  (c) the same shape but the named ancestor is not in the oracle's symbol set at all -- lands in
      ``unverified_inherited``, never ``extra``, and produces no ``CATEGORIES``-gated finding.
  (d) a REAL regression reproduction: ``tests/fixtures/3d_python``'s real ``Node``/``find_property``/
      ``A3DObject`` case -- before this card, every one of ``Node``'s 7 real inherited members
      (4 methods + 3 properties) was unconditionally counted hard "extra"; after this card, every
      one is silently verified against its real named ancestor's own real oracle symbol.
  (e) a method name and a property name sharing the same bare name on the SAME ancestor
      (constructed synthetically, since no real fixture has this) -- confirms the kind-matching
      requirement actually prevents a false cross-kind verification.
  (f) the full, existing ``tests/release/test_source_differential.py`` (137 tests as of this
      card's authoring, not the original 128, after TC-692's own 6 added hollow-pass-guard tests
      and TC-694's own 3 added NPM_CONFIG_PREFIX-resolution tests) PLUS TC-623's own
      ``tests/release/test_oracle_shell_resolution.py`` are run for real and every result still
      holds, unchanged by this card -- the expected combined count is collected live via
      ``--collect-only`` (G3/TC-696), no longer a hand-maintained literal.
  (extra) ``evaluate_ceilings`` still iterates ``CATEGORIES`` only -- the two new fields can never
      gate a release, confirmed directly even when they are large.

No mocks anywhere below; (d) reads the real, committed fixture under ``tests/fixtures/`` and never
writes to it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
EXISTING_SUITES = (
    REPO_ROOT / "tests" / "release" / "test_source_differential.py",
    REPO_ROOT / "tests" / "release" / "test_oracle_shell_resolution.py",
)
# G3/TC-696: this used to be a hand-maintained literal pin of EXISTING_SUITES' combined test count
# (132 -> 138 -> 141 across TC-692/TC-694), which required a mid-review rework of this unrelated
# file every time an unrelated card changed test_source_differential.py's own count. It is now
# collected live, below, via `_collect_test_count`, so this file no longer needs editing when that
# count moves again.

sys.path.insert(0, str(RELEASE_DIR))

import source_differential as sd  # noqa: E402

OM = sd.OracleMember
OS = sd.OracleSymbol


def _collect_test_count(paths: tuple[Path, ...]) -> int:
    """Live-collects the real, current number of tests ``paths`` contains via
    ``pytest --collect-only``, so callers can compare a real run's reported total against the
    SAME suite(s) collected at the SAME commit instead of a hand-maintained literal that goes
    stale the moment an unrelated card adds a test to ``test_source_differential.py`` (TC-692,
    TC-694 both hit this).

    Deliberately passes no explicit ``-q``: this repository's own ``pyproject.toml`` sets
    ``addopts = "-q"`` already, and confirming this card's own instructions directly (not merely
    assuming them) showed that stacking a SECOND, explicit ``-q`` on top of it raises the quiet
    level enough that ``--collect-only`` stops printing one node-id line per collected test (each
    containing ``"::"``) and instead prints a single collapsed ``"<file>: N"`` summary line per
    file -- which would make the ``"::"``-counting approach below silently count zero. This is the
    same ``-q``-stacking hazard ``test_oracle_shell_resolution.py``'s own
    ``test_existing_source_differential_suite_is_unaffected_by_this_fix`` already documents for the
    real run's final summary line, now shown to apply to ``--collect-only``'s per-test lines too,
    specifically when an explicit ``-q`` is added on top of it. Relying on just the single ``-q``
    ``addopts`` already supplies keeps the per-test node-id lines intact -- confirmed directly
    against this real suite before relying on it.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *(str(path) for path in paths),
            "--collect-only",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    count = sum(1 for line in result.stdout.splitlines() if "::" in line)
    assert count > 0, f"expected at least one collected test node id, got none:\n{result.stdout}"
    return count


def _baked_type(
    name: str,
    *,
    methods: list[dict] | None = None,
    properties: list[dict] | None = None,
    enum_members: list[dict] | None = None,
) -> dict:
    """Mirrors ``test_source_differential.py``'s own private ``_baked_type`` helper (not imported
    across test files -- it is simple enough to duplicate)."""
    return {
        "name": name,
        "kind": "class_declaration",
        "class_import": name,
        "methods": methods or [],
        "properties": properties or [],
        "enum_members": enum_members or [],
    }


def _old_diff_symbol_extra_count(oracle: sd.OracleSymbol, baked: dict) -> tuple[int, list[str]]:
    """The pre-TC-624 ``diff_symbol``'s three trailing "extra" loops, reproduced verbatim (never
    imported from ``source_differential`` itself, which no longer contains this exact logic) only
    to PROVE the pre-card regression existed: every baked method/property not found in the
    oracle's own member names was unconditionally counted "extra", whether or not it carried
    ``inherited_from``. Never used by the real, fixed ``diff_symbol`` under test below."""
    baked_methods = {str(m.get("name")): m for m in baked.get("methods") or []}
    baked_props = {str(p.get("name")): p for p in baked.get("properties") or []}
    oracle_method_names = {member.name for member in oracle.members if member.kind == "method"}
    oracle_prop_names = {member.name for member in oracle.members if member.kind == "property"}
    extra = 0
    extra_members: list[str] = []
    for name in baked_methods:
        if name not in oracle_method_names:
            extra += 1
            extra_members.append(name)
    for name in baked_props:
        if name not in oracle_prop_names:
            extra += 1
            extra_members.append(name)
    return extra, extra_members


# ---------------------------------------------------------------------------------------------------
# (a) The named ancestor's own oracle symbol DOES declare the inherited member -- fully verified,
# silently: zero "extra", zero entries in either new field.
#
# Routed through diff_language (never diff_symbol called directly with a hand-built oracle_by_name)
# so the card's own negative control -- which forces diff_language's real, internally-built
# oracle_by_name back to an empty dict immediately after it is constructed -- actually changes this
# test's outcome. Calling diff_symbol directly with an explicit oracle_by_name argument would
# bypass that line entirely and could never detect the mutation (see this card's own
# negative_control.rationale).
# ---------------------------------------------------------------------------------------------------


def test_a_inherited_member_verified_against_its_named_ancestor_is_silent() -> None:
    ancestor = OS("Base", "class", members=(OM("widget_method", "method"),))
    child = OS("Child", "class", members=())
    baked_child = _baked_type(
        "Child",
        methods=[{"name": "widget_method", "params": [], "inherited_from": "pkg.Base.Base"}],
    )
    baked_base = _baked_type("Base", methods=[{"name": "widget_method", "params": []}])

    counts, findings = sd.diff_language([child, ancestor], [baked_child, baked_base])

    assert counts.extra == 0
    assert counts.missing == 0
    assert counts.inherited_mismatch == 0
    assert counts.unverified_inherited == 0
    assert findings == []


# ---------------------------------------------------------------------------------------------------
# (b) The named ancestor IS in oracle_by_name, but its own oracle symbol does NOT declare the
# member -- a genuine disagreement, lands in the new `inherited_mismatch` field, never `extra`.
#
# Routed through diff_language, for the same reason test (a) above is.
# ---------------------------------------------------------------------------------------------------


def test_b_inherited_member_ancestor_found_but_lacking_it_is_inherited_mismatch() -> None:
    ancestor = OS("Base", "class", members=())  # Base does NOT declare widget_method
    child = OS("Child", "class", members=())
    baked_child = _baked_type(
        "Child",
        methods=[{"name": "widget_method", "params": [], "inherited_from": "pkg.Base.Base"}],
    )
    baked_base = _baked_type("Base")  # Base's own baked entry also has no widget_method -- consistent

    counts, findings = sd.diff_language([child, ancestor], [baked_child, baked_base])

    assert counts.inherited_mismatch == 1
    assert counts.extra == 0
    assert counts.unverified_inherited == 0
    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "inherited_mismatch"
    assert finding.category != "extra"
    assert finding.member == "widget_method"
    assert "Base" in finding.detail  # names the real ancestor
    assert "widget_method" in finding.detail or "method" in finding.detail  # names the real mismatch


# ---------------------------------------------------------------------------------------------------
# (c) The named ancestor is not in oracle_by_name at all (never scanned by this run) -- an honest
# "could not check", lands in `unverified_inherited`, never `extra`, and never CATEGORIES-gated.
# ---------------------------------------------------------------------------------------------------


def test_c_inherited_member_ancestor_never_scanned_is_unverified_not_extra() -> None:
    oracle = OS("Child", "class", members=())
    baked = _baked_type(
        "Child",
        methods=[{"name": "widget_method", "params": [], "inherited_from": "pkg.Base.Base"}],
    )

    counts, findings = sd.diff_symbol(oracle, baked, {})  # Base was never scanned

    assert counts.unverified_inherited == 1
    assert counts.extra == 0
    assert counts.inherited_mismatch == 0
    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "unverified_inherited"
    assert finding.category not in sd.CATEGORIES  # never ceiling-gated
    assert all(f.category not in sd.CATEGORIES for f in findings)

    # the same default-``oracle_by_name`` path also applies when the caller passes nothing at all
    counts_default, findings_default = sd.diff_symbol(oracle, baked)
    assert counts_default.unverified_inherited == 1
    assert counts_default.extra == 0
    assert findings_default[0].category == "unverified_inherited"


# ---------------------------------------------------------------------------------------------------
# (d) A REAL regression reproduction: tests/fixtures/3d_python/api_surface.json's real Node class
# genuinely inherits find_property/get_property/remove_property/set_property (methods) and
# name/properties (from A3DObject) plus scene (from SceneObject). Before this card, every one of
# these 7 real inherited members was unconditionally counted hard "extra". After this card, every
# one is silently verified against its real named ancestor's own real oracle symbol.
# ---------------------------------------------------------------------------------------------------


def test_d_real_3d_python_node_find_property_a3dobject_regression() -> None:
    fixture = json.loads((FIXTURES_DIR / "3d_python" / "api_surface.json").read_text(encoding="utf-8"))
    types = fixture["types"]

    node = next(
        t
        for t in types
        if t.get("name") == "Node"
        and t.get("class_import") == "aspose.threed.Node.Node"
        and not t.get("reexport_of")
    )
    a3dobject = next(
        t
        for t in types
        if t.get("name") == "A3DObject"
        and t.get("class_import") == "aspose.threed.A3DObject.A3DObject"
        and not t.get("reexport_of")
    )
    sceneobject = next(
        t
        for t in types
        if t.get("name") == "SceneObject"
        and t.get("class_import") == "aspose.threed.SceneObject.SceneObject"
        and not t.get("reexport_of")
    )

    inherited_methods = [str(m["name"]) for m in node.get("methods") or [] if m.get("inherited_from")]
    inherited_props = [str(p["name"]) for p in node.get("properties") or [] if p.get("inherited_from")]
    assert "find_property" in inherited_methods, "the real fixture must still carry this regression case"
    assert inherited_methods == ["find_property", "get_property", "remove_property", "set_property"]
    assert inherited_props == ["scene", "name", "properties"]

    # The real, independent Python AST oracle (enumerate_python) only ever lists a class's OWN
    # declared members -- never a base's (it never walks bases at all). Build each oracle symbol
    # from exactly its own baked definition's own-declared (no inherited_from) methods/properties,
    # mirroring what the real tool would have produced.
    def _own_declared_oracle(name: str, entry: dict) -> sd.OracleSymbol:
        return OS(
            name,
            "class",
            members=tuple(
                OM(str(m["name"]), "method")
                for m in entry.get("methods") or []
                if not m.get("inherited_from")
            )
            + tuple(
                OM(str(p["name"]), "property")
                for p in entry.get("properties") or []
                if not p.get("inherited_from")
            ),
        )

    node_oracle = _own_declared_oracle("Node", node)
    a3dobject_oracle = _own_declared_oracle("A3DObject", a3dobject)
    sceneobject_oracle = _own_declared_oracle("SceneObject", sceneobject)
    # A3DObject's own real definition has no inherited_from on any of its members (confirmed by
    # reading the real fixture), so its "own-declared" oracle already carries find_property etc.
    assert {m.name for m in a3dobject_oracle.members} >= {"find_property", "name", "properties"}
    assert {m.name for m in sceneobject_oracle.members} >= {"scene"}

    # BEFORE this card: every one of the 7 real inherited members was unconditionally "extra".
    # (the OLD, pre-card trailing-loop logic, reproduced verbatim -- never diff_language/diff_symbol
    # themselves, which no longer behave this way.)
    old_extra, old_extra_members = _old_diff_symbol_extra_count(node_oracle, node)
    assert old_extra == len(inherited_methods) + len(inherited_props) == 7
    assert "find_property" in old_extra_members

    # AFTER this card: routed through the real diff_language (never diff_symbol called directly
    # with a hand-built oracle_by_name) so this test's outcome actually depends on diff_language's
    # own, internally-built oracle_by_name -- exactly what this card's own negative control targets
    # (see this card's negative_control.rationale). Node's, A3DObject's and SceneObject's own
    # definitions (all three real, read directly off the committed fixture above) are enumerated
    # together, exactly as one real enumerate_python run over the real library would produce them.
    new_counts, new_findings = sd.diff_language(
        [node_oracle, a3dobject_oracle, sceneobject_oracle], [node, a3dobject, sceneobject]
    )
    assert new_counts.extra == 0, new_findings
    assert new_counts.inherited_mismatch == 0, new_findings
    assert new_counts.unverified_inherited == 0, new_findings
    # Node's own (inherited) find_property -- never A3DObject's own, unrelated signature check
    # (A3DObject's synthetic oracle above omits real params, a harmless, orthogonal artifact of
    # this test's simplified construction, not something this card's ancestor-verification governs).
    node_find_property_findings = [
        f for f in new_findings if f.symbol == "Node" and f.member == "find_property"
    ]
    assert node_find_property_findings == [], "find_property must now be silently verified, not flagged"


# ---------------------------------------------------------------------------------------------------
# (e) A method and a property share the same bare name on the SAME ancestor (no real fixture has
# this -- constructed synthetically). The kind-matching requirement must prevent the ancestor's
# property of that name from falsely verifying the baked, inherited METHOD of the same name.
# ---------------------------------------------------------------------------------------------------


def test_e_kind_matching_prevents_a_false_cross_kind_verification() -> None:
    # Base declares a PROPERTY named "value" -- no method of that name.
    ancestor = OS("Base", "class", members=(OM("value", "property"),))
    oracle = OS("Child", "class", members=())
    baked = _baked_type(
        "Child",
        methods=[{"name": "value", "params": [], "inherited_from": "pkg.Base.Base"}],
    )

    counts, findings = sd.diff_symbol(oracle, baked, {"Base": ancestor})

    # The ancestor DOES have something named "value", but it is a property, not a method -- this
    # must not silently verify the inherited METHOD of the same name.
    assert counts.inherited_mismatch == 1
    assert counts.extra == 0
    assert counts.unverified_inherited == 0
    assert findings[0].category == "inherited_mismatch"


# ---------------------------------------------------------------------------------------------------
# (f) The full, existing test_source_differential.py (137 tests as of this card's authoring, not
# the original 128, after TC-692's own 6 added hollow-pass-guard tests and TC-694's own 3 added
# NPM_CONFIG_PREFIX-resolution tests) + TC-623's own test_oracle_shell_resolution.py are run for
# real and every result still holds, compared against a live `--collect-only` count (G3/TC-696).
# ---------------------------------------------------------------------------------------------------


def test_f_existing_suite_is_unaffected_by_this_card() -> None:
    """Run as a subprocess of the SAME interpreter running this test (so the already-activated
    toolchain's PATH -- node/java/dotnet/clang/Docker -- is inherited), read from a JUnit XML
    report (never from stdout text, for the same ``-q``-stacking reason TC-623's own
    ``test_oracle_shell_resolution.py`` documents), and confirm the real, live-collected test count
    across BOTH existing files still passes, unchanged by this card (G3/TC-696: no longer a
    hand-maintained literal).
    """
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "existing-suites.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *[str(path) for path in EXISTING_SUITES],
                "-p",
                "no:cacheprovider",
                f"--junitxml={report}",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=280,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        root = ET.parse(report).getroot()
        testsuite = root if root.tag == "testsuite" else root.find("testsuite")
        assert testsuite is not None, ET.tostring(root, encoding="unicode")
        total = int(testsuite.get("tests", "0"))
        failures = int(testsuite.get("failures", "0"))
        errors = int(testsuite.get("errors", "0"))
    assert failures == 0 and errors == 0, (failures, errors, result.stdout + result.stderr)
    expected = _collect_test_count(EXISTING_SUITES)
    assert total == expected, (
        f"expected all {expected} tests `pytest --collect-only` reports for these suites right now "
        f"to still all pass unchanged, got {total} tests ({failures} failures, {errors} errors)"
    )


# ---------------------------------------------------------------------------------------------------
# (extra) evaluate_ceilings must still iterate CATEGORIES only -- the two new fields can never gate
# a release, confirmed directly even when they are large, never merely assumed from reading the code.
# ---------------------------------------------------------------------------------------------------


def test_evaluate_ceilings_never_gates_on_the_two_new_fields() -> None:
    assert "inherited_mismatch" not in sd.CATEGORIES
    assert "unverified_inherited" not in sd.CATEGORIES
    counts = sd.DiffCounts(inherited_mismatch=50, unverified_inherited=50)
    reasons = sd.evaluate_ceilings(counts, {category: 0 for category in sd.CATEGORIES})
    assert reasons == []


# ---------------------------------------------------------------------------------------------------
# New, isolated coverage of `_collect_test_count` itself (G3/TC-696): proves the helper this card
# adds genuinely counts real collected tests, on a trivially small, already-collectible real suite
# -- TC-623's own test_oracle_shell_resolution.py -- rather than merely restating test (f) above.
# ---------------------------------------------------------------------------------------------------


def test_collect_test_count_helper_matches_an_independent_count_of_a_small_real_suite() -> None:
    """``_collect_test_count`` run against ``test_oracle_shell_resolution.py`` must return a sane
    positive integer that matches an independent count of that file's own top-level
    ``def test_...`` lines -- a cross-check computed a completely different way (static text
    scanning, never pytest itself), so this test cannot pass merely because both counting methods
    share the same bug."""
    small_suite = REPO_ROOT / "tests" / "release" / "test_oracle_shell_resolution.py"
    independent_count = sum(
        1 for line in small_suite.read_text(encoding="utf-8").splitlines() if line.startswith("def test_")
    )
    assert independent_count > 0

    collected = _collect_test_count((small_suite,))

    assert isinstance(collected, int)
    assert collected > 0
    assert collected == independent_count
