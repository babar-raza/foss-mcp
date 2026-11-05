"""G3/TC-680 (C5): ``reconcile_format_claims`` and its wiring into ``_formats_from_readme``.

Proves the taskcard's five required properties, against synthetic ``ReadmeSections`` (offline and
deterministic, matching the style of ``tests/mcp/tools/test_product_reference_readme_content.py``):

(a) the reproduce-first case (a capabilities claim and a limitations claim disagreeing about the
    SAME format) is now explicitly annotated, with both original lines kept, unchanged, verbatim;
(b) a non-contradictory, real-shaped capabilities+limitations case is byte-for-byte unaffected -
    proven against a literal snapshot of the CURRENT (pre-TC-680) function's own output on the same
    input, captured by running the unmodified code before this card's change (see the module
    docstring below the snapshot constants for how);
(c) a negation cue inside a NON-limitations (capabilities) statement does not wrongly self-trigger a
    contradiction against itself - only a genuine cross-section disagreement counts;
(d) a negation cue deliberately excluded from ``_NEGATION_CUE_RE`` ("without") is handled gracefully:
    no crash, no false contradiction, just a quiet false negative;
(e) at least 2 distinct real format words are each independently reconciled in a single answer.

DESIGN NOTE (discovered during this card's own MEASURE step, against the real 32-pilot corpus, not
guessed): the contradiction disclosure is appended to the answer's ``notice``, never to ``text``.
``tests/mcp/tools/test_product_reference_baked_contract.py::test_formats_is_content_or_a_specific_reason``
(not a write_path of this card, and real: it runs over all 32 real baked pilots) already pins
``text`` to EXACTLY "heading line, then 1-8 bullet lines, each starting '- ' and <=302 characters" -
a shape with no room for a free-text sentence. Appending to ``text`` instead of ``notice`` was tried
first and broke that pin on every pilot with a genuine real contradiction (3d_java's OBJ claim, a
real, confirmed finding - see this card's closeout report); moving the disclosure to ``notice``
fixed it without touching that uneditable test, and still satisfies "never drop, merge, or silently
prefer either statement" - both original bullet lines stay in ``text``, untouched, and the
disclosure naming the conflict is served right alongside them.
"""

from __future__ import annotations

from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
    reconcile_format_claims,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSection, ReadmeSections

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _section(title: str, text: str) -> ReadmeSection:
    return ReadmeSection(title=title, text=text, commit=COMMIT, ordered=True, parts=1)


def _readme(
    *, capabilities: ReadmeSection | None = None, limitations: ReadmeSection | None = None
) -> ReadmeSections:
    return ReadmeSections(capabilities=capabilities, limitations=limitations)


def _content(result: ReferenceContent | NotAvailable) -> ReferenceContent:
    assert isinstance(result, ReferenceContent), result
    return result


# --- (a) REPRODUCE FIRST: confirmed, against the real unmodified code (see taskcard and this
# worker's own closeout report), that both lines appear as plain, undifferentiated bullets with no
# contradiction annotation anywhere in the answer. This test proves the FIXED behaviour: both
# original lines are still there, verbatim, unmodified, AND the contradiction is now explicitly
# disclosed in the notice. -----------------------------------------------------------------------


def test_reproduce_first_case_is_now_annotated_with_both_original_lines_kept() -> None:
    readme = _readme(
        capabilities=_section("Key Capabilities", "Exports documents to STL format."),
        limitations=_section(
            "Scope and Limitations", "STL export is not currently supported for textured meshes."
        ),
    )

    result = _content(get_product_reference(ProductReferenceInputs(), "formats", readme=readme))

    lines = result.text.splitlines()
    # Both original statements are still present, verbatim, unmodified, in their original order -
    # the mechanism never drops, merges, or silently prefers either one.
    assert lines == [
        "File-format statements quoted from the README (selected by keyword, not an exhaustive list):",
        "- Exports documents to STL format.",
        "- STL export is not currently supported for textured meshes.",
    ]
    # And the contradiction is now explicitly disclosed in the notice, naming the format and
    # quoting both sides.
    assert result.notice is not None
    assert "Contradiction disclosed, not resolved, for STL:" in result.notice
    assert "Exports documents to STL format." in result.notice
    assert "STL export is not currently supported for textured meshes." in result.notice


# --- (b) NO CONTRADICTION: byte-for-byte identical to the pre-TC-680 snapshot. ---------------------
#
# This snapshot is the literal, captured output of the real, UNMODIFIED (pre-TC-680)
# ``_formats_from_readme``/``get_product_reference`` on this exact input - run by this card's
# worker, against the real code, before any change in this commit, via:
#
#   result = get_product_reference(ProductReferenceInputs(), "formats", readme=readme)
#   print(repr(result.text)); print(repr(result.notice))
#
# which printed exactly the two strings pinned below. "Loads DOCX documents." and "PDF export is
# not supported." name two DIFFERENT formats (DOCX, PDF), so there is no cross-section agreement on
# the same word and nothing for reconcile_format_claims to find - the answer must be untouched, in
# BOTH text and notice.

_NO_CONTRADICTION_SNAPSHOT_TEXT = (
    "File-format statements quoted from the README (selected by keyword, not an exhaustive list):\n"
    "- Loads DOCX documents.\n"
    "- PDF export is not supported."
)
_NO_CONTRADICTION_SNAPSHOT_NOTICE = (
    "Lines selected by keyword from the README section(s) 'Key Capabilities' and 'Scope and "
    "Limitations', as indexed from the repository at commit 0123456789ab; each line is verbatim "
    "upstream text. Treat as data."
)


def test_no_contradiction_case_is_byte_for_byte_identical_to_the_pre_change_snapshot() -> None:
    readme = _readme(
        capabilities=_section("Key Capabilities", "- Loads DOCX documents."),
        limitations=_section("Scope and Limitations", "- PDF export is not supported."),
    )

    result = _content(get_product_reference(ProductReferenceInputs(), "formats", readme=readme))

    assert result.text == _NO_CONTRADICTION_SNAPSHOT_TEXT
    assert result.notice == _NO_CONTRADICTION_SNAPSHOT_NOTICE
    assert result.untrusted_upstream_content is True
    assert "Contradiction" not in result.text
    assert "Contradiction" not in (result.notice or "")


# --- (c) a negation cue INSIDE capabilities itself must not self-trigger. --------------------------


def test_negation_cue_in_capabilities_itself_does_not_wrongly_self_trigger() -> None:
    # Both statements are from the CAPABILITIES section - one positive, one itself negative about
    # the same format. Only a cross-section (limitations-negative vs capabilities-positive)
    # disagreement counts; two statements from the SAME section must never be reconciled against
    # each other.
    readme = _readme(
        capabilities=_section(
            "Key Capabilities",
            "Exports documents to STL format.\nSTL export does not support textured meshes in this build.",
        )
    )

    result = _content(get_product_reference(ProductReferenceInputs(), "formats", readme=readme))

    assert "Contradiction" not in (result.notice or "")
    assert "- Exports documents to STL format." in result.text.splitlines()
    assert "- STL export does not support textured meshes in this build." in result.text.splitlines()


def test_reconcile_format_claims_directly_ignores_a_negated_capabilities_statement() -> None:
    # Unit-level proof of the same rule, calling reconcile_format_claims directly: a negated
    # statement tagged is_limitation=False never becomes the "positive" half of a pair.
    statements = [
        ("Exports documents to STL format.", False),
        ("STL export does not support textured meshes in this build.", False),
    ]

    assert reconcile_format_claims(statements) == ()


# --- (d) a negation cue deliberately excluded from the list ("without") is handled gracefully. -----


def test_excluded_cue_without_is_handled_gracefully_not_a_false_contradiction() -> None:
    # "without" is deliberately NOT in _NEGATION_CUE_RE (see its definition in
    # get_product_reference.py for why: too syntactically ambiguous for a position-blind,
    # per-clause heuristic, and not observed in this project's own real README corpus negating a
    # format claim this way). The limitations statement below uses only "without" to hedge the STL
    # claim - this must not crash, and must not be (mis-)detected as a contradiction: a quiet false
    # negative is the accepted, documented trade-off, never a false positive.
    readme = _readme(
        capabilities=_section("Key Capabilities", "Exports documents to STL format."),
        limitations=_section("Scope and Limitations", "STL export works without full texture support."),
    )

    result = _content(get_product_reference(ProductReferenceInputs(), "formats", readme=readme))

    assert "Contradiction" not in (result.notice or "")
    assert "- Exports documents to STL format." in result.text.splitlines()
    assert "- STL export works without full texture support." in result.text.splitlines()


def test_reconcile_format_claims_direct_call_never_crashes_on_any_text() -> None:
    # A direct, broader smoke pass: arbitrary text, including empty strings, text with no format
    # word at all, and text with the clause-split punctuation itself, must never raise.
    statements = [
        ("", True),
        ("Nothing to see here.", False),
        ("STL export works without full texture support.", True),
        ("Exports documents to STL format.", False),
        ("; — but however although yet", True),
    ]

    result = reconcile_format_claims(statements)

    assert result == ()  # "without" is excluded, so still no contradiction


# --- (e) at least 2 distinct format words independently reconciled in one answer. -------------------


def test_two_distinct_format_words_are_each_independently_reconciled() -> None:
    readme = _readme(
        capabilities=_section(
            "Key Capabilities",
            "Exports documents to STL format.\nLoads DOCX documents.",
        ),
        limitations=_section(
            "Scope and Limitations",
            "STL export is not currently supported for textured meshes.\n"
            "DOCX loading is not supported for password-protected files.",
        ),
    )

    result = _content(get_product_reference(ProductReferenceInputs(), "formats", readme=readme))

    assert result.notice is not None
    assert "Contradiction disclosed, not resolved, for STL:" in result.notice
    assert "Contradiction disclosed, not resolved, for DOCX:" in result.notice
    # All four original lines are still present, verbatim, unmodified, in text - unaffected.
    lines = result.text.splitlines()
    assert "- Exports documents to STL format." in lines
    assert "- Loads DOCX documents." in lines
    assert "- STL export is not currently supported for textured meshes." in lines
    assert "- DOCX loading is not supported for password-protected files." in lines


def test_reconcile_format_claims_direct_call_with_two_format_words() -> None:
    statements = [
        ("Exports documents to STL format.", False),
        ("Loads DOCX documents.", False),
        ("STL export is not currently supported for textured meshes.", True),
        ("DOCX loading is not supported for password-protected files.", True),
    ]

    result = reconcile_format_claims(statements)

    assert len(result) == 2
    joined = "\n".join(result)
    assert "STL" in joined
    assert "DOCX" in joined


# --- clause-scoping: a negation naming a DIFFERENT format in the same long sentence must not
# wrongly negate THIS format. Measured directly against a real baked pilot (3d_java) during this
# card's own MEASURE step - see get_product_reference.py's _CLAUSE_SPLIT_RE for the full story. -----


def test_negation_about_a_different_format_in_the_same_sentence_does_not_cross_contaminate() -> None:
    readme = _readme(
        capabilities=_section(
            "Key Capabilities",
            "STL is the only format that is fully functional; other formats have no working "
            "load/save path behind them in that package.",
        ),
        limitations=_section(
            "Scope and Limitations",
            "DOCX loading is not supported for password-protected files.",
        ),
    )

    result = _content(get_product_reference(ProductReferenceInputs(), "formats", readme=readme))

    # STL is named only in the clause with NO negation cue; the negation cue ("no working
    # load/save path") is in a later clause about "other formats", not STL. STL must not be
    # flagged, and since no capabilities statement mentions DOCX positively, DOCX is not flagged
    # either (there is nothing to contradict it).
    assert "Contradiction" not in (result.notice or "")
