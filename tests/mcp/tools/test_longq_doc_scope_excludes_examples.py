"""G3/TC-835 (REQ-G3-011): authored-example growth must never move the documentation IDF that
``search_docs`` and ``lookup`` weigh a query with - the drift guard behind LONGQ-001.

Root cause (measured 2026-11-14): ``relevance.doc_scope_term_weights`` counted DOCUMENTATION **and
EXAMPLE** chunks. Adding five authored ``verified_example`` chunks to pdf/net (943 -> 948) raised the weights
of ``file`` and ``reference``, lifted the generic 'Key Capabilities and API Reference' chunks' best-passage
coverage from 0.4032 to 0.4173 (over ``PARTIAL_COVERAGE_THRESHOLD`` 0.41), and the literal LONGQ-001 sentence
stopped being an honest Miss. Example chunks are a SEPARATE retrieval path (``find_examples``); they must not
move ``search_docs``' scope. This file pins that:

(a) a synthetic lexical payload: adding N example chunks leaves the default doc-scope weights unchanged;
(b) the real pdf/net LONGQ-001 literal is still a Miss from ``search_docs`` alone however many synthetic
    example chunks are added to a copy of the payload (5, 50, 500);
(c) ``find_examples``'s own weighting (``term_weights``, the whole-generation table) is untouched and the
    explicit ``(DOCUMENTATION, EXAMPLE)`` scope is still reachable.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import math
from typing import Any

import pytest

from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools import relevance
from foss_mcp.mcp.tools.relevance import (
    DOCUMENTATION,
    EXAMPLE,
    WEIGHT_CEILING,
    doc_scope_term_weights,
    term_weights,
)
from foss_mcp.mcp.tools.search_docs import Miss, search_docs
from foss_mcp.mcp.tools.search_symbols import scope_key
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus
from tests.conformance.test_sentence_wrapped_retrieval import LONGQ_001_PILOT, LONGQ_001_QUERY

_DOC_TEXT = "FQN: Doc: Some Documentation\nDocumentation body."
_EXAMPLE_TEXT = "FQN: Example: Some Example\nSample code body."
_SYMBOL_TEXT = "FQN: Namespace.Type.Method\nSymbol body."


def _payload(entries: list[tuple[list[str], str]]) -> dict[str, Any]:
    documents: dict[str, dict] = {}
    doc_freq: dict[str, int] = {}
    for index, (tokens, text) in enumerate(entries):
        documents[f"doc-{index}"] = {"tokens": tokens, "text": text}
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1
    return {"documents": documents, "doc_freq": doc_freq}


# ---- (a) synthetic payload ----------------------------------------------------------------------


@pytest.mark.parametrize("example_count", [1, 5, 50, 500])
def test_adding_example_chunks_does_not_change_the_default_doc_scope_weights(example_count: int) -> None:
    base = (
        [(["install", "file"], _DOC_TEXT)] * 3
        + [(["file"], _DOC_TEXT)] * 7
        + [(["other"], _SYMBOL_TEXT)] * 40
    )
    terms = {"install", "file", "convert"}
    before = doc_scope_term_weights(terms, _payload(base))
    grown = base + [(["install", "file", "convert"], _EXAMPLE_TEXT)] * example_count
    after = doc_scope_term_weights(terms, _payload(grown))
    assert after == before
    # and the numbers are the documentation-only arithmetic: N = 10, df(file) = 10, df(install) = 3.
    assert after["file"] == pytest.approx(math.log(1 + 10 / 11))
    assert after["install"] == pytest.approx(min(1.0, math.log(1 + 10 / 4)))
    assert after["convert"] == WEIGHT_CEILING


def test_explicit_doc_and_example_scope_is_still_reachable_and_does_move_with_examples() -> None:
    base = [(["file"], _DOC_TEXT)] * 10
    both = (DOCUMENTATION, EXAMPLE)
    without = doc_scope_term_weights({"file"}, _payload(base), both)
    with_examples = doc_scope_term_weights({"file"}, _payload(base + [(["file"], _EXAMPLE_TEXT)] * 40), both)
    assert without["file"] == pytest.approx(math.log(1 + 10 / 11))
    assert with_examples["file"] == pytest.approx(math.log(1 + 50 / 51))
    assert with_examples["file"] > without["file"]


# ---- (b) the real pdf/net LONGQ-001 literal ------------------------------------------------------


class _GrownStore:
    """The two read calls ``search_docs`` makes, over a copy of the real generation with *extra* synthetic
    example chunks appended to its lexical payload (``doc_freq`` untouched: only the chunk count grows)."""

    def __init__(self, store: Any, scope: Any, extra_examples: int) -> None:
        self._store = store
        self._key = scope_key(scope, "self_extracted")
        self._extra = extra_examples

    def read_active(self, key: Any) -> Any:
        return self._store.read_active(key)

    def read_generation(self, key: Any, generation_id: Any) -> Any:
        manifest = self._store.read_generation(key, generation_id)
        payload = copy.deepcopy(manifest.payload)
        documents = payload["lexical_index"]["documents"]
        for index in range(self._extra):
            documents[f"synthetic-example-{index}"] = {
                "chunk_id": f"synthetic-example-{index}",
                "text": f"FQN: Example: Synthetic Example {index}\nConvert a file reference.\n```\nvar x = 1;\n```",
                "tokens": ["convert", "file", "reference", "synthetic", "example", "install", "start"],
            }
        return dataclasses.replace(manifest, payload=payload)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("longq-doc-scope-corpus"))


@pytest.mark.parametrize("extra_examples", [0, 5, 50])
def test_longq_001_literal_stays_a_miss_whatever_number_of_example_chunks_are_added(
    corpus: ConformanceCorpus, extra_examples: int
) -> None:
    scope = corpus.pilots[LONGQ_001_PILOT].scope
    store = _GrownStore(corpus.store, scope, extra_examples)
    result = search_docs(store, scope, LONGQ_001_QUERY)  # type: ignore[arg-type]
    assert isinstance(result, Miss), (
        f"{extra_examples} synthetic example chunks flipped LONGQ-001 off an honest Miss: {result!r}"
    )


# ---- (c) find_examples is unchanged -------------------------------------------------------------


def test_find_examples_still_weighs_with_the_whole_generation_term_weights() -> None:
    source = inspect.getsource(find_examples_module)
    assert "doc_scope_term_weights" not in source
    assert "term_weights" in source
    # A fixed payload: N = 6 chunks, df(alpha) = 3, df(beta) = 1, df(gamma) = 0 -> byte-identical weights.
    payload = _payload(
        [(["alpha", "beta"], _EXAMPLE_TEXT), (["alpha"], _EXAMPLE_TEXT), (["alpha"], _DOC_TEXT)]
        + [(["other"], _SYMBOL_TEXT)] * 3
    )
    weights = term_weights({"alpha", "beta", "gamma"}, payload)
    assert weights == {
        "alpha": min(1.0, max(0.5, math.log(1 + 6 / 4))),
        "beta": min(1.0, max(0.5, math.log(1 + 6 / 2))),
        "gamma": relevance.WEIGHT_CEILING,
    }
