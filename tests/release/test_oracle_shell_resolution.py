"""G3/TC-623 (O1 part 1, REQ-G3-012): the oracle diff engine's bare-name same-name collision fix.

``scripts/release/source_differential.py``'s ``diff_language`` matches every independent oracle
symbol against the baked fixture's own ``types`` entries of the same bare ``name``. Before this
card, ``baked_by_name`` was built with a plain ``{str(t.get("name")): t for t in baked_types}``
dict comprehension -- EVERY entry of the fixture's ``types`` list, shell and definition alike, so
whenever a Python re-export shell and its real definition share a bare name, whichever happens to
iterate LAST in the fixture's own list order silently overwrites the other. When the shell wins
(member-less, or carrying only a subset), every one of the real definition's genuine members is
then diffed against the wrong, near-empty baked entry and reported as spuriously "missing" --
Appendix D of the oracle-disagreement investigation measured this as 43% of the whole "missing"
bucket (~10,490 rows). This card's fix consults ``symbol_identity.compute_canonical_identities``
(I1, TC-612) once per ``diff_language`` call so a shell can never win a same-name collision against
its own definition, regardless of list order.

This is deliberately the ONE, narrow, mechanical fix described in ``plans/TC-623.yaml``: it does
NOT attempt fully-qualified (cross-module) keying, overload-set comparison, or
``inherited_from`` verification against a named ancestor -- those are separate, larger O1
sub-tasks for later cards.

Four cases, exactly as the card's own ``inputs`` section describes them:

  (a) synthetic shell-AFTER-definition -- the exact "last wins" bug, before/after proof.
  (b) synthetic shell-BEFORE-definition -- the "shell happens to win by luck" case that was already
      accidentally correct before this card; a regression guard that the fix does not break it.
  (c) a REAL pilot fixture regression: ``tests/fixtures/3d_python/api_surface.json`` carries a
      genuine same-name collision between ``aspose.threed.PropertyCollection.PropertyCollection``
      (the real, member-bearing definition) and ``aspose.threed.PropertyCollection`` (its
      ``__init__.py`` re-export shell, member-less, ``reexport_of`` the definition's own
      ``class_import``) -- the shell iterates immediately AFTER its definition in the fixture's own
      committed list, reproducing the exact bug case (a) reproduces synthetically.
  (d) the full, existing ``tests/release/test_source_differential.py`` suite is unaffected by this
      fix (137 tests as of this card's authoring, not the original 128, after TC-692's own 6 added
      hollow-pass-guard tests and TC-694's own 3 added NPM_CONFIG_PREFIX-resolution tests -- G3/
      TC-696 later made the expected count below self-computing via ``--collect-only``, so a future
      change to that count no longer requires hand-editing this file).

No mocks anywhere below; (c) reads the real, committed fixture under ``tests/fixtures/`` and never
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
EXISTING_SUITE = REPO_ROOT / "tests" / "release" / "test_source_differential.py"
# G3/TC-696: this used to be a hand-maintained literal pin of test_source_differential.py's exact
# test count (128 -> 134 -> 137 across TC-692/TC-694), which required a mid-review rework of this
# unrelated file every time an unrelated card changed that count. It is now collected live, below,
# via `_collect_test_count`, so this file no longer needs editing when that count moves again.

sys.path.insert(0, str(RELEASE_DIR))

import source_differential as sd  # noqa: E402


def _collect_test_count(paths: list[Path]) -> int:
    """Live-collects the real, current number of tests ``paths`` contains via
    ``pytest --collect-only``, so callers can compare a real run's reported total against the
    SAME suite collected at the SAME commit instead of a hand-maintained literal that goes stale
    the moment an unrelated card adds a test to it (TC-692, TC-694 both hit this).

    Deliberately passes no explicit ``-q``: this repository's own ``pyproject.toml`` sets
    ``addopts = "-q"`` already, and confirming this card's own instructions directly (not merely
    assuming them) showed that stacking a SECOND, explicit ``-q`` on top of it raises the quiet
    level enough that ``--collect-only`` stops printing one node-id line per collected test
    (each containing ``"::"``) and instead prints a single collapsed ``"<file>: N"`` summary line
    per file -- which would make the ``"::"``-counting approach below silently count zero. This is
    the same ``-q``-stacking hazard this file's own ``test_existing_source_differential_suite_is_unaffected_by_this_fix``
    already documents for the real run's final summary line, now shown to apply to
    ``--collect-only``'s per-test lines too, specifically when an explicit ``-q`` is added on top
    of it. Relying on just the single ``-q`` ``addopts`` already supplies keeps the per-test
    node-id lines intact -- confirmed directly against this real suite before relying on it.
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


OM = sd.OracleMember
OP = sd.OracleParam
OS = sd.OracleSymbol


def _old_baked_by_name(baked_types: list[dict]) -> dict[str, dict]:
    """The pre-TC-623 ``baked_by_name`` construction, reproduced verbatim (never imported from
    ``source_differential`` itself, which no longer contains it) only to PROVE the bug existed --
    and, in case (b), that one ordering was already accidentally correct -- never used by the real,
    fixed ``diff_language`` under test."""
    return {str(t.get("name")): t for t in baked_types}


def _shape_definition() -> dict:
    return {
        "name": "Shape",
        "kind": "class",
        "file": "pkg/real.py",
        "class_import": "pkg.real.Shape",
        "methods": [{"name": "area", "params": [], "return_type": ""}],
        "properties": [{"name": "sides", "type": "int"}],
    }


def _shape_shell() -> dict:
    return {
        "name": "Shape",
        "kind": "class",
        "file": "pkg/__init__.py",
        "class_import": "pkg.Shape",
        "reexport_of": "pkg.real.Shape",
        "methods": [],
        "properties": [],
    }


def _shape_oracle() -> sd.OracleSymbol:
    return OS("Shape", "class", members=(OM("area", "method"), OM("sides", "property")))


# ---------------------------------------------------------------------------------------------------
# (a) Synthetic: the shell iterates AFTER the definition -- the exact "last wins" bug this card
# fixes. Before/after: the OLD dict-comprehension logic really does let the shell win and really
# does report both real members as falsely missing; the NEW diff_language never does.
# ---------------------------------------------------------------------------------------------------


def test_shell_after_definition_old_logic_reports_false_missing_new_logic_reports_zero() -> None:
    definition = _shape_definition()
    shell = _shape_shell()
    baked_types = [definition, shell]  # the shell iterates LAST in fixture order
    oracle = _shape_oracle()

    # Before this card: the bare dict-comprehension logic really does let the shell silently
    # overwrite the definition, and diff_symbol against that wrong, near-empty entry really does
    # report both of the definition's real members as falsely "missing".
    old_by_name = _old_baked_by_name(baked_types)
    assert old_by_name["Shape"] is shell, "the pre-fix bug: the LAST entry in list order wins"
    old_counts, old_findings = sd.diff_symbol(oracle, old_by_name.get("Shape"))
    assert old_counts.missing == 2
    assert {finding.member for finding in old_findings} == {"area", "sides"}

    # After this card: diff_language consults compute_canonical_identities, so the shell can never
    # win regardless of list order -- zero false "missing" findings.
    new_counts, new_findings = sd.diff_language([oracle], baked_types)
    assert new_counts.missing == 0, new_findings
    assert new_counts.checked == 2
    assert new_findings == []


# ---------------------------------------------------------------------------------------------------
# (b) Synthetic: the SAME case with the shell placed BEFORE the definition -- the "shell happens to
# win by luck" ordering that was already accidentally correct before this card. A regression guard:
# the fix must not disturb this already-correct case.
# ---------------------------------------------------------------------------------------------------


def test_shell_before_definition_already_lucky_case_still_correct_after_the_fix() -> None:
    definition = _shape_definition()
    shell = _shape_shell()
    baked_types = [shell, definition]  # the definition iterates LAST -- already correct by luck
    oracle = _shape_oracle()

    old_by_name = _old_baked_by_name(baked_types)
    assert old_by_name["Shape"] is definition, "this ordering was already accidentally correct pre-fix"
    old_counts, _old_findings = sd.diff_symbol(oracle, old_by_name.get("Shape"))
    assert old_counts.missing == 0

    new_counts, new_findings = sd.diff_language([oracle], baked_types)
    assert new_counts.missing == 0, new_findings
    assert new_counts.checked == 2
    assert new_findings == []


# ---------------------------------------------------------------------------------------------------
# (c) A REAL regression: tests/fixtures/3d_python/api_surface.json carries a genuine same-name
# shell/definition collision. Read directly from the real, committed fixture -- never invented.
# ---------------------------------------------------------------------------------------------------


def test_real_3d_python_fixture_propertycollection_shell_definition_collision() -> None:
    """``tests/fixtures/3d_python/api_surface.json``'s ``types`` list carries a real collision on
    the bare name ``PropertyCollection``: ``aspose.threed.PropertyCollection.PropertyCollection``
    (``aspose/threed/PropertyCollection.py``, the real definition -- a constructor, three methods
    and one property) and ``aspose.threed.PropertyCollection`` (``aspose/threed/__init__.py``, a
    member-less re-export shell whose ``reexport_of`` names the definition's own ``class_import``
    exactly). The shell iterates immediately AFTER the definition in the fixture's own committed
    list order, reproducing the exact bug case (a) reproduces synthetically. The oracle's members
    below are read directly from the real definition entry, not invented."""
    fixture = json.loads((FIXTURES_DIR / "3d_python" / "api_surface.json").read_text(encoding="utf-8"))
    types = fixture["types"]

    definition = next(
        t
        for t in types
        if t.get("name") == "PropertyCollection"
        and t.get("class_import") == "aspose.threed.PropertyCollection.PropertyCollection"
        and not t.get("reexport_of")
    )
    shell = next(
        t
        for t in types
        if t.get("name") == "PropertyCollection"
        and t.get("reexport_of") == "aspose.threed.PropertyCollection.PropertyCollection"
    )
    assert types.index(shell) > types.index(definition), (
        "the real fixture must list the shell after the definition to reproduce the bug this card fixes"
    )
    assert definition.get("methods"), "the real definition must genuinely carry members"
    assert not shell.get("methods") and not shell.get("properties"), "the real shell must be member-less"

    # The oracle's members are read directly off the real definition entry above -- __init__,
    # find_property, get, remove_property and the count property -- not invented.
    oracle = OS(
        "PropertyCollection",
        "class",
        members=(
            OM("__init__", "method"),
            OM("find_property", "method", params=(OP("property_name"),)),
            OM("get", "method", params=(OP("property"),)),
            OM("remove_property", "method", params=(OP("property"),)),
            OM("count", "property"),
        ),
    )

    # Before this card: the old bare-name logic lets the member-less shell win this real collision,
    # and every one of the real definition's members is reported as falsely missing.
    old_by_name = _old_baked_by_name(types)
    assert old_by_name["PropertyCollection"] is shell, "the real fixture must reproduce the pre-fix bug"
    old_counts, _old_findings = sd.diff_symbol(oracle, old_by_name.get("PropertyCollection"))
    assert old_counts.missing == 5, "the old bug must report every real member of the definition as missing"

    # After this card: diff_language resolves the real collision correctly; zero false "missing".
    new_counts, new_findings = sd.diff_language([oracle], types)
    missing_findings = [finding for finding in new_findings if finding.category == "missing"]
    assert new_counts.missing == 0, missing_findings


# ---------------------------------------------------------------------------------------------------
# (d) The full, existing tests/release/test_source_differential.py suite (137 tests as of this
# card's authoring, not the original 128, after TC-692's own 6 added hollow-pass-guard tests and
# TC-694's own 3 added NPM_CONFIG_PREFIX-resolution tests) is unaffected by this fix: it is run for
# real (never imported and iterated in-process, since several of its own tests really shell out to
# node/java/dotnet/clang/Docker) and its result still holds against a live `--collect-only` count
# of the same suite (G3/TC-696), not a hand-maintained literal.
# ---------------------------------------------------------------------------------------------------


def test_existing_source_differential_suite_is_unaffected_by_this_fix() -> None:
    """None of ``test_source_differential.py``'s own 137 tests build a bare-name shell/definition
    collision through ``diff_language``/``verify_pilot`` (confirmed by inspection: its own
    ``_baked_type`` helper never sets ``file``/``reexport_of``, so none of its synthetic baked
    types are ever grouped as a shell by ``compute_canonical_identities`` -- every one of them
    stays its own, unaliased, ``is_definition=True`` unit, exactly as the pre-fix bare dict
    comprehension already treated it). This runs the real suite as a subprocess of the SAME
    interpreter running this test (so the already-activated toolchain's PATH -- node/java/dotnet/
    clang/Docker -- is inherited) and confirms the real, live-collected test count still passes,
    unchanged (G3/TC-696: no longer a hand-maintained literal).

    The result is read from a JUnit XML report (``--junitxml``), never from stdout text: this
    repository's own ``pyproject.toml`` already sets ``addopts = "-q"``, and stacking a second,
    explicit ``-q`` on top of it raises pytest's quiet level enough to drop the final "N passed in
    Ys" summary line entirely -- confirmed directly against this very suite, not merely assumed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "existing-suite.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(EXISTING_SUITE),
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
    expected = _collect_test_count([EXISTING_SUITE])
    assert total == expected, (
        f"expected all {expected} tests `pytest --collect-only` reports for this suite right now "
        f"to still all pass unchanged, got {total} tests ({failures} failures, {errors} errors)"
    )


# ---------------------------------------------------------------------------------------------------
# New, isolated coverage of `_collect_test_count` itself (G3/TC-696): proves the helper this card
# adds genuinely counts real collected tests, on a trivially small, already-collectible real suite
# -- this file itself -- rather than merely restating the "existing suite is unaffected" test above.
# ---------------------------------------------------------------------------------------------------


def test_collect_test_count_helper_matches_an_independent_count_of_this_own_file() -> None:
    """``_collect_test_count`` run against THIS file must return a sane positive integer that
    matches an independent count of this file's own top-level ``def test_...`` lines -- a
    cross-check computed a completely different way (static text scanning, never pytest itself),
    so this test cannot pass merely because both counting methods share the same bug."""
    this_file = Path(__file__)
    independent_count = sum(
        1 for line in this_file.read_text(encoding="utf-8").splitlines() if line.startswith("def test_")
    )
    assert independent_count > 0

    collected = _collect_test_count([this_file])

    assert isinstance(collected, int)
    assert collected > 0
    assert collected == independent_count
