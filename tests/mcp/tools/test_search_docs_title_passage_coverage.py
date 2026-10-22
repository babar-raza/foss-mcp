"""G3/TC-572 (REQ-G3-008, REQ-G3-009): a documentation chunk's own declared title-topic is available to
every ``passage_coverage`` window, not just the physical lines near line 0.

``search_docs.passage_coverage`` (TC-562) scores a query only over ``PASSAGE_LINES`` (10) consecutive
lines of a chunk, to stop two genuinely unrelated LATER sections of a long chunk from matching by
incidental overlap. A chunk classified by its own ``FQN: Doc: <title>`` first line (TC-517) declares
that title as the topic of the WHOLE chunk, not just the lines near line 0 - so a real, natural query
naming both the title's own topic word ("dependencies") and a specific item a body list under that
title enumerates, more than ``PASSAGE_LINES`` lines below it, cleared whole-chunk coverage easily yet
failed every single window. CONFIRMED live against cells/rust ("roxmltree") and slides/python ("lxml"),
both baked in infra/demo-manifests and read directly here, read-only, no copy needed (``lookup`` never
writes to the store it reads).

Offline and deterministic: no network, no docker, no clock. The synthetic chunk mirrors the real shape
without copying it verbatim.
"""

from __future__ import annotations

from pathlib import Path

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.lookup import Miss, TaskAnswer, lookup
from foss_mcp.mcp.tools.relevance import RELEVANCE_THRESHOLD, guarded_coverage
from foss_mcp.mcp.tools.search_docs import _DOC_TITLE_PREFIX, PASSAGE_LINES, _rule_for, passage_coverage

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

# A synthetic chunk mirroring the real cells/rust shape: a title naming "Dependencies" as the WHOLE
# chunk's topic, a distinctive body term under it ("roxmltree") more than PASSAGE_LINES lines below the
# title, and a SECOND, unrelated distinctive body term ("zephyrglyph") more than PASSAGE_LINES lines
# away from "roxmltree" too - two genuinely unrelated body sections, exactly what TC-562's window was
# built to keep apart.
_TITLED_CHUNK = "\n".join(
    [
        "FQN: Doc: Installation, Dependencies, and Quick Start",
        "Follow these setup steps carefully before you begin anything major.",
        "The configuration requires a module named zephyrglyph for icon rendering.",
        "Review the licensing terms before you proceed any further today.",
        "Download the release archive from the official project site now.",
        "Extract the archive into a clean local working directory first.",
        "Verify the checksum matches the published value exactly right away.",
        "Add the binary directory to your system path variable permanently.",
        "Confirm the installation by running the version command here too.",
        "Read through the quick start guide for a minimal working example.",
        "Set the working directory to the project root before building anything.",
        "Enable verbose logging if the build output looks incomplete somehow.",
        "Check the release notes for any breaking changes before upgrading now.",
        "A supported library used here is roxmltree for fast xml parsing tasks.",
    ]
)

# The same body, with no `FQN: Doc: ` first line - the keyword-classified, hand-written-chunk shape
# TC-572 must leave untouched.
_UNTITLED_CHUNK = "\n".join(_TITLED_CHUNK.splitlines()[1:])


def _old_passage_coverage(rule, text: str) -> float:
    """The pre-TC-572 behaviour, reimplemented directly: each window scored alone, with no title fold.
    Used only to prove a chunk with no title line is unaffected by the fix."""
    lines = text.splitlines()
    line_tokens = [tokenize(line) for line in lines]
    best = 0.0
    for start in range(max(1, len(lines) - PASSAGE_LINES + 1)):
        tokens = [token for found in line_tokens[start : start + PASSAGE_LINES] for token in found]
        best = max(best, guarded_coverage(rule.terms, tokens, rule.required, rule.weights))
        if best >= 1.0:
            break
    return best


def _rule(query: str):
    rule = _rule_for(query, "demo", None)
    assert rule is not None, f"query {query!r} named no topic"
    return rule


def test_the_chunk_has_no_title_distance_matters() -> None:
    """Sanity check on the synthetic fixture itself: the title line is strictly more than PASSAGE_LINES
    lines away from the "roxmltree" body line, and "zephyrglyph" is strictly more than PASSAGE_LINES
    lines away from "roxmltree" too - otherwise this fixture would not exercise the defect at all."""
    lines = _TITLED_CHUNK.splitlines()
    title_index = 0
    roxmltree_index = next(i for i, line in enumerate(lines) if "roxmltree" in line.lower())
    zephyr_index = next(i for i, line in enumerate(lines) if "zephyrglyph" in line.lower())
    assert roxmltree_index - title_index > PASSAGE_LINES
    assert roxmltree_index - zephyr_index > PASSAGE_LINES
    assert lines[0].startswith(_DOC_TITLE_PREFIX)


def test_a_query_naming_the_title_topic_and_a_distant_body_item_now_clears_the_threshold() -> None:
    """THE FIX: "dependencies" is the title's own word; "roxmltree" sits 13 lines below it. Before
    TC-572, no single PASSAGE_LINES window held both, so this real-shaped query failed. After TC-572,
    the title's tokens are folded into every window, so the window holding "roxmltree" now also carries
    "dependencies" and clears RELEVANCE_THRESHOLD."""
    rule = _rule("installation dependencies roxmltree")
    assert passage_coverage(rule, _TITLED_CHUNK) >= RELEVANCE_THRESHOLD


def test_two_unrelated_body_terms_still_do_not_clear_the_threshold() -> None:
    """THE ANTI-REGRESSION PROOF: "zephyrglyph" and "roxmltree" are both body terms, neither is a title
    word, and they are more than PASSAGE_LINES lines apart. TC-572 folds the TITLE's tokens into every
    window - never a second body window's tokens - so this pair, which names no title word at all, must
    still fail every window exactly as before the fix. If this assertion ever passes, the fix has
    quietly widened the window generally rather than only adding the title, which would defeat TC-562's
    own original anti-dilution purpose."""
    rule = _rule("zephyrglyph roxmltree")
    assert passage_coverage(rule, _TITLED_CHUNK) < RELEVANCE_THRESHOLD


def test_a_chunk_with_no_title_line_is_completely_unaffected() -> None:
    """``title_tokens`` is empty for a chunk with no ``FQN: Doc: `` first line, so its coverage must be
    identical, line for line, to the pre-fix window-alone computation - for the query the fix helps AND
    for the query it must never help."""
    for query in ("installation dependencies roxmltree", "zephyrglyph roxmltree"):
        rule = _rule(query)
        assert passage_coverage(rule, _UNTITLED_CHUNK) == _old_passage_coverage(rule, _UNTITLED_CHUNK)


# -- the real, baked data: cells/rust and slides/python, read directly (never copied, never written) ----------------


def test_the_real_cells_rust_dependency_query_now_finds_the_real_chunk() -> None:
    """The exact regression this card exists for, against the real, committed, shipped standalone
    image's baked manifests - not a fixture, not a copy. ``REAL_DOC_TASK_QUERY`` of
    tests/e2e/test_cells_rust_live_content.py."""
    store = GenerationManifestStore(DEMO_MANIFESTS)
    scope = Scope(family="cells", platform="rust", source_kind="self_extracted")
    result = lookup(store, scope, "what installation dependencies include roxmltree")
    assert isinstance(result, TaskAnswer), (
        f"expected a TaskAnswer with real doc_matches, got a {type(result).__name__}: {result}"
    )
    assert result.doc_matches, "expected at least one real doc match, got none"


def test_the_real_slides_python_dependency_query_now_finds_the_real_chunk() -> None:
    """Same regression, the second real pilot it was confirmed on. ``REAL_DOC_TASK_QUERY`` of
    tests/e2e/test_slides_python_live_content.py."""
    store = GenerationManifestStore(DEMO_MANIFESTS)
    scope = Scope(family="slides", platform="python", source_kind="self_extracted")
    result = lookup(store, scope, "what installation dependencies include lxml")
    assert isinstance(result, TaskAnswer), (
        f"expected a TaskAnswer with real doc_matches, got a {type(result).__name__}: {result}"
    )
    assert result.doc_matches, "expected at least one real doc match, got none"


def test_the_negative_control_shape_really_was_a_miss_before_the_fix() -> None:
    """Mirrors the card's own REPRODUCE-FIRST step: the pre-fix ``_old_passage_coverage`` (window alone,
    no title fold) over the SAME real chunk text that answers the cells/rust query above is the exact
    failure this card closes - never confused with a wrong query or a wrong scope."""
    store = GenerationManifestStore(DEMO_MANIFESTS)
    scope = Scope(family="cells", platform="rust", source_kind="self_extracted")
    result = lookup(store, scope, "what installation dependencies include roxmltree")
    assert isinstance(result, TaskAnswer) and result.doc_matches
    text = result.doc_matches[0].text
    rule = _rule("what installation dependencies include roxmltree")
    assert passage_coverage(rule, text) >= RELEVANCE_THRESHOLD
    assert _old_passage_coverage(rule, text) < RELEVANCE_THRESHOLD, (
        "the real chunk no longer reproduces the pre-fix miss - the card's own premise may have changed"
    )


def test_miss_is_still_the_type_for_a_genuinely_unanswerable_query() -> None:
    """The fix is additive, never a general relaxation: a query naming nothing real still misses."""
    store = GenerationManifestStore(DEMO_MANIFESTS)
    scope = Scope(family="cells", platform="rust", source_kind="self_extracted")
    result = lookup(store, scope, "zyxqwvutpoiuybnmqqzz nonsense gibberish term")
    assert isinstance(result, Miss)
