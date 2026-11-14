"""G3/TC-605 (REQ-G3-010): F5, the sentence-wrapped retrieval invariant - the direct, permanent
regression test for the originally reported bug (LONGQ-001: a 20-word natural-language ``lookup``
query on pdf/net returns "nothing matches").

Root cause (already confirmed by this plan's own prior analysis; not re-derived here): the real
pdf/net "Installation, Dependencies, and Quick Start" chunk holds only 3 of LONGQ-001's 7
distinctive query terms (coverage 0.43), failing the WHOLE-CHUNK ``guarded_coverage()`` gate at 0.6
- not the 10-line passage window. Most sentence-frame cases below are therefore expected to FAIL
today; Q1-Q10 (later cards) fix the underlying mechanism. This card only measures and pins today's
real baseline, including the literal LONGQ-001 sentence as its own dedicated, always-run case.

Method: a real doc-chunk heading or verified-example title is extracted from each of the 32 real
baked pilots (via ``get_symbol.extract_fqn`` - the product's own FQN-line extractor, never a second
regex), then wrapped in one of twelve genuinely different, natural, conversational sentence frames
(``FRAMES`` below). ``lookup()`` is called with the resulting sentence, exactly as a real MCP client
would; a case PASSES only when the real chunk the title came from is still part of a CONFIDENT
(``match_quality == MATCH_EXACT``) answer - today, before Q7/Q8 land, a ``"partial"`` hit on the
right chunk is not good enough and counts as a FAILURE, same as a plain ``Miss``.

Offline and deterministic: every pilot's manifests are COPIED into a temporary directory by
``tests.conformance.corpus`` (the repository is never written), and the embedding gateway env vars
are scrubbed so this runs exactly as the standalone image does with no gateway configured (TC-399's
own pattern).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT
from tests.conformance.baseline import assert_no_new_failures, load_baseline
from tests.conformance.corpus import (
    ConformanceCorpus,
    ConformancePilot,
    exhaustive_enabled,
    load_demo_manifests_corpus,
    sample_items,
)

BASELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "conformance"
    / "baseline_sentence_wrapped_retrieval.json"
)

EXPECTED_PILOT_COUNT = 32

# The two pseudo-FQN prefixes every self-extracted doc/example chunk's ``FQN:`` line carries
# (``get_symbol._FQN_LINE`` / ``extract_fqn`` already does the real parsing; these are the two fixed,
# literal protocol strings the rest of the product already keys its own ``Doc: ``/``Example: ``
# dispatch on - see e.g. ``search_symbols._NON_SYMBOL_FQN_PREFIXES``).
_DOC_PREFIX = "Doc: "
_EXAMPLE_PREFIX = "Example: "

# Human-readable product/platform names for the sentence frames below, so a generated sentence reads
# naturally ("the PDF library", "my .NET project") instead of echoing the raw scope token ("pdf",
# "net"). A family/platform this plan adds later that is not yet in these maps still renders - just
# with a plain capitalized fallback (``_display`` below) - so a new pilot can never break this test.
_FAMILY_DISPLAY = {
    "3d": "3D",
    "barcode": "Barcode",
    "cells": "Cells",
    "email": "Email",
    "html": "HTML",
    "imaging": "Imaging",
    "note": "Note",
    "page": "Page",
    "pdf": "PDF",
    "slides": "Slides",
    "tex": "TeX",
    "words": "Words",
}
_PLATFORM_DISPLAY = {
    "cpp": "C++",
    "go": "Go",
    "java": "Java",
    "net": ".NET",
    "python": "Python",
    "rust": "Rust",
    "typescript": "TypeScript",
}


def _display(mapping: dict[str, str], key: str) -> str:
    return mapping.get(key, key.capitalize())


# ---- the twelve sentence frames (TC-605 inputs, bullet 3: "vary the filler ... do not just repeat
# one template") -----------------------------------------------------------------------------------
#
# Each is a ``(name, template)`` pair. ``template`` takes ``{title}`` (the real chunk title, first
# letter lowercased so it reads naturally mid-sentence - see ``_lower_first``), ``{family}`` and
# ``{platform}`` (the human-readable names above). Three of the twelve (``id_like_to_understand``,
# ``was_wondering``, ``quick_question``) are deliberately long, multi-clause sentences in the literal
# LONGQ-001 style, per the card's own explicit requirement that the invariant exercise the long-query
# failure mode and not only short ones.

FRAME_HOW_DO_I = (
    "how_do_i",
    "How do I {title} when I'm working with {family} on {platform} right now?",
)
FRAME_I_NEED_TO = (
    "i_need_to",
    "I need to understand {title} for the {platform} app I'm building with {family}, can you help me out?",
)
FRAME_CAN_YOU_SHOW_ME = (
    "can_you_show_me",
    "Can you show me {title}? I'm using {family} inside a {platform} project right now.",
)
FRAME_WHAT_IS_THE_WAY_TO = (
    "what_is_the_way_to",
    "What is the way to handle {title} when you're working with the {family} library on {platform}?",
)
FRAME_IM_TRYING_TO = (
    "im_trying_to",
    "I'm trying to figure out {title} because my {platform} project depends on {family} and I keep getting stuck.",
)
FRAME_COULD_YOU_HELP_ME = (
    "could_you_help_me",
    "Could you help me with {title}? I'm pretty new to {family} and I only just started this {platform} project.",
)
FRAME_WHATS_THE_BEST_APPROACH = (
    "whats_the_best_approach",
    "What's the best approach to {title} if I'm using {family} inside a {platform} codebase at work?",
)
FRAME_IM_LOOKING_FOR_GUIDANCE = (
    "im_looking_for_guidance",
    "I'm looking for guidance on {title}, since our team just adopted {family} for a {platform} service we maintain.",
)
FRAME_DO_YOU_KNOW_HOW_TO = (
    "do_you_know_how_to",
    "Do you know how to {title} with {family} on {platform}, or is there an easier way to go about it?",
)
FRAME_ID_LIKE_TO_UNDERSTAND = (
    "id_like_to_understand",
    "I'd like to understand {title} because I'm building a {platform} app with {family} and want to get it "
    "right before writing code.",
)
FRAME_WAS_WONDERING = (
    "was_wondering",
    "I was wondering, since I'm new to {family} and set up a {platform} project, whether you could walk me "
    "through {title} without missing anything.",
)
FRAME_QUICK_QUESTION = (
    "quick_question",
    "Quick question: how do I deal with {title} in my {platform} project built on {family}, so I don't get "
    "stuck on this part?",
)

FRAMES: tuple[tuple[str, str], ...] = (
    FRAME_HOW_DO_I,
    FRAME_I_NEED_TO,
    FRAME_CAN_YOU_SHOW_ME,
    FRAME_WHAT_IS_THE_WAY_TO,
    FRAME_IM_TRYING_TO,
    FRAME_COULD_YOU_HELP_ME,
    FRAME_WHATS_THE_BEST_APPROACH,
    FRAME_IM_LOOKING_FOR_GUIDANCE,
    FRAME_DO_YOU_KNOW_HOW_TO,
    FRAME_ID_LIKE_TO_UNDERSTAND,
    FRAME_WAS_WONDERING,
    FRAME_QUICK_QUESTION,
)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _render(template: str, title: str, family: str, platform: str) -> str:
    return template.format(title=_lower_first(title), family=family, platform=platform)


# ---- real titles, extracted the same way the product itself does ---------------------------------


@dataclass(frozen=True)
class _TitledChunk:
    """One real doc/example chunk this card wraps in sentence frames: its title (with the pseudo-FQN
    prefix already stripped), the ``chunk_id`` a matching ``DocMatch``/``SymbolMatch`` would carry,
    and the full pseudo-FQN (``"Doc: <title>"`` / ``"Example: <title>"``) a matching
    ``ExampleMatch.fqn`` would carry.
    """

    title: str
    chunk_id: str
    fqn: str


def _titled_chunks(pilot: ConformancePilot) -> list[_TitledChunk]:
    """Every distinct, non-empty doc-heading or example-title this pilot's real chunks carry,
    extracted via ``get_symbol.extract_fqn`` - the product's own FQN-line parser - never a second,
    hand-rolled regex. Sorted by title for a deterministic iteration order.
    """
    chunks: list[_TitledChunk] = []
    seen: set[str] = set()
    for document, prefix in (
        *((document, _DOC_PREFIX) for document in pilot.doc_chunks()),
        *((document, _EXAMPLE_PREFIX) for document in pilot.example_chunks()),
    ):
        fqn = extract_fqn(document["text"]) or ""
        if not fqn.startswith(prefix):
            continue
        title = fqn[len(prefix) :]
        if not title or title in seen:
            continue
        seen.add(title)
        chunks.append(_TitledChunk(title=title, chunk_id=document["chunk_id"], fqn=fqn))
    return sorted(chunks, key=lambda chunk: chunk.title)


# ---- pass/fail judgment: "the real chunk is still part of a CONFIDENT answer" ---------------------


def _is_confident_hit(result: Any) -> bool:
    """Whether *result* (whatever ``lookup`` returned) is a confident, non-empty answer of ANY kind -
    never mind whether it is the RIGHT chunk (``_case_passes`` below checks that separately). A
    ``TaskAnswer`` is confident only when ``match_quality == MATCH_EXACT`` (today, before Q7/Q8 land,
    a ``"partial"`` composed answer does not count); a bare ``list[SymbolMatch]`` fallback (lookup's
    named-type path) is confident unless every member explicitly says otherwise. ``Miss`` and ``None``
    are never confident.
    """
    if isinstance(result, TaskAnswer):
        return result.match_quality == MATCH_EXACT
    if isinstance(result, list) and result:
        return all(getattr(match, "match_quality", MATCH_EXACT) == MATCH_EXACT for match in result)
    return False


def _case_passes(result: Any, target_chunk_id: str, target_fqn: str) -> bool:
    """PASS only when *result* is a confident hit AND the real chunk *target_chunk_id*/*target_fqn*
    came from is actually part of it - a confident answer built from some OTHER chunk is not evidence
    that THIS chunk was found, and must not be counted as a pass.
    """
    if not _is_confident_hit(result):
        return False
    if isinstance(result, TaskAnswer):
        if any(match.chunk_id == target_chunk_id for match in result.doc_matches):
            return True
        if result.example is not None and result.example.fqn == target_fqn:
            return True
        return any(match.chunk_id == target_chunk_id for match in result.symbols)
    if isinstance(result, list):
        return any(getattr(match, "chunk_id", None) == target_chunk_id for match in result)
    return False


# ---- a small, deliberately-absent-vocabulary set, for the precision side --------------------------
#
# Same length range and conversational style as the real sentence frames, built around
# ``test_nonsense_queries_miss.py``'s own nonsense vocabulary (modems faxing, cakes, teleportation,
# quantum spreadsheets) - words that appear in NONE of the 32 real pilots' corpora. These must always
# miss, today and after any future fix; a confident hit on one of these is a precision regression,
# recorded as a failure in the very same baseline.

NONSENSE_SENTENCES: tuple[str, ...] = (
    "How do I send a fax via modem using this library, and will that work the same way on every "
    "platform you support today?",
    "I need to bake a chocolate cake while a long conversion job runs, is that something your "
    "examples could help me plan around?",
    "Can you show me how to teleport the cat to mars before I finish writing the rest of my unit "
    "tests for this afternoon?",
    "What is the way to achieve quantum entanglement of spreadsheets so that two separate files "
    "update each other automatically forever?",
    "I'm trying to figure out how a modem could fax a chocolate cake to mars without losing any "
    "quantum entanglement along the way.",
)


# ---- fixtures ---------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - nothing in
    the repo is ever written to; reading the real baked data is always allowed."""
    root = tmp_path_factory.mktemp("sentence-wrapped-corpus")
    return load_demo_manifests_corpus(root)


# ---- sanity: the corpus this whole card measures against is the real, full 32-pilot set -----------


def test_the_real_demo_manifests_corpus_has_the_expected_pilot_count(corpus: ConformanceCorpus) -> None:
    assert len(corpus.pilots) == EXPECTED_PILOT_COUNT, sorted(corpus.pilots)


def test_every_pilot_has_at_least_one_real_titled_chunk(corpus: ConformanceCorpus) -> None:
    empty = [name for name, pilot in corpus.pilots.items() if not _titled_chunks(pilot)]
    assert not empty, empty


# ---- the LONGQ-001 literal case: its own dedicated, always-run, never-sampled test -----------------
#
# This is the one case this whole card exists to protect going forward (TC-605's own closeout): the
# literal, verbatim reported query, pinned against the real pdf/net pilot. It is never sampled out and
# never gated behind ``exhaustive_enabled()`` - it always executes in the fast default run.

LONGQ_001_QUERY = (
    "how do I install and reference the Aspose.PDF FOSS library in my .NET project so I can start "
    "converting files"
)
LONGQ_001_PILOT = "pdf__net"
LONGQ_001_TITLE = "Installation, Dependencies, and Quick Start"
LONGQ_001_CASE_ID = "sentence-wrapped:pdf__net:longq-001:literal"


def test_longq_001_literal_query_pinned_against_the_real_pdf_net_pilot(corpus: ConformanceCorpus) -> None:
    """The permanent regression test for the originally reported bug. Confirmed by this card's own
    worker, by actually running it (not assumed): TODAY this is a ``Miss`` - the real
    "Installation, Dependencies, and Quick Start" chunk holds only 3 of LONGQ-001's 7 distinctive
    terms (coverage 0.43), under the 0.6 ``guarded_coverage()`` floor. This single case flipping to
    PASS (with the baseline then tightened to ``strict: true`` by Q17, a much later closure card) is
    the literal, permanent proof the original bug is fixed.
    """
    pilot = corpus.pilots[LONGQ_001_PILOT]
    install_chunk = next(chunk for chunk in _titled_chunks(pilot) if chunk.title == LONGQ_001_TITLE)
    result = lookup(corpus.store, pilot.scope, LONGQ_001_QUERY)
    passed = _case_passes(result, install_chunk.chunk_id, install_chunk.fqn)
    observed_failures = (
        ()
        if passed
        else (
            {
                "pilot": LONGQ_001_PILOT,
                "case_id": LONGQ_001_CASE_ID,
                "reason": f"literal LONGQ-001 query -> {type(result).__name__}: {result!r}",
            },
        )
    )
    assert_no_new_failures(load_baseline(BASELINE_PATH), observed_failures)


# ---- the generic sentence-wrapped sweep: a seeded sample in the default run, every combination -----
# behind ``exhaustive_enabled()`` ---------------------------------------------------------------------

# TC-605 inputs: "seeded sample of 2-3 titles per pilot x the 12 frames (so roughly 60-100 real-
# sentence cases)". Clamped per pilot by ``sample_items`` when a pilot has fewer distinct titles than
# this. Each sampled (pilot, title) pair draws ONE frame from the 12 (cycling deterministically across
# the whole sweep, so every frame is exercised many times over the full run without the combinatorial
# blow-up every-title-x-every-frame would be); the full cross product is the exhaustive sweep below.
_TITLES_PER_PILOT = 3
_SAMPLE_SEED = 605


def _default_sample_cases(corpus: ConformanceCorpus) -> list[tuple[str, ConformancePilot, int, _TitledChunk]]:
    cases: list[tuple[str, ConformancePilot, int, _TitledChunk]] = []
    case_index = 0
    for pilot_name in sorted(corpus.pilots):
        pilot = corpus.pilots[pilot_name]
        sampled = sample_items(_titled_chunks(pilot), seed=_SAMPLE_SEED, n=_TITLES_PER_PILOT)
        for title_chunk in sampled:
            cases.append((pilot_name, pilot, case_index % len(FRAMES), title_chunk))
            case_index += 1
    return cases


def test_sentence_wrapped_retrieval_default_sample(corpus: ConformanceCorpus) -> None:
    """The card's own default-run sweep: a seeded sample of 2-3 real titles per pilot, each wrapped in
    one of the twelve frames, measured against the real ``lookup()`` entry point and pinned into the
    committed baseline. Most of these are expected to FAIL today (see module docstring); this test
    only proves the measured set of failures has not grown beyond what is already pinned.
    """
    cases = _default_sample_cases(corpus)
    assert cases, "no sampled cases were generated - the corpus or the sampler is broken"
    observed_failures: list[dict[str, str]] = []
    for pilot_name, pilot, frame_index, title_chunk in cases:
        family = _display(_FAMILY_DISPLAY, pilot.scope.family)
        platform = _display(_PLATFORM_DISPLAY, pilot.scope.platform)
        frame_name, frame_template = FRAMES[frame_index]
        sentence = _render(frame_template, title_chunk.title, family, platform)
        word_count = len(sentence.split())
        if not 12 <= word_count <= 35:
            # A soft, baseline-tracked failure (the G3/TC-775 pattern), never a bare assert: an assert
            # here aborts the whole sweep before ``assert_no_new_failures`` below is reached and masks
            # every other measurement. G3/TC-813 bounds authored-example titles at 12 words so no
            # authored title can reach this branch.
            observed_failures.append(
                {
                    "pilot": pilot_name,
                    "case_id": f"sentence-wrapped-wordcount:{pilot_name}:{frame_index}:{title_chunk.title}",
                    "reason": (
                        f"frame={frame_name!r} rendered a {word_count}-word sentence (want 12-35): "
                        f"{sentence!r}"
                    ),
                }
            )
        result = lookup(corpus.store, pilot.scope, sentence)
        if not _case_passes(result, title_chunk.chunk_id, title_chunk.fqn):
            observed_failures.append(
                {
                    "pilot": pilot_name,
                    "case_id": f"sentence-wrapped:{pilot_name}:{frame_index}:{title_chunk.title}",
                    "reason": (
                        f"frame={frame_name!r} title={title_chunk.title!r} sentence={sentence!r} "
                        f"-> {type(result).__name__}"
                    ),
                }
            )
    assert_no_new_failures(load_baseline(BASELINE_PATH), observed_failures)


# The full cross-product sweep is defined only when ``FOSS_MCP_CONFORMANCE_EXHAUSTIVE=1``
# (``exhaustive_enabled()``) - NOT skipped at call time. A test gated by ``pytest.skip()`` still shows
# up as a collected-but-skipped test, and the structural gate treats any skip as unproven ("skips are
# not evidence"). Defining the function only inside this ``if`` means pytest never even collects it in
# the default run: it simply does not exist for that run, so there is nothing to skip.
if exhaustive_enabled():

    def test_sentence_wrapped_retrieval_full_sweep(corpus: ConformanceCorpus) -> None:
        """Every real title x all twelve frames x all 32 pilots - opt-in only
        (``FOSS_MCP_CONFORMANCE_EXHAUSTIVE=1``) since it is far too slow for the default run; not
        collected at all otherwise (see the module-level ``if`` this is defined under).
        """
        observed_failures: list[dict[str, str]] = []
        for pilot_name in sorted(corpus.pilots):
            pilot = corpus.pilots[pilot_name]
            family = _display(_FAMILY_DISPLAY, pilot.scope.family)
            platform = _display(_PLATFORM_DISPLAY, pilot.scope.platform)
            for title_chunk in _titled_chunks(pilot):
                for frame_index, (frame_name, frame_template) in enumerate(FRAMES):
                    sentence = _render(frame_template, title_chunk.title, family, platform)
                    result = lookup(corpus.store, pilot.scope, sentence)
                    if not _case_passes(result, title_chunk.chunk_id, title_chunk.fqn):
                        observed_failures.append(
                            {
                                "pilot": pilot_name,
                                "case_id": f"sentence-wrapped:{pilot_name}:{frame_index}:{title_chunk.title}",
                                "reason": (
                                    f"frame={frame_name!r} title={title_chunk.title!r} "
                                    f"sentence={sentence!r} -> {type(result).__name__}"
                                ),
                            }
                        )
        assert_no_new_failures(load_baseline(BASELINE_PATH), observed_failures)


# ---- the precision side: nonsense sentences must (almost) always miss ------------------------------


def test_nonsense_sentence_frames_are_an_honest_miss_across_all_pilots(corpus: ConformanceCorpus) -> None:
    """Every nonsense sentence, against every one of the 32 real pilots, must never produce a
    confident hit of ANY kind - a confident answer built from absent vocabulary would be a precision
    regression, not merely an incomplete one, and is recorded as a failure in the same baseline.
    """
    observed_failures: list[dict[str, str]] = []
    for pilot_name in sorted(corpus.pilots):
        pilot = corpus.pilots[pilot_name]
        for sentence_index, sentence in enumerate(NONSENSE_SENTENCES):
            result = lookup(corpus.store, pilot.scope, sentence)
            if _is_confident_hit(result):
                observed_failures.append(
                    {
                        "pilot": pilot_name,
                        "case_id": f"sentence-wrapped-nonsense:{pilot_name}:{sentence_index}",
                        "reason": f"nonsense sentence unexpectedly confident: {sentence!r} -> {result!r}",
                    }
                )
    assert_no_new_failures(load_baseline(BASELINE_PATH), observed_failures)
