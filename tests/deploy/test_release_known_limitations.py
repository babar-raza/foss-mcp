"""G3/TC-489: docs/RELEASE.md publishes every accepted review finding (REQ-G3-007).

The definition of done is that every finding of the independent reviews is FIXED or ACCEPTED with a recorded
reason. A peer who pulls the image reads docs/RELEASE.md, so section 8 repeats each accepted finding there. This
module pins that list: the ids in ACCEPTED_LEDGER_IDS must each appear exactly once, in bold, with a real
sentence of explanation, and section 8 must name no other ledger id. It reads only committed files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"

SECTION_8_HEADING = "## 8. Known limitations"
STANDALONE_HEADING = "### Standalone image"
KUBERNETES_HEADING = "### Kubernetes chart (not part of the standalone release)"
NOT_DEFECT_HEADING = "### Reported, not defects"

# Standalone image.
STANDALONE_IDS = (
    "L-022",
    "L-054",
    "L-071",
    "L-072",
    "L-080",
    "L-089",
    "L-090",
    "L-091",
    "L-095",
    "L-097",
    "L-098",
    "L-099",
    "L-100",
    "L-102",
    "L-105",
    "L-106",
    "L-107",
    "L-108",
    "L-109",
    "L-110",
    "L-111",
    "L-119",
    "L-120",
    "L-121",
    "L-122",
    "L-123",
    "L-124",
    "L-125",
    "L-126",
    "L-127",
    "L-128",
    "L-129",
    "L-133",
)
# Kubernetes chart only.
KUBERNETES_IDS = ("L-115", "L-116", "L-117", "L-118")
# Reported and checked, not a defect.
NOT_DEFECT_IDS = ("L-104", "L-130", "L-131", "L-132")

ACCEPTED_LEDGER_IDS = STANDALONE_IDS + KUBERNETES_IDS + NOT_DEFECT_IDS

# One phrase each of the ten ids this card adds, that the id's bullet text must contain.
KEY_PHRASES = {
    "L-022": "2026-07-28",
    "L-054": "find_examples",
    "L-071": "limitations",
    "L-072": "C++",
    "L-080": "/readyz",
    "L-104": "search_symbols",
    "L-130": "imaging/net",
    "L-131": "MIT",
    "L-132": "gatectl validate",
}

SECTION_HEADINGS = (
    "# Releasing the standalone demo image",
    "## 1. What the standalone demo image is",
    "## 2. How it is built",
    "## 3. The pre-deployment gate",
    "## 4. Hosting",
    "## 5. Recording",
    "## 6. What the checks say about it",
    "## 7. How a peer pulls and runs it",
)

MIN_BULLET_TEXT = 80

_LEDGER_ID = re.compile(r"\bL-\d+\b")
_BOLD_ID = re.compile(r"\*\*(L-\d+)\*\*")


def _doc() -> str:
    assert RELEASE_DOC.is_file(), "docs/RELEASE.md does not exist"
    return RELEASE_DOC.read_text(encoding="utf-8")


def _heading_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines()]


def _section_8() -> str:
    lines = _heading_lines(_doc())
    start = [i for i, ln in enumerate(lines) if ln == SECTION_8_HEADING]
    assert len(start) == 1, f"expected exactly one {SECTION_8_HEADING!r} heading, found {len(start)}"
    body: list[str] = []
    for ln in lines[start[0] + 1 :]:
        if re.match(r"## (?!#)", ln):
            break
        body.append(ln)
    return "\n".join(body)


def _subsection(heading: str) -> str:
    lines = _section_8().splitlines()
    start = [i for i, ln in enumerate(lines) if ln.rstrip() == heading]
    assert len(start) == 1, f"expected exactly one {heading!r} heading in section 8, found {len(start)}"
    body: list[str] = []
    for ln in lines[start[0] + 1 :]:
        if ln.startswith("### "):
            break
        body.append(ln)
    return "\n".join(body)


def _bullets(text: str) -> dict[str, str]:
    """Map each bold ledger id that opens a bullet to the bullet's text after the id, continuation lines joined."""
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


# (1) the list itself ---------------------------------------------------------------------------------------------


def test_the_accepted_id_tuple_has_no_duplicates():
    assert len(set(ACCEPTED_LEDGER_IDS)) == len(ACCEPTED_LEDGER_IDS)


def test_the_accepted_id_tuple_is_well_formed():
    for ledger_id in ACCEPTED_LEDGER_IDS:
        assert re.fullmatch(r"L-\d{3}", ledger_id), ledger_id


# (2) the structure of section 8 -----------------------------------------------------------------------------------


def test_release_md_has_exactly_one_known_limitations_heading():
    lines = _heading_lines(_doc())
    assert lines.count(SECTION_8_HEADING) == 1


def test_section_8_follows_section_7_and_section_9_is_the_last_section():
    headings = [ln for ln in _heading_lines(_doc()) if re.match(r"## (?!#)", ln)]
    assert headings[-1] == "## 9. Deferred extraction improvements"
    assert headings[-2] == SECTION_8_HEADING
    assert headings[-3] == "## 7. How a peer pulls and runs it"


def test_section_8_has_the_standalone_subsection_once():
    assert _heading_lines(_section_8()).count(STANDALONE_HEADING) == 1


def test_section_8_has_the_kubernetes_subsection_once():
    assert _heading_lines(_section_8()).count(KUBERNETES_HEADING) == 1


def test_section_8_has_the_not_defect_subsection_once():
    assert _heading_lines(_section_8()).count(NOT_DEFECT_HEADING) == 1


def test_the_not_defect_subsection_comes_after_the_kubernetes_one():
    body = _section_8()
    assert body.index(KUBERNETES_HEADING) < body.index(NOT_DEFECT_HEADING)


def test_the_not_defect_subsection_opens_by_saying_not_defects():
    lines = _section_8().splitlines()
    start = [i for i, ln in enumerate(lines) if ln.rstrip() == NOT_DEFECT_HEADING]
    assert len(start) == 1
    intro: list[str] = []
    for ln in lines[start[0] + 1 :]:
        if ln.lstrip().startswith("- **L-"):
            break
        intro.append(ln)
    flat = " ".join(" ".join(intro).split())
    assert "not defects" in flat


def test_section_8_opens_by_pointing_at_the_full_ledger():
    intro = _section_8().split("### ", 1)[0]
    flat = " ".join(intro.split())
    assert "docs/REVIEW_LEDGER.md" in flat
    assert "accepts rather than fixes" in flat


def test_the_standalone_subsection_comes_before_the_kubernetes_one():
    body = _section_8()
    assert body.index(STANDALONE_HEADING) < body.index(KUBERNETES_HEADING)


@pytest.mark.parametrize("heading", SECTION_HEADINGS)
def test_sections_1_to_7_keep_their_headings(heading: str):
    assert _heading_lines(_doc()).count(heading) == 1, f"missing or duplicated heading: {heading}"


# (3) every accepted id, once, with a reason ------------------------------------------------------------------------


@pytest.mark.parametrize("ledger_id", ACCEPTED_LEDGER_IDS)
def test_accepted_id_appears_in_bold_exactly_once_in_section_8(ledger_id: str):
    found = _BOLD_ID.findall(_section_8())
    assert found.count(ledger_id) == 1, (
        f"{ledger_id} appears in bold {found.count(ledger_id)} times in section 8"
    )


@pytest.mark.parametrize("ledger_id", ACCEPTED_LEDGER_IDS)
def test_accepted_id_has_a_bullet_with_a_reason(ledger_id: str):
    bullets = _bullets(_section_8())
    assert ledger_id in bullets, f"{ledger_id} has no bullet that opens with its bold id"
    text = bullets[ledger_id]
    assert len(text) >= MIN_BULLET_TEXT, f"{ledger_id} bullet has only {len(text)} characters of text"


@pytest.mark.parametrize("ledger_id", STANDALONE_IDS)
def test_standalone_id_sits_under_the_standalone_subsection(ledger_id: str):
    assert ledger_id in _bullets(_subsection(STANDALONE_HEADING))


@pytest.mark.parametrize("ledger_id", KUBERNETES_IDS)
def test_kubernetes_id_sits_under_the_kubernetes_subsection(ledger_id: str):
    assert ledger_id in _bullets(_subsection(KUBERNETES_HEADING))


@pytest.mark.parametrize("ledger_id", NOT_DEFECT_IDS)
def test_not_defect_id_sits_under_the_not_defect_subsection(ledger_id: str):
    assert ledger_id in _bullets(_subsection(NOT_DEFECT_HEADING))


@pytest.mark.parametrize("ledger_id, phrase", sorted(KEY_PHRASES.items()))
def test_bullet_contains_its_key_phrase(ledger_id: str, phrase: str):
    bullets = _bullets(_section_8())
    assert ledger_id in bullets, f"{ledger_id} has no bullet that opens with its bold id"
    assert phrase in bullets[ledger_id], f"{ledger_id} bullet is missing the phrase {phrase!r}"


# (4) nothing else -------------------------------------------------------------------------------------------------


def test_section_8_names_no_ledger_id_outside_the_tuple():
    unknown = sorted(set(_LEDGER_ID.findall(_section_8())) - set(ACCEPTED_LEDGER_IDS))
    assert unknown == [], f"section 8 names ledger ids that are not in ACCEPTED_LEDGER_IDS: {unknown}"


def test_every_section_8_bullet_belongs_to_the_tuple():
    bullets = _bullets(_section_8())
    assert sorted(set(bullets) - set(ACCEPTED_LEDGER_IDS)) == []


def test_section_8_has_exactly_one_bullet_per_accepted_id():
    openers = re.findall(r"^- \*\*(L-\d+)\*\*", _section_8(), re.M)
    assert sorted(openers) == sorted(ACCEPTED_LEDGER_IDS)
