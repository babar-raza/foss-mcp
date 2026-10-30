"""G3/TC-617 (REQ-G3-011): Q2 - wire Q1's ``normalize_query`` and the ``platform``-aware term
exclusion (TC-610, built and unit-tested in isolation there) into ``lookup`` itself.

Two independent, composable fixes are wired right after the existing ``query = fold_query(query)``
line inside ``lookup()``:

1. ``normalize_query(query, scope.family)`` rewrites a literal "Aspose.<family>" dotted
   product-name mention to its two-word, space-separated form.
2. Every helper this function calls that computes a relevance rule or a distinctive-term set
   (``_limitation_docs``, ``_answer_quality`` and the shared ``search_docs._rule_for``, and
   ``_without_unsupported_tables``) now also accepts and threads an optional ``platform``
   parameter from ``scope.platform``, so the scope's own platform/runtime word ("net", "java", ...)
   is excluded from the query's distinctive terms exactly like the family word already is.

REWORK (attempt 2) regression and fix: fix (2) alone broke TC-549's product-title answerability -
reproduced here through ``lookup`` too, since ``lookup``'s own doc-composition path calls
``search_docs`` directly. Part (c) below proves ``lookup`` was equally broken by the attempt-1-only
wiring and that this card's ``_rule_for`` fix (committed in ``search_docs.py``, this card's shared
write path) repairs it here as well.

MEASURED FINDING for the dotted-mention fix, reported honestly (TC-616 found it a no-op for
``find_examples``; this card's own ``search_docs`` file finds it a no-op there too - re-measured
fresh here, NOT assumed to carry over): for ``lookup`` specifically, the dotted-mention fix is
``NOT`` a no-op. Unlike ``search_docs``/``find_examples``, ``lookup`` dispatches to
``search_symbols`` with the SAME query string its own wiring normalizes - and
``search_symbols._evidence_ranking`` gates its whole evidence stage on
``search_symbols._plain_words``, which refuses (returns ``None``) the instant any word of the query
contains a literal dot. Before this card, a literal "Aspose.PDF" mention in a task sentence
disabled ``lookup``'s symbol-evidence stage outright; after, with the dot rewritten to a space, the
evidence stage runs normally. Part (b) below measures this concretely, both at the
``search_symbols`` level and through the whole, real, wired ``lookup`` function.

Real-pilot data only for the real-pilot cases: reads the real, committed ``infra/demo-manifests``
in place (copied to a temporary directory first; the repository is never written). Offline and
deterministic: the real embedding gateway provider is always refused (never constructed, matching
the existing lookup/find_examples test convention) - no network call is reachable, matching
``network: false``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import foss_mcp.mcp.tools.find_examples as find_examples_module
import foss_mcp.mcp.tools.lookup as lookup_module
import foss_mcp.mcp.tools.relevance as relevance_module
import foss_mcp.mcp.tools.search_docs as search_docs_module
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.relevance import distinctive_terms, fold_query, normalize_query
from foss_mcp.mcp.tools.search_symbols import Miss, search_symbols

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
LOOKUP_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "lookup.py"

PDF_NET = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc617-lookup"

# (a) a short query naming the scope's own platform word ("net") alongside two real distinctive
# words the real pdf/net pilot's own documentation carries - the same query search_docs's own
# TC-617 test file measures, reused here to show the SAME narrowing surfaces through lookup's own
# composed TaskAnswer.
PLATFORM_WORD_QUERY = "watermark annotation net"

# (b) a sentence naming the product with a literal, dotted "Aspose.PDF" mention, phrased so it
# names no symbol by a non-dotted path and so genuinely depends on the evidence stage.
DOTTED_MENTION_QUERY = "add a watermark with Aspose.PDF"

# (c) the exact REWORK (attempt 2) regression query: a bare product-title whose only non-generic,
# non-family content is its own platform word.
PRODUCT_TITLE_QUERY = "Aspose.PDF FOSS for Python"

# (d) the bare platform word alone, no other content.
BARE_PLATFORM_WORD_QUERY = "net"


class _NoGatewayEmbeddingProvider:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)


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
    root = tmp_path_factory.mktemp("lookup_normalization_pdf_net")
    shutil.copytree(DEMO_MANIFESTS / "pdf__net__self_extracted", root / "pdf__net__self_extracted")
    return GenerationManifestStore(root)


def _symbol_fqns(answer: TaskAnswer) -> list[str | None]:
    return [extract_fqn(match.text) for match in answer.symbols]


# Captured BEFORE any monkeypatch below, so the "simulate the pre-card wiring" helpers never
# recurse into their own patched replacement.
_REAL_RULE_FOR = search_docs_module._rule_for  # noqa: SLF001
_REAL_DISTINCTIVE_TERMS = lookup_module.distinctive_terms
_REAL_QUERY_TERMS = lookup_module.query_terms


def _rule_for_ignoring_platform(query: str, family: str, lexical_payload: dict | None, *, platform=None):
    return _REAL_RULE_FOR(query, family, lexical_payload)


def _distinctive_terms_ignoring_platform(query: str, family: str, platform: str | None = None):
    return _REAL_DISTINCTIVE_TERMS(query, family)


def _query_terms_ignoring_platform(query: str, family: str, platform: str | None = None):
    return _REAL_QUERY_TERMS(query, family)


@pytest.fixture
def simulate_platform_unaware_lookup(monkeypatch: pytest.MonkeyPatch):
    """Patches every one of this card's platform-threading call sites back to its pre-card,
    platform-unaware shape (``_rule_for``, shared with ``search_docs.py`` and reused here unchanged;
    ``lookup``'s own ``distinctive_terms``/``query_terms`` bindings, used by ``_limitation_docs``,
    ``_answer_quality``'s unmatched-terms fallback and ``_without_unsupported_tables``) - the ONLY
    way to measure, by true A/B comparison on the real wired ``lookup()``, exactly what this card's
    platform-exclusion fix changed end to end, rather than guessing which single call site matters."""

    def _apply() -> None:
        monkeypatch.setattr(search_docs_module, "_rule_for", _rule_for_ignoring_platform)
        monkeypatch.setattr(lookup_module, "_rule_for", _rule_for_ignoring_platform)
        monkeypatch.setattr(lookup_module, "distinctive_terms", _distinctive_terms_ignoring_platform)
        monkeypatch.setattr(lookup_module, "query_terms", _query_terms_ignoring_platform)

    return _apply


# =================================================================================================
# (a) the scope's own platform word is excluded from the distinctive-term count
# =================================================================================================


def test_platform_exclusion_drops_the_distinctive_term_count_by_exactly_one_on_a_real_pilot() -> None:
    without_platform = distinctive_terms(PLATFORM_WORD_QUERY, PDF_NET.family)
    with_platform = distinctive_terms(PLATFORM_WORD_QUERY, PDF_NET.family, platform=PDF_NET.platform)

    assert "net" in without_platform
    assert "net" not in with_platform
    assert len(without_platform) - len(with_platform) == 1


def test_platform_exclusion_narrows_lookups_own_doc_matches_on_the_real_pilot(
    pdf_net_store: GenerationManifestStore,
    simulate_platform_unaware_lookup,
) -> None:
    """The same narrowing TC-617's own ``search_docs`` test file measures directly (4 chunks before,
    2 after) surfaces through ``lookup``'s own composed ``TaskAnswer.doc_matches`` too, since
    ``lookup``'s doc-composition path calls ``search_docs`` directly for each content category.
    Measured directly: ``doc_matches`` drops from 4 to 3 (``_without_unsupported_tables`` and the
    other threaded helpers are not the dominant effect here; ``search_docs``'s own narrowing is) -
    ``symbols``, ``example`` and ``match_quality`` are unaffected for this query."""
    after = lookup(pdf_net_store, PDF_NET, PLATFORM_WORD_QUERY)
    assert isinstance(after, TaskAnswer)

    simulate_platform_unaware_lookup()
    before = lookup(pdf_net_store, PDF_NET, PLATFORM_WORD_QUERY)
    assert isinstance(before, TaskAnswer)

    assert len(before.doc_matches) == 4
    assert len(after.doc_matches) == 3
    assert before.match_quality == after.match_quality == "exact"
    assert _symbol_fqns(before) == _symbol_fqns(after)
    assert bool(before.example) == bool(after.example) is True


# =================================================================================================
# (b) a literal "Aspose.PDF"-style dotted mention - NOT a no-op for lookup
# =================================================================================================


def test_the_wiring_calls_normalize_query_right_after_fold_query() -> None:
    """Integration/liveness assertion on committed source (AGENTS.md: a wired call site must be
    proven to exist in the real entrypoint file, not merely that the wired function's own unit
    test still passes)."""
    source = LOOKUP_SOURCE.read_text(encoding="utf-8")
    fold_line = "query = fold_query(query)"
    normalize_line = "query = normalize_query(query, scope.family)"
    assert source.count(fold_line) == 1
    assert normalize_line in source
    assert source.index(fold_line) < source.index(normalize_line)


def test_normalize_query_is_actually_called_by_the_real_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves ``lookup`` really calls the module-level ``normalize_query`` (not merely that it is
    imported) by spying on it."""
    calls: list[tuple[str, str]] = []
    real_normalize_query = relevance_module.normalize_query

    def _spy(query: str, family: str) -> str:
        calls.append((query, family))
        return real_normalize_query(query, family)

    monkeypatch.setattr(lookup_module, "normalize_query", _spy)

    store = _InMemoryStore(_doc_chunk("Guide", "Aspose.PDF FOSS is a free library for pdf files."))
    lookup(store, PDF_NET, DOTTED_MENTION_QUERY)  # type: ignore[arg-type]

    assert calls, "normalize_query was never called by lookup - the wiring is missing or shadowed"
    assert calls[0] == (fold_query(DOTTED_MENTION_QUERY), "pdf")


def test_the_dot_alone_disables_search_symbols_evidence_stage_before_this_card() -> None:
    """MEASURED mechanism, not assumed: ``search_symbols._plain_words`` (and so
    ``_evidence_ranking``, the stage a sentence like this one needs) refuses any query containing a
    literal dot. ``lookup`` hands ``search_symbols`` the SAME string its own wiring normalizes, so
    this is exactly what this card's dotted-mention fix repairs for ``lookup`` - genuinely
    different from the no-op TC-616 found for ``find_examples`` and this card's own ``search_docs``
    finds too (neither calls ``search_symbols``)."""
    import foss_mcp.mcp.tools.search_symbols as search_symbols_module

    dotted = fold_query(DOTTED_MENTION_QUERY)
    cleaned = normalize_query(dotted, "pdf")
    assert dotted != cleaned

    assert search_symbols_module._plain_words(dotted) is None  # noqa: SLF001 - the literal dot blocks it
    assert search_symbols_module._plain_words(cleaned) is not None  # noqa: SLF001 - no dot remains


def test_the_dotted_mention_fix_is_observable_on_the_real_pilot_through_search_symbols(
    pdf_net_store: GenerationManifestStore,
) -> None:
    """Real-pilot measurement at the mechanism level: on the real, committed pdf/net pilot, the
    dotted query is a complete miss for ``search_symbols`` (the evidence stage never even runs);
    the de-dotted, equivalent query - exactly what ``lookup``'s own wiring now hands it - finds 10
    real symbol matches."""
    dotted = fold_query(DOTTED_MENTION_QUERY)
    cleaned = normalize_query(dotted, "pdf")

    before = search_symbols(pdf_net_store, PDF_NET, dotted)
    after = search_symbols(pdf_net_store, PDF_NET, cleaned)

    assert isinstance(before, Miss)
    assert isinstance(after, list)
    assert len(after) == 10


def test_the_dotted_mention_fix_is_observable_through_the_real_wired_lookup(
    pdf_net_store: GenerationManifestStore,
) -> None:
    """The mechanism-level finding above is not academic: through the whole, real, wired
    ``lookup()`` function on the real pdf/net pilot, the dotted-mention fix changes
    ``TaskAnswer.symbols`` from empty to two real, named symbols. ``doc_matches``, ``example`` and
    ``match_quality`` are unaffected for this query - only the symbol-evidence stage this card's fix
    newly unblocks changes.

    Measured directly (not assumed): "before" is simulated by shadowing ``normalize_query`` with the
    identity function - the exact shape of this card's own negative control - so the dotted string
    reaches ``search_symbols`` unmodified, exactly as it did before this card.
    """
    after = lookup(pdf_net_store, PDF_NET, DOTTED_MENTION_QUERY)
    assert isinstance(after, TaskAnswer)

    real_normalize_query = lookup_module.normalize_query
    lookup_module.normalize_query = lambda q, f: q  # type: ignore[assignment]
    try:
        before = lookup(pdf_net_store, PDF_NET, DOTTED_MENTION_QUERY)
    finally:
        lookup_module.normalize_query = real_normalize_query
    assert isinstance(before, TaskAnswer)

    assert _symbol_fqns(before) == []
    assert _symbol_fqns(after) == ["Aspose.Pdf.Watermark", "Aspose.Pdf.Stamps.WatermarkStamp"]
    assert len(before.doc_matches) == len(after.doc_matches)
    assert before.match_quality == after.match_quality
    assert bool(before.example) == bool(after.example) is True


# =================================================================================================
# (c) the REWORK (attempt 2) regression: a bare product-title whose only distinctive content is
# the scope's own platform word - reproduced through lookup, not just search_docs
# =================================================================================================


def test_a_python_product_title_is_still_answered_through_lookup() -> None:
    """The exact attempt-1-found regression, reproduced through ``lookup`` (which calls
    ``search_docs`` internally for its doc-composition path) and confirmed fixed."""
    store = _InMemoryStore(_doc_chunk("Guide", "Aspose.PDF FOSS for Python is a free library."))
    scope = Scope(family="pdf", platform="python")

    result = lookup(store, scope, PRODUCT_TITLE_QUERY)

    assert isinstance(result, TaskAnswer)
    assert result.doc_matches
    assert result.match_quality == "exact"


def test_reverting_just_the_attempt_2_special_case_reproduces_the_regression_through_lookup() -> None:
    """Hand-verification that the attempt-2 ``_rule_for`` fix is load-bearing for ``lookup`` too:
    with ``_rule_for`` reverted to its attempt-1-only shape (platform threaded but without the new
    special case), the exact same query that answers cleanly above becomes a complete miss through
    the real, wired ``lookup`` function - the regression this card's rework fixed, reproduced one
    layer higher than ``search_docs`` alone."""
    from foss_mcp.mcp.tools.relevance import (
        has_minimum_evidence,
        names_language_letter,
        query_terms,
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

    store = _InMemoryStore(_doc_chunk("Guide", "Aspose.PDF FOSS for Python is a free library."))
    scope = Scope(family="pdf", platform="python")

    real_sd_rule_for = search_docs_module._rule_for  # noqa: SLF001
    real_lk_rule_for = lookup_module._rule_for  # noqa: SLF001
    search_docs_module._rule_for = attempt_1_only_rule_for  # noqa: SLF001
    lookup_module._rule_for = attempt_1_only_rule_for  # noqa: SLF001
    try:
        regressed = lookup(store, scope, PRODUCT_TITLE_QUERY)
    finally:
        search_docs_module._rule_for = real_sd_rule_for  # noqa: SLF001
        lookup_module._rule_for = real_lk_rule_for  # noqa: SLF001

    assert isinstance(regressed, Miss)


# =================================================================================================
# (d) a query that is only the bare platform word, with no other distinctive content
# =================================================================================================


def test_lookup_gives_a_sensible_match_for_the_bare_platform_word_query() -> None:
    """REAL MEASURED BEHAVIOUR (reported, not assumed): with no stray symbol named for the bare
    platform word, ``lookup`` falls through to its doc-composition path, where ``search_docs``'s own
    bare-platform-word special case (this card's attempt-2 fix) answers sensibly - never a forced
    miss."""
    store = _InMemoryStore(_doc_chunk("Guide", "This pdf library runs on the dotnet runtime, called net."))
    scope = Scope(family="pdf", platform="net")

    result = lookup(store, scope, BARE_PLATFORM_WORD_QUERY)

    assert isinstance(result, TaskAnswer)
    assert result.doc_matches
    assert not isinstance(result, Miss)
