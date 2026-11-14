"""G3/TC-608 (REQ-G3-010): F8, the reference-content conformance invariant.

Measures, against all 32 real baked pilots, three already-confirmed defects in
``get_product_reference`` (src/foss_mcp/mcp/tools/get_product_reference.py):

1. a formats/limitations/install/agent_guidance answer can be truncated at an arbitrary character
   boundary (a mid-cut, severing a word rather than landing on a clean line/paragraph break) -
   fence-awareness and a real-offset locator on the marker were both fixed by G3/TC-678, so only
   the arbitrary-character-boundary possibility itself remains a live sub-case here;
2. an "install" answer can carry more than one, sometimes contradictory, install recipe (this is
   most visible on the cpp platform, whose generated source-build guidance is followed by the
   repository's own README installation section, verbatim but for CMake-target correction - a
   README that itself documents more than one installation method then surfaces as more than one
   fenced recipe block);
3. a section that comes back empty (``NotAvailable``) gives no reason distinguishing "upstream
   genuinely has none" from "not extracted" - ``NotAvailable`` carries only a free-text ``reason``
   string, never a structured field, so EVERY ``NotAvailable`` answer counts here by construction.

HISTORY, superseded (G3/TC-809): this module was written when all 32 baked product-reference
sidecars (``infra/demo-manifests/product_reference_<family>_<platform>.json``) predated G3/TC-561 and
carried no ``readme_document`` or ``manifest_path`` key, so the README-fallback path those fields feed
was dead. G3/TC-786/787/788 regenerated all 32 sidecars and every one now carries both keys (checked
directly below, in ``test_real_sidecars_carry_readme_document_and_manifest_path``); the README-fallback
path is live and most "formats"/"limitations" answers that used to be NotAvailable now return content.

Call shape (mirrors the production call site exactly - ``_live_get_product_reference`` in
src/foss_mcp/mcp/server.py): ``ProductReferenceInputs`` comes from
``infra.fetch_product_reference.load_manifest_sidecar`` reading the real, committed sidecar JSON
for each pilot's identity; ``readme`` comes from ``read_readme_sections(store, scope)`` - the exact
same ``GenerationManifestStore`` ``tests/conformance/corpus.py`` builds over the copied
``*__self_extracted`` pilot directories - for every section production itself resolves README
sections for (``server._README_SECTIONS``), ``None`` otherwise. The one thing this module does NOT
replicate is ``_ActiveGeneration.install_inputs``'s rollback-commit correction (G3/TC-564): that
only matters when a sidecar's commit is not among the active generation's own commits, a transient
serving-time condition this static, baked-pilot measurement cannot exercise either way.

Truncation ground truth: rather than guess from the final answer text (several call sites prepend
further text - an import WARNING, a contributor-guidance label - AFTER capping, which would throw
off any attempt to infer the raw cut point from the finished string), this module monkeypatches the
tool's own ``_cap_at_line``/``_cap_at_paragraph`` for the duration of the measurement and records,
at the exact moment either is actually invoked with text longer than its cap, whether the SAME
boundary search those functions themselves perform (``rfind("\\n")`` / ``rfind("\\n\\n")`` then
``rfind("\\n")``) found a real line/paragraph break inside the capped window. No break found, and
the character the raw slice ends on is not whitespace - that is an arbitrary, mid-token cut
(a "mid-cut" failure). A break WAS found (a clean cut) is never a failure. (G3/TC-678 fixed the
marker itself - every real cap hit now names the real N/M character counts via
``_truncation_marker``/``_fence_snap`` - so a truncation that occurs at all is no longer, by
itself, also a "marker-lacks-locator" failure; only a mid-cut is measured as a failure now.
``_TRUNCATED_MARKER``, the old bare-string constant with no chunk_id/offset of any kind, is kept
only for this module's own ground-truth unit tests below, not for the real marker production
emits.)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import foss_mcp.mcp.tools.get_product_reference as gpr
import infra.fetch_product_reference as fetch_product_reference
from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.mcp.server import _README_SECTIONS
from foss_mcp.mcp.tools.get_product_reference import (
    SECTIONS,
    NotAvailable,
    ReferenceContent,
    Section,
    get_product_reference,
)
from foss_mcp.mcp.tools.readme_sections import read_readme_sections
from tests.conformance.baseline import assert_no_new_failures, load_baseline
from tests.conformance.corpus import (
    DEMO_MANIFESTS,
    ConformanceCorpus,
    load_demo_manifests_corpus,
)

EXPECTED_PILOT_COUNT = 32

BASELINE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "conformance" / "baseline_reference_content.json"
)

# A fenced code block, lang tag optional, body captured.
_FENCE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

# A line that itself acquires/registers a package - the real signal "an install step happened here",
# not merely "this block mentions the package" (a usage-only block, e.g. a bare find_package +
# target_link_libraries pair with no clone/FetchContent/registry command, does not match and so does
# not count as its own recipe - it is the continuation half of the block that DID install something).
_RECIPE_SIGNAL_RE = re.compile(
    r"\binstall\b|\badd\s+package\b|PackageReference|<dependency>|<artifactId>|\brequire\b|\bnpm\s+i\b"
    r"|FetchContent_Declare|FetchContent_MakeAvailable|pip\s+install|conda\s+install|cargo\s+add"
    r"|yarn\s+add|go\s+get|Install-Package|nuget\s+install",
    re.IGNORECASE,
)


def _install_recipe_block_count(text: str) -> int:
    """How many of *text*'s fenced code blocks each independently look like a standalone install
    recipe (``_RECIPE_SIGNAL_RE`` matches somewhere in the block) - a block that only shows how to
    USE an already-installed library (e.g. a bare ``find_package``/``target_link_libraries`` pair,
    the second half of the cpp "broken embedding" guidance) is a continuation, not its own recipe,
    and does not match."""
    return sum(1 for body in _FENCE_BLOCK_RE.findall(text) if _RECIPE_SIGNAL_RE.search(body))


# G3/TC-698: the disclosure sentence `_cpp_install` (get_product_reference.py) now appends to
# `notice` whenever its own, independently-implemented `_count_install_recipe_blocks` finds more
# than one recipe block - "This install answer combines N distinct install routes: ...". Used both
# to tell sub-case 2 below an "undisclosed" multi-recipe answer from a disclosed one, and by this
# module's own new tests to check the disclosed count against this module's independent oracle.
_DISCLOSED_ROUTE_COUNT_RE = re.compile(r"combines\s+(\d+)\s+distinct install routes", re.IGNORECASE)


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    """The real, committed ``infra/demo-manifests`` loaded through a temp-directory copy - nothing in
    the repo is ever written to; reading the real baked data is always allowed."""
    root = tmp_path_factory.mktemp("tc608-reference-content-corpus")
    return load_demo_manifests_corpus(root)


@dataclass(frozen=True)
class _SectionAnswer:
    pilot: str
    section: Section
    result: Any  # ReferenceContent | NotAvailable


@dataclass(frozen=True)
class _CapCall:
    """One real, observed invocation of ``_cap_at_line``/``_cap_at_paragraph`` with text longer than
    its cap - captured from inside the real functions themselves, at the exact instant they decide
    whether a clean line/paragraph boundary exists inside the capped window, so this is never a
    guess reconstructed from the finished answer text."""

    pilot: str
    section: Section
    cap: int
    mid_cut: bool


@dataclass(frozen=True)
class Measurement:
    answers: tuple[_SectionAnswer, ...]
    cap_calls: tuple[_CapCall, ...]
    failures: tuple[dict[str, str], ...]


def _patch_cap_functions(
    monkeypatch: pytest.MonkeyPatch, cap_calls: list[_CapCall], current: dict[str, str]
) -> None:
    """Wrap the real ``_cap_at_line``/``_cap_at_paragraph`` (read in full before writing this test)
    so every call with text longer than its cap is recorded, tagged with whatever (pilot, section)
    *current* holds at call time - the caller updates *current* right before each
    ``get_product_reference`` call. The wrapped functions still call straight through to the
    originals: observed behaviour is never altered, only watched."""
    original_cap_at_line = gpr._cap_at_line
    original_cap_at_paragraph = gpr._cap_at_paragraph

    def cap_at_line(text: str, cap: int) -> str:
        if len(text) > cap:
            cut = text[:cap]
            boundary = cut.rfind("\n")
            mid_cut = boundary <= 0 and bool(cut) and cut[-1].isalnum()
            cap_calls.append(_CapCall(current["pilot"], current["section"], cap, mid_cut))
        return original_cap_at_line(text, cap)

    def cap_at_paragraph(text: str, cap: int) -> str:
        if len(text) > cap:
            cut = text[:cap]
            boundary = cut.rfind("\n\n")
            if boundary <= 0:
                boundary = cut.rfind("\n")
            mid_cut = boundary <= 0 and bool(cut) and cut[-1].isalnum()
            cap_calls.append(_CapCall(current["pilot"], current["section"], cap, mid_cut))
        return original_cap_at_paragraph(text, cap)

    monkeypatch.setattr(gpr, "_cap_at_line", cap_at_line)
    monkeypatch.setattr(gpr, "_cap_at_paragraph", cap_at_paragraph)


def _collect_answers(
    corpus: ConformanceCorpus,
    cap_calls: list[_CapCall],
    current: dict[str, str],
    setup_failures: list[dict[str, str]],
) -> tuple[_SectionAnswer, ...]:
    """Every (pilot, section) answer for all 32 real pilots, calling the real
    ``get_product_reference`` exactly as production's ``_live_get_product_reference`` does.

    A pilot whose sidecar is missing or unloadable is recorded as a soft failure in
    *setup_failures* (``sidecar-missing:<pilot>`` / ``sidecar-unloadable:<pilot>``) and skipped,
    never asserted here: this runs inside the module-scoped ``measured`` fixture, ahead of
    ``assert_no_new_failures``, so a bare assert would ERROR every dependent test instead of
    letting the baseline comparison see it (G3/TC-809, the shape G3/TC-773/774/775 fixed in the
    sibling conformance modules)."""
    answers: list[_SectionAnswer] = []
    for pilot_name in sorted(corpus.pilots):
        pilot = corpus.pilots[pilot_name]
        sidecar_path = DEMO_MANIFESTS / fetch_product_reference.sidecar_name(
            pilot.scope.family, pilot.scope.platform
        )
        if not sidecar_path.is_file():
            setup_failures.append(
                {
                    "pilot": pilot_name,
                    "case_id": f"sidecar-missing:{pilot_name}",
                    "reason": f"no sidecar for real baked pilot {pilot_name}: {sidecar_path}",
                }
            )
            continue
        inputs = fetch_product_reference.load_manifest_sidecar(sidecar_path)
        if inputs is None:
            setup_failures.append(
                {
                    "pilot": pilot_name,
                    "case_id": f"sidecar-unloadable:{pilot_name}",
                    "reason": f"load_manifest_sidecar returned None for {sidecar_path}",
                }
            )
            continue
        for section in SECTIONS:
            current["pilot"], current["section"] = pilot_name, section
            readme = read_readme_sections(corpus.store, pilot.scope) if section in _README_SECTIONS else None
            result = get_product_reference(inputs, section, readme=readme)
            answers.append(_SectionAnswer(pilot_name, section, result))
    return tuple(answers)


def _build_failures(
    answers: tuple[_SectionAnswer, ...], cap_calls: tuple[_CapCall, ...]
) -> tuple[dict[str, str], ...]:
    """The combined, deduplicated failure set from all three sub-cases, each entry shaped as the
    baseline contract (``tests/conformance/baseline.py``) expects: ``{"pilot", "case_id", "reason"}``."""
    failures: dict[tuple[str, str], dict[str, str]] = {}

    def record(pilot: str, case_id: str, reason: str) -> None:
        failures[(pilot, case_id)] = {"pilot": pilot, "case_id": case_id, "reason": reason}

    # Sub-case 1: truncation. A cap hit with no clean boundary found is a mid-cut. (G3/TC-678 made
    # every real cap hit's marker name the real N/M character counts via its own fence-aware
    # _fence_snap/_truncation_marker machinery, so the former "every cap hit also lacks a locator"
    # failure this loop used to record unconditionally is no longer true and has been retired.)
    for call in cap_calls:
        if call.mid_cut:
            record(
                call.pilot,
                f"mid-cut:{call.pilot}:{call.section}",
                f"{call.section} was cut at an arbitrary character boundary (cap={call.cap}): no "
                "clean line/paragraph break existed inside the capped window, so the cut severed a "
                "word rather than landing on whitespace or punctuation.",
            )

    # Sub-case 2 (install only): more than one distinct, complete fenced install-recipe block, with
    # no disclosure that more than one route exists. G3/TC-698: `_cpp_install` now appends a
    # disclosure sentence to `notice` whenever its own, independently-implemented recipe-block
    # count exceeds one, so a real multi-recipe install answer is no longer a failure by itself -
    # only an UNDISCLOSED one is (reusing `reconcile_format_claims`'s "disclose, never silently
    # resolve" pattern, extended here to the install section's own multi-recipe case).
    for answer in answers:
        if answer.section != "install" or not isinstance(answer.result, ReferenceContent):
            continue
        count = _install_recipe_block_count(answer.result.text)
        if count > 1 and _DISCLOSED_ROUTE_COUNT_RE.search(answer.result.notice or "") is None:
            record(
                answer.pilot,
                f"multi-recipe:{answer.pilot}:install",
                f"the install answer carries {count} distinct fenced install-recipe blocks, not one, "
                "with no disclosure in its notice that more than one route exists.",
            )

    # Sub-case 3: empty/absent with no structured reason. NotAvailable carries only a free-text
    # ``reason`` string - never a field distinguishing "upstream genuinely has none" from "not
    # extracted" - so every NotAvailable answer qualifies by construction; a blank ReferenceContent
    # (never observed in the real 32-pilot sweep, but checked for completeness) qualifies the same way.
    for answer in answers:
        result = answer.result
        is_empty = isinstance(result, NotAvailable) or (
            isinstance(result, ReferenceContent) and not result.text.strip()
        )
        if not is_empty:
            continue
        reason_text = result.reason if isinstance(result, NotAvailable) else "(blank ReferenceContent.text)"
        record(
            answer.pilot,
            f"empty-no-reason:{answer.pilot}:{answer.section}",
            f"{answer.section} answered absent/empty with only the free-text reason {reason_text!r}; "
            "NotAvailable (src/foss_mcp/mcp/tools/get_product_reference.py) has no structured field "
            "distinguishing 'upstream genuinely has none' from 'not extracted'.",
        )

    return tuple(sorted(failures.values(), key=lambda entry: (entry["pilot"], entry["case_id"])))


def _measure(corpus: ConformanceCorpus, monkeypatch: pytest.MonkeyPatch) -> Measurement:
    cap_calls: list[_CapCall] = []
    current: dict[str, str] = {"pilot": "", "section": ""}
    _patch_cap_functions(monkeypatch, cap_calls, current)
    setup_failures: list[dict[str, str]] = []
    answers = _collect_answers(corpus, cap_calls, current, setup_failures)
    failures = tuple(
        sorted(
            (*_build_failures(answers, tuple(cap_calls)), *setup_failures),
            key=lambda entry: (entry["pilot"], entry["case_id"]),
        )
    )
    return Measurement(answers=answers, cap_calls=tuple(cap_calls), failures=failures)


@pytest.fixture(scope="module")
def measured(corpus: ConformanceCorpus) -> Measurement:
    # A module-scoped fixture cannot depend on the function-scoped ``monkeypatch`` fixture, so this
    # uses pytest.MonkeyPatch() directly (public API since pytest 6) and undoes it itself.
    mp = pytest.MonkeyPatch()
    try:
        return _measure(corpus, mp)
    finally:
        mp.undo()


# ---- (a) the real sidecars, read directly - the TC-561 staleness context, confirmed not cited ----


def test_every_real_pilot_has_a_product_reference_sidecar() -> None:
    sidecars = sorted(path.name for path in DEMO_MANIFESTS.glob("product_reference_*.json"))
    assert len(sidecars) == EXPECTED_PILOT_COUNT, sidecars


def test_real_sidecars_carry_readme_document_and_manifest_path() -> None:
    """Direct confirmation, from the committed sidecar JSON itself, that TC-561's
    ``readme_document``/``manifest_path`` fields are present in every real sidecar. (G3/TC-809:
    this test formerly asserted every sidecar LACKED them - true before G3/TC-786/787/788
    regenerated all 32 sidecars, false today, so that pin was stale.) A sidecar that loses either
    field again fails here, naming it."""
    sidecars = sorted(DEMO_MANIFESTS.glob("product_reference_*.json"))
    assert len(sidecars) >= 3
    missing = [
        path.name
        for path in sidecars
        if "readme_document" not in (payload := json.loads(path.read_text(encoding="utf-8")))
        or "manifest_path" not in payload
    ]
    assert missing == [], (
        f"expected ALL {len(sidecars)} real sidecars to carry readme_document and manifest_path "
        f"(regenerated by G3/TC-786/787/788); these lack one or both: {missing}"
    )


def test_truncated_marker_carries_no_chunk_id_or_offset_locator_today() -> None:
    """Verified directly against the CURRENT source, not assumed from prior research: the
    truncation marker is a fixed, bare string - no chunk_id, no offset, no locator of any kind."""
    marker = gpr._TRUNCATED_MARKER
    assert marker == "[truncated; use search_docs for the rest]"
    assert re.search(r"chunk|offset|#\d|:\d+\b", marker, re.IGNORECASE) is None, marker


# ---- (b) the measurement mechanism itself, proven against synthetic text ------------------------


def test_cap_at_line_mid_cut_ground_truth_matches_a_clean_vs_arbitrary_boundary() -> None:
    """A direct, synthetic proof that the real ``_cap_at_line`` behaves the two ways this module's
    mid-cut detector distinguishes: a text with a newline inside the capped window is cut cleanly
    (no marker-adjacent word severed); a text with no newline at all is cut exactly at the raw
    character boundary, landing mid-word."""
    clean = "first line\n" + "x" * 40
    assert gpr._cap_at_line(clean, 15) == f"first line\n{gpr._truncation_marker(10, len(clean))}"
    no_boundary = "a" * 20
    cut = gpr._cap_at_line(no_boundary, 15)
    assert cut == f"{'a' * 15}\n{gpr._truncation_marker(15, len(no_boundary))}"
    assert cut.split("\n", 1)[0][-1].isalnum()  # the exact condition this module calls "mid-cut"


def test_cap_at_paragraph_mid_cut_ground_truth_matches_a_clean_vs_arbitrary_boundary() -> None:
    clean = "para one.\n\n" + "y" * 40
    assert gpr._cap_at_paragraph(clean, 15) == f"para one.\n\n{gpr._truncation_marker(9, len(clean))}"
    no_boundary = "b" * 20
    cut = gpr._cap_at_paragraph(no_boundary, 15)
    assert cut == f"{'b' * 15}\n\n{gpr._truncation_marker(15, len(no_boundary))}"
    assert cut.split("\n", 1)[0][-1].isalnum()


def test_install_recipe_block_counter_ignores_a_usage_only_continuation_block() -> None:
    """The exact cpp "broken embedding" shape (src/foss_mcp/mcp/tools/get_product_reference.py's
    ``_cpp_build_guidance``): a build-and-install bash block followed by a find_package/
    target_link_libraries USAGE block. The usage block names no install command and must not be
    double-counted as a second recipe on its own."""
    text = (
        "```bash\ngit clone https://example.invalid/x.git\ncmake --install build --prefix out\n```\n\n"
        "```cmake\nfind_package(x CONFIG REQUIRED)\ntarget_link_libraries(app PRIVATE x::x)\n```"
    )
    assert _install_recipe_block_count(text) == 1


def test_install_recipe_block_counter_counts_two_real_install_blocks() -> None:
    text = "```bash\npip install one\n```\n\n```bash\nconda install one\n```"
    assert _install_recipe_block_count(text) == 2


# ---- (c) the real, measured sweep over all 32 pilots x 8 sections --------------------------------


def test_every_measured_answer_is_content_or_an_explicit_absence(measured: Measurement) -> None:
    assert len(measured.answers) == EXPECTED_PILOT_COUNT * len(SECTIONS)
    for answer in measured.answers:
        assert isinstance(answer.result, (ReferenceContent, NotAvailable)), (answer.pilot, answer.section)


def test_measured_failures_cover_the_subcases_this_card_actually_found(measured: Measurement) -> None:
    """A meta-check against a vacuously-passing measurement: at least the sub-cases this card's real
    run actually found (empty-no-reason always fired; mid-cut measured zero today - see this
    module's own docstring) must be represented, so a future refactor that accidentally stops
    exercising a sub-case is caught here rather than only showing up as a shrinking, unexplained
    baseline. (marker-lacks-locator used to always fire here too, but G3/TC-678 fixed the marker to
    name real N/M character counts, so that sub-case no longer applies and its assertion was
    retired along with the failure it used to record. multi-recipe used to always fire on
    email__cpp/pdf__cpp/slides__cpp too, but G3/TC-698 made `_cpp_install` disclose the real
    recipe-block count via `notice` whenever it exceeds one, so sub-case 2 here now only fires on an
    UNDISCLOSED multi-recipe answer - never observed in the real 32-pilot corpus after that fix -
    and its assertion was retired here too, along with the failures it used to record; see
    `test_cpp_multi_recipe_install_answer_discloses_the_real_route_count` below for the real,
    positive proof that disclosure now happens instead.)"""
    prefixes = {
        case_id.split(":", 1)[0]
        for _pilot, case_id, _reason in ((f["pilot"], f["case_id"], f["reason"]) for f in measured.failures)
    }
    assert "empty-no-reason" in prefixes, measured.failures
    assert "multi-recipe" not in prefixes, measured.failures


def test_reference_content_matches_the_committed_baseline(measured: Measurement) -> None:
    baseline = load_baseline(BASELINE_PATH)
    assert_no_new_failures(baseline, measured.failures)


# ---- (d) G3/TC-698: the install section's own multi-recipe disclosure ----------------------------


@pytest.mark.parametrize(
    "pilot_name,expected_count",
    [("email__cpp", 2), ("pdf__cpp", 2), ("slides__cpp", 3)],
)
def test_cpp_multi_recipe_install_answer_discloses_the_real_route_count(
    corpus: ConformanceCorpus, pilot_name: str, expected_count: int
) -> None:
    """On each of the 3 real cpp pilots whose README documents more than one install method on top
    of the generated source-build recipe (this card's own re-confirmed measurement), the real,
    end-to-end ``get_product_reference`` "install" answer's ``.notice`` now states a concrete count
    of distinct install routes - and that count agrees with BOTH this module's own independent
    ``_install_recipe_block_count`` oracle and production's separately-implemented
    ``_count_install_recipe_blocks``, computed on the exact same final text, even though the two
    counters are never the same code."""
    pilot = corpus.pilots[pilot_name]
    sidecar_path = DEMO_MANIFESTS / fetch_product_reference.sidecar_name(
        pilot.scope.family, pilot.scope.platform
    )
    assert sidecar_path.is_file(), sidecar_path
    inputs = fetch_product_reference.load_manifest_sidecar(sidecar_path)
    assert inputs is not None
    readme = read_readme_sections(corpus.store, pilot.scope)
    result = get_product_reference(inputs, "install", readme=readme)
    assert isinstance(result, ReferenceContent), (pilot_name, result)
    assert result.notice is not None, (pilot_name, result)

    oracle_count = _install_recipe_block_count(result.text)
    assert oracle_count == expected_count, (pilot_name, oracle_count)
    production_count = gpr._count_install_recipe_blocks(result.text)
    assert production_count == oracle_count, (pilot_name, production_count, oracle_count)

    match = _DISCLOSED_ROUTE_COUNT_RE.search(result.notice)
    assert match is not None, result.notice
    assert int(match.group(1)) == oracle_count, (pilot_name, match.group(1), oracle_count)


def test_cpp_install_answer_discloses_its_route_count_whenever_it_exceeds_one(
    corpus: ConformanceCorpus,
) -> None:
    """``cells__cpp``'s install answer. (G3/TC-809: this test formerly pinned ``cells__cpp`` to
    exactly 1 fenced install-recipe block and no disclosure - stale, its install answer now yields 3
    blocks.) The invariant is the one the disclosure exists for: whenever the count exceeds 1 the
    notice MUST carry the TC-698 "combines N distinct install routes" sentence with the right N;
    only a genuinely single-route answer gets no added disclosure. A missing disclosure at a count
    above 1 still fails this test."""
    pilot_name = "cells__cpp"
    pilot = corpus.pilots[pilot_name]
    sidecar_path = DEMO_MANIFESTS / fetch_product_reference.sidecar_name(
        pilot.scope.family, pilot.scope.platform
    )
    assert sidecar_path.is_file(), sidecar_path
    inputs = fetch_product_reference.load_manifest_sidecar(sidecar_path)
    assert inputs is not None
    readme = read_readme_sections(corpus.store, pilot.scope)
    result = get_product_reference(inputs, "install", readme=readme)
    assert isinstance(result, ReferenceContent), (pilot_name, result)
    assert result.notice is not None, (pilot_name, result)

    oracle_count = _install_recipe_block_count(result.text)
    assert oracle_count >= 1, (pilot_name, oracle_count, "an install answer with no recipe block at all")
    assert gpr._count_install_recipe_blocks(result.text) == oracle_count, pilot_name
    match = _DISCLOSED_ROUTE_COUNT_RE.search(result.notice)
    if oracle_count > 1:
        assert match is not None, (
            pilot_name,
            oracle_count,
            "multi-recipe answer lacks the disclosure",
            result.notice,
        )
        assert int(match.group(1)) == oracle_count, (pilot_name, match.group(1), oracle_count)
    else:
        assert match is None, result.notice
