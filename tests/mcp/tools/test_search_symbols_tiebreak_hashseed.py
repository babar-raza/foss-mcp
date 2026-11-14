"""TC-793 (G3/REQ-G3-017, H8): ``_safe_extra_generic`` ranks its candidate stems deterministically.

The candidates are a ``frozenset``; ranking them by the per-scope fraction ALONE left two equal-fraction
stems in frozenset iteration order, which depends on ``PYTHONHASHSEED`` - so the greedy selection that
follows (which protects the last strong term of the query) could pick a different subset on every process
start. The ranking key now ends in the stem itself.

The scenario forces every candidate to tie (``family_generic_fraction`` pinned to one constant) with four
real strong terms and a scope whose own documents carry each of them, then asserts the result is identical
across ``PYTHONHASHSEED`` 0..7 in fresh subprocesses, and that equal-fraction stems are chosen in stem order.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from foss_mcp.mcp.tools import relevance
from foss_mcp.mcp.tools import search_symbols as ss

REPO_ROOT = Path(__file__).resolve().parents[3]
SEEDS = tuple(range(8))
WORDS = ["gamma", "delta", "epsilon", "zeta", "omega", "kappa"]

_SCENARIO = """
import json
from foss_mcp.mcp.tools import relevance, search_symbols as ss
relevance.family_generic_fraction = lambda stem, payload: 0.5
docs = {f"d{i}": {"text": f"FQN: Sample.Type{i}\\nKind: class_declaration\\n"} for i in range(40)}
words = ["gamma", "delta", "epsilon", "zeta", "omega", "kappa"]
payload = {"documents": docs, "doc_freq": {w: 20 for w in words}}
print(json.dumps(sorted(ss._safe_extra_generic(words, "sample", payload))))
"""


def _run_with_seed(seed: int) -> list[str]:
    env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(REPO_ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, "-c", _SCENARIO],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_equal_fraction_ties_resolve_identically_across_hash_seeds() -> None:
    results = {seed: _run_with_seed(seed) for seed in SEEDS}
    assert len({tuple(chosen) for chosen in results.values()}) == 1, results
    # Four ties, six strong terms: the greedy selection keeps all but the last in stem order.
    assert results[0] == ["delta", "epsilon", "gamma", "kappa", "omega"]


def test_equal_fraction_stems_come_back_in_stem_order(monkeypatch) -> None:
    monkeypatch.setattr(relevance, "family_generic_fraction", lambda stem, payload: 0.5)
    docs = {f"d{i}": {"text": f"FQN: Sample.Type{i}\nKind: class_declaration\n"} for i in range(40)}
    payload = {"documents": docs, "doc_freq": {word: 20 for word in WORDS}}

    chosen = ss._safe_extra_generic(WORDS, "sample", payload)

    # The alphabetically last strong stem is the one the greedy selection leaves unexcluded.
    assert chosen == frozenset(sorted(WORDS)[:-1])


def test_a_higher_fraction_still_ranks_before_a_lower_one(monkeypatch) -> None:
    fractions = {"gamma": 0.2, "delta": 0.9, "epsilon": 0.5, "zeta": 0.4, "omega": 0.3, "kappa": 0.6}
    monkeypatch.setattr(relevance, "family_generic_fraction", lambda stem, payload: fractions[stem])
    docs = {f"d{i}": {"text": f"FQN: Sample.Type{i}\nKind: class_declaration\n"} for i in range(40)}
    payload = {"documents": docs, "doc_freq": {word: 20 for word in WORDS}}

    chosen = ss._safe_extra_generic(WORDS, "sample", payload)

    # Most-ubiquitous-first is unchanged: the single least-ubiquitous stem is the one left unexcluded.
    assert chosen == frozenset(WORDS) - {"gamma"}
