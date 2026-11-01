"""G3/TC-640 (REQ-G3-013, round-2 audit workstream G, C2): a title merely CONTAINING the substring
"setup" is no longer misfiled as getting_started.

``_GETTING_STARTED_TITLE`` used to carry a bare ``setup`` alternative that matched that substring
ANYWHERE in a title. The real baked title "Page Setup and Print Configuration" (a reference title
about page layout, not onboarding) tripped it purely because it contains the word "setup", and
misclassified as getting_started.

Measured directly against every real distinct ``FQN: Doc: <title>`` value in
``infra/demo-manifests/*/generation__*.json`` (59 distinct titles as of this card): outright
removing the bare ``setup`` alternative would have zero regression there, but the existing
synthetic title "Environment Setup" in ``test_doc_classification_by_title.py`` (not this card's to
edit) already depends on it classifying as getting_started. The fix instead narrows the
alternative: ``setup`` only counts as onboarding when it is the title's own LAST word
("Environment Setup", "Quick Setup"), never when more title text follows it ("Page Setup and
Print Configuration").

Offline and deterministic: no network, no docker, no clock. The real-corpus test below reads the
baked demo manifests directly, read-only - the same files ``infra/demo-manifests`` always ships
with the repository.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from foss_mcp.mcp.tools.search_docs import _DOC_TITLE_PREFIX, classify_content_type

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

_TITLE_FIRST_LINE = re.compile(r"^" + re.escape(_DOC_TITLE_PREFIX) + r"(.+)$")


def _text(title: str, first_body_line: str = "Plain prose") -> str:
    return f"{_DOC_TITLE_PREFIX}{title}\n{first_body_line}\nmore body text\nSource-Commit: {'0' * 40}"


def _real_corpus_titles() -> set[str]:
    """Every real distinct ``FQN: Doc: <title>`` value baked into the demo manifests."""
    titles: set[str] = set()
    for manifest_path in sorted(DEMO_MANIFESTS.glob("*/generation__*.json")):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        documents = data.get("payload", {}).get("lexical_index", {}).get("documents") or {}
        for doc in documents.values():
            text = doc.get("text") or ""
            first_line = text.split("\n", 1)[0]
            match = _TITLE_FIRST_LINE.match(first_line)
            if match is not None:
                titles.add(match.group(1))
    return titles


def test_the_real_page_setup_title_no_longer_misfiles_as_getting_started() -> None:
    # The exact repro the round-2 audit found: a reference title about page layout, not onboarding.
    assert classify_content_type(_text("Page Setup and Print Configuration")) == "developer_guide"


@pytest.mark.parametrize(
    "title",
    [
        "How do I install the library?",
        "How do I install Aspose.PDF FOSS for TypeScript?",
        "Installation and Dependencies",
        "Installation and Quick Start",
        "Installation, Dependencies, and Quick Start",
    ],
)
def test_every_real_onboarding_title_still_classifies_as_getting_started(title: str) -> None:
    # Every genuine onboarding title this card's own corpus measurement found - all already
    # matched via `install`, `quick ?start`, or `dependenc`, never the bare `setup` alternative,
    # so narrowing or removing it must not touch any of these.
    assert classify_content_type(_text(title)) == "getting_started"


def test_scope_and_limitations_still_classifies_as_troubleshooting() -> None:
    assert classify_content_type(_text("Scope and Limitations")) == "troubleshooting"


@pytest.mark.parametrize("title", ["Environment Setup", "Quick Setup", "Initial Setup", "Setup"])
def test_a_title_ending_in_setup_still_classifies_as_getting_started(title: str) -> None:
    # Narrowing, not removal: "setup" as the title's own last word is still a genuine onboarding
    # signal ("Environment Setup" is an existing synthetic title in
    # test_doc_classification_by_title.py that this card must not regress).
    assert classify_content_type(_text(title)) == "getting_started"


def test_setup_followed_by_more_title_text_is_not_getting_started() -> None:
    # The actual distinction the fix draws: "setup" mid-title, with unrelated title text after it,
    # is reference material (page layout, print configuration), not onboarding.
    assert classify_content_type(_text("Setup and Printing Options")) == "developer_guide"
    assert classify_content_type(_text("Page Setup and Print Configuration")) == "developer_guide"


def test_the_real_baked_corpus_has_exactly_one_title_whose_classification_the_fix_changes() -> None:
    """Re-derive the measurement this card's contract demands, against the real corpus, every time
    this test runs - not a one-off manual check trusted on faith.

    Before the fix: 'Page Setup and Print Configuration' was the only real title misfiled as
    getting_started by the bare `setup` substring. After the fix: that title is `developer_guide`,
    and every one of the OTHER real distinct titles in the corpus must classify exactly the same as
    it did before the fix (zero regression).
    """
    titles = _real_corpus_titles()
    assert len(titles) >= 50, f"expected dozens of real distinct titles, found {len(titles)}"
    assert "Page Setup and Print Configuration" in titles

    pre_fix_getting_started = re.compile(
        r"install|quick ?start|getting started|prerequisite|setup|dependenc|requirement",
        re.IGNORECASE,
    )
    pre_fix_troubleshooting = re.compile(
        r"troubleshoot|known issue|limitation|unsupported|error|problem", re.IGNORECASE
    )

    def classify_pre_fix(title: str) -> str:
        if pre_fix_troubleshooting.search(title):
            return "troubleshooting"
        if pre_fix_getting_started.search(title):
            return "getting_started"
        return "developer_guide"

    changed = []
    for title in sorted(titles):
        before = classify_pre_fix(title)
        after = classify_content_type(_text(title))
        if before != after:
            changed.append((title, before, after))

    assert changed == [("Page Setup and Print Configuration", "getting_started", "developer_guide")]
