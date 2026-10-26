"""G3/TC-516 (REQ-G3-008): the relevance module's query-side forgiveness and its IDF weighting.

Pure functions only, offline and deterministic: synonyms (convert/conversion, colour/color,
tab/sheet/worksheet, export/save), the plural-acronym fix ("PDFs"), the generic words, ``distinctive_terms``,
``term_weights`` (clipped IDF), identifier-like ``required_terms`` and the single ``guarded_coverage`` rule
that find_examples and search_docs compare with the unchanged 0.6 threshold.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.tools.relevance import (
    FAMILY_UBIQUITY_FRACTION,
    MIN_SCOPE_DOCUMENTS_FOR_UBIQUITY,
    RELEVANCE_THRESHOLD,
    SYNONYM_GROUPS,
    coverage,
    distinctive_terms,
    evidence_terms,
    family_generic_fraction,
    family_generic_stems,
    guarded_coverage,
    query_terms,
    required_terms,
    stem_token,
    term_weights,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEX_PYTHON_MANIFEST_DIR = REPO_ROOT / "infra" / "demo-manifests" / "tex__python__self_extracted"


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


# ---- TC-585 (G3/REQ-G3-009): the per-family ubiquitous-noun detector ----------------------------------
#
# Independent reproduction (RST round-2, RD-5) found that a word merely ubiquitous within ONE family's
# own data ("file" on pdf, "color" on slides, "cell" on cells) was never excluded by the hand-maintained
# global ``_GENERIC_WORDS``, so a literal match on it could bury a correct, differently-named answer.
# ``family_generic_stems`` measures this per SCOPE from the same doc_freq table ``term_weights`` already
# reads - these tests pin the measurement itself (pure, offline, synthetic payloads), never an answer
# derived from the function's own prior output.


def test_a_word_in_almost_every_document_is_family_generic() -> None:
    # "common" is in all 20 documents (20/20 = 1.0, far over the floor); "rare" is in one. 20 documents
    # also clears MIN_SCOPE_DOCUMENTS_FOR_UBIQUITY - below it no claim is measured at all (next test).
    payload = _payload([["common", "rare" if index == 0 else "filler"] for index in range(20)])
    assert family_generic_stems({"common", "rare"}, "sample", payload) == frozenset({"common"})


def test_the_measured_floor_is_0_12() -> None:
    assert FAMILY_UBIQUITY_FRACTION == pytest.approx(0.12)


def test_the_minimum_scope_size_is_20_documents() -> None:
    assert MIN_SCOPE_DOCUMENTS_FOR_UBIQUITY == 20


def test_too_few_documents_makes_no_claim_however_high_the_fraction() -> None:
    # 1 of 2 documents is already a 50 percent fraction without meaning anything about real ubiquity -
    # reproduced directly against this exact shape (TC-585's own worker report): a 2-chunk synthetic
    # fixture wrongly swept up "create" and "pivot" as "family-generic" before this floor existed.
    payload = _payload([["common"], ["common", "rare"]])
    assert family_generic_stems({"common", "rare"}, "sample", payload) == frozenset()


def test_a_word_just_under_the_floor_is_not_family_generic() -> None:
    # 2 documents of 20 carry "rare" (0.10, under 0.12); "common" (0.90) clears it.
    documents = [["common"]] * 18 + [["common", "rare"]] * 2
    payload = _payload(documents)
    assert family_generic_stems({"common", "rare"}, "sample", payload) == frozenset({"common"})
    assert "rare" not in family_generic_stems({"rare"}, "sample", payload)


def test_family_generic_stems_sums_a_stems_raw_token_variants() -> None:
    # "page" and "pages" both stem to "page" (TC-516's own stemmer); term_weights already sums a stem's
    # raw tokens the same way, so family_generic_stems must count both forms as the one word they are.
    documents = [["page"]] * 6 + [["pages"]] * 6 + [["unrelated"]] * 8
    payload = _payload(documents)
    assert family_generic_stems({"page"}, "sample", payload) == frozenset({"page"})


def test_the_familys_own_stem_is_never_included_however_ubiquitous() -> None:
    # Every document of a real scope's own namespace carries the family word; it has its OWN, different
    # treatment (the family-stem light-term rescue) that most of the corpus depends on, so it must never
    # be swept into the plain generic-discard bucket by this measurement alone.
    payload = _payload([["pdf", "file"]] * 20)
    assert family_generic_stems({"pdf", "file"}, "pdf", payload) == frozenset({"file"})


def test_no_document_frequency_table_makes_no_claim() -> None:
    assert family_generic_stems({"anything"}, "sample", None) == frozenset()
    assert family_generic_stems({"anything"}, "sample", {}) == frozenset()
    assert family_generic_stems({"anything"}, "sample", {"documents": {}, "doc_freq": {}}) == frozenset()


def test_family_generic_fraction_is_the_plain_unclipped_fraction() -> None:
    payload = _payload([["common"]] * 6 + [["other"]] * 4)
    assert family_generic_fraction("common", payload) == pytest.approx(0.6)
    assert family_generic_fraction("absent", payload) == 0.0
    assert family_generic_fraction("common", None) == 0.0


def test_family_generic_fraction_ranks_the_worst_offender_first() -> None:
    # The same measurement a caller needing to pick ONE word to exclude from several candidates (TC-585's
    # own worker report: "cell"/"workbook"/"value" on the cells pilots) would rank by.
    payload = _payload([["cell", "workbook"]] * 8 + [["cell"]] * 2)
    ranked = sorted({"cell", "workbook"}, key=lambda stem: -family_generic_fraction(stem, payload))
    assert ranked == ["cell", "workbook"]


# ---- G3/TC-587 (REQ-G3-009, RC-7 round-2 audit): evidence_terms must keep a splitting camelCase run's
# WHOLE word as a candidate ALONGSIDE its sub-word parts, mirroring lexical_index_writer.tokenize's own
# discipline (the whole run is always a candidate; sub-words are added in addition, never instead). Before
# this fix, a run that split into more than one sub-word part lost the whole word entirely - only the
# (often meaningless) fragments survived - which buried an exact-identifier query like "LaTeXDocument"
# behind "la"/"te"/"document" alone. -------------------------------------------------------------------


def test_evidence_terms_keeps_the_whole_splitting_camel_run_alongside_its_parts() -> None:
    # "LaTeXDocument" splits (via the same camel-part regex tokenize() uses) into "La"/"Te"/"X"/"Document".
    # tokenize() itself would emit the whole run ("latexdocument") PLUS each sub-word ("la", "te", "document";
    # the single-letter "x" is dropped by both tokenize() and evidence_terms as not meaningful alone). A
    # neutral family ("pdf") is used so the whole run is not itself a family word/alias that would divert it
    # to the common-word fallback instead of ``kept`` - this test is about the candidate set evidence_terms
    # builds, not about the family-alias rescue (a separate, unrelated mechanism).
    words = {term.word for term in evidence_terms("LaTeXDocument", "pdf")}
    assert words == {"latexdocument", "la", "te", "document"}
    # The whole run and its parts are independent candidates, not a repeat of the same key under ``seen``.
    keys = {term.key for term in evidence_terms("LaTeXDocument", "pdf")}
    assert len(keys) == len({"latexdocument", "la", "te", "document"})


def test_evidence_terms_still_returns_only_the_run_when_it_does_not_split() -> None:
    # A run with at most one camel-part (plain lower-case, or PascalCase with a single capitalised word) is
    # unaffected: the single-element branch of the fix is unchanged from before.
    assert {term.word for term in evidence_terms("Document", "pdf")} == {"document"}
    assert {term.word for term in evidence_terms("document", "pdf")} == {"document"}


def test_evidence_terms_whole_run_matches_tokenizes_own_camel_split() -> None:
    # The exact discipline evidence_terms must now mirror, word for word, is lexical_index_writer.tokenize's
    # own documented behaviour: "emits the whole run lowercased ... PLUS ... each sub-word lowercased too".
    for run in ("AddWatermarkAnnotation", "LaTeXDocument", "PdfDocument"):
        assert set(tokenize(run)) == {term.word for term in evidence_terms(run, "sample")} | (
            {"x"} if run == "LaTeXDocument" else set()
        )


# ---- The real reproduction, against the real baked infra/demo-manifests tex/python generation (RC-7,
# round 2): the tex family's own product name, "LaTeX", is a camelCase run that splits into "La"/"Te"/"X".
# Before the fix, evidence_terms("LaTeXDocument", "tex") (mirroring a user naming the product directly,
# e.g. "LaTeXDocument", "AsposeTeXError") lost the whole run entirely; querying the pilot's OWN real
# identifiers the same way (drawn straight from the real generation, not invented) shows the same loss on
# this exact family and the same fix. -------------------------------------------------------------------


def _real_tex_python_fqns() -> list[str]:
    pointer = json.loads((TEX_PYTHON_MANIFEST_DIR / "active_pointer.json").read_text(encoding="utf-8"))
    suffix = pointer["generation_id"].rsplit("::", 1)[-1]
    generation_file = TEX_PYTHON_MANIFEST_DIR / f"generation__{suffix}.json"
    manifest = json.loads(generation_file.read_text(encoding="utf-8"))
    documents = manifest["payload"]["lexical_index"]["documents"]
    fqns = []
    for document in documents.values():
        first_line = document["text"].split("\n", 1)[0]
        if first_line.startswith("FQN: ") and "Doc:" not in first_line and "Example:" not in first_line:
            fqns.append(first_line[len("FQN: ") :].strip().rsplit(".", 1)[-1])
    return fqns


def test_tex_python_real_baked_data_reproduction_of_the_whole_run_loss() -> None:
    # Grounds the fix in the real, committed infra/demo-manifests fixture rather than an invented name:
    # "TeXJob" and "TeXOptions" are real symbol short-names of the tex/python pilot's own active generation.
    real_fqns = _real_tex_python_fqns()
    assert "TeXJob" in real_fqns
    assert "TeXOptions" in real_fqns
    for short_name in ("TeXJob", "TeXOptions"):
        words = {term.word for term in evidence_terms(short_name, "tex")}
        # The whole run - the pilot's own real identifier, lowercased - is now a candidate term. Before this
        # card's fix it was NOT: only the sub-word fragments ("te", "job"/"options") survived, so a caller
        # that scored symbols purely by evidence_terms's candidate set (as search_symbols._safe_extra_generic
        # does over the raw query) never saw the real name as a term in its own right.
        assert short_name.lower() in words
    # "LaTeX" itself is the one real exception on THIS family: it is a registered FAMILY_ALIASES entry for
    # "tex" (a product name, not an API term - same footing as the bare family word "tex" itself, deliberately
    # excluded from being a query TERM by a separate, unrelated mechanism - see evidence_terms's own
    # docstring and FAMILY_ALIASES). Confirming it does NOT silently reappear as a spurious "kept" term
    # pins that this card's fix did not disturb that separate rescue.
    latex_words = {term.word for term in evidence_terms("LaTeX", "tex")}
    assert "latex" not in latex_words
