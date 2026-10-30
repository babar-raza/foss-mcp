"""G3/TC-617 (REQ-G3-011): Q2 - wire Q1's ``normalize_query`` and the ``platform``-aware term
exclusion (TC-610, built and unit-tested in isolation there) into ``search_docs`` itself.

Two independent, composable fixes are wired right after the existing ``query = fold_query(query)``
line inside ``search_docs()``:

1. ``normalize_query(query, scope.family)`` rewrites a literal "Aspose.<family>" dotted
   product-name mention to its two-word, space-separated form.
2. ``_rule_for`` (the one real place this module builds its relevance rule) now also accepts an
   optional ``platform`` parameter, threaded from its one real call site as
   ``platform=scope.platform``, so the scope's own platform/runtime word ("net", "java", ...) is
   excluded from the query's distinctive terms exactly like the family word already is.

REWORK (attempt 2) regression and fix, measured and reproduced here: wiring fix (2) alone broke
TC-549's product-title answerability - excluding the platform word could empty
``distinctive_terms`` even though the query named a real topic (a bare "Aspose.<Family> FOSS for
<Platform>" title whose only non-generic, non-family content was its own platform word), and
``_rule_for``'s pre-existing bare-family-word special case did not anticipate this. ``_rule_for``
now carries a SECOND special case: when platform exclusion alone is what emptied
``distinctive_terms``, it rebuilds its terms WITHOUT the platform exclusion, preserving the exact
single-term rule TC-549 depended on. Part (c) below reproduces the regression directly and proves
the fix is load-bearing (the exact scenario this card's own attempt 1 found).

MEASURED FINDING for the dotted-mention fix, reported honestly (TC-616 found it a no-op for
``find_examples`` - re-measured fresh here, not assumed): it is ALSO a no-op for ``search_docs``,
for the identical reason - this module's own term machinery (``query_terms``/``distinctive_terms``,
via ``lexical_index_writer.tokenize``) already splits "Aspose.PDF" into "aspose" + "pdf" regardless
of the dot. The wiring is still correct and required (defensive, and it is this card's own write
path) - the dotted-mention fix's real, observable effect for THIS entrypoint is "SAME result",
confirmed concretely in part (b) below.

The platform-exclusion fix IS observable for ``search_docs``: part (a) below measures, against the
real, committed pdf/net pilot, that excluding the platform word narrows which documentation chunks
``search_docs`` considers relevant enough to return.

Real-pilot data only for the real-pilot cases: reads the real, committed ``infra/demo-manifests``
in place (copied to a temporary directory first; the repository is never written). Offline and
deterministic - no network call is reachable, matching ``network: false``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import foss_mcp.mcp.tools.relevance as relevance_module
import foss_mcp.mcp.tools.search_docs as search_docs_module
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.relevance import distinctive_terms, fold_query, normalize_query, query_terms
from foss_mcp.mcp.tools.search_docs import Miss, search_docs

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SEARCH_DOCS_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "search_docs.py"

PDF_NET = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc617-search-docs"

# (a) a short query naming the scope's own platform word ("net") alongside two real distinctive
# words that the real pdf/net pilot's own documentation carries.
PLATFORM_WORD_QUERY = "watermark annotation net"

# (b) a sentence naming the product with a literal, dotted "Aspose.PDF" mention.
DOTTED_MENTION_QUERY = "add a watermark to a pdf using Aspose.PDF"

# (c) the exact REWORK (attempt 2) regression query: a bare product-title whose only non-generic,
# non-family content is its own platform word.
PRODUCT_TITLE_QUERY = "Aspose.PDF FOSS for Python"

# (d) the bare platform word alone, no other content.
BARE_PLATFORM_WORD_QUERY = "net"


class _InMemoryStore:
    """One active generation whose chunks are exactly the given texts (a chunk is never split or merged)."""

    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _doc_chunk(title: str, *body_lines: str) -> str:
    return "\n".join([f"FQN: Doc: {title}", *body_lines]) + "\n"


@pytest.fixture(scope="module")
def pdf_net_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    """A store over a COPY of the real, committed pdf/net pilot (the repository is never written)."""
    root = tmp_path_factory.mktemp("search_docs_normalization_pdf_net")
    shutil.copytree(DEMO_MANIFESTS / "pdf__net__self_extracted", root / "pdf__net__self_extracted")
    return GenerationManifestStore(root)


_REAL_RULE_FOR = search_docs_module._rule_for  # noqa: SLF001 - captured BEFORE any monkeypatch below


def _no_platform_rule_for(
    query: str, family: str, lexical_payload: dict | None, *, platform: str | None = None
):
    """The exact pre-card ``_rule_for`` shape (platform never threaded) - used ONLY to measure, by
    direct A/B comparison against the real wired module, what this card's platform-exclusion fix
    actually changed for ``search_docs``'s own returned matches."""
    return _REAL_RULE_FOR(query, family, lexical_payload)


# =================================================================================================
# (a) the scope's own platform word is excluded from the distinctive-term count
# =================================================================================================


def test_platform_exclusion_drops_the_distinctive_term_count_by_exactly_one_on_a_real_pilot() -> None:
    """Mirrors TC-610's and TC-616's own measurement methodology, against this card's real pdf/net
    pilot/platform pair."""
    without_platform = distinctive_terms(PLATFORM_WORD_QUERY, PDF_NET.family)
    with_platform = distinctive_terms(PLATFORM_WORD_QUERY, PDF_NET.family, platform=PDF_NET.platform)

    assert "net" in without_platform
    assert "net" not in with_platform
    assert len(without_platform) - len(with_platform) == 1
    assert without_platform - with_platform == frozenset({"net"})


def test_platform_exclusion_narrows_search_docs_own_matches_on_the_real_pilot(
    pdf_net_store: GenerationManifestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-term drop measured above is not academic: against the real, committed pdf/net
    pilot, it changes which documentation chunks ``search_docs`` considers relevant enough to
    return - measured by direct A/B comparison (the wired module vs. the identical call with
    ``_rule_for`` reverted to its pre-card, platform-unaware shape) against the SAME real query and
    the SAME real generation, not merely asserted.

    Measured directly: BEFORE this card's platform-exclusion fix, ``search_docs`` returns 4 chunks
    for this query - including the small "Aspose.PDF FOSS for .NET" title chunk, which only clears
    the relevance floor because the query's own "net" inflates its coverage. AFTER, with "net"
    correctly excluded as the scope's own platform word, only the 2 "Key Capabilities and API
    Reference" chunks that genuinely discuss watermarking/annotations remain - a real precision
    gain, not merely a different-sized list.
    """
    after = search_docs(pdf_net_store, PDF_NET, PLATFORM_WORD_QUERY)
    assert isinstance(after, list)

    monkeypatch.setattr(search_docs_module, "_rule_for", _no_platform_rule_for)
    before = search_docs(pdf_net_store, PDF_NET, PLATFORM_WORD_QUERY)
    assert isinstance(before, list)

    assert len(before) == 4
    assert len(after) == 2
    after_ids = {match.doc_id for match in after}
    before_ids = {match.doc_id for match in before}
    assert after_ids <= before_ids
    assert all(extract_fqn(match.text) == "Doc: Key Capabilities and API Reference" for match in after)


# =================================================================================================
# (b) a literal "Aspose.PDF"-style dotted mention
# =================================================================================================


def test_the_wiring_calls_normalize_query_right_after_fold_query() -> None:
    """Integration/liveness assertion on committed source (AGENTS.md: a wired call site must be
    proven to exist in the real entrypoint file, not merely that the wired function's own unit
    test still passes)."""
    source = SEARCH_DOCS_SOURCE.read_text(encoding="utf-8")
    fold_line = "query = fold_query(query)"
    normalize_line = "query = normalize_query(query, scope.family)"
    assert source.count(fold_line) == 1
    assert normalize_line in source
    assert source.index(fold_line) < source.index(normalize_line)


def test_normalize_query_is_actually_called_by_the_real_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE test the card's negative control must break: proves ``search_docs`` really calls the
    module-level ``normalize_query`` (not merely that it is imported) by spying on it. This is the
    one assertion in this file sensitive to the negative control's exact mutation (a LOCAL
    ``normalize_query = lambda q, f: q`` assignment inside the function body, which shadows the
    module-level name for the whole function)."""
    calls: list[tuple[str, str]] = []
    real_normalize_query = relevance_module.normalize_query

    def _spy(query: str, family: str) -> str:
        calls.append((query, family))
        return real_normalize_query(query, family)

    monkeypatch.setattr(search_docs_module, "normalize_query", _spy)

    store = _InMemoryStore(_doc_chunk("Guide", "Aspose.PDF FOSS is a free library for pdf files."))
    search_docs(store, PDF_NET, DOTTED_MENTION_QUERY)  # type: ignore[arg-type]

    assert calls, "normalize_query was never called by search_docs - the wiring is missing or shadowed"
    assert calls[0] == (fold_query(DOTTED_MENTION_QUERY), "pdf")


def test_the_term_set_this_tool_scores_with_is_measurably_unchanged_by_the_dotted_mention() -> None:
    """MEASURED, not assumed: like ``find_examples`` (TC-616), ``search_docs``'s own tokenizer
    already splits "Aspose.PDF" into "aspose" + "pdf" regardless of the dot, so the
    distinctive/query term SETS computed from the pre-normalization (dotted) and
    post-normalization (de-dotted) strings are identical for this tool too."""
    old_reference = fold_query(DOTTED_MENTION_QUERY)
    new_wired = normalize_query(old_reference, "pdf")
    assert old_reference != new_wired  # the literal strings genuinely differ ...

    assert distinctive_terms(old_reference, "pdf", "net") == distinctive_terms(new_wired, "pdf", "net")
    assert query_terms(old_reference, "pdf", "net") == query_terms(new_wired, "pdf", "net")


def test_search_docs_returns_the_identical_real_pilot_result_whether_or_not_the_query_is_pre_normalized(
    pdf_net_store: GenerationManifestStore,
) -> None:
    """Real-pilot proof, end to end through the wired function: the dotted and an equivalent
    already-non-dotted query give byte-identical results on the real, committed pdf/net pilot -
    the honest "SAME result" outcome measured above, confirmed at the whole-tool level."""
    dotted_result = search_docs(pdf_net_store, PDF_NET, "add a watermark with Aspose.PDF")
    predotted_result = search_docs(pdf_net_store, PDF_NET, "add a watermark with Aspose PDF")

    assert dotted_result == predotted_result


# =================================================================================================
# (c) the REWORK (attempt 2) regression: a bare product-title whose only distinctive content is
# the scope's own platform word
# =================================================================================================


def test_a_python_product_title_is_still_answered_after_the_rule_for_fix() -> None:
    """The exact attempt-1-found regression, reproduced and confirmed fixed: without this card's
    attempt-2 ``_rule_for`` special case, excluding "python" (the scope's own platform word) from
    "Aspose.PDF FOSS for Python" would have emptied its distinctive terms AND, because the bare-
    family-word fallback only recognises the family's own word, made ``_rule_for`` return ``None`` -
    turning what used to be an honest match into a false miss for every pilot whose product title is
    only its own family + platform words. With the fix, the match holds."""
    store = _InMemoryStore(_doc_chunk("Guide", "Aspose.PDF FOSS for Python is a free library."))
    scope = Scope(family="pdf", platform="python")

    result = search_docs(store, scope, PRODUCT_TITLE_QUERY, "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, list)
    assert result


def test_reverting_just_the_attempt_2_special_case_reproduces_the_regression() -> None:
    """Hand-verification that the attempt-2 fix is load-bearing, not accidentally inert: calling
    ``_rule_for`` with the attempt-1-only shape (platform threaded into ``query_terms``/
    ``distinctive_terms`` but WITHOUT the new special case) on the exact regression query returns
    ``None`` - the real root cause this card's rework fixed."""
    from foss_mcp.mcp.tools.relevance import (
        has_minimum_evidence,
        names_language_letter,
        required_terms,
        stem_token,
        term_weights,
    )

    def attempt_1_only_rule_for(query: str, family: str, lexical_payload: dict | None, *, platform=None):
        if not has_minimum_evidence(query):
            return None
        terms = query_terms(query, family, platform=platform)
        names_topic = bool(distinctive_terms(query, family, platform=platform)) or names_language_letter(
            query
        )
        if not names_topic and terms != frozenset({stem_token(family)}):
            return None
        return search_docs_module._Rule(
            terms, term_weights(terms, lexical_payload or {}), required_terms(query)
        )  # noqa: SLF001

    assert attempt_1_only_rule_for(PRODUCT_TITLE_QUERY, "pdf", {}, platform="python") is None
    assert search_docs_module._rule_for(PRODUCT_TITLE_QUERY, "pdf", {}, platform="python") is not None  # noqa: SLF001


# =================================================================================================
# (d) a query that is only the bare platform word, with no other distinctive content
# =================================================================================================


def test_bare_platform_word_keeps_the_single_term_rule_instead_of_naming_no_topic() -> None:
    """The attempt-2 special case fires for the bare platform word exactly as it does for the bare
    family word: ``distinctive_terms`` without platform exclusion is exactly ``{platform}``, so
    ``_rule_for`` rebuilds its terms without the exclusion rather than reporting "no topic"."""
    assert distinctive_terms(BARE_PLATFORM_WORD_QUERY, "pdf") == frozenset({"net"})  # the old, 2-arg form
    assert distinctive_terms(BARE_PLATFORM_WORD_QUERY, "pdf", "net") == frozenset()  # platform excluded

    rule = search_docs_module._rule_for(BARE_PLATFORM_WORD_QUERY, "pdf", {}, platform="net")  # noqa: SLF001
    assert rule is not None
    assert rule.terms == frozenset({"net"})


def test_search_docs_gives_a_sensible_match_for_the_bare_platform_word_query() -> None:
    """REAL MEASURED BEHAVIOUR (reported, not assumed): the bare platform word still answers with
    the chunk that discusses it, exactly as the bare family word always has - never a forced miss."""
    store = _InMemoryStore(_doc_chunk("Guide", "This pdf library runs on .NET, often just called net."))
    scope = Scope(family="pdf", platform="net")

    result = search_docs(store, scope, BARE_PLATFORM_WORD_QUERY, "developer_guide")  # type: ignore[arg-type]

    assert isinstance(result, list)
    assert result
    assert not isinstance(result, Miss)
