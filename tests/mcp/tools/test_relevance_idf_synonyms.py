"""G3/TC-516 (REQ-G3-008): the relevance module's query-side forgiveness and its IDF weighting.

Pure functions only, offline and deterministic: synonyms (convert/conversion, colour/color,
tab/sheet/worksheet, export/save), the plural-acronym fix ("PDFs"), the generic words, ``distinctive_terms``,
``term_weights`` (clipped IDF), identifier-like ``required_terms`` and the single ``guarded_coverage`` rule
that find_examples and search_docs compare with the unchanged 0.6 threshold.
"""

from __future__ import annotations

import math

import pytest

from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.tools.relevance import (
    RELEVANCE_THRESHOLD,
    SYNONYM_GROUPS,
    coverage,
    distinctive_terms,
    guarded_coverage,
    query_terms,
    required_terms,
    stem_token,
    term_weights,
)


def _payload(documents: list[list[str]]) -> dict:
    """A lexical payload over *documents* (each a token list), shaped like ``build_lexical_index``'s."""
    doc_freq: dict[str, int] = {}
    for tokens in documents:
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1
    return {
        "documents": {
            f"doc-{index}": {"tokens": tokens, "text": ""} for index, tokens in enumerate(documents)
        },
        "doc_freq": doc_freq,
    }


def test_the_threshold_is_unchanged_at_0_6() -> None:
    assert RELEVANCE_THRESHOLD == 0.6


def test_pdfs_does_not_split_into_pd_and_fs() -> None:
    terms = query_terms("merge PDFs", "words")
    assert "pd" not in terms
    assert "fs" not in terms
    assert terms == frozenset({"merge", "pdf"})
    # A lower-case plural or a mixed word is untouched.
    assert "pdfs" not in query_terms("PDFs", "words")
    assert query_terms("AddWatermarkAnnotation", "pdf") >= {"add", "watermark", stem_token("annotation")}


@pytest.mark.parametrize("word", ["example", "examples", "sample", "samples", "use", "how"])
def test_example_sample_use_how_are_generic(word: str) -> None:
    assert query_terms(f"{word} merge", "pdf") == frozenset({"merge"})
    assert word not in distinctive_terms(f"{word} merge", "pdf")


@pytest.mark.parametrize("query", ["Example:", "pdf examples", "how to use samples", "PDF", "  "])
def test_distinctive_terms_are_empty_for_example_colon_and_family_only_queries(query: str) -> None:
    assert distinctive_terms(query, "pdf") == frozenset()


def test_distinctive_terms_keep_the_task_words() -> None:
    assert distinctive_terms("Example: merge pdf files", "pdf") == frozenset({"merge", "file"})
    # query_terms keeps its documented fallback, distinctive_terms deliberately has none.
    assert query_terms("pdf", "pdf") == frozenset({"pdf"})


def _present(term: str, document_token: str) -> bool:
    return coverage([stem_token(term)], [document_token]) == 1.0


def test_convert_matches_conversion_both_ways() -> None:
    assert _present("convert", "conversion")
    assert _present("conversion", "convert")
    assert coverage(query_terms("convert pdf to docx", "pdf"), tokenize("docx conversion")) == 1.0


def test_colour_matches_color() -> None:
    assert _present("colour", "color")
    assert _present("color", "colour")


def test_tab_sheet_worksheet_are_one_group() -> None:
    words = ("tab", "sheet", "worksheet")
    for query_word in words:
        for document_word in words:
            assert _present(query_word, document_word), (query_word, document_word)


def test_export_matches_save() -> None:
    assert _present("export", "save")
    assert _present("save", "export")


def test_a_word_outside_every_group_gets_no_synonym() -> None:
    assert not _present("convert", "color")
    assert not _present("export", "sheet")
    assert not _present("merge", "split")


def test_the_synonym_groups_are_stems() -> None:
    assert len(SYNONYM_GROUPS) == 4
    for group in SYNONYM_GROUPS:
        assert all(stem_token(member) == member for member in group)


def test_synonyms_never_change_document_tokens() -> None:
    tokens = ["conversion", "color", "sheet", "save"]
    before = list(tokens)
    coverage(["convert", "colour", "tab", "export"], tokens)
    guarded_coverage(["convert", "colour", "tab", "export"], tokens, ["conversion"], {"convert": 1.0})
    assert tokens == before
    # The index side is untouched: the tokenizer knows nothing of synonyms.
    assert tokenize("conversion") == ["conversion"]
    assert tokenize("colour") == ["colour"]
    # And the expansion is one-way from the query: a document word is not widened to its group.
    assert coverage(["unrelated"], ["conversion"]) == 0.0


def test_weights_are_clipped_to_0_5_and_2_0_and_deterministic() -> None:
    documents = [
        ["common", "rare" if index == 0 else "filler", "mid" if index < 5 else "filler"]
        for index in range(40)
    ]
    payload = _payload(documents)
    terms = {"common", "rare", "mid", "absent"}
    first = term_weights(terms, payload)
    assert first == term_weights(terms, payload)
    assert set(first) == terms
    assert all(0.5 <= weight <= 2.0 for weight in first.values()), first
    assert first["absent"] == 2.0
    # A term in every document sits on the floor of what the generation knows; a known term never
    # outweighs a term the whole generation has never seen.
    assert first["common"] <= first["mid"] <= first["rare"] <= first["absent"]
    # df 40 of 40 documents: ln(1 + 40 / 41), inside the clip.
    assert first["common"] == pytest.approx(math.log(1 + 40 / 41))


def test_weights_stem_the_raw_tokens_they_count() -> None:
    payload = _payload([["pages"], ["page"], ["other"]])
    weights = term_weights({"page"}, payload)
    unseen = term_weights({"zebra"}, payload)
    # "pages" and "page" both stem to "page", so df is 2 and the weight is below the absent term's.
    assert weights["page"] < unseen["zebra"] == 2.0


def test_an_absent_term_weighs_more_than_a_common_one() -> None:
    payload = _payload([["alpha", "beta"]] * 10 + [["alpha"]] * 10)
    weights = term_weights({"alpha", "beta", "gamma"}, payload)
    assert weights["gamma"] > weights["beta"] > weights["alpha"]
    # So a candidate that misses the unseen word loses more than one that misses the common word.
    misses_gamma = coverage({"alpha", "gamma"}, ["alpha"], weights=weights)
    misses_alpha = coverage({"alpha", "gamma"}, ["gamma"], weights=weights)
    assert misses_gamma < misses_alpha


def test_weighted_coverage_without_weights_is_the_plain_fraction() -> None:
    assert coverage({"a1", "b2", "c3"}, ["a1", "b2"]) == pytest.approx(2 / 3)
    assert coverage({"a1", "b2"}, []) == 0.0
    assert coverage(set(), ["a1"]) == 0.0
    assert coverage({"a1", "b2"}, ["a1"], weights=None) == 0.5
    # Equal weights are the plain fraction too.
    assert coverage({"a1", "b2"}, ["a1"], weights={"a1": 1.0, "b2": 1.0}) == 0.5


def test_weighted_coverage_is_the_weight_of_the_present_terms_over_all() -> None:
    weights = {"heavy": 2.0, "light": 0.5}
    assert coverage({"heavy", "light"}, ["heavy"], weights=weights) == pytest.approx(0.8)
    assert coverage({"heavy", "light"}, ["light"], weights=weights) == pytest.approx(0.2)


def test_an_identifier_like_query_term_must_be_present() -> None:
    assert required_terms("DataSource example") == frozenset({"datasource"})
    terms = query_terms("DataSource example", "pdf")
    assert terms == frozenset({"datasource", "data", "source"})
    without_identifier = ["data", "source", "example"]
    with_identifier = ["datasource", "data", "source"]
    required = required_terms("DataSource example")
    # The sub-words alone would be a full plain match; the identifier itself is missing, so it is 0.
    assert coverage(terms, without_identifier) == pytest.approx(2 / 3)
    assert guarded_coverage(terms, without_identifier, required) == 0.0
    assert guarded_coverage(terms, with_identifier, required) == 1.0


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("getValue", {"getvalue"}),
        ("set_password", {"set", "password"}),
        ("Pdf2Image", {"pdf2image"}),
        ("mp3", {"mp3"}),
        ("call getValue and PdfDocument", {"getvalue", "pdfdocument"}),
    ],
)
def test_identifier_like_runs_are_required(query: str, expected: set[str]) -> None:
    assert required_terms(query) == frozenset(expected)


@pytest.mark.parametrize(
    "query",
    ["merge pdf files", "PDF", "XLSX", "EML", "3MF", "convert XLSX to PDF", "OCR a scanned pdf", "Example:"],
)
def test_plain_words_and_all_caps_acronyms_are_not_required_terms(query: str) -> None:
    assert required_terms(query) == frozenset()


def test_guarded_coverage_without_required_terms_is_just_coverage() -> None:
    weights = {"a1": 1.0, "b2": 2.0}
    assert guarded_coverage({"a1", "b2"}, ["a1"], (), weights) == coverage(
        {"a1", "b2"}, ["a1"], weights=weights
    )
    assert guarded_coverage({"a1", "b2"}, iter(["a1", "b2"])) == 1.0
