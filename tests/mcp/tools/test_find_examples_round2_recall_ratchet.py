"""G3/TC-810 (REQ-G3-011): a FROZEN recall ratchet for ``find_examples`` over the round-2 audit's own queries.

``tests/fixtures/conformance/round2_find_examples_recall.json`` holds the 316 distinct (pilot, query) pairs
parsed from the ``find_examples`` repro commands of ``docs/round2/issues.json`` - the queries real
auditors typed against the real pilots. Each pair is answered here by the real ``find_examples`` over a
copy of the committed ``infra/demo-manifests`` (lexical stage only: the embedding gateway is replaced by
one that raises, as in the standalone image with no gateway).

Before TC-810 the authored-example campaign had pushed 22 of 32 pilots past the rare-code-word scope cap
and 108 of the 316 pairs answered; TC-810 restored 132. The pairs that answered are frozen as
``must_hit`` and must keep answering - the set may only GROW (flip a pair to true in the fixture after a
measured improvement, never to false to make a regression pass). The second pin guards the other
direction: how many of the answers do not literally hold every query word in their title or code must not
rise, so recall cannot be bought with unexplained answers.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import ExampleMatch, NoExampleFound, find_examples
from foss_mcp.mcp.tools.vector_guard import _set_guard

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "conformance" / "round2_find_examples_recall.json"

# The recall measured by TC-810 (``must_hit`` pairs in the fixture) and the count it started from.
FROZEN_MUST_HIT = 132
BASELINE_HITS_BEFORE_TC810 = 108
TOTAL_PAIRS = 316

_STOP = frozenset("a an the and or of to in on for with how do i my is it from as at by".split())


class _NoGatewayEmbeddingProvider:
    """What the standalone image has when no embedding gateway is configured: embedding fails."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


def _terms(query: str) -> list[str]:
    """The query's literal words, lower-cased - the oracle's own (deliberately simpler than the matcher's)."""
    query = re.sub(r"^Example:\s*", "", query)
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+", query)
        if token.lower() not in _STOP and len(token) > 1
    ]


def _holds_every_word(match: ExampleMatch, terms: list[str]) -> bool:
    text = f"{match.fqn} {match.snippet}".lower()
    return all(re.search(r"(?<![a-z0-9])" + re.escape(term), text) for term in terms)


def _load_pairs() -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["pairs"]


@pytest.fixture(scope="module")
def answers(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], list[ExampleMatch] | None]:
    """(pilot, query) -> the matches find_examples answered with (``None`` for an honest miss)."""
    root = tmp_path_factory.mktemp("round2_recall")
    stores: dict[str, GenerationManifestStore] = {}
    original = find_examples_module.GatewayEmbeddingProvider
    find_examples_module.GatewayEmbeddingProvider = _NoGatewayEmbeddingProvider  # type: ignore[misc]
    _set_guard(None)
    found: dict[tuple[str, str], list[ExampleMatch] | None] = {}
    try:
        for pair in _load_pairs():
            pilot, query = str(pair["pilot"]), str(pair["query"])
            directory = f"{pilot}__self_extracted"
            if directory not in stores:
                shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
                stores[directory] = GenerationManifestStore(root)
            family, platform = pilot.split("__", 1)
            result = find_examples(stores[directory], Scope(family=family, platform=platform), query, top_k=5)
            found[(pilot, query)] = None if isinstance(result, NoExampleFound) else result
    finally:
        find_examples_module.GatewayEmbeddingProvider = original  # type: ignore[misc]
        _set_guard(None)
    return found


def test_the_fixture_holds_the_316_round_two_pairs_and_only_ever_grows_its_must_hit_set() -> None:
    pairs = _load_pairs()
    assert len(pairs) == TOTAL_PAIRS
    assert len({(pair["pilot"], str(pair["query"]).lower()) for pair in pairs}) == TOTAL_PAIRS
    assert len({pair["pilot"] for pair in pairs}) == 32
    must_hit = sum(1 for pair in pairs if pair["must_hit"])
    assert must_hit >= FROZEN_MUST_HIT, "the must-hit set may only grow"
    assert must_hit > BASELINE_HITS_BEFORE_TC810


def test_every_must_hit_pair_still_answers(answers: dict[tuple[str, str], list[ExampleMatch] | None]) -> None:
    lost = [
        (pair["pilot"], pair["query"])
        for pair in _load_pairs()
        if pair["must_hit"] and not answers[(str(pair["pilot"]), str(pair["query"]))]
    ]
    assert not lost, f"{len(lost)} round-2 queries that used to answer are a miss again: {lost}"


def test_the_recall_over_the_round_two_pairs_does_not_fall(
    answers: dict[tuple[str, str], list[ExampleMatch] | None],
) -> None:
    hits = sum(1 for matches in answers.values() if matches)
    assert hits >= FROZEN_MUST_HIT, hits


def test_answers_that_do_not_hold_every_query_word_do_not_multiply(
    answers: dict[tuple[str, str], list[ExampleMatch] | None],
) -> None:
    """Recall must not be bought with unexplained answers: of the pairs that answer, how many return
    examples none of which holds every query word (in its title or code) may not exceed the frozen count."""
    budget = json.loads(FIXTURE.read_text(encoding="utf-8"))["max_unexplained_hits"]
    unexplained = [
        key
        for key, matches in answers.items()
        if matches and not any(_holds_every_word(match, _terms(key[1])) for match in matches)
    ]
    assert len(unexplained) <= budget, (len(unexplained), budget, unexplained)
