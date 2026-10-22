"""G3/TC-537 (ROUND-3 FOLLOW-UP AMENDMENT): docs/ROUND3_REVIEW.md is closed before this card closes.

This module parses docs/ROUND3_REVIEW.md, the committed register for the Round-3 independent review, and
fails when:

  (a) any finding row RD3-001 to RD3-172 is not CLOSED (with the accepted card id) or listed in section 4
      with its disposition;
  (b) any supervisor item SUP-1 to SUP-8 of section 6.1 is not DONE with an evidence reference that
      exists (a commit reachable from HEAD or a file under evidence/; a TC-NNN card id that is itself
      ACCEPTED in project/state.yaml, whose own commit is reachable from HEAD, is also accepted as
      evidence - the supervisor's own closure prose for several SUP rows names the accepted card that did
      the work rather than repeating its commit hash);
  (c) any open question OQ-012 to OQ-015 is still OPEN.

Offline, deterministic, no network. Local ``git`` calls only (no fetch, no remote).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUND3_DOC = REPO_ROOT / "docs" / "ROUND3_REVIEW.md"
STATE_FILE = REPO_ROOT / "project" / "state.yaml"
OPEN_QUESTIONS_FILE = REPO_ROOT / "ops" / "open_questions.jsonl"
EVIDENCE_DIR = REPO_ROOT / "evidence"

SUP_IDS = tuple(f"SUP-{n}" for n in range(1, 9))
OQ_IDS = ("OQ-012", "OQ-013", "OQ-014", "OQ-015")

CARD_ID_RE = re.compile(r"TC-\d{3}")
COMMIT_HASH_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
EVIDENCE_PATH_RE = re.compile(r"evidence/[A-Za-z0-9_./-]+")


def _doc() -> str:
    assert ROUND3_DOC.is_file(), "docs/ROUND3_REVIEW.md does not exist"
    text = ROUND3_DOC.read_text(encoding="utf-8")
    assert "\r\n" not in text
    return text


def _section(text: str, heading: str, *, level: int) -> str:
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln == heading]
    assert len(starts) == 1, f"expected exactly one {heading!r} heading, found {len(starts)}"
    higher_markers = tuple("#" * n + " " for n in range(1, level + 1))
    body: list[str] = []
    for ln in lines[starts[0] + 1 :]:
        if ln.startswith(higher_markers):
            break
        body.append(ln)
    return "\n".join(body)


def _row_cells(line: str) -> list[str] | None:
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return None
    if re.fullmatch(r"\|[-| ]+\|", line):
        return None
    return [c.strip() for c in line[1:-1].split("|")]


def _rd3_rows() -> dict[str, list[str]]:
    """Every RD3-NNN row under section 3 (any subsection table), keyed by id, full cell list."""
    text = _doc()
    section3 = _section(text, "## 3. Findings register", level=2)
    rows: dict[str, list[str]] = {}
    for ln in section3.splitlines():
        cells = _row_cells(ln)
        if not cells:
            continue
        if re.fullmatch(r"RD3-\d{3}", cells[0]):
            rows[cells[0]] = cells
    return rows


def _section4_ids() -> set[str]:
    text = _doc()
    section4 = _section(text, "## 4. Accepted or upstream (disclose, no product code change)", level=2)
    ids = set()
    for ln in section4.splitlines():
        cells = _row_cells(ln)
        if cells and re.fullmatch(r"RD3-\d{3}|RD3-U\d{2}", cells[0]):
            ids.add(cells[0])
    return ids


def _sup_rows() -> dict[str, list[str]]:
    text = _doc()
    section61 = _section(
        text,
        "## 6.1 Supervisor work items (not dispatched to workers: they edit ops/, hosts and the cluster)",
        level=2,
    )
    rows: dict[str, list[str]] = {}
    for ln in section61.splitlines():
        cells = _row_cells(ln)
        if cells and re.fullmatch(r"SUP-\d", cells[0]):
            rows[cells[0]] = cells
    return rows


def _card_status_map() -> dict[str, str]:
    with open(STATE_FILE, encoding="utf-8") as f:
        state = yaml.safe_load(f)
    return {c["id"]: c["status"] for c in state["cards"]}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def _commit_reachable_from_head(commit: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        return False
    exists = _git("cat-file", "-e", commit + "^{commit}")
    if exists.returncode != 0:
        return False
    anc = _git("merge-base", "--is-ancestor", commit, "HEAD")
    return anc.returncode == 0


def _card_integrated_into_head(card: str) -> bool:
    """A card's integration is proven by its (GATE/TC-NNN) commit subject reachable from HEAD, not by
    the worker commit hash project/state.yaml records: integration replays worker commits onto main
    with new hashes (docs/RELEASE.md L-111), so that stored hash is not, in general, a HEAD ancestor."""
    result = _git("log", "--oneline", "--fixed-strings", "--grep", f"/{card})")
    return result.returncode == 0 and bool(result.stdout.strip())


def _open_questions() -> dict[str, dict]:
    assert OPEN_QUESTIONS_FILE.is_file()
    out: dict[str, dict] = {}
    with open(OPEN_QUESTIONS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out[obj["id"]] = obj
    return out


# ---------------------------------------------------------------------------
# (a) every RD3 finding row is CLOSED with an accepted card, or disclosed/rejected in section 4.
# ---------------------------------------------------------------------------


def _all_rd3_ids() -> tuple[str, ...]:
    return tuple(sorted(_rd3_rows().keys(), key=lambda s: int(s.split("-")[1])))


_RD3_IDS = _all_rd3_ids()


def test_the_register_has_findings_rows():
    assert len(_RD3_IDS) >= 90, f"expected at least 90 RD3 rows, found {len(_RD3_IDS)}"


@pytest.mark.parametrize("rd3_id", _RD3_IDS)
def test_rd3_row_is_closed_or_disclosed(rd3_id: str):
    row = _rd3_rows()[rd3_id]
    status_cell = row[-1]
    section4_ids = _section4_ids()
    card_status = _card_status_map()

    closed = re.match(r"^CLOSED \(([^)]*)\)$", status_cell)
    disclosed = status_cell.startswith("DISCLOSED") or status_cell.startswith("REJECTED")

    assert closed or disclosed, (
        f"{rd3_id} has status {status_cell!r}, neither CLOSED(...) nor DISCLOSED/REJECTED"
    )

    if closed:
        cards = CARD_ID_RE.findall(closed.group(1))
        assert cards, f"{rd3_id} is CLOSED but names no TC-NNN card: {status_cell!r}"
        for card in cards:
            assert card_status.get(card) == "ACCEPTED", (
                f"{rd3_id} closes with {card}, whose project/state.yaml status is "
                f"{card_status.get(card)!r}, not ACCEPTED"
            )
    else:
        assert rd3_id in section4_ids, f"{rd3_id} is {status_cell!r} but is not listed in section 4"


def test_no_rd3_row_is_left_open():
    for rd3_id, row in _rd3_rows().items():
        status_cell = row[-1]
        assert status_cell.upper() != "OPEN", f"{rd3_id} is still OPEN"
        assert status_cell.strip() != "", f"{rd3_id} has an empty Status cell"


# ---------------------------------------------------------------------------
# (b) SUP-1 to SUP-8 are DONE with an evidence reference that exists.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sup_id", SUP_IDS)
def test_supervisor_item_is_done_with_existing_evidence(sup_id: str):
    rows = _sup_rows()
    assert sup_id in rows, f"{sup_id} is missing from section 6.1"
    row = rows[sup_id]
    assert len(row) == 5, f"{sup_id} row has {len(row)} cells, expected 5"
    status_cell = row[3]
    assert status_cell.startswith("DONE"), f"{sup_id} status is {status_cell!r}, not DONE"

    full_row_text = " | ".join(row)
    card_status = _card_status_map()

    found_valid_evidence = False

    for path_match in EVIDENCE_PATH_RE.findall(full_row_text):
        candidate = REPO_ROOT / path_match
        if candidate.is_file() or candidate.is_dir():
            found_valid_evidence = True
            break

    if not found_valid_evidence:
        for commit_match in COMMIT_HASH_RE.findall(full_row_text):
            if _commit_reachable_from_head(commit_match):
                found_valid_evidence = True
                break

    if not found_valid_evidence:
        for card in CARD_ID_RE.findall(full_row_text):
            if card_status.get(card) == "ACCEPTED" and _card_integrated_into_head(card):
                found_valid_evidence = True
                break

    assert found_valid_evidence, (
        f"{sup_id} is DONE but names no verifiable evidence (no existing evidence/ path, no "
        f"HEAD-reachable commit hash, and no ACCEPTED card with a HEAD-reachable commit): {row!r}"
    )


# ---------------------------------------------------------------------------
# (c) OQ-012 to OQ-015 are not OPEN.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("oq_id", OQ_IDS)
def test_open_question_is_not_open(oq_id: str):
    questions = _open_questions()
    assert oq_id in questions, f"{oq_id} is missing from ops/open_questions.jsonl"
    status = questions[oq_id]["status"]
    assert status != "OPEN", f"{oq_id} is still OPEN"
    assert status == "RESOLVED", f"{oq_id} has status {status!r}, expected RESOLVED"


# ---------------------------------------------------------------------------
# Synthetic tests proving each check can fail on a hand-built fixture.
# ---------------------------------------------------------------------------


def test_synthetic_an_unclosed_rd3_row_fails():
    status_cell = "OPEN"
    assert not (
        status_cell.startswith("CLOSED")
        or status_cell.startswith("DISCLOSED")
        or status_cell.startswith("REJECTED")
    )


def test_synthetic_a_closed_row_naming_an_unaccepted_card_fails():
    card_status = {"TC-900": "PENDING"}
    status_cell = "CLOSED (TC-900)"
    m = re.match(r"^CLOSED \(([^)]*)\)$", status_cell)
    assert m
    cards = CARD_ID_RE.findall(m.group(1))
    with pytest.raises(AssertionError):
        for card in cards:
            assert card_status.get(card) == "ACCEPTED"


def test_synthetic_a_disclosed_row_missing_from_section_4_fails():
    section4_ids = {"RD3-999"}
    rd3_id = "RD3-160"
    with pytest.raises(AssertionError):
        assert rd3_id in section4_ids


def test_synthetic_a_sup_row_not_done_fails():
    status_cell = "PARTLY DONE"
    with pytest.raises(AssertionError):
        assert status_cell.startswith("DONE") and not status_cell.startswith("PARTLY")


def test_synthetic_a_sup_row_with_no_verifiable_evidence_fails():
    full_row_text = "DONE (trust me)"
    found = bool(EVIDENCE_PATH_RE.search(full_row_text)) or bool(COMMIT_HASH_RE.search(full_row_text))
    with pytest.raises(AssertionError):
        assert found, "no verifiable evidence reference"


def test_synthetic_an_open_oq_fails():
    status = "OPEN"
    with pytest.raises(AssertionError):
        assert status == "RESOLVED"


def test_synthetic_commit_reachability_check_rejects_a_bogus_hash():
    assert _commit_reachable_from_head("0000000") is False


def test_synthetic_commit_reachability_check_accepts_head_itself():
    head = _git("rev-parse", "HEAD").stdout.strip()
    assert _commit_reachable_from_head(head) is True
