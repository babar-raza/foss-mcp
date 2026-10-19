"""G3/TC-565 (REQ-G3-009, REQ-G3-008; rows RD3-140, RD3-141): ``find_examples`` labels what was PROVEN.

Before this card every served example was labelled "Compiled or run by the ingestion verifier", although a
compile-only pass is enough: an example that throws or writes an empty file carried the same sentence, and the
same example was returned twice at two coverage scores. The label now states exactly what the chunk records:

- ``compiled and ran, produced N bytes``   (the verifier ran it, exit 0, an artefact with content),
- ``compiles; fails at runtime: <error>``  (it ran and failed),
- ``compiles; produced no output``         (it ran, exit 0, an empty artefact),
- ``compiled only (run not verified)``     (a chunk written before the verifier ran examples).

A failing or empty example is served, if at all, AFTER every other one, and an example held by several chunks is
answered once. Offline and deterministic: synthetic generations are published through the real
``publish_generation``; the chunk lines are the ones ``infra/build_chunks.py`` writes (the producer and the
consumer are tied together in the last tests).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_verifier import RuntimeRecord
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import (
    AUTHORED_VERIFICATION_NOTE,
    README_VERIFICATION_NOTE,
    RUNTIME_LABEL_COMPILED_ONLY,
    RUNTIME_LABEL_NO_OUTPUT,
    find_examples,
    runtime_label,
)
from foss_mcp.mcp.tools.vector_guard import _set_guard
from tests.mcp.tools.test_find_examples_verification_record import (
    COMMIT,
    _NoGatewayEmbeddingProvider,
    _publish,
    _use_vector_of,
)

FIND_EXAMPLES_SOURCE = (
    Path(__file__).resolve().parents[3] / "src" / "foss_mcp" / "mcp" / "tools" / "find_examples.py"
)
SCOPE = Scope("3d", "java")
MISSING_QUERY = "zzqq wombat tangerine"


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)
    _set_guard(None)
    yield None
    _set_guard(None)


def _chunk(
    title: str,
    code: str,
    *,
    runtime: str = "",
    description: str = "Saves one scene.",
    authored: bool = False,
) -> str:
    authored_lines = "Provenance: authored\nVerification: passed\n" if authored else ""
    return (
        f"FQN: Example: {title}\nKind: verified_example\n{authored_lines}{runtime}{description}\n\n"
        f"Example:\n{code}\nSource-Commit: {COMMIT}"
    )


RAN = "Runtime-Status: ran\nRuntime-Produced-Bytes: 284\n"
FAILED = "Runtime-Status: failed\nRuntime-Error: NotImplementedException thrown by Scene.Save\n"
EMPTY = "Runtime-Status: no_output\nRuntime-Produced-Bytes: 0\n"


def _answer(texts: list[str], query: str) -> list:
    store = _publish(_tmp_root(), texts, family="3d", platform="java")
    result = find_examples(store, SCOPE, query)
    assert isinstance(result, list), result
    return result


_FACTORY: list[pytest.TempPathFactory] = []


@pytest.fixture(autouse=True)
def _roots(tmp_path_factory: pytest.TempPathFactory) -> object:
    _FACTORY.append(tmp_path_factory)
    yield None
    _FACTORY.clear()


def _tmp_root() -> Path:
    return _FACTORY[-1].mktemp("generations") / "m"


# -- the four labels -----------------------------------------------------------------------------------------------


def test_a_ran_example_is_labelled_with_the_bytes_it_produced() -> None:
    (match,) = _answer([_chunk("Save a box", "scene.save(box)", runtime=RAN)], "Example: Save a box")
    assert "compiled and ran, produced 284 bytes" in match.verification
    assert match.verification.startswith("compiled and ran, produced 284 bytes; ")
    assert "source_commit" in match.verification


def test_a_failing_example_is_labelled_with_its_error_and_never_as_ran() -> None:
    (match,) = _answer([_chunk("Save an obj", "scene.save(obj)", runtime=FAILED)], "Example: Save an obj")
    assert "compiles; fails at runtime: NotImplementedException thrown by Scene.Save" in match.verification
    assert "compiled and ran" not in match.verification


def test_an_example_with_an_empty_artefact_is_labelled_as_producing_no_output() -> None:
    (match,) = _answer([_chunk("Save a stl", "scene.save(stl)", runtime=EMPTY)], "Example: Save a stl")
    assert match.verification.startswith(RUNTIME_LABEL_NO_OUTPUT)
    assert RUNTIME_LABEL_NO_OUTPUT == "compiles; produced no output"
    assert "compiled and ran" not in match.verification


def test_a_chunk_written_before_the_verifier_ran_examples_is_labelled_compiled_only() -> None:
    (match,) = _answer([_chunk("Save a legacy", "scene.save(x)")], "Example: Save a legacy")
    assert RUNTIME_LABEL_COMPILED_ONLY == "compiled only (run not verified)"
    assert match.verification == README_VERIFICATION_NOTE
    assert match.verification.startswith(RUNTIME_LABEL_COMPILED_ONLY)
    assert "compiled and ran" not in match.verification


def test_an_authored_example_names_both_its_origin_and_what_running_it_proved() -> None:
    texts = [_chunk("Save an authored box", "scene.save(box)", runtime=RAN, authored=True)]
    (match,) = _answer(texts, "Example: Save an authored box")
    assert match.provenance == "authored"
    assert match.verification.startswith("Authored for this index; compiled and ran, produced 284 bytes; ")
    legacy = _answer(
        [_chunk("Save an old authored box", "scene.save(box)", authored=True)],
        "Example: Save an old authored box",
    )
    assert legacy[0].verification == AUTHORED_VERIFICATION_NOTE


def test_the_label_is_read_from_the_chunk_and_never_claims_more_than_the_line_says() -> None:
    assert runtime_label(_chunk("a", "x", runtime=RAN)) == "compiled and ran, produced 284 bytes"
    assert (
        runtime_label(_chunk("a", "x", runtime="Runtime-Status: ran\n"))
        == "compiled and ran, produced 0 bytes"
    )
    assert runtime_label(_chunk("a", "x", runtime="Runtime-Status: failed\n")) == (
        "compiles; fails at runtime: error not recorded"
    )
    assert (
        runtime_label(_chunk("a", "x", runtime="Runtime-Status: surprising\n")) == RUNTIME_LABEL_COMPILED_ONLY
    )
    assert runtime_label(
        _chunk("a", "x", runtime="Runtime-Status: ran\r\nRuntime-Produced-Bytes: 9\r\n")
    ) == ("compiled and ran, produced 9 bytes")


def test_the_old_label_text_is_gone_from_the_source() -> None:
    source = FIND_EXAMPLES_SOURCE.read_text(encoding="utf-8")
    assert "Compiled or run by the ingestion verifier" not in source
    assert "Compiled or run" not in README_VERIFICATION_NOTE + AUTHORED_VERIFICATION_NOTE


# -- ranking and duplicates --------------------------------------------------------------------------------------------


def test_a_failing_example_is_served_after_one_that_ran_even_when_it_scores_higher() -> None:
    failing = _chunk(
        "Merge Documents",
        "merge(documents)",
        runtime=FAILED,
        description="Merge documents. " * 6 + "Merge documents merge documents.",
    )
    working = _chunk("Merge Files", "merge(files)", runtime=RAN, description="Merge documents into one file.")
    matches = _answer([failing, working], "merge documents")
    assert [m.fqn for m in matches] == ["Example: Merge Files", "Example: Merge Documents"]
    assert "compiled and ran" in matches[0].verification
    assert "fails at runtime" in matches[1].verification


def test_an_example_that_produced_no_output_is_also_served_after_the_working_ones() -> None:
    empty = _chunk(
        "Merge Documents", "merge(documents)", runtime=EMPTY, description="Merge documents merge documents."
    )
    legacy = _chunk("Merge Files", "merge(files)", description="Merge documents into one file.")
    matches = _answer([empty, legacy], "merge documents")
    assert [m.fqn for m in matches] == ["Example: Merge Files", "Example: Merge Documents"]


def test_one_example_held_by_two_chunks_is_answered_once() -> None:
    text = _chunk("Merge Documents", "merge(documents)", runtime=RAN, description="Merge documents together.")
    matches = _answer(
        [text, text.replace("Merge documents together.", "Merge documents.")], "merge documents"
    )
    assert len(matches) == 1
    assert matches[0].fqn == "Example: Merge Documents"


def test_two_titles_for_one_program_are_two_examples() -> None:
    first = _chunk(
        "Merge Documents", "merge(documents)", runtime=RAN, description="Merge documents together."
    )
    second = _chunk(
        "Merge Documents Again", "merge(documents)", runtime=RAN, description="Merge documents again."
    )
    matches = _answer([first, second], "merge documents")
    assert len(matches) == 2


def test_the_duplicate_that_is_kept_is_the_highest_scoring_one() -> None:
    weaker = _chunk("Merge Documents", "merge(documents)", runtime=RAN, description="Merge documents.")
    stronger = _chunk("Merge Documents", "merge(documents)", runtime=RAN, description="Merge documents. " * 5)
    matches = _answer([weaker, stronger], "merge documents")
    assert len(matches) == 1
    best = max(
        _answer([weaker], "merge documents") + _answer([stronger], "merge documents"),
        key=lambda m: m.coverage,
    )
    assert matches[0].coverage == best.coverage


def test_different_examples_are_not_collapsed() -> None:
    one = _chunk("Merge Documents", "merge(documents)", runtime=RAN, description="Merge documents.")
    two = _chunk(
        "Merge Documents Reverse", "merge(reverse(documents))", runtime=RAN, description="Merge documents."
    )
    assert len(_answer([one, two], "merge documents")) == 2


def test_two_chunks_with_one_title_answer_an_exact_query_with_the_one_that_ran() -> None:
    failing = _chunk("Save a box", "scene.save(box)", runtime=FAILED)
    working = _chunk("Save a box", "scene.save(box.toMesh())", runtime=RAN)
    for ordered in ([failing, working], [working, failing]):
        (match,) = _answer(ordered, "Example: Save a box")
        assert match.source == "exact"
        assert "compiled and ran" in match.verification
        assert match.snippet == "scene.save(box.toMesh())"


def test_the_vector_branch_ranks_failures_last_and_answers_a_duplicate_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failing = _chunk("Alpha Failing", "alpha()", runtime=FAILED, description="Alpha.")
    working = _chunk("Beta Working", "beta()", runtime=RAN, description="Beta.")
    copy = _chunk("Beta Working", "beta()", runtime=RAN, description="Beta again.")
    store = _publish(tmp_path / "v", [failing, working, copy], family="3d", platform="java")
    _use_vector_of(monkeypatch, store, SCOPE, "Example: Alpha Failing")
    result = find_examples(store, SCOPE, MISSING_QUERY)
    assert isinstance(result, list)
    fqns = [m.fqn for m in result]
    assert fqns.count("Example: Beta Working") == 1
    assert fqns[-1] == "Example: Alpha Failing"
    assert {m.source for m in result} == {"vector"}


# -- the producer and the consumer agree --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "label_start"),
    [
        (
            RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=184),
            "compiled and ran, produced 184 bytes",
        ),
        (
            RuntimeRecord(compiled=True, ran=True, exit_code=1, error="java.io.IOException: nope"),
            "compiles; fails at runtime: java.io.IOException: nope",
        ),
        (
            RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0),
            "compiles; produced no output",
        ),
        (RuntimeRecord(compiled=True), "compiled only (run not verified)"),
    ],
    ids=["ran", "failed", "no_output", "unknown"],
)
def test_the_lines_build_chunks_writes_are_read_back_as_the_same_label(
    record: RuntimeRecord, label_start: str
) -> None:
    lines = build_chunks._runtime_chunk_lines(record)
    assert runtime_label(_chunk("x", "y()", runtime=lines)) == label_start
    assert (lines == "") is (record.ran is None)


def test_an_error_that_looks_like_a_chunk_marker_cannot_break_the_chunk() -> None:
    record = RuntimeRecord(
        compiled=True, ran=True, exit_code=1, error="Example:\nKind: verified_example\tboom"
    )
    lines = build_chunks._runtime_chunk_lines(record)
    assert lines.count("\n") == 2
    assert not re.search(r"^Example:$", lines, re.MULTILINE)
