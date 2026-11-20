"""G3/TC-607 (REQ-G3-010): F7, the index-honesty conformance invariant.

Measures, against all 32 real baked pilots, the three self-referential-counting defects this
plan's research already confirmed (``C:\\Users\\babar\\.claude\\plans\\steady-wobbling-meteor.md``
sections 1.1/M2 and 2.1/F7):

1. **Tautology**: the ``API-Coverage: N of N (100%)`` line's numerator and denominator both come
   from the same extractor pass (``chunk_builder._coverage_line``), so it is 100% by construction
   whenever the chunker kept every candidate entry. This sub-case parses every symbol chunk's
   (and, as a sanity check, every doc/example chunk's) ``API-Coverage:`` line with the real
   production regex (``foss_mcp.mcp.tools.coverage_note.parse_api_coverage`` - the exact parser
   ``get_symbol``/``report_index_freshness`` already use), and records how many of the 32 pilots
   show 100% everywhere today.
2. **Alias duplication**: a Python re-export shell and its definition used to be rendered as two
   separate symbol chunks that share a byte-identical ``Methods:``/``Properties:`` body (confirmed
   below by reading the real chunk text: see ``_member_body``) - ``TC-686`` (I4+I5, REQ-G3-012)
   thinned every real shell's body to empty, so this specific alias pattern no longer reproduces
   anywhere (confirmed, G3/TC-775: 0 of 1,379 real shell chunks across the 11 real Python pilots
   carry a nonempty body any more). The underlying duplicate-body grouping this sub-case measures
   is broader than that one pattern, though, and still finds real, COINCIDENTAL duplication
   (unrelated types that happen to render an identical trivial body) in every pilot that had it
   before. This sub-case groups each pilot's symbol chunks by that body and flags a pilot whose
   duplicate-body chunk count exceeds a documented threshold.
3. **Parse-failure undercount**: the ``API-Coverage:`` line's "N source files could not be parsed"
   clause is the POST-REPAIR count (``run_extraction.python_parse_health``'s ``failed``), which can
   understate the AS-WRITTEN count (``as_written_failed``) - already confirmed for tex/python
   (reported "11 of 45" vs. a real "35 of 45"). This sub-case checks, for each of the 11 real Python
   pilots, whether its committed ``tests/fixtures/<pilot>/api_surface.json`` fixture separately
   exposes an ``as_written_failed`` field. It does NOT re-clone or re-parse any upstream repository
   (this card is offline, ``network: false``) - where the fixture does not carry that distinction,
   this card's own closeout says so honestly rather than fabricating a verification it did not
   perform.

All three sub-cases feed their findings into ONE combined baseline
(``tests/fixtures/conformance/baseline_index_honesty.json``) via
``tests.conformance.baseline.assert_no_new_failures``, ``case_id`` naming the sub-case and the
pilot (``"tautology:<pilot>"``, ``"alias_duplication:<pilot>"``,
``"parse_failure_undercount:<pilot>"``). This card only measures and pins today's real numbers;
I1-I3 (later cards) do the fixing - nothing here is weakened or fixed in place.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.mcp.tools.coverage_note import parse_api_coverage
from tests.conformance.baseline import Baseline, assert_no_new_failures, load_baseline
from tests.conformance.corpus import (
    REPO_ROOT,
    ConformanceCorpus,
    load_demo_manifests_corpus,
)

BASELINE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "conformance" / "baseline_index_honesty.json"
)

EXPECTED_PILOT_COUNT = 32
EXPECTED_PYTHON_PILOT_COUNT = 11

# TC-607: "a small, documented threshold ... e.g. more than a handful of duplicate-body pairs".
# Chosen as 10 duplicate-body CHUNKS (not groups): most pilots measured 2026-10-30 have 0-7 (a
# handful of incidental or small re-export groups); the pilots that exceed 10 are measurably in a
# different class (double digits to several hundred duplicate chunks - see the baseline for the
# real per-pilot numbers). 10 is a round number comfortably above the largest "incidental" pilot
# observed (cells/rust and cells/typescript both sit at 6-7) and comfortably below the smallest
# pilot this baseline records as a real finding (tex/python at 14).
DUPLICATE_CHUNK_THRESHOLD = 10

_COVERAGE_LINE = re.compile(r"^API-Coverage:.*$", re.MULTILINE)


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy."""
    root = tmp_path_factory.mktemp("index-honesty-corpus")
    return load_demo_manifests_corpus(root)


@pytest.fixture(scope="module")
def baseline() -> Baseline:
    return load_baseline(BASELINE_PATH)


def _coverage_lines(text: str) -> list[str]:
    """Every literal ``API-Coverage:`` line in *text* (a chunk's raw document text)."""
    return _COVERAGE_LINE.findall(text)


def _member_body(text: str) -> str:
    """The rendered ``Methods:``/``Properties:`` section(s) of a symbol chunk's text, joined.

    ``chunk_builder._format_type_text`` separates every section with a blank line, so splitting on
    ``"\\n\\n"`` and keeping the sections whose header starts a ``Methods:`` or ``Properties:``
    block recovers exactly the member-body text two chunks would share if one is a Python re-export
    shell rendered from the other's ``members_from`` (confirmed against the real
    ``slides__python`` corpus: a shell's and its definition's chunks differ only in their ``FQN:``/
    ``Defined-In:``/``Exported-As:`` header lines - their ``Methods:``/``Properties:`` bodies are
    byte-identical). A type with neither methods nor properties renders an empty body here, which
    is deliberately excluded by every caller below (grouping empty bodies together would flag every
    member-less type in a pilot as "duplicated", which is not the signal this sub-case measures).
    """
    sections = text.split("\n\n")
    picked = [
        section for section in sections if section.startswith("Methods:") or section.startswith("Properties:")
    ]
    return "\n\n".join(picked)


def _python_pilot_names() -> list[str]:
    """The real Python pilots, read from ``config/products/*/python.yaml`` - never a hardcoded guess."""
    roots = sorted((REPO_ROOT / "config" / "products").glob("*/python.yaml"))
    return [f"{path.parent.name}__python" for path in roots]


# ---- sub-case 1: the API-Coverage tautology -----------------------------------------------------


def _tautology_observations(corpus: ConformanceCorpus) -> tuple[list[dict[str, str]], dict[str, bool]]:
    """``(observed_failures, {pilot: is_100_percent_everywhere})`` over every real pilot.

    A "failure" here covers two things: the sanity check the card asks for (the SAME line's own
    numerator exceeding its denominator, or a functions clause doing the same - an internally
    inconsistent claim no honest accounting explains), and - G3/TC-775 - a structural gap in the
    disclosure itself (a symbol chunk with zero or more than one ``API-Coverage:`` line, a
    non-symbol chunk that carries one, an unparsable line, or a pilot with no disclosed line at
    all). These structural checks used to be bare ``assert`` statements here, which crashed this
    whole function (and every one of this test module's three sub-cases, since they share one
    pytest collection) before ``assert_no_new_failures`` was ever reached - exactly the same
    crash-masking shape `TC-773`/`TC-774` found and fixed in `test_sentence_wrapped_retrieval.py`.
    They are real findings now, not crashes: confirmed, G3/TC-775, against the real baked corpus,
    exactly one of them fires today - `pdf__java`'s real
    `org.aspose.pdf.engine.xfa.model.template.Proto` symbol chunk carries ZERO ``API-Coverage:``
    lines, because its rendered text (hundreds of XFA child-element accessor methods) exceeds
    ``infra/build_chunks.py``'s ``MAX_CHUNK_TEXT_CHARS`` (24,000) cap and ``_sanitize_chunk_text``'s
    truncation cuts the text before reaching the trailing ``API-Coverage:`` line
    ``chunk_builder._format_type_text`` always appends last. That is a real, genuine
    extraction/build-pipeline defect (any sufficiently large chunk can silently lose its coverage
    disclosure the same way) - its real fix lives in ``infra/build_chunks.py``, outside this card's
    ``write_paths``, so it is flagged here for a focused follow-up card rather than forced into this
    one's scope; this test only ensures the real gap is tracked, not silently absorbed or allowed to
    crash the rest of this module's measurements. A genuine, honestly-disclosed <100% case is never
    a failure (the card calls that a good sign); it only flips that pilot's own
    ``is_100_percent_everywhere`` entry to ``False`` for the closeout's own count - and so does a
    structural gap, since "100% everywhere" cannot honestly be claimed for a pilot with a symbol
    chunk that discloses no coverage at all.
    """
    failures: list[dict[str, str]] = []
    all_100_percent: dict[str, bool] = {}
    for name, pilot in sorted(corpus.pilots.items()):
        lines_seen: set[str] = set()
        has_structural_gap = False
        for chunk in pilot.symbol_chunks():
            lines = _coverage_lines(chunk["text"])
            if len(lines) != 1:
                has_structural_gap = True
                failures.append(
                    {
                        "pilot": name,
                        "case_id": f"tautology:{name}",
                        "reason": (
                            f"a symbol chunk ({chunk.get('chunk_id')!r}) carries {len(lines)} "
                            "API-Coverage lines instead of exactly 1"
                        ),
                    }
                )
                continue
            lines_seen.add(lines[0])
        for chunk in [*pilot.doc_chunks(), *pilot.example_chunks()]:
            if "API-Coverage:" in chunk["text"]:
                has_structural_gap = True
                failures.append(
                    {
                        "pilot": name,
                        "case_id": f"tautology:{name}",
                        "reason": f"a non-symbol chunk ({chunk.get('chunk_id')!r}) carries the API-Coverage line",
                    }
                )
        if not lines_seen:
            has_structural_gap = True
            failures.append(
                {
                    "pilot": name,
                    "case_id": f"tautology:{name}",
                    "reason": "no symbol chunk carries an API-Coverage line",
                }
            )

        pilot_is_100_percent = not has_structural_gap
        for line in lines_seen:
            parsed = parse_api_coverage(line)
            if parsed is None:
                has_structural_gap = True
                pilot_is_100_percent = False
                failures.append(
                    {
                        "pilot": name,
                        "case_id": f"tautology:{name}",
                        "reason": f"unparsable API-Coverage line {line!r}",
                    }
                )
                continue
            if parsed.indexed > parsed.total:
                failures.append(
                    {
                        "pilot": name,
                        "case_id": f"tautology:{name}",
                        "reason": f"types clause indexed ({parsed.indexed}) exceeds total ({parsed.total}) in {line!r}",
                    }
                )
            if parsed.functions is not None and parsed.functions[0] > parsed.functions[1]:
                failures.append(
                    {
                        "pilot": name,
                        "case_id": f"tautology:{name}",
                        "reason": f"functions clause indexed ({parsed.functions[0]}) exceeds total "
                        f"({parsed.functions[1]}) in {line!r}",
                    }
                )
            if parsed.indexed != parsed.total or (
                parsed.functions is not None and parsed.functions[0] != parsed.functions[1]
            ):
                pilot_is_100_percent = False
        all_100_percent[name] = pilot_is_100_percent
    return failures, all_100_percent


def test_tautology_sub_case_matches_the_committed_baseline(
    corpus: ConformanceCorpus, baseline: Baseline
) -> None:
    failures, _ = _tautology_observations(corpus)
    assert_no_new_failures(baseline, failures)


def test_tautology_sub_case_pins_todays_real_count_of_fully_tautological_pilots(
    corpus: ConformanceCorpus,
) -> None:
    """The real audit finding this sub-case exists to pin: of the 32 pilots, how many show 100%
    coverage everywhere today, naming any exception. Measured 2026-10-30: all 32 (no exception) -
    the tautology this card's purpose describes reproduces on every real baked pilot, every symbol
    chunk, with no doc/example chunk ever carrying the line and no symbol chunk ever missing it.

    Re-measured G3/TC-775 (2026-11-12): 31 of 32, with exactly one real, confirmed exception -
    `pdf__java`. Its real `org.aspose.pdf.engine.xfa.model.template.Proto` symbol chunk discloses NO
    ``API-Coverage:`` line at all (not merely a <100% one): the chunk's rendered text is large enough
    (hundreds of XFA child-element accessor methods) to exceed ``infra/build_chunks.py``'s 24,000-
    character cap, and that script's ``_sanitize_chunk_text`` truncation cuts the text before it
    reaches the trailing coverage line `chunk_builder._format_type_text` always appends last - a
    real extraction/build-pipeline defect (see `_tautology_observations`'s own docstring), flagged
    for a focused follow-up card since its fix lives outside this card's `write_paths`. This is not
    the "genuine, honestly-disclosed <100%" case this docstring's prior paragraph calls a good sign
    - it is an outright missing disclosure - so it is named here as the one real exception rather
    than silently folded back into "no exception".

    Re-measured G3/TC-850 (2026-11-20): all 32 again, no exception. TC-790 made ``_sanitize_chunk_text`` keep the trailing
    ``API-Coverage:`` line when it truncates a chunk, and the cohort C3 regeneration re-baked `pdf__java`: its
    `...xfa.model.template.Proto` chunk now carries its single coverage line (after its own ``Truncated: N characters omitted``
    disclosure), so the one confirmed exception is FIXED and the pinned exception list is empty.
    """
    _, all_100_percent = _tautology_observations(corpus)
    assert len(all_100_percent) == EXPECTED_PILOT_COUNT, sorted(all_100_percent)
    exceptions = sorted(name for name, is_full in all_100_percent.items() if not is_full)
    assert exceptions == [], (
        f"the set of pilots with a real coverage exception changed: {exceptions} (update this test's own pinned list and name the change in the next card's closeout)"
    )


# ---- sub-case 2: alias-duplication (Python re-export shells rendered as full duplicate chunks) ---


def _alias_duplication_observations(
    corpus: ConformanceCorpus,
) -> tuple[list[dict[str, str]], dict[str, tuple[int, int, int]]]:
    """``(observed_failures, {pilot: (duplicate_group_count, duplicate_chunk_count, symbol_chunk_count)})``."""
    failures: list[dict[str, str]] = []
    measurements: dict[str, tuple[int, int, int]] = {}
    for name, pilot in sorted(corpus.pilots.items()):
        symbol_chunks = pilot.symbol_chunks()
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk in symbol_chunks:
            body = _member_body(chunk["text"])
            if body.strip():  # an empty Methods:/Properties: body is never counted as a duplicate
                groups[body].append(chunk)
        duplicate_groups = {body: chunks for body, chunks in groups.items() if len(chunks) > 1}
        duplicate_chunk_count = sum(len(chunks) for chunks in duplicate_groups.values())
        measurements[name] = (len(duplicate_groups), duplicate_chunk_count, len(symbol_chunks))
        if duplicate_chunk_count > DUPLICATE_CHUNK_THRESHOLD:
            failures.append(
                {
                    "pilot": name,
                    "case_id": f"alias_duplication:{name}",
                    "reason": (
                        f"{len(duplicate_groups)} duplicate-body group(s) across {duplicate_chunk_count} of "
                        f"{len(symbol_chunks)} symbol chunks exceed the >{DUPLICATE_CHUNK_THRESHOLD}-duplicate-"
                        "chunk threshold"
                    ),
                }
            )
    return failures, measurements


def test_alias_duplication_sub_case_matches_the_committed_baseline(
    corpus: ConformanceCorpus, baseline: Baseline
) -> None:
    failures, _ = _alias_duplication_observations(corpus)
    assert_no_new_failures(baseline, failures)


def test_alias_duplication_sub_case_confirms_slides_python_is_the_worst_case(
    corpus: ConformanceCorpus,
) -> None:
    """The card's own instruction: confirm (or correct) the prior research's citation of
    slides/python as the worst alias-duplication case. Measured 2026-10-30: confirmed by BOTH
    metrics - it has both the most duplicate-body groups (320) and the most duplicate-body chunks
    (764 of 1023 symbol chunks, 74.7%) of any of the 32 pilots. (3d/python and html/python each have
    a larger SINGLE group - 62 chunks sharing one body, vs. slides/python's largest group of 12 -
    but far fewer groups overall, so they do not overtake slides/python on either headline metric.)

    Re-measured G3/TC-775 (2026-11-12), after `TC-686` (I4+I5) thinned every real Python re-export
    shell's body to empty: slides/python drops to (53, 114, 1023) - a large, expected DROP (320->53
    groups, 764->114 chunks) consistent with removing the shell+definition alias pattern while
    leaving its coincidental, non-alias duplicate-body groups untouched. It is STILL the worst case
    by both metrics (confirmed directly, not assumed): no other pilot overtakes it on groups or
    chunks. 3d/python and html/python still each have the largest SINGLE group of any pilot (31
    chunks sharing one body, down from the pre-TC-686 62, since their own shell-pattern groups
    thinned too) - comfortably larger than slides/python's own largest group, now 6.
    """
    _, measurements = _alias_duplication_observations(corpus)
    worst_by_groups = max(measurements, key=lambda name: measurements[name][0])
    worst_by_chunks = max(measurements, key=lambda name: measurements[name][1])
    assert worst_by_groups == "slides__python", measurements
    assert worst_by_chunks == "slides__python", measurements
    groups, chunks, total = measurements["slides__python"]
    # TC-848: total 1023 -> 1025 (cohort C1 regeneration added the two module-level constants C15_EXT_URI and C15_NS as
    # symbol chunks); the duplicate-body groups (53) and chunks (114) are unchanged, and slides/python is still the worst case.
    assert (groups, chunks, total) == (53, 114, 1025), measurements["slides__python"]


def test_alias_duplication_sub_case_most_pilots_are_zero_or_a_handful(corpus: ConformanceCorpus) -> None:
    """The card's own expectation: "most pilots should have zero or very few" duplicate-body
    chunks. Measured 2026-10-30: 15 of the 32 pilots are at or below the threshold (9 of those have
    zero duplicate-body groups at all); this baseline exists to measure the other 17.

    Re-measured G3/TC-775 (2026-11-12), after `TC-686` (I4+I5) thinned every real Python re-export
    shell's body to empty: 19 of the 32 pilots are now at or below the threshold - a real, expected
    RISE (15->19) from removing shell+definition alias duplication in several Python pilots whose
    remaining (coincidental) duplication alone no longer exceeds it (`barcode__python`, `email__
    python`, `note__python`, `page__python`, `tex__python`). ``zero_groups`` is unchanged at 9 -
    every pilot that had zero duplicate-body groups before `TC-686` still has zero now; `TC-686`
    only shrinks pilots that already had some duplication, it never introduces a new zero.
    """
    _, measurements = _alias_duplication_observations(corpus)
    at_or_below_threshold = [
        name for name, (_, chunks, _) in measurements.items() if chunks <= DUPLICATE_CHUNK_THRESHOLD
    ]
    zero_groups = [name for name, (groups, _, _) in measurements.items() if groups == 0]
    assert len(at_or_below_threshold) == 19, sorted(at_or_below_threshold)
    assert len(zero_groups) == 9, sorted(zero_groups)


# ---- sub-case 3: the Python parse-failure undercount (offline, no network) -----------------------


def _parse_failure_undercount_observations() -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    """``(observed_failures, {pilot: {measured fields}})`` for each of the 11 real Python pilots,
    read directly from each one's committed ``tests/fixtures/<family>_python/api_surface.json`` -
    never from a network re-clone (this card is ``network: false``).
    """
    failures: list[dict[str, str]] = []
    measured: dict[str, dict[str, Any]] = {}
    for name in _python_pilot_names():
        family = name.split("__", 1)[0]
        fixture_path = REPO_ROOT / "tests" / "fixtures" / f"{family}_python" / "api_surface.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        parse_failures = fixture.get("parse_failures") or {}
        failed = parse_failures.get("failed")
        total = parse_failures.get("total")
        has_as_written_field = "as_written_failed" in parse_failures
        as_written_failed = parse_failures.get("as_written_failed")
        measured[name] = {
            "failed": failed,
            "total": total,
            "has_as_written_field": has_as_written_field,
            "as_written_failed": as_written_failed,
        }
        if (
            has_as_written_field
            and isinstance(as_written_failed, int)
            and isinstance(failed, int)
            and as_written_failed > failed
        ):
            # The fixture itself proves the undercount - an independently confirmed discrepancy.
            failures.append(
                {
                    "pilot": name,
                    "case_id": f"parse_failure_undercount:{name}",
                    "reason": (
                        f"the fixture's own as_written_failed ({as_written_failed}) exceeds its reported "
                        f"failed count ({failed}) of {total} - independently confirmed undercount"
                    ),
                }
            )
        elif isinstance(failed, int) and failed > 0 and not has_as_written_field:
            # A nonzero reported failure count the committed fixture gives no way to independently
            # cross-check offline. Per the card's own instruction: record this honestly as
            # unverifiable, never fabricate a verification not actually performed.
            failures.append(
                {
                    "pilot": name,
                    "case_id": f"parse_failure_undercount:{name}",
                    "reason": (
                        f"reports {failed} of {total} source files could not be parsed; the committed "
                        "fixture exposes no as_written_failed field, so this offline measurement cannot "
                        "independently confirm or refute whether the reported count understates the real "
                        "figure"
                    ),
                }
            )
    return failures, measured


def test_parse_failure_undercount_sub_case_matches_the_committed_baseline(baseline: Baseline) -> None:
    failures, _ = _parse_failure_undercount_observations()
    assert_no_new_failures(baseline, failures)


def test_parse_failure_undercount_sub_case_covers_the_real_eleven_python_pilots() -> None:
    """The card's own instruction: get the real Python-pilot list from
    ``config/products/*/python.yaml``, never a hardcoded guess."""
    names = _python_pilot_names()
    assert len(names) == EXPECTED_PYTHON_PILOT_COUNT, names
    assert names == sorted(names)


def test_parse_failure_undercount_sub_case_names_what_could_and_could_not_be_verified_offline() -> None:
    """The card's explicit honesty instruction: name exactly which pilots could and could not be
    independently checked. Measured 2026-10-30: all 11 fixtures were read offline (0 network calls);
    none of the 11 exposes an ``as_written_failed`` field, so 0 of 11 could be independently
    cross-checked for an as-written-vs-repaired discrepancy from committed fixture data alone. 10 of
    11 report zero parse failures (nothing to flag). Exactly one - tex/python - reports a nonzero
    count (11 of 45) with no as-written field to check it against; this is the only pilot this
    sub-case records as a finding, and its closeout says plainly that the plan's own prior-research
    citation of a real 35-of-45 figure is NOT reproduced by this card's offline measurement.
    """
    _, measured = _parse_failure_undercount_observations()
    assert len(measured) == EXPECTED_PYTHON_PILOT_COUNT, sorted(measured)

    verifiable = [name for name, info in measured.items() if info["has_as_written_field"]]
    nonzero_failed = [name for name, info in measured.items() if info["failed"]]
    # TC-841: tex/python was regenerated with the current extractor (TC-561), so its fixture now exposes as_written_failed
    # (35 of 45) next to the repaired failed count (11 of 45): exactly one of the 11 pilots can be cross-checked offline, and
    # that cross-check CONFIRMS the undercount. No other pilot may expose the field without this test and the baseline
    # being updated.
    assert verifiable == ["tex__python"], (
        f"the set of fixtures exposing as_written_failed changed - update this test and the baseline: {verifiable}"
    )
    assert nonzero_failed == ["tex__python"], nonzero_failed
    assert measured["tex__python"]["failed"] == 11
    assert measured["tex__python"]["total"] == 45
    assert measured["tex__python"]["as_written_failed"] == 35


def test_member_body_excludes_an_empty_methods_and_properties_section() -> None:
    """A unit test of this file's own ``_member_body`` helper (not a product invariant): a chunk
    with neither a Methods: nor a Properties: section renders an empty body, which every caller
    above deliberately excludes from duplicate-grouping (grouping every member-less type together
    would manufacture a false "duplicate" signal out of ordinary empty types)."""
    text = "FQN: pkg.Empty\nKind: class\n\nAPI-Coverage: 1 of 1 types"
    assert _member_body(text) == ""


def test_member_body_recovers_a_real_shared_section_from_two_unrelated_duplicate_types(
    corpus: ConformanceCorpus,
) -> None:
    """A real (non-synthetic) smoke test of ``_member_body``.

    G3/TC-775: this test's original subject - the slides/python ``Presentation`` Python re-export
    shell and its definition sharing a ``_member_body`` - no longer exists anywhere in the real
    corpus. ``TC-686`` (I4+I5, REQ-G3-012) thinned every real Python re-export shell's body to
    empty; confirmed directly (this card's own worker, by scanning all 1,379 real shell chunks
    across the 11 real Python pilots): 0 of them carry a nonempty body any more, so no
    shell+definition pair can ever again satisfy this test's original premise anywhere in the
    corpus - this is a correct, honest consequence of ``TC-686``'s already-accepted change, not a
    new regression.

    The real, still-reproducing duplicate-body case used instead: six distinct, UNRELATED
    slides/python collection-interface types (``IAdjustValueCollection``, ``IFillFormatCollection``,
    ``IExtraColorSchemeCollection``, ``ITextAnimationCollection``, ``IEffectStyleCollection``,
    ``ILineFormatCollection``) that coincidentally render the exact same trivial two-property body -
    the kind of coincidental, non-alias duplication ``_alias_duplication_observations`` above
    already measures regardless of what causes it.
    """
    pilot = corpus.pilots["slides__python"]
    target_type_names = {
        "IAdjustValueCollection",
        "IFillFormatCollection",
        "IExtraColorSchemeCollection",
        "ITextAnimationCollection",
        "IEffectStyleCollection",
        "ILineFormatCollection",
    }
    bodies_by_fqn = {}
    for chunk in pilot.symbol_chunks():
        first_line = chunk["text"].splitlines()[0]
        if first_line.rsplit(".", 1)[-1] in target_type_names:
            body = _member_body(chunk["text"])
            if body:
                bodies_by_fqn[first_line] = body
    assert len(bodies_by_fqn) >= 2, bodies_by_fqn.keys()
    assert len(set(bodies_by_fqn.values())) == 1, "these unrelated types should share one coincidental body"
