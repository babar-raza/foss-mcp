"""G3/TC-492: scripts/release/build_review_ledger.py regenerates docs/REVIEW_LEDGER.md from the repository.

Offline and deterministic: no network, no git call (``last_data_commit`` is replaced), no wall clock. The status
rules are exercised on small synthetic findings against a synthetic repository under tmp_path; the last part pins
the REAL finding table that ships in the script.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
TOOL = RELEASE_DIR / "build_review_ledger.py"

sys.path.insert(0, str(RELEASE_DIR))

import build_review_ledger as ledger  # noqa: E402

F = ledger.F
A8 = ledger.A8
A9 = ledger.A9

GENERATED_AT = "2026-10-12T03:54:12Z"


def make_repo(
    root: Path,
    cards: dict[str, str] | None = None,
    *,
    section7: str = "",
    section8: str = "",
    section9: str = "",
    decision_log: str = "",
    release_images: list[Any] | None = None,
    receipts: dict[str, str] | None = None,
) -> Path:
    """Build a synthetic repository: state.yaml, an empty gatectl.py, RELEASE.md, DECISION_LOG.md and receipts."""
    (root / "project").mkdir(parents=True, exist_ok=True)
    (root / "ops").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "ops" / "gatectl.py").write_text("", encoding="utf-8")
    receipts = receipts or {}
    card_rows = []
    for cid, status in (cards or {}).items():
        row: dict[str, Any] = {"id": cid, "status": status}
        if cid in receipts:
            row["receipt"] = f"G3/{cid}/receipt.json"
        card_rows.append(row)
    state = {
        "schema_version": 1,
        "generated_at": GENERATED_AT,
        "cards": card_rows,
        "release_images": release_images if release_images is not None else [],
    }
    (root / "project" / "state.yaml").write_text(yaml.safe_dump(state), encoding="utf-8")
    (root / "docs" / "RELEASE.md").write_text(
        f"# Release\n\n## 7. How a peer pulls\n{section7}\n\n## 8. Known limitations\n{section8}\n\n"
        f"## 9. Deferred extraction improvements\n{section9}\n",
        encoding="utf-8",
    )
    (root / "docs" / "DECISION_LOG.md").write_text(decision_log, encoding="utf-8")
    for cid, stamp in receipts.items():
        path = root / "evidence" / "build" / "G3" / cid / "receipt.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"generated_at": stamp}), encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test calls git: by default there is no data commit."""
    monkeypatch.setattr(ledger, "last_data_commit", lambda repo, kind: None)


def use_findings(monkeypatch: pytest.MonkeyPatch, *findings: ledger.Finding) -> None:
    monkeypatch.setattr(ledger, "FINDINGS", list(findings))


def one(repo: Path, require_data: bool = False) -> ledger.Row:
    rows, _meta = ledger.compute(repo, require_data)
    assert len(rows) == 1
    return rows[0]


# --- (a) FIXED / OPEN from the card states -------------------------------------------------------------------


def test_fixed_when_every_card_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "a defect", "TC-900, TC-901", check="rv1 C01"))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED", "TC-901": "ACCEPTED"}))
    assert row.status == "FIXED"
    assert "Fixed by TC-900 (ACCEPTED), TC-901 (ACCEPTED)." in row.evidence
    assert "Check on the final image: rv1 C01." in row.evidence


def test_fixed_when_a_card_is_superseded_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G3/TC-576: a SUPERSEDED card's work was fully carried by its accepted successor, exactly the
    precedent ops/gatecli.py's own `_accepted_or_superseded_cards()` already follows - so a finding
    whose closing cards are a mix of ACCEPTED and SUPERSEDED resolves as done, not pending."""
    use_findings(monkeypatch, F("L-001", "Blocker", "a defect", "TC-900, TC-901", check="rv1 C01"))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED", "TC-901": "SUPERSEDED"}))
    assert row.status == "FIXED"
    assert row.pending == []
    assert "Fixed by TC-900 (ACCEPTED), TC-901 (SUPERSEDED)." in row.evidence


def test_in_progress_card_still_blocks_a_finding_even_alongside_a_superseded_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative proof for the SUPERSEDED fix above: a card that is merely IN_PROGRESS (neither ACCEPTED
    nor SUPERSEDED) must still keep the finding OPEN and pending on it - this is the test that would
    have caught a careless 'treat everything as done' overcorrection."""
    use_findings(monkeypatch, F("L-001", "Blocker", "a defect", "TC-900, TC-901", check="rv1 C01"))
    row = one(make_repo(tmp_path, {"TC-900": "IN_PROGRESS", "TC-901": "SUPERSEDED"}))
    assert row.status == "OPEN"
    assert row.pending == [("TC-900", "IN_PROGRESS")]
    assert "Waiting for TC-900 (IN_PROGRESS)." in row.evidence
    assert "Done: TC-901 (SUPERSEDED)." in row.evidence


def test_fixed_without_a_check_names_the_cards_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Minor", "a defect", "TC-900"))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED"}))
    assert row.status == "FIXED"
    assert "the committed tests of the fixing cards" in row.evidence


def test_open_names_the_pending_card_and_its_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "a defect", "TC-900, TC-901"))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED", "TC-901": "IN_PROGRESS"}))
    assert row.status == "OPEN"
    assert row.pending == [("TC-901", "IN_PROGRESS")]
    assert "Waiting for TC-901 (IN_PROGRESS)." in row.evidence
    assert "Done: TC-900 (ACCEPTED)." in row.evidence


def test_open_for_a_card_missing_from_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "a defect", "TC-999"))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED"}))
    assert row.status == "OPEN"
    assert "TC-999 (UNKNOWN)" in row.evidence


def test_open_when_there_is_no_card_and_no_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Minor", "an orphan"))
    row = one(make_repo(tmp_path))
    assert row.status == "OPEN"
    assert "No card and no recorded decision." in row.evidence


def test_sources_are_appended_to_the_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Minor", "a defect", "TC-900", sources="audit A A-001"))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED"}))
    assert row.evidence.endswith("Sources: audit A A-001.")


# --- (b) ACCEPTED decisions -----------------------------------------------------------------------------------


def test_accepted_a8_row_with_its_id_bold_in_section_8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(monkeypatch, F("L-001", "Minor", "a limitation", accepted=A8("shipped on purpose")))
    row = one(make_repo(tmp_path, section8="- **L-001** shipped on purpose"))
    assert row.status == "ACCEPTED"
    assert row.release_missing == []
    assert row.evidence.startswith("Accepted: shipped on purpose.")
    assert "See docs/RELEASE.md section 8 (**L-001**)." in row.evidence


def test_accepted_with_a_fixed_part_names_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(
        monkeypatch,
        F("L-001", "Major", "half fixed", "TC-900", accepted=A8("the rest ships"), check="rv2 core"),
    )
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED"}, section8="**L-001**"))
    assert row.status == "ACCEPTED"
    assert "Fixed part: TC-900 (ACCEPTED)." in row.evidence
    assert "Check: rv2 core." in row.evidence


def test_open_with_the_accepted_part_named_while_a_card_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "half fixed", "TC-900", accepted=A8("the rest ships")))
    row = one(make_repo(tmp_path, {"TC-900": "IN_REVIEW"}, section8="**L-001**"))
    assert row.status == "OPEN"
    assert "Waiting for TC-900 (IN_REVIEW)." in row.evidence
    assert "Accepted part, final once the cards above are ACCEPTED: the rest ships" in row.evidence
    assert row.release_missing == []


def test_gap_without_acceptance_stays_open_although_every_card_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "partly fixed", "TC-900", gap="the unfixed part"))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED"}))
    assert row.status == "OPEN"
    assert "Not covered: the unfixed part." in row.evidence


def test_a_gap_covered_by_an_acceptance_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "partly fixed", "TC-900", gap="g", accepted=A8("covered")))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED"}, section8="**L-001**"))
    assert row.status == "ACCEPTED"
    assert "Not covered" not in row.evidence


def test_a9_row_is_accepted_and_checked_against_section_9_card_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(
        monkeypatch, F("L-001", "Major", "deferred", accepted=A9("cut from this release", "TC-397", "TC-402"))
    )
    present = one(make_repo(tmp_path / "a", section9="- **TC-397** and **TC-402** are deferred"))
    assert present.status == "ACCEPTED"
    assert present.release_missing == []
    assert "See docs/RELEASE.md section 9 (**TC-397**, **TC-402**)." in present.evidence
    partial = one(make_repo(tmp_path / "b", section9="- **TC-397** is deferred"))
    assert partial.status == "ACCEPTED"
    assert partial.release_missing == ["TC-402"]
    # An A9 row is not satisfied by the ledger id in section 8.
    wrong = one(make_repo(tmp_path / "c", section8="**L-001**"))
    assert wrong.release_missing == ["TC-397", "TC-402"]


def test_a_not_a_defect_acceptance_renders_not_a_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(
        monkeypatch,
        F("L-001", "Polish", "false alarm", accepted=A8("it works", kind=ledger.NOT_DEFECT)),
        F("L-002", "Polish", "a limitation", accepted=A8("it ships")),
    )
    rows, _ = ledger.compute(make_repo(tmp_path, section8="**L-001** **L-002**"), False)
    assert rows[0].evidence.startswith("Not a defect: it works.")
    assert rows[1].evidence.startswith("Accepted: it ships.")


# --- (c) the RELEASE.md warning -------------------------------------------------------------------------------


def _warnings(repo: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    use_findings(monkeypatch, F("L-001", "Minor", "a limitation", accepted=A8("it ships")))
    rows, _ = ledger.compute(repo, False)
    return ledger.post_checks(rows)


def test_warning_names_an_accepted_id_absent_from_section_8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = _warnings(make_repo(tmp_path), monkeypatch)
    assert len(warnings) == 1
    assert "ACCEPTED rows not found in docs/RELEASE.md" in warnings[0]
    assert "L-001 (needs **L-001** in section 8)" in warnings[0]


def test_warning_names_nothing_when_the_id_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _warnings(make_repo(tmp_path, section8="- **L-001** it ships"), monkeypatch) == []


def test_an_id_bold_only_in_section_7_does_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    warnings = _warnings(make_repo(tmp_path, section7="**L-001**"), monkeypatch)
    assert len(warnings) == 1
    assert "L-001" in warnings[0]


def test_an_unbolded_id_does_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(_warnings(make_repo(tmp_path, section8="L-001 is mentioned plainly"), monkeypatch)) == 1


def test_a9_warning_asks_for_the_card_id_in_section_9(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(monkeypatch, F("L-001", "Minor", "deferred", accepted=A9("cut", "TC-397")))
    rows, _ = ledger.compute(make_repo(tmp_path), False)
    warnings = ledger.post_checks(rows)
    assert warnings == ["ACCEPTED rows not found in docs/RELEASE.md: L-001 (needs **TC-397** in section 9)"]


def test_a_fixed_row_that_names_no_card_is_warned_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(
        monkeypatch,
        F(
            "L-001",
            "Minor",
            "recorded",
            record=ledger.Record("a note", "docs/DECISION_LOG.md", regex="^## done"),
        ),
    )
    rows, _ = ledger.compute(make_repo(tmp_path, decision_log="## done\n"), False)
    assert rows[0].status == "FIXED"
    assert ledger.post_checks(rows) == ["L-001: FIXED row names no TC- card id"]


def test_release_sections_collects_bold_ids_per_numbered_section(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, section7="**L-007**", section8="**L-008** **L-018**", section9="**TC-397**")
    (repo / "docs" / "RELEASE.md").write_text(
        (repo / "docs" / "RELEASE.md").read_text(encoding="utf-8") + "\n## Appendix\n**L-099**\n",
        encoding="utf-8",
    )
    assert ledger.release_sections(repo) == {"7": {"L-007"}, "8": {"L-008", "L-018"}, "9": {"TC-397"}}


# --- (d) Record rows -----------------------------------------------------------------------------------------


def test_record_regex_row_is_open_until_the_decision_log_line_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = ledger.Record("correction entry", "docs/DECISION_LOG.md", regex=r"^## .*[Cc]orrect.*\bC1\b")
    use_findings(monkeypatch, F("L-001", "Minor", "closure claimed", record=rec))
    before = one(make_repo(tmp_path / "a", decision_log="## 2026-10-12 — something else\n"))
    assert before.status == "OPEN"
    assert "Waiting for the correction entry." in before.evidence
    after = one(
        make_repo(tmp_path / "b", decision_log="# Log\n\n## 2026-10-12 — Correction of C1 (supervisor)\n")
    )
    assert after.status == "FIXED"
    assert "Fixed by the correction entry." in after.evidence


def test_record_yaml_key_row_needs_a_non_empty_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = ledger.Record("release record", "project/state.yaml", yaml_key="release_images")
    use_findings(monkeypatch, F("L-001", "Minor", "no release record", record=rec))
    assert one(make_repo(tmp_path / "a", release_images=[])).status == "OPEN"
    assert one(make_repo(tmp_path / "b", release_images=[{"tag": "x"}])).status == "FIXED"


def test_record_on_a_missing_file_is_not_ok(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert not ledger.record_ok(repo, ledger.Record("x", "docs/NOPE.md", regex="a"))
    assert not ledger.record_ok(repo, ledger.Record("x", "docs/DECISION_LOG.md"))


def test_a_record_and_cards_must_both_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = ledger.Record("note", "docs/DECISION_LOG.md", regex="^## done")
    use_findings(monkeypatch, F("L-001", "Minor", "both", "TC-900", record=rec))
    assert one(make_repo(tmp_path / "a", {"TC-900": "ACCEPTED"})).status == "OPEN"
    assert one(make_repo(tmp_path / "b", {"TC-900": "PENDING"}, decision_log="## done\n")).status == "OPEN"
    assert one(make_repo(tmp_path / "c", {"TC-900": "ACCEPTED"}, decision_log="## done\n")).status == "FIXED"


# --- (e) baked data and --require-data ------------------------------------------------------------------------


def _reingest_finding() -> ledger.Finding:
    return F("L-001", "Major", "a data fix", "TC-900", effect="reingest", check="rv3 D10")


def _data_commit(stamp: datetime) -> Any:
    return lambda repo, kind: (f"abc12345 (TC-354) [{kind}]", stamp)


def test_require_data_keeps_a_reingest_fix_open_while_the_data_commit_is_older(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(monkeypatch, _reingest_finding())
    monkeypatch.setattr(ledger, "last_data_commit", _data_commit(datetime(2026, 10, 10, tzinfo=UTC)))
    repo = make_repo(tmp_path, {"TC-900": "ACCEPTED"}, receipts={"TC-900": "2026-10-11T12:00:00Z"})
    row = one(repo, require_data=True)
    assert row.status == "OPEN"
    assert row.awaiting_data
    assert "Waiting for the final re-ingest and demo-manifest snapshot" in row.evidence


def test_require_data_is_fixed_once_the_data_commit_is_newer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(monkeypatch, _reingest_finding())
    monkeypatch.setattr(ledger, "last_data_commit", _data_commit(datetime(2026, 10, 12, tzinfo=UTC)))
    repo = make_repo(tmp_path, {"TC-900": "ACCEPTED"}, receipts={"TC-900": "2026-10-11T12:00:00Z"})
    row = one(repo, require_data=True)
    assert row.status == "FIXED"
    assert not row.awaiting_data
    assert "Reaches the image with the final re-ingest and demo-manifest snapshot." in row.evidence


def test_default_mode_reports_the_stale_data_fix_fixed_with_the_not_yet_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(monkeypatch, _reingest_finding())
    monkeypatch.setattr(ledger, "last_data_commit", _data_commit(datetime(2026, 10, 10, tzinfo=UTC)))
    repo = make_repo(tmp_path, {"TC-900": "ACCEPTED"}, receipts={"TC-900": "2026-10-11T12:00:00Z"})
    row = one(repo, require_data=False)
    assert row.status == "FIXED"
    assert row.awaiting_data
    assert "Not yet in the baked data" in row.evidence


def test_a_missing_data_commit_counts_as_awaiting_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(monkeypatch, _reingest_finding())
    repo = make_repo(tmp_path, {"TC-900": "ACCEPTED"}, receipts={"TC-900": "2026-10-11T12:00:00Z"})
    assert one(repo, require_data=True).status == "OPEN"


def test_a_rebuild_effect_never_waits_for_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "image fix", "TC-900", effect="rebuild"))
    row = one(make_repo(tmp_path, {"TC-900": "ACCEPTED"}, receipts={"TC-900": "2026-10-11T12:00:00Z"}), True)
    assert row.status == "FIXED"
    assert "Reaches the image with the serving image rebuild." in row.evidence


def test_receipt_time_reads_generated_at_and_tolerates_a_missing_receipt(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path, {"TC-900": "ACCEPTED", "TC-901": "ACCEPTED"}, receipts={"TC-900": "2026-10-11T12:00:00Z"}
    )
    assert ledger.receipt_time(repo, {"id": "TC-900", "receipt": "G3/TC-900/receipt.json"}) == datetime(
        2026, 10, 11, 12, tzinfo=UTC
    )
    assert ledger.receipt_time(repo, {"id": "TC-901", "receipt": "G3/TC-901/receipt.json"}) is None
    assert ledger.receipt_time(repo, {"id": "TC-902"}) is None


# --- (f) validate_data -----------------------------------------------------------------------------------------


def test_validate_data_is_empty_for_a_clean_table(monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(
        monkeypatch, F("L-001", "Major", "a", "TC-900"), F("L-002", "Polish", "b", accepted=A9("c", "TC-397"))
    )
    assert ledger.validate_data() == []


@pytest.mark.parametrize(
    ("findings", "expected"),
    [
        pytest.param(
            [F("L-001", "Major", "a"), F("L-001", "Major", "b")],
            "duplicate id L-001",
            id="duplicate-id",
        ),
        pytest.param([F("L-001", "Severe", "a")], "L-001: bad severity Severe", id="bad-severity"),
        pytest.param([F("L-001", "Major", "a", "TC-9")], "L-001: bad card id TC-9", id="bad-card-id"),
        pytest.param(
            [F("L-001", "Major", "a"), F("L-003", "Major", "b")],
            "ids are not L-001.. in order without gaps",
            id="id-sequence-gap",
        ),
        pytest.param(
            [F("L-001", "Major", "a", accepted=A9("cut"))],
            "L-001: a section-9 acceptance must name the deferred cards",
            id="a9-without-cards",
        ),
        pytest.param(
            [F("L-001", "Major", "a", record=ledger.Record("r", "docs/X.md"))],
            "L-001: a record needs a regex or a yaml_key",
            id="record-without-matcher",
        ),
    ],
)
def test_validate_data_reports(
    monkeypatch: pytest.MonkeyPatch, findings: list[ledger.Finding], expected: str
) -> None:
    use_findings(monkeypatch, *findings)
    assert expected in ledger.validate_data()


def test_f_splits_a_comma_separated_card_string() -> None:
    assert F("L-001", "Major", "a", "TC-900, TC-901 ,TC-902").cards == ("TC-900", "TC-901", "TC-902")
    assert F("L-001", "Major", "a", ["TC-900"]).cards == ("TC-900",)


# --- (g) rendering ----------------------------------------------------------------------------------------------


def test_render_emits_one_markdown_table_with_one_row_per_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_findings(
        monkeypatch,
        F("L-001", "Major", "first", "TC-900"),
        F("L-002", "Minor", "second", accepted=A8("shipped")),
        F("L-003", "Polish", "third"),
    )
    rows, meta = ledger.compute(make_repo(tmp_path, {"TC-900": "ACCEPTED"}, section8="**L-002**"), False)
    text = ledger.render(rows, meta, "2026-10-12", "rev1")
    lines = text.splitlines()
    assert lines.count("| ID | Finding | Severity | Status | Evidence |") == 1
    assert lines.count("|---|---|---|---|---|") == 1
    table = [line for line in lines if line.startswith("|")]
    assert len(table) == 2 + 3
    assert [line.split(" | ")[0] for line in table[2:]] == ["| L-001", "| L-002", "| L-003"]
    assert "Totals: 3 findings, 1 FIXED, 1 ACCEPTED, 1 OPEN." in text
    assert "Date: 2026-10-12. Image revision: rev1." in text
    assert text.endswith("\n")


def test_render_escapes_a_pipe_in_a_cell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "uses a | pipe\nand a newline", "TC-900"))
    rows, meta = ledger.compute(make_repo(tmp_path, {"TC-900": "ACCEPTED"}), False)
    row_line = next(
        line for line in ledger.render(rows, meta, "d", "r").splitlines() if line.startswith("| L-001")
    )
    assert "uses a \\| pipe and a newline" in row_line
    assert len(re.findall(r"(?<!\\)\|", row_line)) == 6


# --- (h) main: determinism, --check, errors -----------------------------------------------------------------------


def _two_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    use_findings(
        monkeypatch,
        F("L-001", "Major", "first", "TC-900", effect="rebuild"),
        F("L-002", "Minor", "second", accepted=A8("shipped")),
    )


def test_main_writes_byte_identical_files_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _two_findings(monkeypatch)
    repo = make_repo(tmp_path / "repo", {"TC-900": "ACCEPTED"}, section8="**L-002**")
    first, second = tmp_path / "one.md", tmp_path / "two.md"
    assert ledger.main(["--repo", str(repo), str(first)]) == 0
    assert ledger.main(["--repo", str(repo), str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    assert b"\r\n" not in first.read_bytes()


def test_main_default_date_is_the_date_part_of_generated_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_findings(monkeypatch)
    repo = make_repo(tmp_path / "repo", {"TC-900": "ACCEPTED"}, section8="**L-002**")
    out = tmp_path / "ledger.md"
    assert ledger.main(["--repo", str(repo), str(out)]) == 0
    assert "Date: 2026-10-12. Image revision: " in out.read_text(encoding="utf-8")
    assert (
        ledger.main(["--repo", str(repo), "--date", "2030-01-02", "--image-revision", "abc", str(out)]) == 0
    )
    assert "Date: 2030-01-02. Image revision: abc." in out.read_text(encoding="utf-8")


def test_main_check_returns_zero_and_prints_the_totals_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _two_findings(monkeypatch)
    repo = make_repo(tmp_path, {"TC-900": "ACCEPTED"}, section8="**L-002**")
    assert ledger.main(["--repo", str(repo), "--check"]) == 0
    out = capsys.readouterr().out
    assert "total 2: FIXED 1, ACCEPTED 1, OPEN 0" in out
    assert "OPEN rows (0):" in out
    assert "generated_at 2026-10-12T03:54:12Z; require_data=False" in out


def test_main_check_lists_the_open_rows_and_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "waiting", "TC-900"), F("L-002", "Minor", "orphan"))
    repo = make_repo(tmp_path, {"TC-900": "IN_PROGRESS"})
    assert ledger.main(["--repo", str(repo), "--check"]) == 0
    out = capsys.readouterr().out
    assert "L-001 [Major] pending TC-900 IN_PROGRESS" in out
    assert "L-002 [Minor] no card and no decision" in out


def test_main_prints_the_ledger_to_stdout_without_an_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _two_findings(monkeypatch)
    repo = make_repo(tmp_path, {"TC-900": "ACCEPTED"}, section8="**L-002**")
    assert ledger.main(["--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Review ledger\n")
    assert "| L-002 | second | Minor | ACCEPTED |" in out


def test_main_returns_2_on_invalid_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_findings(monkeypatch, F("L-001", "Major", "a"), F("L-001", "Major", "b"))
    repo = make_repo(tmp_path)
    assert ledger.main(["--repo", str(repo), "--check"]) == 2
    assert "ERROR: duplicate id L-001" in capsys.readouterr().err


def test_main_returns_2_when_no_repository_root_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _two_findings(monkeypatch)
    lonely = tmp_path / "nowhere" / "build_review_ledger.py"
    lonely.parent.mkdir()
    monkeypatch.setattr(ledger, "__file__", str(lonely))
    assert ledger.main(["--check"]) == 2
    assert "ERROR: repository root not found; pass --repo" in capsys.readouterr().err


def test_find_repo_prefers_the_explicit_path(tmp_path: Path) -> None:
    assert ledger.find_repo(str(tmp_path)) == tmp_path.resolve()


def test_find_repo_walks_up_from_the_script_to_the_root_holding_state_and_gatectl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "root")
    script = repo / "scripts" / "release" / "build_review_ledger.py"
    script.parent.mkdir(parents=True)
    monkeypatch.setattr(ledger, "__file__", str(script))
    assert ledger.find_repo(None) == repo.resolve()


def test_find_repo_needs_both_state_and_gatectl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path / "root")
    (repo / "ops" / "gatectl.py").unlink()
    script = repo / "scripts" / "release" / "build_review_ledger.py"
    script.parent.mkdir(parents=True)
    monkeypatch.setattr(ledger, "__file__", str(script))
    found = ledger.find_repo(None)
    assert found is None or found != repo.resolve()


# --- (i) the REAL module -----------------------------------------------------------------------------------------


def _real(fid: str) -> ledger.Finding:
    return next(f for f in ledger.FINDINGS if f.id == fid)


def test_real_validate_data_is_empty() -> None:
    assert ledger.validate_data() == []


def test_real_ids_run_from_l_001_to_l_133_in_order() -> None:
    assert [f.id for f in ledger.FINDINGS] == [f"L-{n:03d}" for n in range(1, 134)]


@pytest.mark.parametrize("fid", ["L-054", "L-080"])
def test_real_l054_and_l080_carry_a_section_8_acceptance_and_no_gap(fid: str) -> None:
    finding = _real(fid)
    assert finding.accepted is not None
    assert finding.accepted.section == "8"
    assert finding.accepted.kind == "limitation"
    assert finding.gap == ""


def test_real_l054_acceptance_states_the_one_word_rule() -> None:
    reason = _real("L-054").accepted.reason  # type: ignore[union-attr]
    # TC-543 changed the rule (a one- or two-word query needs a title word); TC-546 re-pinned the reason.
    assert "title" in reason
    assert "60%" in reason


def test_real_l080_acceptance_states_the_probe_rule() -> None:
    reason = _real("L-080").accepted.reason  # type: ignore[union-attr]
    assert "/readyz needs no authentication" in reason
    assert "tex/python" in reason


def test_real_l059_names_tc_493_and_has_no_gap() -> None:
    finding = _real("L-059")
    assert finding.cards == ("TC-381", "TC-386", "TC-493")
    assert finding.gap == ""
    assert "TC-493" in finding.note


def test_real_script_source_has_no_machine_path_and_no_scratchpad() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert not re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", source)
    assert "scratchpad" not in source.lower()
    assert "FALLBACK_REPO" not in source
    assert "build_ledger.py" not in source
    assert "python scripts/release/build_review_ledger.py" in source


def test_real_compute_keeps_exactly_one_accepted_status_assignment() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert source.count('status = "ACCEPTED"') == 1
