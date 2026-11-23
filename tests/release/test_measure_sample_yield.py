"""G3/TC-866 (REQ-G3-014): offline proof of ``scripts/release/measure_sample_yield.py``.

A tiny synthetic sample directory and a fake runner stand in for the container: no docker, no network, no clone.
What is proved is the tool's own logic - every classification, the deterministic ordering, the exclusion
accounting and the DG-3 decision rule - not the product verifier it calls inside the image.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "release"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import measure_sample_yield as msy  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# What the container reports for each synthetic sample (the keys ``run_one_sample`` writes).
OUTCOMES: dict[str, dict[str, object]] = {
    "a_prints.py": {"verified": True, "exit_code": 0, "stdout": "42\n", "stderr": "", "new_files": {}},
    "b_writes.py": {"verified": True, "exit_code": 0, "stdout": "", "new_files": {"out.xlsx": 4096}},
    "c_silent.py": {"verified": True, "exit_code": 0, "stdout": "", "stderr": "", "new_files": {}},
    "d_syntax.py": {"verified": False, "compile_error": "SyntaxError: invalid syntax (<unknown>, line 3)"},
    "e_fixture.py": {
        "verified": False,
        "fixture_unavailable": True,
        "missing": ["data/input.xlsx"],
        "exit_code": 1,
        "stderr": "FileNotFoundError: [Errno 2] No such file or directory: 'data/input.xlsx'",
    },
    "f_runtime.py": {
        "verified": False,
        "exit_code": 1,
        "stderr": "Traceback (most recent call last):\n  File ...\nAttributeError: 'Workbook' has no attribute 'nope'",
        "error_line": "AttributeError: 'Workbook' has no attribute 'nope'",
    },
    "g_network.py": {
        "verified": False,
        "exit_code": 1,
        "stderr": "urllib.error.URLError: <urlopen error [Errno -3] Temporary failure in name resolution>",
        "error_line": "urllib.error.URLError: <urlopen error [Errno -3] Temporary failure in name resolution>",
    },
    "h_imports.py": {"verified": False, "no_statements": True},
}
EXPECTED = {
    "a_prints.py": msy.C_PRODUCED,
    "b_writes.py": msy.C_PRODUCED,
    "c_silent.py": msy.C_EXIT0,
    "d_syntax.py": msy.C_COMPILE,
    "e_fixture.py": msy.C_FIXTURE,
    "f_runtime.py": msy.C_RUNTIME,
    "g_network.py": msy.C_NETWORK,
    "h_imports.py": msy.C_NO_STATEMENTS,
}


def _fake_runner(name: str, code: str) -> Mapping[str, object]:
    return OUTCOMES[name]


def _synthetic_samples(root: Path) -> Path:
    examples = root / "examples"
    examples.mkdir()
    for name in OUTCOMES:
        (examples / name).write_text(f"# {name}\nprint('x')\n", encoding="utf-8")
    (examples / "notes.txt").write_text("not a sample", encoding="utf-8")
    sub = examples / "advanced"
    sub.mkdir()
    (sub / "z_nested.py").write_text("print(1)\n", encoding="utf-8")
    return root


def test_every_classification_is_reachable_and_exactly_once(tmp_path: Path) -> None:
    samples = msy.collect_samples(_synthetic_samples(tmp_path), "examples")
    OUTCOMES["advanced/z_nested.py"] = {"verified": True, "stdout": "1\n"}
    try:
        results = msy.measure_samples(
            samples, _fake_runner, excluded={"b_writes.py": "bundled font is not MIT"}
        )
    finally:
        del OUTCOMES["advanced/z_nested.py"]
    by_name = {result.name: result.classification for result in results}
    assert len(results) == len(samples) == len(by_name)  # exactly once each
    expected = {**EXPECTED, "advanced/z_nested.py": msy.C_PRODUCED, "b_writes.py": msy.C_EXCLUDED}
    assert by_name == expected
    assert set(by_name.values()) == set(msy.CLASSES)  # every class appears at least once in this run


def test_reasons_name_the_missing_file_and_the_first_error_line() -> None:
    assert msy.classify_outcome(OUTCOMES["e_fixture.py"]) == (msy.C_FIXTURE, "data/input.xlsx")
    assert msy.classify_outcome(OUTCOMES["f_runtime.py"]) == (
        msy.C_RUNTIME,
        "AttributeError: 'Workbook' has no attribute 'nope'",
    )
    # A FileNotFoundError the fixture planner did not flag still names its file.
    unflagged = {
        "verified": False,
        "exit_code": 1,
        "stderr": "FileNotFoundError: [Errno 2] No such file: 'a.png'",
    }
    assert msy.classify_outcome(unflagged) == (msy.C_FIXTURE, "a.png")
    # A SyntaxError raised by the run (not by the pre-check) is still a compile rejection.
    late = {
        "verified": False,
        "exit_code": 1,
        "stderr": "  File 'candidate.py', line 1\nSyntaxError: invalid syntax",
    }
    assert msy.classify_outcome(late)[0] == msy.C_COMPILE


def test_a_verified_run_with_an_invalid_artefact_is_rejected_not_counted() -> None:
    outcome = {
        "verified": True,
        "exit_code": 0,
        "new_files": {"r.pdf": 0},
        "invalid_artifacts": ["r.pdf: empty file"],
    }
    classification, reason = msy.classify_outcome(outcome)
    assert classification == msy.C_RUNTIME
    assert "r.pdf: empty file" in reason


def test_output_is_sorted_and_deterministic() -> None:
    samples = {name: "pass\n" for name in reversed(sorted(OUTCOMES))}
    first = msy.measure_samples(samples, _fake_runner, clock=lambda: 0.0)
    second = msy.measure_samples(dict(reversed(list(samples.items()))), _fake_runner, clock=lambda: 0.0)
    assert [r.name for r in first] == sorted(OUTCOMES)
    assert first == second
    summary = msy.summarise(first)
    assert msy.render_markdown(first, summary) == msy.render_markdown(second, msy.summarise(second))
    assert json.dumps(msy.summarise(first), sort_keys=True) == json.dumps(
        msy.summarise(second), sort_keys=True
    )


def test_exclusions_leave_the_denominator_but_stay_counted() -> None:
    samples = {name: "pass\n" for name in OUTCOMES}
    results = msy.measure_samples(
        samples, _fake_runner, excluded={"a_prints.py": "logo", "d_syntax.py": "font"}
    )
    summary = msy.summarise(results)
    assert summary["total_samples"] == 8
    assert summary["excluded"] == 2
    assert summary["denominator"] == 6
    assert summary["verified"] == 2  # b_writes, c_silent
    assert summary["counts"][msy.C_EXCLUDED] == 2  # type: ignore[index]
    table = msy.render_markdown(results, summary)
    assert "| `a_prints.py` | excluded | logo |" in table
    assert "2 excluded (of 8 found)" in table
    with pytest.raises(ValueError, match="not samples"):
        msy.measure_samples(samples, _fake_runner, excluded={"nope.py": "x"})


def test_a_runner_that_raises_is_recorded_against_its_sample() -> None:
    def broken(name: str, code: str) -> Mapping[str, object]:
        raise RuntimeError("container exited 125")

    (result,) = msy.measure_samples({"x.py": "pass\n"}, broken)
    assert result.classification == msy.C_RUNTIME
    assert result.reason.startswith("harness: RuntimeError")


@pytest.mark.parametrize(
    ("verified", "total", "expected"),
    [
        (7, 10, "proceed"),  # exactly 0.70
        (70, 100, "proceed"),
        (21, 30, "proceed"),  # exactly 0.70 in a non-trivial denominator
        (6, 10, "revise"),
        (69, 100, "revise"),  # just below
        (20, 30, "revise"),
        (10, 10, "proceed"),
        (0, 5, "revise"),
        (0, 0, "revise"),  # nothing measured is never a proceed
    ],
)
def test_yield_decision_boundary(verified: int, total: int, expected: str) -> None:
    assert msy.yield_decision(verified, total) == expected


def test_collect_samples_reads_py_files_recursively(tmp_path: Path) -> None:
    samples = msy.collect_samples(_synthetic_samples(tmp_path), "examples")
    assert "notes.txt" not in samples
    assert "advanced/z_nested.py" in samples
    assert sorted(samples) == sorted([*OUTCOMES, "advanced/z_nested.py"])


def test_pilot_pin_reads_the_real_values_file() -> None:
    repository, commit = msy.pilot_pin(msy.VALUES_FILE, "cells/python")
    assert repository == "aspose-cells-foss/Aspose.Cells-FOSS-for-Python"
    assert len(commit) == 40 and set(commit) <= set("0123456789abcdef")
    with pytest.raises(LookupError):
        msy.pilot_pin(msy.VALUES_FILE, "nonesuch/nowhere")


def test_missing_names_come_from_the_verifiers_own_line() -> None:
    output = (
        "[foss-mcp] assembly: python staged-files=none\nboom\n[foss-mcp] fixture unavailable: a.xlsx, d/b.png"
    )
    assert msy._missing_names(output) == ["a.xlsx", "d/b.png"]
    assert msy._missing_names("nothing here") == []


def test_the_tool_reuses_the_products_verifier_rather_than_copying_it() -> None:
    source = (SCRIPT_DIR / "measure_sample_yield.py").read_text(encoding="utf-8")
    for needle in (
        "ev.verify_python_example(",
        "ev.prepare_python_library(",
        "example_artifacts.artifact_problems(",
    ):
        assert needle in source
    assert "--network" in source and '"none"' in source  # every sample container runs without a network


def test_unittest_summary_exposes_an_all_skipped_run() -> None:
    assert (
        msy.unittest_summary("Ran 3 tests in 0.1s\n\nOK (skipped=3)\n") == "unittest: ran 3, OK (skipped=3)"
    )
    assert msy.unittest_summary("Ran 1 test in 0.0s\n\nOK\n") == "unittest: ran 1, OK"
    assert msy.unittest_summary("nothing") == ""
    silent = {
        "verified": True,
        "exit_code": 0,
        "stdout": "",
        "stderr": "Ran 2 tests in 0.1s\n\nOK (skipped=2)\n",
    }
    classification, reason = msy.classify_outcome(silent)
    assert classification == msy.C_EXIT0
    assert reason.endswith("unittest: ran 2, OK (skipped=2)")


def test_a_test_that_looks_for_an_input_file_the_repository_lacks_is_a_missing_fixture() -> None:
    named = {
        "verified": False,
        "exit_code": 1,
        "stderr": "AssertionError: False is not true : Input file /tmp/measure_sample_ab12/input/sales.xlsx does not exist",
    }
    assert msy.classify_outcome(named) == (msy.C_FIXTURE, "input/sales.xlsx")
    globbed = {
        "verified": False,
        "exit_code": 1,
        "stderr": "AssertionError: 0 not greater than 0 : No XLSX files found in input directory",
    }
    assert msy.classify_outcome(globbed) == (msy.C_FIXTURE, "input/*.xlsx")
    # The verifier's own flag with a placeholder where a name should be falls back to what the test said.
    placeholder = {**named, "fixture_unavailable": True, "missing": ["{input_path}"]}
    assert msy.classify_outcome(placeholder) == (msy.C_FIXTURE, "input/sales.xlsx")
    # ... even when the traceback echoes the source line of the failing assertion first.
    echoed = {
        **placeholder,
        "stderr": 'self.assertTrue(f"Input file {input_path} does not exist")\n' + str(named["stderr"]),
    }
    assert msy.classify_outcome(echoed) == (msy.C_FIXTURE, "input/sales.xlsx")
