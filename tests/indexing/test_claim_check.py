"""Tests for src/foss_mcp/indexing/claim_check.py (G3/TC-648, REQ-G3-013).

The round-2 content audit's one confirmed fabrication across all 32 pilots,
``CELLS-CPP-001``, is cells/cpp's own committed FAQ answer claiming "`.csv` load is
supported" when its real ``LoadFormat`` enum (``{Auto, Xlsx}``) has no ``Csv`` member at
all. This module is the general cross-check the audit's own ``suggested_fix`` named: it
scans a furnished page's FAQ/content text for format-support claims and flags any that
contradict the pilot's own, already-extracted ``LoadFormat``/``SaveFormat`` enum data.

Tests run in three layers, outside-in:

* synthetic, hand-built text and enum data (isolates each rule);
* the real, committed ``cells_cpp`` fixture, both before this card's fix (read from the
  base commit this worker's branch descends from, via ``git show``) and after (the current
  committed file);
* a real, full sweep of all 32 committed pilot fixtures against their own committed
  ``api_surface.json`` files — the test the card's own negative control targets: reverting
  the ``cells_cpp`` fixture to its pre-fix text must make this one fail.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foss_mcp.indexing import claim_check as cc

# The commit this worker's branch (worker/TC-648) descends from (ops/instructions.jsonl's
# TC-648 dispatch entry's own issue_rev). Confirmed directly, not assumed: `git show
# <this commit>:tests/fixtures/furnished/cells_cpp/pages/_index.md` was read before writing
# this test and its FAQ answer is the real, original fabricated text this card fixes.
BASE_COMMIT = "c89b8f7dd70380c8311b18ffe241fe7b8ee74f4d"
CELLS_CPP_PAGE = "tests/fixtures/furnished/cells_cpp/pages/_index.md"


def _cells_cpp_api_surface() -> dict:
    path = cc.DEFAULT_API_SURFACE_ROOT / "cells_cpp" / "api_surface.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Synthetic tests: exercise each rule in isolation before touching any real fixture.
# ---------------------------------------------------------------------------


def test_format_claimed_supported_with_no_matching_enum_member_is_flagged() -> None:
    """A positive support claim with no matching enum member is a contradiction."""
    api_surface = {
        "types": [
            {"kind": "enum_specifier", "name": "LoadFormat", "enum_members": [{"name": "Xlsx", "value": "0"}]}
        ]
    }
    text = "`.csv` load is supported."
    found = cc.check_page_text("synthetic", "faq", "q", text, api_surface)
    assert len(found) == 1
    assert found[0].format_name == "csv"
    assert found[0].direction == "load"


def test_format_correctly_claimed_as_unsupported_is_not_flagged() -> None:
    """A negated claim ("is not supported") is never a contradiction, matching or not."""
    api_surface = {
        "types": [
            {"kind": "enum_specifier", "name": "LoadFormat", "enum_members": [{"name": "Xlsx", "value": "0"}]}
        ]
    }
    text = "`.csv` load is not supported."
    found = cc.check_page_text("synthetic", "faq", "q", text, api_surface)
    assert found == []


def test_format_genuinely_supported_is_not_flagged() -> None:
    """A format that IS a real enum member is never flagged for claiming support."""
    api_surface = {
        "types": [
            {"kind": "enum_specifier", "name": "LoadFormat", "enum_members": [{"name": "Xlsx", "value": "0"}]}
        ]
    }
    text = "`.xlsx` load is supported."
    found = cc.check_page_text("synthetic", "faq", "q", text, api_surface)
    assert found == []


def test_enum_match_is_case_insensitive() -> None:
    """'CSV' in text and 'Csv' as an enum member name are the same format."""
    api_surface = {
        "types": [
            {"kind": "enum_specifier", "name": "LoadFormat", "enum_members": [{"name": "Csv", "value": "0"}]}
        ]
    }
    text = "CSV load is supported."
    found = cc.check_page_text("synthetic", "faq", "q", text, api_surface)
    assert found == []


def test_direction_specific_check_uses_only_the_matching_enum() -> None:
    """A 'load' claim is checked against LoadFormat only, never against SaveFormat, even
    when the format is a real SaveFormat member."""
    api_surface = {
        "types": [
            {
                "kind": "enum_specifier",
                "name": "LoadFormat",
                "enum_members": [{"name": "Xlsx", "value": "0"}],
            },
            {"kind": "enum_specifier", "name": "SaveFormat", "enum_members": [{"name": "Csv", "value": "0"}]},
        ]
    }
    text = "`.csv` load is supported."
    found = cc.check_page_text("synthetic", "faq", "q", text, api_surface)
    assert len(found) == 1 and found[0].direction == "load"

    # The same format, claimed for save (where it IS a real enum member), is not flagged.
    text_save = "`.csv` save is supported."
    found_save = cc.check_page_text("synthetic", "faq", "q", text_save, api_surface)
    assert found_save == []


def test_ambiguous_direction_checks_the_union_of_available_enums() -> None:
    """A claim naming neither 'load' nor 'save' explicitly is checked against whichever
    enum(s) exist, and is not flagged if the format matches either one."""
    api_surface = {
        "types": [
            {"kind": "enum_specifier", "name": "SaveFormat", "enum_members": [{"name": "Pdf", "value": "0"}]}
        ]
    }
    text = "PDF export is supported."
    found = cc.check_page_text("synthetic", "overview", "t", text, api_surface)
    assert found == []


def test_enum_absent_entirely_is_not_treated_as_unsupported() -> None:
    """A pilot whose api_surface carries no LoadFormat/SaveFormat enum at all (true for 30
    of the 32 real committed pilots) has no ground truth for this check, so a format claim
    in its direction is never flagged — absence of the enum must not be read as "nothing is
    supported" (confirmed necessary: an earlier, ungated version of this module produced
    hundreds of false positives, every one in a pilot lacking this enum entirely)."""
    api_surface = {"types": []}
    text = "PDF export is supported. CSV load is supported."
    found = cc.check_page_text("synthetic", "overview", "t", text, api_surface)
    assert found == []


def test_enum_present_but_empty_is_real_ground_truth() -> None:
    """Unlike an absent enum, one that exists with zero members means "nothing is
    affirmatively known-supported" — a claim in that direction is still flagged."""
    api_surface = {"types": [{"kind": "enum_specifier", "name": "LoadFormat", "enum_members": []}]}
    text = "`.xlsx` load is supported."
    found = cc.check_page_text("synthetic", "faq", "q", text, api_surface)
    assert len(found) == 1


def test_clause_splitting_does_not_break_on_the_format_tokens_own_period() -> None:
    """A format token like "`.csv`" starts with a period immediately followed by a letter
    (never whitespace), so clause-splitting on sentence-ending periods must not cut the
    text apart in the middle of the token itself."""
    claims = cc.claimed_supported_formats("`.csv` load is supported.")
    assert ("csv", "load", "`.csv` load is supported") in claims


def test_multiple_clauses_in_one_answer_are_each_checked_independently() -> None:
    """The real CELLS-CPP-001 shape: one clause claims (and should be flagged), the next
    clause in the same answer, about the same format, negates it and is not flagged."""
    api_surface = {
        "types": [
            {"kind": "enum_specifier", "name": "LoadFormat", "enum_members": [{"name": "Xlsx", "value": "0"}]}
        ]
    }
    text = "`.xlsx` for both load and save. `.csv` load is supported; `.csv` save is not available in v0.1."
    found = cc.check_page_text("synthetic", "faq", "q", text, api_surface)
    assert len(found) == 1
    assert found[0].format_name == "csv"
    assert found[0].direction == "load"


# ---------------------------------------------------------------------------
# The real cells_cpp fixture: before this card's fix, and after it.
# ---------------------------------------------------------------------------


def _write_furnished_page(tmp_path: Path, pilot: str, text: str) -> Path:
    furnished_root = tmp_path / "furnished"
    page_path = furnished_root / pilot / "pages" / "_index.md"
    page_path.parent.mkdir(parents=True)
    page_path.write_text(text, encoding="utf-8")
    return furnished_root


def test_real_cells_cpp_fixture_before_the_fix_is_flagged(tmp_path: Path) -> None:
    """The real, historical committed fixture (read from the commit this card's branch
    descends from) is flagged by the verifier — proving the verifier would have caught the
    real CELLS-CPP-001 fabrication had it existed before the audit found it."""
    before_text = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:{CELLS_CPP_PAGE}"],
        cwd=cc.REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "`.csv` load is supported" in before_text, (
        "the base commit's own cells_cpp FAQ text no longer reads as expected; "
        "this test's premise (it is the real pre-fix text) would no longer hold"
    )

    furnished_root = _write_furnished_page(tmp_path, "cells_cpp", before_text)
    found = cc.check_pilot(
        "cells_cpp", furnished_root=furnished_root, api_surface_root=cc.DEFAULT_API_SURFACE_ROOT
    )
    assert len(found) == 1
    assert found[0].pilot == "cells_cpp"
    assert found[0].format_name == "csv"
    assert found[0].origin == "faq"


def test_real_cells_cpp_fixture_after_the_fix_is_not_flagged() -> None:
    """The same fixture, as currently committed (this card's own fix), is clean."""
    found = cc.check_pilot("cells_cpp")
    assert found == [], [c.describe() for c in found]


def test_fixed_fixture_no_longer_contains_the_fabricated_claim() -> None:
    """Direct textual confirmation of the fix: the fabricated 'csv load is supported'
    literal is gone from the real, committed file."""
    text = (cc.DEFAULT_FURNISHED_ROOT / "cells_cpp" / "pages" / "_index.md").read_text(encoding="utf-8")
    assert "`.csv` load is supported" not in text
    # The corrected answer still states the real ground truth plainly (no csv support at
    # all), it does not merely delete the sentence.
    assert "csv" in text.lower()
    assert "not supported" in text.lower()


# ---------------------------------------------------------------------------
# The real, full 32-pilot sweep. This is the test the card's own negative control targets:
# reverting the cells_cpp fixture to its pre-fix text must make this fail.
# ---------------------------------------------------------------------------


def test_discover_pilots_finds_all_32_real_pilots_and_excludes_sealed_current() -> None:
    pilots = cc.discover_pilots()
    assert len(pilots) == 32, pilots
    assert "sealed_current" not in pilots
    for known in ("cells_cpp", "slides_cpp", "pdf_net", "cells_rust"):
        assert known in pilots


def test_full_sweep_of_all_committed_pilots_finds_zero_contradictions() -> None:
    """The real cross-check, run for real against every committed pilot's own furnished
    page and api_surface.json. Zero contradictions after this card's fix (the negative
    control reverts cells_cpp's fixture and asserts this test then fails)."""
    contradictions = cc.sweep_all_pilots()
    assert contradictions == [], [c.describe() for c in contradictions]


def test_main_entry_point_exits_zero_when_clean(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cc.main()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "zero contradictions" in captured.out


def test_main_entry_point_exits_nonzero_when_a_pilot_has_a_contradiction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A synthetic end-to-end check: point the CLI's defaults at a tiny fixture tree with
    one deliberately contradictory pilot, confirm it reports and exits nonzero."""
    furnished_root = tmp_path / "furnished"
    api_surface_root = tmp_path / "api_surfaces"
    page_dir = furnished_root / "fake_pilot" / "pages"
    page_dir.mkdir(parents=True)
    (page_dir / "_index.md").write_text(
        "---\n"
        "faq:\n"
        "  enable: true\n"
        "  list:\n"
        "    - question: Which formats?\n"
        "      answer: CSV load is supported.\n"
        "---\n",
        encoding="utf-8",
    )
    surface_dir = api_surface_root / "fake_pilot"
    surface_dir.mkdir(parents=True)
    (surface_dir / "api_surface.json").write_text(
        json.dumps({"types": [{"kind": "enum_specifier", "name": "LoadFormat", "enum_members": []}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(cc, "DEFAULT_FURNISHED_ROOT", furnished_root)
    monkeypatch.setattr(cc, "DEFAULT_API_SURFACE_ROOT", api_surface_root)

    exit_code = cc.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "fake_pilot" in captured.out
    assert "csv" in captured.out
