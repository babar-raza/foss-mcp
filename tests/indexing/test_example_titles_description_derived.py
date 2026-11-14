"""G3/TC-804 (REQ-G3-011): description-derived titles for counter-titled README examples.

README examples are titled with the heading of the block they sit in. When one block holds several fences,
or two blocks share a heading, the later ones were titled ``Quick Start (2)``, ``Additional Examples (5)``:
the title names no subject and cannot tell the siblings apart. ``retitle_counter_examples`` replaces such a
title with the example's own description sentence, at candidate-extraction time (the title feeds the chunk
``FQN: Example: <title>``).

The change is MEASUREMENT-GATED (TC-760/761: retitling interacts with ``title_anchored`` and the
find_examples wrong-confident ratchet has about two cases of headroom). The last section re-measures the
gate on every run: it applies the PRODUCTION derivation, in memory, to the committed example chunks and runs
the retrieval ratchets (``test_round3_retrieval_corpus``, ``test_forgiveness_acceptance``) against that
retitled corpus. A derivation that collides titles, or that tips a ratchet, fails here before anything is
regenerated. Nothing under ``infra/demo-manifests`` is ever written.
"""

from __future__ import annotations

import ast
import glob
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from foss_mcp.indexing import example_candidates
from foss_mcp.indexing.example_candidates import (
    DERIVED_TITLE_MAX_WORDS,
    GENERIC_README_TITLES,
    CandidateExample,
    derive_title_from_description,
    extract_candidate_supply,
    retitle_counter_examples,
)
from foss_mcp.indexing.generation_manifest import GenerationManifestStore, payload_stats
from foss_mcp.indexing.lexical_index_writer import tokenize
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import NoExampleFound, find_examples
from foss_mcp.mcp.tools.relevance import GENERIC_EXAMPLE_TITLES, is_generic_example_title
from tests.indexing.test_example_candidates_supply import PILOTS, _load_page
from tests.mcp.tools import test_forgiveness_acceptance as forgiveness
from tests.mcp.tools import test_round3_retrieval_corpus as round3

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

# The find_examples wrong-confident ceiling of the retrieval corpus, as a count of its 729 cases.
WRONG_CONFIDENT_CASES_MAX = 43
CORPUS_CASES = 729


def _candidate(title: str, description: str, code: str = "x = 1") -> CandidateExample:
    return CandidateExample(title=title, description=description, language="python", code=code)


# --- the derivation -------------------------------------------------------------------------------


def test_the_generic_heading_set_equals_the_retrieval_sides_set() -> None:
    # foss_mcp.mcp imports foss_mcp.indexing, so the set is repeated in indexing; the two must never drift.
    assert GENERIC_README_TITLES == GENERIC_EXAMPLE_TITLES


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "Render through a specific `Renderer` instance directly:",
            "Render through a specific `Renderer` instance directly",
        ),
        ("Load a workbook with recovery diagnostics.", "Load a workbook with recovery diagnostics"),
        # only the FIRST sentence of the paragraph nearest the fence is the title
        ("Export a worksheet to PDF. Then open it again.", "Export a worksheet to PDF"),
        # a heading mark is dropped, an earlier paragraph is not the one above the fence
        (
            "### Quick Start\n\nIntro paragraph about the library.\n\nCreate a message and save it as MSG:",
            "Create a message and save it as MSG",
        ),
        # a sentence wrapped over several lines is one title
        (
            "Attach a stylesheet and\nresolve the computed style:",
            "Attach a stylesheet and resolve the computed style",
        ),
        # no terminal punctuation at all is fine
        ("Read an existing workbook", "Read an existing workbook"),
    ],
)
def test_the_title_is_the_first_sentence_of_the_paragraph_above_the_fence(
    description: str, expected: str
) -> None:
    assert derive_title_from_description(description) == expected


@pytest.mark.parametrize(
    "description",
    [
        "",
        "   \n\n  ",
        # a heading that is itself generic names no subject
        "## Quick Start",
        # a sentence that would put the chunk's own marker line into the title
        "Run the Example: block below",
        # longer than DERIVED_TITLE_MAX_WORDS: a workflow summary, not a subject
        " ".join(["word"] * (DERIVED_TITLE_MAX_WORDS + 1)) + ".",
    ],
)
def test_a_description_that_supports_no_title_derives_none(description: str) -> None:
    assert derive_title_from_description(description) == ""


def test_the_word_limit_is_inclusive() -> None:
    sentence = " ".join(["word"] * DERIVED_TITLE_MAX_WORDS)
    assert derive_title_from_description(sentence + ".") == sentence


def test_only_a_generic_heading_with_a_counter_is_retitled() -> None:
    candidates = [
        _candidate("Quick Start", "Create a document and save it."),
        _candidate("Quick Start (2)", "Load an existing document."),
        _candidate("Additional Examples (3)", "Convert it to PDF."),
        _candidate("Installation, Dependencies, and Quick Start (part 2)", "Create a PDF."),
        # a specific title keeps its counter: there it is the supply's own uniqueness suffix
        _candidate("Save As (part 2)", "Save a copy."),
        _candidate("Anything Else (2)", "Another thing."),
    ]
    titles = [c.title for c in retitle_counter_examples(candidates)]
    assert titles == [
        "Quick Start",
        "Load an existing document",
        "Convert it to PDF",
        "Create a PDF",
        "Save As (part 2)",
        "Anything Else (2)",
    ]


def test_a_counter_title_with_no_derivable_description_keeps_its_counter() -> None:
    candidates = [_candidate("Quick Start (2)", ""), _candidate("Quick Start (3)", "Open a file.")]
    assert [c.title for c in retitle_counter_examples(candidates)] == ["Quick Start (2)", "Open a file"]


def test_a_derived_title_never_collides_with_another_title() -> None:
    candidates = [
        _candidate("Open a file", "unrelated"),
        _candidate("Quick Start (2)", "Open a file."),
        _candidate("Quick Start (3)", "Open a file."),
        _candidate("Quick Start (4)", "Open a file!"),
    ]
    titles = [c.title for c in retitle_counter_examples(candidates)]
    assert titles == ["Open a file", "Open a file (2)", "Open a file (3)", "Open a file (4)"]
    assert len(set(titles)) == len(titles)


def test_retitling_keeps_order_and_every_other_field_and_is_idempotent() -> None:
    candidates = [
        _candidate("Quick Start", "Create it.", "a()"),
        _candidate("Quick Start (2)", "Load it.", "b()"),
    ]
    once = retitle_counter_examples(candidates)
    assert [c.code for c in once] == ["a()", "b()"]
    assert [c.description for c in once] == ["Create it.", "Load it."]
    assert once[0] is candidates[0]
    assert retitle_counter_examples(once) == once


# --- the production path: the README page of every pilot, then the ingestion job ------------------------


def _is_counter_generic(title: str) -> bool:
    return title != example_candidates._TITLE_COUNTER_RE.sub("", title) and is_generic_example_title(title)


def test_the_candidates_of_the_real_pages_lose_every_counter_title_their_description_can_replace() -> None:
    """The supply of every real committed README page, as the ingestion job takes it, then retitled: every
    counter-generic title is replaced unless its description supports none, no pilot's titles collide, and the
    supply itself still holds counter titles (so this is not vacuous)."""
    counters_before = counters_after = 0
    for name, platform in PILOTS:
        supplied = list(extract_candidate_supply(_load_page(name), platform).candidates)
        retitled = retitle_counter_examples(supplied)
        assert len(retitled) == len(supplied), name
        titles = [c.title for c in retitled]
        assert len(set(titles)) == len(titles), (name, [t for t, n in Counter(titles).items() if n > 1])
        counters_before += sum(_is_counter_generic(c.title) for c in supplied)
        counters_after += sum(_is_counter_generic(c.title) for c in retitled)
        for candidate in retitled:
            if _is_counter_generic(candidate.title):
                assert derive_title_from_description(candidate.description) == "", (name, candidate.title)
    assert counters_before >= 10, counters_before
    assert counters_after < counters_before


def test_a_synthetic_page_end_to_end_through_the_supply() -> None:
    page = {
        "single": {
            "block": [
                {"title": "Quick Start", "content": "Create a file:\n\n```python\nmake()\n```\n"},
                {"title": "Quick Start", "content": "Open the file again:\n\n```python\nopen_it()\n```\n"},
            ]
        }
    }
    supply = extract_candidate_supply(page, "python")
    assert [c.title for c in supply.candidates] == [
        "Quick Start",
        "Quick Start (2)",
    ]  # the supply is unchanged
    assert [c.title for c in retitle_counter_examples(supply.candidates)] == [
        "Quick Start",
        "Open the file again",
    ]


def _function_source(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_ingestion_job_applies_the_retitle_to_the_candidates_it_verifies_and_publishes() -> None:
    """Wiring (AGENTS.md "Integration and liveness"): ``retitle_counter_examples`` is CALLED, in the function that
    builds the example chunks, and its result is what the chunks are built from - not merely importable."""
    build = _function_source(REPO_ROOT / "infra" / "build_chunks.py", "_build_verified_example_chunks")
    assigns = [
        node
        for node in ast.walk(build)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", "") == "retitle_counter_examples"
        and [getattr(t, "id", "") for t in node.targets] == ["candidates"]
    ]
    assert len(assigns) == 1, (
        "build_chunks._build_verified_example_chunks must do `candidates = retitle_counter_examples(...)`"
    )
    # ...and it happens before the candidates are first iterated (planned, verified, chunked)
    first_use = next(
        node.lineno
        for node in ast.walk(build)
        if isinstance(node, ast.For)
        and getattr(node.iter, "id", "") in ("candidates",)
        or isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and any(getattr(a, "id", "") == "candidates" for a in node.iter.args)
    )
    assert assigns[0].lineno < first_use


# --- the measurement gate, re-run: the committed corpus, retitled in memory --------------------------

_EXAMPLE_KIND_LINE = "Kind: verified_example"
_HEADER_PREFIXES = (
    "Provenance:",
    "Verification",
    "Authored example:",
    "Verified-With:",
    "Runtime-",
    "Harness:",
)


def _is_example(text: str) -> bool:
    lines = text.split("\n", 2)
    return len(lines) > 1 and lines[1] == _EXAMPLE_KIND_LINE


def _title_of(text: str) -> str:
    return text.split("\n", 1)[0].removeprefix("FQN: Example: ")


def _description_of(text: str) -> str:
    """The prose of a published example chunk between its header lines and its ``Example:`` marker line."""
    lines = text.split("\n")
    marker = max((n for n, line in enumerate(lines) if line.rstrip() == "Example:"), default=len(lines))
    body = lines[2:marker]
    while body and (not body[0].strip() or body[0].startswith(_HEADER_PREFIXES)):
        body.pop(0)
    return "\n".join(body)


def retitle_tree(source: Path, dest: Path) -> dict[str, dict[str, str]]:
    """Copy every ``*__self_extracted`` scope of *source* into *dest*, applying ``retitle_counter_examples`` to
    each scope's example chunks (the lexical documents and the vector points) and rebuilding the lexical
    statistics. Returns ``scope -> {old title: new title}`` for the titles that changed."""
    changes: dict[str, dict[str, str]] = {}
    for scope in sorted(source.glob("*__self_extracted")):
        if not scope.is_dir():
            continue
        target = dest / scope.name
        shutil.copytree(scope, target)
        path = Path(glob.glob(str(target / "generation__*.json"))[0])
        data = json.loads(path.read_text(encoding="utf-8"))
        documents = data["payload"]["lexical_index"]["documents"]
        keys = sorted(key for key, doc in documents.items() if _is_example(doc["text"]))
        candidates = [
            _candidate(_title_of(documents[key]["text"]), _description_of(documents[key]["text"]))
            for key in keys
        ]
        renamed = {
            documents[key]["text"]: new.title
            for key, old, new in zip(keys, candidates, retitle_counter_examples(candidates), strict=True)
            if new.title != old.title
        }
        if not renamed:
            continue
        changes[scope.name] = {_title_of(text): title for text, title in renamed.items()}
        for doc in documents.values():
            if doc["text"] in renamed:
                doc["text"] = f"FQN: Example: {renamed[doc['text']]}\n{doc['text'].split(chr(10), 1)[1]}"
                doc["tokens"] = tokenize(doc["text"])
        for point in data["payload"]["vector_index"]["points"]:
            if point["text"] in renamed:
                point["text"] = (
                    f"FQN: Example: {renamed[point['text']]}\n{point['text'].split(chr(10), 1)[1]}"
                )
        frequency: Counter[str] = Counter()
        for doc in documents.values():
            frequency.update(set(doc["tokens"]))
        data["payload"]["lexical_index"]["doc_freq"] = dict(frequency)
        data["payload"]["stats"] = {**data["payload"].get("stats", {}), **payload_stats(data["payload"])}
        path.write_text(json.dumps(data), encoding="utf-8")
    return changes


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(round3._ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(round3._API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        find_examples_module, "GatewayEmbeddingProvider", forgiveness._NoGatewayEmbeddingProvider
    )


@pytest.fixture(scope="module")
def retitled(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, dict[str, str]]]:
    root = tmp_path_factory.mktemp("retitled-scopes")
    return root, retitle_tree(DEMO_MANIFESTS, root)


def _example_titles(root: Path) -> dict[str, list[str]]:
    titles: dict[str, list[str]] = {}
    for scope in sorted(root.glob("*__self_extracted")):
        path = Path(glob.glob(str(scope / "generation__*.json"))[0])
        documents = json.loads(path.read_text(encoding="utf-8"))["payload"]["lexical_index"]["documents"]
        titles[scope.name] = [_title_of(d["text"]) for d in documents.values() if _is_example(d["text"])]
    return titles


def test_the_retitled_committed_corpus_has_unique_example_titles(retitled: tuple[Path, dict]) -> None:
    root, _ = retitled
    for scope, titles in _example_titles(root).items():
        assert len(set(titles)) == len(titles), (scope, [t for t, n in Counter(titles).items() if n > 1])


def test_the_retitled_titles_are_description_derived_and_never_longer_than_the_limit(
    retitled: tuple[Path, dict],
) -> None:
    _, changes = retitled
    for scope, renames in changes.items():
        for old, new in renames.items():
            assert is_generic_example_title(old), (scope, old)
            assert not is_generic_example_title(new), (scope, new)
            assert "\n" not in new and "Example:" not in new, (scope, new)
            assert len(new.split()) <= DERIVED_TITLE_MAX_WORDS + 2, (scope, new)  # +2: a " (k)" tiebreak


@pytest.fixture(scope="module")
def corpus_cases(retitled: tuple[Path, dict]) -> tuple[list[round3.Case], list[round3.Case]]:
    """(lookup cases, find_examples cases) of the round-3 retrieval corpus over the retitled scopes."""
    root, _ = retitled
    corpus = round3.Corpus(root)
    return round3.run_corpus(corpus), round3.run_corpus_find_examples(corpus)


def test_the_find_examples_wrong_confident_ratchet_holds_over_the_retitled_corpus(
    corpus_cases: tuple[list[round3.Case], list[round3.Case]],
) -> None:
    _, cases = corpus_cases
    row = round3.summarise(cases)["ALL"]
    assert row["n"] == CORPUS_CASES, row["n"]
    assert row["wrong_conf"] <= round3.WRONG_MAX_EXAMPLES, row["wrong_conf"]
    assert round(row["wrong_conf"] * row["n"]) <= WRONG_CONFIDENT_CASES_MAX, row["wrong_conf"]


def test_the_lookup_ratchets_hold_over_the_retitled_corpus(
    corpus_cases: tuple[list[round3.Case], list[round3.Case]],
) -> None:
    cases, _ = corpus_cases
    table = round3.summarise(cases)
    assert table["ALL"]["hit3"] >= max(round3.HIT3_OVERALL, round3.BASELINE_HIT3["ALL"] + 0.15)
    assert table["ALL"]["wrong_conf"] <= round3.WRONG_MAX
    for family in round3.FAMILIES:
        assert table[family]["hit3"] >= max(round3.HIT3_FAMILY, round3.BASELINE_HIT3[family] + 0.10), family
        assert table[family]["wrong_conf"] <= round3.WRONG_MAX_FAMILY[family], family
    by_pilot = round3.summarise_by_pilot(cases, "other")
    for pilot, ceiling in round3.WRONG_MAX_OTHER_PILOT.items():
        assert by_pilot[pilot]["wrong_conf"] <= ceiling, pilot


def test_the_forgiveness_example_queries_still_hit_over_the_retitled_corpus(
    retitled: tuple[Path, dict], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, _ = retitled
    monkeypatch.setattr(forgiveness, "DEMO_MANIFESTS", root)
    stores = forgiveness._Stores(tmp_path)
    counts = {"hit": 0, "miss": 0, "wrong": 0}
    for row in forgiveness.EXAMPLE_ROWS:
        counts[forgiveness._example_outcome(stores, row[0], row[1], row[2])] += 1
    assert counts["hit"] >= forgiveness.MIN_CONCISE_HITS and counts["wrong"] == 0, counts


def test_a_retitled_example_is_found_by_the_words_of_its_title(
    retitled: tuple[Path, dict], tmp_path: Path
) -> None:
    """End to end over the real data: html/python's stylesheet example has a title that names its subject (derived
    from its description now, or already when the data was regenerated), and a query for that subject finds it
    first, by its title."""
    root, _ = retitled
    scope_dir = "html__python__self_extracted"
    shutil.copytree(root / scope_dir, tmp_path / scope_dir)
    store = GenerationManifestStore(tmp_path)
    scope = Scope(family="html", platform="python", source_kind="self_extracted")
    result = find_examples(store, scope, "attach a stylesheet and resolve the computed style", top_k=3)
    assert not isinstance(result, NoExampleFound)
    assert "Attach a stylesheet and resolve the computed style" in result[0].fqn
    assert "Quick Start" not in result[0].fqn
