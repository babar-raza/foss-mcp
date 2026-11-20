"""G3/TC-855: the remaining-work programme's decisions are on record and REQ-G3-018 has a card (REQ-G3-018).

Offline; reads only committed files. The DECISION_LOG section is located by its exact heading, the retired cards'
plan files must be gone, docs/RELEASE.md must carry the cluster-free regeneration how-to, and the requirement
ledger / plans are parsed as YAML (never grepped as prose) to prove REQ-G3-018 exists and has a satisfying card.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION_LOG = REPO_ROOT / "docs" / "DECISION_LOG.md"
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"
REQUIREMENTS = REPO_ROOT / "ops" / "requirements.yaml"
PLANS_DIR = REPO_ROOT / "plans"

SECTION_HEADING = (
    "## 2026-11-19 - Remaining-work programme record "
    "(retired cards, regeneration route, size ceiling, baseline re-record)"
)
REGENERATION_HEADING = "### Regenerating the demo data without a cluster"
RETIRED_CARDS = ("TC-834", "TC-843", "TC-845")
REQUIRED_NAMES = ("TC-834", "TC-843", "TC-845", "OQ-029", "DG-1", "regenerate_local.py")


def _decision_section() -> str:
    """The text of the TC-855 section: from its heading up to the next '## ' heading or the end of the file."""
    text = DECISION_LOG.read_text(encoding="utf-8")
    assert text.count(SECTION_HEADING) == 1, f"expected exactly one {SECTION_HEADING!r}"
    body = text.split(SECTION_HEADING, 1)[1]
    next_heading = body.find("\n## ")
    return body if next_heading == -1 else body[:next_heading]


def test_decision_section_exists_once() -> None:
    assert SECTION_HEADING in DECISION_LOG.read_text(encoding="utf-8")
    assert len(_decision_section().strip()) > 500


@pytest.mark.parametrize("name", REQUIRED_NAMES)
def test_decision_section_names(name: str) -> None:
    assert name in _decision_section(), f"{name} is not named in the 2026-11-19 DECISION_LOG section"


@pytest.mark.parametrize("card", RETIRED_CARDS)
def test_retired_card_plan_file_is_absent(card: str) -> None:
    assert not (PLANS_DIR / f"{card}.yaml").exists(), f"plans/{card}.yaml was retired and must not exist"


@pytest.mark.parametrize("name", ("regenerate_local.py", "answer_diff.py"))
def test_release_doc_names_tool(name: str) -> None:
    text = RELEASE_DOC.read_text(encoding="utf-8")
    assert REGENERATION_HEADING in text
    section = text.split(REGENERATION_HEADING, 1)[1].split("\n## ", 1)[0]
    assert name in section, f"{name} is not named in the cluster-free regeneration section of docs/RELEASE.md"
    assert (REPO_ROOT / "scripts" / "release" / name).is_file()


def test_requirement_g3_018_exists() -> None:
    data = yaml.safe_load(REQUIREMENTS.read_text(encoding="utf-8"))
    entries = data["requirements"] if isinstance(data, dict) and "requirements" in data else data
    ids = {entry["id"] for entry in entries if isinstance(entry, dict) and "id" in entry}
    assert "REQ-G3-018" in ids


def test_requirement_g3_018_has_a_satisfying_plan() -> None:
    satisfying = []
    for plan_path in sorted(PLANS_DIR.glob("TC-*.yaml")):
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        if isinstance(plan, dict) and "REQ-G3-018" in (plan.get("satisfies") or []):
            satisfying.append(plan_path.name)
    assert satisfying, "no plan lists REQ-G3-018 in satisfies"
