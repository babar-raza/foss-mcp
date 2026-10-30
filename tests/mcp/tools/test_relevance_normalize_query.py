"""G3/TC-610 (REQ-G3-011): Q1 - ``normalize_query`` and the optional ``platform`` exclusion.

Two independent, composable fixes to the shared relevance module, built and unit-tested in
isolation here; wiring either into a real tool entrypoint (``lookup``, ``search_docs``,
``find_examples``, ``search_symbols``) is out of scope for this card (Q2/Q3/Q4's job).

1. ``_excluded_terms``/``query_terms``/``distinctive_terms`` gain an optional ``platform``
   parameter: when given, the scope's own platform/runtime word ("net", "java", ...) is excluded
   exactly like the family word already is. ``platform=None`` (the default) must leave every
   existing two-argument call site byte-for-byte unchanged - that backward-compatibility contract
   is what (a) below pins.
2. ``normalize_query(query, family)`` rewrites a literal "Aspose.<family>" dotted product-name
   mention to a non-dotted, two-word form, so it no longer defeats
   ``search_symbols._plain_words``'s "any dotted word disables the evidence stage" rule for a
   query that merely NAMES the product rather than naming an exact symbol.

Pure functions only, offline and deterministic: no I/O, no clock, no randomness.
"""

from __future__ import annotations

from foss_mcp.mcp.tools.relevance import distinctive_terms, is_common_word, normalize_query, query_terms
from foss_mcp.mcp.tools.search_symbols import _plain_words

# The literal LONGQ-001 query (F5/AU3's own reproduction, repeated verbatim here as this card's
# measurement input).
LONGQ_001 = (
    "how do I install and reference the Aspose.PDF FOSS library in my .NET project "
    "so I can start converting files"
)


# --- (a) old two-argument call sites are byte-for-byte unchanged -----------------------------


def test_query_terms_two_argument_form_is_unchanged() -> None:
    # Copied verbatim from tests/mcp/tools/test_relevance_query_terms.py and
    # tests/mcp/tools/test_relevance_idf_synonyms.py so this card's new ``platform`` parameter is
    # proven inert when a caller never passes it.
    assert query_terms("split a pdf into pages", "pdf") == frozenset({"split", "page"})
    assert query_terms("installing cells", "cells") == frozenset({"install"})
    assert query_terms("pdf", "pdf") == frozenset({"pdf"})
    assert query_terms("the api", "pdf") == frozenset({"api"})


def test_distinctive_terms_two_argument_form_is_unchanged() -> None:
    assert distinctive_terms("Example: merge pdf files", "pdf") == frozenset({"merge", "file"})
    assert distinctive_terms("pdf examples", "pdf") == frozenset()


def test_is_common_word_is_unaffected_by_the_platform_parameter() -> None:
    # is_common_word's own signature is untouched by this card (no real caller needs platform
    # there); its internal call into _excluded_terms must still behave exactly as before.
    assert is_common_word("pdf", "pdf") is True
    assert is_common_word("watermark", "pdf") is False


def test_longq_001_without_platform_matches_the_already_measured_baseline() -> None:
    # The already-pinned number (F5/AU3): "net" is not excluded, so it is one of 7 distinctive terms.
    terms = distinctive_terms(LONGQ_001, "pdf")
    assert terms == frozenset({"install", "reference", "net", "project", "start", "convert", "file"})
    assert len(terms) == 7


# --- (b) platform exclusion drops the query's own platform word, concretely measured ---------


def test_longq_001_with_platform_excludes_the_platform_word() -> None:
    without_platform = distinctive_terms(LONGQ_001, "pdf")
    with_platform = distinctive_terms(LONGQ_001, "pdf", "net")
    assert len(without_platform) == 7
    assert "net" in without_platform
    assert len(with_platform) == 6
    assert "net" not in with_platform
    # Nothing else changed: dropping "net" is the ONLY difference.
    assert without_platform - with_platform == frozenset({"net"})


def test_query_terms_with_platform_also_excludes_the_platform_word() -> None:
    assert "net" not in query_terms(LONGQ_001, "pdf", "net")
    assert "net" in query_terms(LONGQ_001, "pdf")


# --- (c) normalize_query fixes _plain_words for a literal product-name mention ----------------


def test_normalize_query_rewrites_the_dotted_aspose_mention() -> None:
    query = "I want to use the Aspose.PDF library for my project"
    normalized = normalize_query(query, "pdf")
    assert normalized == "I want to use the Aspose PDF library for my project"
    assert "Aspose.PDF" not in normalized


def test_plain_words_is_none_for_the_original_query_but_not_for_the_normalized_one() -> None:
    query = "I want to use the Aspose.PDF library for my project"
    normalized = normalize_query(query, "pdf")
    # Prove this concretely by calling _plain_words on both, not by asserting on the string shape.
    assert _plain_words(query) is None
    assert _plain_words(normalized) is not None


def test_normalize_query_handles_the_leading_digit_3d_family() -> None:
    # "Aspose.3D" has a digit immediately after the dot - confirm this is still recognised and
    # that _plain_words is fixed by it too.
    query = "how do I use Aspose.3D to load a model"
    normalized = normalize_query(query, "3d")
    assert normalized == "how do I use Aspose 3D to load a model"
    assert _plain_words(query) is None
    assert _plain_words(normalized) is not None


# --- (d) a genuine identifier-style query is left untouched -----------------------------------


def test_normalize_query_leaves_a_real_identifier_query_untouched() -> None:
    query = "PdfDocument.Rotate"
    assert normalize_query(query, "pdf") == query


def test_plain_words_still_disables_evidence_for_the_real_identifier_query() -> None:
    query = "PdfDocument.Rotate"
    normalized = normalize_query(query, "pdf")
    assert normalized == query
    # The evidence stage must still be correctly disabled for a real exact-name query: this is the
    # regression guard proving the fix is narrow, not a blanket "strip all dots" change.
    assert _plain_words(query) is None
    assert _plain_words(normalized) is None


def test_normalize_query_does_not_touch_an_unrelated_dotted_token() -> None:
    # A file extension / unrelated dotted token in a sentence that also happens to mention another
    # family's product name must not be touched either.
    query = "open the report.docx file next to the Aspose.Cells workbook"
    normalized = normalize_query(query, "pdf")
    assert normalized == query  # family is "pdf", there is no "Aspose.PDF" mention here
    assert "report.docx" in normalized


# --- (e) idempotence and no-op when there is nothing to normalize -----------------------------


def test_normalize_query_is_idempotent() -> None:
    query = "I want to use the Aspose.PDF library for my project"
    once = normalize_query(query, "pdf")
    twice = normalize_query(once, "pdf")
    assert twice == once


def test_normalize_query_is_a_no_op_with_no_product_mention() -> None:
    query = "how do I load a pdf file and rotate a page"
    assert normalize_query(query, "pdf") == query


def test_normalize_query_is_a_no_op_for_an_unrelated_family() -> None:
    # Family "cells" is given but the query names "Aspose.PDF", not "Aspose.Cells" - no mention of
    # THIS family is present, so nothing is rewritten.
    query = "I want to use the Aspose.PDF library for my project"
    assert normalize_query(query, "cells") == query
