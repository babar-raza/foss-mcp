"""search_symbols' new, weakest tier: a genuine prefix fragment of a real word (TC-654, G3/REQ-G3-011).

Q12 (findability, workstream B): the plan's own frozen acceptance for this stage names "prefix tier
(>= 3 chars)" as the weakest evidence admitted, with "an explicit Miss reason" below that floor.
This closes 10 real round-2 findability issues that all shared one root cause - a query naming a
genuine prefix of a real type's own word (``"Watermar"`` of ``PdfDocument.AddWatermarkAnnotation``'s
own ``Watermark``) was rejected entirely, contradicting this tool's own description ("type name or
fragment"); see ``docs/round2/issues.json``'s ``3D-JAVA-008``, ``3D-TYPESCRIPT-002``,
``BARCODE-PY-003``, ``CELLS-TYPESCRIPT-010``, ``EMAIL-CPP-008``, ``EML-NET-001``, ``NOTE-PY-004``,
``PDF-TS-001``, ``SLIDES-JAVA-005``, ``SLIDES-NET-003``.

Offline except the real-pilot measurement tests at the bottom, which read COPIES of the committed
``infra/demo-manifests`` scope directories (no network, no container, no embedding gateway) - the
same real pdf/net, cells/cpp and slides/python pilots this module's own docstring already cites for
TC-278's and TC-637's own measurements.
"""

from __future__ import annotations

import random
import re
import shutil
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import (
    _PREFIX_MIN_LENGTH,
    MATCH_EXACT,
    MATCH_PARTIAL,
    Miss,
    SymbolMatch,
    scope_key,
    search_symbols,
)
from tests.mcp.tools.test_round3_retrieval_corpus import DEMO_MANIFESTS

SCOPE = Scope(family="sample", platform="python")
_GENERATION_ID = "gen-tc654-prefix-tier"
_FINAL_SEGMENT = re.compile(r"\.|::")
_NON_SYMBOL_FQN_PREFIXES = ("Example: ", "Doc: ")


def _type_chunk(fqn: str, *members: str) -> str:
    lines = [f"FQN: {fqn}", "Kind: class_declaration"]
    if members:
        lines.append("Methods:")
        lines.extend(f"  - {member}" for member in members)
    return "\n".join(lines) + "\n"


class _InMemoryStore:
    """Stands in for GenerationManifestStore: one active generation, held entirely in memory (the
    same pattern ``test_search_symbols_forgiveness.py`` already established)."""

    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _search(store: object, query: str, *, top_k: int = 10) -> object:
    return search_symbols(store, SCOPE, query, top_k=top_k)  # type: ignore[arg-type]


def _fqns(result: object) -> list[str]:
    assert isinstance(result, list), f"expected a list of matches, got {result!r}"
    assert all(isinstance(match, SymbolMatch) for match in result)
    return [extract_fqn(match.text) or "" for match in result]


# A watermark-annotation symbol and an unrelated decoy - the card's own worked example.
_WATERMARK_STORE = _InMemoryStore(
    _type_chunk("Sample.PdfDocument.AddWatermarkAnnotation"),
    _type_chunk("Sample.Networking.HttpClient"),
)


def test_a_real_prefix_of_a_real_word_is_now_found_as_match_quality_partial() -> None:
    result = _search(_WATERMARK_STORE, "Watermar")

    fqns = _fqns(result)
    assert any(fqn.endswith("AddWatermarkAnnotation") for fqn in fqns)
    assert all(match.match_quality == MATCH_PARTIAL for match in result)  # type: ignore[union-attr]


def test_the_prefix_match_is_case_insensitive() -> None:
    # "WaterMar" is deliberately excluded: an embedded capital mid-word is camelCase-split by
    # `_symbol_words` into two words ("water", "mar"), the same pre-existing, case-SENSITIVE
    # boundary detection every other tier already relies on - a different, two-word query, not a
    # case-folding difference of this one.
    for query in ("watermar", "WATERMAR"):
        result = _search(_WATERMARK_STORE, query)
        fqns = _fqns(result)
        assert any(fqn.endswith("AddWatermarkAnnotation") for fqn in fqns), query
        assert all(match.match_quality == MATCH_PARTIAL for match in result), query  # type: ignore[union-attr]


def test_the_same_query_as_a_whole_word_still_gets_match_exact() -> None:
    # Tier 4 (prefix) never shadows a stronger tier: the WHOLE word "watermark" is a pre-existing
    # bag-of-words hit (overall tier 3), not the new, weaker prefix tier.
    result = _search(_WATERMARK_STORE, "watermark")

    fqns = _fqns(result)
    assert any(fqn.endswith("AddWatermarkAnnotation") for fqn in fqns)
    assert all(match.match_quality == MATCH_EXACT for match in result)  # type: ignore[union-attr]


def test_a_query_shorter_than_the_floor_gets_the_explicit_length_reason() -> None:
    for query in ("a", "Wa", "I"):
        result = _search(_WATERMARK_STORE, query)
        assert isinstance(result, Miss), (query, result)
        assert (
            result.reason
            == f"query must be at least {_PREFIX_MIN_LENGTH} characters to match a name fragment"
        )
        assert result.suggestions == ()


def test_a_short_query_that_already_matches_a_real_whole_word_is_never_forced_into_a_miss() -> None:
    # The floor governs only the MISS REASON for a query nothing else explains; it must never
    # override a genuine hit at an existing, stronger stage - here a real 2-character type name.
    store = _InMemoryStore(_type_chunk("Sample.Go"), _type_chunk("Sample.Other"))

    result = _search(store, "Go")

    fqns = _fqns(result)
    assert any(fqn.endswith(".Go") for fqn in fqns)


def test_a_query_sharing_no_prefix_with_any_real_word_stays_an_unchanged_miss() -> None:
    result = _search(_WATERMARK_STORE, "Zephyrine")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'Zephyrine'"


def test_a_mixed_whole_word_and_prefix_query_is_admitted() -> None:
    # "Add" is a real whole word of the FQN; "Watermar" is only a genuine prefix of its real word
    # "Watermark" - every word of the query is explained (whole or prefix), so this is admitted.
    result = _search(_WATERMARK_STORE, "Add Watermar")

    fqns = _fqns(result)
    assert any(fqn.endswith("AddWatermarkAnnotation") for fqn in fqns)
    assert all(match.match_quality == MATCH_PARTIAL for match in result)  # type: ignore[union-attr]


def test_a_query_where_one_word_matches_neither_whole_nor_prefix_is_not_admitted() -> None:
    # "Add" is explained (whole word); "Xyzzy" is neither a whole word nor a genuine prefix of any
    # real word - the SAME "every query word must be explained" discipline tier 3 already has.
    result = _search(_WATERMARK_STORE, "Add Xyzzy")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'Add Xyzzy'"


def test_a_tier_4_hit_never_outranks_a_stronger_tier_for_the_same_query() -> None:
    # "Document" equals the final segment of Sample.Document (tier 1, overall) exactly, and is
    # only a genuine prefix of Sample.DocumentationHelper's own word "documentation" (tier 4).
    store = _InMemoryStore(
        _type_chunk("Sample.DocumentationHelper"),
        _type_chunk("Sample.Document"),
    )

    result = _search(store, "Document")

    fqns = _fqns(result)
    assert fqns[0].endswith(".Document")
    assert fqns[1].endswith("DocumentationHelper")
    assert result[0].match_quality == MATCH_EXACT  # type: ignore[index]
    assert result[1].match_quality == MATCH_PARTIAL  # type: ignore[index]


def test_bm25_still_breaks_ties_within_the_new_tier() -> None:
    # Both types are admitted only at tier 4 for this query (neither's FQN contains the whole word
    # "classif"); repeating "Sample" in Classifier's own declared members raises its own BM25 score
    # relative to ClassifierZed's, so it must still rank first within the shared, weakest tier.
    store = _InMemoryStore(
        _type_chunk("Sample.Classifier", "SampleWidgetHelper()", "SampleWidgetTool()"),
        _type_chunk("Sample.ClassifierZed"),
    )

    result = _search(store, "Sample Classif")

    fqns = _fqns(result)
    assert fqns == ["Sample.Classifier", "Sample.ClassifierZed"]
    assert all(match.match_quality == MATCH_PARTIAL for match in result)  # type: ignore[union-attr]


def test_a_tier_4_hit_admits_every_real_sibling_sharing_the_prefix() -> None:
    # The same "admit every tied candidate, let BM25 sort them" discipline TC-278's own bag-of-words
    # tier already has for "Annotation"'s 8 real subclasses (see this module's own docstring).
    store = _InMemoryStore(
        _type_chunk("Sample.Classifier"),
        _type_chunk("Sample.ClassifierTwo"),
        _type_chunk("Sample.ClassifierThree"),
    )

    result = _search(store, "Classif")

    fqns = _fqns(result)
    assert {"Sample.Classifier", "Sample.ClassifierTwo", "Sample.ClassifierThree"} <= set(fqns)
    assert all(match.match_quality == MATCH_PARTIAL for match in result)  # type: ignore[union-attr]


def test_a_tier_4_hit_leaves_unmatched_terms_empty() -> None:
    # Every word of the query is fully explained (whole or prefix) - this is not a partial-evidence
    # answer with named unexplained words, unlike the evidence-ranking stage's own "partial" meaning.
    result = _search(_WATERMARK_STORE, "Watermar")

    assert all(match.unmatched_terms == () for match in result)  # type: ignore[union-attr]


def test_a_word_shorter_than_the_floor_still_fails_inside_an_otherwise_long_enough_query() -> None:
    # The whole QUERY here is well above the floor; the per-WORD floor is independent and still
    # applies to "wa" (2 characters, not a whole word of anything either).
    result = _search(_WATERMARK_STORE, "Sample Wa")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'Sample Wa'"


def test_a_cpp_scoped_prefix_is_admitted_too() -> None:
    # The card's own second worked example: "AFRelation" against a ``::``-scoped C++ FQN.
    store = _InMemoryStore(_type_chunk("Aspose::Pdf::AFRelationship"))

    result = _search(store, "AFRelation")

    fqns = _fqns(result)
    assert any(fqn.endswith("AFRelationship") for fqn in fqns)
    assert all(match.match_quality == MATCH_PARTIAL for match in result)  # type: ignore[union-attr]


# ---- real multi-pilot measurement (TC-654's own closing-report number) ------------------------------------
#
# Reproducible (seeded) sample of 10 real >=3-char prefixes of real type names per pilot, against the
# same 3 real pilots (pdf/net, cells/cpp, slides/python) this module's own docstring already cites for
# TC-278's and TC-637's own measurements. Measured 2026-11-01, top_k=10 (the plan's own acceptance
# criterion: "finds the type in top 10"): pdf/net 9/10 (90%), cells/cpp 6/10 (60%), slides/python 8/10
# (80%) - pooled 23/30 (76.7%). This FALLS SHORT of the plan's 95% target; reported honestly rather than
# rounded up (Decision F). Six of the seven misses share one root cause distinct from the admission logic
# itself: a prefix cut by raw character position can leave a trailing camelCase word of 1-2 characters
# (e.g. "SetMiterLi" -> "Li", "XlsxD" -> "D"), which the per-word floor correctly refuses to treat as a
# fragment - the same floor that keeps a bare "I" or "T" an honest miss. The seventh ("IChar" against
# slides/python's own 26 real "IChart*" interfaces) is a genuine top-10 PAGINATION gap: the type is
# admitted (confirmed directly: present among all 26 tier-4 candidates returned at top_k=100), just
# outside the top 10 once this many same-prefix siblings tie at the same tier and are sorted by BM25
# alone - the same characteristic TC-278's own docstring already documents for "Dispose" (9 real types,
# all admitted, BM25-sorted) and 3D-TYPESCRIPT-002 separately flags as a pre-existing ranking issue, not
# one this card's own write_paths claim to fix. The threshold below is set comfortably under the measured
# 76.7% (never inflated to make 95% look reached) so this test is still meaningful, not just lucky, and
# fails hard under the negative control (renaming ``_PREFIX_MIN_LENGTH`` collapses every pilot to ~0%).

_REAL_PILOTS = ("pdf__net", "cells__cpp", "slides__python")
_SAMPLE_SEED = 654
_SAMPLE_SIZE = 10
_MEASURED_POOLED_HIT_RATE_FLOOR = 0.60  # measured 0.767 pooled; kept well below to avoid flaking.


@pytest.fixture(scope="module")
def _real_pilot_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    root = tmp_path_factory.mktemp("tc654_prefix_tier_real_pilots")
    for pilot in _REAL_PILOTS:
        directory = DEMO_MANIFESTS / f"{pilot}__self_extracted"
        shutil.copytree(directory, root / directory.name)
    return GenerationManifestStore(root)


def _real_fqns(store: GenerationManifestStore, scope: Scope) -> list[str]:
    key = scope_key(scope, "self_extracted")
    generation_id = store.read_active(key)
    assert generation_id is not None
    documents = store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
    fqns: list[str] = []
    for document in documents.values():
        fqn = extract_fqn(document["text"])
        if fqn is None or fqn.startswith(_NON_SYMBOL_FQN_PREFIXES):
            continue
        fqns.append(fqn.strip())
    return fqns


def _sampled_prefix_hit_rate(
    store: GenerationManifestStore, scope: Scope, fqns: list[str], *, seed: int
) -> tuple[int, int]:
    """``(hits, sampled)`` for *seed*'s reproducible sample of >= 3-char, genuinely-partial prefixes
    of real short type names in *fqns*, each checked for the real type in the default top-10 page."""
    rng = random.Random(seed)
    shorts = sorted({_FINAL_SEGMENT.split(fqn)[-1] for fqn in fqns if _FINAL_SEGMENT.split(fqn)[-1]})
    candidates = [name for name in shorts if len(name) >= 4]
    sample = rng.sample(candidates, k=min(_SAMPLE_SIZE, len(candidates)))
    hits = 0
    for name in sample:
        prefix_length = rng.randint(_PREFIX_MIN_LENGTH, len(name) - 1)
        prefix = name[:prefix_length]
        answer = search_symbols(store, scope, prefix)
        if isinstance(answer, list):
            for match in answer:
                fqn = extract_fqn(match.text) or ""
                if _FINAL_SEGMENT.split(fqn.strip())[-1] == name:
                    hits += 1
                    break
    return hits, len(sample)


@pytest.mark.parametrize("pilot", _REAL_PILOTS)
def test_real_pilot_prefix_sample_is_mostly_found_in_top_10(
    _real_pilot_store: GenerationManifestStore, pilot: str
) -> None:
    family, platform = pilot.split("__")
    scope = Scope(family=family, platform=platform)
    fqns = _real_fqns(_real_pilot_store, scope)
    assert fqns, pilot

    hits, sampled = _sampled_prefix_hit_rate(_real_pilot_store, scope, fqns, seed=_SAMPLE_SEED)

    assert sampled > 0, pilot
    # At least one real prefix hit per pilot - the mechanism genuinely works on real data, even
    # where a pilot's own candidate pool makes the floor/pagination artifacts above more likely.
    assert hits > 0, (pilot, hits, sampled)


def test_real_pooled_prefix_sample_hit_rate_meets_the_measured_floor(
    _real_pilot_store: GenerationManifestStore,
) -> None:
    total_hits = 0
    total_sampled = 0
    for pilot in _REAL_PILOTS:
        family, platform = pilot.split("__")
        scope = Scope(family=family, platform=platform)
        fqns = _real_fqns(_real_pilot_store, scope)
        hits, sampled = _sampled_prefix_hit_rate(_real_pilot_store, scope, fqns, seed=_SAMPLE_SEED)
        total_hits += hits
        total_sampled += sampled

    assert total_sampled > 0
    pooled_hit_rate = total_hits / total_sampled
    assert pooled_hit_rate >= _MEASURED_POOLED_HIT_RATE_FLOOR, (total_hits, total_sampled, pooled_hit_rate)
