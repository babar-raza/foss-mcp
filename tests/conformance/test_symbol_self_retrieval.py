"""G3/TC-604 (REQ-G3-010): F4, the symbol self-retrieval conformance invariant.

Measures, against all 32 real baked pilots, how reliably ``search_symbols``/``get_symbol`` resolve a
real symbol by its own real name in every form a caller might reasonably try - and, just as
importantly, that a FABRICATED name or a bare member/namespace word is never labelled a confident
``"exact"`` match. ``src/foss_mcp/mcp/tools/search_symbols.py`` was read in full before writing this:
confirmed there is genuinely no prefix tier (``_match_tier`` only ever admits tier 0 full-FQN equality,
tier 1 final-segment equality, or tier 2 bag-of-words contiguous-run containment) and that
``_symbol_words``'s camel-case boundary regex never splits a digit run from the letters before it
(``Vector2`` stays one word), so sub-cases (d) and (e) below are expected to fail today for most or
every real symbol measured - this card's whole job is to measure and PIN today's real numbers as a
committed baseline for later fix cards (Q11-Q16) to shrink, not to fix any of it.

This card's own ``inputs`` instruct reuse of ``tests/conformance/corpus.py`` (the loader/sampler) and
``tests/conformance/baseline.py`` (the committed-baseline contract) rather than reinventing either -
see those two modules' own docstrings. Each (pilot, case_id) measurement is recorded as an observed
failure or a pass, never a bare per-symbol ``assert``, so ``assert_no_new_failures`` against the
committed baseline is the one place this file's checks can fail.

Default run: a small, seeded sample of real symbols per pilot (``SAMPLE_SIZE``), so the whole file
completes in well under 3 minutes. Set ``FOSS_MCP_CONFORMANCE_EXHAUSTIVE=1`` (``exhaustive_enabled()``,
from ``corpus.py``) for an unsampled sweep over every real symbol in every pilot instead.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.mcp.tools.get_symbol import NotFound, _extract_block, extract_fqn, get_symbol
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, _member_name, search_symbols
from tests.conformance.baseline import assert_no_new_failures, load_baseline
from tests.conformance.corpus import (
    ConformanceCorpus,
    ConformancePilot,
    exhaustive_enabled,
    load_demo_manifests_corpus,
    sample_items,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "conformance" / "baseline_symbol_self_retrieval.json"

EXPECTED_PILOT_COUNT = 32
# A fixed seed (this card's own number), never the global ``random`` module - see ``sample_items``'s
# own docstring. SAMPLE_SIZE keeps the default run's per-pilot symbol count small; DIGIT_SAMPLE_SIZE
# bounds sub-case (e)'s own scan, which runs over every real FQN regardless (cheap: a regex check, no
# ``search_symbols`` call) but only probes a bounded, deterministic subset of what it finds.
SEED = 604
SAMPLE_SIZE = 3
DIGIT_SAMPLE_SIZE = 5

# The exact regex ``search_symbols.py``'s own ``_FINAL_SEGMENT`` uses - copied rather than imported
# (that name is private to its module; several existing test files in this repo already copy this same
# one-line regex locally rather than reach into the product module's private names, e.g.
# ``tests/mcp/tools/test_forgiveness_acceptance.py`` and ``tests/indexing/test_chunk_builder_stub_pairing.py``).
_FINAL_SEGMENT = re.compile(r"\.|::")
# A letter immediately followed by a digit - the exact pattern sub-case (e) needs: the letter-only
# prefix before the first digit run of a real short name (``Vector2`` -> ``Vector``; ``Code128Options``
# -> ``Code``).
_DIGIT_AFTER_LETTER = re.compile(r"^([A-Za-z]+)\d")


def _short_name(fqn: str) -> str:
    """The FQN's own final dotted (or ``::``-scoped) segment - the same split ``_match_tier``'s own
    tier 1 compares against."""
    return _FINAL_SEGMENT.split(fqn.strip())[-1]


def _symbol_texts(pilot: ConformancePilot) -> dict[str, str]:
    """Real FQN -> its own chunk text, the first chunk seen for a given FQN (a later chunk sharing the
    same FQN is a duplicate/alias of the same type, not a second distinct real symbol)."""
    texts: dict[str, str] = {}
    for chunk in pilot.symbol_chunks():
        fqn = extract_fqn(chunk["text"])
        if fqn and fqn not in texts:
            texts[fqn] = chunk["text"]
    return texts


def _fabricate_fqn(fqn: str, short: str, sample: Sequence[str], known_fqns: frozenset[str]) -> str:
    """A syntactically-plausible but FABRICATED full FQN: *fqn* with only its own final segment
    replaced. The replacement is, where possible, another REAL symbol's own short name drawn from this
    same pilot's sample - a genuine tail-segment collision candidate (sub-case (f) measures whether this
    ever lets the fabricated name resolve ``exact`` through ``_match_tier``'s bag-of-words tier, which
    compares WORDS, not the literal dotted string, so a shared tail word can admit a hit even though the
    full fabricated string itself never appears anywhere real). Falls back to a made-up word when the
    sample holds no other real short name to borrow. Guaranteed, by construction and the final check
    below, never to equal a real FQN already in *known_fqns*.
    """
    others = [_short_name(candidate) for candidate in sample if candidate != fqn]
    borrowed = next((name for name in others if name != short), None) or f"Zzqfabricated{short[:4]}"
    sep = "::" if "::" in fqn else "."
    parts = list(_FINAL_SEGMENT.split(fqn.strip()))
    parts[-1] = borrowed
    fabricated = sep.join(parts)
    if fabricated in known_fqns:
        parts[-1] = f"Zzqfabricated{borrowed}"
        fabricated = sep.join(parts)
    return fabricated


def _first_declared_member(texts: Mapping[str, str], sample: Sequence[str]) -> str | None:
    """The first member name declared in any sampled symbol's own rendered ``Methods:``/``Properties:``
    block (parsed with ``get_symbol.py``'s own ``_extract_block`` and ``search_symbols.py``'s own
    ``_member_name`` - the same parsing those two modules already use, not a second, different one), or
    ``None`` if nothing in the sample declares any member at all."""
    for fqn in sample:
        text = texts[fqn]
        for header in ("Methods:", "Properties:"):
            for item in _extract_block(text, header):
                name = _member_name(item)
                if name:
                    return name
    return None


def _is_exact_hit(result: object, fqn: str | None = None) -> bool:
    """True when *result* is a non-empty ``search_symbols`` match list whose first (best-ranked) item is
    an ``exact`` match - optionally, that it also names *fqn* exactly (sub-case (a)'s own rank-1 check)."""
    if not isinstance(result, list) or not result:
        return False
    first = result[0]
    if first.match_quality != MATCH_EXACT:
        return False
    return fqn is None or extract_fqn(first.text) == fqn


def _is_findable(result: object, fqn: str) -> bool:
    """True when *result* is a ``search_symbols`` match list containing *fqn* anywhere (not necessarily
    first, not necessarily ``exact`` - sub-cases (c)/(d)/(e) only ask "is it in the top 10 at all").

    TC-686 (I4+I5, REQ-G3-012): a real alias pair (a re-export shell and its definition) now
    correctly collapses into ONE result instead of two separate hits, with the shell's fqn listed
    in the kept result's own ``.aliases`` - so *fqn* is also "found" when it is the requested name
    of a match whose primary ``extract_fqn(match.text)`` is a different, canonical fqn, as long as
    *fqn* itself appears among that match's ``aliases``.
    """
    return isinstance(result, list) and any(
        extract_fqn(match.text) == fqn or fqn in match.aliases for match in result
    )


def _measure_pilot(store: object, pilot: ConformancePilot, *, exhaustive: bool) -> list[str]:
    """The ``case_id`` strings this pilot measures as a FAILURE right now, for sub-cases (a)-(g). The
    caller pairs each with this pilot's own name to build the (pilot, case_id) observed-failure pairs
    ``assert_no_new_failures`` compares against the committed baseline.
    """
    texts = _symbol_texts(pilot)
    fqns = frozenset(texts)
    if not fqns:
        return []
    scope = pilot.scope
    ordered_fqns = sorted(fqns)
    sample_size = len(ordered_fqns) if exhaustive else min(SAMPLE_SIZE, len(ordered_fqns))
    sample = ordered_fqns if exhaustive else sample_items(ordered_fqns, seed=SEED, n=sample_size)
    failures: list[str] = []

    for fqn in sample:
        short = _short_name(fqn)

        # (a) exact FQN -> rank-1, match_quality == "exact".
        result_a = search_symbols(store, scope, fqn, top_k=10)  # type: ignore[arg-type]
        if not _is_exact_hit(result_a, fqn):
            failures.append(f"exact-fqn-rank1:{fqn}")

        # (b) get_symbol resolves (not NotFound).
        resolved = get_symbol(store, scope, fqn)  # type: ignore[arg-type]
        if isinstance(resolved, NotFound):
            failures.append(f"get-symbol-resolves:{fqn}")

        # (c) bare short name: findable in the top 10 (not necessarily "exact").
        result_c = search_symbols(store, scope, short, top_k=10)  # type: ignore[arg-type]
        if not _is_findable(result_c, fqn):
            failures.append(f"bare-short-name-findable:{fqn}")

        # (d) a 3-character prefix of the short name: findable in the top 10. EXPECTED TO FAIL TODAY
        # for most/every real symbol - no prefix tier exists.
        if len(short) >= 3:
            result_d = search_symbols(store, scope, short[:3], top_k=10)  # type: ignore[arg-type]
            if not _is_findable(result_d, fqn):
                failures.append(f"prefix3-findable:{fqn}")

        # (f) fabricated full FQN must NOT resolve "exact".
        fabricated = _fabricate_fqn(fqn, short, sample, fqns)
        result_f = search_symbols(store, scope, fabricated, top_k=10)  # type: ignore[arg-type]
        if _is_exact_hit(result_f):
            failures.append(f"fabricated-fqn-false-exact:{fqn}")

    # (e) digit-immediately-after-letter real names: the letter-only prefix before the digit should
    # find the real symbol. Scans every real FQN of the pilot (cheap: a regex check, no search call),
    # then probes only a bounded, deterministic subset when not exhaustive. ALSO EXPECTED TO FAIL TODAY
    # wherever a real example exists - no digit-run splitting is implemented.
    digit_candidates = [fqn for fqn in ordered_fqns if _DIGIT_AFTER_LETTER.match(_short_name(fqn))]
    if not exhaustive and len(digit_candidates) > DIGIT_SAMPLE_SIZE:
        digit_candidates = sample_items(digit_candidates, seed=SEED, n=DIGIT_SAMPLE_SIZE)
    for fqn in digit_candidates:
        letter_prefix = _DIGIT_AFTER_LETTER.match(_short_name(fqn)).group(1)  # type: ignore[union-attr]
        result_e = search_symbols(store, scope, letter_prefix, top_k=10)  # type: ignore[arg-type]
        if not _is_findable(result_e, fqn):
            failures.append(f"digit-stripped-family-findable:{fqn}")

    # (g) a bare namespace segment word, and a bare declared-member word, must never resolve "exact".
    # One representative probe per pilot is enough: every real FQN in a pilot shares the same first
    # namespace segment, and the member-declaration tier (see search_symbols.py's own module docstring,
    # TC-279) treats every admitted member-name hit as "exact" the same way regardless of which real
    # member supplied it.
    namespace_word = _FINAL_SEGMENT.split(sample[0].strip())[0]
    result_g_namespace = search_symbols(store, scope, namespace_word, top_k=10)  # type: ignore[arg-type]
    if _is_exact_hit(result_g_namespace):
        failures.append(f"bare-namespace-word-false-exact:{namespace_word}")

    member_word = _first_declared_member(texts, sample)
    if member_word is not None:
        result_g_member = search_symbols(store, scope, member_word, top_k=10)  # type: ignore[arg-type]
        if _is_exact_hit(result_g_member):
            failures.append(f"bare-member-word-false-exact:{member_word}")

    return failures


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - nothing in
    the repo is ever written to; reading the real baked data is always allowed."""
    root = tmp_path_factory.mktemp("symbol-self-retrieval-corpus")
    return load_demo_manifests_corpus(root)


def test_corpus_loads_all_32_real_baked_pilots(corpus: ConformanceCorpus) -> None:
    assert len(corpus.pilots) == EXPECTED_PILOT_COUNT, sorted(corpus.pilots)


def test_every_real_pilot_has_at_least_one_real_symbol(corpus: ConformanceCorpus) -> None:
    empty = [name for name, pilot in corpus.pilots.items() if not _symbol_texts(pilot)]
    assert not empty, empty


def test_the_default_sample_is_deterministic_for_this_module_own_seed(corpus: ConformanceCorpus) -> None:
    pilot = corpus.pilots["cells__net"]
    fqns = sorted(_symbol_texts(pilot))
    n = min(SAMPLE_SIZE, len(fqns))
    first = sample_items(fqns, seed=SEED, n=n)
    second = sample_items(fqns, seed=SEED, n=n)
    assert first == second
    assert len(first) == n


def test_fabricate_fqn_never_reproduces_a_real_known_fqn(corpus: ConformanceCorpus) -> None:
    """A regression here would silently turn sub-case (f) into a no-op: a "fabricated" name that
    actually IS a real FQN would trivially resolve exact for an honest reason, not the false-positive
    this sub-case exists to catch."""
    for pilot in corpus.pilots.values():
        texts = _symbol_texts(pilot)
        fqns = frozenset(texts)
        if not fqns:
            continue
        ordered = sorted(fqns)
        sample = sample_items(ordered, seed=SEED, n=min(SAMPLE_SIZE, len(ordered)))
        for fqn in sample:
            fabricated = _fabricate_fqn(fqn, _short_name(fqn), sample, fqns)
            assert fabricated not in fqns, (pilot.name, fabricated)


def test_symbol_self_retrieval_matches_the_committed_baseline(corpus: ConformanceCorpus) -> None:
    """The one real measurement this card exists to pin: every (pilot, case_id) failure observed on
    THIS run must already be known to the committed baseline - a failure outside it is a genuine new
    regression (or, if the behaviour genuinely improved, the baseline may simply be ahead; see
    ``assert_no_new_failures``'s own docstring for the non-strict/strict distinction)."""
    exhaustive = exhaustive_enabled()
    observed: list[tuple[str, str]] = []
    for name in sorted(corpus.pilots):
        pilot = corpus.pilots[name]
        for case_id in _measure_pilot(corpus.store, pilot, exhaustive=exhaustive):
            observed.append((name, case_id))
    baseline = load_baseline(BASELINE_PATH)
    assert_no_new_failures(baseline, observed)
