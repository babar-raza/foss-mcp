"""G3/TC-597 (structural checks), extended by G3/TC-777 (Q17 closure): checks for
docs/ROUND2_REVIEW.md, the round-2 32-pilot portfolio audit register.

This module parses docs/ROUND2_REVIEW.md's "## 3. Register" table and proves, programmatically (never by
eyeballing the table), that:

  (a) the register has exactly 286 rows, `RD2-001` through `RD2-286`, in that exact sequential order (the id
      in row i must literally equal ``f"RD2-{i+1:03d}"`` - this is what the card's negative control targets:
      renaming the first row's id must fail this check, proving the test actually reads the table rather than
      just counting rows or distinct ids);
  (b) for the first 285 rows, every row's Sev/Cat/Pilot column matches, byte-for-byte, the
      severity/category/pilot field of the issue at the same position (file order) in docs/round2/issues.json;
  (c) for the first 285 rows, every row's WS/Cards column matches the workstream the round-2 remediation plan's
      Appendix A assigns to that issue's real id, and the exact "Closing cards" text Appendix A records for
      that workstream (the Appendix A table is baked into this test file as ``APPENDIX_A``, copied verbatim from
      ``C:\\Users\\babar\\.claude\\plans\\steady-wobbling-meteor.md``, since that plan file lives outside this
      repository and this test must stay offline and hermetic - see AGENTS.md's S9 authoring rule: no card test
      reads live external state);
  (d) row 286 (``RD2-286``) carries the fixed LONGQ-001 fields the card specifies;
  (e) every row's Status is one of the real, defined disposition values (`OPEN`/`FIXED`/`DISCLOSED`/
      `NOT_REPRODUCED`/`DEFINITIONAL`/`ACCEPTED(P3)`/`BLOCKED_UPSTREAM`) - this replaces TC-597's own original
      precondition snapshot ("every row starts OPEN"), which TC-777 (Q17) necessarily broke by live-verifying
      and dispositioning the 73 real Q-series rows away from OPEN;
  (f) the baked Appendix A mapping itself is total and disjoint over the real 285 issue ids (a self-check on the
      baked copy, independent of the committed register).

Offline, deterministic, no network, no git calls, no state.yaml/open_questions.jsonl/receipts reads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypedDict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_DOC = REPO_ROOT / "docs" / "ROUND2_REVIEW.md"
ISSUES_JSON = REPO_ROOT / "docs" / "round2" / "issues.json"

REGISTER_COLUMNS = ("Id", "Sev", "Cat", "Pilot", "WS", "Cards", "Finding", "Evidence", "Status")


# Appendix A, copied verbatim from the round-2 remediation plan
# (C:\Users\babar\.claude\plans\steady-wobbling-meteor.md, "Appendix A: issue ledger (workstream -> closing
# cards)"). This is the authoritative workstream assignment for all 285 issues; it is baked in here (rather
# than read from the plan file at test time) because the plan file is outside this repository and this test
# must be offline, deterministic and hermetic.
class _WorkstreamEntry(TypedDict):
    cards: str
    ids: list[str]


APPENDIX_A: dict[str, _WorkstreamEntry] = {
    "A": {
        "cards": "Q8 (label precision, PDF-JAVA-005), Q10 (IMAGING-NET-011), Q1+Q12 (PDF-GO-006), KD4 (NOTE-PY-005)",
        "ids": "CELLS-CPP-007 CELLS-RUST-001 CELLS-RUST-007 HTML-PY-007 IMAGING-NET-011 NOTE-PY-005 PDF-GO-006 PDF-JAVA-005 SLIDES-CPP-004 WORDS-PY-004".split(),
    },
    "B": {
        "cards": "Q11-Q16, I5; probes in F6 for WORDS-PY-001, EMAIL-PY-003",
        "ids": "3D-JAVA-001 3D-JAVA-008 3D-NET-013 3D-PYTHON-003 3D-TYPESCRIPT-002 BARCODE-PY-003 CELLS-CPP-008 CELLS-GO-005 CELLS-GO-007 CELLS-PY-008 CELLS-RUST-009 CELLS-TYPESCRIPT-010 EMAIL-CPP-008 EMAIL-PY-003 EML-NET-001 NOTE-PY-004 PDF-TS-001 PDF-TS-006 SLIDES-JAVA-005 SLIDES-NET-003 WORDS-NET-004 WORDS-PY-001 WORDS-PY-002".split(),
    },
    "C": {
        "cards": "Q3, Q10 (+XA9, XA10, AD-* for dead corpora CELLS-JAVA-002, SLIDES-CPP-001, TEX-PYTHON-004, BARCODE-PY-001)",
        "ids": "3D-NET-009 3D-PYTHON-007 BARCODE-PY-001 CELLS-GO-003 CELLS-JAVA-002 CELLS-NET-003 CELLS-RUST-004 EMAIL-CPP-005 EMAIL-PY-001 EML-NET-002 HTML-PY-006 PDF-NET-002 PDF-PYTHON-002 SLIDES-CPP-001 SLIDES-JAVA-008 SLIDES-NET-004 TEX-PYTHON-004".split(),
    },
    "D": {
        "cards": "XA1 (3D-PYTHON-005), XA3-XA4 (3D-NET-002, WORDS-PY-010, PDF-GO-003, WORDS-NET-009), XA8 (NOTE-PY-006), KD3 (SLIDES-JAVA-003), RT3 (PDF-CPP-011), XA9-XA19 + AD-* (thin corpora)",
        "ids": "3D-JAVA-009 3D-NET-002 3D-PYTHON-005 3D-TYPESCRIPT-003 CELLS-CPP-003 CELLS-PY-006 CELLS-RUST-002 CELLS-TYPESCRIPT-002 IMAGING-NET-007 NOTE-PY-006 PAGE-PYTHON-001 PDF-CPP-002 PDF-CPP-008 PDF-CPP-011 PDF-GO-002 PDF-GO-003 PDF-JAVA-004 PDF-NET-003 PDF-NET-006 PDF-TS-007 SLIDES-JAVA-003 SLIDES-PY-004 WORDS-NET-002 WORDS-NET-009 WORDS-PY-003 WORDS-PY-010".split(),
    },
    "E": {
        "cards": "I1-I5, H8; CELLS-GO-010 and CELLS-RUST-005 (delegating aliases) end DEFINITIONAL or DISCLOSED unless I1 detects them",
        "ids": "3D-PYTHON-004 3D-TYPESCRIPT-007 BARCODE-PY-002 CELLS-GO-006 CELLS-GO-010 CELLS-RUST-005 EMAIL-PY-004 EMAIL-PY-005 HTML-PY-004 PAGE-PYTHON-008 PDF-CPP-003 PDF-GO-001 SLIDES-PY-003 TEX-PYTHON-002 TEX-PYTHON-007 WORDS-NET-010 WORDS-PY-011".split(),
    },
    "F": {
        "cards": "I9-I13, O5-O6, Q15 (PDF-TS-009), RT3 (WORDS-NET-008); CELLS-NET-007 is P3 and may be ACCEPTED",
        "ids": "3D-PYTHON-009 3D-TYPESCRIPT-004 3D-TYPESCRIPT-006 CELLS-NET-001 CELLS-NET-007 EMAIL-CPP-007 EMAIL-PY-002 EML-NET-006 HTML-PY-003 NOTE-PY-007 NOTE-PY-009 PDF-GO-004 PDF-NET-001 PDF-NET-007 PDF-PYTHON-001 PDF-TS-002 PDF-TS-004 PDF-TS-009 TEX-PYTHON-003 WORDS-NET-005 WORDS-NET-006 WORDS-NET-008".split(),
    },
    "G": {
        "cards": "C1-C12 (+KD4 for false README claims: 3D-NET-001, SLIDES-PY-002, HTML-PY-001/005, PDF-JAVA-003, SLIDES-JAVA-001/002), C9 (CELLS-CPP-001)",
        "ids": "3D-JAVA-005 3D-NET-001 3D-NET-005 3D-NET-011 3D-NET-012 3D-PYTHON-011 3D-TYPESCRIPT-001 BARCODE-PY-004 BARCODE-PY-005 CELLS-CPP-001 CELLS-CPP-002 CELLS-CPP-005 CELLS-GO-002 CELLS-GO-008 CELLS-GO-009 CELLS-JAVA-003 CELLS-NET-005 CELLS-PY-001 CELLS-PY-002 CELLS-PY-010 CELLS-RUST-010 CELLS-TYPESCRIPT-007 CELLS-TYPESCRIPT-012 EMAIL-CPP-006 EML-NET-004 HTML-PY-001 HTML-PY-002 HTML-PY-005 IMAGING-NET-005 IMAGING-NET-006 IMAGING-NET-010 NOTE-PY-001 PAGE-PYTHON-002 PAGE-PYTHON-007 PDF-CPP-004 PDF-CPP-009 PDF-CPP-010 PDF-CPP-012 PDF-GO-007 PDF-JAVA-003 PDF-JAVA-006 PDF-JAVA-007 PDF-PYTHON-003 PDF-PYTHON-005 PDF-PYTHON-006 PDF-TS-003 PDF-TS-005 SLIDES-CPP-005 SLIDES-CPP-006 SLIDES-CPP-008 SLIDES-JAVA-001 SLIDES-JAVA-002 SLIDES-JAVA-006 SLIDES-JAVA-009 SLIDES-NET-001 SLIDES-NET-002 SLIDES-NET-008 SLIDES-PY-002 TEX-PYTHON-006 WORDS-NET-003 WORDS-PY-005 WORDS-PY-006 WORDS-PY-007".split(),
    },
    "H": {
        "cards": "I6-I8, KD5, O1-O3 (WORDS-NET-001: declared in source, absent in package)",
        "ids": "3D-NET-007 3D-TYPESCRIPT-005 CELLS-CPP-006 CELLS-GO-004 CELLS-NET-006 CELLS-PY-009 EML-NET-005 NOTE-PY-003 PAGE-PYTHON-004 PDF-NET-005 PDF-NET-008 SLIDES-JAVA-004 SLIDES-JAVA-007 WORDS-NET-001".split(),
    },
    "I": {
        "cards": "C1, C2, C3 (empty categories get real chunks or a reason)",
        "ids": "3D-JAVA-010 3D-NET-015 3D-TYPESCRIPT-009 CELLS-JAVA-004 CELLS-PY-007 CELLS-RUST-008 EMAIL-PY-007 HTML-PY-008 PDF-CPP-006 PDF-CPP-007 PDF-GO-005 PDF-NET-004 PDF-TS-008 SLIDES-CPP-007 SLIDES-PY-006 TEX-PYTHON-005 WORDS-NET-007".split(),
    },
    "J": {
        "cards": "KD1-KD11, data cards KD6-KD9 by family",
        "ids": "3D-JAVA-002 3D-JAVA-003 3D-JAVA-004 3D-JAVA-006 3D-JAVA-007 3D-NET-003 3D-NET-004 3D-NET-006 3D-NET-008 3D-PYTHON-001 3D-PYTHON-002 3D-PYTHON-006 3D-PYTHON-008 3D-PYTHON-010 3D-TYPESCRIPT-008 BARCODE-PY-006 CELLS-CPP-004 CELLS-GO-001 CELLS-GO-011 CELLS-JAVA-001 CELLS-NET-002 CELLS-PY-003 CELLS-PY-004 CELLS-PY-005 CELLS-RUST-003 CELLS-RUST-006 CELLS-RUST-011 CELLS-TYPESCRIPT-001 CELLS-TYPESCRIPT-003 CELLS-TYPESCRIPT-004 CELLS-TYPESCRIPT-005 CELLS-TYPESCRIPT-006 CELLS-TYPESCRIPT-008 CELLS-TYPESCRIPT-009 CELLS-TYPESCRIPT-011 EMAIL-CPP-001 EMAIL-CPP-002 EMAIL-CPP-003 EMAIL-CPP-004 EMAIL-PY-006 EML-NET-003 IMAGING-NET-001 IMAGING-NET-002 IMAGING-NET-003 IMAGING-NET-004 IMAGING-NET-008 IMAGING-NET-012 NOTE-PY-002 NOTE-PY-008 PAGE-PYTHON-003 PAGE-PYTHON-005 PAGE-PYTHON-006 PDF-CPP-001 PDF-CPP-005 PDF-JAVA-001 PDF-JAVA-002 PDF-PYTHON-004 SLIDES-CPP-002 SLIDES-CPP-003 SLIDES-NET-006 SLIDES-NET-007 SLIDES-PY-001 SLIDES-PY-005 TEX-PYTHON-001 WORDS-PY-008 WORDS-PY-009".split(),
    },
    "K": {
        "cards": "RT3 (3D-NET-014, SLIDES-NET-005); CELLS-CPP-009 is informational: pinned as a contract test in F6",
        "ids": "3D-NET-014 CELLS-CPP-009 SLIDES-NET-005".split(),
    },
    "L": {
        "cards": "RT1, RT2, RT4 (fixed with data) or a measured disclosed limitation",
        "ids": "3D-NET-010 CELLS-NET-004 CELLS-PY-011 IMAGING-NET-009 PAGE-PYTHON-009 PDF-PYTHON-007 PDF-TS-010".split(),
    },
}

# LONGQ-001 is described separately in the Appendix A intro sentence, not in a per-workstream id list:
# "LONGQ-001 (the reported sentence) belongs to A and is closed by Q1, Q5-Q8 (+Q9 if needed), proof in F5/AU3."
LONGQ_001_WS = "A"
LONGQ_001_CARDS = "Q1, Q5-Q8 (+Q9 if needed); proof in F5/AU3"

_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _split_row(line: str) -> list[str]:
    """Split one markdown table row into cells, honouring ``\\|`` as a literal escaped pipe."""
    inner = line.strip()
    assert inner.startswith("|") and inner.endswith("|"), f"not a table row: {line!r}"
    inner = inner[1:-1]
    cells = [c.strip().replace("\\|", "|") for c in _CELL_SPLIT_RE.split(inner)]
    return cells


def _load_register_rows() -> list[dict[str, str]]:
    text = REVIEW_DOC.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        header_idx = next(i for i, ln in enumerate(lines) if ln.strip().startswith("| Id |"))
    except StopIteration:
        pytest.fail("docs/ROUND2_REVIEW.md: could not find the register table header row ('| Id | ...')")

    header_cells = _split_row(lines[header_idx])
    assert tuple(header_cells) == REGISTER_COLUMNS, (
        f"register table header mismatch: {header_cells!r} != {REGISTER_COLUMNS!r}"
    )

    # the line right after the header is the markdown separator ('|---|---|...'); real rows start after it
    rows = []
    for ln in lines[header_idx + 2 :]:
        stripped = ln.strip()
        if not stripped.startswith("|"):
            break
        cells = _split_row(ln)
        assert len(cells) == len(REGISTER_COLUMNS), (
            f"row has {len(cells)} cells, expected {len(REGISTER_COLUMNS)}: {ln!r}"
        )
        rows.append(dict(zip(REGISTER_COLUMNS, cells, strict=True)))
    return rows


def _load_issues() -> list[dict]:
    data = json.loads(ISSUES_JSON.read_text(encoding="utf-8"))
    assert isinstance(data, list), "docs/round2/issues.json must be a JSON array"
    return data


def _build_id_to_ws() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for ws, entry in APPENDIX_A.items():
        for issue_id in entry["ids"]:
            assert issue_id not in mapping, (
                f"Appendix A baked mapping assigns {issue_id} to both {mapping[issue_id]} and {ws}"
            )
            mapping[issue_id] = ws
    return mapping


# ---------------------------------------------------------------------------
# Fixtures-equivalent module-level loads (cheap, pure, offline)
# ---------------------------------------------------------------------------

REGISTER_ROWS = _load_register_rows()
ISSUES = _load_issues()
ID_TO_WS = _build_id_to_ws()


def test_appendix_a_mapping_is_total_and_disjoint_over_the_285_real_ids():
    """Self-check on the baked Appendix A copy: every real issue id in docs/round2/issues.json is assigned to
    exactly one workstream, and every id Appendix A names exists in docs/round2/issues.json."""
    assert len(ISSUES) == 285, f"expected exactly 285 issues in docs/round2/issues.json, found {len(ISSUES)}"
    real_ids = {issue["id"] for issue in ISSUES}
    assert len(real_ids) == 285, "docs/round2/issues.json has duplicate issue ids"

    appendix_ids = [iid for entry in APPENDIX_A.values() for iid in entry["ids"]]
    assert len(appendix_ids) == 285, f"Appendix A (baked) names {len(appendix_ids)} ids, expected 285"
    assert len(set(appendix_ids)) == 285, "Appendix A (baked) assigns some id to more than one workstream"

    missing_from_appendix = sorted(real_ids - set(appendix_ids))
    extra_in_appendix = sorted(set(appendix_ids) - real_ids)
    assert not missing_from_appendix, (
        f"ids in issues.json but not in the baked Appendix A: {missing_from_appendix}"
    )
    assert not extra_in_appendix, f"ids in the baked Appendix A but not in issues.json: {extra_in_appendix}"


def test_register_has_exactly_286_rows():
    assert len(REGISTER_ROWS) == 286, f"expected exactly 286 register rows, found {len(REGISTER_ROWS)}"


def test_register_ids_are_sequential_in_file_order():
    """The core structural guarantee: row i's Id must literally be f"RD2-{i+1:03d}", in the exact order the
    rows appear in the document. This is what the card's negative control targets - renaming row 0's id to
    'RD2-001-mutated' must fail here, proving this test reads the table rather than just counting rows."""
    for i, row in enumerate(REGISTER_ROWS, start=1):
        expected_id = f"RD2-{i:03d}"
        assert row["Id"] == expected_id, (
            f"register row {i} has Id {row['Id']!r}, expected {expected_id!r} "
            "(ids must be sequential, in file order, with no gaps or renames)"
        )


def test_first_285_rows_sev_cat_pilot_match_issues_json_in_file_order():
    assert len(REGISTER_ROWS) >= 285
    for i in range(285):
        row = REGISTER_ROWS[i]
        issue = ISSUES[i]
        assert row["Sev"] == issue["severity"], (
            f"{row['Id']} (position {i + 1}, real id {issue['id']}): Sev {row['Sev']!r} != "
            f"issues.json severity {issue['severity']!r}"
        )
        assert row["Cat"] == issue["category"], (
            f"{row['Id']} (position {i + 1}, real id {issue['id']}): Cat {row['Cat']!r} != "
            f"issues.json category {issue['category']!r}"
        )
        assert row["Pilot"] == issue["pilot"], (
            f"{row['Id']} (position {i + 1}, real id {issue['id']}): Pilot {row['Pilot']!r} != "
            f"issues.json pilot {issue['pilot']!r}"
        )


def test_first_285_rows_ws_and_cards_match_appendix_a():
    for i in range(285):
        row = REGISTER_ROWS[i]
        issue = ISSUES[i]
        real_id = issue["id"]
        expected_ws = ID_TO_WS[real_id]
        assert row["WS"] == expected_ws, (
            f"{row['Id']} (real id {real_id}): WS {row['WS']!r} != Appendix A workstream {expected_ws!r}"
        )
        expected_cards = APPENDIX_A[expected_ws]["cards"]
        assert row["Cards"] == expected_cards, (
            f"{row['Id']} (real id {real_id}, workstream {expected_ws}): Cards {row['Cards']!r} != "
            f"Appendix A closing-cards text {expected_cards!r}"
        )


def test_row_286_is_longq_001_with_fixed_fields():
    row = REGISTER_ROWS[285]
    assert row["Id"] == "RD2-286"
    assert row["Sev"] == "P1"
    assert row["Cat"] == "mcp-search"
    assert row["Pilot"] == "pdf/net"
    assert row["WS"] == LONGQ_001_WS
    assert row["Cards"] == LONGQ_001_CARDS
    assert row["Status"] == "OPEN"


def test_no_row_duplicates_a_real_issue_position():
    """Each of the first 285 register rows must correspond 1:1, by file-order position, to one issues.json
    entry - i.e. REGISTER_ROWS and ISSUES must be the same length for the real-issue portion."""
    assert len(REGISTER_ROWS) - 1 == len(ISSUES), (
        "register must carry exactly one row per issues.json entry plus the one LONGQ-001 row"
    )


VALID_STATUSES = frozenset(
    {
        "OPEN",
        "FIXED",
        "DISCLOSED",
        "NOT_REPRODUCED",
        "DEFINITIONAL",
        "ACCEPTED(P3)",
        "BLOCKED_UPSTREAM",
    }
)


def test_every_row_status_is_a_real_disposition_value():
    """G3/TC-777 (Q17): this card dispositions the 73 real Q-series rows away from `OPEN` to their
    individually live-verified current status, so the precondition snapshot this test replaces
    (`test_all_286_rows_start_open`, which asserted literally no row may be non-OPEN) no longer
    holds. What must hold instead, permanently, is that every row's `Status` cell is one of the
    real, defined disposition values a closure card is allowed to write - never a typo, a stray
    value, or free text. This is deliberately a total check over all 286 rows (not just the 73
    Q-series ones): any later closure card (`I14`, `O10`, `C11`, `XA19`, `KD11`, `RT5`, `R7`) that
    dispositions its own rows is held to the same real enum, not a weaker one scoped to this
    card's own cards."""
    bad = [(row["Id"], row["Status"]) for row in REGISTER_ROWS if row["Status"] not in VALID_STATUSES]
    assert not bad, f"register rows with a Status outside the real defined enum {sorted(VALID_STATUSES)}: {bad}"


def test_every_register_row_has_a_known_workstream_letter():
    valid = set(APPENDIX_A.keys())
    bad = [(row["Id"], row["WS"]) for row in REGISTER_ROWS if row["WS"] not in valid]
    assert not bad, f"register rows with a WS letter outside A-L: {bad}"


def test_findings_are_non_empty_and_reasonably_bounded():
    too_long = [(row["Id"], len(row["Finding"])) for row in REGISTER_ROWS if len(row["Finding"]) > 140]
    empty = [row["Id"] for row in REGISTER_ROWS if not row["Finding"]]
    assert not empty, f"rows with an empty Finding column: {empty}"
    assert not too_long, f"rows whose Finding column is far longer than the ~110 char target: {too_long}"
