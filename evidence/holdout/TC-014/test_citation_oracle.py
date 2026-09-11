"""HOLDOUT oracle for TC-014 — supervisor-authored, not named by the card.

TC-014's substance is a three-way distinction that is easy to implement
vacuously. If `insufficient_evidence` is returned for everything, every test
about "unresolvable claims are excluded" still passes while the system has
stopped distinguishing *wrong* from *not corroborated* — and that distinction is
the whole point of §8.10: citation resolution proves traceability, not
correctness.

So the load-bearing test here is not "does the 805-classes claim fail". It is
"are all three verdicts actually reachable". A mechanism that can never say
`unsupported` is not validating anything; it is declining to.

Everything below runs against the real committed fixtures — the real furnished
`products.aspose.org` page and the real TC-011 extraction — never a synthetic
stand-in.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

WT = pathlib.Path(__file__).resolve().parents[3]
SRC = WT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from foss_mcp.normalization.citation import (  # noqa: E402
    find_numeric_claims,
    known_counts_from_fixture,
    validate_numeric_claim,
)

API_FIXTURE = WT / "tests" / "fixtures" / "pdf_net" / "api_surface.json"
FURNISHED = WT / "tests" / "fixtures" / "furnished" / "pdf_net" / "pages" / "_index.md"


@pytest.fixture(scope="module")
def known_counts():
    return known_counts_from_fixture(json.loads(API_FIXTURE.read_text(encoding="utf-8")))


def verdict(result) -> str:
    return getattr(result, "verdict", result)


def test_all_three_verdicts_are_reachable(known_counts):
    """The anti-vacuity guard, and the most important assertion in this file.

    A validator that only ever returns `insufficient_evidence` passes every
    exclusion test while validating nothing at all.
    """
    assert "types" in known_counts, (
        "raw knowledge must expose at least one confident count, or nothing can ever be "
        "corroborated and the whole mechanism collapses to 'I don't know'"
    )
    confident = known_counts["types"]

    supported = verdict(validate_numeric_claim(confident, "types", known_counts))
    contradicts = verdict(validate_numeric_claim(confident + 11446, "types", known_counts))
    uncountable = verdict(validate_numeric_claim(805, "classes", known_counts))

    assert supported == "supported", f"a claim matching raw knowledge must be supported, got {supported!r}"
    assert contradicts == "unsupported", (
        f"a claim CONTRADICTING a confident count must be unsupported, got {contradicts!r}. If this "
        f"returns insufficient_evidence the validator has stopped distinguishing wrong from unknown."
    )
    assert uncountable == "insufficient_evidence", (
        f"a claim about a quantity raw knowledge cannot count must be insufficient_evidence, got "
        f"{uncountable!r}"
    )
    assert len({supported, contradicts, uncountable}) == 3, "the three verdicts must be distinct"


def test_the_real_furnished_805_classes_claim_is_not_corroborated(known_counts):
    """OQ-001's actual sentence, from the real exported page.

    The fixture is deliberately truncated, so the class count is genuinely
    unknown. The correct answer is 'cannot corroborate' - never 'supported',
    and never silently retained.
    """
    page = FURNISHED.read_text(encoding="utf-8")
    sentence = re.search(r"[^.]*\b805 classes\b[^.]*\.", page)
    assert sentence, "the real 805-classes sentence has vanished from the furnished fixture"

    claims = find_numeric_claims(sentence.group(0))
    assert (805, "classes") in claims, f"the claim was not extracted from its own sentence: {claims}"
    assert verdict(validate_numeric_claim(805, "classes", known_counts)) != "supported", (
        "a precise count that raw knowledge cannot confirm must never be served as supported - this "
        "is the fabricated-constraint defect class the project exists to prevent"
    )


def test_a_truncated_fixture_never_yields_a_confident_per_kind_count():
    """The reasoning that makes the verdict correct, not merely convenient.

    'classes' is absent from known_counts because the fixture is truncated, not
    because the key happens to be missing. Flip truncated to false and the count
    must become available - otherwise the mechanism is right by accident.
    """
    raw = json.loads(API_FIXTURE.read_text(encoding="utf-8"))
    assert raw.get("truncated") is True, "precondition: TC-011 committed a reduced fixture"
    assert "classes" not in known_counts_from_fixture(raw)

    complete = dict(raw)
    complete["truncated"] = False
    assert "classes" in known_counts_from_fixture(complete), (
        "an untruncated fixture must yield a per-kind count; if it does not, the insufficient "
        "verdict above was luck rather than reasoning"
    )


def test_chunking_is_section_level_on_the_real_furnished_page():
    """Page-level chunking cannot localise one bad sentence inside a good page.

    Built through the module's own `make_document` factory rather than the
    dataclass constructor, so this oracle tests the card's public contract and
    not my guess at its field order.
    """
    from foss_mcp.normalization.chunker import chunk_document
    from foss_mcp.normalization.document_schema import Provenance, make_document

    body = FURNISHED.read_text(encoding="utf-8")
    doc = make_document(
        source_kind="furnished",
        content_type="product_overview",
        provenance=Provenance(
            repository="Aspose/aspose.org",
            commit="0" * 40,
            path="content/products.aspose.org/en/pdf/net/_index.md",
        ),
        title="Aspose.PDF FOSS for .NET",
        body=body,
    )
    chunks = chunk_document(doc)
    assert len(chunks) > 1, (
        f"the real page produced {len(chunks)} chunk(s); section granularity is what makes it "
        f"possible to exclude one unsupported sentence without discarding the whole page"
    )
    assert sum(len(c.text) for c in chunks) > 0, "chunks carry no text"
