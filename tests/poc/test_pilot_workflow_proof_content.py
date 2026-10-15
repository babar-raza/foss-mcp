"""G3/TC-513: the opt-in minimum-content check 12 and the unresolved-symbol warning of scripts/poc/pilot_workflow_proof.py.

Offline. It reuses the fixtures and the scripted fake client of tests/poc/test_pilot_workflow_proof.py, so the eleven checks
behave exactly as they do there and only what this card adds is under test.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "test_pilot_workflow_proof.py"


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("pilot_workflow_proof_base_tests", BASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = _load_base()
pwp = base.pwp
PILOT = base.PILOT
TYPE_COUNT = 42


def _fixtures(
    root: Path, *, furnished: bool = True, type_count: int | None = TYPE_COUNT, unresolved: int | None = None
) -> None:
    base._write_fixtures(root, furnished=furnished)
    path = root / PILOT / "api_surface.json"
    surface = json.loads(path.read_text(encoding="utf-8"))
    if type_count is not None:
        surface["type_count"] = type_count
    if unresolved is not None:
        surface["unresolved"] = [{"name": f"Missing{index}"} for index in range(unresolved)]
    path.write_text(json.dumps(surface), encoding="utf-8")


def _answers(*, indexed: Any = 10, total: Any = TYPE_COUNT, docs: Any = None) -> dict[str, Any]:
    answers = base.passing_answers()
    original = answers["report_index_freshness"]

    def freshness(arguments: dict[str, Any]) -> Any:
        report = original(arguments)
        report.update(indexed_types=indexed, total_types=total)
        return report

    answers["report_index_freshness"] = freshness
    if docs is not None:
        answers["search_docs"] = docs
    return answers


def _run(root: Path, answers: dict[str, Any], *, min_content: bool = True) -> list[dict[str, Any]]:
    return pwp.run_checks(base.FakeClient(answers), pwp.load_pilot(PILOT, root), min_content=min_content)


def _check(checks: list[dict[str, Any]], number: int) -> dict[str, Any]:
    return next(check for check in checks if check["id"] == number)


def _empty_docs(arguments: dict[str, Any]) -> Any:
    return {
        "scope": base.SCOPE,
        "query": arguments["query"],
        "content_type": arguments["content_type"],
        "reason": "none",
    }


def test_the_default_run_has_eleven_checks_and_no_check_12(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    checks = _run(tmp_path, _answers(), min_content=False)
    assert [c["id"] for c in checks] == list(range(1, 12))
    assert [number for number, _ in pwp.CHECKS] == list(range(1, 12))
    assert 12 not in {c["id"] for c in checks}
    default = pwp.run_checks(base.FakeClient(_answers()), pwp.load_pilot(PILOT, tmp_path))
    assert [c["id"] for c in default] == list(range(1, 12))


def test_min_content_adds_check_12(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    checks = _run(tmp_path, _answers())
    assert [c["id"] for c in checks] == list(range(1, 13))
    assert pwp.CHECKS_MIN_CONTENT[0][0] == 12
    assert _check(checks, 12)["name"] == pwp.CHECK_TITLES[12]
    assert [c["status"] for c in checks[:11]] == [
        c["status"] for c in _run(tmp_path, _answers(), min_content=False)
    ]


def test_check_12_passes_with_enough_indexed_types_and_matching_totals(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    check = _check(_run(tmp_path, _answers(indexed=3, total=TYPE_COUNT)), 12)
    assert check["status"] == "pass", check["reason"]
    assert check["evidence"]["indexed_types"] == 3
    assert check["evidence"]["total_types"] == TYPE_COUNT
    assert check["evidence"]["documentation_match_count"] >= 1


def test_check_12_fails_with_two_indexed_types(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    for indexed in (2, 0, None, "3", True):
        check = _check(_run(tmp_path, _answers(indexed=indexed)), 12)
        assert check["status"] == "fail", indexed
        assert "indexed_types" in check["reason"]
        assert "minimum of 3" in check["reason"]


def test_check_12_fails_when_total_types_differs_from_the_fixture(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    check = _check(_run(tmp_path, _answers(total=TYPE_COUNT + 1)), 12)
    assert check["status"] == "fail"
    assert f"total_types {TYPE_COUNT + 1}" in check["reason"]
    assert f"fixture declares {TYPE_COUNT}" in check["reason"]
    # A fixture that declares no type_count has nothing to compare against, so a differing total is not a failure.
    other = tmp_path / "no_count"
    _fixtures(other, type_count=None)
    assert _check(_run(other, _answers(total=7)), 12)["status"] == "pass"


def test_check_12_fails_when_search_docs_for_the_furnished_title_is_empty(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    check = _check(_run(tmp_path, _answers(docs=_empty_docs)), 12)
    assert check["status"] == "fail"
    assert "empty list under every content_type" in check["reason"]
    assert len(check["evidence"]["documentation_attempts"]) == len(pwp.CONTENT_TYPES)
    listing = _answers(docs=lambda arguments: [])
    assert _check(_run(tmp_path, listing), 12)["status"] == "fail"


def test_check_12_notes_that_documentation_is_enforced_offline_without_a_furnished_page(
    tmp_path: Path,
) -> None:
    _fixtures(tmp_path, furnished=False)
    fake = base.FakeClient(_answers(docs=_empty_docs))
    checks = pwp.run_checks(fake, pwp.load_pilot(PILOT, tmp_path), min_content=True)
    check = _check(checks, 12)
    assert check["status"] == "pass", check["reason"]
    assert "tests/deploy/test_fleet_consistency.py" in check["evidence"]["documentation"]
    assert "search_docs" not in fake.calls


def _main(root: Path, report: Path, answers: dict[str, Any]) -> int:
    return int(
        pwp.main(
            [
                "--pilot",
                PILOT,
                "--url",
                "http://127.0.0.1:9",
                "--fixtures",
                str(root),
                "--report",
                str(report),
            ],
            client_factory=lambda url: base.FakeClient(answers),
        )
    )


def test_unresolved_symbols_are_a_warning_not_a_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = tmp_path / "clean"
    dirty = tmp_path / "dirty"
    _fixtures(clean)
    _fixtures(dirty, unresolved=2)
    clean_code = _main(clean, tmp_path / "clean.json", base.passing_answers())
    capsys.readouterr()
    dirty_code = _main(dirty, tmp_path / "dirty.json", base.passing_answers())
    out = capsys.readouterr().out
    report = json.loads((tmp_path / "dirty.json").read_text(encoding="utf-8"))
    assert report["warnings"] == [
        f"{PILOT}: the extraction left 2 unresolved symbol(s) (see the fixture's unresolved list)"
    ]
    assert dirty_code == clean_code == 0
    assert report["all_applicable_passed"] is True
    assert any(line.startswith("WARNING ") and "2 unresolved symbol(s)" in line for line in out.splitlines())


def test_a_clean_fixture_has_an_empty_warnings_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fixtures(tmp_path)
    _main(tmp_path, tmp_path / "report.json", base.passing_answers())
    out = capsys.readouterr().out
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["warnings"] == []
    assert "WARNING" not in out
    assert pwp.pilot_warnings(None) == []
    unloadable = pwp.build_report("nope", "http://x", None, pwp.unrunnable_checks("no fixtures"))
    assert unloadable["warnings"] == []


def test_unrunnable_checks_with_min_content_has_twelve_entries() -> None:
    assert len(pwp.unrunnable_checks("nothing runs")) == 11
    twelve = pwp.unrunnable_checks("nothing runs", min_content=True)
    assert [c["id"] for c in twelve] == list(range(1, 13))
    assert {c["status"] for c in twelve} == {"fail"}
    assert twelve[-1]["name"] == pwp.CHECK_TITLES[12]


def test_load_pilot_reads_type_count_and_unresolved_count(tmp_path: Path) -> None:
    _fixtures(tmp_path / "with", type_count=TYPE_COUNT, unresolved=3)
    pilot = pwp.load_pilot(PILOT, tmp_path / "with")
    assert pilot.type_count == TYPE_COUNT
    assert pilot.unresolved_count == 3
    _fixtures(tmp_path / "without", type_count=None)
    bare = pwp.load_pilot(PILOT, tmp_path / "without")
    assert bare.type_count is None
    assert bare.unresolved_count == 0
