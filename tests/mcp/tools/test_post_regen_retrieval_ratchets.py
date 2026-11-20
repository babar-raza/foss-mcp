"""G3/TC-839 (REQ-G3-014): the retrieval ratchets, run against the POST-REGENERATION state of every pilot.

``tests/mcp/tools/post_regen_simulation.py`` builds, offline, the tree the next re-ingest will bake: every pilot's
authored examples replaced by its CURRENT ``authored_examples.yaml`` entries in the real chunk format, the lexical
index recomputed. The ratchet tests that guard retrieval quality read the COMMITTED ``infra/demo-manifests``, so a
sidecar edit that breaks one of them stays invisible until the data is regenerated. This file closes that gap: it
IMPORTS the ratchet modules and CALLS their own test functions, with their own constants, over the simulated tree
(``DEMO_MANIFESTS`` of each module is repointed at it for the duration of this module and restored afterwards) -
no threshold, table or pair is copied here, so a floor can only be changed where it lives.

A failure names the exact cases: the confident-wrong (pilot, task, query) triples of the retrieval corpus, the
forgiveness rows with their outcome, the round-2 pairs that stopped answering.

Not run here: the forgiveness symbol-phrase and verified-absent tables (symbol chunks are not touched by a
sidecar; the 729-case corpus below exercises lookup over the same simulated documents).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

import tests.conformance.corpus as conformance_corpus
import tests.mcp.tools.test_find_examples_round2_recall_ratchet as r2
import tests.mcp.tools.test_find_examples_title_anchor as ta
import tests.mcp.tools.test_forgiveness_acceptance as fa
import tests.mcp.tools.test_round3_retrieval_corpus as rc
from foss_mcp.indexing.authored_examples import SIDECAR_NAME
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import ExampleMatch, NoExampleFound, find_examples
from foss_mcp.mcp.tools.relevance import (
    _CHUNK_METADATA_LINE_PREFIXES,
    example_lead_in,
    is_generic_example_title,
)
from foss_mcp.mcp.tools.vector_guard import _set_guard
from tests.mcp.tools import post_regen_simulation as sim

_MODULES = (rc, fa, ta, r2, conformance_corpus)


class _Tree:
    """The simulated tree and the per-pilot example counts of the committed vs the simulated state."""

    def __init__(self, root: Path, counts: dict[str, sim.PilotCounts]) -> None:
        self.root = root
        self.counts = counts


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> _Tree:
    root = tmp_path_factory.mktemp("post-regeneration")
    return _Tree(root, sim.build_post_regeneration_tree(root))


@pytest.fixture(scope="module", autouse=True)
def _simulated_state(tree: _Tree) -> Iterator[None]:
    """Repoint every ratchet module at the simulated tree, with no embedding gateway (the standalone image's own
    state), for the whole module; restore everything afterwards."""
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(rc._ENDPOINT_ENV, raising=False)
        patch.delenv(rc._API_KEY_ENV, raising=False)
        patch.setattr(find_examples_module, "GatewayEmbeddingProvider", fa._NoGatewayEmbeddingProvider)
        _set_guard(None)
        with sim.repointed(_MODULES, tree.root):
            yield
        _set_guard(None)


def _ratchet(ratchet: Callable[..., Any], *args: Any, detail: Callable[[], str] = lambda: "") -> None:
    """Call a committed-manifest ratchet; on failure re-raise with the exact cases that broke it."""
    try:
        ratchet(*args)
    except AssertionError as error:
        raise AssertionError(f"{error}\n{detail()}") from error


def _wrong_confident(cases: list[rc.Case], family: str | None = None, pilot: str | None = None) -> str:
    rows = [
        f"  {case.pilot} | {case.task_id} | {case.query!r} -> {', '.join(case.top)}"
        for case in cases
        if case.answered
        and case.rank is None
        and not case.partial
        and (family is None or case.family == family)
        and (pilot is None or case.pilot == pilot)
    ]
    return "confident-wrong cases:\n" + "\n".join(rows)


def _missed(cases: list[rc.Case], family: str | None = None) -> str:
    rows = [
        f"  {case.pilot} | {case.task_id} | {case.query!r} -> rank {case.rank}"
        for case in cases
        if (family is None or case.family == family) and (case.rank is None or case.rank > 3)
    ]
    return "cases outside the top 3:\n" + "\n".join(rows)


# ---- the simulation itself is faithful (a simulation that changes nothing proves nothing) -------------------------


def _documents(root: Path, directory: str) -> dict[str, dict[str, Any]]:
    """The lexical-index documents of one scope directory, read straight from its generation file (read-only)."""
    data = json.loads(sim._generation_path(root / directory).read_text(encoding="utf-8"))
    return data["payload"]["lexical_index"]


# The pilots whose committed generation is not a regenerated one (no ``Verification-Level:`` line on its example chunks).
NOT_REGENERATED_PILOTS = ("imaging__net",)


def _stamped_example_chunks_by_pilot(root: Path) -> dict[str, int]:
    """Per pilot under *root*: how many example chunks carry a ``Verification-Level:`` line (read straight from the files)."""
    counts: dict[str, int] = {}
    for directory in sorted(root.glob("*__self_extracted")):
        counts[directory.name.removesuffix("__self_extracted")] = sum(
            1
            for document in _documents(root, directory.name)["documents"].values()
            if sim.is_example_text(str(document["text"]))
            and "\nVerification-Level: " in str(document["text"])
        )
    return counts


def test_the_simulation_covers_every_baked_pilot_and_every_sidecar_example(tree: _Tree) -> None:
    assert len(tree.counts) == 32
    with_sidecar = {
        pilot: value for pilot, value in tree.counts.items() if value.sidecar_examples is not None
    }
    assert len(with_sidecar) == 30, sorted(set(tree.counts) - set(with_sidecar))
    for pilot, value in with_sidecar.items():
        assert value.simulated_authored == value.sidecar_examples, pilot
    committed_examples = sum(v.committed_examples for v in tree.counts.values())
    simulated_examples = sum(v.simulated_examples for v in tree.counts.values())
    assert simulated_examples >= committed_examples, (
        "the simulated tree lost examples the committed one holds"
    )
    # TC-850: cohort C3 was the last cohort whose committed data lacked authored examples its sidecar already carried, so
    # from then on the simulation can no longer ADD an example (every sidecar entry is committed already).
    # TC-852: cohort C5 regenerated the last pilots whose committed example chunks lacked a ``Verification-Level:`` line, so the
    # other thing the simulation always rewrites, the verification stamp, can no longer be ADDED to 31 of the 32 pilots, and the old
    # non-vacuity guard (simulated stamped chunks strictly greater than committed stamped chunks, summed over the tree) is
    # replaced by the pin that is still meaningful once nothing is left to add: every regenerated baked pilot has stamped example
    # chunks and the simulated tree holds exactly as many stamped example chunks as the committed one, per pilot (the simulation
    # neither drops a stamp nor invents a chunk). The one exception is imaging/net (cohort C0, never re-baked by a cohort card:
    # three README examples with no level line, see tests/mcp/tools/test_find_examples_verification_level_label.py): the
    # simulation still stamps those, which keeps the simulation non-vacuous (simulated stamped > committed stamped for it).
    committed_stamped = _stamped_example_chunks_by_pilot(sim.DEMO_MANIFESTS)
    simulated_stamped = _stamped_example_chunks_by_pilot(tree.root)
    assert len(committed_stamped) == 32
    regenerated = sorted(set(committed_stamped) - set(NOT_REGENERATED_PILOTS))
    assert len(regenerated) == 31
    for pilot in regenerated:
        assert committed_stamped[pilot] > 0, pilot
        assert simulated_stamped[pilot] == committed_stamped[pilot], pilot
    for pilot in NOT_REGENERATED_PILOTS:
        assert committed_stamped[pilot] == 0, pilot
        assert simulated_stamped[pilot] > committed_stamped[pilot], (
            f"{pilot}: the simulated tree stamps no example chunk the committed one lacks a verification level on: "
            "the simulation is vacuous"
        )


def test_every_simulated_authored_chunk_is_a_current_sidecar_entry_in_the_real_chunk_format(
    tree: _Tree,
) -> None:
    for pilot, value in tree.counts.items():
        if value.sidecar_examples is None:
            continue
        family, platform = pilot.split("__")
        sidecar = yaml.safe_load(
            (sim.FURNISHED / f"{family}_{platform}" / SIDECAR_NAME).read_text(encoding="utf-8")
        )
        texts = [
            document["text"]
            for document in _documents(tree.root, f"{pilot}__self_extracted")["documents"].values()
            if sim.is_authored_example_text(document["text"])
        ]
        assert sorted(sim.example_title(text) for text in texts) == sorted(
            entry["title"] for entry in sidecar["examples"]
        ), pilot
        for text in texts:
            lines = text.split("\n")
            assert lines[1] == "Kind: verified_example" and lines[2] == "Provenance: authored", (
                pilot,
                lines[:3],
            )
            assert any(line.startswith("Verification-Level:") for line in lines[:10]), (pilot, lines[:8])
            assert "\nExample:\n" in text and lines[-1].startswith("Source-Commit: "), pilot


def test_the_simulated_index_statistics_are_the_ones_the_index_writer_computes(tree: _Tree) -> None:
    for directory in sorted(path.name for path in tree.root.glob("*__self_extracted")):
        lexical = _documents(tree.root, directory)
        frequency: dict[str, int] = {}
        for document in lexical["documents"].values():
            assert document["tokens"] == tokenize(document["text"]), directory
            for token in set(document["tokens"]):
                frequency[token] = frequency.get(token, 0) + 1
        assert lexical["doc_freq"] == frequency, directory


def test_the_readme_derived_examples_stay_as_committed_apart_from_the_verification_stamp(tree: _Tree) -> None:
    stamp = ("Verification-Level:", "Verified-With:")

    def readme_examples(root: Path, pilot: str) -> dict[str, str]:
        return {
            sim.example_title(document["text"]): document["text"]
            for document in _documents(root, f"{pilot}__self_extracted")["documents"].values()
            if sim.is_example_text(document["text"]) and not sim.is_authored_example_text(document["text"])
        }

    for pilot in tree.counts:
        committed = readme_examples(sim.DEMO_MANIFESTS, pilot)
        simulated = readme_examples(tree.root, pilot)
        assert simulated.keys() == committed.keys(), pilot
        for title, text in simulated.items():
            kept = [line for line in text.splitlines() if line.strip() and not line.startswith(stamp)]
            # TC-848: a regenerated pilot's committed example chunks carry the stamp themselves, so it is
            # dropped from the committed side too: the two must agree on everything but the stamp.
            assert kept == [
                line for line in committed[title].splitlines() if line.strip() and not line.startswith(stamp)
            ], (pilot, title)


# ---- relevance.example_lead_in knows every chunk header line the ingestion writes --------------------------------


@pytest.mark.parametrize("prefix", ["Verification-Level:", "Verified-With:", "Runtime-Produced-Bytes:"])
def test_a_chunk_header_line_is_never_the_lead_in_of_an_example_with_an_empty_description(
    prefix: str,
) -> None:
    assert prefix in _CHUNK_METADATA_LINE_PREFIXES
    text = (
        "FQN: Example: Quick Start\nKind: verified_example\n"
        f"{prefix} ran-exit0\n\n\nExample:\nprint(1)\nSource-Commit: {'a' * 40}"
    )
    assert is_generic_example_title("Quick Start")
    assert example_lead_in(text) == ""


def test_the_lead_in_is_still_the_description_when_the_header_carries_the_new_lines() -> None:
    text = (
        "FQN: Example: Quick Start\nKind: verified_example\nVerification-Level: ran-exit0\n"
        "Verified-With: Python 3.12.1\nRuntime-Produced-Bytes: 22\n\nOpen a file and read it.\n\nExample:\n"
        f"print(1)\nSource-Commit: {'a' * 40}"
    )
    assert example_lead_in(text) == "Open a file and read it."


def test_a_simulated_generic_titled_chunk_with_an_empty_description_has_no_lead_in(tree: _Tree) -> None:
    chunk = sim.render_authored_chunk(
        {"title": "Quick Start", "description": "", "code": "print(1)"},
        repository="org/repo",
        commit="a" * 40,
        platform="java",
        toolchain="javac 21",
        committed=None,
    )
    assert "Verification-Level:" in chunk.text and "Runtime-Produced-Bytes:" in chunk.text
    assert example_lead_in(chunk.text) == ""


# ---- the 729-case retrieval corpus (test_round3_retrieval_corpus.py) -------------------------------------------


@pytest.fixture(scope="module")
def corpus(tree: _Tree) -> rc.Corpus:
    return rc.Corpus(tree.root)


@pytest.fixture(scope="module")
def cases(corpus: rc.Corpus) -> list[rc.Case]:
    return rc.run_corpus(corpus)


@pytest.fixture(scope="module")
def find_examples_cases(corpus: rc.Corpus) -> list[rc.Case]:
    return rc.run_corpus_find_examples(corpus)


def test_lookup_hit_at_3_overall_holds_after_regeneration(cases: list[rc.Case]) -> None:
    _ratchet(rc.test_hit_at_3_overall_meets_the_card_and_the_ratchet, cases, detail=lambda: _missed(cases))


@pytest.mark.parametrize("family", rc.FAMILIES)
def test_lookup_hit_at_3_per_family_holds_after_regeneration(cases: list[rc.Case], family: str) -> None:
    _ratchet(
        rc.test_hit_at_3_per_family_meets_the_card_and_the_ratchet,
        cases,
        family,
        detail=lambda: _missed(cases, family),
    )


def test_lookup_held_out_tasks_hold_after_regeneration(cases: list[rc.Case]) -> None:
    _ratchet(rc.test_the_held_out_tasks_meet_the_overall_floor_too, cases, detail=lambda: _missed(cases))


def test_lookup_wrong_confident_answers_stay_rare_after_regeneration(cases: list[rc.Case]) -> None:
    _ratchet(
        rc.test_non_empty_answers_that_miss_the_intended_api_stay_rare,
        cases,
        detail=lambda: _wrong_confident(cases),
    )


@pytest.mark.parametrize("family", rc.FAMILIES)
def test_lookup_wrong_rate_per_family_holds_after_regeneration(cases: list[rc.Case], family: str) -> None:
    _ratchet(
        rc.test_wrong_rate_per_family_meets_the_ratchet,
        cases,
        family,
        detail=lambda: _wrong_confident(cases, family),
    )


@pytest.mark.parametrize("pilot", sorted(rc.WRONG_MAX_OTHER_PILOT))
def test_lookup_wrong_rate_per_other_pilot_holds_after_regeneration(cases: list[rc.Case], pilot: str) -> None:
    _ratchet(
        rc.test_wrong_rate_per_pilot_in_the_other_family_meets_the_ratchet,
        cases,
        pilot,
        detail=lambda: _wrong_confident(cases, pilot=pilot),
    )


def test_find_examples_wrong_confident_answers_stay_rare_after_regeneration(
    find_examples_cases: list[rc.Case],
) -> None:
    """The 729-case find_examples ceiling (``rc.WRONG_MAX_EXAMPLES``, 43 of 729), over the simulated tree."""
    _ratchet(
        rc.test_find_examples_non_empty_answers_that_miss_the_intended_api_stay_rare,
        find_examples_cases,
        detail=lambda: _wrong_confident(find_examples_cases),
    )


# ---- the forgiveness tables (test_forgiveness_acceptance.py, test_find_examples_title_anchor.py) -----------------


@pytest.fixture(scope="module")
def fa_stores(tmp_path_factory: pytest.TempPathFactory) -> fa._Stores:
    return fa._Stores(tmp_path_factory.mktemp("post_regen_forgiveness"))


@pytest.fixture(scope="module")
def ta_stores(tmp_path_factory: pytest.TempPathFactory) -> ta._Stores:
    return ta._Stores(tmp_path_factory.mktemp("post_regen_title_anchor"))


def _forgiveness_rows(stores: fa._Stores, column: int) -> str:
    rows = [
        f"  {row[0]} | target {row[1]!r} | {row[column]!r} -> {fa._example_outcome(stores, row[0], row[1], row[column])}"
        for row in fa.EXAMPLE_ROWS
    ]
    return "forgiveness rows:\n" + "\n".join(rows)


def test_the_concise_example_table_still_hits_23_of_26_with_no_wrong_answer(fa_stores: fa._Stores) -> None:
    _ratchet(
        fa.test_concise_example_queries_still_hit_23_of_26,
        fa_stores,
        detail=lambda: _forgiveness_rows(fa_stores, 2),
    )


def test_the_verbose_example_table_does_not_regress(fa_stores: fa._Stores) -> None:
    _ratchet(
        fa.test_verbose_example_queries_do_not_regress,
        fa_stores,
        detail=lambda: _forgiveness_rows(fa_stores, 3),
    )


@pytest.mark.parametrize(("query", "pilot"), fa.FIXED_NEGATIVES)
def test_the_fixed_negative_example_queries_are_still_misses(
    query: str, pilot: str, fa_stores: fa._Stores
) -> None:
    fa.test_fixed_negative_example_queries_are_misses(query, pilot, fa_stores)


def test_the_tc516_example_tables_are_unchanged_after_regeneration(
    ta_stores: ta._Stores, fa_stores: fa._Stores
) -> None:
    _ratchet(
        ta.test_the_tc516_example_tables_are_unchanged,
        ta_stores,
        detail=lambda: _forgiveness_rows(fa_stores, 2),
    )


@pytest.fixture(scope="module")
def generic_word_answers(ta_stores: ta._Stores) -> dict[str, list[tuple[str, ExampleMatch]]]:
    return {word: ta._answers(ta_stores, word) for word in ta.GENERIC_WORDS}


@pytest.mark.parametrize("word", ta.GENERIC_WORDS)
def test_generic_one_word_queries_still_return_only_title_anchored_examples(
    word: str, generic_word_answers: dict[str, list[tuple[str, ExampleMatch]]]
) -> None:
    ta.test_generic_one_word_queries_return_only_title_anchored_examples(word, generic_word_answers)


def test_the_title_anchor_pins_on_the_live_pilots_hold_after_regeneration(ta_stores: ta._Stores) -> None:
    ta.test_pdf_net_watermark_and_pdf_java_page_dimensions_still_answer_as_the_live_tests_pin(ta_stores)
    ta.test_the_slides_cpp_export_format_reproduction_is_fixed(ta_stores)
    ta.test_set_password_returns_only_examples_with_a_title_word(ta_stores)
    ta.test_the_real_slides_cpp_deck_export_query_is_an_honest_miss_with_its_seven_examples(ta_stores)


# ---- the round-2 recall ratchet (test_find_examples_round2_recall_ratchet.py) ----------------------------------


@pytest.fixture(scope="module")
def answers(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], list[ExampleMatch] | None]:
    """``r2``'s own ``answers`` fixture, over the simulated tree: (pilot, query) -> the matches find_examples gave."""
    root = tmp_path_factory.mktemp("post_regen_round2")
    stores: dict[str, GenerationManifestStore] = {}
    found: dict[tuple[str, str], list[ExampleMatch] | None] = {}
    for pair in r2._load_pairs():
        pilot, query = str(pair["pilot"]), str(pair["query"])
        directory = f"{pilot}__self_extracted"
        if directory not in stores:
            shutil.copytree(r2.DEMO_MANIFESTS / directory, root / directory)
            stores[directory] = GenerationManifestStore(root)
        family, platform = pilot.split("__", 1)
        result = find_examples(stores[directory], Scope(family=family, platform=platform), query, top_k=5)
        found[(pilot, query)] = None if isinstance(result, NoExampleFound) else result
    return found


def test_every_round_two_must_hit_pair_still_answers_after_regeneration(
    answers: dict[tuple[str, str], list[ExampleMatch] | None],
) -> None:
    r2.test_the_fixture_holds_the_316_round_two_pairs_and_only_ever_grows_its_must_hit_set()
    r2.test_every_must_hit_pair_still_answers(answers)


def test_the_round_two_recall_does_not_fall_after_regeneration(
    answers: dict[tuple[str, str], list[ExampleMatch] | None],
) -> None:
    r2.test_the_recall_over_the_round_two_pairs_does_not_fall(answers)


def test_unexplained_round_two_answers_do_not_multiply_after_regeneration(
    answers: dict[tuple[str, str], list[ExampleMatch] | None],
) -> None:
    r2.test_answers_that_do_not_hold_every_query_word_do_not_multiply(answers)
