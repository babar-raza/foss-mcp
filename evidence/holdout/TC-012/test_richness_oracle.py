"""HOLDOUT oracle for TC-012 — supervisor-authored, and the card does not name it.

Why this exists. TC-012 passed every check it declared, its falsifier bit, and it
still failed the requirement it was written to satisfy: `classify_richness`
called auto-generated boilerplate `detailed`, because it measures FORMATTING
(headings, bullets, a fenced code block) rather than INFORMATION.

That was invisible to the card's own suite because the fixtures were written by
the same worker as the classifier — the test agreed with the code because both
came from one idea of what "detailed" means. A card cannot escape that on its
own. Only data it never saw can.

The evidence that settles it: `aspose-font-foss/Aspose.Font-FOSS-for-Python`
publishes release bodies that are byte-identical in LENGTH (458) across at least
six consecutive versions, differing only in the version string. Whatever else
that is, it is not a per-release migration note.

The bodies under `data/` are real, fetched from the live GitHub API. Nothing here
is synthetic, and nothing here is editable by the worker: holdout files live
under `evidence/`, which is globally denied to every card.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent / "data"
SRC = Path(__file__).resolve().parents[3] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from foss_mcp.extraction.github_release_reader import classify_richness  # noqa: E402


def body(name: str) -> str:
    return (DATA / f"{name}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        # Real per-release engineering content: an overview naming what changed,
        # and in pdf-go's case a single named fix with a stated audience.
        ("cells-python", "detailed"),
        ("pdf-go", "detailed"),
        # Filled-in template. Well-formatted, structurally rich, and carrying no
        # information about THIS release beyond its version number.
        ("font-python", "templated"),
        ("font-python-older", "templated"),
        # No release body at all.
        ("barcode-python", "none"),
    ],
)
def test_richness_reflects_information_not_formatting(sample, expected):
    got = classify_richness(body(sample))
    assert got == expected, (
        f"{sample}: expected {expected!r}, got {got!r}. A client has to be able to tell a real "
        f"migration note from boilerplate; that is the whole point of typing this field."
    )


def test_two_template_instances_are_not_reported_as_distinct_quality():
    """The strongest signal available, and the one no formatting heuristic sees.

    Two different versions of the same template carry the same information about
    their own release: none. They must therefore classify identically, and they
    must not classify as `detailed`.
    """
    a = classify_richness(body("font-python"))
    b = classify_richness(body("font-python-older"))
    assert a == b, f"two instances of one template classified differently: {a!r} vs {b!r}"
    assert a != "detailed", (
        "six consecutive releases of this repo have byte-identical body length and differ only in "
        "the version string; calling that `detailed` is precisely the dishonest degradation this "
        "requirement forbids"
    )


def test_a_real_note_outranks_a_template():
    """Ordering, not just labels: substance must never rank below boilerplate."""
    order = {"none": 0, "templated": 1, "detailed": 2}
    real = order[classify_richness(body("cells-python"))]
    template = order[classify_richness(body("font-python"))]
    assert real > template, "a real migration note must outrank a filled-in template"
