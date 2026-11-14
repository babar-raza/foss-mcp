"""G3/TC-813 (REQ-G3-014): authored-example titles are bounded at 12 words.

``tests/conformance/test_sentence_wrapped_retrieval.py`` wraps every real titled chunk in twelve
conversational sentence frames and requires each rendered sentence to stay within 12-35 words. The
tightest frame (``was_wondering``) adds 23 words around the title, so a title of at most 12 words is the
largest that keeps every frame within bound; a 13-word title breaks that frame. The validator in
``foss_mcp.indexing.authored_examples`` rejects a longer title, and every committed sidecar title obeys
the same limit. Offline: nothing here reaches a network, a toolchain or an interpreter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from foss_mcp.indexing import authored_examples
from foss_mcp.indexing.authored_examples import SIDECAR_NAME, validate_authored_examples
from foss_mcp.indexing.example_candidates import normalised_code_sha256
from tests.conformance.test_sentence_wrapped_retrieval import (
    _FAMILY_DISPLAY,
    _PLATFORM_DISPLAY,
    FRAMES,
    _display,
    _render,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FURNISHED = REPO_ROOT / "tests" / "fixtures" / "furnished"
MAX_WORDS = 12
MAX_SENTENCE_WORDS = 35
COMMIT = "a" * 40

_API_TYPES = [
    {
        "name": "Widget",
        "canonical_namespace": "Acme.Widgets",
        "class_import": "Acme.Widgets.Widget",
        "methods": [{"name": "Spin"}],
        "properties": [],
    }
]


def _sidecars() -> list[Path]:
    return sorted(FURNISHED.glob(f"*/{SIDECAR_NAME}"))


def _title_of_words(count: int) -> str:
    return " ".join(f"word{number}" for number in range(count))


def _document(titles: list[str]) -> dict:
    code = "# Authored example: spin a widget\nimport acme\nacme.Widget().Spin()\n"
    return {
        "schema_version": 1,
        "pilot": "demo_python",
        "library_repository": "example-org/Demo-FOSS-for-Python",
        "library_commit": COMMIT,
        "language": "python",
        "examples": [
            {
                "title": title,
                "description": "Create a widget and spin it once.",
                "symbols": ["Widget.Spin"],
                "code": code,
                "code_sha256": normalised_code_sha256(code),
            }
            for title in titles
        ],
    }


def _validate(titles: list[str]) -> list[str]:
    return validate_authored_examples(
        _document(titles),
        pilot="demo_python",
        library_platform="python",
        library_commit=COMMIT,
        api_types=_API_TYPES,
    )


def _committed_titles() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for sidecar in _sidecars():
        document = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        pairs.extend((sidecar.parent.name, example["title"]) for example in document["examples"])
    return pairs


def _longest_rendered_sentence(title: str, family: str, platform: str) -> int:
    return max(len(_render(template, title, family, platform).split()) for _name, template in FRAMES)


def test_the_validator_limit_is_twelve_words() -> None:
    assert authored_examples._MAX_TITLE_WORDS == MAX_WORDS


def test_a_twelve_word_title_is_accepted() -> None:
    assert _validate([_title_of_words(12)]) == []


def test_a_thirteen_word_title_is_rejected_with_a_clear_error() -> None:
    problems = _validate([_title_of_words(13)])
    assert len(problems) == 1, problems
    assert "title has 13 words" in problems[0]
    assert "at most 12" in problems[0]
    assert "example 1:" in problems[0]


def test_twelve_words_is_the_largest_title_every_frame_accepts() -> None:
    """The rule is the right bound: with the worst-case family and platform names a 12-word title keeps
    every frame within 35 words, and a 13-word title overflows the tightest frame."""
    worst_12 = 0
    worst_13 = 0
    for family in _FAMILY_DISPLAY.values():
        for platform in _PLATFORM_DISPLAY.values():
            worst_12 = max(worst_12, _longest_rendered_sentence(_title_of_words(12), family, platform))
            worst_13 = max(worst_13, _longest_rendered_sentence(_title_of_words(13), family, platform))
    assert worst_12 <= MAX_SENTENCE_WORDS, worst_12
    assert worst_13 > MAX_SENTENCE_WORDS, worst_13


def test_there_are_committed_sidecars_to_check() -> None:
    assert len(_sidecars()) >= 30
    assert len(_committed_titles()) >= 140


def test_every_committed_sidecar_title_is_at_most_twelve_words() -> None:
    too_long = [
        f"{pilot}: {len(title.split())} words: {title!r}"
        for pilot, title in _committed_titles()
        if len(title.split()) > MAX_WORDS
    ]
    assert not too_long, too_long


def test_no_committed_sidecar_repeats_a_title_within_a_pilot() -> None:
    duplicates: list[str] = []
    for sidecar in _sidecars():
        document = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        titles = [example["title"] for example in document["examples"]]
        duplicates.extend(
            f"{sidecar.parent.name}: {title!r}" for title in set(titles) if titles.count(title) > 1
        )
    assert not duplicates, duplicates


def test_every_committed_title_renders_within_the_sentence_bound_in_every_frame() -> None:
    overflow: list[str] = []
    for pilot, title in _committed_titles():
        family_key, _, platform_key = pilot.partition("_")
        family = _display(_FAMILY_DISPLAY, family_key)
        platform = _display(_PLATFORM_DISPLAY, platform_key)
        longest = _longest_rendered_sentence(title, family, platform)
        if longest > MAX_SENTENCE_WORDS:
            overflow.append(f"{pilot}: {longest} words: {title!r}")
    assert not overflow, overflow


@pytest.mark.parametrize("sidecar", _sidecars(), ids=lambda path: path.parent.name)
def test_each_committed_sidecar_passes_the_structural_title_rules(sidecar: Path) -> None:
    document = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    problems = [problem for problem in authored_examples._structure_problems(document) if "title" in problem]
    assert not problems, problems
