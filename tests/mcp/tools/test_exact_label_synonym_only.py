"""G3/TC-856 (REQ-G3-011, RD2-230): the MEASURED limit of a "synonym-only hit is not ``exact``" rule.

RD2-230: ``lookup('transition')`` on slides/cpp answers ``EffectFormat`` labelled ``exact`` although the word
"transition" occurs nowhere in that type (it is admitted through the synonym "effect" alone). TC-823 found that
the obvious rule - a query term must occur literally in the candidate - also flips correct rank-1 answers
("add a slide" on four slides pilots) and left the label in place. This card (RW-14) searched for a narrower
rule that separates the two classes, measured against the 729-case retrieval corpus, and found NONE:

- the signature "labelled ``exact`` by the evidence stage AND no distinctive query term occurs literally in
  the candidate's chunk" (the card's own proposal: the term is not in the name, members, docs or example
  titles) is carried by the wrong answer AND by plainly correct ones: ``deck`` -> ``Presentation`` (slides),
  ``picture`` -> ``Image`` (pdf), ``insert a picture`` -> ``Image``/``XImage``, ``add a link to a website`` ->
  ``Hyperlink`` (cells), ``add a cube`` -> ``Box`` (3d), ``add a slide`` -> ``Slide`` (slides);
- narrowing it further (only a hit the synonym table alone admitted; only a name the synonym explains less
  than fully; only a one-word query) keeps flipping correct corpus answers, or - for the one-word restriction -
  flips none ONLY because the corpus holds no one-word synonym query at all, which is no measurement
  (AGENTS.md: a decision only one branch can reach is not a measurement), while the real pilots answer
  ``deck`` -> ``Presentation`` and ``picture`` -> ``Image`` through exactly that path.

So no rule that keeps every currently correct ``exact`` answer exists, and none is shipped: RD2-230 is a
measured limitation. These tests pin that measurement. They are deliberately a regression lock in BOTH
directions: if a future change makes the transition label honest, the wrong-answer pin fails and must be
updated together with proof that every control below (and the ratchets in
``test_round3_retrieval_corpus.py``) still holds.

Offline and deterministic: the committed ``infra/demo-manifests`` copied to a temp dir; the embedding
gateway is never configured.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest

import tests.mcp.tools.test_round3_retrieval_corpus as retrieval
from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.relevance import distinctive_terms, stem_token
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, SymbolMatch
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

EVIDENCE_TIER = "evidence_sentence"


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(retrieval._ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(retrieval._API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def pilots(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("synonym-only-pilots"))


def _symbols(answer: object) -> list[SymbolMatch]:
    if isinstance(answer, TaskAnswer):
        return list(answer.symbols)
    if not isinstance(answer, list):  # a Miss carries no symbols
        return []
    return [match for match in answer if isinstance(match, SymbolMatch)]


def _short(match: SymbolMatch) -> str:
    return (extract_fqn(match.text) or "").strip().rsplit(".", 1)[-1].rsplit("::", 1)[-1]


def _no_literal_term(match: SymbolMatch, query: str, scope: Scope) -> bool:
    """The card's candidate signature: NO distinctive word of the query occurs (stemmed) in the candidate's
    chunk - its name, its members or its prose."""
    terms = distinctive_terms(query, scope.family, platform=scope.platform)
    return not (terms & {stem_token(token) for token in tokenize(match.text)})


def _is_flippable(match: SymbolMatch, query: str, scope: Scope) -> bool:
    """Would the candidate rule demote this hit: labelled ``exact`` by the evidence stage with no literal term."""
    return (
        match.match_quality == MATCH_EXACT
        and match.match_kind == EVIDENCE_TIER
        and _no_literal_term(match, query, scope)
    )


def _named(pilots: ConformanceCorpus, pilot: str, query: str, name: str) -> tuple[SymbolMatch, Scope]:
    scope = pilots.pilots[pilot].scope
    found = {_short(match): match for match in _symbols(lookup(pilots.store, scope, query))}
    assert name in found, (pilot, query, sorted(found))
    return found[name], scope


# ---- the reproduction: RD2-230 is real and is pinned as measured --------------------------------------------


def test_slides_cpp_transition_is_labelled_exact_through_the_synonym_alone(pilots: ConformanceCorpus) -> None:
    """RD2-230, reproduced: ``EffectFormat`` is the first answer, ``exact``, admitted by the evidence stage,
    and the query word occurs nowhere in the chunk."""
    scope = pilots.pilots["slides__cpp"].scope
    first = _symbols(lookup(pilots.store, scope, "transition"))[0]
    assert _short(first) == "EffectFormat"
    assert first.match_quality == MATCH_EXACT
    assert first.match_kind == EVIDENCE_TIER
    assert _no_literal_term(first, "transition", scope)


# ---- the controls: the same signature on answers that are RIGHT ----------------------------------------------

# (pilot, query, the correct answer's short name) - every one is ``exact``, evidence-tier and carries no
# literal query term, exactly like the ``transition`` answer above; a rule keyed on that signature flips them.
CONTROLS = (
    ("slides__cpp", "add a slide", "Slide"),
    ("slides__java", "add a slide", "Slide"),
    ("slides__net", "add a slide", "Slide"),
    ("slides__python", "add a slide", "Slide"),
    ("slides__cpp", "deck", "Presentation"),
    ("slides__java", "deck", "Presentation"),
    ("pdf__go", "picture", "Image"),
    ("pdf__net", "picture", "Image"),
    ("3d__java", "add a cube", "Box"),
)


@pytest.mark.parametrize(("pilot", "query", "name"), CONTROLS)
def test_a_correct_exact_answer_carries_the_same_signature_as_the_transition_answer(
    pilots: ConformanceCorpus, pilot: str, query: str, name: str
) -> None:
    match, scope = _named(pilots, pilot, query, name)
    assert match.match_quality == MATCH_EXACT
    assert _is_flippable(match, query, scope), (
        f"{pilot} {query!r} -> {name}: a rule keyed on 'no literal term, evidence tier' would NOT flip this "
        "control any more - the signature may now separate the classes; re-measure the whole corpus"
    )


# ---- the corpus: the candidate rule flips correct answers, so none ships -------------------------------------


@pytest.fixture(scope="module")
def corpus_flips(tmp_path_factory: pytest.TempPathFactory) -> list[tuple[str, str, str, bool]]:
    """Every ``exact`` evidence-tier symbol with no literal query term that lookup returns over the whole
    729-case corpus: ``(pilot, task id, short name, is_intended)``."""
    root = tmp_path_factory.mktemp("synonym-only-corpus")
    for directory in retrieval.DEMO_MANIFESTS.glob("*__self_extracted"):
        if directory.is_dir():
            shutil.copytree(directory, root / directory.name)
    corpus = retrieval.Corpus(root)
    flips: list[tuple[str, str, str, bool]] = []
    for case_pilot, query, task_id, specs in _corpus_cases(corpus):
        scope = corpus.pilots[case_pilot].scope
        for match in _symbols(lookup(corpus.store, scope, query)):
            if _is_flippable(match, query, scope):
                item = retrieval.make_item("symbol", match.text)
                flips.append(
                    (case_pilot, task_id, _short(match), any(retrieval.satisfies(item, s) for s in specs))
                )
    return flips


def _corpus_cases(corpus: retrieval.Corpus) -> Iterator[tuple[str, str, str, list[str]]]:
    for family in retrieval.FAMILIES:
        document = retrieval.load_family(family)
        for task in document["tasks"]:
            for pilot, specs in retrieval.applicable(corpus, document, task):
                yield pilot, task["query"], task["id"], specs


def test_the_candidate_rule_would_flip_correct_answers_of_the_corpus(
    corpus_flips: list[tuple[str, str, str, bool]],
) -> None:
    correct = [flip for flip in corpus_flips if flip[3]]
    # Measured at TC-856: 96 such hits over the corpus, 29 of them the intended answer of 22 (pilot, task) cases.
    assert len(corpus_flips) >= 90, len(corpus_flips)
    assert len(correct) >= 25, len(correct)
    # The held-out 'deck' slides task, 'insert a picture', 'add a cube' and 'add a link to a website' are among
    # the correct answers the rule would turn into ``partial``.
    flipped_tasks = {(pilot, task_id) for pilot, task_id, _name, intended in correct if intended}
    assert {("slides__cpp", "h-s04"), ("pdf__go", "p12"), ("3d__java", "t03"), ("cells__python", "c06")} <= (
        flipped_tasks
    )
