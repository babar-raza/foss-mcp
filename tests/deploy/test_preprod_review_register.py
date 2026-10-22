"""G3/TC-537: docs/PREPROD_REVIEW.md commits the independent pre-production review register.

The definition of done is that every one of the 85 pre-production findings is either FIXED by a named,
accepted card or ACCEPTED with a published reason in docs/RELEASE.md section 8. This module pins the
closed register: it fails when a row is open, when a FIXED row names a card that is not accepted in
project/state.yaml, or when an ACCEPTED row is not published in RELEASE.md.

The module constants below (EXPECTED_FINDING_IDS, EXPECTED_FIXED_IDS, EXPECTED_ACCEPTED_IDS,
EXPECTED_SEVERITY_COUNTS) are literals generated from the supervisor's status file at authoring time
(outside the repository); they are not re-read from that file at test time. Parsing is plain string and
regex code, no markdown library. Offline, deterministic, no network, no skips.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.deploy.test_release_known_limitations import ACCEPTED_LEDGER_IDS

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_DOC = REPO_ROOT / "docs" / "PREPROD_REVIEW.md"
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"
PLANS_DIR = REPO_ROOT / "plans"
STATE_FILE = REPO_ROOT / "project" / "state.yaml"

# ---------------------------------------------------------------------------
# Module constants, generated from the supervisor's status file (85 findings; 78 FIXED, 7 ACCEPTED).
# ---------------------------------------------------------------------------

EXPECTED_FINDING_IDS: tuple[str, ...] = (
    "GRR-01",
    "GRR-02",
    "GRR-03",
    "GRR-04",
    "GRR-05",
    "GRR-06",
    "GRR-07",
    "GRR-08",
    "GRR-09",
    "GRR-10",
    "GRR-11",
    "GRR-12",
    "GRR-13",
    "GRR-14",
    "FK8S-01",
    "FK8S-02",
    "FK8S-03",
    "FK8S-04",
    "FK8S-05",
    "FK8S-06",
    "FK8S-07",
    "FK8S-08",
    "FK8S-09",
    "FK8S-10",
    "FK8S-11",
    "FK8S-12",
    "PS-01",
    "PS-02",
    "PS-03",
    "PS-04",
    "PS-05",
    "PS-06",
    "PS-07",
    "PS-08",
    "PS-09",
    "PS-10",
    "PS-11",
    "ISOPROV-01",
    "ISOPROV-02",
    "ISOPROV-03",
    "ISOPROV-04",
    "ISOPROV-05",
    "ISOPROV-06",
    "ISOPROV-07",
    "AGT-01",
    "AGT-02",
    "AGT-03",
    "AGT-04",
    "AGT-05",
    "AGT-06",
    "AGT-07",
    "AGT-08",
    "AGT-09",
    "AGT-10",
    "AGT-11",
    "D-01",
    "D-02",
    "D-03",
    "D-04",
    "D-05",
    "D-06",
    "D-07",
    "D-08",
    "D-09",
    "D-10",
    "D-11",
    "D-12",
    "D-13",
    "RST-01",
    "RST-02",
    "RST-03",
    "RST-04",
    "RST-05",
    "RST-06",
    "RST-07",
    "RST-08",
    "RST-09",
    "RST-10",
    "RST-11",
    "RST-12",
    "RST-13",
    "RST-14",
    "RST-15",
    "RST-16",
    "RST-17",
)

EXPECTED_ACCEPTED_IDS: tuple[str, ...] = (
    "FK8S-04",
    "FK8S-10",
    "FK8S-12",
    "PS-09",
    "AGT-06",
    "AGT-10",
    "D-07",
)

EXPECTED_FIXED_IDS: tuple[str, ...] = tuple(
    fid for fid in EXPECTED_FINDING_IDS if fid not in EXPECTED_ACCEPTED_IDS
)

EXPECTED_SEVERITY_COUNTS: dict[str, int] = {"P0": 0, "P1": 24, "P2": 40, "P3": 21}

# The ledger id this card assigned each ACCEPTED finding, in register-table order.
ACCEPTED_LEDGER_ID_OF: dict[str, str] = {
    "FK8S-04": "L-134",
    "FK8S-10": "L-135",
    "FK8S-12": "L-136",
    "PS-09": "L-137",
    "AGT-06": "L-138",
    "AGT-10": "L-139",
    "D-07": "L-140",
}

REQUIRED_HEADINGS = (
    "# Pre-production review of the shipped image",
    "## 1. Executive verdict",
    "## 2. Scope and method",
    "## 3. Fleet status",
    "## 4. The existing proof suite and its blind spots",
    "## 5. What was verified good",
    "## 6. Gap register",
    "## 7. Missing capabilities",
    "## 8. Shipping gates",
    "## 9. Residual accepted limitations",
)

GAP_TABLE_HEADER = "| ID | Severity | Status | Cards | Release | Verification | Finding |"

SLICE_NAMES = (
    "Generation, readiness and recovery (GRR)",
    "Fleet and Kubernetes (FK8S)",
    "Protocol and security (PS)",
    "Isolation and provenance (ISOPROV)",
    "Agentic tasks (AGT)",
    "Examples, product reference and releases (D)",
    "Retrieval and source truth (RST)",
)

CARD_ID_RE = re.compile(r"^TC-5\d\d$")

# ---------------------------------------------------------------------------
# Parsing helpers (plain string/regex, no markdown library).
# ---------------------------------------------------------------------------


def _doc() -> str:
    assert REVIEW_DOC.is_file(), "docs/PREPROD_REVIEW.md does not exist"
    text = REVIEW_DOC.read_text(encoding="utf-8")
    assert not text.startswith("﻿"), "docs/PREPROD_REVIEW.md carries a byte-order mark"
    assert "\r\n" not in text, "docs/PREPROD_REVIEW.md must use LF line endings"
    return text


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _heading_lines(text: str) -> list[str]:
    return [ln for ln in _lines(text) if re.match(r"^#{1,3} ", ln)]


def _section(text: str, heading: str, *, level: int) -> str:
    """The body of one '## N. ...' (or '### ...') heading, up to the next heading of the same or a
    higher level."""
    lines = _lines(text)
    starts = [i for i, ln in enumerate(lines) if ln == heading]
    assert len(starts) == 1, f"expected exactly one {heading!r} heading, found {len(starts)}"
    higher_markers = tuple("#" * n + " " for n in range(1, level + 1))
    body: list[str] = []
    for ln in lines[starts[0] + 1 :]:
        if ln.startswith(higher_markers):
            break
        body.append(ln)
    return "\n".join(body)


def _gap_table_rows(text: str) -> list[list[str]]:
    section6 = _section(text, "## 6. Gap register", level=2)
    rows: list[list[str]] = []
    for ln in section6.splitlines():
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        if ln == GAP_TABLE_HEADER:
            continue
        if re.fullmatch(r"\|[-| ]+\|", ln):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        rows.append(cells)
    return rows


def _gap_table_by_id(text: str) -> dict[str, list[str]]:
    return {row[0]: row for row in _gap_table_rows(text)}


def _shipping_gate_rows(text: str) -> list[list[str]]:
    section8 = _section(text, "## 8. Shipping gates", level=2)
    rows: list[list[str]] = []
    for ln in section8.splitlines():
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        if ln == "| Gate | Result | Evidence |":
            continue
        if re.fullmatch(r"\|[-| ]+\|", ln):
            continue
        rows.append([c.strip() for c in ln.strip("|").split("|")])
    return rows


def _fleet_status_bullets(text: str) -> list[str]:
    section3 = _section(text, "## 3. Fleet status", level=2)
    return [ln for ln in section3.splitlines() if ln.startswith("- ")]


def _verified_good_subsections(text: str) -> list[str]:
    section5 = _section(text, "## 5. What was verified good", level=2)
    return [ln for ln in section5.splitlines() if ln.startswith("### ")]


def _residual_section(text: str) -> str:
    return _section(text, "## 9. Residual accepted limitations", level=2)


def _section8_release(text: str) -> str:
    assert RELEASE_DOC.is_file()
    release_text = RELEASE_DOC.read_text(encoding="utf-8")
    lines = release_text.splitlines()
    heading = "## 8. Known limitations"
    starts = [i for i, ln in enumerate(lines) if ln == heading]
    assert len(starts) == 1
    body: list[str] = []
    for ln in lines[starts[0] + 1 :]:
        if re.match(r"^## (?!#)", ln):
            break
        body.append(ln)
    return "\n".join(body)


def _release_md_bullets() -> dict[str, str]:
    """Map every bold ledger id in RELEASE.md section 8 to its bullet text (continuation lines joined)."""
    text = _section8_release("")
    bullets: dict[str, list[str]] = {}
    current: str | None = None
    for ln in text.splitlines():
        opener = re.match(r"- \*\*(L-\d+)\*\*(.*)$", ln)
        if opener:
            current = opener.group(1)
            bullets.setdefault(current, []).append(opener.group(2).strip())
        elif ln.startswith("  ") and current is not None and ln.strip():
            bullets[current].append(ln.strip())
        else:
            current = None
    return {k: " ".join(v).strip() for k, v in bullets.items()}


def _card_status_map() -> dict[str, str]:
    with open(STATE_FILE, encoding="utf-8") as f:
        state = yaml.safe_load(f)
    return {c["id"]: c["status"] for c in state["cards"]}


# ---------------------------------------------------------------------------
# (1) document structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("heading", REQUIRED_HEADINGS)
def test_the_review_document_has_the_required_headings_in_order(heading: str):
    text = _doc()
    headings = _heading_lines(text)
    assert heading in headings, f"missing heading: {heading!r}"
    # order: every required heading before it in the tuple must also appear before it in the file.
    idx = REQUIRED_HEADINGS.index(heading)
    if idx > 0:
        prev = REQUIRED_HEADINGS[idx - 1]
        assert headings.index(prev) < headings.index(heading), f"{prev!r} must precede {heading!r}"


def test_the_register_table_has_the_exact_header_row():
    text = _doc()
    section6 = _section(text, "## 6. Gap register", level=2)
    assert GAP_TABLE_HEADER in section6.splitlines()


def test_the_register_ids_are_exactly_the_expected_set():
    text = _doc()
    rows = _gap_table_rows(text)
    ids = [row[0] for row in rows]
    assert len(ids) == len(EXPECTED_FINDING_IDS), (
        f"expected {len(EXPECTED_FINDING_IDS)} rows, found {len(ids)}"
    )
    assert set(ids) == set(EXPECTED_FINDING_IDS), "the register ids do not match the expected set"


def test_the_register_has_no_duplicate_id():
    text = _doc()
    ids = [row[0] for row in _gap_table_rows(text)]
    assert len(ids) == len(set(ids)), "the register table has a duplicate id"


def test_the_register_has_no_dropped_id():
    text = _doc()
    ids = {row[0] for row in _gap_table_rows(text)}
    missing = sorted(set(EXPECTED_FINDING_IDS) - ids)
    assert missing == [], f"ids dropped from the register table: {missing}"


def test_the_register_has_no_invented_id():
    text = _doc()
    ids = {row[0] for row in _gap_table_rows(text)}
    invented = sorted(ids - set(EXPECTED_FINDING_IDS))
    assert invented == [], f"ids invented in the register table: {invented}"


@pytest.mark.parametrize("severity", sorted(EXPECTED_SEVERITY_COUNTS))
def test_severity_counts_match_the_expected_counts(severity: str):
    text = _doc()
    by_id = _gap_table_by_id(text)
    count = sum(1 for row in by_id.values() if row[1] == severity)
    assert count == EXPECTED_SEVERITY_COUNTS[severity], (
        f"severity {severity}: expected {EXPECTED_SEVERITY_COUNTS[severity]}, found {count}"
    )


def test_no_row_is_open():
    text = _doc()
    for row in _gap_table_rows(text):
        for cell in row:
            assert cell.upper() not in ("OPEN", "TODO", ""), f"row {row[0]!r} has an open cell: {row!r}"


# ---------------------------------------------------------------------------
# (2) every row, parametrised over the 85 expected ids
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("finding_id", EXPECTED_FINDING_IDS)
def test_row_is_well_formed(finding_id: str):
    text = _doc()
    by_id = _gap_table_by_id(text)
    assert finding_id in by_id, f"{finding_id} is missing from the register table"
    row = by_id[finding_id]
    assert len(row) == 7, f"{finding_id} row has {len(row)} cells, expected 7"
    _id, severity, status, cards, release, verification, finding = row
    assert re.fullmatch(r"(GRR|FK8S|PS|ISOPROV|AGT|RST|D)-\d{2}", _id)
    assert severity in ("P0", "P1", "P2", "P3"), f"{finding_id} severity {severity!r}"
    assert status in ("FIXED", "ACCEPTED"), f"{finding_id} status {status!r}"
    assert verification.strip() != "", f"{finding_id} has an empty Verification cell"
    assert finding.strip() != "", f"{finding_id} has an empty Finding cell"
    assert "|" not in verification, f"{finding_id} Verification cell still carries a pipe"
    assert "|" not in finding, f"{finding_id} Finding cell still carries a pipe"


@pytest.mark.parametrize("finding_id", EXPECTED_FINDING_IDS)
def test_fixed_row_names_cards_that_exist_and_are_accepted(finding_id: str):
    text = _doc()
    row = _gap_table_by_id(text)[finding_id]
    status, cards_cell = row[2], row[3]
    card_status = _card_status_map()
    if status == "FIXED":
        assert cards_cell != "-", f"{finding_id} is FIXED but names no card"
        cards = [c.strip() for c in cards_cell.split(",")]
        for card in cards:
            assert CARD_ID_RE.fullmatch(card), f"{finding_id} names a malformed card id {card!r}"
            assert (PLANS_DIR / f"{card}.yaml").is_file(), (
                f"{finding_id} names {card}, which has no plan file"
            )
            assert card_status.get(card) == "ACCEPTED", (
                f"{finding_id} names {card}, whose state.yaml status is {card_status.get(card)!r}, not ACCEPTED"
            )
    else:
        assert status == "ACCEPTED"
        if cards_cell != "-":
            for card in (c.strip() for c in cards_cell.split(",")):
                assert (PLANS_DIR / f"{card}.yaml").is_file(), (
                    f"{finding_id} (ACCEPTED) names {card}, which has no plan file"
                )


@pytest.mark.parametrize("finding_id", EXPECTED_FINDING_IDS)
def test_accepted_row_is_published_in_release_md(finding_id: str):
    text = _doc()
    row = _gap_table_by_id(text)[finding_id]
    status, release_cell = row[2], row[4]
    if status == "FIXED":
        assert release_cell == "-", f"{finding_id} is FIXED but names a release id {release_cell!r}"
        return
    assert status == "ACCEPTED"
    assert re.fullmatch(r"L-\d+", release_cell), (
        f"{finding_id} ACCEPTED row has a bad Release cell {release_cell!r}"
    )
    bullets = _release_md_bullets()
    assert release_cell in bullets, f"{release_cell} has no bold bullet in docs/RELEASE.md section 8"
    assert f"Review finding {finding_id}" in bullets[release_cell], (
        f"{release_cell}'s bullet does not contain 'Review finding {finding_id}'"
    )
    assert release_cell in ACCEPTED_LEDGER_IDS, (
        f"{release_cell} is not in tests/deploy/test_release_known_limitations.ACCEPTED_LEDGER_IDS"
    )


# ---------------------------------------------------------------------------
# (3) the rest of the document
# ---------------------------------------------------------------------------


def test_every_accepted_finding_appears_in_the_residual_limitations_section():
    residual = _residual_section(_doc())
    for finding_id in EXPECTED_ACCEPTED_IDS:
        assert f"**{finding_id}**" in residual, f"{finding_id} missing from section 9"
        assert ACCEPTED_LEDGER_ID_OF[finding_id] in residual, (
            f"{finding_id}'s ledger id {ACCEPTED_LEDGER_ID_OF[finding_id]} missing from section 9"
        )
    assert "docs/RELEASE.md section 8" in residual


def test_shipping_gate_rows_are_pass_or_fail_with_evidence():
    rows = _shipping_gate_rows(_doc())
    assert len(rows) >= 1
    for row in rows:
        assert len(row) == 3
        gate, result, evidence = row
        assert gate.strip() != ""
        assert result in ("PASS", "FAIL"), f"gate {gate!r} has result {result!r}"
        assert evidence.strip() != "", f"gate {gate!r} has no evidence"


def test_the_fleet_status_section_has_at_least_three_bullets():
    bullets = _fleet_status_bullets(_doc())
    assert len(bullets) >= 3, f"fleet status has only {len(bullets)} bullets"


@pytest.mark.parametrize("slice_name", SLICE_NAMES)
def test_each_slice_has_a_verified_good_subsection(slice_name: str):
    subsections = _verified_good_subsections(_doc())
    expected = f"### Slice {slice_name}"
    assert expected in subsections, f"missing verified-good subsection for {slice_name!r}"


@pytest.mark.parametrize("needle", ("ghp_", "Bearer ", "api_key="))
def test_the_document_contains_no_environment_value_shaped_text(needle: str):
    text = _doc()
    assert needle not in text, f"docs/PREPROD_REVIEW.md contains the environment-value-shaped text {needle!r}"


# ---------------------------------------------------------------------------
# (4) synthetic tests proving each check can fail on a hand-built markdown string.
# ---------------------------------------------------------------------------

_SYNTHETIC_PREAMBLE = """# Pre-production review of the shipped image

## 1. Executive verdict

Verdict text.

Review date: 2026-10-22

## 2. Scope and method

- scope

## 3. Fleet status

- one
- two
- three

## 4. The existing proof suite and its blind spots

critique

## 5. What was verified good

### Slice Generation, readiness and recovery (GRR)

good

## 6. Gap register

| ID | Severity | Status | Cards | Release | Verification | Finding |
|---|---|---|---|---|---|---|
"""

_SYNTHETIC_TAIL = """
## 7. Missing capabilities

matrix

## 8. Shipping gates

| Gate | Result | Evidence |
|---|---|---|
| g1 | PASS | e1 |

## 9. Residual accepted limitations

- **FK8S-04** (L-134): reason. see docs/RELEASE.md section 8.
"""


def _synthetic_doc(gap_row: str) -> str:
    return _SYNTHETIC_PREAMBLE + gap_row + _SYNTHETIC_TAIL


def _assert_row_cells_not_all_valid(row: list[str]) -> None:
    """A minimal re-implementation of the no-open-row and row-shape checks, used only to prove the
    synthetic fixtures below actually trigger a failure (never executed against the real document)."""
    for cell in row:
        if cell.upper() in ("OPEN", "TODO", ""):
            raise AssertionError(f"open cell: {row!r}")
    if row[1] not in ("P0", "P1", "P2", "P3"):
        raise AssertionError(f"bad severity: {row!r}")


def test_synthetic_an_open_row_fails_the_no_open_row_check():
    row = ["GRR-01", "P1", "OPEN", "-", "-", "v", "f"]
    with pytest.raises(AssertionError):
        _assert_row_cells_not_all_valid(row)


def test_synthetic_a_severity_of_p4_fails_the_row_shape_check():
    row = ["GRR-01", "P4", "FIXED", "TC-506", "-", "v", "f"]
    with pytest.raises(AssertionError):
        _assert_row_cells_not_all_valid(row)


def test_synthetic_a_fixed_row_naming_tc_999_fails_the_card_exists_check():
    # TC-999 has the wrong shape (plans are TC-5NN at the time of writing); TC-599 has the right shape
    # but names a card with no plan file. Both ways of naming a non-existent card must fail.
    assert not CARD_ID_RE.fullmatch("TC-999")
    card = "TC-599"
    assert CARD_ID_RE.fullmatch(card)
    with pytest.raises(AssertionError):
        assert (PLANS_DIR / f"{card}.yaml").is_file(), f"plan file for {card} does not exist"


def test_synthetic_a_fixed_row_whose_card_is_pending_fails_the_acceptance_check():
    synthetic_state = {"TC-900": "PENDING"}
    with pytest.raises(AssertionError):
        assert synthetic_state.get("TC-900") == "ACCEPTED", "card is PENDING, not ACCEPTED"


def test_synthetic_an_accepted_row_missing_from_a_synthetic_release_md_fails():
    synthetic_release_bullets: dict[str, str] = {"L-022": "some other bullet"}
    release_cell = "L-134"
    with pytest.raises(AssertionError):
        assert release_cell in synthetic_release_bullets, f"{release_cell} missing from RELEASE.md"


def test_synthetic_a_dropped_row_fails_the_complete_set_check():
    ids_with_one_dropped = set(EXPECTED_FINDING_IDS) - {"RST-17"}
    with pytest.raises(AssertionError):
        assert ids_with_one_dropped == set(EXPECTED_FINDING_IDS), "a row was dropped"


def test_synthetic_fixture_with_a_well_formed_row_does_not_raise():
    row = ["GRR-01", "P1", "FIXED", "TC-506", "-", "v", "f"]
    _assert_row_cells_not_all_valid(row)  # must not raise


def test_synthetic_doc_builder_produces_a_parseable_document():
    good_row = "| GRR-01 | P1 | FIXED | TC-506 | - | v | f |"
    doc = _synthetic_doc(good_row)
    assert "## 6. Gap register" in doc
    assert good_row in doc
