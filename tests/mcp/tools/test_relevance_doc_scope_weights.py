"""G3/TC-625 (Q5, REQ-G3-011): ``doc_scope_term_weights`` - a new, pure, DORMANT IDF-weight
function scoped to doc/example chunks only, closing the root mechanism behind the originally
reported LONGQ-001 failure: ``term_weights``'s own ``doc_freq`` table is a GLOBAL,
whole-generation aggregate, so a documentation-specific word ("install") ends up weighing the
same as a near-universal symbol-chunk word ("file") of comparable raw frequency.

This card builds and unit-tests the new function in ISOLATION ONLY: it is not called from
``search_docs``, ``lookup``, ``find_examples`` or anywhere else real (see ``DOC_SCOPE_WEIGHTS_ENABLED``
in ``relevance.py`` and this symbol's entry in ``tests/test_unwired_modules.py``'s
``_KNOWN_UNWIRED``) - wiring and the measurement-gated enable decision are Q6's separate job.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from foss_mcp.mcp.tools.relevance import (
    DOC_SCOPE_WEIGHTS_ENABLED,
    DOCUMENTATION,
    EXAMPLE,
    SYMBOL,
    WEIGHT_CEILING,
    WEIGHT_FLOOR,
    WEIGHT_KNOWN_CEILING,
    chunk_kind,
    doc_scope_term_weights,
    stem_token,
    term_weights,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PDF_NET_MANIFEST_DIR = REPO_ROOT / "infra" / "demo-manifests" / "pdf__net__self_extracted"

# Chunk texts that drive chunk_kind's own classification (see relevance.chunk_kind): no FQN line
# at all means DOCUMENTATION; an FQN line starting "Example: " means EXAMPLE; any other FQN line
# means SYMBOL.
_DOC_TEXT = ""
_EXAMPLE_TEXT = "FQN: Example: Some Example\nSample code body."
_SYMBOL_TEXT = "FQN: Namespace.Type.Method\nSymbol body."

# TC-835: ``doc_scope_term_weights`` now defaults to DOCUMENTATION chunks only (example chunks are a separate
# retrieval path and must not move search_docs' IDF). The TC-625 tests below pin the original doc+example
# arithmetic, so they ask for that scope explicitly; the new default is pinned by
# tests/mcp/tools/test_longq_doc_scope_excludes_examples.py and the default-scope test further down.
_DOC_AND_EXAMPLE_KINDS = (DOCUMENTATION, EXAMPLE)


def _lexical_payload(entries: list[tuple[list[str], str]]) -> dict[str, object]:
    """A lexical payload shaped like ``build_lexical_index``'s, where *entries* pairs each
    document's own raw token list with its own chunk text (the text controls ``chunk_kind``;
    the tokens control what each document's own stemmed token set carries)."""
    documents: dict[str, dict] = {}
    doc_freq: dict[str, int] = {}
    for index, (tokens, text) in enumerate(entries):
        documents[f"doc-{index}"] = {"tokens": tokens, "text": text}
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1
    return {"documents": documents, "doc_freq": doc_freq}


def _real_pdf_net_lexical_payload() -> dict[str, object]:
    """The real, committed ``pdf/net`` pilot's own lexical-index payload, read straight from
    ``infra/demo-manifests`` - no ingestion, no mocks, exactly as ``build_lexical_index`` wrote it."""
    pointer = json.loads((PDF_NET_MANIFEST_DIR / "active_pointer.json").read_text(encoding="utf-8"))
    suffix = pointer["generation_id"].rsplit("::", 1)[-1]
    generation_file = PDF_NET_MANIFEST_DIR / f"generation__{suffix}.json"
    manifest = json.loads(generation_file.read_text(encoding="utf-8"))
    return manifest["payload"]["lexical_index"]


# ---- (a) a synthetic doc/example-only corpus -------------------------------------------------


def test_doc_scope_weights_floor_and_ceiling_on_a_pure_doc_example_corpus() -> None:
    """Every chunk is doc/example (text="" -> DOCUMENTATION); term A is in every one of the 40,
    term B in none - mirrors term_weights' own
    test_weights_are_clipped_to_0_5_and_2_0_and_deterministic shape, scoped to doc/example only."""
    entries = [(["alpha", "filler"], _DOC_TEXT) for _ in range(40)]
    payload = _lexical_payload(entries)
    weights = doc_scope_term_weights({"alpha", "beta"}, payload)

    assert set(weights) == {"alpha", "beta"}
    assert all(WEIGHT_FLOOR <= weight <= WEIGHT_CEILING for weight in weights.values()), weights
    # A in every doc/example chunk (df 40 of N 40): ln(1 + 40 / 41), the same floor-end value
    # term_weights' own test pins for a term known by the whole generation.
    assert weights["alpha"] == pytest.approx(math.log(1 + 40 / 41))
    # B never appears in any doc/example chunk (df 0): the unseen ceiling.
    assert weights["beta"] == WEIGHT_CEILING


def test_doc_scope_weights_count_example_chunks_the_same_as_documentation_chunks() -> None:
    """EXAMPLE chunks count towards N and df exactly like DOCUMENTATION chunks - the card's own
    "DOCUMENTATION or EXAMPLE" scope, not DOCUMENTATION alone."""
    entries = [(["alpha"], _DOC_TEXT)] * 5 + [(["alpha"], _EXAMPLE_TEXT)] * 5
    payload = _lexical_payload(entries)
    weights = doc_scope_term_weights({"alpha"}, payload, kinds=_DOC_AND_EXAMPLE_KINDS)
    assert weights["alpha"] == pytest.approx(math.log(1 + 10 / 11))


def test_doc_scope_weights_default_scope_is_documentation_only() -> None:
    """TC-835: with no *kinds* argument, EXAMPLE chunks do not count towards N or df - 5 documentation chunks
    holding "alpha" weigh ln(1 + 5/6) whatever number of example chunks sit beside them."""
    expected = math.log(1 + 5 / 6)
    for example_count in (0, 5, 50):
        entries = [(["alpha"], _DOC_TEXT)] * 5 + [(["alpha", "beta"], _EXAMPLE_TEXT)] * example_count
        weights = doc_scope_term_weights({"alpha", "beta"}, _lexical_payload(entries))
        assert weights["alpha"] == pytest.approx(expected)
        assert weights["beta"] == WEIGHT_CEILING


# ---- (b) a MIXED corpus: doc/example chunks AND many symbol chunks ---------------------------


def test_doc_scope_weights_diverge_from_term_weights_on_a_mixed_corpus() -> None:
    """ "common" is carried by nearly every SYMBOL chunk (200 of 220) but by only one of the 20
    doc/example chunks - the exact MIXED-corpus shape the plan's row-1 diagnosis describes: a
    documentation-specific word must not weigh the same as a near-universal symbol-chunk word.
    doc_scope_term_weights must weigh it HIGH (doc-scope-rare); term_weights, on the SAME
    payload, must weigh it LOW (whole-generation-common) - this concrete A/B contrast is the
    proof this card's whole premise is real, not assumed.
    """
    doc_example_entries = [(["common"], _DOC_TEXT)] + [(["other"], _EXAMPLE_TEXT) for _ in range(19)]
    symbol_entries = [(["common"], _SYMBOL_TEXT) for _ in range(199)] + [(["other"], _SYMBOL_TEXT)]
    payload = _lexical_payload(doc_example_entries + symbol_entries)

    # Sanity on the corpus shape itself: 220 chunks total, 20 of them doc/example.
    documents = payload["documents"]
    assert len(documents) == 220
    assert sum(1 for d in documents.values() if chunk_kind(d["text"]) in (DOCUMENTATION, EXAMPLE)) == 20
    assert sum(1 for d in documents.values() if chunk_kind(d["text"]) == SYMBOL) == 200

    whole = term_weights({"common", "other"}, payload)
    scoped = doc_scope_term_weights({"common", "other"}, payload, kinds=_DOC_AND_EXAMPLE_KINDS)

    # term_weights, over the WHOLE 220-chunk generation (df 200 of 220): correctly LOW, close to
    # the floor of what the generation "knows".
    assert whole["common"] == pytest.approx(math.log(1 + 220 / 201))
    assert whole["common"] < 0.75
    # doc_scope_term_weights, scoped to the 20 doc/example chunks only (df 1 of 20): correctly
    # HIGH, saturating the known ceiling.
    assert scoped["common"] == pytest.approx(WEIGHT_KNOWN_CEILING)
    assert scoped["common"] > whole["common"]

    # "other" is the mirror case, present in 19 of the 20 doc/example chunks (common WITHIN the
    # doc-scope itself) - its doc-scope weight is genuinely computed, not merely capped, so it
    # pins the real ln(1 + N / (1 + df)) value rather than coincidentally landing on the known
    # ceiling the way "common"'s doc-scope value above does.
    assert scoped["other"] == pytest.approx(math.log(1 + 20 / 20))


# ---- (c) the real LONGQ-001-relevant case: a real, committed pdf/net generation --------------


def test_real_pdf_net_pilot_doc_scope_gap_is_wider_than_whole_generation_gap() -> None:
    """The real reproduction of the plan's own row-1 diagnosis, against the real, committed
    pdf/net pilot (948 chunks, 25 of them documentation). Real measured numbers (TC-835 re-measured
    them on the current 948-chunk generation and moved the scope to documentation chunks only):

    - whole-generation df: "install" 6, "file" 179 (out of 948; 180 before TC-849) - term_weights' own
      ``ln(1 + N / (1 + df))`` SATURATES BOTH at the known ceiling (1.0), so the whole-generation
      gap between them is 0.0 - exactly the LONGQ-001 failure mode (a doc-specific word weighs
      the same as a near-universal symbol-chunk word).
    - doc-scope df (within the 25 documentation chunks only): "install" 2, "file" 16 -
      doc_scope_term_weights gives "install" the known ceiling (1.0) and "file" ~0.9045, a real,
      measured gap of ~0.0955 - WIDER than term_weights' own whole-generation gap of 0.0, proving
      this card's premise on real data, not an invented example.

    Do not weaken this test to match the plan's illustrative "df 5 vs df 163" figures more
    precisely than the real data supports - the real numbers are what is asserted here.
    """
    payload = _real_pdf_net_lexical_payload()
    documents = payload["documents"]
    install_stem = stem_token("install")
    file_stem = stem_token("file")
    terms = {install_stem, file_stem}

    # Pin the real fixture's own shape so this test fails loudly (not silently) if
    # infra/demo-manifests' pdf/net generation is ever regenerated differently underneath it.
    assert len(documents) == 948
    doc_example_documents = [d for d in documents.values() if chunk_kind(d["text"]) == DOCUMENTATION]
    assert len(doc_example_documents) == 25

    whole_generation_install_df = sum(
        count for token, count in payload["doc_freq"].items() if stem_token(token) == install_stem
    )
    whole_generation_file_df = sum(
        count for token, count in payload["doc_freq"].items() if stem_token(token) == file_stem
    )
    assert whole_generation_install_df == 6
    # TC-849 re-pin 180 -> 179: cohort C2 regenerated pdf/net; its authored "Extract Text Fragments from a Saved Page" example
    # replaced the README example of the same subject, which read a file ("document.pdf") and so held the word "file"; the
    # chunk count (948) and the documentation chunks (25) are unchanged.
    assert whole_generation_file_df == 179

    doc_scope_install_df = sum(
        1 for d in doc_example_documents if install_stem in {stem_token(t) for t in d["tokens"]}
    )
    doc_scope_file_df = sum(
        1 for d in doc_example_documents if file_stem in {stem_token(t) for t in d["tokens"]}
    )
    assert doc_scope_install_df == 2
    assert doc_scope_file_df == 16

    whole = term_weights(terms, payload)
    scoped = doc_scope_term_weights(terms, payload)

    whole_gap = whole[install_stem] - whole[file_stem]
    scoped_gap = scoped[install_stem] - scoped[file_stem]

    assert whole[install_stem] == pytest.approx(1.0)
    assert whole[file_stem] == pytest.approx(1.0)
    assert whole_gap == pytest.approx(0.0)

    assert scoped[install_stem] == pytest.approx(1.0)
    assert scoped[file_stem] == pytest.approx(0.9044562742271522, abs=1e-6)
    assert scoped_gap == pytest.approx(0.09554372577284775, abs=1e-6)

    # The whole point of this card: the doc-scope gap is real and WIDER than the whole-generation
    # gap for the exact same two terms on the exact same real generation.
    assert scoped_gap > whole_gap


# ---- (d) the zero-doc/example-chunks edge case ------------------------------------------------


def test_zero_doc_example_chunks_makes_every_term_weigh_the_ceiling() -> None:
    """A generation with no documentation or example chunks at all (N == 0): every term must
    weigh WEIGHT_CEILING - an absent scope means every term is "unseen" by it, never a
    division-by-zero or an arbitrary default - regardless of whether the term appears in the
    (symbol-only) chunks that do exist."""
    entries = [(["common"], _SYMBOL_TEXT) for _ in range(10)]
    payload = _lexical_payload(entries)

    weights = doc_scope_term_weights({"common", "absent"}, payload)

    assert weights == {"common": WEIGHT_CEILING, "absent": WEIGHT_CEILING}


# ---- purity, determinism and the dormant marker -----------------------------------------------


def test_doc_scope_term_weights_is_pure_and_deterministic() -> None:
    entries = [(["common", "rare" if i == 0 else "filler"], _DOC_TEXT) for i in range(20)]
    payload = _lexical_payload(entries)
    terms = {"common", "rare", "absent"}

    first = doc_scope_term_weights(terms, payload)
    second = doc_scope_term_weights(terms, payload)

    assert first == second
    assert set(first) == terms
    assert all(WEIGHT_FLOOR <= weight <= WEIGHT_CEILING for weight in first.values())


def test_doc_scope_weights_enabled_marker_is_true_and_live() -> None:
    """DOC_SCOPE_WEIGHTS_ENABLED is a documentation/handoff marker, now flipped live by Q6
    (TC-626: search_docs and lookup now genuinely call doc_scope_term_weights) - it must now
    read True, and doc_scope_term_weights' own behaviour must still not depend on it (the
    function always computes the real doc-scope weights; nothing gates its body on this flag)."""
    assert DOC_SCOPE_WEIGHTS_ENABLED is True

    entries = [(["alpha"], _DOC_TEXT) for _ in range(5)]
    payload = _lexical_payload(entries)
    # Flipping the module-level flag (then restoring it) must not change the function's output -
    # it genuinely reads only its own two arguments, exactly like term_weights.
    import foss_mcp.mcp.tools.relevance as relevance_module

    before = doc_scope_term_weights({"alpha"}, payload)
    relevance_module.DOC_SCOPE_WEIGHTS_ENABLED = False
    try:
        after = doc_scope_term_weights({"alpha"}, payload)
    finally:
        relevance_module.DOC_SCOPE_WEIGHTS_ENABLED = True
    assert before == after
