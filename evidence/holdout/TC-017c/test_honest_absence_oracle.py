"""HOLDOUT oracle for TC-017c — supervisor-authored, not named by the card.

These tools answer consumer questions, and the plan's own portfolio survey found
that most FOSS repos have no CONTRIBUTING.md and no AGENTS.md at all. So the
common case for two of the eight sections is *absence*, and the tempting failure
is a helpful-sounding summary of a document that does not exist.

Absence is easy to test badly in two opposite directions:

- assert only that a missing document yields NotAvailable, which a tool returning
  NotAvailable for *everything* would also pass; or
- assert only the happy paths, and never discover that a present document is
  reported as missing.

This oracle pins both ends at once: every section is exercised, absence must be
explicit AND carry a reason, presence must return the REAL bytes, and no
NotAvailable reason may read like content. It also checks the specific
flattening the plan calls out — pdf/net's TargetFramework is `net8.0` and must
not be softened into "supports .NET", because the whole point of the section is
that products differ.

Inputs are built from the REAL committed .csproj fixture, not a synthetic stub.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

WT = pathlib.Path(__file__).resolve().parents[3]
for p in (str(WT), str(WT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from foss_mcp.extraction.repo_native_reader import (  # noqa: E402
    DocumentNotPresent,
    DocumentPresent,
)
from foss_mcp.mcp.tools.get_product_reference import (  # noqa: E402
    SECTIONS,
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)

CSPROJ = WT / "tests" / "fixtures" / "repo_native" / "pdf_net.csproj"
NL = chr(10)
CONTRIBUTING_TEXT = "# Contributing" + NL + NL + "Run the tests." + NL
AGENTS_TEXT = "# Agents" + NL + NL + "Use the venv." + NL


@pytest.fixture(scope="module")
def manifest_text():
    assert CSPROJ.is_file(), f"the real committed manifest fixture is missing: {CSPROJ}"
    return CSPROJ.read_text(encoding="utf-8")


def present_inputs(manifest_text):
    """A repo that HAS both optional documents - the rarer case."""
    return ProductReferenceInputs(
        manifest_text=manifest_text,
        contributing=DocumentPresent(
            path="CONTRIBUTING.md", sha="a" * 40, size=len(CONTRIBUTING_TEXT), content=CONTRIBUTING_TEXT
        ),
        agent_guidance=DocumentPresent(
            path="AGENTS.md", sha="b" * 40, size=len(AGENTS_TEXT), content=AGENTS_TEXT
        ),
    )


def absent_inputs(manifest_text):
    """The COMMON case across this portfolio: neither document exists."""
    return ProductReferenceInputs(
        manifest_text=manifest_text,
        contributing=DocumentNotPresent(path="CONTRIBUTING.md"),
        agent_guidance=DocumentNotPresent(path="AGENTS.md"),
    )


def test_every_declared_section_is_answerable_without_raising(manifest_text):
    """All eight, not a convenient subset."""
    inputs = present_inputs(manifest_text)
    assert len(SECTIONS) == 8, f"the registry no longer declares eight sections: {SECTIONS}"
    for section in SECTIONS:
        result = get_product_reference(inputs, section)
        assert isinstance(result, (ReferenceContent, NotAvailable)), f"{section} returned {result!r}"


def test_an_absent_document_is_explicit_and_never_summarised(manifest_text):
    """The fabrication case. Most repos in this portfolio hit it."""
    inputs = absent_inputs(manifest_text)
    for section in ("contributing", "agent_guidance"):
        result = get_product_reference(inputs, section)
        assert isinstance(result, NotAvailable), (
            f"{section} produced content for a document that does not exist: {result!r}"
        )
        assert result.reason and result.reason.strip(), f"{section} absence carries no reason"


def test_a_present_document_returns_its_real_bytes_not_a_paraphrase(manifest_text):
    """The other direction: honest absence must not be implemented as always-absent."""
    inputs = present_inputs(manifest_text)
    contributing = get_product_reference(inputs, "contributing")
    assert isinstance(contributing, ReferenceContent), (
        f"a document that EXISTS was reported unavailable: {contributing!r}"
    )
    assert "Run the tests." in contributing.text, (
        f"the section paraphrased instead of returning the document: {contributing.text!r}"
    )


def test_absence_and_presence_actually_differ(manifest_text):
    """Anti-vacuity, stated as a single claim: the tool must distinguish the two."""
    present = get_product_reference(present_inputs(manifest_text), "contributing")
    absent = get_product_reference(absent_inputs(manifest_text), "contributing")
    assert type(present) is not type(absent), (
        "presence and absence produce the same kind of answer - the tool is not reading its inputs"
    )


def test_compatibility_is_the_exact_manifest_value_not_a_softened_summary(manifest_text):
    """Plan §8.2: this must not be flattened, because products genuinely differ.

    pdf/net is net8.0-only; words/net targets net462;netstandard2.0;net8.0. A tool
    that answers "supports .NET" for both has destroyed the only useful fact.
    """
    result = get_product_reference(present_inputs(manifest_text), "compatibility")
    assert isinstance(result, ReferenceContent), f"compatibility was unavailable: {result!r}"
    assert "net8.0" in result.text, f"the real TargetFramework is absent from the answer: {result.text!r}"
    for softened in ("supports .net", "compatible with .net", "any .net"):
        assert softened not in result.text.lower(), (
            f"compatibility was flattened into a generic claim: {result.text!r}"
        )


def test_a_not_available_reason_never_reads_like_content(manifest_text):
    """A reason is an explanation of absence. If it starts describing the thing,
    a caller can mistake it for the document."""
    inputs = absent_inputs(manifest_text)
    for section in SECTIONS:
        result = get_product_reference(inputs, section)
        if isinstance(result, NotAvailable):
            assert len(result.reason) < 300, (
                f"{section}'s absence reason is long enough to be mistaken for content: {result.reason!r}"
            )


def test_an_unknown_section_is_rejected_rather_than_guessed(manifest_text):
    inputs = present_inputs(manifest_text)
    for bogus in ("licence", "INSTALL", "changelog", ""):
        with pytest.raises(ValueError):
            get_product_reference(inputs, bogus)
