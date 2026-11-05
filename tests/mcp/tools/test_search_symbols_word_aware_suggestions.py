"""Word-aware "did you mean" suggestions (TC-675, G3/REQ-G3-011, Q14).

``suggest_similar_fqns`` (``search_symbols.py``) is the ONE shared implementation both
``search_symbols`` and ``get_symbol`` already draw their ``suggestions``/``NotFound.suggestions``
from (confirmed at ``get_symbol.py``'s own import block and its two call sites, ~line 416/460 - see
this card's own worker report). Before this card it had two additive, purely character-sequence
tiers (TC-386: full-FQN at cutoff 0.6, then final-segment at cutoff 0.75) and could not recognise a
compound identifier built from the same words in a different order as a near-match. This module adds
a THIRD, additive, word-SET-based tier, tried only when both of those return empty, reusing
``lexical_index_writer.tokenize()`` (never a new splitter).

REAL, MEASURED LIMITATION (see ``search_symbols.py``'s own module docstring, TC-675 paragraph, for
the full account): ``tokenize()`` always also emits the WHOLE alphanumeric run as its own token, and
that whole-run token differs by concatenation order between two orderings of a NO-separator
camelCase/PascalCase compound (``"fragmenttext"`` vs ``"textfragment"``), so a pure camelCase
word-order swap with no separator between the swapped words - including this card's own two cited
motivating examples, ``"FragmentText"`` vs a real ``TextFragment`` and ``"CollectionPage"`` vs a real
``PageCollection`` - is NOT caught by this exact, verbatim-required tier shape. A word-order swap of a
SEPARATOR-joined compound (an underscore, or any other boundary ``tokenize()``'s own ``_TOKEN_RE``
already splits on) IS caught, because the separator keeps ``tokenize()`` from ever emitting a single
merged whole-run token in the first place. This module's own tests below prove the tier against a
separator-joined fixture (which it genuinely fixes) and separately PIN the no-separator limitation
(which it does not), rather than silently assuming either.

Offline and deterministic throughout: in-memory generations built from hand-written type chunks, the
same pattern ``test_search_symbols_prefix_tier.py`` and ``test_member_lookup.py`` already established.
"""

from __future__ import annotations

from types import SimpleNamespace

from foss_mcp.indexing.lexical_index_writer import build_lexical_index, tokenize
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import NotFound, get_symbol
from foss_mcp.mcp.tools.search_symbols import _word_aware_suggestions, _word_set, suggest_similar_fqns

SCOPE = Scope(family="sample", platform="python")
_GENERATION_ID = "gen-tc675-word-aware"


def _type_chunk(fqn: str, *methods: str) -> str:
    lines = [f"FQN: {fqn}", "Kind: class_declaration"]
    if methods:
        lines.append("Methods:")
        lines.extend(f"  - {method}" for method in methods)
    return "\n".join(lines) + "\n"


class _InMemoryStore:
    """Stands in for GenerationManifestStore: one active generation, held entirely in memory - the
    same pattern ``test_search_symbols_prefix_tier.py`` already established."""

    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


# ---------------------------------------------------------------------------------------------------
# REPRODUCE FIRST: the card's own cited motivating example, against the real function.
# ---------------------------------------------------------------------------------------------------

_PDF_DOC_LIST = [
    "Aspose.Pdf_FOSS.TextFragment",
    "Aspose.Pdf_FOSS.Document",
    "Aspose.Cells_FOSS.FormatCondition",
]


def test_the_cards_own_cited_example_is_an_honest_miss_with_or_without_this_fix() -> None:
    """Pins the REAL, MEASURED limitation this card's own worker report discloses: a pure camelCase
    word-order swap with NO separator between the swapped words (``"FragmentText"`` vs the real
    ``TextFragment``) is not caught by the exact, verbatim-required tier shape this card adds, because
    ``tokenize()`` itself always also emits a whole-run token that differs by concatenation order
    between the two orderings. This is NOT a regression introduced by this card - difflib's own
    character-sequence tiers already returned (), and the new word-set tier, by the card's own exact
    specification, cannot fix it either. Proven directly against the real function, not asserted from
    memory: if ``tokenize()``'s own behaviour ever changes to stop emitting the whole-run token, this
    test (not a changed production assertion) is what will catch the drift.
    """
    assert _word_set("FragmentText") != _word_set("TextFragment")
    assert suggest_similar_fqns(_PDF_DOC_LIST, "FragmentText") == ()
    assert suggest_similar_fqns(_PDF_DOC_LIST, "CollectionPage") == ()


# ---------------------------------------------------------------------------------------------------
# (a) a real word-order-swap case the two prior tiers both miss now returns the real expected FQN.
# ---------------------------------------------------------------------------------------------------

_SEPARATOR_JOINED_LIST = [
    "Aspose.Cells_FOSS.Format_Condition",
    "Aspose.Pdf_FOSS.Document",
    # A decoy sharing the same two words with NO separator: proves the match is WORD-SET equality,
    # not a looser containment check that a merged compound would also satisfy.
    "Aspose.Cells_FOSS.FormatCondition",
]


def test_a_separator_joined_word_order_swap_neither_prior_tier_catches_is_now_found() -> None:
    result = suggest_similar_fqns(_SEPARATOR_JOINED_LIST, "Condition_Format")

    assert result == ("Aspose.Cells_FOSS.Format_Condition",)
    # The merged-compound decoy (same two words, no separator) is correctly excluded: EQUALITY of
    # word sets is required, not mere overlap.
    assert "Aspose.Cells_FOSS.FormatCondition" not in result


def test_the_new_tier_is_reached_only_once_both_prior_tiers_are_genuinely_empty() -> None:
    # Direct evidence the fixture above exercises the NEW tier, not an accidental difflib hit.
    known = [fqn for fqn in _SEPARATOR_JOINED_LIST]
    from difflib import get_close_matches

    assert get_close_matches("Condition_Format", known, n=3, cutoff=0.6) == []
    segments = ["Format_Condition", "Document", "FormatCondition"]
    assert get_close_matches("Condition_Format", segments, n=3, cutoff=0.75) == []


# ---------------------------------------------------------------------------------------------------
# (b) a genuine character typo is unchanged - same result, same (existing) tier as before.
# ---------------------------------------------------------------------------------------------------

_WORKBOOK_LIST = ["Aspose.Cells_FOSS.Workbook", "Aspose.Pdf_FOSS.Document"]


def test_a_genuine_character_typo_is_unchanged_through_the_same_existing_tier() -> None:
    # "Workbok" is a genuine character-level typo of "Workbook" (TC-386's own worked example) -
    # caught by the EXISTING final-segment tier (cutoff 0.75), not the new word-aware one: the two
    # words' own token sets are not equal to begin with ("workbok" tokenizes to nothing resembling
    # "workbook" as a distinct word), so this result is unaffected by this card's change.
    assert _word_set("Workbok") != _word_set("Workbook")

    result = suggest_similar_fqns(_WORKBOOK_LIST, "Workbok")

    assert result == ("Aspose.Cells_FOSS.Workbook",)


# ---------------------------------------------------------------------------------------------------
# (c) a target whose word set equals no known FQN's final-segment word set stays an honest miss.
# ---------------------------------------------------------------------------------------------------


def test_a_target_sharing_no_word_set_with_any_known_fqn_stays_a_miss() -> None:
    known = ["Aspose.Pdf_FOSS.Document", "Aspose.Cells_FOSS.Workbook"]

    result = suggest_similar_fqns(known, "Zephyrine_Quartz")

    assert result == ()


def test_the_match_is_exact_equality_not_mere_overlap() -> None:
    # "Format_Condition_Extra" shares both words of "Condition_Format" but has a third word too -
    # overlap alone would wrongly admit it; exact set equality correctly rejects it.
    known = ["Aspose.Cells_FOSS.Format_Condition_Extra"]

    result = suggest_similar_fqns(known, "Condition_Format")

    assert result == ()
    assert _word_aware_suggestions(known, "Condition_Format", limit=3) == ()


# ---------------------------------------------------------------------------------------------------
# (d) get_symbol's own NotFound.suggestions surfaces a word-order-swap suggestion too, through the
# shared implementation, without touching get_symbol.py's own source.
# ---------------------------------------------------------------------------------------------------


def test_get_symbol_notfound_suggestions_surfaces_a_word_order_swap_through_the_shared_function() -> None:
    store = _InMemoryStore(
        _type_chunk("Aspose.Cells_FOSS.Format_Condition"),
        _type_chunk("Aspose.Pdf_FOSS.Document"),
    )

    result = get_symbol(store, SCOPE, "Condition_Format")  # type: ignore[arg-type]

    assert isinstance(result, NotFound)
    assert result.found is False
    assert result.suggestions == ("Aspose.Cells_FOSS.Format_Condition",)
    # Exactly the shared function's own answer for the identical inputs - proves the benefit flows
    # through the one shared implementation, not a separate get_symbol-only mechanism.
    known_fqns = ["Aspose.Cells_FOSS.Format_Condition", "Aspose.Pdf_FOSS.Document"]
    assert result.suggestions == suggest_similar_fqns(known_fqns, "Condition_Format")


def test_get_symbol_still_reports_todays_honest_miss_reason_unchanged() -> None:
    store = _InMemoryStore(_type_chunk("Aspose.Cells_FOSS.Format_Condition"))

    result = get_symbol(store, SCOPE, "Condition_Format")  # type: ignore[arg-type]

    assert isinstance(result, NotFound)
    assert result.reason == "no symbol with this exact FQN is in this index"


# ---------------------------------------------------------------------------------------------------
# (e) a target with no eligible final-segment words never crashes and returns ().
# ---------------------------------------------------------------------------------------------------


def test_a_single_character_target_has_no_eligible_words_and_returns_no_suggestions() -> None:
    assert tokenize("I") == []  # confirms the fixture actually has zero eligible words

    result = suggest_similar_fqns(["Aspose.Pdf_FOSS.Document"], "I")

    assert result == ()


def test_a_stopword_only_target_has_no_eligible_words_and_returns_no_suggestions() -> None:
    assert tokenize("The_Of") == []  # both words are stopwords - confirms zero eligible words

    result = suggest_similar_fqns(["Aspose.Pdf_FOSS.Document"], "The_Of")

    assert result == ()


def test_the_word_aware_helper_itself_never_crashes_on_an_empty_word_set() -> None:
    assert _word_aware_suggestions(["Aspose.Pdf_FOSS.Document"], "I", limit=3) == ()
    assert _word_aware_suggestions([], "AnythingAtAll", limit=3) == ()
