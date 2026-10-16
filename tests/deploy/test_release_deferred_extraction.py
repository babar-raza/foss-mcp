"""G3/TC-490: docs/RELEASE.md publishes the deferred extractor improvements (REQ-G3-007).

Six extractor cards were cut from the Monday release, and TC-546 records four more as accepted limitations. A peer reads docs/RELEASE.md, so section 9 names each
one and what the peer will notice. This module pins that list so the deferral stays a recorded, visible
decision. It reads only committed files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"
PLANS_DIR = REPO_ROOT / "plans"

SECTION_8_PREFIX = "## 8. "
SECTION_9_HEADING = "## 9. Deferred extraction improvements"

DEFERRED_CARD_IDS = (
    "TC-397",
    "TC-398",
    "TC-402",
    "TC-403",
    "TC-428",
    "TC-475",
    # Cut from the release and recorded as accepted limitations by TC-546.
    "TC-526",
    "TC-527",
    "TC-528",
    "TC-531",
)

SECTION_HEADINGS_1_TO_8 = (
    "# Releasing the standalone demo image",
    "## 1. What the standalone demo image is",
    "## 2. How it is built",
    "## 3. The pre-deployment gate",
    "## 4. Hosting",
    "## 5. Recording",
    "## 6. What the checks say about it",
    "## 7. How a peer pulls and runs it",
    "## 8. Known limitations",
)

_BOLD_CARD_RE = re.compile(r"\*\*(TC-\d+)\*\*")
_MIN_TEXT_AFTER_ID = 80


def _text() -> str:
    return RELEASE_DOC.read_text(encoding="utf-8")


def _lines() -> list[str]:
    return _text().splitlines()


def _section_9() -> str:
    text = _text()
    start = text.index(SECTION_9_HEADING)
    rest = text[start + len(SECTION_9_HEADING) :]
    match = re.search(r"^## ", rest, re.MULTILINE)
    return rest if match is None else rest[: match.start()]


def test_deferred_card_ids_are_exactly_the_ten() -> None:
    assert len(DEFERRED_CARD_IDS) == 10
    assert len(set(DEFERRED_CARD_IDS)) == 10
    assert set(DEFERRED_CARD_IDS) == {
        "TC-397",
        "TC-398",
        "TC-402",
        "TC-403",
        "TC-428",
        "TC-475",
        "TC-526",
        "TC-527",
        "TC-528",
        "TC-531",
    }


def test_section_9_heading_appears_exactly_once() -> None:
    headings = [line for line in _lines() if line.strip() == SECTION_9_HEADING]
    assert len(headings) == 1, f"expected one {SECTION_9_HEADING!r}, found {len(headings)}"


def test_section_9_is_placed_after_section_8() -> None:
    lines = _lines()
    s8 = [i for i, line in enumerate(lines) if line.startswith(SECTION_8_PREFIX)]
    s9 = [i for i, line in enumerate(lines) if line.strip() == SECTION_9_HEADING]
    assert len(s8) == 1, "section 8 heading must appear exactly once"
    assert len(s9) == 1
    assert s8[0] < s9[0]


def test_sections_1_to_8_headings_are_unchanged() -> None:
    headings = [line.rstrip() for line in _lines() if re.match(r"#{1,2} ", line)]
    assert tuple(headings[: len(SECTION_HEADINGS_1_TO_8)]) == SECTION_HEADINGS_1_TO_8
    assert headings[len(SECTION_HEADINGS_1_TO_8) :] == [SECTION_9_HEADING]


def test_section_9_opens_with_two_plain_sentences_before_the_bullets() -> None:
    body = _section_9().strip()
    intro = body.split("\n- ", 1)[0].strip()
    assert "not in this release" in intro
    assert "docs/REVIEW_LEDGER.md" in intro
    assert "**" not in intro


def test_section_9_has_exactly_ten_bullets() -> None:
    bullets = [line for line in _section_9().splitlines() if line.startswith("- ")]
    assert len(bullets) == len(DEFERRED_CARD_IDS)


def test_section_9_contains_no_other_bold_card_id() -> None:
    found = _BOLD_CARD_RE.findall(_section_9())
    assert sorted(found) == sorted(DEFERRED_CARD_IDS)
    assert set(found) - set(DEFERRED_CARD_IDS) == set()


@pytest.mark.parametrize("card_id", DEFERRED_CARD_IDS)
def test_card_id_appears_in_bold_exactly_once_in_section_9(card_id: str) -> None:
    assert _section_9().count(f"**{card_id}**") == 1, f"{card_id} must appear in bold exactly once"


@pytest.mark.parametrize("card_id", DEFERRED_CARD_IDS)
def test_card_id_has_enough_text_after_it(card_id: str) -> None:
    bullets = [line for line in _section_9().splitlines() if line.startswith(f"- **{card_id}**")]
    assert len(bullets) == 1, f"{card_id} must start exactly one bullet"
    after = bullets[0].split(f"**{card_id}**", 1)[1].strip()
    assert len(after) >= _MIN_TEXT_AFTER_ID, f"{card_id} has only {len(after)} characters of text"


@pytest.mark.parametrize("card_id", DEFERRED_CARD_IDS)
def test_card_id_is_not_bold_outside_section_9(card_id: str) -> None:
    text = _text()
    start = text.index(SECTION_9_HEADING)
    assert f"**{card_id}**" not in text[:start]


@pytest.mark.parametrize("card_id", DEFERRED_CARD_IDS)
def test_deferred_plan_file_exists(card_id: str) -> None:
    assert (PLANS_DIR / f"{card_id}.yaml").is_file(), f"plans/{card_id}.yaml does not exist"
