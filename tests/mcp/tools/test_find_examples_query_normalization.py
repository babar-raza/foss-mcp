"""G3/TC-616 (REQ-G3-011): Q3 - wire Q1's ``normalize_query`` and the ``platform``-aware term
exclusion (TC-610, built and unit-tested in isolation there) into ``find_examples`` itself.

Two independent, composable fixes are wired right after the existing ``query = fold_query(query)``
line:

1. ``normalize_query(query, scope.family)`` rewrites a literal "Aspose.<family>" dotted
   product-name mention to its two-word, space-separated form.
2. Every call in this function to ``distinctive_terms``/``query_terms`` now also passes
   ``platform=scope.platform``, so the scope's own platform/runtime word ("net", "java", ...) is
   excluded from the query's distinctive terms exactly like the family word already is.

MEASURED FINDING (reported honestly, per this card's own "report reality, do not assume the
outcome" instruction): for ``find_examples`` specifically, fix (1) is a provable NO-OP on every
scored term set and on every final ``ExampleMatch``/``NoExampleFound`` this module can produce,
for ANY query. ``lexical_index_writer.tokenize`` (used, directly or indirectly, by
``distinctive_terms``, ``query_terms``, ``required_terms``'s own regex, ``title_anchored`` and the
BM25 scorer this module calls) already splits on every non-alphanumeric character, the dot
included, so "Aspose.PDF" and "Aspose PDF" tokenize identically in every consumer this function
has - unlike ``search_symbols._plain_words`` (TC-610's own stated beneficiary), which inspects the
RAW string for a literal dot and is not called from here at all. This was verified directly: running
this file's own measurements with ``normalize_query`` stubbed to the identity function (the exact
shape of this card's own negative control) reproduces byte-identical ``ExampleMatch``/
``NoExampleFound`` results for every query below. The wiring is still correct and still required by
the card (and is defensive: it stops this function from ever regressing if its own tokenizer is ever
changed to stop splitting on punctuation) - but the dotted-mention fix's real, observable effect for
THIS entrypoint is "SAME result", the outcome this card's own input (a) explicitly allows. Fix (2),
by contrast, IS observable: threading ``platform`` drops the distinctive-term count by exactly one
on a real pilot/platform pair, and that one-term drop measurably changes ``match_basis`` and
``coverage`` through the real, wired ``find_examples`` function (see part (b) below).

Because fix (1) has no observable effect on this function's return value, the negative control that
shadows ``normalize_query`` with an identity no-op cannot be caught by pinning
``find_examples``'s output - it must be caught by proving the real module-level ``normalize_query``
is actually CALLED by the wired code (a wiring/liveness assertion, not a result assertion): the
mutation inserts ``normalize_query = lambda q, f: q`` as a LOCAL assignment inside
``find_examples``'s own body, which (by ordinary Python scoping - any assignment to a name anywhere
in a function body makes that name local for the WHOLE function) shadows the module-level import for
the entire function, so a spy installed on the module attribute is never reached. That is exactly
what ``test_normalize_query_is_actually_called_by_the_real_wiring`` below checks.

Real-pilot data only for (a)-(d): reads the real, committed ``infra/demo-manifests`` in place (never
copied, never written to). Offline and deterministic: the real embedding gateway provider is always
refused (never constructed), matching the existing find_examples test convention - no network call
is reachable, and ``network: false`` holds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import foss_mcp.mcp.tools.find_examples as find_examples_module
import foss_mcp.mcp.tools.relevance as relevance_module
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.find_examples import (
    MATCH_BASIS_QUERY_TERMS,
    MATCH_BASIS_TITLE_WORD,
    ExampleMatch,
    NoExampleFound,
    find_examples,
)
from foss_mcp.mcp.tools.relevance import distinctive_terms, fold_query, normalize_query, query_terms

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FIND_EXAMPLES_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "find_examples.py"

PDF_NET = Scope(family="pdf", platform="net")

# A real, committed pdf/net verified example (see "Add a Watermark Annotation" in
# infra/demo-manifests/pdf__net__self_extracted) - picked because its own code, title and the
# query probes below give a clean, reproducible lexical hit with no vector fallback involved.
WATERMARK_FQN = "Example: Add a Watermark Annotation"

# (a) a sentence query naming the product with a literal, dotted "Aspose.PDF" mention.
DOTTED_MENTION_QUERY = "add a watermark to a pdf using Aspose.PDF"

# (b) a short query naming the scope's own platform word ("net") alongside two real distinctive
# words from the watermark example's own title - chosen so dropping "net" crosses
# relevance.TITLE_ANCHOR_MAX_TERMS (2), which is the one place this card's fix is observable.
PLATFORM_WORD_QUERY = "watermark annotation net"

# (d) the bare platform word alone, no other content.
BARE_PLATFORM_WORD_QUERY = "net"


@pytest.fixture(autouse=True)
def _never_construct_the_real_gateway_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matches the existing find_examples test convention: the real embedding gateway is never
    constructed, so every test here is offline and deterministic (``network: false`` holds) and a
    vector-stage "miss" is always ``skipped_error``, never a real network call."""

    def _refuse(*_args, **_kwargs):
        raise RuntimeError("the real gateway embedding provider must never be constructed in this module")

    monkeypatch.setattr("foss_mcp.mcp.tools.find_examples.GatewayEmbeddingProvider", _refuse)


def _store() -> GenerationManifestStore:
    """A store reading the real, committed demo-manifests IN PLACE. Nothing is ever written."""
    return GenerationManifestStore(DEMO_MANIFESTS)


# =================================================================================================
# (a) a literal "Aspose.PDF"-style dotted mention
# =================================================================================================


def test_the_wiring_calls_normalize_query_right_after_fold_query() -> None:
    """Integration/liveness assertion on committed source (AGENTS.md: a wired call site must be
    proven to exist in the real entrypoint file, not merely that the wired function's own unit
    test still passes)."""
    source = FIND_EXAMPLES_SOURCE.read_text(encoding="utf-8")
    fold_line = "query = fold_query(query)"
    normalize_line = "query = normalize_query(query, scope.family)"
    assert source.count(fold_line) == 1
    assert normalize_line in source
    # normalize_query must run AFTER fold_query, not before and not replacing it.
    assert source.index(fold_line) < source.index(normalize_line)


def test_normalize_query_is_actually_called_by_the_real_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE test the card's negative control must break: proves ``find_examples`` really calls the
    module-level ``normalize_query`` (not merely that it is imported) by spying on it. This is the
    one assertion in this file that is sensitive to the negative control's exact mutation (a LOCAL
    ``normalize_query = lambda q, f: q`` assignment inside the function body, which shadows the
    module-level name for the whole function and so is never reached by a spy on the module
    attribute)."""
    calls: list[tuple[str, str]] = []
    real_normalize_query = relevance_module.normalize_query

    def _spy(query: str, family: str) -> str:
        calls.append((query, family))
        return real_normalize_query(query, family)

    monkeypatch.setattr(find_examples_module, "normalize_query", _spy)

    find_examples(_store(), PDF_NET, DOTTED_MENTION_QUERY)

    assert calls, "normalize_query was never called by find_examples - the wiring is missing or shadowed"
    # fold_query is a no-op on this plain-ASCII query, so the string reaching normalize_query is the
    # query itself, with the family taken from scope.family.
    assert calls[0] == (fold_query(DOTTED_MENTION_QUERY), "pdf")


def test_normalize_query_removes_the_literal_dotted_mention_before_scoring() -> None:
    """The query string that reaches every downstream consumer (``distinctive_terms``,
    ``query_terms``, the BM25 scorer, ``title_anchored``) no longer contains the literal dotted
    substring "Aspose.PDF" once normalized - the old code (the ``fold_query``-only reference call
    below, bypassing the new ``normalize_query`` line) kept it intact."""
    old_reference = fold_query(DOTTED_MENTION_QUERY)  # what find_examples handed downstream BEFORE this card
    new_wired = normalize_query(old_reference, "pdf")  # what find_examples hands downstream AFTER this card

    assert "Aspose.PDF" in old_reference
    assert "Aspose.PDF" not in new_wired
    assert "Aspose PDF" in new_wired


def test_the_term_set_this_tool_scores_with_is_measurably_unchanged_by_the_dotted_mention() -> None:
    """MEASURED, not assumed: ``find_examples``'s own tokenizer already splits "Aspose.PDF" into
    "aspose" + "pdf" regardless of the dot, so the distinctive/query term SETS computed from the
    pre-normalization (dotted) and post-normalization (de-dotted) strings are identical for this
    tool. This is the honest "SAME result" outcome input (a) explicitly permits - documented
    concretely here rather than assumed, since the card's own framing ("the dotted mention no
    longer stays a single dotted token for this tool's own term machinery") describes
    ``search_symbols._plain_words`` (TC-610's named beneficiary), not this module."""
    old_reference = fold_query(DOTTED_MENTION_QUERY)
    new_wired = normalize_query(old_reference, "pdf")
    assert old_reference != new_wired  # the literal strings genuinely differ ...

    assert distinctive_terms(old_reference, "pdf", "net") == distinctive_terms(new_wired, "pdf", "net")
    assert query_terms(old_reference, "pdf", "net") == query_terms(new_wired, "pdf", "net")


def test_find_examples_returns_the_real_pilots_watermark_example_for_the_dotted_mention_query() -> None:
    """Real-pilot proof, end to end through the wired function: a dotted "Aspose.PDF" mention in a
    natural-language query still reaches and matches the real, committed pdf/net watermark
    example - at least as good a result as before this card (the measured, honest outcome here is
    byte-identical to before, per the finding above: SAME result, not a regression)."""
    result = find_examples(_store(), PDF_NET, DOTTED_MENTION_QUERY)

    assert isinstance(result, list) and len(result) == 1
    match = result[0]
    assert isinstance(match, ExampleMatch)
    assert match.fqn == WATERMARK_FQN
    assert match.source == "lexical"
    assert match.coverage == pytest.approx(1.0)
    assert match.match_basis == MATCH_BASIS_QUERY_TERMS


def test_find_examples_result_is_unchanged_whether_or_not_the_query_is_pre_normalized() -> None:
    """The dotted and an equivalent already-non-dotted query give the identical match - proving the
    fix is inert here, concretely, rather than merely asserting a fixed shape."""
    dotted_result = find_examples(_store(), PDF_NET, DOTTED_MENTION_QUERY)
    predotted_result = find_examples(_store(), PDF_NET, "add a watermark to a pdf using Aspose PDF")

    assert dotted_result == predotted_result


# =================================================================================================
# (b) the scope's own platform word is excluded from the distinctive-term count
# =================================================================================================


def test_platform_exclusion_drops_the_distinctive_term_count_by_exactly_one_on_a_real_pilot() -> None:
    """Mirrors TC-610's own LONGQ-001 measurement methodology, through the SAME module-qualified
    functions ``find_examples`` itself now calls (imported, not re-implemented), against this
    card's real pdf/net pilot/platform pair."""
    without_platform = find_examples_module.distinctive_terms(PLATFORM_WORD_QUERY, PDF_NET.family)
    with_platform = find_examples_module.distinctive_terms(
        PLATFORM_WORD_QUERY, PDF_NET.family, platform=PDF_NET.platform
    )

    assert "net" in without_platform
    assert "net" not in with_platform
    assert len(without_platform) - len(with_platform) == 1
    assert without_platform - with_platform == frozenset({"net"})


def test_the_longq_001_sentence_also_drops_by_one_through_the_wired_module() -> None:
    """The exact LONGQ-001 sentence (F5/AU3's own reproduction), proving ``find_examples.py``'s
    imported ``distinctive_terms``/``query_terms`` are the SAME functions TC-610 already pinned -
    not a duplicate or a diverged copy."""
    longq_001 = (
        "how do I install and reference the Aspose.PDF FOSS library in my .NET project "
        "so I can start converting files"
    )

    without_platform = find_examples_module.distinctive_terms(longq_001, "pdf")
    with_platform = find_examples_module.distinctive_terms(longq_001, "pdf", platform="net")

    assert len(without_platform) == 7
    assert len(with_platform) == 6
    assert without_platform - with_platform == frozenset({"net"})
    assert "net" not in find_examples_module.query_terms(longq_001, "pdf", platform="net")


def test_platform_exclusion_changes_match_basis_and_coverage_through_the_real_wired_function() -> None:
    """The one-term drop measured above is not academic: crossing
    ``relevance.TITLE_ANCHOR_MAX_TERMS`` (2) changes which rule ``find_examples`` actually applies
    to this real query against the real pilot, through the wired code path end to end (no
    monkeypatching of any term function here - this calls the real, wired ``find_examples``)."""
    result = find_examples(_store(), PDF_NET, PLATFORM_WORD_QUERY)

    assert isinstance(result, list) and len(result) == 1
    match = result[0]
    assert match.fqn == WATERMARK_FQN
    # Before this card: 3 distinctive terms ("annot", "net", "watermark"), > TITLE_ANCHOR_MAX_TERMS,
    # so the coverage-floor-only rule applied: match_basis="query_terms_in_example_text",
    # coverage=0.666... (2 of 3 terms present in the example text). Measured directly against the
    # unpatched code before writing this test (git-stashed this card's own diff and re-ran the same
    # query): NoExampleFound was NOT returned; the match's own match_basis and coverage were exactly
    # those two unpatched values. After this card: 2 distinctive terms ("annot", "watermark"),
    # <= TITLE_ANCHOR_MAX_TERMS, so the title-anchor rule applies instead.
    assert match.match_basis == MATCH_BASIS_TITLE_WORD
    assert match.coverage == pytest.approx(1.0)


# =================================================================================================
# (d) a query that is only the bare platform word, with no other distinctive content
# =================================================================================================


def test_bare_platform_word_has_no_distinctive_terms_once_the_platform_is_excluded() -> None:
    assert distinctive_terms(BARE_PLATFORM_WORD_QUERY, "pdf") == frozenset({"net"})  # the old, 2-arg form
    assert distinctive_terms(BARE_PLATFORM_WORD_QUERY, "pdf", "net") == frozenset()  # this card's fix


def test_find_examples_gives_an_explicit_miss_for_the_bare_platform_word_query() -> None:
    """REAL MEASURED BEHAVIOUR (reported, not assumed): the bare platform word now trips the
    existing "no distinctive terms" early-return in ``find_examples`` (relevance.py's own
    ``distinctive_terms(query, scope.family, platform=scope.platform)`` call at the top of the
    lexical stage), returning a clean, explicit ``NoExampleFound`` that never even reaches the
    vector-fallback stage (``vector_fallback`` is ``None``, not a skip status).

    Measured contrast with the code before this card (git-stashed this card's own diff and re-ran
    the identical query): the unpatched ``find_examples`` did NOT early-return here (its own
    2-argument ``distinctive_terms("net", "pdf")`` is NOT empty - the platform word itself WAS,
    before this card, treated as the query's one distinctive term), so it fell through the whole
    lexical stage (finding nothing) and then into the vector fallback, which - with the real
    gateway provider refused by this file's own autouse fixture - degraded to
    ``vector_fallback="skipped_error"`` and a noisier reason string
    ("... (semantic fallback skipped: error)"). This card's fix is therefore STRICTLY BETTER for
    this one case too: an honest, immediate miss instead of a miss reached only after an
    unreachable semantic stage was asked and failed.
    """
    result = find_examples(_store(), PDF_NET, BARE_PLATFORM_WORD_QUERY)

    assert isinstance(result, NoExampleFound)
    assert result.found is False
    assert result.query == BARE_PLATFORM_WORD_QUERY
    assert result.reason == "no verified example matches this query"
    assert result.vector_fallback is None
